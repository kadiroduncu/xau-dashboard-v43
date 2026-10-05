"""Pure, fail-closed v43 decision engine. No broker or network dependencies.
Scores are configurable heuristics, NEVER calibrated probabilities.
Snapshot timestamps are ISO-8601 with an explicit UTC offset.
"""
from datetime import datetime, timezone, timedelta
from pathlib import Path
import hashlib
import json
import math
import statistics

DEFAULT_CONFIG = Path(__file__).with_name('risk_config.json')


def number(value):
    if isinstance(value, bool):
        raise ValueError('boolean is not a price')
    result = float(value)
    if not math.isfinite(result):
        raise ValueError('nonfinite number')
    return result


def timestamp(value):
    result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('timestamp must include timezone')
    return result.astimezone(timezone.utc)


def load_config(path=DEFAULT_CONFIG):
    c = json.loads(Path(path).read_text())
    default = json.loads(DEFAULT_CONFIG.read_text())
    if c.get('schema_version') != 1:
        raise ValueError('unsupported config schema')
    if set(c) != set(default):
        raise ValueError('config keys must match schema')
    for key, value in c.items():
        if isinstance(value, dict):
            if set(value) != set(default[key]):
                raise ValueError(f'config keys: {key}')
            for v in value.values():
                if number(v) <= 0:
                    raise ValueError(f'positive config required: {key}')
        elif number(value) <= 0:
            raise ValueError(f'positive config required: {key}')
    if not 0 < c['tail_medium'] < c['tail_high'] < c['tail_extreme'] <= 100:
        raise ValueError('tail thresholds order')
    if not 0 < c['quality_b'] < c['quality_a'] < c['quality_a_plus'] <= 12:
        raise ValueError('quality thresholds order')
    for k in ('atr_period', 'jump_period', 'spread_samples', 'acceptance_closes', 'minimum_model_samples', 'minimum_model_losses'):
        if not isinstance(c[k], int) or c[k] < 2:
            raise ValueError(f'integer window required: {k}')
    return c


def config_hash(c):
    return hashlib.sha256(json.dumps(c, sort_keys=True).encode()).hexdigest()


def fresh(value, now, age, future=5):
    delta = (now - timestamp(value)).total_seconds()
    return -future <= delta <= age


def bars_valid(rows, now, minutes, count, c):
    if not isinstance(rows, list) or len(rows) < count:
        raise ValueError('insufficient bars')
    rows = rows[-count:]
    times = [timestamp(r['closed_at']) for r in rows]
    if any(t > now for t in times):
        raise ValueError('open/future bar')
    if any((b-a).total_seconds() != minutes*60 for a, b in zip(times, times[1:])):
        raise ValueError('duplicate, unordered or missing bars')
    if (now-times[-1]).total_seconds() > minutes*60 + c['max_age_seconds']['quote']:
        raise ValueError('stale bars')
    for r in rows:
        o, h, l, close = [number(r[k]) for k in ('open', 'high', 'low', 'close')]
        if not 0 < l <= min(o, close) <= max(o, close) <= h:
            raise ValueError('OHLC geometry')
    return rows


def level_state(rows, side, level, c):
    sign = 1 if side == 'SHORT' else -1
    recent = rows[-c['acceptance_closes']:]
    if len(recent) < c['acceptance_closes']:
        return 'UNKNOWN'
    if all(sign*(number(r['close'])-level) > c['level_buffer'] for r in recent):
        return 'ACCEPTANCE'
    last = recent[-1]
    touched = any(number(r['high']) >= level-c['level_buffer'] if side == 'SHORT'
                  else number(r['low']) <= level+c['level_buffer'] for r in recent)
    if touched and sign*(number(last['close'])-level) < -c['level_buffer']:
        return 'REJECTION'
    return 'UNCONFIRMED'


def news_reasons(calendar, now, normalized, c):
    reasons = []
    keys = ('nonfarm', 'non-farm', 'payroll', 'nfp', 'cpi', 'consumer price', 'pce',
            'personal consumption', 'fomc', 'federal funds', 'interest rate decision')
    if calendar.get('complete') is not True:
        reasons.append('CALENDAR_INCOMPLETE')
    start, end = timestamp(calendar['coverage_start']), timestamp(calendar['coverage_end'])
    if start > now-timedelta(minutes=c['post_news_watch_minutes']) or end < now+timedelta(minutes=c['news_before_minutes']):
        reasons.append('CALENDAR_COVERAGE')
    for event in calendar['events']:
        if event.get('country') != 'US' or not any(k in str(event.get('event', '')).lower() for k in keys):
            continue
        when = timestamp(event['time'])
        elapsed = (now-when).total_seconds()/60
        if -c['news_before_minutes'] <= elapsed <= c['news_after_minutes']:
            reasons.append('NEWS_WINDOW:' + str(event['event']))
        elif c['news_after_minutes'] < elapsed <= c['post_news_watch_minutes'] and not normalized:
            reasons.append('POST_NEWS_NOT_NORMALIZED:' + str(event['event']))
    return reasons


def evaluate(snapshot, legacy, c=None):
    c = c or load_config()
    now = timestamp(snapshot['as_of'])
    reasons = list(legacy.get('hard_reasons', []))
    if legacy.get('tradeable') is not True:
        reasons.append('LEGACY_MASTER_BLOCK')
    if legacy.get('suppression'):
        reasons.append('SUPPRESSION')
    for name in ('JUMP', 'COT', 'GVZ', 'NEWS'):
        status = legacy.get('channels', {}).get(name, ('UNKNOWN', 'missing'))
        color, detail = status
        if color == 'RED' or color not in ('GREEN', 'YELLOW') or any(k in str(detail).lower() for k in ('err', 'yok', 'yetersiz', 'unknown')):
            reasons.append('CHANNEL_UNAVAILABLE_OR_RED:' + name)
    for name, age in c['max_age_seconds'].items():
        try:
            if not fresh(snapshot[name]['observed_at'], now, age, c['future_tolerance_seconds']):
                reasons.append('STALE:' + name)
        except (KeyError, TypeError, ValueError):
            reasons.append('MISSING:' + name)
    spread_ratio = vol_ratio = jump = None
    cross = False
    try:
        q = snapshot['quote']
        bid, ask = number(q['bid']), number(q['ask'])
        if not 0 < bid < ask:
            raise ValueError('bid/ask')
        history = q['spread_history'][-c['spread_samples']:]
        if len(history) < c['spread_samples']:
            raise ValueError('spread history')
        ts = [timestamp(r['observed_at']) for r in history]
        if any(b <= a for a,b in zip(ts, ts[1:])) or ts[-1] >= timestamp(q['observed_at']):
            raise ValueError('spread history must precede quote')
        if not fresh(ts[0].isoformat(), now, c['post_news_watch_minutes']*60):
            raise ValueError('spread baseline stale')
        spreads = [number(r['spread']) for r in history]
        if min(spreads) <= 0:
            raise ValueError('spread baseline')
        spread_ratio = (ask-bid)/statistics.median(spreads)
        if spread_ratio > c['spread_shock_ratio']:
            reasons.append('SPREAD_SHOCK')
    except (KeyError, TypeError, ValueError, IndexError):
        reasons.append('INVALID_SPREAD_DATA')
    m5 = None
    try:
        m5 = bars_valid(snapshot['m5'], now, 5, max(c['atr_period']+2, c['acceptance_closes']), c)
        trs = [max(number(b['high'])-number(b['low']), abs(number(b['high'])-number(a['close'])), abs(number(b['low'])-number(a['close']))) for a,b in zip(m5, m5[1:])]
        baseline = statistics.mean(trs[-c['atr_period']-1:-1])
        if baseline <= 0:
            raise ValueError('zero ATR')
        vol_ratio = trs[-1]/baseline
        if vol_ratio > c['m5_shock_ratio']:
            reasons.append('M5_VOLATILITY_SHOCK')
    except (KeyError, TypeError, ValueError):
        reasons.append('INVALID_M5_DATA')
    try:
        m1 = bars_valid(snapshot['m1'], now, 1, c['jump_period']+2, c)
        returns = [math.log(number(b['close'])/number(a['close'])) for a,b in zip(m1,m1[1:])]
        sigma = statistics.stdev(returns[:-1])
        jump = abs(returns[-1])/sigma if sigma > 0 else (0 if returns[-1] == 0 else math.inf)
        if jump > c['m1_jump_sigma']:
            reasons.append('M1_JUMP')
    except (KeyError, TypeError, ValueError):
        reasons.append('INVALID_M1_DATA')
    side = snapshot.get('side')
    state, room, entry = 'UNKNOWN', None, None
    if side not in ('LONG', 'SHORT') or side not in legacy.get('allowed_sides', []):
        reasons.append('DIRECTION_NOT_ALLOWED')
    try:
        thresholds = c['cross_market_thresholds']
        # Changes must refer to the same configured sampling interval supplied by adapter.
        periods = [snapshot[k]['change_period_seconds'] for k in ('dxy','yield2','yield10')]
        if len(set(periods)) != 1 or number(periods[0]) <= 0:
            raise ValueError('cross market horizons differ')
        changes = [number(snapshot[k]['change']) for k in ('dxy','yield2','yield10')]
        sign = 1 if side == 'LONG' else -1
        cross = all(sign*v >= thresholds[k] for v,k in zip(changes, ('dxy_pct','yield2_bp','yield10_bp')))
        if cross:
            reasons.append('CROSS_MARKET_SHOCK')
    except (KeyError, TypeError, ValueError):
        reasons.append('INVALID_CROSS_MARKET')
    try:
        level = number(snapshot['setup_level'])
        if legacy.get('obstacles') is None:
            reasons.append('LEGACY_LEVELS_UNAVAILABLE')
        levels = snapshot['levels']
        if levels.get('complete') is not True:
            raise ValueError('incomplete obstacle map')
        if m5 and side in ('LONG', 'SHORT') and level > 0:
            state = level_state(m5, side, level, c)
            if state == 'ACCEPTANCE':
                reasons.append('ACCEPTANCE')
            elif state != 'REJECTION':
                reasons.append('REJECTION_NOT_CONFIRMED')
        entry = number(snapshot['quote']['ask' if side == 'LONG' else 'bid'])
        sign = 1 if side == 'LONG' else -1
        all_levels = list(levels['prices']) + list(legacy.get('obstacles') or [])
        distances = [sign*(number(v)-entry) for v in all_levels]
        if any(number(v) <= 0 for v in levels['prices']) or level <= 0:
            raise ValueError('invalid level')
        ahead = [v for v in distances if v >= 0]
        room = min(ahead) if ahead else None
        if room is not None and room < c['tp_distance'] + c['room_buffer']:
            reasons.append('ROOM_TO_TP')
        if m5:
            state = level_state(m5, side, level, c)
        if state == 'ACCEPTANCE':
            reasons.append('ACCEPTANCE')
        elif state != 'REJECTION':
            reasons.append('REJECTION_NOT_CONFIRMED')
    except (KeyError, TypeError, ValueError):
        reasons.append('INVALID_SETUP_LEVELS')
    normalized = spread_ratio is not None and vol_ratio is not None and spread_ratio <= c['spread_shock_ratio'] and vol_ratio <= c['m5_shock_ratio']
    news = []
    try:
        news = news_reasons(snapshot['calendar'], now, normalized, c)
        reasons.extend(news)
    except (KeyError, TypeError, ValueError):
        reasons.append('INVALID_CALENDAR')
    components = {'spread': min(1, (spread_ratio or 0)/c['spread_shock_ratio']),
                  'volatility': min(1, (vol_ratio or 0)/c['m5_shock_ratio']),
                  'jump': min(1, (jump or 0)/c['m1_jump_sigma']),
                  'cross_market': int(cross), 'news': int(bool(news))}
    weights = c['risk_weights']
    score = round(100*sum(components[k]*weights[k] for k in weights)/sum(weights.values()), 1)
    incomplete = any(r.startswith(('MISSING:', 'STALE:', 'INVALID_', 'CALENDAR_', 'CHANNEL_UNAVAILABLE')) for r in reasons)
    if incomplete:
        score = 100.0
    label = 'EXTREME' if score >= c['tail_extreme'] else 'HIGH' if score >= c['tail_high'] else 'MEDIUM' if score >= c['tail_medium'] else 'LOW'
    if score >= c['tail_high']:
        reasons.append('TAIL_RISK_HIGH')
    evidence = snapshot.get('quality_evidence', {})
    quality_parts = {k: 2 if evidence.get(k) is True else 0 for k in ('regime', 'macro', 'location')}
    quality_parts.update(rejection=2 if state == 'REJECTION' else 0,
                         room=2 if 'ROOM_TO_TP' not in reasons and 'INVALID_SETUP_LEVELS' not in reasons and 'LEGACY_LEVELS_UNAVAILABLE' not in reasons else 0,
                         news=2 if not any('NEWS' in r or 'CALENDAR' in r or r == 'SUPPRESSION' for r in reasons) else 0)
    quality = sum(quality_parts.values())
    grade = 'A+' if quality >= c['quality_a_plus'] else 'A' if quality >= c['quality_a'] else 'B' if quality >= c['quality_b'] else 'NO TRADE'
    if quality < c['quality_a']:
        reasons.append('QUALITY_BELOW_A')
    reasons = list(dict.fromkeys(reasons))
    return {'schema_version': 1, 'as_of': now.isoformat(), 'config_hash': config_hash(c),
            'side': side, 'entry': entry, 'entry_tolerance': c['level_buffer'], 'tradeable': not reasons, 'reasons': reasons, 'tail_score': score,
            'tail_label': label, 'quality_score': quality, 'quality_grade': grade,
            'quality_components': quality_parts, 'level_state': state, 'room_to_tp': room,
            'data_complete': not incomplete,
            'measurements': {'spread_ratio': spread_ratio, 'm5_tr_atr': vol_ratio,
                             'm1_jump_sigma': jump},
            'large_loser_probability': None, 'probability_status': 'LOGGING_ONLY_NO_VALIDATED_MODEL'}


def kill_reasons(position, snapshot, decision, c):
    """Paper-position exit recommendations, not orders. Opening direction stays immutable."""
    reasons = []
    now = timestamp(snapshot['as_of'])
    if (now-timestamp(position['opened_at'])).total_seconds() >= c['time_kill_minutes']*60:
        reasons.append('TIME_KILL')
    if any(r.startswith(('NEWS_', 'POST_NEWS', 'SPREAD_SHOCK', 'M5_', 'M1_', 'SUPPRESSION', 'STALE:', 'MISSING:', 'INVALID_')) for r in decision['reasons']):
        reasons.append('VOLATILITY_NEWS_DATA_KILL')
    try:
        rows = bars_valid(snapshot['m5'], now, 5, c['acceptance_closes'], c)
        if level_state(rows, position['side'], number(position['structure_level']), c) == 'ACCEPTANCE':
            reasons.append('STRUCTURE_KILL')
    except (KeyError, TypeError, ValueError):
        reasons.append('STRUCTURE_DATA_UNKNOWN')
    return reasons


def candidate_approved(side, entry, decision):
    """Approval applies only to the assessed entry and direction, never a future level."""
    try:
        return (decision['tradeable'] and side == decision['side'] and
                abs(number(entry)-number(decision['entry'])) <= number(decision['entry_tolerance']))
    except (KeyError, ValueError, TypeError):
        return False
