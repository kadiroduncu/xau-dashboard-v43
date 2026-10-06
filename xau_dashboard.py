"""XAU/USD Trading Dashboard v36 (v35 + tek MASTER TRADEABLE GATE).
v35 -> v36 DEGISIKLIK (yeni edge/strateji YOK; tutarlilik + dogru etiketleme):
  MASTER GATE — tek dogruluk kaynagi: Trend(_tf) + Makro(_macro) + Suppression + Takvim(tier'li)
    -> tek verdikt (RED/YELLOW/GREEN). TUM aksiyon yuzeyleri buna uyar:
      * Ust KARAR rozeti artik kanal sinyali ILE master-gate'in DAHA KOTUMSERI'ni gosterir
        (yanlis yesil onlendi: makro RED / takvim blackout varken tepe 'GREEN Trade OK' DEMEZ).
      * Otoriter tam-genislik MASTER GATE banneri eklendi (tepe).
      * LIMIT EMIR PLANI artik event/macro hard-block'a da uyar (yalniz trend degil).
  TAKVIM TIER'LEME — generic 'high' (Existing Home Sales vb.) artik tier-1 blackout SANILMIYOR.
    Tier-1 (NFP/CPI/PCE/FOMC/Powell/core inflation) -> SERT blok + suppression;
    generic high -> yumusak dikkat. Etiket artik olayin gercek adi/tier'i ile yaziliyor.
v35 cekirdegi (K1/K2/K3 fix) ve v34 (kirilim vetosu), v33 mantigi KORUNDU. Korunan fonksiyonlar
imza/davranis olarak DEGISMEDI."""
import streamlit as st
from xau_signal_export import build_snapshot, publish_snapshot, utc_now
from pathlib import Path as _SignalPath
_signal_started_at = utc_now()
_signal_errors = []
_signal_probability = None
_signal_readonly = st.query_params.get("signal_readonly") == "1"
_signal_request_id = str(st.query_params.get("signal_request_id", ""))[:64]
from streamlit_autorefresh import st_autorefresh
import pandas as pd
import numpy as np
import requests
from datetime import datetime, timezone
from camarilla import camarilla_levels, trade_signal, confluence_signal
from news import fetch_news, fetch_calendar
from ai_sentiment import make_client, analyze, aggregate_sentiment
from traffic_light import news_channel, combined_signal
from sr_map import build_sr_map
import plotly.graph_objects as go
import json
from probability import forecast_inputs
from risk_panel import run_risk_panel, explain
from risk_engine import candidate_approved
from market_data import time_series, economic_calendar

st.set_page_config(page_title="XAU v43", layout="wide", page_icon="🥇")
st_autorefresh(interval=420000, key="auto_refresh")

TD_KEY = st.secrets["TWELVEDATA_KEY"]
FH_KEY = st.secrets["FINNHUB_KEY"]
AI = make_client(st.secrets["GEMINI_KEY"])
TG_TOKEN = None  # v43 review/paper build: outbound notifications disabled
TG_CHAT_ID = None
FRED_KEY = st.secrets.get("FRED_KEY")

with st.sidebar:
    CAPITAL = st.number_input("Sermaye ($)", value=3000, min_value=500, step=100)


# === FADE SAAT FILTRESI ===========================================================
# 12-16 UTC fade penceresi gate'i. Backtest p=0.54 -> istatistiksel olarak anlamsiz
# (fade hicbir saatte edge uretmiyor; iyi/kotu saat ayrimi gurultuye overfit). Bu yuzden
# KAPALI: fade her saat serbest. Eski 12-16 davranisini geri istersen True yap (ve V10
# prompt rule-4 + state satirini eski metnine dondur). YALNIZ FADE yolunu etkiler;
# V29 confluence session gate'i (ayri, backtest-valide) bundan BAGIMSIZDIR.
FADE_SESSION_GATE = False


def _in_fade_session(utc_hour):
    """Gate kapaliyken her saat True; aciksa yalniz 12-16 UTC."""
    return (not FADE_SESSION_GATE) or (12 <= utc_hour < 16)


_feed_errors = {}

def get_ohlc(interval, size=3):
    # All panel sizes share a single provider request for each timeframe.
    frame, error = time_series(TD_KEY, interval)
    if error:
        _feed_errors[interval] = error
    return None if frame is None else frame.tail(size).reset_index(drop=True)


def _resample_ohlc(df1h, rule):
    """1h df -> hedef TF (2h/6h/12h). TwelveData 6h/12h vermedigi icin client-side resample.
    YALNIZ GOSTERIM/confluence icin; trade mantigi kullanmaz."""
    if df1h is None or len(df1h) < 2:
        return None
    d = df1h.set_index('datetime')
    r = (d.resample(rule).agg({'open': 'first', 'high': 'max',
                               'low': 'min', 'close': 'last'})
         .dropna().reset_index())
    return r if len(r) >= 2 else None


def _render_cam_table(cam, price, height=350):
    """Camarilla seviye tablosunu RENKLI render eder: R yesil, S kirmizi, NOW sari, PP gri."""
    items = [(L, cam[L]) for L in ['R4', 'R3', 'R2', 'R1', 'PP', 'S1', 'S2', 'S3', 'S4']]
    items.append(('NOW', price))
    items.sort(key=lambda x: -x[1])
    rows = []
    for nm, v in items:
        if nm == 'NOW':
            rows.append({'Lvl': 'NOW', 'Price': f"${v:.2f}", 'Dist': '---', ' ': '<<< FIYAT'})
        else:
            dd = price - v
            mk = "<--" if abs(dd) < 5 else ""
            rows.append({'Lvl': nm, 'Price': f"${v:.2f}", 'Dist': f"{dd:+.1f}", ' ': mk})
    _df = pd.DataFrame(rows)

    def _row_style(r):
        lvl = str(r['Lvl'])
        if lvl == 'NOW':
            css = 'background-color: rgba(255,210,0,0.30); color: #ffd400; font-weight: bold'
        elif lvl.startswith('R'):
            css = 'color: #2ecc71; font-weight: bold'      # yesil = direnc (R)
        elif lvl.startswith('S'):
            css = 'color: #e74c3c; font-weight: bold'      # kirmizi = destek (S)
        else:
            css = 'color: #9aa0a6'                          # PP = gri
        return [css] * len(r)

    st.dataframe(_df.style.apply(_row_style, axis=1),
                 hide_index=True, use_container_width=True, height=height)


def _render_sr_map(price, chart_bars=120, near=250):
    """Strength-ranked S/R map (sr_map.py): renkli tablo + mum grafigi uzerine cizgiler.

    EMPIRIK TEMEL (research/sr_validation_results.md): YALNIZ D1 pivotlari bootstrap
    testini gecti (+2.7..+3.5pp, p<0.05). Yuvarlak sayilar friction/volatilite bolgesi
    (bounce edge'i YOK). Harita bir RISK/CONTEXT katmanidir, trade sinyali DEGILDIR.
    """
    df = get_ohlc("1day", 500)
    if df is None or len(df) < 60:
        st.warning("S/R haritasi icin D1 verisi alinamadi")
        return
    m = build_sr_map(df, lookback=min(500, len(df)))
    if m.empty:
        st.info("S/R seviyesi bulunamadi")
        return

    col_t, col_c = st.columns([1, 2])

    # --- Tablo: fiyata yakin seviyeler, guce gore ---
    with col_t:
        nearm = m[m['dist'].abs() <= near].copy()
        rows = []
        for _, r in nearm.iterrows():
            mark = "<<<" if abs(r['dist']) < 8 else ""
            bf = "" if pd.isna(r['bounce_freq']) else f"{r['bounce_freq']*100:.0f}%"
            rows.append({'Lvl': f"${r['level']:.0f}", 'Tip': r['tier'],
                         'Rol': r['role'][:4], 'Dok': int(r['touches']),
                         'Bnc': bf, 'Guc': f"{r['strength']:.0f}",
                         'Dist': f"{r['dist']:+.0f}", ' ': mark})
        _t = pd.DataFrame(rows)

        def _style(row):
            role = str(row['Rol'])
            if row[' '] == "<<<":
                css = 'background-color: rgba(255,210,0,0.25); color:#ffd400; font-weight:bold'
            elif str(row['Tip']) == '🟡':
                css = 'color:#9aa0a6'                       # friction/round = gri
            elif role.startswith('resi'):
                css = 'color:#2ecc71; font-weight:bold'     # direnc = yesil (app standardi)
            else:
                css = 'color:#e74c3c; font-weight:bold'     # destek = kirmizi
            return [css] * len(row)
        st.dataframe(_t.style.apply(_style, axis=1), hide_index=True,
                     use_container_width=True, height=420)
        st.caption("🟢 doğrulanmış D1 pivot · 🟡 friction (yuvarlak — range bekle, dönüş değil) · "
                   "Güç = geçmiş bounce oranı (tek OOS-doğrulanmış öngörücü; dokunuş sayısı DEĞİL)")

    # --- Grafik: SAATLIK (H1) mumlar + D1 seviyeleri (etiketli) ---
    # Seviyeler D1'den gelir (tek dogrulanan); chart H1 cunku ince gorunum daha faydali.
    with col_c:
        dch = get_ohlc("1h", 200)
        tf_label = "H1"
        if dch is None or len(dch) < 30:
            dch, tf_label = df.tail(chart_bars), "D1"   # H1 yoksa D1'e dus
        fig = go.Figure(go.Candlestick(
            x=dch['datetime'], open=dch['open'], high=dch['high'],
            low=dch['low'], close=dch['close'], name='XAU',
            increasing_line_color='#26a69a', decreasing_line_color='#ef5350'))
        lo_v, hi_v = dch['low'].min(), dch['high'].max()
        pad = (hi_v - lo_v) * 0.04
        vis = m[(m['level'] >= lo_v - pad) & (m['level'] <= hi_v + pad)]
        # kalabaligi onle: en guclu 12 pivot + yalniz $100+ yuvarlak sayilar
        piv = vis[vis['tier'] == '🟢'].nlargest(12, 'strength')
        rnd = vis[(vis['tier'] == '🟡') & (vis['level'] % 100 == 0)]
        vis = pd.concat([piv, rnd])
        for _, r in vis.iterrows():
            if r['tier'] == '🟡':
                color, w = 'rgba(154,160,166,0.55)', 1
                label = f"${r['level']:.0f}"            # friction: yalniz fiyat
            else:
                color = '#2ecc71' if r['role'].startswith('resi') else '#e74c3c'
                w = 1 + r['strength'] / 35.0            # guce gore kalinlik
                label = f"${r['level']:.0f} · G{r['strength']:.0f}"  # fiyat + guc
            fig.add_hline(y=r['level'], line_color=color, line_width=w,
                          line_dash=('dot' if r['tier'] == '🟡' else 'solid'),
                          opacity=0.85 if r['tier'] == '🟢' else 0.5,
                          annotation_text=label, annotation_position="right",
                          annotation_font_size=10, annotation_font_color=color)
        fig.add_hline(y=price, line_color='#ffd400', line_width=1.5, line_dash='dash',
                      annotation_text=f"NOW ${price:.0f}", annotation_position="left",
                      annotation_font_size=11, annotation_font_color='#ffd400')
        fig.update_layout(height=460, margin=dict(l=0, r=64, t=10, b=0),
                          xaxis_rangeslider_visible=False, showlegend=False,
                          template='plotly_dark',
                          title=dict(text=f"{tf_label} · D1 seviyeleri", x=0.01, font_size=12))
        st.plotly_chart(fig, use_container_width=True)


@st.cache_data(ttl=15)
def get_price_snapshot():
    try:
        r = requests.get("https://api.twelvedata.com/price",
                         params={"symbol": "XAU/USD", "apikey": TD_KEY}).json()
        return float(r.get('price', 0)), utc_now()
    except Exception:
        return 0, None


@st.cache_data(ttl=600)
def get_news_and_sentiment():
    news = fetch_news(FH_KEY, hours_back=4, limit=10)
    analyses = [analyze(AI, n.get('headline', ''), n.get('summary', ''))
                for n in news[:3]]
    agg = aggregate_sentiment(analyses)
    return news, analyses, agg


@st.cache_data(ttl=900)
def get_calendar():
    return fetch_calendar(FH_KEY)


# ============ TREND FILTER (inline, no external module) ============
def _hurst_inline(prices, max_lag=20):
    prices = np.asarray(prices, dtype=float)
    if len(prices) < max_lag + 1:
        return None
    lags = list(range(2, max_lag))
    tau = []
    for lag in lags:
        s = np.std(np.subtract(prices[lag:], prices[:-lag]))
        if s <= 0:
            return None
        tau.append(np.sqrt(s))
    try:
        slope, _ = np.polyfit(np.log(lags), np.log(tau), 1)
        return float(slope * 2.0)
    except Exception:
        return None


def compute_trend_gate(df):
    """Returns dict with long_allowed, short_allowed, reason, metrics"""
    if df is None or len(df) < 100:
        return {'long_allowed': True, 'short_allowed': True,
                'reason': 'Yetersiz D1 verisi',
                'metrics': {}}
    closes = df['close']
    highs = df['high']
    lows = df['low']
    price = closes.iloc[-1]
    h = _hurst_inline(closes.tail(100).values)
    sma50 = closes.rolling(50).mean()
    price_vs_sma50 = (price - sma50.iloc[-1]) / sma50.iloc[-1] * 100
    ret_5d = (closes.iloc[-1] - closes.iloc[-6]) / closes.iloc[-6] * 100
    don_low = lows.rolling(20).min().shift(1).iloc[-1]
    don_high = highs.rolling(20).max().shift(1).iloc[-1]
    broke_low = bool((closes.tail(3) < don_low).any())
    broke_high = bool((closes.tail(3) > don_high).any())

    long_blocks = []
    short_blocks = []
    if price_vs_sma50 < -1.0:
        long_blocks.append(f"Fiyat SMA50 alti %{price_vs_sma50:+.1f}")
    elif price_vs_sma50 > 1.0:
        short_blocks.append(f"Fiyat SMA50 ustu %{price_vs_sma50:+.1f}")
    if ret_5d < -1.5:
        long_blocks.append(f"5g getiri {ret_5d:+.1f}%")
    elif ret_5d > 1.5:
        short_blocks.append(f"5g getiri {ret_5d:+.1f}%")
    if h is not None and h > 0.55:
        long_blocks.append(f"Hurst={h:.2f} trend")
        short_blocks.append(f"Hurst={h:.2f} trend")
    if broke_low:
        long_blocks.append("Donch-20 LOW kirildi")
    if broke_high:
        short_blocks.append("Donch-20 HIGH kirildi")

    long_ok = len(long_blocks) == 0
    short_ok = len(short_blocks) == 0
    if long_ok and short_ok:
        reason = "Iki yon serbest"
    elif long_ok:
        reason = f"SADECE LONG | SHORT bloklu: {', '.join(short_blocks)}"
    elif short_ok:
        reason = f"SADECE SHORT | LONG bloklu: {', '.join(long_blocks)}"
    else:
        reason = f"IKI YON BLOKLU"

    return {
        'long_allowed': long_ok,
        'short_allowed': short_ok,
        'reason': reason,
        'metrics': {
            'hurst': h,
            'price_vs_sma50': price_vs_sma50,
            'ret_5d': ret_5d,
            'broke_high': broke_high,
            'broke_low': broke_low,
            'sma50': sma50.iloc[-1],
        }
    }


# ============ MAKRO BIAS (D1) — FRED: reel getiri + dolar + petrol ============
# Yalniz SIKILASTIRIR: confluence sinyalini sadece yone-TERS makroda veto eder. Yeni trade URETMEZ.
# Esikler yargi/tunable - gecmise OPTIMIZE ETME (overfit). Makro mantikla birak.
MACRO_RY_WIN, MACRO_RY_BP = 5, 8        # 5-gunde >= +8bp reel getiri -> gold headwind
MACRO_USD_WIN, MACRO_USD_PCT = 5, 0.5   # 5-gunde >= %0.5 broad-USD -> headwind
MACRO_OIL_WIN, MACRO_OIL_PCTL = 20, 80  # oil 20-gun %80 percentile ustu -> supply-shock headwind


@st.cache_data(ttl=3600)
def fred_series(series_id, n=40):
    """FRED gunluk seri (eski->yeni sirali float listesi). Key yok / hata -> None."""
    if not FRED_KEY:
        return None
    try:
        r = requests.get(
            "https://api.stlouisfed.org/fred/series/observations",
            params={"series_id": series_id, "api_key": FRED_KEY,
                    "file_type": "json", "sort_order": "desc", "limit": n},
            timeout=10,
        ).json()
        vals = [float(o["value"]) for o in r.get("observations", [])
                if o.get("value") not in (".", None, "")]
        return list(reversed(vals)) if vals else None
    except Exception:
        return None


def _macro_chg_bp(s, w):
    return None if not s or len(s) <= w else (s[-1] - s[-1 - w]) * 100


def _macro_chg_pct(s, w):
    return None if not s or len(s) <= w else (s[-1] - s[-1 - w]) / s[-1 - w] * 100


def _macro_pctl(s, w):
    return None if not s or len(s) < w else sum(x <= s[-1] for x in s[-w:]) / w * 100


def compute_macro_bias(ry, usd, oil, gvz_high):
    """ry=DFII10 (reel getiri), usd=DTWEXBGS (broad USD), oil=DCOILWTICO (WTI); FRED gunluk eski->yeni.
    Donus: {'bias','why','factors'} — bias gold acisindan HEADWIND/TAILWIND/NEUTRAL."""
    ry_bp = _macro_chg_bp(ry, MACRO_RY_WIN)
    usd_p = _macro_chg_pct(usd, MACRO_USD_WIN)
    oil_p = _macro_pctl(oil, MACRO_OIL_WIN)
    hw, tw, why = 0, 0, []
    if ry_bp is not None:
        if ry_bp >= MACRO_RY_BP:
            hw += 2; why.append(f"Reel getiri {ry_bp:+.0f}bp/5g -> headwind")
        elif ry_bp <= -MACRO_RY_BP:
            tw += 2; why.append(f"Reel getiri {ry_bp:+.0f}bp/5g -> tailwind")
    if usd_p is not None:
        if usd_p >= MACRO_USD_PCT:
            hw += 1; why.append(f"USD {usd_p:+.1f}%/5g -> headwind")
        elif usd_p <= -MACRO_USD_PCT:
            tw += 1; why.append(f"USD {usd_p:+.1f}%/5g -> tailwind")
    if oil_p is not None and oil_p >= MACRO_OIL_PCTL:
        hw += 1; why.append(f"Petrol {oil_p:.0f}p (enflasyon/getiri riski) -> headwind")
    if gvz_high:
        why.append("GVZ yuksek: fade whipsaw riski")
    bias = "HEADWIND" if hw - tw >= 2 else "TAILWIND" if tw - hw >= 2 else "NEUTRAL"
    return {"bias": bias, "why": why,
            "factors": {"ry_bp": ry_bp, "usd_pct": usd_p, "oil_pctl": oil_p, "gvz_high": gvz_high}}


# ============================================================
# V10 LAYER — F5 trend, V6 risk score, V10 signal, V10 notifications
# ============================================================

def compute_f5_state(df_d1):
    """F5 BULL onayı: close > SMA200 AND SMA200 10g yükselen AND DD200 > -3%
    Return: (is_bull, reasons_list)"""
    if df_d1 is None or len(df_d1) < 210:
        return False, [f"Yetersiz D1 verisi (n={len(df_d1) if df_d1 is not None else 0}, gerekli 210+)"]
    close = df_d1['close'].values
    sma200 = pd.Series(close).rolling(200).mean().values
    if np.isnan(sma200[-1]):
        return False, ["SMA200 NaN"]
    cond_price = close[-1] > sma200[-1]
    cond_slope = sma200[-1] > sma200[-11]
    peak_200 = close[-200:].max()
    dd_pct = (close[-1] / peak_200 - 1) * 100
    cond_dd = dd_pct > -3
    is_bull = cond_price and cond_slope and cond_dd
    reasons = [
        f"{'✓' if cond_price else '✗'} Close>SMA200 ({close[-1]:.0f} vs {sma200[-1]:.0f})",
        f"{'✓' if cond_slope else '✗'} SMA200 10g yükselen",
        f"{'✓' if cond_dd else '✗'} DD200>-3% (şu an {dd_pct:+.1f}%)",
    ]
    return is_bull, reasons


def compute_v6_risk_score(df_d1, utc_now=None):
    """V6 breakout risk skoru 0-5. Her +1 risk faktörü:
    Pazartesi, tehlikeli saat (00,01,12,13 UTC), prev range/ATR>1.5, ATR5/ATR20>1.3, vol z>1.5
    Return: (score, reasons_list)"""
    if utc_now is None:
        utc_now = datetime.utcnow()
    score = 0
    reasons = []
    # 1. Pazartesi
    if utc_now.weekday() == 0:
        score += 1
        reasons.append("🔴 Pazartesi (+1) %98 breakout")
    else:
        reasons.append("🟢 Gün OK")
    # 2. Tehlikeli saat
    if utc_now.hour in [0, 1, 12, 13]:
        score += 1
        reasons.append(f"🔴 Saat {utc_now.hour:02d}:00 UTC tehlikeli (+1)")
    else:
        reasons.append(f"🟢 Saat {utc_now.hour:02d}:00 UTC OK")
    # 3,4 ATR/range tabanlı
    if df_d1 is not None and len(df_d1) >= 22:
        h = df_d1['high'].values
        l = df_d1['low'].values
        c = df_d1['close'].values
        tr_list = []
        for i in range(-21, -1):
            t = max(h[i]-l[i], abs(h[i]-c[i-1]), abs(l[i]-c[i-1]))
            tr_list.append(t)
        atr20 = np.mean(tr_list)
        atr5 = np.mean(tr_list[-5:])
        prev_range = h[-2] - l[-2]
        range_ratio = prev_range / atr20 if atr20 > 0 else 0
        if range_ratio > 1.5:
            score += 1
            reasons.append(f"🔴 Önceki gün range/ATR20={range_ratio:.2f} >1.5 (+1)")
        else:
            reasons.append(f"🟢 Range/ATR20 OK ({range_ratio:.2f})")
        atr_ratio_v = atr5 / atr20 if atr20 > 0 else 0
        if atr_ratio_v > 1.3:
            score += 1
            reasons.append(f"🔴 ATR5/ATR20={atr_ratio_v:.2f} >1.3 vol expansion (+1)")
        else:
            reasons.append(f"🟢 ATR ratio OK ({atr_ratio_v:.2f})")
    return score, reasons


def detect_v10_signal(price, lvls_d1, f5_bull, risk_score, utc_hour, n_open_fades):
    """V10 sinyal: F5 OFF + risk<2 + saat 12-16 UTC + max 3 fade + R3/S3 dokunma
    Return: dict or None"""
    if lvls_d1 is None:
        return None
    R3, R4, S3, S4 = lvls_d1['R3'], lvls_d1['R4'], lvls_d1['S3'], lvls_d1['S4']
    # F5 BULL: breakout sinyali
    if f5_bull:
        if price > R4:
            return {'type': 'BREAKOUT_LONG', 'entry': R4 + 0.01, 'tp': None, 'sl': None,
                    'note': 'F5 BULL onayı + R4 kırıldı (multi-day chandelier)'}
        return None
    # F5 OFF: fade — filtreler
    if risk_score >= 2:
        return None
    if not _in_fade_session(utc_hour):
        return None
    if n_open_fades >= 3:
        return None
    # SHORT fade: fiyat R3'e yakın
    if -3 <= (price - R3) <= 5:
        return {'type': 'FADE_SHORT', 'entry': R3, 'tp': R3 - 10, 'sl': R3 + 30,
                'note': f"R3 dokunma ({price-R3:+.2f})"}
    # LONG fade: fiyat S3'e yakın
    if -3 <= (S3 - price) <= 5:
        return {'type': 'FADE_LONG', 'entry': S3, 'tp': S3 + 10, 'sl': S3 - 30,
                'note': f"S3 dokunma ({price-S3:+.2f})"}
    return None


# ============================================================================
# V34 — KIRILIM VETOSU (Efficiency Ratio, H1). KORUMA katmani, edge degil.
# Hicbir mevcut fonksiyona dokunmaz; ayri/additive.
# ============================================================================
def compute_breakout_veto(df_h1, n=10, er_threshold=0.50):
    """Fiyat fade seviyesine NASIL geldi? -> Efficiency Ratio (Kaufman).
    ER = |son kapanis - n bar onceki kapanis| / toplam(|bar-bar degisim|), son n H1 mumu.
    ER 1'e yakin = duz/hizli/tek yonlu = KIRILIM karakteri -> fade VETO.
    ER 0'a yakin = zikzak/range = fade serbest.
    Yon-bagimsizdir; tarafi (short R3 / long S3) zaten dokunulan seviye belirler.
    Walk-forward dogrulandi: 20/20 OOS yil kaybi azaltti, sabit esik 0.50 19/20 yil ayristirdi.
    Fren'dir, motor degil: kaybi ~2/3 keser ama fade'i kara cevirmez.
    Donus: (veto_active: bool, er: float|None, reason: str)
    Fail-open: H1 yoksa/yetersizse veto PAS (False) -> yanlislikla bloke etmez.
    """
    if df_h1 is None or len(df_h1) < n + 1:
        return False, None, "H1 verisi yetersiz — veto PAS"
    try:
        closes = df_h1['close'].astype(float).values[-(n + 1):]
    except Exception:
        return False, None, "H1 close okunamadi — veto PAS"
    net = abs(closes[-1] - closes[0])
    path = float(np.sum(np.abs(np.diff(closes))))
    if path <= 0:
        return False, None, "ER hesaplanamadi (path=0) — veto PAS"
    er = net / path
    if er >= er_threshold:
        return True, er, f"ER={er:.2f} ≥ {er_threshold:.2f} → seviyeye DÜZ/HIZLI geldi (kırılım)"
    return False, er, f"ER={er:.2f} < {er_threshold:.2f} → zikzak/range yaklaşımı"


def validate_v10_positions(positions, current_price):
    """Açık pozisyonlar V10 kurallarına uyuyor mu?"""
    warnings = []
    fades = [p for p in positions if p.get('type') == 'fade']
    if len(fades) > 3:
        warnings.append(f"🚨 {len(fades)} fade pozisyon (LİMİT: 3)")
    for i, p in enumerate(fades):
        entry, sl, tp, side = p['entry'], p.get('sl'), p.get('tp'), p['side']
        if sl is None:
            warnings.append(f"🚨 #{i+1} {side} @${entry:.2f}: SL YOK — HEMEN -$30 KOY")
        elif abs(sl - entry) > 35:
            warnings.append(f"⚠️ #{i+1} {side}: SL=${abs(sl-entry):.0f} (>$30)")
        if tp and abs(tp - entry) > 15:
            warnings.append(f"⚠️ #{i+1} {side}: TP çok geniş")
        # Trail check
        pnl = (current_price - entry) if side == 'LONG' else (entry - current_price)
        if pnl >= 15:
            new_sl = entry + 10 if side == 'LONG' else entry - 10
            warnings.append(f"🟡 #{i+1} {side}: +${pnl:.1f} kâr → SL'i ${new_sl:.2f}'e çek (trail)")
    return warnings


# ============================================================
# MAGNET LEVELS — likidite mıknatısları (PDH, PDL, round, Asia H/L, equal H/L)
# ============================================================

def compute_magnet_levels(df_d1, df_h1, current_price):
    """Stop hunt mıknatısı olabilecek seviyeleri tespit et"""
    magnets = []
    
    # 1. PDH / PDL (önceki gün H/L)
    if df_d1 is not None and len(df_d1) >= 2:
        prev_day = df_d1.iloc[-2]
        pdh = float(prev_day['high'])
        pdl = float(prev_day['low'])
        magnets.append({'label': 'PDH', 'price': pdh, 'type': 'resistance',
                        'note': 'Önceki gün High — stop hunt mıknatısı'})
        magnets.append({'label': 'PDL', 'price': pdl, 'type': 'support',
                        'note': 'Önceki gün Low — stop hunt mıknatısı'})
    
    # 2. Round numbers - en yakın $50 ve $25 katları
    p = current_price
    round_50_below = (int(p) // 50) * 50
    round_50_above = round_50_below + 50
    round_25_below = (int(p) // 25) * 25
    round_25_above = round_25_below + 25
    # Sadece $25 katları olanları al (50'liler zaten 25'in alt kümesi)
    for r in [round_25_below, round_25_above]:
        is_50 = (r % 50 == 0)
        magnets.append({
            'label': f'Round ${r}' + (' (büyük)' if is_50 else ''),
            'price': float(r),
            'type': 'magnet',
            'note': 'Yuvarlak sayı - psikolojik mıknatıs' + (' (güçlü)' if is_50 else ''),
        })
    
    # 3. Asia seans H/L (00:00-07:00 UTC bugün)
    if df_h1 is not None and len(df_h1) > 0:
        last_ts = df_h1['datetime' if 'datetime' in df_h1.columns else df_h1.columns[0]]
        try:
            df_h1_dt = pd.to_datetime(last_ts)
            today_utc = datetime.utcnow().date()
            asia_mask = (df_h1_dt.dt.date == today_utc) & (df_h1_dt.dt.hour < 7)
            asia_bars = df_h1[asia_mask]
            if len(asia_bars) >= 2:
                ah = float(asia_bars['high'].max())
                al = float(asia_bars['low'].min())
                magnets.append({'label': 'Asia H', 'price': ah, 'type': 'resistance',
                                'note': 'Asia seans High — London open hunt mıknatısı'})
                magnets.append({'label': 'Asia L', 'price': al, 'type': 'support',
                                'note': 'Asia seans Low — London open hunt mıknatısı'})
        except Exception:
            pass
    
    # 4. Equal highs/lows - son 10 D1 barda iki kez aynı seviyeye dokunma (±$3)
    if df_d1 is not None and len(df_d1) >= 10:
        recent = df_d1.tail(10)
        highs = recent['high'].values
        lows = recent['low'].values
        # Eşit high'lar: en yüksek 3 high'tan iki tanesi $3 içinde mi?
        sorted_h = sorted(highs, reverse=True)[:5]
        for i in range(len(sorted_h)):
            for j in range(i+1, len(sorted_h)):
                if abs(sorted_h[i] - sorted_h[j]) <= 3:
                    magnets.append({'label': 'Equal H', 'price': float((sorted_h[i]+sorted_h[j])/2),
                                    'type': 'resistance',
                                    'note': 'Son 10 günde iki kez tepe — stop birikme (ICT)'})
                    break
            else:
                continue
            break
        sorted_l = sorted(lows)[:5]
        for i in range(len(sorted_l)):
            for j in range(i+1, len(sorted_l)):
                if abs(sorted_l[i] - sorted_l[j]) <= 3:
                    magnets.append({'label': 'Equal L', 'price': float((sorted_l[i]+sorted_l[j])/2),
                                    'type': 'support',
                                    'note': 'Son 10 günde iki kez dip — stop birikme (ICT)'})
                    break
            else:
                continue
            break
    
    # Mesafe hesapla ve sırala
    for m in magnets:
        m['distance'] = m['price'] - current_price
        m['distance_abs'] = abs(m['distance'])
    
    # En yakın 8'i döndür (mesafe sırasıyla)
    magnets.sort(key=lambda x: x['distance_abs'])
    return magnets[:8]


def nearest_magnet_warning(magnets, current_price, threshold=10):
    """Fiyat bir magnet'e $10'dan yakınsa uyarı listesi döndür"""
    warns = []
    for m in magnets:
        if m['distance_abs'] <= threshold:
            direction = "↑" if m['distance'] > 0 else "↓"
            warns.append(f"{m['label']} ({direction}{m['distance_abs']:.0f} USD) — {m['note']}")
    return warns


# ============================================================
# AI DECISION ENGINE — tüm veriyi LLM'ye verip karar al
# ============================================================

V10_STRATEGY_PROMPT = """Sen Abdulkadir'in V10 trade sisteminin danışmanısın.

V10 KURALLARI (DEĞİŞTİRİLEMEZ - sen bunları DEĞİL, mevcut duruma uygunluğunu değerlendir):
1. F5 BULL onayı → fade YOK, sadece R4 LONG breakout (multi-day chandelier exit)
2. F5 OFF → fade aktif: SHORT @ R3, LONG @ S3, TP=$10, SL=$30 sabit
3. V6 risk skoru >=2 → fade DEVRE DIŞI (breakout riski yüksek)
4. Saat filtresi KAPALI: fade HER saat serbest (12-16 UTC kısıtı kaldırıldı — backtest p=0.54, istatistiksel anlamsız). Saati fade engeli olarak KULLANMA.
5. Max 3 paralel fade pozisyon
6. F5 BULL onayı geldiğinde tüm açık fade'leri zorla kapan
7. Pazartesi fade YASAK (%98 breakout oranı)

EK PRENSİPLER:
- Suppression (Gold Impact LLM) aktifse SHORT fade riskli — yukarı kaçma riski
- PDH/PDL ve round number magnetleri ÇİFT YÖNLÜ:
  (a) KORUMA: fiyat magnet'e $5 içindeyse hunt riski yüksek, yeni pozisyon ÖNERME
  (b) FIRSAT/CONFLUENCE: V10 sinyali ile aynı yöndeki magnet GÜÇLÜ ONAY:
      * R3 SHORT sinyali + en yakın magnet AŞAĞIDA → güçlü (fiyat magnet'e iner)
      * R3 SHORT sinyali + en yakın magnet YUKARIDA → zayıf (fiyat magnet'e çıkar)
      * S3 LONG sinyali + en yakın magnet YUKARIDA → güçlü
      * S3 LONG sinyali + en yakın magnet AŞAĞIDA → zayıf
- Asia H/L magnet'ine yakınsa London open hunt bekle (07-09 UTC)
- COT extreme = tersine dönme riski
- V26 trend filter yönü (SMA50): LONG-bloklu durumlar var
- AI olarak SEN: V10 kurallarını İHLAL EDEN sinyal ÖNERME. Sadece kurallara uygun durumda karar üret.
- Magnet confluence varsa "magnet_risk" alanına "düşük + güçlü onay" gibi yaz

ÇIKTIYI MUTLAKA AŞAĞIDAKİ JSON FORMATINDA VER:
{
  "action": "FADE_SHORT" | "FADE_LONG" | "BREAKOUT_LONG" | "WAIT" | "CLOSE_FADES",
  "confidence": 1-10,
  "reasoning": "kısa gerekçe (max 50 kelime)",
  "warnings": ["uyarı1", "uyarı2", ...],
  "v10_compliant": true | false,
  "magnet_risk": "düşük" | "orta" | "yüksek",
  "next_check": "ne zaman tekrar bakılmalı (örn: '1 saat sonra', 'R3 dokunduğunda')"
}

NOT: action=WAIT en sık çıkacak karar - bekle ki doğru setup gelsin. Acele etme.
"""

def build_ai_context(state_dict):
    """State dict'i AI'nın anlayacağı biçime çevir"""
    lines = []
    lines.append(f"ZAMAN: {state_dict.get('time_utc', '?')}")
    lines.append(f"XAU FİYAT: ${state_dict.get('price', 0):.2f}")
    lines.append("")
    lines.append("=== V26 KARAR KATMANI ===")
    lines.append(f"Ana karar: {state_dict.get('v26_karar', '?')}")
    lines.append(f"Kanallar: {state_dict.get('v26_channels', {})}")
    lines.append(f"Trend Filtre: {state_dict.get('v26_trend', {})}")
    lines.append(f"Gold Impact: {state_dict.get('v26_impact', {})}")
    _mb = state_dict.get('macro_bias', {})
    if _mb:
        lines.append(f"Makro Bias (D1, gold): {_mb.get('bias', '?')} — {', '.join(_mb.get('why', []))}")
    lines.append("")
    lines.append("=== V10 KATMANI ===")
    lines.append(f"F5 Modu: {state_dict.get('v10_f5_mode', '?')}")
    lines.append(f"V6 Risk: {state_dict.get('v10_risk', '?')}/4")
    lines.append(f"Saat UTC: {state_dict.get('utc_hour', '?')} (saat filtresi KAPALI — fade her saat serbest)")
    lines.append(f"Açık fade: {state_dict.get('open_fades', 0)}/3")
    lines.append(f"V10 sinyali (kural-bazlı): {state_dict.get('v10_signal', 'Yok')}")
    lines.append("")
    lines.append("=== CAMARILLA PIVOTLAR (D1) ===")
    pivots = state_dict.get('pivots', {})
    for k in ['R4', 'R3', 'P', 'S3', 'S4']:
        v = pivots.get(k)
        if v:
            dist = v - state_dict.get('price', 0)
            lines.append(f"  {k}: ${v:.2f} ({dist:+.1f}$)")
    lines.append("")
    lines.append("=== MAGNET SEVİYELERİ (yakındakiler) ===")
    for m in state_dict.get('magnets', [])[:6]:
        lines.append(f"  {m['label']}: ${m['price']:.0f} ({m['distance']:+.1f}$) - {m['note']}")
    lines.append("")
    return "\n".join(lines)


def ai_decide(client, state_dict, history_summary="", groq_key=None):
    """Gemini'ye sor, kota dolu ise Groq'a fallback. JSON döner."""
    context = build_ai_context(state_dict)
    full_prompt = V10_STRATEGY_PROMPT + "\n\n=== MEVCUT DURUM ===\n" + context
    if history_summary:
        full_prompt += "\n\n=== SON TRADE'LERDEN ÖZET ===\n" + history_summary
    full_prompt += "\n\nKARAR VER (sadece JSON):"
    
    # Önce Gemini dene
    try:
        resp = client.models.generate_content(
            model="gemini-2.5-flash-lite",
            contents=full_prompt,
        )
        text = resp.text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
        result = json.loads(text)
        result['_provider'] = 'gemini'
        return result
    except Exception as gemini_err:
        # Gemini başarısız - Groq fallback dene
        if not groq_key:
            return {
                'action': 'WAIT', 'confidence': 0,
                'reasoning': f'Gemini hata, Groq key yok: {gemini_err}',
                'warnings': [], 'v10_compliant': True,
                'magnet_risk': '?', 'next_check': '5 dakika sonra',
                '_provider': 'none',
            }
        try:
            r = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"},
                json={
                    "model": "llama-3.3-70b-versatile",
                    "messages": [
                        {"role": "system", "content": "Sen V10 trade danışmanısın. SADECE geçerli JSON döndür, başka metin yok."},
                        {"role": "user", "content": full_prompt},
                    ],
                    "response_format": {"type": "json_object"},
                    "temperature": 0.3,
                },
                timeout=20,
            )
            r.raise_for_status()
            text = r.json()['choices'][0]['message']['content'].strip()
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
                text = text.strip()
            result = json.loads(text)
            result['_provider'] = 'groq'
            return result
        except Exception as groq_err:
            return {
                'action': 'WAIT', 'confidence': 0,
                'reasoning': f'Gemini+Groq başarısız. Gemini: {gemini_err} | Groq: {groq_err}',
                'warnings': [], 'v10_compliant': True,
                'magnet_risk': '?', 'next_check': '5 dakika sonra',
                '_provider': 'failed',
            }


# ============ TELEGRAM (inline) ============
def send_telegram(token, chat_id, message):
    if _signal_readonly:
        return False
    if not token or not chat_id:
        return False
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        r = requests.post(url, json={"chat_id": chat_id, "text": message,
                                     "parse_mode": "Markdown"}, timeout=5)
        return r.status_code == 200
    except Exception:
        return False


def notify_signal(token, chat_id, signal_level, price=None):
    """STRONG/VERY_STRONG sinyal - 30dk cooldown"""
    if signal_level not in ('STRONG', 'VERY_STRONG'):
        return
    key = "last_signal_notify"
    now = datetime.now()
    last = st.session_state.get(key)
    if last and (now - last).total_seconds() < 1800:
        return
    msg = f"🚨 *Confluence {signal_level}*"
    if price:
        msg += f"\nFiyat: ${price:.2f}"
    if send_telegram(token, chat_id, msg):
        st.session_state[key] = now


def notify_v10_signal(token, chat_id, sig, price, risk_score):
    """V10 sinyal alert - 15dk cooldown"""
    if not sig:
        return
    key = f"v10_{sig['type']}_notify"
    now = datetime.now()
    last = st.session_state.get(key)
    if last and (now - last).total_seconds() < 900:
        return
    icon = {'FADE_SHORT': '🔴', 'FADE_LONG': '🟢', 'BREAKOUT_LONG': '🚀'}.get(sig['type'], '⚪')
    msg = f"{icon} *V10 {sig['type']}*\n"
    msg += f"Fiyat: ${price:.2f}\n"
    msg += f"Entry: ${sig['entry']:.2f}\n"
    if sig.get('tp'):
        msg += f"TP: ${sig['tp']:.2f}\n"
    if sig.get('sl'):
        msg += f"SL: ${sig['sl']:.2f}\n"
    msg += f"Risk: {risk_score}/4\n{sig['note']}"
    if send_telegram(token, chat_id, msg):
        st.session_state[key] = now


def notify_f5_bull_close_fades(token, chat_id, n_fades):
    """F5 BULL onayı geldiğinde açık fade'ler için uyarı - 1h cooldown"""
    if n_fades == 0:
        return
    key = "v10_f5_bull_alert"
    now = datetime.now()
    last = st.session_state.get(key)
    if last and (now - last).total_seconds() < 3600:
        return
    msg = f"🚨 *V10: F5 BULL ONAYI*\n{n_fades} açık fade pozisyonu KAPAT!\nBreakout modu aktif."
    if send_telegram(token, chat_id, msg):
        st.session_state[key] = now


def notify_macro_red(token, chat_id):
    """Macro RED - 1 saat cooldown"""
    key = "last_macro_red"
    now = datetime.now()
    last = st.session_state.get(key)
    if last and (now - last).total_seconds() < 3600:
        return
    if send_telegram(token, chat_id, "🔴 *Macro RED* - islem iptal"):
        st.session_state[key] = now


def notify_daily_status(token, chat_id, plan):
    """Gunluk sabah plani - gunde 1 kez"""
    from datetime import date as _date
    key = f"daily_status_{_date.today().isoformat()}"
    if st.session_state.get(key):
        return
    capital = plan.get('capital', 0)
    p = plan.get('price', 0)
    d1 = plan.get('d1_levels') or {}
    lines = [
        "📊 *v39 Sabah Plani*",
        f"Tarih: {datetime.now().strftime('%d.%m.%Y %H:%M')}",
        f"Sermaye: ${capital:.0f}",
        f"Fiyat: ${p:.2f}",
    ]
    if d1:
        lines += [
            "",
            "*D1 Seviyeleri:*",
            f"R3: ${d1.get('R3', 0):.2f}",
            f"R1: ${d1.get('R1', 0):.2f}",
            f"S1: ${d1.get('S1', 0):.2f}",
            f"S3: ${d1.get('S3', 0):.2f}",
        ]
    if send_telegram(token, chat_id, "\n".join(lines)):
        st.session_state[key] = True


def notify_hourly_summary(token, chat_id, snapshot):
    """Saatlik tam ozet - 1 saat cooldown"""
    key = "last_hourly_summary"
    now = datetime.now()
    last = st.session_state.get(key)
    if last and (now - last).total_seconds() < 3600:
        return

    p = snapshot.get('price', 0)
    overall = snapshot.get('overall', '?')
    channels_snap = snapshot.get('channels', {})
    d1 = snapshot.get('d1') or {}
    h4 = snapshot.get('h4') or {}
    h1 = snapshot.get('h1') or {}
    final_level = snapshot.get('final_level', 'NONE')
    final_reason = snapshot.get('final_reason', '')
    trend = snapshot.get('trend') or {}

    emoji_map_tg = {'GREEN': '🟢', 'YELLOW': '🟡', 'RED': '🔴'}
    karar_emoji = emoji_map_tg.get(overall, '⚪')

    lines = [
        f"📊 *v39 Saatlik Ozet* {now.strftime('%H:%M')}",
        "",
        f"💰 Fiyat: ${p:.2f}",
        f"🎯 KARAR: {karar_emoji} {overall}",
        "",
    ]

    ch_line = "Kanallar: "
    for name in ['JUMP', 'COT', 'GVZ', 'NEWS']:
        ch_val = channels_snap.get(name)
        if isinstance(ch_val, tuple):
            color = ch_val[0]
        else:
            color = '?'
        ch_line += f"{name}{emoji_map_tg.get(color, '⚪')} "
    lines.append(ch_line.strip())
    lines.append("")

    if trend:
        m = trend.get('metrics', {}) or {}
        pvs = m.get('price_vs_sma50')
        ret5 = m.get('ret_5d')
        h_val = m.get('hurst')
        long_ok = trend.get('long_allowed', True)
        short_ok = trend.get('short_allowed', True)
        long_str = "✅" if long_ok else "🚫 BLOKLU"
        short_str = "✅" if short_ok else "🚫 BLOKLU"
        regime = ""
        if pvs is not None:
            if pvs < -1:
                regime = "(downtrend)"
            elif pvs > 1:
                regime = "(uptrend)"
            else:
                regime = "(neutral)"
        lines.append("🧭 *Trend Filtre (D1):*")
        if pvs is not None:
            lines.append(f"  Fiyat vs SMA-50: {pvs:+.1f}% {regime}")
        if ret5 is not None:
            lines.append(f"  5-gun getirisi: {ret5:+.1f}%")
        if h_val is not None:
            lines.append(f"  Hurst: {h_val:.2f}")
        lines.append(f"  LONG: {long_str}  |  SHORT: {short_str}")
        if not long_ok or not short_ok:
            reason = trend.get('reason', '')
            if reason:
                lines.append(f"  Sebep: {reason}")
        lines.append("")

    def tf_block(label, lvls, note=""):
        if not lvls:
            return [f"*{label}* {note}", "  veri yok", ""]
        r3 = lvls.get('R3', 0)
        r1 = lvls.get('R1', 0)
        s1 = lvls.get('S1', 0)
        s3 = lvls.get('S3', 0)
        return [
            f"*{label}* {note}",
            f"  R3: ${r3:.2f} ({r3-p:+.1f})",
            f"  R1: ${r1:.2f} ({r1-p:+.1f})",
            f"  S1: ${s1:.2f} ({s1-p:+.1f})",
            f"  S3: ${s3:.2f} ({s3-p:+.1f})",
            "",
        ]

    lines += tf_block("D1", d1, "(ana)")
    lines += tf_block("H4", h4, "(excluded - bilgi)")
    lines += tf_block("H1", h1, "(mikro)")

    # Suppression block (P3)
    sup = snapshot.get('suppression') or {}
    sup_score = sup.get('score', 0)
    if sup_score > 0.5:
        sup_dir = "BEARISH" if sup.get('bearish', 0) > sup.get('bullish', 0) else "BULLISH"
        sup_icon = "🚫" if sup_score > 5 else "⚠️"
        lines.append(f"{sup_icon} *Gold Impact:* ${sup_score:.0f}/oz ({sup_dir})")
        lines.append(f"  Relevant news: {sup.get('relevant_count', 0)}/5")
        lines.append("")

    lines.append(f"Confluence: {final_level}")
    if final_reason:
        lines.append(f"  {final_reason}")

    if send_telegram(token, chat_id, "\n".join(lines)):
        st.session_state[key] = now


def notify_gold_impact_alert(token, chat_id, summary):
    """High-impact gold news alert - 30dk cooldown"""
    if summary.get('suppression_score', 0) < 5:
        return
    key = "last_gold_impact_alert"
    now = datetime.now()
    last = st.session_state.get(key)
    if last and (now - last).total_seconds() < 1800:
        return
    direction = "BEARISH" if summary.get('immediate_bearish', 0) > summary.get('immediate_bullish', 0) else "BULLISH"
    msg = f"🚨 *Gold Impact Alert* — {direction}\n"
    msg += f"Beklenen impact: ${summary.get('suppression_score', 0):.0f}/oz\n"
    msg += f"Bearish: ${summary.get('immediate_bearish', 0):.0f}  |  Bullish: ${summary.get('immediate_bullish', 0):.0f}\n"
    titles = summary.get('titles', [])[:3]
    if titles:
        msg += "\nIlgili haberler:\n"
        for t in titles:
            msg += f"• {t[:80]}\n"
    if send_telegram(token, chat_id, msg):
        st.session_state[key] = now


# ============ GOLD IMPACT ANALYZER (LLM) ============
def analyze_gold_impact(ai_client, headline, summary):
    """Gold-specific deep impact analysis via Gemini 2.5 Flash-Lite

    Uses google.genai SDK pattern (same as ai_sentiment.py).
    """
    if not ai_client:
        return {'error': 'no client', 'is_gold_relevant': False}
    try:
        from google.genai import types as _genai_types
        prompt = f'''You are a senior commodities analyst specializing in gold markets.
Analyze this news for likely impact on XAU/USD (spot gold price).

Headline: "{headline}"
Summary: "{summary[:500]}"

Return ONLY this JSON, nothing else:
{{
  "is_gold_relevant": true or false (true if news could move gold by >$3/oz),
  "direction": "bullish" or "bearish" or "neutral",
  "magnitude_usd": number (estimated absolute $/oz move),
  "confidence": number 0 to 1,
  "channel": "usd" or "real_yields" or "geopolitical" or "inflation" or "etf_flows" or "central_bank" or "other",
  "timing": "immediate" or "delayed" or "already_priced",
  "reason": "one short sentence"
}}'''
        resp = ai_client.models.generate_content(
            model="gemini-2.5-flash-lite",
            contents=prompt,
            config=_genai_types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.2,
                max_output_tokens=400,
            )
        )
        return json.loads(resp.text.strip())
    except Exception as e:
        return {'error': str(e)[:120], 'is_gold_relevant': False}


# Gold-relevance keywords (lower-case). Pre-filter to save Gemini quota.
GOLD_KEYWORDS = frozenset({
    # Direct
    'gold', 'xau', 'bullion', 'precious metal', 'precious metals',
    # Fed / monetary
    'fed', 'fomc', 'federal reserve', 'powell', 'rate cut', 'rate hike',
    'rate decision', 'monetary policy', 'qe', 'quantitative',
    # Yields / treasury
    'yield', 'yields', 'treasury', 'tips', 'real yield', 'real yields',
    'bond', 'bonds',
    # Inflation / data
    'cpi', 'inflation', 'deflation', 'ppi', 'pce',
    'employment', 'nfp', 'non-farm', 'payroll', 'payrolls', 'jobless',
    'unemployment', 'jobs report',
    # Dollar
    'dollar', 'dxy', 'greenback', 'usd',
    # Geopolitics (safe-haven channel)
    'iran', 'israel', 'palestine', 'gaza', 'hamas', 'hezbollah',
    'war', 'conflict', 'military strike', 'missile', 'invasion',
    'china', 'taiwan', 'russia', 'ukraine', 'putin', 'xi jinping',
    # Other central banks
    'ecb', 'boe', 'boj', 'pboc', 'lagarde', 'bailey',
    # Macro
    'recession', 'crisis', 'safe haven', 'safe-haven', 'risk-off', 'risk off',
    # ETF / positioning
    'gld', 'iau', 'etf flows', 'central bank gold', 'central banks buying',
    # Names sometimes relevant
    'yellen', 'bessent',
})


def is_potentially_gold_relevant(headline, summary):
    """Cheap keyword pre-filter before hitting LLM"""
    text = (headline + ' ' + summary).lower()
    return any(kw in text for kw in GOLD_KEYWORDS)


@st.cache_data(ttl=1800)  # 30dk cache (quota friendly)
def get_gold_impact_analyses(news_signatures):
    """Cached deep analysis with keyword pre-filter to save quota"""
    results = []
    for headline, summary in news_signatures:
        if not is_potentially_gold_relevant(headline, summary):
            # Skip LLM, mark as not-relevant via pre-filter
            results.append({
                'headline': headline,
                'summary': summary,
                'impact': {
                    'is_gold_relevant': False,
                    'direction': 'neutral',
                    'magnitude_usd': 0,
                    'confidence': 1.0,
                    'channel': 'other',
                    'timing': 'already_priced',
                    'reason': 'Pre-filter: no gold-relevant keyword',
                    'pre_filtered': True,
                }
            })
            continue
        impact = analyze_gold_impact(AI, headline, summary)
        results.append({'headline': headline, 'summary': summary, 'impact': impact})
    return results


price, _price_fetched_at = get_price_snapshot()
st.title(f"🥇 XAU/USD Dashboard v43 — ${price:.2f}")
st.caption(f"Update: {datetime.now().strftime('%H:%M:%S')}")

news, analyses, agg = get_news_and_sentiment()
_calendar_snapshot, _calendar_error = economic_calendar(FH_KEY, FRED_KEY)
calendar = _calendar_snapshot["events"]
from official_calendar import legacy_events
news_sig = news_channel(legacy_events(calendar), agg)
from risk_engine import news_reasons as _calendar_reasons, load_config as _calendar_config
_current_news_reasons = _calendar_reasons(_calendar_snapshot, datetime.now(timezone.utc), False, _calendar_config())
if any(r.startswith('NEWS_TIME_UNKNOWN:') for r in _current_news_reasons):
    news_sig = ('RED', 'Haber saati doğrulanmadı: gün boyu temkinli blok')
if _calendar_error:
    news_sig = ('RED', 'Takvim: ' + _calendar_error)

from ews_bridge import jump_channel, cot_channel, gvz_channel
channels = {
    'JUMP': jump_channel(get_ohlc('5min', 320)),
    'COT':  cot_channel(),
    'GVZ':  gvz_channel(),
    'NEWS': news_sig,
}
overall, msg = combined_signal(channels)

cols = st.columns(5)
emoji_map = {'GREEN': '🟢', 'YELLOW': '🟡', 'RED': '🔴'}
_karar_slot = cols[0].empty()   # v36: master-gate hesaplandiktan sonra DOLDURULUR
_karar_slot.metric("KARAR", f"{emoji_map[overall]} {overall}", msg)  # gecici (yalniz kanal sinyali)
for i, (name, (color, reason)) in enumerate(channels.items(), start=1):
    cols[i].metric(name, emoji_map[color])
    cols[i].caption(reason)
_master_banner = st.empty()   # v36: otoriter master-gate verdikti (asagida doldurulur)
_forecast_slot = st.empty()   # v38: olasilikli gorunum (aralik+kirilma) — EN SONDA tum veriyle doldurulur

# === TREND FILTRE ===
st.divider()
st.subheader("🧭 Trend Filtre — Yön Kapısı (D1)")
_df_d1_120 = get_ohlc("1day", 120)
_tf = compute_trend_gate(_df_d1_120)
_m = _tf.get('metrics', {})
tf_cols = st.columns(4)
_pvs = _m.get('price_vs_sma50')
_ret = _m.get('ret_5d')
_h = _m.get('hurst')
tf_cols[0].metric("Fiyat vs SMA-50",
                  f"{_pvs:+.1f}%" if _pvs is not None else "—",
                  "downtrend" if (_pvs is not None and _pvs < -1) else
                  "uptrend" if (_pvs is not None and _pvs > 1) else "neutral")
tf_cols[1].metric("5-gun getirisi",
                  f"{_ret:+.1f}%" if _ret is not None else "—")
tf_cols[2].metric("Hurst",
                  f"{_h:.2f}" if _h is not None else "—")
tf_cols[3].metric("Yon",
                  "✅ Iki" if _tf['long_allowed'] and _tf['short_allowed']
                  else "LONG" if _tf['long_allowed']
                  else "SHORT" if _tf['short_allowed']
                  else "🚫 YOK")

if not _tf['long_allowed'] and not _tf['short_allowed']:
    st.error(f"🚫 **IKI YON DE BLOKLU** — {_tf['reason']}")
elif not _tf['long_allowed']:
    st.warning(f"⚠️ **LONG BLOKLU** — {_tf['reason']}")
elif not _tf['short_allowed']:
    st.warning(f"⚠️ **SHORT BLOKLU** — {_tf['reason']}")
else:
    st.info(f"ℹ️ {_tf['reason']}")

st.divider()

# === MAKRO BIAS (D1) — reel getiri + dolar + petrol (yalniz sikilastirir) ===
st.subheader("🌍 Makro Bias (D1) — Gold Rüzgârı")
st.caption("Reel getiri (FRED DFII10) + Broad USD (DTWEXBGS) + Petrol (WTI) | yalnız SIKILAŞTIRIR, sinyal ÜRETMEZ")
_ry = fred_series("DFII10")
_usd = fred_series("DTWEXBGS")
_oil = fred_series("DCOILWTICO")
_gvz_color = channels['GVZ'][0] if isinstance(channels.get('GVZ'), tuple) else 'GREEN'
_gvz_high = _gvz_color in ('RED', 'YELLOW')
_macro = compute_macro_bias(_ry, _usd, _oil, _gvz_high)
_mf = _macro['factors']

mb_cols = st.columns(4)
mb_cols[0].metric(
    "Reel getiri 5g",
    f"{_mf['ry_bp']:+.0f}bp" if _mf['ry_bp'] is not None else "—",
    "headwind" if (_mf['ry_bp'] is not None and _mf['ry_bp'] >= MACRO_RY_BP)
    else "tailwind" if (_mf['ry_bp'] is not None and _mf['ry_bp'] <= -MACRO_RY_BP) else "nötr")
mb_cols[1].metric(
    "USD (broad) 5g",
    f"{_mf['usd_pct']:+.1f}%" if _mf['usd_pct'] is not None else "—")
mb_cols[2].metric(
    "Petrol percentile",
    f"{_mf['oil_pctl']:.0f}p" if _mf['oil_pctl'] is not None else "—",
    "supply-shock" if (_mf['oil_pctl'] is not None and _mf['oil_pctl'] >= MACRO_OIL_PCTL) else "")
mb_cols[3].metric(
    "Gold Bias",
    {'HEADWIND': '🔴 HEADWIND', 'TAILWIND': '🟢 TAILWIND', 'NEUTRAL': '⚪ NÖTR'}[_macro['bias']])

if _macro['bias'] == 'HEADWIND':
    st.warning("⚠️ **Gold HEADWIND** (long'a karşı rüzgâr) — " +
               (" | ".join(_macro['why']) if _macro['why'] else "faktör yok"))
elif _macro['bias'] == 'TAILWIND':
    st.info("ℹ️ **Gold TAILWIND** (short'a karşı rüzgâr) — " + " | ".join(_macro['why']))
else:
    if _macro['why']:
        st.caption("Makro: " + " | ".join(_macro['why']))
    else:
        st.caption("Makro: nötr / veri yok" + ("" if FRED_KEY else " (FRED_KEY tanımlı değil)"))

st.divider()

# === HAFTALIK / AYLIK GORUNUM (sentez karti — TAHMIN DEGIL) ===
# Mevcut _tf (trend) + _macro (makro) + _df_d1_120 (volatilite) sentezi. Yeni veri cekmez, yeni trade uretmez.
st.subheader("🗓️ Haftalık / Aylık Görünüm — Sentez")
st.caption("Trend + Makro + Volatilite tek bakışta | KOŞULLU EĞİLİMDİR, fiyat tahmini DEĞİLDİR")
if _df_d1_120 is not None and len(_df_d1_120) >= 21 and price > 0:
    _wk_rets = _df_d1_120['close'].pct_change().dropna()
    _wk_dsig = float(_wk_rets.tail(20).std())      # son 20 gun realized daily vol
    _wk_1s = price * _wk_dsig * (5 ** 0.5)          # 1 hafta ~1sigma ($)
    _wk_1m = price * _wk_dsig * (21 ** 0.5)         # 1 ay ~1sigma ($)
    _wk_1d = price * _wk_dsig                        # 1 gun ~1sigma ($)
    _wk_down = (not _tf['long_allowed']) and _tf['short_allowed']
    _wk_up = (not _tf['short_allowed']) and _tf['long_allowed']
    _wk_mb = _macro['bias']
    if _wk_down and _wk_mb == 'HEADWIND':
        _wk_lean, _wk_box = "↓ AŞAĞI (güçlü)", "error"
    elif _wk_up and _wk_mb == 'TAILWIND':
        _wk_lean, _wk_box = "↑ YUKARI (güçlü)", "success"
    elif _wk_down and _wk_mb == 'TAILWIND':
        _wk_lean, _wk_box = "↔ ÇELİŞKİLİ (trend aşağı / makro yukarı)", "warning"
    elif _wk_up and _wk_mb == 'HEADWIND':
        _wk_lean, _wk_box = "↔ ÇELİŞKİLİ (trend yukarı / makro aşağı)", "warning"
    elif _wk_down:
        _wk_lean, _wk_box = "↓ AŞAĞI (orta)", "warning"
    elif _wk_up:
        _wk_lean, _wk_box = "↑ YUKARI (orta)", "info"
    else:
        _wk_lean, _wk_box = "↔ NÖTR / yatay", "info"
    _wk_sma = _tf.get('metrics', {}).get('sma50')
    # Satir 1: yon egilimi + rejim donus seviyesi
    wk_r1 = st.columns(2)
    wk_r1[0].metric("Yön eğilimi", _wk_lean)
    wk_r1[1].metric("Rejim dönüş seviyesi", f"${_wk_sma:.0f}" if _wk_sma else "—",
                    "SMA50 — kalıcı geçiş eğilimi bozar", delta_color="off")
    # Satir 2: gunluk / haftalik / aylik beklenen bant (±1sigma)
    wk_r2 = st.columns(3)
    wk_r2[0].metric("1 gün ±1σ", f"±${_wk_1d:.0f}",
                    f"${price - _wk_1d:.0f} – ${price + _wk_1d:.0f}", delta_color="off")
    wk_r2[1].metric("1 hafta ±1σ", f"±${_wk_1s:.0f}",
                    f"${price - _wk_1s:.0f} – ${price + _wk_1s:.0f}", delta_color="off")
    wk_r2[2].metric("1 ay ±1σ", f"±${_wk_1m:.0f}",
                    f"${price - _wk_1m:.0f} – ${price + _wk_1m:.0f}", delta_color="off")
    _wk_flip = ""
    if _wk_sma:
        if _wk_down:
            _wk_flip = f" \\${_wk_sma:.0f} **üstüne** kalıcı çıkış aşağı eğilimi bozar."
        elif _wk_up:
            _wk_flip = f" \\${_wk_sma:.0f} **altına** kalıcı iniş yukarı eğilimi bozar."
    _wk_msg = (f"**Bugün:** ~±\\${_wk_1d:.0f} (\\${price - _wk_1d:.0f}–\\${price + _wk_1d:.0f}) | "
               f"**bu hafta:** \\${price - _wk_1s:.0f}–\\${price + _wk_1s:.0f} | "
               f"eğilim **{_wk_lean}** | makro **{_wk_mb}**.{_wk_flip}")
    getattr(st, _wk_box)(_wk_msg)
    st.caption("⚠️ Aralık = son 20 gün realized volatiliteden (±1σ ≈ %68 olasılık, drift'siz simetrik). "
               "Yön = trend + makro eğilimi, NOKTA FİYAT TAHMİNİ DEĞİLDİR.")
else:
    st.info("Haftalık görünüm için yetersiz D1 verisi")

st.divider()

# === GOLD IMPACT ANALYZER (P3) ===
st.subheader("🎯 Gold Impact Analyzer (LLM)")
_news_sigs = tuple((n.get('headline', ''), n.get('summary', '')[:300]) for n in news[:5])
_gold_impacts = get_gold_impact_analyses(_news_sigs) if _news_sigs else []

_suppression_score = 0.0
_immediate_bearish = 0.0
_immediate_bullish = 0.0
_relevant_titles = []
for _r in _gold_impacts:
    _imp = _r.get('impact', {})
    if not _imp.get('is_gold_relevant'):
        continue
    _relevant_titles.append(_r.get('headline', ''))
    if _imp.get('timing') == 'immediate':
        try:
            _mag = abs(float(_imp.get('magnitude_usd', 0) or 0))
            _conf = float(_imp.get('confidence', 0) or 0)
            _weighted = _mag * _conf
            _suppression_score += _weighted
            if _imp.get('direction') == 'bearish':
                _immediate_bearish += _weighted
            elif _imp.get('direction') == 'bullish':
                _immediate_bullish += _weighted
        except Exception:
            pass

_relevant_count = len(_relevant_titles)
_imp_cols = st.columns(4)
_imp_cols[0].metric("Gold-relevant", f"{_relevant_count}/5")
_imp_cols[1].metric("Suppression score", f"${_suppression_score:.0f}/oz")
_imp_cols[2].metric("Bearish risk", f"${_immediate_bearish:.0f}")
_imp_cols[3].metric("Bullish risk", f"${_immediate_bullish:.0f}")

# Suppression gate
_suppression_active = _suppression_score > 5.0
_suppression_warn = _suppression_score > 2.0
if _suppression_active:
    _net_dir = "BEARISH" if _immediate_bearish > _immediate_bullish else "BULLISH"
    st.error(f"🚫 **SUPPRESSION AKTIF** — Beklenen impact ${_suppression_score:.0f}/oz ({_net_dir}). MR fade RISKLI.")
elif _suppression_warn:
    st.warning(f"⚠️ Dikkat — Beklenen impact ${_suppression_score:.0f}/oz. Pozisyonu kucult veya bekle.")
else:
    st.success(f"✅ Suppression yok — beklenen immediate impact dusuk")

# K2 — TAKVIM ENTEGRASYONU: impact analyzer'a kadar SADECE haber basliklari giriyordu;
# planli olaylar (NFP/CPI/FOMC) yapisal olarak gorunmezdi (NFP gunu yesil cikiyordu).
# Bugun yuksek-etkili takvim olayi varsa suppression'i ZORLA aktif et -> confluence + V10 panelleri de bloke eder.
# v36 TIER'LEME: tier-1 blackout (NFP/CPI/PCE/FOMC/Powell/core inflation) -> SERT blok + suppression.
#               generic 'high' (Existing Home Sales vb.) -> yumusak dikkat (master-gate'te soft).
_BLACKOUT_KEYS = ('nonfarm', 'payroll', 'cpi', 'consumer price', 'core inflation',
                  'pce', 'fomc', 'interest rate decision', 'rate decision', 'powell', 'fed funds')
_today_utc = datetime.utcnow().strftime("%Y-%m-%d")
_cal_high_today = []
for _e in (calendar or []):
    if _e.get('impact') == 'high':
        _et = str(_e.get('time', ''))
        if (_et[:10] == _today_utc) or (_et[:10] == ''):   # bugun veya tarihsiz -> dikkate al
            _cal_high_today.append(_e)
_cal_blackout_today = [_e for _e in _cal_high_today
                       if any(_k in str(_e.get('event', '')).lower() for _k in _BLACKOUT_KEYS)]
_cal_high_names = ", ".join(str(_e.get('event', '?'))[:24] for _e in _cal_high_today[:3])
_cal_blackout_names = ", ".join(str(_e.get('event', '?'))[:24] for _e in _cal_blackout_today[:3])
if _cal_blackout_today:
    _suppression_active = True
    if '_net_dir' not in dir():
        _net_dir = "EVENT-RISK"
    st.error(f"🚫 **TAKVIM BLACKOUT — MACRO RED** · Bugün: {_cal_blackout_names} · "
             f"Tier-1 olay (NFP/CPI/PCE/FOMC). Olay geçene kadar fade AÇMA.")
elif _cal_high_today:
    st.warning(f"⚠️ Takvim: bugün yüksek-etkili olay(lar) — {_cal_high_names}. "
               f"Tier-1 (NFP/CPI/FOMC) DEĞİL; pozisyonu küçült/dikkatli ol.")

# ============================================================
# v36 — MASTER TRADEABLE GATE  (tek dogruluk kaynagi)
# Trend + Makro + Suppression + Takvim(tier'li) -> tek verdikt.
# TUM aksiyon yuzeyleri (ust KARAR rozeti, master banner, LIMIT plan) buna uyar.
# ============================================================
_now_utc = datetime.utcnow()
_mg_hard = []   # SERT blok: hic fade yok
_mg_soft = []   # YUMUSAK: bekle / kucult / dikkat
if _cal_blackout_today:
    _mg_hard.append(f"Takvim blackout: {_cal_blackout_names}")
if _suppression_active and not _cal_blackout_today:
    _mg_hard.append("Suppression aktif (haber etkisi)")
if (not _tf['long_allowed']) and (not _tf['short_allowed']):
    _mg_hard.append("Trend: iki yon de bloklu")
if not _in_fade_session(_now_utc.hour):
    _mg_soft.append(f"Seans disi ({_now_utc.hour:02d}:00 UTC; fade 12-16 UTC)")
if _now_utc.weekday() == 0:
    _mg_soft.append("Pazartesi: fade yasak")
if _cal_high_today and not _cal_blackout_today:
    _mg_soft.append(f"Takvim yuksek-etki ({_cal_high_names}) — kucult/bekle")
if _macro['bias'] == 'HEADWIND':
    _mg_soft.append("Makro HEADWIND (long'a karsi)")
elif _macro['bias'] == 'TAILWIND':
    _mg_soft.append("Makro TAILWIND (short'a karsi)")
# Izinli yonler (trend + makro suzgecinden gecen)
_mg_sides = []
if _tf['long_allowed'] and _macro['bias'] != 'HEADWIND':
    _mg_sides.append('LONG')
if _tf['short_allowed'] and _macro['bias'] != 'TAILWIND':
    _mg_sides.append('SHORT')

if _mg_hard:
    _mg_level = 'RED'
elif _mg_soft or not _mg_sides:
    _mg_level = 'YELLOW'
else:
    _mg_level = 'GREEN'
_mg_tradeable = (not _mg_hard) and bool(_mg_sides) and _in_fade_session(_now_utc.hour) and _now_utc.weekday() != 0

# v43 only tightens the legacy result. Never clears an existing veto.
try:
    _risk_h1 = get_ohlc('1h', 50)
    _risk_obstacles = None
    if _df_d1_120 is not None and len(_df_d1_120) >= 2 and _risk_h1 is not None and len(_risk_h1) >= 2:
        _risk_obstacles = [m['price'] for m in compute_magnet_levels(_df_d1_120, _risk_h1, price)]
        for _risk_df in (_df_d1_120, _risk_h1):
            _risk_prev = _risk_df.iloc[-2]
            _risk_obstacles.extend(camarilla_levels(_risk_prev['high'], _risk_prev['low'], _risk_prev['close']).values())
    _v43_result = run_risk_panel({
        'tradeable': _mg_tradeable, 'allowed_sides': list(_mg_sides),
        'hard_reasons': list(_mg_hard), 'suppression': _suppression_active,
        'channels': channels, 'obstacles': _risk_obstacles,
    }, local_data={
        'm1_frame': get_ohlc('1min', 100), 'm5_frame': get_ohlc('5min', 320),
        'calendar': _calendar_snapshot, 'obstacles': _risk_obstacles,
        'price': price, 'sides': list(_mg_sides),
        'regime': _tf.get('metrics', {}).get('hurst'),
        'macro_bias': _macro['bias'], 'errors': dict(_feed_errors),
        'calendar_error': _calendar_error,
    })
except Exception:
    _v43_result = {'tradeable': False, 'reasons': ['V43_CONFIG_OR_ADAPTER_ERROR']}
    st.error('WHY NOT TRADE — v43 ayar/veri katmanı yüklenemedi')
if not _v43_result['tradeable']:
    _mg_hard.extend(_v43_result['reasons'])
    _mg_level = 'RED'
    _mg_tradeable = False

if _v43_result['tradeable']:
    _mg_sides = [side for side in _mg_sides if side == _v43_result['side']]

# Chart analysis is useful with existing candle feeds. Execution gate remains strict.
from risk_panel import compact_execution_reasons
_analysis = _v43_result.get('analysis', {'status':'VERİ EKSİK','side':None,'reasons':['Analiz katmanı yüklenemedi']})
_karar_slot.markdown('**ANALİZ**\n\n**' + (_analysis['side'] or 'YÖN YOK') + '**\n\n' + _analysis['status'])
with _master_banner.container():
    st.info('**GRAFİK ANALİZİ — ' + _analysis['status'] + '** · Yön: ' + (_analysis['side'] or 'belirlenmedi') +
            ' · ' + (' · '.join(explain(r) for r in _analysis['reasons']) or 'Mevcut mum, seviye ve haber kontrollerinde engel yok; işlem onayı değildir.'))
    _execution_reasons = compact_execution_reasons(_mg_hard)
    if not _mg_tradeable:
        st.warning('**MASTER GATE — TRADE BLOKLU / İŞLEM ONAYI YOK** · ' +
                   ('Spread ve anlık DXY/2Y/10Y kontrolleri tamamlanamadı. ' if not _v43_result.get('data_complete') else '') +
                   'Grafik analizinden ayrı değerlendirilir.')
    else:
        st.success('**MASTER GATE — yalnız paper adayı onaylandı**')
    with st.expander('İşlem onayı: eksik veriler ve tüm engeller', expanded=False):
        for _reason in _execution_reasons:
            st.write('• ' + explain(_reason))
        for _note in _mg_soft:
            st.caption(_note)
    st.caption('JUMP/COT/GVZ/NEWS yeşil olması, fiyat-spread ve çapraz piyasa kontrollerinin tamamlandığı anlamına gelmez.')

from data_access import render_data_access
render_data_access(TD_KEY, FRED_KEY)

# Detail expander - HER haberin LLM analizini goster (seffaflik)
if _gold_impacts:
    with st.expander(f"📑 LLM analiz detayi (5 haber, {_relevant_count} relevant)", expanded=False):
        for _idx, _r in enumerate(_gold_impacts, 1):
            _imp = _r.get('impact', {})
            _err = _imp.get('error')
            _rel = _imp.get('is_gold_relevant', False)
            _pf = _imp.get('pre_filtered', False)
            if _rel:
                _rel_icon = "✅ RELEVANT"
            elif _pf:
                _rel_icon = "⚡ pre-filter skip"
            else:
                _rel_icon = "⚪ LLM: not-relevant"
            _dc = {'bullish': '📈 bullish', 'bearish': '📉 bearish', 'neutral': '➡️ neutral'}.get(_imp.get('direction', ''), '? ?')
            _tc = {'immediate': '⚡ immediate', 'delayed': '⏰ delayed', 'already_priced': '✅ already-priced'}.get(_imp.get('timing', ''), '? ?')

            st.markdown(f"**{_idx}. {_r.get('headline', '')[:100]}**")

            if _err:
                st.error(f"LLM hata: {_err}")
            else:
                _c1, _c2 = st.columns([1, 3])
                _c1.markdown(f"**{_rel_icon}**")
                _mag = _imp.get('magnitude_usd', 0)
                _conf = float(_imp.get('confidence', 0) or 0)
                _c2.markdown(
                    f"{_dc} | "
                    f"Magnitude: **${_mag}/oz** | "
                    f"Confidence: **{_conf:.0%}** | "
                    f"Channel: **{_imp.get('channel', '?')}** | "
                    f"{_tc}"
                )
                _reason = _imp.get('reason', '')
                if _reason:
                    st.caption(f"💡 {_reason}")

            if _idx < len(_gold_impacts):
                st.divider()

# Send telegram alert if suppression high
if TG_TOKEN and TG_CHAT_ID and _suppression_active:
    notify_gold_impact_alert(TG_TOKEN, TG_CHAT_ID, {
        'suppression_score': _suppression_score,
        'immediate_bearish': _immediate_bearish,
        'immediate_bullish': _immediate_bullish,
        'titles': _relevant_titles,
    })

st.divider()

st.subheader("🗺️ S/R Güç Haritası (D1) — Doğrulanmış Pivotlar + Friction")
st.caption("Bootstrap-doğrulanmış D1 swing pivotları (🟢, +2.7→3.5pp bounce lift) + yuvarlak "
           "friction bölgeleri (🟡). RISK/CONTEXT katmanı — trade sinyali DEĞİL. "
           "Detay: research/sr_validation_results.md")
_render_sr_map(price)

st.divider()

st.subheader("📊 Multi-TF Camarilla")
lvls_d1 = None
lvls_h4 = None
lvls_h1 = None
signals_per_tf = {}
cam_cols = st.columns(3)
configs = [(cam_cols[0], "1day", "D1 (ana sinyal)"),
           (cam_cols[1], "4h", "H4 (confluence)"),
           (cam_cols[2], "1h", "H1 (mikro)")]

for col, tf, label in configs:
    with col:
        st.markdown(f"**{label}**")
        df = get_ohlc(tf, size=7 if tf == "1day" else 3)
        if df is not None and len(df) >= 2:
            if tf == "1day":
                today = datetime.utcnow().date()
                prev = None
                for i in range(len(df) - 1, -1, -1):
                    b = df.iloc[i]
                    if b['datetime'].date() != today and (b['high'] - b['low']) >= 5:
                        prev = b
                        break
                if prev is None:
                    prev = df.iloc[-2]
            else:
                prev = df.iloc[-2]
            cam = camarilla_levels(prev['high'], prev['low'], prev['close'])
            if tf == "1day":
                lvls_d1 = cam
            elif tf == "4h":
                lvls_h4 = cam
            elif tf == "1h":
                lvls_h1 = cam
            _render_cam_table(cam, price, height=350)
            atr = (df['high'] - df['low']).rolling(min(14, len(df))).mean().iloc[-1]
            sig = trade_signal(price, cam, atr if pd.notna(atr) else 30)
            signals_per_tf[tf] = sig
            if sig:
                st.caption(f"📍 {sig['side']} aday @ ${sig['entry']:.2f}")
        else:
            st.warning("Veri alinamadi")

# === v36.1 — EK CONFLUENCE MERCEKLERI (H12/H6/H2) — YALNIZ GOSTERIM ===
# Trade karari DEGISMEDI: confluence_signal yine SADECE D1+H4+H1 kullanir.
# Bunlar "kac TF ayni yone bakiyor" teyit lensleridir; 5-trade/gun ureteci DEGIL.
st.caption("➕ Ek confluence mercekleri (yalniz gosterim — trade sinyali yine D1+H4+H1'den gelir):")
_extra_cols = st.columns(3)
_extra_cfg = [(_extra_cols[0], "12h", "H12"),
              (_extra_cols[1], "6h", "H6"),
              (_extra_cols[2], "2h", "H2")]
_df_1h_multi = get_ohlc("1h", 50)   # v36.2: cache'li, veto/v10 ile AYNI cagri -> ek API yok (rate-limit fix)
_signals_extra = {}
_extra_cams = []
for _col, _rule, _lab in _extra_cfg:
    with _col:
        st.markdown(f"**{_lab}**")
        _dfx = _resample_ohlc(_df_1h_multi, _rule)
        if _dfx is not None and len(_dfx) >= 2:
            _prevx = _dfx.iloc[-2]
            _camx = camarilla_levels(_prevx['high'], _prevx['low'], _prevx['close'])
            _render_cam_table(_camx, price, height=300)
            _extra_cams.append(_camx)
            _atrx = (_dfx['high'] - _dfx['low']).rolling(min(14, len(_dfx))).mean().iloc[-1]
            _sigx = trade_signal(price, _camx, _atrx if pd.notna(_atrx) else 30)
            _signals_extra[_lab] = _sigx
            st.caption(f"📍 {_sigx['side']} aday @ ${_sigx['entry']:.2f}" if _sigx else "— seviye disi")
        else:
            _signals_extra[_lab] = None
            st.warning("Veri yok")

# === v37 — ORTALAMA CAMARILLA (tum TF blended) — kozmetik/gosterim ===
_all_cams = [c for c in ([lvls_d1, lvls_h4, lvls_h1] + _extra_cams) if c]
if _all_cams:
    st.markdown(f"**📊 Ortalama Camarilla — {len(_all_cams)} TF blended** (R yeşil / S kırmızı / NOW sarı)")
    _avg_cam = {k: sum(c[k] for c in _all_cams) / len(_all_cams)
                for k in ['R4', 'R3', 'R2', 'R1', 'PP', 'S1', 'S2', 'S3', 'S4']}
    _avg_mid = st.columns([1, 2, 1])[1]
    with _avg_mid:
        _render_cam_table(_avg_cam, price, height=350)

# === MULTI-TF CONFLUENCE KARARI ===
final = confluence_signal(
    signals_per_tf.get("1day"),
    signals_per_tf.get("4h"),
    signals_per_tf.get("1h"),
)

# v36.1 — CONFLUENCE SAYACI (yalniz bilgi): kac TF final yone katiliyor (6 TF uzerinden)
_all_tf_sigs = {'D1': signals_per_tf.get("1day"), 'H4': signals_per_tf.get("4h"),
                'H1': signals_per_tf.get("1h"), **_signals_extra}
if final.get('side'):
    _agree = [k for k, s in _all_tf_sigs.items() if s and s.get('side') == final['side']]
    st.caption(f"🔭 Confluence: **{len(_agree)}/6** TF '{final['side']}' yönünde "
               f"({', '.join(_agree) if _agree else 'yok'}) · NOT: trade kararı yine D1+H4+H1; "
               f"bu sayı yalnız teyit bilgisidir, sinyal üretmez.")

# Trend filter + Suppression veto check on confluence signal
_trend_veto = "" if _mg_tradeable else " | MASTER GATE: " + " · ".join(_mg_hard)
if final.get('side') == 'LONG' and not _tf['long_allowed']:
    _trend_veto = " | 🚫 TREND VETO: LONG bloklu"
elif final.get('side') == 'SHORT' and not _tf['short_allowed']:
    _trend_veto = " | 🚫 TREND VETO: SHORT bloklu"
if _suppression_active:
    _trend_veto += f" | 🚫 SUPPRESSION: ${_suppression_score:.0f}/oz beklenen impact"
# Makro bias veto: sadece niyetle TERS makro -> veto (yalniz sikilastirir, yeni sinyal uretmez)
if final.get('side') == 'LONG' and _macro['bias'] == 'HEADWIND':
    _trend_veto += " | 🚫 MAKRO VETO: gold headwind (long'a karsi)"
elif final.get('side') == 'SHORT' and _macro['bias'] == 'TAILWIND':
    _trend_veto += " | 🚫 MAKRO VETO: gold tailwind (short'a karsi)"

if final.get('side') and not candidate_approved(final['side'], final.get('entry'), _v43_result):
    _trend_veto += ' | v43: bu yön/giriş seviyesi için onaylı setup yok'

# === V29 EK: SESSION GATE (12-16 UTC) -- confluence path, backtest-valide (her yil pozitif, PF 1.8-5.0) ===
# Sadece GOSTERIM gate'i. trade_signal / confluence_signal / V10 / EWS / magnet DEGISMEDI.
_utc_hour_sess = datetime.utcnow().hour
_session_ok = (12 <= _utc_hour_sess < 16)
_session_note = "" if _session_ok else f" | SESSION DISI ({_utc_hour_sess:02d}:00 UTC) - fade penceresi 12-16 UTC"

if final['level'] in ('VERY_STRONG', 'STRONG'):
    if overall == 'GREEN' and not _trend_veto and _session_ok:
        st.success(f"{final['emoji']} **{final['level']}** — {final['reason']} | "
                   f"Entry ${final['entry']:.2f} | TP ${final['tp']:.2f} | SL ${final['sl']:.2f}")
    elif not _session_ok:
        st.warning(f"{final['emoji']} {final['level']} sinyal var AMA{_session_note}")
    elif _trend_veto:
        st.error(f"{final['emoji']} {final['level']} sinyal var AMA TREND BLOKLU{_trend_veto}")
    else:
        st.warning(f"{final['emoji']} {final['level']} sinyal var ama trafik {overall}: {final['reason']}")
elif final['level'] == 'OK':
    if not _session_ok:
        st.warning(f"{final['emoji']} OK sinyal var AMA{_session_note}")
    elif _trend_veto:
        st.error(f"{final['emoji']} OK sinyal var AMA TREND BLOKLU{_trend_veto}")
    else:
        st.info(f"{final['emoji']} **{final['level']}** — {final['reason']} | "
                f"Entry ${final['entry']:.2f} | TP ${final['tp']:.2f} | SL ${final['sl']:.2f}")
elif final['level'] == 'WAIT':
    st.warning(f"{final['emoji']} **BEKLE** — {final['reason']}")
else:
    st.caption(f"{final['emoji']} {final['reason']}")

# === SABAH PLAN (Telegram, gunde 1 kez) ===
if TG_TOKEN and TG_CHAT_ID and lvls_d1:
    notify_daily_status(TG_TOKEN, TG_CHAT_ID, {
        'capital': CAPITAL,
        'price': price,
        'd1_levels': lvls_d1,
    })

# === SAATLIK OZET (Telegram, 1h cooldown) ===
if TG_TOKEN and TG_CHAT_ID:
    notify_hourly_summary(TG_TOKEN, TG_CHAT_ID, {
        'price': price,
        'overall': overall,
        'channels': channels, 'obstacles': _risk_obstacles,
        'd1': lvls_d1,
        'h4': lvls_h4,
        'h1': lvls_h1,
        'final_level': final.get('level', 'NONE'),
        'final_reason': final.get('reason', ''),
        'trend': _tf,
        'suppression': {
            'score': _suppression_score,
            'bearish': _immediate_bearish,
            'bullish': _immediate_bullish,
            'relevant_count': _relevant_count,
        },
    })

# ============================================================
# V10 PANEL — F5 + V6 risk + V10 sinyal + pozisyon tracker
# ============================================================
st.divider()
st.subheader("🎯 V10 — Disiplinli Fade + Breakout Protector")
st.caption("F5 trend (SMA200+slope+DD) → range modu | V6 risk skoru → fade güvenliği | TP=\\$10, SL=\\$30 sabit")

# F5 hesabı için 250 D1 bar lazım (210 minimum)
_df_d1_v10 = get_ohlc("1day", 250)
_f5_bull, _f5_reasons = compute_f5_state(_df_d1_v10)
_v10_risk, _v10_risk_reasons = compute_v6_risk_score(_df_d1_v10)

# Pozisyon tracker (manuel - MT5 live yok)
if 'v10_positions' not in st.session_state:
    st.session_state.v10_positions = []
_n_open_fades = sum(1 for p in st.session_state.v10_positions if p.get('type') == 'fade')

# V10 sinyal (lvls_d1 yukarıda hesaplandı)
_utc_hour = datetime.utcnow().hour
_v10_sig = detect_v10_signal(price, lvls_d1, _f5_bull, _v10_risk, _utc_hour, _n_open_fades)

# V34 — kirilim vetosu hesabi (get_ohlc cache'li, ekstra API maliyeti yok; H1 yoksa PAS)
_df_h1_veto = get_ohlc("1h", 50)
_veto_active, _veto_er, _veto_reason = compute_breakout_veto(_df_h1_veto)

# Üst 4 metrik
v10_cols = st.columns(4)
_v10_data_ok = _df_d1_v10 is not None and len(_df_d1_v10) >= 210
v10_cols[0].metric("F5 Modu",
                   "Veri eksik" if not _v10_data_ok else "🟢 RANGE (Fade)" if not _f5_bull else "🟡 BULL (Breakout)")
_risk_text = {0: "🟢 0/4 GÜVENLİ", 1: "🟡 1/4 DİKKAT"}.get(_v10_risk, f"🔴 {_v10_risk}/4 RİSKLİ")
v10_cols[1].metric("V6 Risk", _risk_text if _v10_data_ok else "Hesaplanamıyor")
_ist_hour = (_utc_hour + 3) % 24
v10_cols[2].metric("Saat UTC/IST", f"{_utc_hour:02d}:00 / {_ist_hour:02d}:00",
                   "🟢 fade serbest (filtre kapalı)" if not FADE_SESSION_GATE
                   else ("🟢 fade saati" if 12 <= _utc_hour < 16 else "🔴 riskli"))
v10_cols[3].metric("Açık fade", f"{_n_open_fades}/3",
                   "🟢 yer var" if _n_open_fades < 3 else "🔴 limit dolu")

# Sinyal kutusu
if _v10_sig:
    t = _v10_sig['type']
    # K3 — V10 panelini TREND KAPISINA + kirilim vetosuna BAGLA.
    # (v34'e kadar bu panel _tf'i hic kontrol etmiyordu: SHORT trendde bile LONG FADE basabiliyordu.)
    _v10_block = None
    if not _mg_tradeable:
        _v10_block = 'MASTER GATE: ' + ' · '.join(_mg_hard)
    elif not candidate_approved('SHORT' if 'SHORT' in t else 'LONG', _v10_sig.get('entry'), _v43_result):
        _v10_block = 'v43: bu yön/giriş seviyesi için onaylı setup yok'
    elif t == 'FADE_LONG' and not _tf['long_allowed']:
        _v10_block = f"LONG bloklu — {_tf['reason']}"
    elif t == 'FADE_SHORT' and not _tf['short_allowed']:
        _v10_block = f"SHORT bloklu — {_tf['reason']}"
    elif t in ('FADE_LONG', 'FADE_SHORT') and _veto_active:
        _v10_block = f"kırılım vetosu aktif — {_veto_reason}"
    elif t in ('FADE_LONG', 'FADE_SHORT') and _suppression_active:
        _v10_block = "macro/suppression aktif — planlı olay veya haber riski"

    if _v10_block:
        st.error(f"🚫 **V10 {t} SİNYALİ var AMA AÇMA** — {_v10_block}")
    elif t == 'FADE_SHORT':
        st.error(f"🔴 **V10 SHORT FADE SİNYALİ** | Entry {_v10_sig['entry']:.2f} USD | "
                 f"TP {_v10_sig['tp']:.2f} (+10 USD) | SL {_v10_sig['sl']:.2f} (-30 USD) | {_v10_sig['note']}")
    elif t == 'FADE_LONG':
        st.success(f"🟢 **V10 LONG FADE SİNYALİ** | Entry {_v10_sig['entry']:.2f} USD | "
                   f"TP {_v10_sig['tp']:.2f} (+10 USD) | SL {_v10_sig['sl']:.2f} (-30 USD) | {_v10_sig['note']}")
    elif t == 'BREAKOUT_LONG':
        if not _tf['long_allowed']:
            st.error(f"🚫 **V10 BREAKOUT LONG var AMA AÇMA** — LONG bloklu — {_tf['reason']}")
        else:
            st.success(f"🚀 **V10 BREAKOUT LONG** | Entry {_v10_sig['entry']:.2f} USD | {_v10_sig['note']}")
else:
    if not _mg_tradeable:
        st.warning("V10 girişe kapalı — MASTER GATE koşulları sağlanmadı.")
    elif _f5_bull:
        _r4 = lvls_d1['R4'] if lvls_d1 else None
        if _r4:
            st.info(f"F5 BULL aktif — R4 (${_r4:.2f}) kırılması beklenir (LONG breakout)")
    elif _v10_risk >= 2:
        st.warning(f"⚠️ Risk skoru {_v10_risk}/4 — fade DEVRE DIŞI bugün")
    elif not _in_fade_session(_utc_hour):
        st.warning(f"⚠️ Saat {_utc_hour:02d}:00 UTC — fade saat dışı (12-16 UTC)")
    elif _n_open_fades >= 3:
        st.warning(f"⚠️ {_n_open_fades} fade pozisyon açık (limit 3)")
    else:
        st.info("🟢 V10 hazır — R3/S3 dokunması bekleniyor")

# V34 — KIRILIM VETOSU paneli (sadece gosterir; _v10_sig'i degistirmez)
_is_fade_sig = bool(_v10_sig) and _v10_sig.get('type') in ('FADE_SHORT', 'FADE_LONG')
if _veto_er is None:
    st.caption(f"🚦 Kırılım vetosu (H1): {_veto_reason}")
elif _veto_active and _is_fade_sig:
    st.error(f"🔴 **KIRILIM VETOSU — FADE BLOKE** · {_veto_reason} · Bu fade'i AÇMA, fiyat kırıyor.")
elif _veto_active:
    st.warning(f"🔴 Kırılım modu (H1) · {_veto_reason} · fade sinyali gelirse açma.")
else:
    st.success(f"🟢 Kırılım yok (H1) · {_veto_reason} · giriş için MASTER GATE geçerli.")

# Detay expander'lar
with st.expander(f"F5 detayı (BULL={_f5_bull})"):
    for r in _f5_reasons:
        st.write(r)
with st.expander(f"V6 risk detayı ({_v10_risk}/4)"):
    for r in _v10_risk_reasons:
        st.write(r)

# Pozisyon tracker
st.markdown("**📋 Açık pozisyon tracker** (MT5 live yok — manuel gir)")
with st.form("v10_add_pos", clear_on_submit=True):
    pc = st.columns(7)
    p_type = pc[0].selectbox("Tip", ['fade', 'breakout'])
    p_side = pc[1].selectbox("Yön", ['LONG', 'SHORT'])
    p_entry = pc[2].number_input("Entry", value=float(price), step=0.01, format="%.2f")
    p_tp = pc[3].number_input("TP", value=float(p_entry + 10 if p_side == 'LONG' else p_entry - 10),
                              step=0.01, format="%.2f")
    p_sl = pc[4].number_input("SL", value=float(p_entry - 30 if p_side == 'LONG' else p_entry + 30),
                              step=0.01, format="%.2f")
    if pc[5].form_submit_button("➕ Ekle"):
        st.session_state.v10_positions.append({
            'type': p_type, 'side': p_side, 'entry': p_entry,
            'tp': p_tp, 'sl': p_sl,
            'opened': datetime.now().isoformat(),
        })
        st.rerun()
    if pc[6].form_submit_button("🗑️ Tümünü sil"):
        st.session_state.v10_positions = []
        st.rerun()

if st.session_state.v10_positions:
    _pos_rows = []
    for i, p in enumerate(st.session_state.v10_positions):
        _pnl = (price - p['entry']) if p['side'] == 'LONG' else (p['entry'] - price)
        _pos_rows.append({
            '#': i + 1, 'Tip': p['type'], 'Yön': p['side'],
            'Entry': f"${p['entry']:.2f}",
            'TP': f"${p['tp']:.2f}",
            'SL': f"${p['sl']:.2f}",
            'PnL': f"${_pnl:+.2f}",
        })
    st.dataframe(pd.DataFrame(_pos_rows), hide_index=True, use_container_width=True)
    _v10_warns = validate_v10_positions(st.session_state.v10_positions, price)
    for _w in _v10_warns:
        st.warning(_w)
    if _f5_bull and _n_open_fades > 0:
        st.error(f"🚨 F5 BULL ONAYI — {_n_open_fades} fade pozisyonu KAPAT (breakout modu)")
else:
    st.caption("Açık pozisyon yok")

# V10 Telegram alerts
if TG_TOKEN and TG_CHAT_ID:
    if _v10_sig:
        notify_v10_signal(TG_TOKEN, TG_CHAT_ID, _v10_sig, price, _v10_risk)
    if _f5_bull and _n_open_fades > 0:
        notify_f5_bull_close_fades(TG_TOKEN, TG_CHAT_ID, _n_open_fades)

# ============================================================
# MAGNET LEVELS PANEL — likidite mıknatısları
# ============================================================
st.divider()
st.subheader("🧲 Magnet Seviyeleri — Likidite Mıknatısları")
st.caption("PDH/PDL, round numbers, Asia H/L, Equal H/L — fiyatın çekilebileceği stop hunt seviyeleri")

_df_h1_v10 = get_ohlc("1h", 50)
_magnets = compute_magnet_levels(_df_d1_v10, _df_h1_v10, price)

if _magnets:
    _mag_rows = []
    for m in _magnets:
        direction = "↑" if m['distance'] > 0 else "↓"
        urgency = ""
        if m['distance_abs'] <= 5:
            urgency = "🔴 ÇOK YAKIN"
        elif m['distance_abs'] <= 15:
            urgency = "🟡 yakın"
        _mag_rows.append({
            'Seviye': m['label'],
            'Fiyat': f"${m['price']:.2f}",
            'Mesafe': f"{direction}{m['distance_abs']:.1f}$",
            'Tip': m['type'],
            'Uyarı': urgency,
            'Not': m['note'],
        })
    st.dataframe(pd.DataFrame(_mag_rows), hide_index=True, use_container_width=True)
    
    _mag_warns = nearest_magnet_warning(_magnets, price, threshold=10)
    if _mag_warns:
        st.warning("⚠️ **Fiyat $10 içinde magnet seviyeleri var — hunt riski:**")
        for w in _mag_warns:
            st.write(f"  • {w}")
else:
    st.info("Magnet hesabı için yetersiz veri")

# === LIMIT EMIR PANELI (D1) ===
if lvls_d1:
    st.subheader("LIMIT EMIR PLANI (D1 fade)")
    _lot = "0.01" if CAPITAL < 3000 else "0.02"
    _r3, _s3 = lvls_d1['R3'], lvls_d1['S3']
    _r4, _s4 = lvls_d1['R4'], lvls_d1['S4']
    _rows = []
    # SHORT row - hide if trend blocks
    _short_status = ("🚫 EVENT/MACRO BLOKLU" if _mg_hard
                     else "✅ paper adayı" if candidate_approved('SHORT', _r3, _v43_result) else "⏳ yeni setup değerlendirmesi gerekli")
    _rows.append({"Side": f"SHORT (R3 fade) — {_short_status}",
                  "Entry": f"${_r3:.2f}",
                  "TP": f"${_s3:.2f}", "SL": f"${_r4:.2f}",
                  "Lot": _lot,
                  "RR": f"1:{abs((_s3-_r3)/(_r4-_r3)):.1f}"})
    # LONG row
    _long_status = ("🚫 EVENT/MACRO BLOKLU" if _mg_hard
                    else "✅ paper adayı" if candidate_approved('LONG', _s3, _v43_result) else "⏳ yeni setup değerlendirmesi gerekli")
    _rows.append({"Side": f"LONG (S3 fade) — {_long_status}",
                  "Entry": f"${_s3:.2f}",
                  "TP": f"${_r3:.2f}", "SL": f"${_s4:.2f}",
                  "Lot": _lot,
                  "RR": f"1:{abs((_r3-_s3)/(_s3-_s4)):.1f}"})
    st.dataframe(pd.DataFrame(_rows), hide_index=True, use_container_width=True)

st.divider()

left, right = st.columns([2, 1])
with left:
    st.subheader(f"📰 Haberler ({len(news)})")
    if agg['n'] > 0:
        score = agg['score']
        emoji = "📈" if score > 0.5 else ("📉" if score < -0.5 else "➡️")
        st.info(f"{emoji} Sentiment: **{score:+.2f}** | guven {agg['confidence']:.0%} | n={agg['n']}")
    for i, n in enumerate(news[:5]):
        ai = analyses[i] if i < len(analyses) else None
        with st.expander(n.get('headline', '?')[:90]):
            st.caption(f"{datetime.fromtimestamp(n['datetime']).strftime('%m-%d %H:%M')} | {n.get('source', '?')}")
            st.write(n.get('summary', '')[:300])
            if ai and 'direction' in ai:
                c1, c2, c3 = st.columns(3)
                emoji = {'up': '📈', 'down': '📉', 'neutral': '➡️'}.get(ai['direction'], '?')
                c1.metric("Yon", f"{emoji} {ai['direction']}")
                c2.metric("Buyukluk", ai['magnitude'])
                c3.metric("Guven", f"{ai['confidence']:.0%}")
                st.caption(f"💡 {ai['reason']}")

with right:
    st.subheader("📅 Takvim")
    st.caption('Kaynak: ' + _calendar_snapshot.get('source', 'Doğrulanamadı'))
    st.caption(_calendar_snapshot.get('scope', '') + ' · Saatler UTC')
    if _calendar_snapshot.get('note'):
        st.caption(_calendar_snapshot['note'])
    if calendar:
        cal_df = pd.DataFrame([{
            'Tarih': e.get('time', '?')[:10],
            'Saat (UTC)': 'Saat bilinmiyor' if e.get('time_precision') == 'date' else e.get('time', '?')[11:16],
            'Olay': e.get('event', '?')[:30],
            '!': '🔴' if e.get('impact') == 'high' else '🟡'
        } for e in calendar[:10]])
        st.dataframe(cal_df, hide_index=True, use_container_width=True)
    else:
        st.info("Kayit yok")

st.divider()
st.error("🚫 **YASAK:** Silver | Seviye-disi entry | Asia (00-07 UTC) | Pazartesi | Re-entry | Macro RED")

# ============================================================
# AI DECISION ENGINE — Gemini ile tüm veriyi sentezle
# ============================================================
st.divider()
st.subheader("🤖 AI Karar — Tüm Verilerin Sentezi")
st.caption("Gemini LLM tüm V26+V10+Magnet verilerini okuyup V10 kurallarına göre öneri verir | V10 kuralları AI'yı bağlar")

_ai_col1, _ai_col2 = st.columns([1, 5])
_run_ai = _ai_col1.button("🧠 AI Sor", type="primary", use_container_width=True)
_ai_col2.caption("Manuel tetik | 5dk cooldown | Aynı durumda cache'lenir | Free tier: 20 sorgu/gün")

if _run_ai:
    # Rate limit: son sorgudan 5 dakika geçmiş mi?
    _last_ai_t = st.session_state.get('last_ai_time')
    if _last_ai_t and (datetime.now() - _last_ai_t).total_seconds() < 300:
        _remain = 300 - (datetime.now() - _last_ai_t).total_seconds()
        st.warning(f"⏳ Çok hızlı — son sorgudan {int(_remain)}s sonra tekrar dene (kota koruması)")
        _run_ai = False

if _run_ai:
    try:
        _ai_client = AI  # v26'da en başta tanımlanan global Gemini client
        _state = {
            'time_utc': datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            'price': float(price),
            'utc_hour': datetime.utcnow().hour,
            'v26_karar': overall,
            'v26_channels': {
                _k: (_v[0] if isinstance(_v, tuple) else _v)
                for _k, _v in channels.items()
            },
            'v26_trend': {
                'direction': ('IKI_YON' if _tf['long_allowed'] and _tf['short_allowed']
                              else 'LONG' if _tf['long_allowed']
                              else 'SHORT' if _tf['short_allowed']
                              else 'BLOKLU'),
                'long_allowed': _tf['long_allowed'],
                'short_allowed': _tf['short_allowed'],
                'price_vs_sma50': _m.get('price_vs_sma50'),
                'hurst': _m.get('hurst'),
                'reason': _tf.get('reason', ''),
            },
            'v26_impact': {
                'suppression_score': round(_suppression_score, 1),
                'active': bool(_suppression_active),
                'direction': (_net_dir if '_net_dir' in dir() else None),
            },
            'calendar_high_impact': [
                {'time': _e.get('time', '?'), 'event': _e.get('event', '?'),
                 'impact': _e.get('impact', '?')}
                for _e in (calendar or []) if _e.get('impact') == 'high'
            ][:8],
            'calendar_today_high': bool(_cal_high_today),
            'v10_f5_mode': 'BULL' if _f5_bull else 'RANGE',
            'v10_risk': _v10_risk,
            'open_fades': _n_open_fades,
            'v10_signal': _v10_sig,
            'pivots': lvls_d1 if lvls_d1 else {},
            'magnets': _magnets[:6] if _magnets else [],
            'macro_bias': {'bias': _macro['bias'], 'why': _macro['why']},
        }
        
        # Son trade'lerden özet (pozisyon tracker'dan)
        _history = ""
        if st.session_state.get('v10_positions'):
            _history = f"Şu an {len(st.session_state.v10_positions)} açık pozisyon var:\n"
            for p in st.session_state.v10_positions[-3:]:
                _history += f"- {p['type']} {p['side']} @${p['entry']:.2f} TP=${p['tp']:.2f} SL=${p['sl']:.2f}\n"
        
        # State hash - aynı snapshot için tekrar sorgulama
        _state_key = f"{_state['v10_f5_mode']}_{_state['v10_risk']}_{_state['utc_hour']}_{int(_state['price']/5)*5}_{_state['open_fades']}"
        _cached_key = st.session_state.get('last_ai_state_key')
        if _cached_key == _state_key and 'last_ai_result' in st.session_state:
            st.info("⚡ Aynı durum — cached AI sonucu kullanılıyor (kota korundu)")
        else:
            with st.spinner("LLM düşünüyor..."):
                _groq_key = st.secrets.get("GROQ_KEY", None)
                _ai_result = ai_decide(_ai_client, _state, _history, groq_key=_groq_key)
            st.session_state['last_ai_result'] = _ai_result
            st.session_state['last_ai_time'] = datetime.now()
            st.session_state['last_ai_state_key'] = _state_key
    except Exception as e:
        st.error(f"AI hatası: {e}")

# Son AI sonucunu göster
if 'last_ai_result' in st.session_state:
    _r = st.session_state['last_ai_result']
    _t = st.session_state.get('last_ai_time')
    
    _action = _r.get('action', '?')
    _conf = _r.get('confidence', 0)
    _action_color = {
        'FADE_SHORT': '🔴',
        'FADE_LONG': '🟢',
        'BREAKOUT_LONG': '🚀',
        'WAIT': '⏳',
        'CLOSE_FADES': '🚨',
    }.get(_action, '⚪')
    
    _ai_compliant = _r.get('v10_compliant', True)
    _compliant_str = "✓ V10 uyumlu" if _ai_compliant else "✗ V10 İHLALİ - DİKKAT"
    
    _ai_cols = st.columns(4)
    _ai_cols[0].metric("Aksiyon", f"{_action_color} {_action}")
    _ai_cols[1].metric("Güven", f"{_conf}/10")
    _ai_cols[2].metric("V10 Uyum", _compliant_str)
    _ai_cols[3].metric("Magnet Riski", _r.get('magnet_risk', '?'))
    
    st.info(f"💭 **Gerekçe:** {_r.get('reasoning', '?')}")
    
    if _r.get('warnings'):
        for w in _r['warnings']:
            st.warning(f"⚠️ {w}")
    
    nc = _r.get('next_check', '?')
    if nc and nc != '?':
        st.caption(f"⏰ Sonraki kontrol önerisi: **{nc}**")
    
    if _t:
        _prov = _r.get('_provider', 'gemini')
        _prov_emoji = {'gemini': '🔷 Gemini', 'groq': '⚡ Groq', 'failed': '❌ Hata', 'none': '⚠️ Key yok'}.get(_prov, _prov)
        st.caption(f"AI son sorgu: {_t.strftime('%H:%M:%S')} | Sağlayıcı: {_prov_emoji}")
    
    # V10 ihlal uyarısı (önemli safety net)
    if not _ai_compliant:
        st.error("🚨 AI önerisi V10 kurallarını İHLAL ediyor — AI'yı dinleme, kuralları takip et!")
else:
    st.caption("Henüz AI sorgulanmadı. 'AI Sor' butonuna tıklayın.")


# =====================================================================
# v37 — GELISMIS ANALITIK KATMAN  (baglam/risk; TRADE SINYALI DEGIL)
# TAMAMEN ADDITIVE: mevcut hicbir fonksiyon/degisken/karar DEGISMEDI.
# confluence_signal + V10 + master gate AYNEN calisir; bu panellerin
# HICBIRI trade kararina girmez. Script'in EN SONUNA eklendigi icin,
# bu blok komple hata verse bile ustteki dashboard zaten render olmustur.
# Agir/opsiyonel kutuphaneler try/except: yoksa panel "kurulu degil" der.
# =====================================================================
import math as _math
import re as _msurp_re

# =====================================================================
# v39 — MAKRO SURPRIZ -> ALTIN TEPKISI (event-driven yon sinyali)
# macro_surprise mantigi INLINE (ayri dosya YOK, hepsi bu tek dashboard'da).
# KANIT: 2000 sonrasi altin counter-cyclical; guclu ABD verisi -> reel faiz/USD up -> altin asagi.
# Trade kararina GIRMEZ; event tepki yonu + volatilite uyarisi. Komple try/except.
# =====================================================================
# (sign = POZITIF surprizin altina etkisi: -1 altin asagi, +1 altin yukari)
_MSURP_REACTION = [
    (("nonfarm", "non-farm", "payroll", "nfp"),          -1, 1.00, 60.0,  "En güçlü mover. Güçlü istihdam → altın aşağı."),
    (("unemployment rate",),                              +1, 0.70, 0.10,  "Yüksek işsizlik = zayıf ekonomi → altın yukarı (ters)."),
    (("initial jobless", "initial claims", "jobless"),    +1, 0.40, 15.0,  "Çok başvuru = zayıf → altın yukarı (ters)."),
    (("core pce",),                                       -1, 0.75, 0.10,  "Fed'in tercih ettiği enflasyon. Hot → altın aşağı."),
    (("pce",),                                            -1, 0.65, 0.12,  "Hot → altın aşağı."),
    (("core cpi", "core inflation", "core consumer"),     -1, 0.80, 0.12,  "Modern reel-faiz kanalı: hot → altın aşağı."),
    (("cpi", "consumer price", "inflation rate"),         -1, 0.75, 0.15,  "Hot → altın aşağı (modern kanal negatif)."),
    (("fomc", "rate decision", "fed funds", "interest rate decision", "powell"), -1, 0.90, 0.10, "Hawkish → altın aşağı. Temiz sürpriz nadir."),
    (("retail sales",),                                   -1, 0.55, 0.40,  "Güçlü tüketim → altın aşağı."),
    (("ism", "pmi", "manufacturing"),                     -1, 0.50, 1.50,  "Güçlü aktivite → altın aşağı."),
    (("gdp",),                                            -1, 0.60, 0.50,  "Güçlü büyüme → altın aşağı."),
    (("durable goods",),                                  -1, 0.40, 2.00,  "Güçlü → altın aşağı."),
    (("consumer confidence", "consumer sentiment", "michigan"), -1, 0.40, 3.0, "Güçlü güven → altın aşağı."),
    (("adp",),                                            -1, 0.45, 40.0,  "Özel istihdam öncüsü; güçlü → altın aşağı."),
]
_MSURP_BASE_BETA = 0.20
_MSURP_BAD_MULT = 1.30

def _msurp_match(name):
    if not name:
        return None
    n = str(name).lower()
    for keys, sign, weight, std, note in _MSURP_REACTION:
        if any(k in n for k in keys):
            return dict(sign=sign, weight=weight, std=std, note=note)
    return None

def _msurp_f(x):
    if x is None:
        return None
    try:
        if isinstance(x, str):
            x = _msurp_re.sub(r"[^0-9eE+\-.,]", "", x).replace(",", "")
            if x in ("", "+", "-", "."):
                return None
        return float(x)
    except Exception:
        return None

def _msurp_expected(name, actual, forecast, previous=None):
    m = _msurp_match(name)
    if m is None:
        return None
    a, f = _msurp_f(actual), _msurp_f(forecast)
    if a is None or f is None:
        if a is not None and previous is not None:
            f = _msurp_f(previous)
        if a is None or f is None:
            return dict(status="veri_yok", note=m["note"], event=name)
    z = (a - f) / (m["std"] + 1e-9)
    z = max(-3.0, min(3.0, z))
    g = m["sign"] * z * m["weight"] * _MSURP_BASE_BETA
    if g > 0:
        g *= _MSURP_BAD_MULT
    direction = "ALTIN ↑ (yukarı)" if g > 0.02 else ("ALTIN ↓ (aşağı)" if g < -0.02 else "nötr/zayıf")
    az = abs(z)
    conf = "yüksek" if (az >= 1.5 and m["weight"] >= 0.7) else ("orta" if az >= 0.8 else "düşük")
    return dict(status="ok", event=name, surprise=a - f, z=round(z, 2), gold_pct=round(g, 3),
                direction=direction, confidence=conf, weight=m["weight"], note=m["note"])

def _msurp_scan(cal):
    results = []; has = False
    EST = ("estimate", "forecast", "consensus", "survey", "expected")
    ACT = ("actual", "value", "result"); PRV = ("prev", "previous", "prior")
    for e in (cal or []):
        if not isinstance(e, dict):
            continue
        name = e.get("event") or e.get("name") or e.get("title") or ""
        est = next((e[k] for k in EST if k in e and e[k] not in (None, "")), None)
        act = next((e[k] for k in ACT if k in e and e[k] not in (None, "")), None)
        prv = next((e[k] for k in PRV if k in e and e[k] not in (None, "")), None)
        if est is not None or act is not None:
            has = True
        r = _msurp_expected(name, act, est, prv)
        if r is not None:
            r["time"] = e.get("time") or e.get("date") or ""
            r["impact"] = e.get("impact", ""); r["actual"] = act; r["estimate"] = est; r["previous"] = prv
            results.append(r)
    return results, has

def _msurp_fmp(api_key, days_ahead=2):
    from datetime import datetime as _dt, timedelta as _td
    d0 = _dt.utcnow().date(); d1 = d0 + _td(days=days_ahead)
    url = ("https://financialmodelingprep.com/api/v3/economic_calendar"
           f"?from={d0}&to={d1}&apikey={api_key}")
    rr = requests.get(url, timeout=20); rr.raise_for_status()
    out = []
    for e in rr.json():
        if str(e.get("country", "")).upper() not in ("US", "USA", "UNITED STATES"):
            continue
        out.append({"event": e.get("event", ""), "time": e.get("date", ""),
                    "estimate": e.get("estimate"), "actual": e.get("actual"),
                    "previous": e.get("previous"), "impact": e.get("impact", "")})
    return out

# ---- v39: JEOPOLITIK SAFE-HAVEN tarayici (haber escalation/de-escalation -> altin yonu) ----
# KANIT: jeopolitik gerginlik -> safe-haven talebi -> altin YUKARI; de-escalation -> prim cozulur -> altin ASAGI
# (Baur-Smales 2020: alt/gumus uc gerginlikte safe-haven; etki rejim-bagimli + cogunlukla volatilite).
_GEO_RELEVANT = ("iran", "israel", "hormuz", "strait", "gaza", "ukraine", "russia", "houthi",
                 "war", "military", "missile", "nuclear", "sanction", "conflict", "attack",
                 "strike", "tariff", "trade war", "middle east", "opec", "geopolit", "tehran",
                 "netanyahu", "hezbollah", "hamas", "syria", "lebanon")
_GEO_ESC = ("attack", "strike", "invade", "escalat", "missile", "killed", "bomb", "threat",
            "retaliat", "erupt", "launch", "imposes sanction", "new sanction", "tension",
            "raid", "assault", "offensive", "warns", "clash", "kills", "fires", "hostil",
            "blames", "downing", "shot down", "downed", "must respond", "vows", "seizes",
            "seizure", "airstrike", "deploys", "buildup", "ultimatum", "responds")
_GEO_DEESC = ("ceasefire", "cease-fire", "truce", "halt attack", "halts attack", "de-escalat",
              "deescalat", "peace", "peace talks", "deal reached", "agreement", "eases tension",
              "withdraw troops", "troop withdrawal", "pulls back", "calm", "diplomat",
              "negotiat", "restraint", "step back", "stand down", "resumes talks")
# gurultu: spor/kultur haberi jeopolitik degil (sert-catisma kelimesi yoksa ele)
_GEO_NOISE = ("world cup", "tournament", "ticket", "olympic", "fifa", "league", "film",
              "movie", "actor", "singer", "concert", "celebrity", "fashion", "recipe",
              "match ", "stadium", "championship")
_GEO_HARD = ("missile", "airstrike", "strike", "attack", "war", "killed", "kills", "troops",
             "bomb", "invasion", "nuclear", "shot down", "downing", "ceasefire", "truce")

def _geo_scan(news_list):
    geo = []; esc = 0; deesc = 0
    for n in (news_list or []):
        if not isinstance(n, dict):
            continue
        txt = ((n.get('headline', '') or '') + ' ' + (n.get('summary', '') or '')).lower()
        if not any(k in txt for k in _GEO_RELEVANT):
            continue
        # gurultu filtresi: spor/kultur + sert-catisma yoksa atla
        if any(k in txt for k in _GEO_NOISE) and not any(k in txt for k in _GEO_HARD):
            continue
        e = sum(1 for k in _GEO_ESC if k in txt)
        d = sum(1 for k in _GEO_DEESC if k in txt)
        lab = 'ESCALATION' if e > d else ('DE-ESCALATION' if d > e else 'nötr/sürüyor')
        esc += e; deesc += d
        geo.append({'headline': n.get('headline', '')[:90], 'tip': lab})
    if not geo:
        return None
    net = esc - deesc
    if net > 0:
        regime = 'ESCALATION → safe-haven ON'; tilt = +1; bias = 'ALTIN ↑ (güvenli-liman talebi)'
    elif net < 0:
        regime = 'DE-ESCALATION → safe-haven OFF'; tilt = -1; bias = 'ALTIN ↓ (güvenli-liman primi çözülür)'
    else:
        regime = 'karışık / sürüyor'; tilt = 0; bias = 'net yön yok'
    return {'geo': geo, 'esc': esc, 'deesc': deesc, 'net': net,
            'regime': regime, 'tilt': tilt, 'bias': bias}

st.divider()
st.subheader("📰 Makro Sürpriz → Altın Tepkisi (event-driven)")
st.caption("KANIT: güçlü ABD verisi → reel faiz/USD ↑ → altın AŞAĞI (2000 sonrası, IMF Roache-Rossi). "
           "Yön sinyali yalnız NET-işaretli sürpriz sonrası ~0–30 dk geçerli; GPR safe-haven rejiminde zayıflar. "
           "Trade kararı DEĞİL — tepki yönü + volatilite uyarısı.")
try:
    _msres, _mshas = _msurp_scan(calendar)
    _msfired = [r for r in _msres if r.get('status') == 'ok']
    # secrets'ta FMP_KEY varsa ve takvimde deger yoksa FMP dene
    if not _msfired and not _mshas:
        try:
            _fmpk = st.secrets.get("FMP_KEY", "")
        except Exception:
            _fmpk = ""
        if _fmpk:
            try:
                _fcal = _msurp_fmp(_fmpk)
                _r2, _h2 = _msurp_scan(_fcal)
                _msfired = [r for r in _r2 if r.get('status') == 'ok']
                if _msfired:
                    st.caption("Kaynak: FMP ücretsiz ekonomik takvim.")
            except Exception as _fe:
                st.caption(f"FMP çekilemedi: {type(_fe).__name__}")
    if _msfired:
        st.dataframe(pd.DataFrame([{
            'Saat': r.get('time', ''), 'Olay': r['event'],
            'Konsensüs': r.get('estimate'), 'Açıklanan': r.get('actual'),
            'z': r['z'], 'Altın tepki %': r['gold_pct'],
            'Yön': r['direction'], 'Güven': r['confidence'],
        } for r in _msfired]), hide_index=True, use_container_width=True)
        st.caption("⚠️ Hareket ilk 0–30 dk'da. CFD spread/slipaj release'te açılır → küçük/temkinli, 'predict' değil 'confirmed move' tercih et.")
    elif _mshas:
        st.info("Takvimde değerler var ama bugün tepki-tablosundaki bir olay yok (CPI/NFP/PCE/FOMC/ISM vb. yok).")
    else:
        st.warning("Takviminde estimate/actual sayıları YOK (sadece program + impact). Sürpriz yönü için: "
                   "(a) `.streamlit/secrets.toml`'a `FMP_KEY=\"...\"` ekle (financialmodelingprep.com — ücretsiz, kart yok), "
                   "veya (b) aşağıdan manuel gir.")
    with st.expander("✍️ Manuel sürpriz hesapla (konsensüs + açıklanan)"):
        _mevn = st.selectbox("Olay", ["Nonfarm Payrolls", "Core CPI MoM", "CPI YoY", "Core PCE MoM",
                                      "Unemployment Rate", "Retail Sales MoM", "ISM Manufacturing",
                                      "GDP", "Fed Interest Rate Decision", "Initial Jobless Claims", "ADP"],
                             key="ms_evn")
        _mc1, _mc2 = st.columns(2)
        _mfc = _mc1.number_input("Konsensüs (forecast)", value=0.0, format="%.2f", key="ms_fc")
        _mac = _mc2.number_input("Açıklanan (actual)", value=0.0, format="%.2f", key="ms_ac")
        if st.button("Hesapla", key="ms_btn"):
            _mr = _msurp_expected(_mevn, _mac, _mfc)
            if _mr and _mr.get('status') == 'ok':
                st.metric(_mevn, _mr['direction'], f"z={_mr['z']} · ~{_mr['gold_pct']}% · güven {_mr['confidence']}")
                st.caption(_mr['note'] + " — Asıl hareket ilk 0–30 dk; spread/slipaj geniş, küçük tut.")
            else:
                st.caption("Bu olay tepki tablosunda yok ya da veri eksik.")
except Exception as _mse:
    st.caption(f"Makro sürpriz paneli atlandı: {type(_mse).__name__}")

# === JEOPOLITIK SAFE-HAVEN RADARI (haber-driven yon) ===
st.divider()
st.subheader("🌍 Jeopolitik & Safe-Haven Radarı")
st.caption("Haber akışını jeopolitik gerginlik için tarar. DİKKAT: İran/Hürmüz'de klasik 'jeopolitik→altın↑' "
           "şablonu TERSİNE işleyebilir (petrol→enflasyon→reel faiz→altın↓). Yön çift-yönlü; bu panel YÖN için "
           "değil, volatilite/gap uyarısı + bağlam içindir. Anahtar-kelime tabanlı; net olaylarda güvenilir.")
try:
    _geo = _geo_scan(news)
    if _geo is None:
        st.info("Şu an haber akışında belirgin jeopolitik sinyal yok.")
    else:
        _glab = _geo['regime'].split(' → ')[0]
        _gc1, _gc2 = st.columns([2, 3])
        _gc1.metric("Jeopolitik gerginlik", _glab, "⚠️ volatilite/gap")
        _gc2.write(f"**Okuma:** {_glab}  (escalation puanı {_geo['esc']} · de-escalation {_geo['deesc']})  \n"
                   "**Yön belirsiz** — aşağıdaki petrol-kanalı notuna bak.")
        st.dataframe(pd.DataFrame([{'Haber': g['headline'], 'Sınıf': g['tip']} for g in _geo['geo']]),
                     hide_index=True, use_container_width=True)
        st.warning("⚠️ Bu savaşa özel (İran/Hürmüz): **escalation altını YUKARI değil AŞAĞI itebilir.** "
                   "Hürmüz/petrol arz korkusu → petrol↑ → enflasyon↑ → Fed şahin → reel faiz↑ + USD↑ → altın↓. "
                   "Klasik 'jeopolitik→safe-haven→altın↑' şablonu burada TERSİNE dönebilir. De-escalation ise "
                   "petrol/enflasyon baskısını azaltır. Net: yön çift-yönlü ve petrole bağlı — radarı YÖN için "
                   "değil, **volatilite/gap uyarısı** için kullan.")
        st.caption("Nüans: Hürmüz fiilen kapanırsa ilk akut şokta kısa süre hem petrol hem altın panikle "
                   "fırlayabilir; petrol-faiz kanalı orta vadede baskın olur. Petrol percentile düşükse "
                   "(şu an ~20p) arz şoku henüz fiyatlanmıyor → bugünkü altın hareketi daha çok genel makro.")
except Exception as _ge:
    st.caption(f"Jeopolitik radar atlandı: {type(_ge).__name__}")

st.divider()
st.header("🔬 Gelişmiş Analitik Katman")
st.caption("⚠️ Bu bölümdeki HİÇBİR panel trade kararına girmez (karar yine D1+H4+H1 confluence + V10 + master gate). "
           "Hepsi REJİM / RİSK / BOYUTLANDIRMA bağlamıdır — sinyal üreteci DEĞİLDİR. "
           "Backtest gerçeği: fade'de edge yok; bu yöntemler hasar-kontrol/bağlam araçlarıdır, edge üretmez.")

try:
    _adv = _df_d1_v10.copy().sort_values('datetime').reset_index(drop=True)
    _adv['ret'] = np.log(_adv['close']).diff()
    _adv['vol20'] = _adv['ret'].rolling(20).std()
    _adv_ok = len(_adv) > 80
except Exception:
    _adv_ok = False

if not _adv_ok:
    st.info("Gelişmiş katman için yeterli D1 verisi yok (bu sefer atlanıyor).")
else:
    _rv = _adv['ret'].fillna(0).values
    _vv = _adv['vol20'].fillna(np.nanmedian(_adv['vol20'].values)).values
    _adv_regime_states = None
    _adv_vol_rank = None

    # ---------- ① HMM REJIM (#2) ----------
    with st.expander("① HMM Rejim — sakin / normal / yüksek-vol (#2)", expanded=True):
        try:
            from hmmlearn.hmm import GaussianHMM
            _X = np.column_stack([_rv, _vv])
            _hmm = GaussianHMM(n_components=3, covariance_type='diag', n_iter=25, random_state=0)
            _hmm.fit(_X)
            _stt = _hmm.predict(_X)
            _adv_regime_states = _stt
            _stats = {s: (float(np.nanmean(_adv['ret'].values[_stt == s])),
                          float(np.nanstd(_adv['ret'].values[_stt == s]))) for s in range(3)}
            _vol_rank = sorted(range(3), key=lambda s: _stats[s][1])  # düşük->yüksek vol
            _adv_vol_rank = _vol_rank
            _nm = {_vol_rank[0]: 'SAKİN (düşük-vol)', _vol_rank[1]: 'NORMAL', _vol_rank[2]: 'YÜKSEK-VOL / stres'}
            _cur = int(_stt[-1])
            c1, c2, c3 = st.columns(3)
            c1.metric("Mevcut rejim", _nm[_cur])
            c2.metric("Rejim ort. günlük", f"{_stats[_cur][0]*100:+.2f}%")
            c3.metric("Rejim oynaklık", f"{_stats[_cur][1]*100:.2f}%")
            if _cur == _vol_rank[2]:
                st.warning("Yüksek-vol/stres rejimi → lot KÜÇÜLT, stop geniş; fade en güvenilmez burada.")
            elif _cur == _vol_rank[0]:
                st.info("Sakin rejim → range şartları (ama yine STOP'lu).")
            else:
                st.caption("Normal rejim.")
            st.caption("Backtest notu: fade ÜÇ rejimde de kaybetti; en az kötü 'sakin' rejim. Bu panel yön vermez.")
        except ImportError:
            try:
                _vt = _adv['vol20'].dropna()
                _q1, _q2 = float(_vt.quantile(0.33)), float(_vt.quantile(0.66))
                _cv = float(_adv['vol20'].iloc[-1])
                _lbl = "SAKİN (düşük-vol)" if _cv <= _q1 else ("YÜKSEK-VOL / stres" if _cv >= _q2 else "NORMAL")
                c1, c2 = st.columns(2)
                c1.metric("Mevcut rejim (vol-tercile)", _lbl)
                c2.metric("Bugünkü 20g vol", f"{_cv*100:.2f}%")
                st.caption("hmmlearn yok → basit vol-tercile rejim gösteriliyor (yine bağlam verir). "
                           "Tam 3-durumlu HMM için `pip install hmmlearn`.")
            except Exception:
                st.warning("`pip install hmmlearn` gerekli — tam HMM rejim paneli için.")
        except Exception as e:
            st.caption(f"Hesaplanamadı: {type(e).__name__}")

    # ---------- ② KARMASIKLIK: Entropy / Hurst / FracDiff (#7 + #1) ----------
    with st.expander("② Karmaşıklık: Permütasyon/Sample Entropy · Hurst · FracDiff (#7)", expanded=False):
        try:
            _wret = _adv['ret'].dropna().values

            def _perm_ent(x, m=3):
                nn = len(x) - m + 1
                if nn < 2:
                    return np.nan
                patt = {}
                for i in range(nn):
                    p = tuple(np.argsort(x[i:i + m]))
                    patt[p] = patt.get(p, 0) + 1
                pr = np.array(list(patt.values())) / nn
                return float(-(pr * np.log(pr)).sum() / np.log(_math.factorial(m)))

            def _samp_ent(x, m=2, r=None):
                x = np.asarray(x); N = len(x)
                if N < m + 2:
                    return np.nan
                if r is None:
                    r = 0.2 * np.std(x)

                def _phi(mm):
                    cnt = 0; tot = 0
                    for i in range(N - mm):
                        for j in range(i + 1, N - mm):
                            tot += 1
                            if np.max(np.abs(x[i:i + mm] - x[j:j + mm])) <= r:
                                cnt += 1
                    return cnt / tot if tot > 0 else 0.0
                a = _phi(m + 1); b = _phi(m)
                return float(-np.log(a / b)) if (a > 0 and b > 0) else np.nan

            def _hurst(x):
                x = np.asarray(x)
                if len(x) < 16 or np.std(x) == 0:
                    return 0.5
                z = np.cumsum(x - x.mean()); R = z.max() - z.min(); S = np.std(x)
                return float(np.log(R / S + 1e-9) / np.log(len(x))) if S > 0 else 0.5

            _pe = _perm_ent(_wret[-50:]); _se = _samp_ent(_wret[-60:]); _hu = _hurst(_wret[-50:])
            c1, c2, c3 = st.columns(3)
            c1.metric("Permütasyon entropy", f"{_pe:.2f}" if _pe == _pe else "—",
                      help="1'e yakın = rastgele/öngörülemez; düşük = yapılı")
            c2.metric("Sample entropy", f"{_se:.2f}" if _se == _se else "—",
                      help="Yüksek = düzensiz/öngörülemez")
            c3.metric("Hurst", f"{_hu:.2f}", help=">0.5 trend · <0.5 mean-revert · ~0.5 rastgele")
            st.caption("Yüksek entropy + Hurst≈0.5 → piyasa gürültülü, mean-revert/fade güvenilmez. (Bağlam, sinyal değil.)")
        except Exception as e:
            st.caption(f"Hesaplanamadı: {type(e).__name__}")

    # ---------- ③ META-LABELING (#1) ----------
    with st.expander("③ Meta-Labeling — fade kalite skoru (#1; düşük örneklem, illüstratif)", expanded=False):
        try:
            from sklearn.ensemble import HistGradientBoostingClassifier
            _H = _adv['high'].values; _L = _adv['low'].values; _C = _adv['close'].values
            _sma = _adv['close'].rolling(50).mean().values
            _vol = _adv['vol20'].values
            _na = len(_adv)

            def _cam(h, l, c):
                r = h - l
                return c + r * 1.1 / 4, c + r * 1.1 / 2, c - r * 1.1 / 4, c - r * 1.1 / 2, (h + l + c) / 3
            _rows = []
            for i in range(55, _na - 1):
                if np.isnan(_vol[i]) or np.isnan(_sma[i - 1]):
                    continue
                R3, R4, S3, S4, PP = _cam(_H[i - 1], _L[i - 1], _C[i - 1])
                down = _C[i - 1] < _sma[i - 1]
                side = None
                if _H[i] >= R3 and down:
                    side = 1; e = R3; tp = PP; sl = R4
                elif _L[i] <= S3 and not down:
                    side = 0; e = S3; tp = PP; sl = S4
                if side is None:
                    continue
                res = None; j = i
                while j < _na and j < i + 20:
                    if side == 1:
                        if _H[j] >= sl: pnl = -(sl - e); res = 1; break
                        if _L[j] <= tp: pnl = (e - tp); res = 1; break
                    else:
                        if _L[j] <= sl: pnl = -(e - sl); res = 1; break
                        if _H[j] >= tp: pnl = (tp - e); res = 1; break
                    j += 1
                if res is None:
                    pnl = (e - _C[min(j, _na - 1)]) if side == 1 else (_C[min(j, _na - 1)] - e)
                lab = 1 if (pnl - 0.40) > 0 else 0
                reg = int(_adv_regime_states[i]) if _adv_regime_states is not None else 0
                _rows.append([side, _vol[i], (_C[i - 1] / _C[i - 6] - 1) if i >= 6 else 0.0,
                              (e - PP) / (_C[i - 1] * 0.01), reg, lab])
            _Tm = np.array(_rows, dtype=float)
            if len(_Tm) >= 60:
                _Xm = _Tm[:, :-1]; _ym = _Tm[:, -1]
                _cut = int(len(_Tm) * 0.7)
                _clf = HistGradientBoostingClassifier(max_depth=3, max_iter=150,
                                                      learning_rate=0.06, min_samples_leaf=20, random_state=0)
                _clf.fit(_Xm[:_cut], _ym[:_cut])
                _ptest = _clf.predict_proba(_Xm[_cut:])[:, 1]
                _ytest = _ym[_cut:]
                _hi = _ptest >= 0.55
                _wr_all = _ytest.mean()
                _wr_hi = _ytest[_hi].mean() if _hi.sum() > 0 else float('nan')
                c1, c2 = st.columns(2)
                c1.metric("Ham fade WR (OOS)", f"{_wr_all*100:.0f}%")
                c2.metric("Meta p≥0.55 WR (OOS)", f"{_wr_hi*100:.0f}%" if _hi.sum() > 0 else "—",
                          help=f"{int(_hi.sum())} işlem onaylandı")
                st.caption(f"İşlem havuzu n={len(_Tm)} (D1, son ~{len(_adv)} bar). "
                           "Meta-model precision'ı artırır (daha az, daha iyi işlem) ama tam backtest'te "
                           "**fade'i pozitife çeviremedi** — kaybı azaltır, edge yaratmaz. Düşük örneklem; illüstratif.")
            else:
                st.caption(f"Yeterli geçmiş fade yok (n={len(_Tm)}), model eğitilemedi. Daha uzun D1 geçmişi gerekir.")
        except ImportError:
            st.warning("`pip install scikit-learn` gerekli — bu panel için. "
                       "(Tam de Prado backtest zaten offline `metalabel.py`'de: fade'i pozitife çeviremedi.)")
        except Exception as e:
            st.caption(f"Hesaplanamadı: {type(e).__name__}")
    with st.expander("④ Cross-Asset: reel faiz / DXY / TLT → altın bağlamı (#3)", expanded=False):
        try:
            @st.cache_data(ttl=1800)
            def _adv_yf(tkr):
                import yfinance as yf
                h = yf.Ticker(tkr).history(period="6mo", interval="1d")
                s = h["Close"].dropna()
                try:
                    s.index = s.index.tz_localize(None)
                except Exception:
                    pass
                return s
            _g = _adv_yf("GC=F"); _dxy = _adv_yf("DX-Y.NYB"); _tlt = _adv_yf("TLT")
            _gr = np.log(_g).diff()
            _rows2 = []
            for nm, s in [("DXY", _dxy), ("TLT", _tlt)]:
                _sr = np.log(s).diff()
                _j = pd.concat([_gr.rename("g"), _sr.rename("x")], axis=1).dropna().tail(60)
                if len(_j) > 20:
                    _c0 = _j["g"].corr(_j["x"])
                    _c1 = _j["g"].corr(_j["x"].shift(1))  # x dün -> altın bugün (lead)
                    _rows2.append({"Varlık": nm, "60g eşzamanlı kor.": f"{_c0:+.2f}",
                                   "1g gecikmeli (lead?)": f"{_c1:+.2f}"})
            if _rows2:
                st.dataframe(pd.DataFrame(_rows2), hide_index=True, use_container_width=True)
            # 10Y reel faiz korelasyonu (Treasury CSV — onun aginda calisir)
            try:
                import io as _io, requests as _rq
                _ua = {"User-Agent": "Mozilla/5.0"}; _yr = datetime.utcnow().year; _real = None
                for _y in (_yr - 1, _yr):
                    try:
                        _u = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
                              f"daily-treasury-rates.csv/{_y}/all?type=daily_treasury_real_yield_curve&_format=csv")
                        _rr = _rq.get(_u, timeout=20, headers=_ua); _rr.raise_for_status()
                        _dfr = pd.read_csv(_io.StringIO(_rr.text))
                        _col = next((cc for cc in _dfr.columns if "10" in str(cc)), None)
                        _dc = _dfr.columns[0]
                        _dfr[_dc] = pd.to_datetime(_dfr[_dc], errors='coerce')
                        _dfr[_col] = pd.to_numeric(_dfr[_col], errors='coerce')
                        _s = _dfr.dropna(subset=[_dc, _col]).sort_values(_dc).set_index(_dc)[_col]
                        _real = _s if _real is None else pd.concat([_real, _s])
                    except Exception:
                        pass
                if _real is not None and len(_g) > 60:
                    _jr = pd.concat([_g.rename('g'), _real.rename('r')], axis=1).dropna()
                    _cr = np.log(_jr['g']).diff().rolling(60).corr(_jr['r'].diff()).iloc[-1]
                    _mr = ("🔴 reel faizden KOPMUŞ (başka rejim baskın)" if _cr > -0.10
                           else "🟢 NORMAL: reel faiz altını sürüyor" if _cr < -0.30 else "🟡 geçiş")
                    st.write(f"**Altın–10Y reel faiz 60g korelasyon: {_cr:+.2f}** → {_mr}")
            except Exception:
                pass
            st.caption("Eşzamanlı korelasyon güçlü, gecikmeli (lead) genelde zayıf → bunlar **teyit/bağlam**, "
                       "öncü sinyal değil. Reel-faiz korelasyonu sıfıra dönerse MB alımı/jeopolitik baskın demektir.")
        except ImportError:
            st.warning("`pip install yfinance` gerekli.")
        except Exception as e:
            st.caption(f"Veri alınamadı: {type(e).__name__}")

    # ---------- ⑤ OYNAKLIK → ONERILEN LOT (#4 GARCH-MIDAS basit) ----------
    with st.expander("⑤ Oynaklık tahmini → önerilen lot/stop (#4)", expanded=False):
        try:
            _r2 = _adv['ret'].dropna().values * 100.0  # %
            _fvol = None; _src = ""
            try:
                from arch import arch_model
                _am = arch_model(_r2[-500:], vol='GARCH', p=1, q=1, dist='normal')
                _res = _am.fit(disp='off')
                _fc = _res.forecast(horizon=1, reindex=False)
                _fvol = float(np.sqrt(_fc.variance.values[-1, 0])) / 100.0
                _src = "GARCH(1,1)"
            except Exception:
                # fallback: EWMA vol
                _lam = 0.94; _v = np.nanvar(_adv['ret'].dropna().values[-30:])
                for x in _adv['ret'].dropna().values[-30:]:
                    _v = _lam * _v + (1 - _lam) * x * x
                _fvol = float(np.sqrt(_v)); _src = "EWMA (arch yok)"
            _atr_usd = _fvol * float(price)              # ~1σ gunluk $ hareket
            _sl_usd = max(5.0, 1.5 * _atr_usd)           # ~1.5σ stop ($/oz)
            _acct = 1000.0                               # varsayim (senin hesabin)
            # XM altin: 0.01 lot = 1 oz -> $1/oz hareket = $1 P&L (min lot)
            _risk_minlot = _sl_usd * 1.0
            _pct_minlot = _risk_minlot / _acct * 100
            c1, c2, c3 = st.columns(3)
            c1.metric("Tahmini 1σ günlük", f"${_atr_usd:.1f}", help=_src)
            c2.metric("~1.5σ stop önerisi", f"${_sl_usd:.1f}")
            c3.metric("Min lot (0.01) riski", f"${_risk_minlot:.0f} · %{_pct_minlot:.1f}",
                      help=f"~${_acct:.0f} hesap varsayımı; 0.01 lot = 1 oz. %2 üstü = fazla risk.")
            if _pct_minlot > 2.0:
                st.warning(f"Min lot 0.01 + \\${_sl_usd:.0f} stop = hesabın %{_pct_minlot:.1f}'i. "
                           f"Lot daha küçültülemez (min 0.01) → bu vol/stop \\${_acct:.0f} hesaba göre BÜYÜK pozisyon.")
            st.caption("Oynaklık YÖN değil **boyutlandırma** içindir; yüksek vol → daha küçük lot gerekir (ama min 0.01 sınırı var). (#4)")
        except Exception as e:
            st.caption(f"Hesaplanamadı: {type(e).__name__}")

    # ---------- ⑥ GOOGLE TRENDS / SENTIMENT (#5) ----------
    with st.expander("⑥ Google Trends 'gold' ilgisi (#5)", expanded=False):
        try:
            @st.cache_data(ttl=3600)
            def _adv_trends():
                from pytrends.request import TrendReq
                _tr = TrendReq(hl='en-US', tz=0)
                _tr.build_payload(["gold price"], timeframe='today 3-m')
                _df = _tr.interest_over_time()
                return _df["gold price"] if "gold price" in _df else None
            _gt = _adv_trends()
            if _gt is not None and len(_gt) > 5:
                _last = float(_gt.iloc[-1]); _pavg = float(_gt.iloc[-8:-1].mean())
                c1, c2 = st.columns(2)
                c1.metric("'gold price' ilgisi (0-100)", f"{_last:.0f}")
                c2.metric("Son hafta vs önceki", f"{_last - _pavg:+.0f}")
                st.caption("Yüksek/artan arama ilgisi → retail kalabalığı/oynaklık göstergesi (kontraryan eğilim). "
                           "Yön sinyali değil, olay/oynaklık bağlamı.")
            else:
                st.caption("Trends verisi alınamadı.")
        except ImportError:
            st.warning("`pip install pytrends` gerekli.")
        except Exception as e:
            st.caption(f"Veri alınamadı: {type(e).__name__}")

    # ---------- ⑦ COT POZISYONLAMA (#6) ----------
    with st.expander("⑦ CFTC COT — yönetilen para net pozisyon (#6)", expanded=False):
        try:
            @st.cache_data(ttl=86400)
            def _adv_cot():
                # CFTC Socrata API - disaggregated, gold (commodity 'GOLD')
                url = ("https://publicreporting.cftc.gov/resource/72hh-3qpy.json"
                       "?$where=contract_market_name like 'GOLD%25'"
                       "&$order=report_date_as_yyyy_mm_dd DESC&$limit=60")
                r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
                r.raise_for_status()
                return pd.DataFrame(r.json())
            _cot = _adv_cot()
            _ncol = next((c for c in _cot.columns if 'm_money_positions_long' in c), None)
            _scol = next((c for c in _cot.columns if 'm_money_positions_short' in c), None)
            if _ncol and _scol:
                _cot[_ncol] = pd.to_numeric(_cot[_ncol], errors='coerce')
                _cot[_scol] = pd.to_numeric(_cot[_scol], errors='coerce')
                _net = (_cot[_ncol] - _cot[_scol]).dropna()
                _cur = float(_net.iloc[0]); _pct = float((_net <= _cur).mean()) * 100
                c1, c2 = st.columns(2)
                c1.metric("Yönetilen para net (son)", f"{_cur:,.0f}")
                c2.metric("Son 60 hafta persentil", f"%{_pct:.0f}",
                          help="Yüksek=aşırı long (kalabalık). COT reaktiftir, öncü değil.")
                st.caption("COT akademik olarak fiyatı TAKİP eder (trend-takipçi), öncülemez. Bağlam/aşırılık göstergesi.")
            else:
                st.caption("COT alanları bulunamadı (CFTC şeması değişmiş olabilir).")
        except Exception as e:
            st.caption(f"Veri alınamadı: {type(e).__name__}")

    st.caption("— Gelişmiş katman sonu. Tekrar: bu panellerin hiçbiri trade kararına girmez; bağlam/risk içindir. —")


# =====================================================================
# v38 — OLASILIKLI GORUNUM (EN UST PANEL) — tum veriyi sentezler
# Placeholder (_forecast_slot) en ustte; burada (script sonunda) doldurulur
# ki vol/regime/camarilla/trend/makro/event hepsini kullanabilsin.
# ARALIK + KIRILMA RISKI gercek sentez; YON sadece kucuk egilim (~coin-flip),
# trade sinyali DEGIL. Komple try/except -> dashboard'i bozamaz.
# =====================================================================
try:
    with _forecast_slot.container():
        st.divider()
        st.subheader("🎯 Olasılıklı Görünüm — Aralık + Kırılma Riski")
        st.caption("Tüm veriyi sentezler (vol + rejim + kümelenme + trend + makro + Camarilla + takvim). "
                   "NOKTA-FİYAT tahmini DEĞİL; olasılıklı aralık + kırılma riskidir. "
                   "Yön ≈ yazı-tura (asıl bilgi aralıkta). Trade kararı ayrı (D1+H4+H1).")
        _fr, _sd20, _clu, _sig_d, _cv, _regime = forecast_inputs(_df_d1_v10)
        # yon egilimi (45-55 KAPALI, coin-flip)
        _tilt = 0.5
        _pvs = (_tf.get('metrics') or {}).get('price_vs_sma50')
        _r5 = (_tf.get('metrics') or {}).get('ret_5d')
        if _pvs is not None and _r5 is not None:
            if _pvs < -1 and _r5 < 0: _tilt -= 3.0
            elif _pvs > 1 and _r5 > 0: _tilt += 3.0
            elif _pvs < -1 or _r5 < 0: _tilt -= 1.5
            elif _pvs > 1 or _r5 > 0: _tilt += 1.5
        if _macro.get('bias') == 'HEADWIND': _tilt -= 2.0
        elif _macro.get('bias') == 'TAILWIND': _tilt += 2.0
        # jeopolitik: YÖN'e BESLEMİYORUZ. İran/Hürmüz'de escalation, petrol→enflasyon→reel-faiz
        # kanalıyla altını DÜŞÜREBİLİR (klasik safe-haven'ın tersi); yön çift-yönlü/güvenilmez.
        # _geo_fc sadece bağlam satırı + volatilite uyarısı için okunur.
        _geo_fc = None
        try:
            _geo_fc = _geo_scan(news)
        except Exception:
            _geo_fc = None
        _tilt = float(np.clip(_tilt, -5.0, 5.0))
        _p_up = 50.0 + _tilt; _p_dn = 100.0 - _p_up
        # kirilma riski
        _bo = 0
        _last_move = abs(float(_fr.iloc[-1])) / (_sd20 + 1e-9)
        if _last_move > 1.5 or _clu > 1.2: _bo += 1
        _gvz_c = (channels.get('GVZ') or ('GREEN', ''))[0]
        if _gvz_c == 'RED': _bo += 2
        elif _gvz_c == 'YELLOW': _bo += 1
        _tier1 = False; _hi_evt = False
        try:
            for _e in (calendar or []):
                _nm = str(_e.get('event', '')).lower()
                if str(_e.get('impact', '')).lower() == 'high':
                    _hi_evt = True
                    if any(_k in _nm for _k in _BLACKOUT_KEYS): _tier1 = True
        except Exception:
            pass
        if _tier1: _bo += 2
        elif _hi_evt: _bo += 1
        if _suppression_active: _bo += 1
        _bo_lbl = "🟢 NORMAL" if _bo <= 1 else ("🟠 YÜKSEK" if _bo <= 3 else "🔴 ÇOK YÜKSEK")
        a, b, c = st.columns(3)
        a.metric("Yön eğilimi (≈ coin-flip)", f"↑%{_p_up:.0f} / ↓%{_p_dn:.0f}",
                 help="45–55 ile sınırlı; yön güvenilir tahmin DEĞİL, sadece trend+makro eğilimi.")
        b.metric("Oynaklık rejimi", _regime, help=f"bugünkü 20g vol %{_cv*100:.2f}")
        c.metric("Kırılma riski", _bo_lbl,
                 help="vol kümelenmesi + GVZ + takvim. Yön coin-flip; bu yalnız OLASILIK/büyüklük.")
        _signal_bands = {}
        _rows = []
        for _lbl, _k in [("1 gün", 1), ("1 hafta", 5), ("1 ay", 21)]:
            _s1 = price * _sig_d * np.sqrt(_k)
            _signal_bands[{1: "1D", 5: "1W", 21: "1M"}[_k]] = {
                "low": price - _s1, "high": price + _s1,
                "low_95": price - 2 * _s1, "high_95": price + 2 * _s1,
                "nominal_coverage": 0.68, "trading_days": _k}
            _rows.append({"Ufuk": _lbl,
                          "±1σ (%68)": f"${price-_s1:,.0f} – ${price+_s1:,.0f}",
                          "±2σ (%95)": f"${price-2*_s1:,.0f} – ${price+2*_s1:,.0f}",
                          "±1σ genişlik": f"±${_s1:,.0f}"})
        _signal_probability = {"volatility_regime": _regime,
                               "breakout_risk": {"score": _bo, "label": _bo_lbl},
                               "bands": _signal_bands}
        st.dataframe(pd.DataFrame(_rows), hide_index=True, use_container_width=True)
        try:
            _ac = _avg_cam
            st.write(f"📊 **Beklenen aralık duvarları (ort. Camarilla):** alt S3 **\\${_ac['S3']:,.0f}** · "
                     f"üst R3 **\\${_ac['R3']:,.0f}**. Bu duvarların DIŞINA kapanış = kırılma "
                     f"(yön coin-flip → **fade etme, kenara çekil**).")
        except Exception:
            pass
        try:
            if _geo_fc and _geo_fc.get('net'):
                _glab = _geo_fc['regime'].split(' → ')[0]
                st.write(f"🌍 **Jeopolitik:** {_glab} tespit → **volatilite/gap riski yüksek, yön belirsiz.** "
                         "İran/Hürmüz'de escalation petrol→enflasyon→reel-faiz kanalıyla altını DÜŞÜREBİLİR "
                         "(klasik safe-haven'ın tersi). Yön için tek başına kullanma — detay 'Jeopolitik Radarı'.")
        except Exception:
            pass
        st.caption("⚠️ Fat-tail: altında >3σ hareketler normal beklentinin ~5 katı sık (basıklık 12.9); "
                   "±2σ 'kesin tavan' değil — kuyruk payı bırak. Kırılma OLUR mu / ne kadar = bağlam var; "
                   "hangi YÖNE = ~yazı-tura. Aralık = son 20g vol × √zaman (kümelenmeyle düzeltildi).")
except Exception as _ferr:
    _signal_probability = None
    _signal_errors.append({"component": "probabilistic_view", "type": type(_ferr).__name__})
    try:
        _forecast_slot.caption(f"Olasılıklı görünüm hesaplanamadı: {type(_ferr).__name__}: {_ferr}")
    except Exception:
        pass


# JSON export is isolated from the dashboard UI and contains an explicit allowlist.
try:
    _signal_payload = build_snapshot(
        price=price, price_fetched_at=_price_fetched_at,
        started_at=_signal_started_at, request_id=_signal_request_id,
        gate_level=_mg_level, sides=_mg_sides, channels=channels,
        metrics=_tf.get("metrics", {}), probability=_signal_probability,
        errors=_signal_errors, news_count=len(news or []), calendar_count=len(calendar or []))
    publish_snapshot(_SignalPath(__file__).resolve().parent / "static" / "xau-signal.json",
                     _signal_payload)
    if _signal_readonly:
        st.json(_signal_payload)
except Exception as _signal_export_error:
    st.warning("JSON export failed: " + type(_signal_export_error).__name__)
