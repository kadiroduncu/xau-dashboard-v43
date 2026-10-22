"""Cloud-only read view. Collection and notifications run independently in Cloudflare."""
from datetime import datetime, timezone
import json
import requests
import streamlit as st
from cloud_health import cloud_health

st.set_page_config(page_title="XAU v43 · Bulut", layout="wide")
st.title("XAU v43 · Bulut izleyicisi")
st.caption("Mac ve MT5 kapalıyken çalışır · Beş dakikalık kontrol · Otomatik emir yok")

@st.cache_data(ttl=30, show_spinner=False)
def read_state(url, token):
    response = requests.post(url.rstrip('/') + '/cloud-state', headers={'Authorization': 'Bearer ' + token}, timeout=15, allow_redirects=False)
    response.raise_for_status()
    return response.json()

@st.fragment(run_every="30s")
def panel():
    url = st.secrets.get('TELEGRAM_RELAY_URL', '')
    token = st.secrets.get('TELEGRAM_RELAY_TOKEN', '')
    if not url or not token:
        st.error('Bulut bağlantı ayarları henüz eklenmedi.')
        return
    try:
        state = read_state(url, token)
    except Exception:
        st.error('Bulut durumuna erişilemiyor. Son karar güncel kabul edilmemeli.')
        return
    health = cloud_health(state)
    if not isinstance(state, dict):
        st.error(health['reason'])
        return
    checked = state.get('checked_at')
    st.caption('Son bulut kontrolü: ' + str(checked) + ' · Kaynak: Twelve Data XAU/USD')
    if health['last_close']:
        from zoneinfo import ZoneInfo
        st.caption('Son kapanmış M5: ' + health['last_close'].astimezone(ZoneInfo('Europe/Istanbul')).strftime('%d.%m.%Y %H:%M İstanbul'))
    st.info('Referans fiyatlarla kapanmış M5 mumları izlenir. XM bid/ask, gerçek spread ve açık pozisyonlar bu sürümde izlenmez. Bu, yerel MT5 sürümünden ayrı bir bulut profilidir.')
    engine = state.get('engine') if isinstance(state.get('engine'), dict) else {}
    green = health['green']
    if not health['ready']:
        st.error(health['reason'])
    elif green:
        st.success(f"🟢 GİRİŞ ADAYI · {green['direction']} · {green['kind']}")
        st.write(f"Referans giriş: {green['entry']:.2f} · Hedef: {green['target']:.2f} · Teknik iptal: {green['invalidation']:.2f}")
        st.warning('Teknik iptal broker stopu değildir. Bu sinyal işlem açmaz.')
    else:
        st.warning('BEKLE · ' + health['reason'])
    bars = health['bars']
    if bars:
        import pandas as pd
        frame = pd.DataFrame(bars[-100:])
        frame['Zaman'] = pd.to_datetime(frame['close_at'], unit='ms', utc=True)
        st.line_chart(frame.set_index('Zaman')['close'])
    st.subheader('İzlenen seviyeler')
    st.dataframe(engine.get('zones', []), use_container_width=True)
    st.subheader('Son kapanışta değerlendirilen adaylar')
    st.dataframe(engine.get('candidates', []), use_container_width=True)
    st.subheader('Yeşil sinyal geçmişi — güncel giriş önerisi değildir')
    st.dataframe(state.get('history', []), use_container_width=True)
    with st.expander('Çalışma ve veri durumu'):
        st.json({'veri_hazır': state.get('data_ready'), 'motor_hazır': state.get('signal_engine_ready'), 'profil': state.get('profile'), 'hatalar': state.get('errors'), 'son_bildirim': state.get('delivery'), 'emir_iletimi': False})
        # Explicit allowlist: never export relay URL/token, secrets or raw provider errors.
        audit = {
            'schema': 'xau-reference-audit-v1', 'checked_at': checked,
            'profile': state.get('profile'),
            'bars_5min': state.get('series', {}).get('5min', {}).get('bars', []),
            'history': state.get('history', []),
            'execution_quote': False,
            'note': 'Referans sinyal ve mum arşivi; XM gerçekleşmiş işlem kaydı değildir.'}
        st.download_button('Denetim verisini indir (JSON)',
            json.dumps(audit, ensure_ascii=False, allow_nan=False),
            file_name='xau-reference-audit.json', mime='application/json')
        from reference_replay import replay_reference
        try:
            results = replay_reference(audit)
            st.write('Mevcut mum penceresindeki sonuçlar — varsayımsal kapanış girişi')
            st.write({'kayıt': results['total'], 'sonuçlar': results['counts'],
                      'hedef_oranı': results['target_rate'],
                      'bildirim_gecikmesi_medyan_sn': results['median_delivery_delay_seconds']})
            st.caption(results['note'])
            if results['partial_history']:
                st.warning('Mum arşivi kayan bir penceredir. Eski mumlar çıktığında önceki sonuçlar burada yeniden doğrulanamaz; eksik kayıtlar bu orana dahil değildir. Bu oran tüm geçmişin başarı oranı değildir. Önceki doğrulanmış sonuçlar ayrı bakım denetim kayıtlarında korunur.')
        except (ValueError, TypeError, KeyError):
            st.warning('Sinyal sonucu için mum/kayıt biçimi doğrulanamadı; başarı oranı hesaplanmadı.')
panel()


