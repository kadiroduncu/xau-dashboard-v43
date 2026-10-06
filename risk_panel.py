"""Streamlit adapter: explicit local feed contract, no inferred credentials or orders."""
from datetime import datetime, timezone
from pathlib import Path
import json
import os
import streamlit as st
import requests
from urllib.parse import urlparse
from risk_engine import load_config, evaluate, timestamp, config_hash, bars_valid, number
from setup_store import SetupStore
from market_data import closed_bars


def setting(name):
    if os.environ.get(name):
        return os.environ[name]
    try:
        return st.secrets.get(name)
    except (FileNotFoundError, KeyError):
        return None


@st.cache_data(ttl=15, show_spinner=False)
def remote_snapshot(url, token):
    parsed = urlparse(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('HTTPS endpoint required')
    response = requests.get(url, headers={'Authorization': 'Bearer '+token} if token else {},
                            timeout=10, allow_redirects=False)
    response.raise_for_status()
    if response.status_code != 200 or len(response.content) > 2_000_000:
        raise ValueError('invalid snapshot response')
    value = response.json()
    if not isinstance(value, dict):
        raise ValueError('snapshot must be an object')
    return value


def local_snapshot(data, now, c):
    """Connect available observations only; quote/spread and cross markets stay absent."""
    snapshot = {'as_of': now.isoformat(), 'm1': closed_bars(data.get('m1_frame'), 1, now),
                'm5': closed_bars(data.get('m5_frame'), 5, now)}
    if data.get('calendar'):
        snapshot['calendar'] = data['calendar']
    levels = data.get('obstacles')
    if levels is not None:
        snapshot['levels'] = {'observed_at': now.isoformat(), 'complete': True, 'prices': levels}
    sides = data.get('sides', [])
    if len(sides) == 1 and levels:
        side, price = sides[0], data['price']
        candidates = [v for v in levels if v >= price] if side == 'SHORT' else [v for v in levels if v <= price]
        if candidates:
            level = min(candidates, key=lambda v:abs(v-price))
            snapshot.update(side=side, setup_level=level)
            hurst = data.get('regime')
            snapshot['quality_evidence'] = {
                'regime': hurst is not None and hurst < 0.5,
                'macro': data.get('macro_bias') == ('HEADWIND' if side == 'SHORT' else 'TAILWIND'),
                'location': abs(level-price) <= c['level_buffer']*2,
            }
    return snapshot


def explain(reason):
    code, _, detail = reason.partition(':')
    labels = {
        'MISSING': 'Veri bağlantısı eksik', 'STALE': 'Veri güncel değil',
        'CHANNEL_UNAVAILABLE_OR_RED': 'Kanal veto / veri sorunu',
        'INVALID_SPREAD_DATA': 'Gerçek bid/ask ve spread geçmişi gerekli',
        'INVALID_CROSS_MARKET': 'Zaman uyumlu DXY ve 2Y/10Y getirileri gerekli',
        'INVALID_M1_DATA': 'M1 mumları eksik, eski veya kesintili',
        'INVALID_M5_DATA': 'M5 mumları eksik, eski veya kesintili',
        'INVALID_CALENDAR': 'Doğrulanmış haber takvimi gerekli',
        'NEWS_TIME_UNKNOWN': 'Haber günü: kesin saat yok, tüm gün blok',
        'CALENDAR_INCOMPLETE': 'Haber takvimi erişimi doğrulanamadı',
        'INVALID_SETUP_LEVELS': 'Yön ve destek/direnç adayı değerlendirilemiyor',
        'DIRECTION_NOT_ALLOWED': 'Mevcut yön kapısından geçen aday yok',
        'TAIL_RISK_HIGH': 'Risk kapısı onay vermiyor',
        'QUALITY_BELOW_A': 'Setup kalitesi A seviyesinin altında',
        'ACCEPTANCE': 'Seviye ötesinde kapanışlar: yapı kabul edildi',
        'REJECTION_NOT_CONFIRMED': 'Seviyeden reddedilme henüz doğrulanmadı',
        'ROOM_TO_TP': 'Hedef öncesinde destek/direnç engeli var',
        'M5_VOLATILITY_SHOCK': 'M5 mum aralığı normal oynaklığın üzerinde',
        'M1_JUMP': 'M1 fiyat sıçraması',
    }
    return labels.get(code, code) + (': '+detail if detail else '')


def indicative_room(snapshot, legacy, c):
    """Chart distance from a fresh closed M1 bar, never an executable entry."""
    try:
        now = timestamp(snapshot['as_of'])
        bars = bars_valid(snapshot['m1'], now, 1, 2, c)
        side = snapshot['side']
        if side not in ('LONG', 'SHORT') or snapshot['levels'].get('complete') is not True:
            return None
        if legacy.get('obstacles') is None:
            return None
        reference = number(bars[-1]['close'])
        sign = 1 if side == 'LONG' else -1
        prices = snapshot['levels']['prices'] + list(legacy['obstacles'])
        distances = [sign*(number(p)-reference) for p in prices]
        if any(number(p) <= 0 for p in prices):
            return None
        ahead = [d for d in distances if d >= 0]
        return {'reference': reference, 'observed_at': bars[-1]['closed_at'],
                'distance': min(ahead) if ahead else None}
    except (KeyError, TypeError, ValueError, IndexError):
        return None


def run_risk_panel(legacy, local_data=None):
    c = load_config(os.environ.get('XAU_RISK_CONFIG', Path(__file__).with_name('risk_config.json')))
    now = datetime.now(timezone.utc)
    path = setting('XAU_RISK_SNAPSHOT')
    url = setting('XAU_RISK_SNAPSHOT_URL')
    snapshot = {'as_of': now.isoformat()}
    error = None
    if path or url:
        try:
            snapshot = json.loads(Path(path).read_text()) if path else remote_snapshot(url, setting('XAU_RISK_SNAPSHOT_TOKEN'))
            age = (now-timestamp(snapshot['as_of'])).total_seconds()
            if not 0 <= age <= c['max_age_seconds']['quote']:
                raise ValueError('Snapshot güncel değil')
            # Evaluate freshness against wall clock, retain original capture time for audit.
            snapshot['captured_at'] = snapshot['as_of']
            snapshot['as_of'] = now.isoformat()
        except (OSError, ValueError, KeyError, TypeError, requests.RequestException):
            snapshot = {'as_of': now.isoformat()}
            error = 'Snapshot okunamadı, hatalı veya eski'
    else:
        snapshot = local_snapshot(local_data or {}, now, c)
    try:
        decision = evaluate(snapshot, legacy, c)
    except (KeyError, ValueError, TypeError, IndexError):
        decision = {'tradeable': False, 'reasons': ['INVALID_SNAPSHOT'], 'tail_score': 100,
                    'tail_label': 'EXTREME', 'quality_score': 0, 'quality_grade': 'NO TRADE',
                    'level_state': 'UNKNOWN', 'room_to_tp': None, 'as_of': now.isoformat(),
                    'config_hash': config_hash(c)}
    st.subheader('v43 — Risk / Setup Gate (paper only)')
    st.caption('Fiyat/mum: Twelve Data · Haber: Finnhub · Takvim: Finnhub veya resmî kaynaklar · Makro: FRED · DXY tarihsel karşılaştırması: Yahoo Finance. Mevcut anahtarlar kullanılır.')
    st.write('**Mevcut yön filtresi:** ' + (', '.join(legacy.get('allowed_sides', [])) or 'İzinli yön yok') +
             ' · **v42 filtre sonucu:** ' + ('Koşullar geçti' if legacy.get('tradeable') else 'Bekle / bloklu') +
             ' · v43 işlem onayı aşağıdaki kontrollerin tamamını gerektirir.')
    if error:
        st.warning(error)
    columns = st.columns(3)
    unavailable = not decision.get('data_complete', False)
    columns[0].metric('Tail Risk (sezgisel)', 'Hesaplanamıyor' if unavailable else f"{decision['tail_score']}/100", 'Kontroller tamamlanmadı' if unavailable else decision['tail_label'])
    columns[1].metric('Trade Quality', f"{decision['quality_score']}/12", 'Kısmi değerlendirme' if unavailable else decision['quality_grade'])
    columns[2].metric('Acceptance / Rejection', decision['level_state'])
    if snapshot.get('calendar'):
        st.caption('Takvim kaynağı: ' + snapshot['calendar'].get('source', 'Haricî snapshot'))
        if snapshot['calendar'].get('note'):
            st.caption(snapshot['calendar']['note'])
    st.caption('Eşikler başlangıç ayarlarıdır; kalibre edilmiş olasılık veya kârlılık kanıtı değildir.')
    measures = decision.get('measurements', {})
    observed = st.columns(2)
    for col, key, label in [(observed[0], 'm5_tr_atr', 'M5 aralık / ATR'),
                            (observed[1], 'm1_jump_sigma', 'M1 sıçrama / sigma')]:
        value = measures.get(key)
        col.metric(label, 'Hesaplanamıyor' if value is None else f'{value:.2f}')
    chart_room = indicative_room(snapshot, legacy, c)
    if chart_room:
        distance = chart_room['distance']
        st.write('**Grafik üzerinde hedef mesafesi:** ' +
                 (f"ilk engel {distance:.2f} $/oz uzakta" if distance is not None else 'mevcut seviye haritasında ileride engel yok') +
                 f" · M1 kapanışı {chart_room['reference']:.2f} ({chart_room['observed_at']})")
        st.caption('Bu mesafe kapanış fiyatından hesaplanır; alış/satış fiyatı ve spread içermez, giriş onayı veya gerçekleşmiş işlem değildir.')
    with st.expander('Veri bağlantıları ve eksiklerin nedeni', expanded=True):
        rows = []
        for key, title in [('quote','Bid/ask + spread'), ('m1','M1'), ('m5','M5'),
                           ('dxy','DXY'), ('yield2','ABD 2Y'), ('yield10','ABD 10Y'),
                           ('calendar','Haber takvimi'), ('levels','Seviye haritası')]:
            value = snapshot.get(key)
            present = bool(value)
            related = [r for r in decision['reasons'] if r.endswith(':'+key) or
                       (key.upper() in r and r.startswith('INVALID_')) or
                       (key == 'calendar' and r.startswith('CALENDAR_')) or
                       (key == 'quote' and r == 'INVALID_SPREAD_DATA')]
            rows.append({'Veri': title, 'Durum': 'Eksik' if not present else 'Kontrol başarısız' if related else 'Bağlı',
                         'Açıklama': ' · '.join(explain(r) for r in related)})
        st.dataframe(rows, hide_index=True, use_container_width=True)
        for interval, message in (local_data or {}).get('errors', {}).items():
            st.warning(f'Twelve Data {interval}: {message}')
        if (local_data or {}).get('calendar_error'):
            st.warning('Takvim: '+local_data['calendar_error'])
        if snapshot.get('side'):
            st.caption(f"İncelenen aday: {snapshot['side']} · yapı seviyesi {snapshot['setup_level']:.2f} · işlem onayı değildir")
        if decision.get('room_to_tp') is not None:
            st.metric('TP öncesi kullanılabilir mesafe ($/oz)', f"{decision['room_to_tp']:.2f}")
    # Use a stable capture timestamp to avoid duplicate setup rows on widget reruns.
    logged = dict(snapshot)
    logged['_legacy'] = legacy
    logged['_config'] = c
    if 'captured_at' in logged:
        logged['as_of'] = logged.pop('captured_at')
    try:
        store_path = Path(os.environ.get('XAU_SETUP_DB', Path(__file__).parent/'data'/'setups.sqlite'))
        store_path.parent.mkdir(parents=True, exist_ok=True)
        store = SetupStore(store_path)
        try:
            store.update(snapshot, decision, c)
            key = store.record(logged, decision)
            if st.button('İzinli setup için paper kayıt aç', disabled=not decision['tradeable']):
                store.open_paper(key, snapshot, decision, c)
                store.update(snapshot, decision, c)
            summary = store.summary(c)
            st.caption(f"Setup kayıtları: {summary['decisions']} · kapanan paper: {summary['closed_paper']} · Large-Loser Probability: hesaplanmıyor ({summary['model_status']})")
            st.caption('MAE/MFE yalnız gözlenen quote’ları kapsar; aradaki hareketler bilinmez. Uygulama kapalıyken takip yapılmaz.')
            positions = store.db.execute('SELECT side,opened_at,mae,mfe,status,exit_reason FROM positions ORDER BY opened_at DESC LIMIT 20').fetchall()
            if positions:
                st.dataframe([dict(p) for p in positions])
        finally:
            store.close()
    except Exception:
        decision['tradeable'] = False
        decision['reasons'].append('SETUP_LOG_UNAVAILABLE')
    if decision['reasons']:
        st.error('WHY NOT TRADE — ' + ' · '.join(explain(r) for r in decision['reasons']))
    else:
        st.success('Risk ve setup kontrolleri geçti — yalnız paper değerlendirme')
    return decision
