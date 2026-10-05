"""Trend Filter v1 - KARAR'a yon kapisi eklemesi"""
import numpy as np
import pandas as pd


def hurst_exponent(prices, max_lag=20):
    prices = np.asarray(prices, dtype=float)
    if len(prices) < max_lag + 1:
        return np.nan
    lags = list(range(2, max_lag))
    tau = []
    for lag in lags:
        diff = np.subtract(prices[lag:], prices[:-lag])
        s = np.std(diff)
        if s <= 0:
            return np.nan
        tau.append(np.sqrt(s))
    if any(t <= 0 for t in tau):
        return np.nan
    try:
        slope, _ = np.polyfit(np.log(lags), np.log(tau), 1)
        return float(slope * 2.0)
    except Exception:
        return np.nan


def sma_slope_pct(prices, period=50, lookback=10):
    s = pd.Series(prices, dtype=float)
    if len(s) < period + lookback:
        return np.nan
    sma = s.rolling(period).mean()
    if pd.isna(sma.iloc[-1]) or pd.isna(sma.iloc[-lookback]):
        return np.nan
    base = sma.iloc[-lookback]
    if base == 0:
        return np.nan
    return float((sma.iloc[-1] - base) / base * 100.0)


def donchian_state(highs, lows, closes, period=20, recent_bars=3):
    highs = pd.Series(highs, dtype=float)
    lows = pd.Series(lows, dtype=float)
    closes = pd.Series(closes, dtype=float)
    if len(closes) < period + recent_bars + 1:
        return {'broke_high': False, 'broke_low': False}
    don_high = highs.rolling(period).max().shift(1)
    don_low = lows.rolling(period).min().shift(1)
    tail_c = closes.tail(recent_bars).reset_index(drop=True)
    tail_h = don_high.tail(recent_bars).reset_index(drop=True)
    tail_l = don_low.tail(recent_bars).reset_index(drop=True)
    return {
        'broke_high': bool((tail_c > tail_h).any()),
        'broke_low': bool((tail_c < tail_l).any())
    }


def trend_filter(df, hurst_window=100, sma_period=50,
                 sma_slope_threshold=0.3, donchian_period=20):
    required = max(hurst_window, sma_period + 10, donchian_period + 3)
    if df is None or len(df) < required:
        return {
            'hurst': None, 'sma_slope': None,
            'donchian_broke_high': False, 'donchian_broke_low': False,
            'long_allowed': True, 'short_allowed': True,
            'reason': f'YETERSIZ VERI ({0 if df is None else len(df)}/{required} bar)'
        }
    closes, highs, lows = df['close'], df['high'], df['low']
    h = hurst_exponent(closes.tail(hurst_window).values)
    slope = sma_slope_pct(closes, period=sma_period, lookback=10)
    donch = donchian_state(highs, lows, closes,
                           period=donchian_period, recent_bars=3)
    long_blocks, short_blocks = [], []
    if h is not None and not np.isnan(h) and h > 0.55:
        long_blocks.append(f'Hurst={h:.2f}>0.55')
        short_blocks.append(f'Hurst={h:.2f}>0.55')
    if slope is not None and not np.isnan(slope):
        if slope < -sma_slope_threshold:
            long_blocks.append(f'SMA50 slope={slope:+.2f}%')
        elif slope > sma_slope_threshold:
            short_blocks.append(f'SMA50 slope={slope:+.2f}%')
    if donch['broke_low']:
        long_blocks.append('Donchian-20 LOW kirildi')
    if donch['broke_high']:
        short_blocks.append('Donchian-20 HIGH kirildi')
    long_allowed = len(long_blocks) == 0
    short_allowed = len(short_blocks) == 0
    if long_allowed and short_allowed:
        reason = 'Iki yon serbest (trend yok)'
    elif long_allowed:
        reason = f"SADECE LONG | SHORT bloklu: {'; '.join(short_blocks)}"
    elif short_allowed:
        reason = f"SADECE SHORT | LONG bloklu: {'; '.join(long_blocks)}"
    else:
        reason = f"IKI YON DE BLOKLU | L: {'; '.join(long_blocks)} | S: {'; '.join(short_blocks)}"
    return {
        'hurst': round(float(h), 3) if (h is not None and not np.isnan(h)) else None,
        'sma_slope': round(float(slope), 3) if (slope is not None and not np.isnan(slope)) else None,
        'donchian_broke_high': donch['broke_high'],
        'donchian_broke_low': donch['broke_low'],
        'long_allowed': long_allowed,
        'short_allowed': short_allowed,
        'reason': reason
    }
