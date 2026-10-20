"""Shared provider reads. Never substitute receipt time for a market observation."""
from datetime import datetime, timezone, timedelta
from io import StringIO
import math
import hashlib
import threading
import json
import time
from pathlib import Path
import pandas as pd
import requests
import streamlit as st


SIZES = {'1day': 500, '1h': 200, '4h': 10, '1min': 100, '5min': 320}
FEED_CONFIG = json.loads(Path(__file__).with_name('feed_config.json').read_text())
REFRESH_SECONDS = FEED_CONFIG.get(
    'time_series_cache_seconds', {'1day': 3600, '1h': 1800, '4h': 7200, '1min': 60, '5min': 60})


def provider_error(payload, status=200):
    # Classify without exposing provider response text (which may contain credentials).
    code = payload.get('code', status) if isinstance(payload, dict) else status
    message = str(payload.get('message', '')).lower() if isinstance(payload, dict) else ''
    if code == 429 or any(x in message for x in ('credit', 'limit', 'quota')):
        if 'day' in message or 'daily' in message:
            return 'Twelve Data günlük kotası doldu; sağlayıcının kota sıfırlaması bekleniyor'
        return 'API istek kotası doldu; önbellek yenilenince tekrar denenecek'
    if code in (401, 403) or any(x in message for x in ('apikey', 'api key', 'permission', 'plan')):
        return 'API anahtarı veya veri aboneliği erişimi gerekli'
    return 'Sağlayıcı geçerli veri döndürmedi'


class ProviderCooldown(Exception):
    pass


_PROVIDER_COOLDOWNS = {}
_PROVIDER_LOCK = threading.Lock()


def provider_get(params, timeout=15):
    """Share quota backoff across intervals; never persist a raw API key."""
    identity = hashlib.sha256(str(params.get('apikey', '')).encode()).hexdigest()
    with _PROVIDER_LOCK:
        until, message = _PROVIDER_COOLDOWNS.get(identity, (0, ''))
        if time.time() < until:
            raise ProviderCooldown(message)
    response = requests.get('https://api.twelvedata.com/time_series', params=params, timeout=timeout)
    try:
        payload = response.json()
    except ValueError:
        return response
    if isinstance(payload, dict):
        code = payload.get('code', getattr(response, 'status_code', 200))
        message = str(payload.get('message', '')).lower()
        limited = code == 429 or (not payload.get('values') and any(x in message for x in ('credit', 'quota', 'limit')))
        if limited:
            now = datetime.fromtimestamp(time.time(), timezone.utc)
            daily = 'day' in message or 'daily' in message
            until = ((now + timedelta(days=1)).replace(hour=0, minute=0, second=2, microsecond=0).timestamp()
                     if daily else (int(time.time() // 60) + 1) * 60 + 2)
            with _PROVIDER_LOCK:
                _PROVIDER_COOLDOWNS[identity] = (until, provider_error(payload, code))
    return response


def closed_at_fetch(frame, interval, fetched_at=None):
    """Never let a cached, unfinished candle become final merely because wall time advances."""
    if interval not in ('1min', '5min'):
        return frame
    fetched_at = fetched_at or datetime.now(timezone.utc)
    cutoff = pd.Timestamp(fetched_at).tz_convert('UTC').tz_localize(None)
    minutes = 1 if interval == '1min' else 5
    return frame.loc[frame['datetime'] + pd.Timedelta(minutes=minutes) <= cutoff].copy()


def verify_conflicting_daily_candles(api_key, frame):
    """Resolve at most three closed dates with independent date-bounded requests.

    Never choose first/last/average from a conflict. A date is usable only if
    the date-specific provider response is unique, valid and matches one of
    the original candidates. An ambiguous/unavailable reply keeps the veto.
    """
    dates = sorted(frame.loc[frame['datetime'].duplicated(keep=False), 'datetime'].unique())
    if not dates or len(dates) > 3:
        return frame, []
    result = frame.copy()
    resolved = []
    audit = []
    result.attrs['conflict_recheck'] = audit
    today = pd.Timestamp.now(tz='UTC').tz_localize(None).normalize()
    for date in dates:
        stamp = pd.Timestamp(date)
        if stamp != stamp.normalize() or stamp >= today:
            continue
        attempt = {'date':stamp.date().isoformat(), 'status':'yanıt doğrulanamadı'}
        audit.append(attempt)
        try:
            response = provider_get(params={
                'symbol': 'XAU/USD', 'interval': '1day', 'apikey': api_key,
                'start_date': stamp.isoformat(),
                'end_date': (stamp + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)).isoformat(),
                'timezone': 'UTC'}, timeout=15)
            payload = response.json()
            attempt['http_status'] = getattr(response, 'status_code', 200)
            if getattr(response, 'status_code', 200) != 200 or not isinstance(payload, dict):
                continue
            meta = payload.get('meta') or {}
            if meta.get('symbol', 'XAU/USD') != 'XAU/USD' or meta.get('interval', '1day') != '1day':
                continue
            rows = payload.get('values')
            attempt['returned_rows'] = len(rows) if isinstance(rows, list) else 0
            if not isinstance(rows, list) or not rows or len(rows) > 4:
                continue
            candidate = pd.DataFrame(rows)
            candidate['datetime'] = pd.to_datetime(candidate['datetime'], utc=True, format='mixed').dt.tz_localize(None)
            attempt['returned_dates'] = [x.isoformat() for x in candidate['datetime']]
            candidate = candidate.loc[candidate['datetime'] == stamp].copy()
            columns = ['open', 'high', 'low', 'close']
            for column in columns:
                candidate[column] = pd.to_numeric(candidate[column], errors='raise')
            candidate = candidate.drop_duplicates(subset=['datetime'] + columns)
            attempt['unique_date_rows'] = len(candidate)
            if len(candidate) != 1:
                attempt['status'] = 'tarih yanıtı eksik veya çelişkili'
                continue
            values = candidate.iloc[0]
            if not all(math.isfinite(values[k]) and values[k] > 0 for k in columns):
                continue
            if values['low'] > min(values['open'], values['close']) or values['high'] < max(values['open'], values['close']):
                continue
            original = result.loc[result['datetime'] == stamp]
            matches = original[columns].apply(lambda row: all(math.isclose(row[k], values[k], rel_tol=1e-10, abs_tol=1e-8) for k in columns), axis=1)
            if not matches.any():
                attempt['status'] = 'tarih yanıtı ilk adaylarla eşleşmiyor'
                continue
            result = pd.concat([result.loc[result['datetime'] != stamp], original.loc[matches].iloc[:1]], ignore_index=True)
            attempt['status'] = 'doğrulandı'
            resolved.append(stamp.date().isoformat())
        except (ProviderCooldown, requests.RequestException, ValueError, TypeError, KeyError):
            continue
    return result, resolved



def verify_daily_history_page(api_key, frame):
    """Re-read the full date window; accept only a complete, matching unique page.

    This avoids a broken single-date endpoint without joining over a missing
    date or selecting an arbitrary duplicate. Every original date must exist;
    every returned candle must match one of that date's original candidates.
    """
    audit = list(frame.attrs.get('conflict_recheck', []))
    attempt = {'method': 'full_date_window', 'status': 'yanıt doğrulanamadı'}
    audit.append(attempt)
    frame.attrs['conflict_recheck'] = audit
    try:
        start, end = frame['datetime'].min(), frame['datetime'].max()
        response = provider_get(params={
            'symbol': 'XAU/USD', 'interval': '1day', 'apikey': api_key,
            'start_date': start.isoformat(),
            'end_date': (end + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)).isoformat(),
            'timezone': 'UTC', 'order': 'asc'}, timeout=15)
        payload = response.json()
        attempt['http_status'] = getattr(response, 'status_code', 200)
        if attempt['http_status'] != 200 or not isinstance(payload, dict):
            return frame, []
        meta = payload.get('meta') or {}
        if meta.get('symbol') != 'XAU/USD' or meta.get('interval') != '1day':
            return frame, []
        rows = payload.get('values')
        attempt['returned_rows'] = len(rows) if isinstance(rows, list) else 0
        if not isinstance(rows, list) or not rows or len(rows) > 5000:
            return frame, []
        page = pd.DataFrame(rows)
        page['datetime'] = pd.to_datetime(page['datetime'], utc=True, format='mixed').dt.tz_localize(None)
        columns = ['open', 'high', 'low', 'close']
        for column in columns:
            page[column] = pd.to_numeric(page[column], errors='raise')
        page = page.drop_duplicates(subset=['datetime'] + columns)
        if page.datetime.isna().any() or page.datetime.duplicated().any():
            attempt['status'] = 'aralık yanıtı da çelişkili'
            return frame, []
        if set(page.datetime) != set(frame.datetime):
            attempt['status'] = 'aralık yanıtının tarih kapsamı eşleşmiyor'
            return frame, []
        for row in page.to_dict('records'):
            original = frame.loc[frame.datetime == row['datetime'], columns]
            if not original.apply(lambda candidate: all(
                    math.isfinite(row[k]) and math.isclose(candidate[k], row[k], rel_tol=1e-10, abs_tol=1e-8)
                    for k in columns), axis=1).any():
                attempt['status'] = 'aralık yanıtının fiyatları ilk adaylarla eşleşmiyor'
                return frame, []
        dates = sorted(frame.loc[frame.datetime.duplicated(keep=False), 'datetime'].unique())
        today = pd.Timestamp.now(tz='UTC').tz_localize(None).normalize()
        if any(pd.Timestamp(date) >= today for date in dates):
            return frame, []
        attempt['status'] = 'tüm tarih ve fiyat kapsamı doğrulandı'
        page.attrs['conflict_recheck'] = audit
        return page, [pd.Timestamp(date).date().isoformat() for date in dates]
    except (ProviderCooldown, requests.RequestException, ValueError, TypeError, KeyError):
        return frame, []


def time_series(api_key, interval):
    seconds = REFRESH_SECONDS[interval]
    if not isinstance(seconds, (int, float)) or seconds <= 0:
        raise ValueError('invalid time series cache duration')
    return _time_series(api_key, interval, ('daily-recheck-v3', int(time.time() // seconds)))


@st.cache_data(ttl=86400, max_entries=512, show_spinner=False)
def _time_series(api_key, interval, refresh_bucket):
    try:
        response = provider_get(params={
            'symbol': 'XAU/USD', 'interval': interval, 'outputsize': SIZES[interval],
            'apikey': api_key, 'timezone': 'UTC'}, timeout=15)
        payload = response.json()
        if getattr(response, 'status_code', 200) != 200 or not isinstance(payload, dict) or not payload.get('values'):
            return None, provider_error(payload, getattr(response, 'status_code', 200))
        df = pd.DataFrame(payload['values'])
        df['datetime'] = pd.to_datetime(df['datetime'], utc=True, format='mixed').dt.tz_localize(None)
        if df['datetime'].isna().any():
            return None, 'Sağlayıcı mum zamanını boş döndürdü'
        for name in ('open', 'high', 'low', 'close'):
            df[name] = pd.to_numeric(df[name], errors='raise')
            if not df[name].map(lambda v: math.isfinite(v) and v > 0).all():
                return None, 'Sağlayıcı sıfır, negatif veya sonlu olmayan mum fiyatı döndürdü'
        if not ((df['low'] <= df[['open','close']].min(axis=1)) &
                (df['high'] >= df[['open','close']].max(axis=1))).all():
            return None, 'Sağlayıcı OHLC sıralaması tutarsız: açılış/kapanış high-low dışında'
        df = closed_at_fetch(df, interval)
        # Some provider pages repeat the same daily candle. Identical OHLC rows
        # carry no additional observation; conflicting candles remain invalid.
        df = df.drop_duplicates(subset=['datetime', 'open', 'high', 'low', 'close'])
        verified_dates = []
        if interval == '1day' and df['datetime'].duplicated().any():
            df, verified_dates = verify_conflicting_daily_candles(api_key, df)
            if df['datetime'].duplicated().any():
                df, page_dates = verify_daily_history_page(api_key, df)
                verified_dates.extend(page_dates)
        if df['datetime'].duplicated().any():
            ambiguous = df.loc[df['datetime'].duplicated(keep=False), 'datetime'].max()
            # A contiguous suffix after the last ambiguous candle is usable
            # without picking a price or bridging a hole in the history.
            recent = df.loc[df['datetime'] > ambiguous].sort_values('datetime').reset_index(drop=True)
            message = f'Sağlayıcı {ambiguous.isoformat()} için farklı OHLC değerleri döndürdü; sonrasındaki doğrulanmış mum sayısı: {len(recent)}'
            if interval == '1day' and len(recent) < FEED_CONFIG.get('daily_min_history', 22):
                # Daily context may use a recent, completed historical segment.
                # Never join across a conflicting day, and disclose its end date.
                conflicts = sorted(df.loc[df['datetime'].duplicated(keep=False), 'datetime'].unique())
                cutoff = pd.Timestamp.now(tz='UTC').tz_localize(None) - pd.Timedelta(days=FEED_CONFIG.get('daily_max_age_days', 5))
                for index in range(len(conflicts)-1, -1, -1):
                    segment = df.loc[df['datetime'] < conflicts[index]]
                    if index:
                        segment = segment.loc[segment['datetime'] > conflicts[index-1]]
                    segment = segment.sort_values('datetime').reset_index(drop=True)
                    if len(segment) >= FEED_CONFIG.get('daily_min_history', 22) and segment['datetime'].iloc[-1] >= cutoff:
                        return segment, message + f". Günlük analizde kullanılan son tutarlı dönem: {segment['datetime'].iloc[-1].date()} ({len(segment)} mum); sonraki kayıtlar analize katılmadı."
            if df.attrs.get('conflict_recheck'):
                message += '. Tarih sorgusu kontrolü: ' + json.dumps(df.attrs['conflict_recheck'], ensure_ascii=False)
            return (recent if len(recent) else None), message
        note = ('Günlük mum çakışması bağımsız tarih sorgusuyla doğrulandı: ' + ', '.join(verified_dates)) if verified_dates else None
        return df.sort_values('datetime').reset_index(drop=True), note
    except ProviderCooldown as error:
        return None, str(error)
    except requests.Timeout:
        return None, 'Twelve Data bağlantısı zaman aşımına uğradı'
    except requests.RequestException:
        return None, 'Twelve Data ağ bağlantısı kurulamadı'
    except (ValueError, TypeError, KeyError):
        return None, 'Sağlayıcının zaman/fiyat biçimi geçersiz veya zorunlu alan eksik'


def closed_bars(df, minutes, now=None):
    if df is None:
        return []
    now = now or datetime.now(timezone.utc)
    rows = []
    for record in df.to_dict('records'):
        start = pd.Timestamp(record['datetime'])
        start = start.tz_localize('UTC') if start.tzinfo is None else start.tz_convert('UTC')
        end = start + timedelta(minutes=minutes)
        if end <= now:
            rows.append({'closed_at': end.isoformat(), 'datetime': start.isoformat(),
                         **{k: float(record[k]) for k in ('open','high','low','close')}})
    return rows


@st.cache_data(ttl=3600, show_spinner=False)
def cot_history():
    try:
        response = requests.get('https://publicreporting.cftc.gov/resource/72hh-3qpy.json',
            params={'market_and_exchange_names': 'GOLD - COMMODITY EXCHANGE INC.',
                    '$order': 'report_date_as_yyyy_mm_dd DESC', '$limit': 156}, timeout=15)
        data = response.json()
        if not isinstance(data, list) or len(data) < 26:
            return None, 'CFTC COT geçmişi alınamadı'
        rows = [{'date': r['report_date_as_yyyy_mm_dd'][:10],
                 'mm_net': int(r['m_money_positions_long_all'])-int(r['m_money_positions_short_all'])}
                for r in reversed(data)]
        return rows, None
    except Exception:
        return None, 'CFTC bağlantısı veya COT yanıtı geçersiz'


@st.cache_data(ttl=3600, show_spinner=False)
def gvz_history():
    try:
        response = requests.get('https://cdn-api.cboe.com/api/global/us_indices/daily_prices/GVZ_History.csv', timeout=15)
        df = pd.read_csv(StringIO(response.text))
        df.columns = [str(c).strip().upper() for c in df.columns]
        date = pd.to_datetime(df['DATE'], format='%m/%d/%Y')
        values = pd.to_numeric(df['GVZ'] if 'GVZ' in df else df['CLOSE'])
        rows = sorted([{'date': d.strftime('%Y-%m-%d'), 'close': float(v)}
                       for d, v in zip(date, values) if math.isfinite(v) and v > 0], key=lambda r:r['date'])
        return (rows[-252:], None) if len(rows) >= 30 else (None, 'GVZ geçmişi yetersiz')
    except Exception:
        return None, 'Cboe günlük GVZ verisi alınamadı'


@st.cache_data(ttl=300, show_spinner=False)
def economic_calendar(api_key, fred_key=None):
    now = datetime.now(timezone.utc)
    start = (now-timedelta(days=1)).date().isoformat()
    end = (now+timedelta(days=2)).date().isoformat()
    result = {'observed_at': now.isoformat(), 'complete': False, 'events': [],
              'coverage_start': start+'T00:00:00+00:00', 'coverage_end': end+'T00:00:00+00:00'}
    try:
        response = requests.get('https://finnhub.io/api/v1/calendar/economic',
            params={'token': api_key, 'from': start, 'to': end}, timeout=15)
        data = response.json()
        if getattr(response, 'status_code', 200) != 200 or not isinstance(data, dict) or not isinstance(data.get('economicCalendar'), list):
            raise ValueError('Calendar unavailable')
        events = []
        for event in data['economicCalendar']:
            if event.get('country') != 'US':
                continue
            when = pd.to_datetime(event['time'], utc=True)
            if pd.isna(when):
                raise ValueError('event time missing')
            events.append({**event, 'time': when.isoformat()})
        result.update(complete=True, events=events, source='Finnhub', scope='US economic calendar')
        return result, None
    except Exception:
        from official_calendar import official_calendar
        fallback, error = official_calendar(fred_key, now)
        fallback['fallback_reason'] = 'Finnhub takvim erişimi doğrulanamadı; resmî kaynaklar kullanılıyor.'
        return fallback, error

