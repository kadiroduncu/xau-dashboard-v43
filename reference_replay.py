"""First-barrier reference-price audit; never actual fills or executable performance."""
from collections import Counter
from datetime import datetime
import math
import statistics

STEP = 300000


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def replay_reference(audit):
    bars = sorted(audit['bars_5min'], key=lambda b: b['open_at'])
    for b in bars:
        if not all(_number(b.get(k)) and b[k] > 0 for k in ('open', 'high', 'low', 'close')):
            raise ValueError('Invalid OHLC')
        if b['high'] < max(b['open'], b['close']) or b['low'] > min(b['open'], b['close']):
            raise ValueError('Invalid OHLC geometry')
        if not all(_number(b.get(k)) for k in ('open_at', 'close_at')) or b['close_at'] != b['open_at'] + STEP:
            raise ValueError('Invalid M5 time')
    if len({b['open_at'] for b in bars}) != len(bars):
        raise ValueError('Duplicate candles')
    rows, seen = [], set()
    for signal in audit['history']:
        sid = signal.get('id')
        if sid in seen:
            continue
        seen.add(sid)
        result = {'id': sid, 'status': 'invalid', 'gross_price_distance': None}
        try:
            stamp = int(sid.split(':')[0])
            side = {'LONG': 1, 'SHORT': -1}[signal['direction']]
            entry, target, stop = (signal[k] for k in ('entry', 'target', 'invalidation'))
            if not all(_number(v) and v > 0 for v in (entry, target, stop)) or not ((target-entry)*side > 0 and (entry-stop)*side > 0):
                raise ValueError('Invalid levels')
            observed = datetime.fromisoformat(signal['at'].replace('Z', '+00:00'))
            if observed.tzinfo is None or observed.timestamp()*1000 < stamp:
                raise ValueError('Invalid signal observation time')
        except (ValueError, TypeError, KeyError, AttributeError):
            rows.append(result)
            continue
        result.update(status='open', at=signal['at'], direction=signal['direction'], kind=signal.get('kind'),
                      delivery_delay_seconds=observed.timestamp()-stamp/1000)
        expected, count = stamp, 0
        for bar in (b for b in bars if b['close_at'] > stamp):
            if bar['open_at'] != expected:
                result['status'] = 'incomplete'
                break
            expected, count = bar['close_at'], count+1
            # A gap through the stop is filled at the worse open; target is a limit.
            if (bar['open']-stop)*side <= 0:
                result.update(status='stop', gross_price_distance=(bar['open']-entry)*side, exit_at=bar['open_at'])
                break
            if (bar['open']-target)*side >= 0:
                result.update(status='target', gross_price_distance=(target-entry)*side, exit_at=bar['open_at'])
                break
            stopped = bar['low'] <= stop if side == 1 else bar['high'] >= stop
            reached = bar['high'] >= target if side == 1 else bar['low'] <= target
            if stopped and reached:
                result.update(status='ambiguous', exit_at=bar['close_at'])
                break
            if stopped or reached:
                result.update(status='stop' if stopped else 'target',
                              gross_price_distance=((stop if stopped else target)-entry)*side,
                              exit_at=bar['close_at'])
                break
        if count == 0:
            result['status'] = 'incomplete'
        rows.append(result)
    counts = Counter(r['status'] for r in rows)
    closed = [r for r in rows if r['status'] in ('target', 'stop')]
    delays = [r['delivery_delay_seconds'] for r in rows if 'delivery_delay_seconds' in r]
    gross = sum(r['gross_price_distance'] for r in closed)
    return {'schema': 'reference-first-barrier-v1', 'checked_at': audit.get('checked_at'),
            'total': len(rows), 'counts': dict(counts), 'resolved': len(closed),
            'target_rate': counts['target']/len(closed) if closed else None,
            'hypothetical_gross_price_distance': gross if closed else None,
            'gross_distance_per_resolved': gross/len(closed) if closed else None,
            'median_delivery_delay_seconds': statistics.median(delays) if delays else None,
            'net_pnl': None, 'max_drawdown': None, 'mae_mfe': None,
            'note': 'Sinyal mumu kapanışındaki varsayımsal referans giriş; bildirim sonrası gerçekleşme değildir. '
                    'Örtüşen bağımsız sinyaller portföy değildir. Spread/komisyon/slippage ve stop mumundaki '
                    'olay sırası eksik; net kâr, portföy düşüşü veya kesin MAE/MFE hesaplanmaz.',
            'rows': rows}
