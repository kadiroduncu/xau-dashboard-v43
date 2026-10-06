"""Measured reference bid/ask and provider-timestamped context, never broker orders."""
from datetime import datetime, timezone, timedelta
from pathlib import Path
import json
import math
import os
import sqlite3
import requests
import streamlit as st

CONFIG = json.loads(Path(__file__).with_name('feed_config.json').read_text())
QUOTE_URL = 'https://forex-data-feed.swissquote.com/public-quotes/bboquotes/instrument/XAU/USD'


def parse_swissquote(payload):
    """Pin one published venue/profile; never mix profiles or select the narrowest spread."""
    selection = CONFIG['reference_quote']
    matches = []
    for group in payload:
        topo = group.get('topo', {})
        if (topo.get('platform'), topo.get('server')) != (selection['platform'], selection['server']):
            continue
        for row in group.get('spreadProfilePrices', []):
            if row.get('spreadProfile') != selection['profile']:
                continue
            if any(isinstance(row[k], bool) for k in ('bid','ask')):
                raise ValueError('Boolean price')
            bid, ask = float(row['bid']), float(row['ask'])
            if not all(math.isfinite(v) for v in (bid, ask)) or not 0 < bid < ask:
                raise ValueError('Invalid bid/ask')
            when = datetime.fromtimestamp(float(group['ts'])/1000, timezone.utc)
            matches.append({'bid': bid, 'ask': ask, 'observed_at': when.isoformat(),
                            'source': f"Swissquote / {selection['platform']} / {selection['server']} / {selection['profile']}", 'symbol': 'XAU/USD',
                            'scope': 'REFERENCE_PAPER_ONLY'})
    if len(matches) != 1:
        raise ValueError('Configured quote profile unavailable or duplicated')
    return matches[0]


@st.cache_data(ttl=30, show_spinner=False)
def fetch_reference_quote():
    try:
        r = requests.get(QUOTE_URL, timeout=10)
        if r.status_code != 200:
            return None, f'Swissquote HTTP {r.status_code}'
        return parse_swissquote(r.json()), None
    except Exception as exc:
        return None, 'Swissquote veri bağlantısı: ' + type(exc).__name__


def record_quote(quote, path, now=None):
    """Shared disk history, deduplicated by source observation time; stale quotes cannot seed it."""
    now = now or datetime.now(timezone.utc)
    when = datetime.fromisoformat(quote['observed_at'])
    age = (now-when).total_seconds()
    if not -5 <= age <= CONFIG['reference_quote']['max_age_seconds']:
        return {**quote, 'spread_history': []}, 'Referans bid/ask güncel değil'
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path, timeout=10) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE IF NOT EXISTS reference_quotes (source TEXT, observed_at TEXT, bid REAL, ask REAL, PRIMARY KEY(source, observed_at))')
        existing = db.execute('SELECT bid,ask FROM reference_quotes WHERE source=? AND observed_at=?',
                              (quote['source'], quote['observed_at'])).fetchone()
        if existing and existing != (quote['bid'], quote['ask']):
            raise ValueError('Conflicting quote at identical source timestamp')
        db.execute('INSERT OR IGNORE INTO reference_quotes VALUES(?,?,?,?)',
                   (quote['source'], quote['observed_at'], quote['bid'], quote['ask']))
        cutoff = (now-timedelta(minutes=120)).isoformat()
        db.execute('DELETE FROM reference_quotes WHERE observed_at < ?', ((now-timedelta(days=2)).isoformat(),))
        rows = db.execute('SELECT observed_at,ask-bid FROM reference_quotes WHERE source=? AND observed_at>=? AND observed_at<? ORDER BY observed_at',
                          (quote['source'], cutoff, quote['observed_at'])).fetchall()
    return {**quote, 'spread_history': [{'observed_at': t, 'spread': v} for t,v in rows]}, None


def connected_quote():
    quote, error = fetch_reference_quote()
    if quote is None:
        return None, error
    default = Path(os.environ.get('XAU_SETUP_DB', Path(__file__).parent/'data'/'setups.sqlite')).with_name('market_quotes.sqlite')
    path = os.environ.get('XAU_QUOTES_DB', str(default))
    try:
        return record_quote(quote, path)
    except Exception as exc:
        return {**quote, 'spread_history': []}, 'Spread geçmişi kaydedilemedi: ' + type(exc).__name__

MACRO_SYMBOLS = {'TVC:DXY': 'dxy', 'TVC:US02Y': 'yield2', 'TVC:US10Y': 'yield10'}
MACRO_COLUMNS = ['name', 'description', 'type', 'update_mode', 'close|1', 'time|1', 'close[1]|1', 'time[1]|1']


def parse_macro(payload):
    """Keep minute-bar source times; daily changes and receipt timestamps never become observations."""
    result = {}
    for row in payload['data']:
        symbol = row['s']
        if symbol not in MACRO_SYMBOLS or symbol in result:
            raise ValueError('Unexpected or duplicate macro symbol')
        d = dict(zip(MACRO_COLUMNS, row['d'], strict=True))
        expected_type = 'index' if symbol == 'TVC:DXY' else 'bond'
        if d['name'] != symbol.split(':')[1] or d['type'] != expected_type or d['update_mode'] != 'streaming':
            raise ValueError('Unexpected identity or delayed feed')
        value, previous = float(d['close|1']), float(d['close[1]|1'])
        current_time, previous_time = int(d['time|1']), int(d['time[1]|1'])
        if not all(math.isfinite(v) and v > 0 for v in (value, previous)) or previous_time >= current_time:
            raise ValueError('Invalid macro bars')
        result[symbol] = {'key': MACRO_SYMBOLS[symbol], 'symbol': symbol,
                          'source': 'TradingView / TVC', 'value': value,
                          'observed_at': datetime.fromtimestamp(current_time, timezone.utc).isoformat(),
                          'bar_start': current_time, 'previous_bar_start': previous_time,
                          'timestamp_kind': 'current_minute_bar_start', 'update_mode': d['update_mode']}
    if set(result) != set(MACRO_SYMBOLS):
        raise ValueError('Missing macro symbol')
    return result


@st.cache_data(ttl=60, show_spinner=False)
def fetch_macro():
    try:
        r = requests.post('https://scanner.tradingview.com/global/scan', json={
            'symbols': {'tickers': list(MACRO_SYMBOLS)}, 'columns': MACRO_COLUMNS}, timeout=10)
        if r.status_code != 200:
            return None, f'Makro referans verisi HTTP {r.status_code}'
        rows = parse_macro(r.json())
        sampled_at = datetime.now(timezone.utc).isoformat()
        for row in rows.values():
            row['sampled_at'] = sampled_at
        return rows, None
    except Exception as exc:
        return None, 'Makro veri bağlantısı: ' + type(exc).__name__


def record_macro(rows, path, now=None):
    """Compare simultaneous request batches; retain distinct provider observation times.

    The period is elapsed time between the two sampled baskets, not fabricated tick
    timestamps. Every input must be fresh at BOTH endpoints; stale data never seeds
    a usable baseline. Missing intervals are never interpolated.
    """
    now = now or datetime.now(timezone.utc)
    cfg = CONFIG['intraday_macro']
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    values = {row['key']: dict(row) for row in rows.values()}
    sampled = {row['sampled_at'] for row in rows.values()}
    if len(sampled) != 1 or set(rows) != set(MACRO_SYMBOLS):
        raise ValueError('Macro request batch mismatch')
    captured = datetime.fromisoformat(sampled.pop())
    if not -5 <= (now-captured).total_seconds() <= cfg['max_age_seconds']:
        return values, 'Makro isteği güncel değil'
    ages = [(captured-datetime.fromisoformat(row['observed_at'])).total_seconds() for row in rows.values()]
    if not all(0 <= age <= cfg['max_age_seconds'] for age in ages):
        return values, 'Makro kaynağında eski gözlem var; yeni veri bekleniyor'
    with sqlite3.connect(path, timeout=10) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE IF NOT EXISTS macro_samples (sampled_at TEXT PRIMARY KEY, payload TEXT NOT NULL)')
        payload = json.dumps(rows, sort_keys=True)
        prior = db.execute('SELECT payload FROM macro_samples WHERE sampled_at=?', (captured.isoformat(),)).fetchone()
        if prior and prior[0] != payload:
            raise ValueError('Conflicting macro request batch')
        db.execute('INSERT OR IGNORE INTO macro_samples VALUES(?,?)', (captured.isoformat(), payload))
        db.execute('DELETE FROM macro_samples WHERE sampled_at < ?', ((now-timedelta(minutes=cfg['history_minutes'])).isoformat(),))
        target = captured-timedelta(seconds=cfg['change_period_seconds'])
        lower = target-timedelta(seconds=cfg['period_tolerance_seconds'])
        baseline = db.execute('SELECT sampled_at,payload FROM macro_samples WHERE sampled_at>=? AND sampled_at<=? ORDER BY sampled_at DESC LIMIT 1', (lower.isoformat(),target.isoformat())).fetchone()
    if not baseline:
        return values, 'Makro geçmişi toplanıyor: yaklaşık 5 dakikalık ortak ölçüm aralığı bekleniyor'
    started = datetime.fromisoformat(baseline[0]);old_rows=json.loads(baseline[1])
    period = (captured-started).total_seconds()
    for symbol, key in MACRO_SYMBOLS.items():
        old, new = old_rows[symbol]['value'], rows[symbol]['value']
        values[key].update(change=(new/old-1)*100 if key=='dxy' else (new-old)*100,
                           change_period_seconds=period,
                           baseline_observed_at=old_rows[symbol]['observed_at'],
                           sample_start=started.isoformat(), sample_end=captured.isoformat(),
                           change_unit='percent' if key=='dxy' else 'basis_points')
    return values, None


def connected_macro():
    rows, error = fetch_macro()
    if rows is None:
        return {}, error
    default = Path(os.environ.get('XAU_SETUP_DB', Path(__file__).parent/'data'/'setups.sqlite')).with_name('market_quotes.sqlite')
    path = os.environ.get('XAU_QUOTES_DB', str(default))
    try:
        return record_macro(rows, path)
    except Exception as exc:
        return {}, 'Makro geçmişi kaydedilemedi: ' + type(exc).__name__
