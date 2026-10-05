"""Legacy EWMA equations with explicit input validation; no fitted probability model."""
import numpy as np
import pandas as pd


def forecast_inputs(frame):
    # get_ohlc returns None when the provider response has no values.
    # The legacy frame['close'] then raises TypeError: NoneType not subscriptable.
    if frame is None or not isinstance(frame, pd.DataFrame) or 'close' not in frame:
        raise ValueError('D1 verisi yok: sağlayıcı geçerli OHLC döndürmedi')
    closes = pd.to_numeric(frame['close'], errors='coerce')
    if len(closes) < 22 or not np.isfinite(closes).all() or (closes <= 0).any():
        raise ValueError('D1: en az 22 sonlu ve pozitif kapanış gerekli')
    returns = np.log(closes).diff().dropna()
    sd20 = float(returns.tail(20).std())
    ev = float(returns.tail(60).var())
    for value in returns.tail(60).values:
        ev = 0.94*ev + 0.06*value*value
    ewma = float(np.sqrt(ev))
    cluster = float(np.clip(ewma/sd20 if sd20 > 0 else 1.0, 0.8, 1.6))
    vols = returns.rolling(20).std().dropna()
    cv = float(vols.iloc[-1])
    q1, q2 = float(vols.quantile(.33)), float(vols.quantile(.66))
    regime = 'SAKİN' if cv <= q1 else 'YÜKSEK-VOL' if cv >= q2 else 'NORMAL'
    return returns, sd20, cluster, sd20*cluster, cv, regime
