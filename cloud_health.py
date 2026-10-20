"""Validate cloud display freshness independently of a worker heartbeat."""
from datetime import datetime, timezone
import math


def cloud_health(state, now=None):
    now = now or datetime.now(timezone.utc)
    result = {'ready': False, 'reason': 'Bulut verisi doğrulanamadı', 'bars': [], 'green': None, 'last_close': None}
    try:
        if not isinstance(state, dict):
            return result
        checked = datetime.fromisoformat(state['checked_at'].replace('Z', '+00:00'))
        if checked.tzinfo is None or not -5 <= (now-checked).total_seconds() <= 480:
            result['reason'] = 'Bulut kontrolü eski veya zaman damgası geçersiz'
            return result
        series = state.get('series', {}).get('5min', {}).get('bars', [])
        if not isinstance(series, list) or not series:
            result['reason'] = 'Kapanmış M5 mumları alınamadı'
            return result
        stamps = []
        for bar in series[-100:]:
            stamp = bar['close_at']
            if isinstance(stamp, bool) or not isinstance(stamp, (int, float)) or not math.isfinite(stamp):
                raise ValueError('Invalid time')
            when = datetime.fromtimestamp(stamp/1000, timezone.utc)
            values = [bar[k] for k in ('open', 'high', 'low', 'close')]
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0 for v in values):
                raise ValueError('Invalid price')
            if bar['low'] > min(bar['open'],bar['close']) or bar['high'] < max(bar['open'],bar['close']):
                raise ValueError('Invalid OHLC')
            if (now-when).total_seconds() < -5:
                raise ValueError('Future candle')
            stamps.append(stamp)
        if any(a >= b for a,b in zip(stamps,stamps[1:])):
            raise ValueError('Unordered or duplicate candles')
        result['bars'] = series[-100:]
        result['last_close'] = datetime.fromtimestamp(stamps[-1]/1000,timezone.utc)
        if (now-result['last_close']).total_seconds() > 480:
            result['reason'] = 'Bulut yanıtı güncel fakat son M5 mumu eski; giriş adayı gösterilmiyor'
            return result
        engine = state.get('engine') or {}
        if state.get('data_ready') is not True or state.get('signal_engine_ready') is not True:
            result['reason'] = engine.get('reason') or state.get('signal_reason') or 'Motor yeterli ve güncel veri bekliyor'
            return result
        green = engine.get('green')
        if green:
            side = green['direction']
            levels = [green[k] for k in ('entry','target','invalidation')]
            if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or v <= 0 for v in levels):
                raise ValueError('Invalid levels')
            entry,target,invalid = levels
            if not ((side == 'LONG' and invalid < entry < target) or (side == 'SHORT' and target < entry < invalid)):
                raise ValueError('Invalid direction')
            if not isinstance(green.get('kind'),str) or not green['kind']:
                raise ValueError('Invalid setup kind')
        result.update(ready=True,reason=engine.get('reason') or 'Yeni kapanış koşulları bekleniyor',green=green)
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, OSError):
        result.update(ready=False,green=None,reason='Bulut zaman/fiyat biçimi doğrulanamadı; giriş adayı gösterilmiyor')
    return result
