"""Scheduled Tier-1 fallback. FRED dates are NOT invented intraday timestamps."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import json
import requests

UTC = timezone.utc
EASTERN = ZoneInfo('America/New_York')
BEA_URL = 'https://apps.bea.gov/API/signup/release_dates.json'
FED_URL = 'https://www.federalreserve.gov/json/calendar.json'
FRED_URL = 'https://api.stlouisfed.org/fred/release/dates'


def event(name, when, source, **extra):
    if when.tzinfo is None:
        raise ValueError('Timezone required')
    return dict(event=name, time=when.astimezone(UTC).isoformat(), country='US',
                impact='high', source=source, **extra)


def bea_events(data):
    dates = data['Personal Income and Outlays']['release_dates']
    if not dates:
        raise ValueError('No PCE dates')
    return [event('PCE / Personal Income and Outlays', datetime.fromisoformat(d), 'BEA')
            for d in sorted(set(dates))]


def fed_events(data):
    result = []
    for row in data['events']:
        if row.get('type') != 'FOMC':
            continue
        # Only parse published clock times, with US daylight-saving rules.
        clock = row['time'].replace('a.m.', 'AM').replace('p.m.', 'PM').strip()
        for day in row['days'].split(','):
            when = datetime.strptime(f"{row['month']}-{int(day):02d} {clock}",
                                     '%Y-%m-%d %I:%M %p').replace(tzinfo=EASTERN)
            result.append(event(row['title'].strip(), when, 'Federal Reserve'))
    if not result:
        raise ValueError('No FOMC dates')
    return result


def fred_events(data, release_id, name):
    rows = data['release_dates']
    if not rows or int(data.get('count', len(rows))) > len(rows):
        raise ValueError('Empty or truncated release dates')
    result = []
    for row in rows:
        if int(row['release_id']) != release_id:
            raise ValueError('Wrong release')
        start = datetime.strptime(row['date'], '%Y-%m-%d').replace(tzinfo=EASTERN)
        end = start + timedelta(days=1)
        result.append(event(name, start, 'FRED / BLS', time_precision='date',
                            window_start=start.astimezone(UTC).isoformat(),
                            window_end=end.astimezone(UTC).isoformat()))
    return result


def fetch_json(url, **kwargs):
    response = requests.get(url, timeout=15, **kwargs)
    if getattr(response, 'status_code', 200) != 200:
        raise ValueError('Provider unavailable')
    try:
        return response.json()
    except ValueError:
        return json.loads(response.content.decode('utf-8-sig'))


def official_calendar(fred_key, now):
    start = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=3)
    groups, errors = [], []
    sources = [
        ('BEA PCE', lambda: bea_events(fetch_json(BEA_URL))),
        ('Fed FOMC', lambda: fed_events(fetch_json(FED_URL))),
    ]
    for rid, name in [(50, 'NFP / Nonfarm Payrolls'), (10, 'CPI / Consumer Price Index')]:
        def fetch_release(rid=rid, name=name):
            if not fred_key:
                raise ValueError('Missing FRED key')
            return fred_events(fetch_json(FRED_URL, params={
                'api_key': fred_key, 'file_type': 'json', 'release_id': rid,
                'include_release_dates_with_no_data': 'true',
                'realtime_start': (start-timedelta(days=100)).date().isoformat(),
                'realtime_end': (end+timedelta(days=120)).date().isoformat(),
            }), rid, name)
        sources.append((name, fetch_release))
    for name, fetch in sources:
        try:
            rows = fetch()
            times = [datetime.fromisoformat(r['time']) for r in rows]
            # Expired/one-sided schedules cannot certify an empty current window.
            if min(times) > start or max(times) < end:
                raise ValueError('Schedule does not bracket current window')
            groups.extend(rows)
        except Exception:
            errors.append(name + ': takvim veya güncel kapsam doğrulanamadı')
    rows = [r for r in groups if start <= datetime.fromisoformat(r['time']) <= end]
    unique = {(r['event'], r['time']): r for r in rows}
    result = dict(observed_at=now.isoformat(), complete=not errors,
                  coverage_start=start.isoformat(), coverage_end=end.isoformat(),
                  events=sorted(unique.values(), key=lambda r:r['time']),
                  source='BEA + Federal Reserve + FRED/BLS',
                  scope='NFP / CPI / PCE / FOMC (planlı duyurular)',
                  source_errors=errors,
                  note='NFP/CPI yalnız tarih: ABD Doğu saatine göre tüm gün veto. Diğer ekonomik duyurular bu yedek takvimin kapsamında değildir.')
    return result, '; '.join(errors) or None


def legacy_events(events):
    """Adapt aware timestamps only at the boundary of the unchanged v42 NEWS API."""
    return [{**row, 'time': datetime.fromisoformat(row['time']).astimezone(UTC).strftime('%Y-%m-%d %H:%M:%S')}
            for row in events if row.get('time_precision') != 'date']
