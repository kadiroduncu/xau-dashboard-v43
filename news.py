"""Finnhub haber + ekonomik takvim"""
import requests
from datetime import datetime, timedelta, timezone

GOLD_KEYWORDS = ['gold', 'xau', 'dollar', 'dxy', 'fed', 'fomc',
                 'trump', 'tariff', 'iran', 'israel', 'china',
                 'rate cut', 'rate hike', 'cpi', 'nfp', 'payroll',
                 'inflation', 'pce', 'powell']

def fetch_news(api_key, hours_back=4, limit=15):
    try:
        r = requests.get("https://finnhub.io/api/v1/news",
                         params={"category": "general", "token": api_key},
                         timeout=10).json()
    except Exception:
        return []
    if not isinstance(r, list):
        return []
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours_back)).timestamp()
    filtered = [n for n in r
                if n.get('datetime', 0) > cutoff
                and any(k in n.get('headline', '').lower() for k in GOLD_KEYWORDS)]
    return sorted(filtered, key=lambda x: -x['datetime'])[:limit]

def fetch_calendar(api_key):
    today = datetime.utcnow().date()
    try:
        r = requests.get("https://finnhub.io/api/v1/calendar/economic",
                         params={"token": api_key,
                                 "from": today.isoformat(),
                                 "to": (today + timedelta(days=1)).isoformat()},
                         timeout=10).json()
    except Exception:
        return []
    events = r.get('economicCalendar', []) or []
    return [e for e in events
            if e.get('country') == 'US'
            and e.get('impact') in ('high', 'medium')]
