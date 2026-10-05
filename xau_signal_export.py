"""Read-only, allowlisted v42 snapshot. No Streamlit or third-party dependency."""
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Lock

_LOCK = Lock()
MODEL_VERSION = 'v43-feed-repair+json.3'


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def number(value):
    if value is None or isinstance(value, (bool, str)):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError, OverflowError):
        return None


def build_snapshot(*, price, price_fetched_at, started_at, request_id='',
                   gate_level=None, sides=(), channels=None, metrics=None,
                   probability=None, errors=(), news_count=None, calendar_count=None):
    metrics = metrics or {}
    channels = channels or {}
    safe_errors = []
    # Exception messages, channel reasons and arbitrary model dictionaries may
    # contain URLs/API keys. Never export them or any session/user state.
    for err in errors:
        kind = str(err.get('type', 'Error'))
        if not kind.isidentifier() or len(kind) > 80:
            kind = 'Error'
        safe_errors.append({'component': 'probabilistic_view', 'type': kind})
    price = number(price)
    if price is None or price <= 0:
        price = None
        safe_errors.append({'component': 'price', 'type': 'Unavailable'})
    allowed = [side for side in ('LONG', 'SHORT') if side in sides]
    # A hard block overrides directional eligibility.
    if gate_level == 'RED':
        allowed = []
    gate = {'RED': 'TRADE_BLOKLU', 'YELLOW': 'BEKLE_DIKKAT',
            'GREEN': 'FADE_UYGUN'}.get(gate_level)
    result = {
        'schema_version': '1.0', 'model_version': MODEL_VERSION,
        'timestamp': utc_now(), 'run_started_at': started_at,
        'request_id': request_id[:64], 'price': price,
        'price_fetched_at': price_fetched_at,
        'price_source_timestamp': None,
        'master_gate': gate, 'master_gate_level': gate_level if gate else None,
        'allowed_direction': allowed,
        'direction': 'BOTH' if len(allowed) == 2 else (allowed[0] if allowed else 'NONE'),
        'sma50_distance_pct': number(metrics.get('price_vs_sma50')),
        'return_5d_pct': number(metrics.get('ret_5d')),
        'hurst': number(metrics.get('hurst')),
        'volatility_regime': None, 'breakout_risk': None,
        'probability_bands': {k: None for k in ('1D', '1W', '1M')},
        'errors': safe_errors,
        'error_scope': 'export_validation_and_probabilistic_view',
        'freshness': {
            'snapshot_max_age_seconds': 300,
            'price_fetch_max_age_seconds': 120,
            'upstream_quote_time_verified': False,
            'note': 'Fetch time is not exchange quote time. Source timestamps for channels are unavailable.'
        }
    }
    for key in ('JUMP', 'COT', 'GVZ', 'NEWS'):
        channel = channels.get(key)
        status = channel[0] if isinstance(channel, (list, tuple)) and channel else None
        status = status if status in ('GREEN', 'YELLOW', 'RED') else None
        reason = str(channel[1]).lower() if isinstance(channel, (list, tuple)) and len(channel) > 1 else ''
        unavailable = reason.startswith('err:') or reason in ('m5 yetersiz', 'cot yok', 'gvz yok')
        if unavailable:
            status = None
        result[key.lower()] = {'status': status, 'value': None, 'source_timestamp': None,
                               'available': status is not None}
        if status is None:
            safe_errors.append({'component': key.lower(), 'type': 'Unavailable'})
    if news_count is not None:
        result['news']['input_count'] = news_count
        result['news']['calendar_count'] = calendar_count
        if news_count == 0 and calendar_count == 0:
            result['news']['status'] = None
            result['news']['available'] = False
            safe_errors.append({'component': 'news', 'type': 'EmptyInputsUnverified'})
    if probability:
        regime = probability.get('volatility_regime')
        result['volatility_regime'] = regime if regime in ('SAKİN', 'NORMAL', 'YÜKSEK-VOL') else None
        risk = probability.get('breakout_risk') or {}
        score = number(risk.get('score'))
        result['breakout_risk'] = ({'score': score, 'level': 'NORMAL' if score <= 1 else
                                  'HIGH' if score <= 3 else 'VERY_HIGH'} if score is not None else None)
        for horizon, days in (('1D', 1), ('1W', 5), ('1M', 21)):
            band = (probability.get('bands') or {}).get(horizon) or {}
            low, high = number(band.get('low')), number(band.get('high'))
            if low is not None and high is not None and low <= high:
                result['probability_bands'][horizon] = {
                    'low': low, 'high': high, 'low_95': number(band.get('low_95')),
                    'high_95': number(band.get('high_95')),
                    'nominal_coverage': 0.68, 'trading_days': days}
    if any(v is None for v in result['probability_bands'].values()) and not any(
            e['component'] == 'probabilistic_view' for e in safe_errors):
        safe_errors.append({'component': 'probabilistic_view', 'type': 'Unavailable'})
    for name in ('master_gate', 'sma50_distance_pct', 'return_5d_pct', 'hurst'):
        if result[name] is None:
            safe_errors.append({'component': name, 'type': 'Unavailable'})
    result['status'] = 'error' if price is None or gate is None else 'partial' if safe_errors else 'ok'
    return result


def publish_snapshot(path, payload):
    """Atomic replacement; a slower older run must not overwrite a newer run."""
    path = Path(path)
    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False, indent=2)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        if path.exists():
            try:
                previous = json.loads(path.read_text())
                if previous['run_started_at'] > payload['run_started_at']:
                    return False
            except (ValueError, KeyError):
                pass
        tmp = None
        try:
            with NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                    prefix='.signal-', suffix='.tmp', delete=False) as f:
                tmp = f.name
                f.write(encoded)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        finally:
            if tmp and os.path.exists(tmp):
                os.unlink(tmp)
    return True


def validate_freshness(payload, request_id=None, now=None):
    """Fail closed on stale output, failed runs, or a mismatching refresh token."""
    now = now or datetime.now(timezone.utc)
    if payload.get('schema_version') != '1.0':
        raise ValueError('Unsupported snapshot schema')
    if request_id is not None and payload.get('request_id') != request_id:
        raise ValueError('Requested model run has not completed')
    for key, limit in (('timestamp', 300), ('run_started_at', 300), ('price_fetched_at', 120)):
        raw = payload.get(key)
        if not isinstance(raw, str):
            raise ValueError('Missing timestamp: ' + key)
        value = datetime.fromisoformat(raw.replace('Z', '+00:00'))
        if value.tzinfo is None:
            raise ValueError('Timestamp must include timezone')
        age = (now - value).total_seconds()
        if age < -30 or age > limit:
            raise ValueError('Stale or future timestamp: ' + key)
    if payload.get('status') not in ('ok', 'partial'):
        raise ValueError('Model run failed')
    if number(payload.get('price')) is None or payload['price'] <= 0:
        raise ValueError('Price unavailable')
    return payload
