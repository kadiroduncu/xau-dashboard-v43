"""Standalone trend checker v2 - price-to-SMA fix"""
import streamlit as st
import requests
import pandas as pd
import numpy as np

st.set_page_config(page_title="Trend Check", layout="centered")
TD_KEY = st.secrets["TWELVEDATA_KEY"]

@st.cache_data(ttl=300)
def get_d1(size=120):
    r = requests.get("https://api.twelvedata.com/time_series",
                     params={"symbol": "XAU/USD", "interval": "1day",
                             "outputsize": size, "apikey": TD_KEY}).json()
    if 'values' not in r:
        return None
    df = pd.DataFrame(r['values']).astype({'open': float, 'high': float,
                                            'low': float, 'close': float})
    df['datetime'] = pd.to_datetime(df['datetime'])
    return df.sort_values('datetime').reset_index(drop=True)

def hurst(prices, max_lag=20):
    prices = np.asarray(prices, dtype=float)
    if len(prices) < max_lag + 1: return None
    lags = list(range(2, max_lag))
    tau = []
    for lag in lags:
        s = np.std(np.subtract(prices[lag:], prices[:-lag]))
        if s <= 0: return None
        tau.append(np.sqrt(s))
    slope, _ = np.polyfit(np.log(lags), np.log(tau), 1)
    return float(slope * 2.0)

df = get_d1(120)
st.title("🧭 Trend Check v2 — XAU/USD D1")

if df is None or len(df) < 100:
    st.error(f"Veri yetersiz ({0 if df is None else len(df)}/100)")
    st.stop()

closes = df['close']; highs = df['high']; lows = df['low']
price = closes.iloc[-1]
h = hurst(closes.tail(100).values)
sma50 = closes.rolling(50).mean()
sma20 = closes.rolling(20).mean()
slope_pct = (sma50.iloc[-1] - sma50.iloc[-10]) / sma50.iloc[-10] * 100
price_vs_sma50 = (price - sma50.iloc[-1]) / sma50.iloc[-1] * 100
ret_5d = (closes.iloc[-1] - closes.iloc[-6]) / closes.iloc[-6] * 100
don_high_20 = highs.rolling(20).max().shift(1).iloc[-1]
don_low_20 = lows.rolling(20).min().shift(1).iloc[-1]
broke_low = bool((closes.tail(3) < don_low_20).any())
broke_high = bool((closes.tail(3) > don_high_20).any())

long_blocks, short_blocks = [], []
if price_vs_sma50 < -1.0:
    long_blocks.append(f"Fiyat SMA50'den %{price_vs_sma50:+.1f}")
elif price_vs_sma50 > 1.0:
    short_blocks.append(f"Fiyat SMA50'den %{price_vs_sma50:+.1f}")
if ret_5d < -1.5:
    long_blocks.append(f"5-gun getirisi {ret_5d:+.1f}%")
elif ret_5d > 1.5:
    short_blocks.append(f"5-gun getirisi {ret_5d:+.1f}%")
if h and h > 0.55:
    long_blocks.append(f"Hurst={h:.2f}>0.55 (trend)")
    short_blocks.append(f"Hurst={h:.2f}>0.55 (trend)")
if broke_low:
    long_blocks.append("Donchian-20 LOW kirildi")
if broke_high:
    short_blocks.append("Donchian-20 HIGH kirildi")

long_ok = len(long_blocks) == 0
short_ok = len(short_blocks) == 0

st.metric("Fiyat", f"${price:.2f}")
c1, c2, c3 = st.columns(3)
c1.metric("Fiyat vs SMA-50", f"{price_vs_sma50:+.1f}%",
          "downtrend" if price_vs_sma50 < -1 else "uptrend" if price_vs_sma50 > 1 else "neutral")
c2.metric("5-gun getirisi", f"{ret_5d:+.1f}%")
c3.metric("Hurst", f"{h:.2f}" if h else "—")

st.divider()
c1, c2 = st.columns(2)
c1.metric("LONG izin", "✅ SERBEST" if long_ok else "🚫 BLOKLU")
c2.metric("SHORT izin", "✅ SERBEST" if short_ok else "🚫 BLOKLU")

if not long_ok:
    st.warning(f"**LONG BLOKLU** — {', '.join(long_blocks)}")
if not short_ok:
    st.warning(f"**SHORT BLOKLU** — {', '.join(short_blocks)}")
if long_ok and short_ok:
    st.success("Iki yon de serbest")

with st.expander("Detay"):
    st.write(f"SMA-50: ${sma50.iloc[-1]:.2f}")
    st.write(f"SMA-20: ${sma20.iloc[-1]:.2f}")
    st.write(f"SMA-50 slope (10g): {slope_pct:+.2f}% (lagging)")
    st.write(f"Donchian-20 high: ${don_high_20:.2f} (broken: {broke_high})")
    st.write(f"Donchian-20 low: ${don_low_20:.2f} (broken: {broke_low})")
    st.write(f"Veri: {len(df)} D1 bar")
