"""Shared provider reads. Never substitute receipt time for a market observation."""
from datetime import datetime, timezone, timedelta
from io import StringIO
import math
import json
import time
from pathlib import Path
import pandas as pd
import requests
import streamlit as st


SIZES = {'1day': 500, '1h': 200, '4h': 10, '1min': 100, '5min': 320}
REFRESH_SECONDS = json.loads(Path(__file__).with_name('feed_config.json').read_text()).get(
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


def time_series(api_key, interval):
    seconds = REFRESH_SECONDS[interval]
    if not isinstance(seconds, (int, float)) or seconds <= 0:
        raise ValueError('invalid time series cache duration')
    return _time_series(api_key, interval, int(time.time() // seconds))


@st.cache_data(ttl=86400, max_entries=512, show_spinner=False)
def _time_series(api_key, interval, refresh_bucket):
    try:
        response = requests.get('https://api.twelvedata.com/time_series', params={
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
        if df['datetime'].duplicated().any():
            return None, 'Sağlayıcı aynı zaman için birden fazla mum döndürdü'
        return df.sort_values('datetime').reset_index(drop=True), None
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
def economic_calendar(api_key):
    now = datetime.now(timezone.utc)
    start = (now-timedelta(days=1)).date().isoformat()
    end = (now+timedelta(days=2)).date().isoformat()
    result = {'observed_at': now.isoformat(), 'complete': False, 'events': [],
              'coverage_start': start+'T00:00:00+00:00', 'coverage_end': end+'T00:00:00+00:00'}
    try:
        response = requests.get('https://finnhub.io/api/v1/calendar/economic',
            params={'token': api_key, 'from': start, 'to': end}, timeout=15)
        data = response.json()
        if not isinstance(data, dict) or not isinstance(data.get('economicCalendar'), list):
            return result, provider_error(data, getattr(response, 'status_code', 200))
        events = []
        for event in data['economicCalendar']:
            if event.get('country') != 'US':
                continue
            when = pd.to_datetime(event['time'], utc=True)
            if pd.isna(when):
                raise ValueError('event time missing')
            events.append({**event, 'time': when.isoformat()})
        result.update(complete=True, events=events)
        return result, None
    except Exception:
        return result, 'Ekonomik takvim erişimi veya zaman bilgisi geçersiz'
