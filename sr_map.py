"""Strength-ranked S/R map for XAU/USD — production module.

Encodes the empirically validated decisions (research/sr_validation_results.md):
  * Strength signal = D1 detected swing pivots (find_peaks + 1-D cluster), the ONLY set with
    a significant bounce edge over random (+2.7..+3.5pp, p<0.05) in the current regime.
  * Computed on a ROLLING RECENT window (regime-local), NOT full history.
  * Round numbers are FRICTION/volatility zones (no bounce edge) — labelled, never "bounce here".
  * Per-bar ATR-scaled touch band (TOUCH_BAND * ATR_t); never a global ATR median.
  * STRENGTH = historical bounce frequency (the ONLY OOS-validated predictor, +2.8pp Q4>Q1
    in walk-forward; see research/sr_validation_results.md). Touch count is NOT rewarded — it
    is empirically COUNTERPRODUCTIVE (−7.6pp inverted: heavily-tested levels break more), so
    it only acts as a sample-confidence shrink, never a strength booster. Recency is neutral
    for bounce prediction (kept as display metadata, not in the score).

Public API:
    build_sr_map(df, lookback=750) -> pandas.DataFrame  (one row per level, ranked by strength)

`df` columns required: high, low, close (timestamp optional). Designed for D1 bars.
"""
import numpy as np
import pandas as pd
from scipy.signal import find_peaks

# Validated params (from sr_validate.py / research) — all tunable
ATR_N           = 14
FIND_PEAKS_PROM = 0.5     # x ATR
FIND_PEAKS_DIST = 5       # bars
CLUSTER_EPS     = 0.40    # x ATR  (tuned 0.25->0.40 via walk-forward, sr_tune.py)
TOUCH_BAND      = 0.15    # x ATR  (a "hit")
BREAK_BAND      = 0.50    # x ATR  (clean break, not a bounce)
BOUNCE_BARS     = 2
ROUND_STEP      = 50      # $ grid
RECENCY_HALFLIFE = 75     # bars; older touches decay
DEFAULT_LOOKBACK = 750    # D1 bars (~3y) — regime-local

# Round-number roundness sub-tiering (Bhattacharya 2012 monotonicity)
ROUNDNESS = [(1000, 1.00), (500, 0.80), (250, 0.55), (100, 0.40), (50, 0.25)]


def wilder_atr(high, low, close, n=ATR_N):
    pc = np.concatenate([[close[0]], close[:-1]])
    tr = np.maximum.reduce([high - low, np.abs(high - pc), np.abs(low - pc)])
    return pd.Series(tr).ewm(alpha=1 / n, adjust=False).mean().values


def _cluster_1d(sorted_prices, eps):
    if len(sorted_prices) == 0:
        return []
    groups, g = [], [sorted_prices[0]]
    for p in sorted_prices[1:]:
        if p - g[-1] <= eps:
            g.append(p)
        else:
            groups.append(g); g = [p]
    groups.append(g)
    return groups


def _roundness(level, step=ROUND_STEP):
    for mod, score in ROUNDNESS:
        if abs(level - round(level / mod) * mod) < step / 2:
            return score
    return 0.10


def build_sr_map(df, lookback=DEFAULT_LOOKBACK, atr_n=ATR_N):
    """Return a ranked S/R map DataFrame for the most recent `lookback` bars of `df`.

    Columns: level, kind, role, touches, bounce_freq, strength, tier, note
      kind  : 'pivot' (validated) | 'round' (friction)
      role  : 'support' | 'resistance' | 'projected' (round number above price, never touched)
      tier  : '🟢' validated D1 pivot · '🟡' friction / lower-confidence
      strength: 0-100 within this map
    """
    d = df.tail(lookback).reset_index(drop=True)
    high, low, close = d['high'].values, d['low'].values, d['close'].values
    atr = wilder_atr(high, low, close, atr_n)
    atr_med = float(np.nanmedian(atr[atr_n:])) if len(atr) > atr_n else float(np.nanmedian(atr))
    price = float(close[-1])
    n = len(close)

    # future close extremes for bounce detection
    fmax = np.full(n, np.nan); fmin = np.full(n, np.nan)
    for k in range(1, BOUNCE_BARS + 1):
        if n - k > 0:
            fmax[:n - k] = np.fmax(fmax[:n - k], close[k:]) if k > 1 else close[k:]
            fmin[:n - k] = np.fmin(fmin[:n - k], close[k:]) if k > 1 else close[k:]

    # 1) detect candidate pivots -> cluster into level zones
    prom = FIND_PEAKS_PROM * atr_med
    hi_idx, _ = find_peaks(high, prominence=prom, distance=FIND_PEAKS_DIST)
    lo_idx, _ = find_peaks(-low, prominence=prom, distance=FIND_PEAKS_DIST)
    pts = np.sort(np.concatenate([high[hi_idx], low[lo_idx]]))
    rows = []
    for g in _cluster_1d(pts, CLUSTER_EPS * atr_med):
        lv = float(np.mean(g))
        rows.append(_score_pivot(lv, high, low, close, atr, fmax, fmin, n))

    # 2) round numbers in range = friction zones (no bounce edge; variance signal)
    lo_p, hi_p = float(low.min()), float(high.max())
    # extend the grid above price for the gapped/never-visited zone
    grid_top = max(hi_p, price * 1.08)
    for lv in np.arange(np.floor(lo_p / ROUND_STEP) * ROUND_STEP, grid_top + ROUND_STEP, ROUND_STEP):
        lv = float(lv)
        touched = (low <= lv + TOUCH_BAND * atr_med) & (high >= lv - TOUCH_BAND * atr_med)
        role = 'projected' if (lv > price and not touched.any()) else \
               ('resistance' if lv >= price else 'support')
        rows.append({'level': lv, 'kind': 'round', 'role': role,
                     'touches': int(touched.sum()), 'bounce_freq': np.nan,
                     'raw': 6 + 14 * _roundness(lv), 'tier': '🟡',
                     'note': 'friction/volatility zone — expect range, not reversal'})

    m = pd.DataFrame(rows)
    # normalize strength to 0-100 within this map
    m['strength'] = (100 * (m['raw'] - m['raw'].min()) /
                     max(m['raw'].max() - m['raw'].min(), 1e-9)).round(1)
    m = m.drop(columns='raw').sort_values('level', ascending=False).reset_index(drop=True)
    m['dist'] = (m['level'] - price).round(2)
    return m


def _score_pivot(lv, high, low, close, atr, fmax, fmin, n):
    band = TOUCH_BAND * atr
    brk = BREAK_BAND * atr
    hit = (low <= lv + band) & (high >= lv - band) & ~np.isnan(fmax)
    idx = np.where(hit)[0]
    touches = len(idx)
    bounces = 0
    for i in idx:
        approach_below = close[i] <= lv
        if approach_below:
            broke = fmax[i] >= lv + brk[i]; bounced = (not broke) and fmin[i] <= lv - band[i]
        else:
            broke = fmin[i] <= lv - brk[i]; bounced = (not broke) and fmax[i] >= lv + band[i]
        bounces += int(bounced)
    bounce_freq = bounces / touches if touches else np.nan
    last_age = (n - 1 - idx.max()) if touches else n
    recency = 0.5 ** (last_age / RECENCY_HALFLIFE)   # display metadata only (neutral for bounce)
    # STRENGTH = historical bounce frequency (only OOS-validated predictor), shrunk toward 0.5
    # by sample confidence (touches/5, saturating). Touch count is NOT a linear booster — it is
    # empirically counterproductive, so it only down-weights tiny-sample levels.
    bf = bounce_freq if not np.isnan(bounce_freq) else 0.5
    conf = min(1.0, touches / 5.0)
    raw = (0.5 + (bf - 0.5) * conf) * 100          # 0..100-ish, centered on 0.5
    price = float(close[-1])
    return {'level': round(lv, 2), 'kind': 'pivot',
            'role': 'resistance' if lv >= price else 'support',
            'touches': touches, 'bounce_freq': round(bounce_freq, 3) if touches else np.nan,
            'raw': raw, 'tier': '🟢',
            'note': f'D1 pivot · bounce {bf*100:.0f}% · {touches} touch · rec {recency:.2f}'}


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else \
        "/Users/Asus/Desktop/xau.engine/xau_d1_full.csv"
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    for c in ['high', 'low', 'close']:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    df = df.dropna(subset=['high', 'low', 'close'])
    m = build_sr_map(df)
    price = float(df['close'].iloc[-1])
    print(f"\nCurrent XAU price: ${price:.2f}   (D1 map, {DEFAULT_LOOKBACK}-bar lookback)\n")
    near = m[m['dist'].abs() <= 250].copy()
    with pd.option_context('display.max_rows', None, 'display.width', 120):
        print(near[['level', 'kind', 'role', 'touches', 'bounce_freq', 'strength', 'tier', 'dist']]
              .to_string(index=False))
    print(f"\nTop 8 strongest levels overall:")
    print(m.nlargest(8, 'strength')[['level', 'kind', 'role', 'touches', 'bounce_freq',
                                     'strength', 'tier']].to_string(index=False))
