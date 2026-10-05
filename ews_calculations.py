"""Pure v18 calculations copied unchanged from the existing desktop engine.
No credentials, imports of the desktop application, or execution side effects.
"""
import math

def compute_lee_mykland_jumps(m5_candles, window=270, alpha=0.01):
    """
    Lee-Mykland (2008) intraday jump detection.
    
    Bilimsel temel:
        Lee & Mykland (2008, Review of Financial Studies 21(6), 2535-2563)
        Wen, Indriawan, Lien & Xu (2023) — EIA inventory haberlerinde 42.2× daha fazla jump.
    
    Hesap:
        L_i = r_i / σ̂_i
        σ̂_i² = (1/(K-2)) Σ |r_{i-j}| × |r_{i-j-1}|  (bipower variation)
        Jump var ise: |L_i| > Gumbel critical value(α, K)
    
    Args:
        m5_candles: M5 OHLC candles
        window: bipower variation penceresi (default 270 ≈ 1.5 işlem günü)
        alpha: significance level (0.01 = %99 güven)
    
    Returns dict:
        recent_jumps: son 4 saatte (~48 M5) jump sayısı
        last_jump_idx: en son jump'ın indeksi
        last_jump_time: en son jump zamanı (string)
        jump_in_last_15min: son 15 dakikada jump var mı (3 M5 bar)
        all_jumps: tüm jump indeksleri (debug için)
    """
    if not m5_candles or len(m5_candles) < window + 10:
        return None
    
    # Log returns
    closes = [c["close"] for c in m5_candles]
    log_rets = []
    for i in range(1, len(closes)):
        if closes[i-1] > 0:
            log_rets.append(math.log(closes[i] / closes[i-1]))
        else:
            log_rets.append(0)
    
    n = len(log_rets)
    if n < window:
        return None
    
    # Gumbel critical value
    # c = (2 log K)^0.5
    # S_n = c - (log π + log log K) / (2c)
    # |L_i| > β* = -log(-log(1-α)) / c + S_n
    K = window
    c_const = math.sqrt(2 * math.log(K))
    S_n = c_const - (math.log(math.pi) + math.log(math.log(K))) / (2 * c_const)
    beta_star = -math.log(-math.log(1 - alpha)) / c_const + S_n
    
    jumps = []
    
    for i in range(window, n):
        # Bipower variation - rolling
        bipower_sum = 0
        for j in range(1, K - 1):
            if i - j - 1 >= 0:
                bipower_sum += abs(log_rets[i - j]) * abs(log_rets[i - j - 1])
        
        sigma_hat_sq = bipower_sum / (K - 2)
        sigma_hat = math.sqrt(sigma_hat_sq) if sigma_hat_sq > 0 else 1e-10
        
        if sigma_hat > 0:
            L_i = abs(log_rets[i]) / sigma_hat
            if L_i > beta_star:
                jumps.append({
                    "idx": i + 1,  # candle indeksi (returns 1-bazlı offset)
                    "L_stat": L_i,
                    "return": log_rets[i],
                    "datetime": m5_candles[i + 1].get("datetime", "?") if i + 1 < len(m5_candles) else "?",
                })
    
    # Son 4 saat = ~48 M5 bar
    recent_jumps_4h = [j for j in jumps if j["idx"] >= n - 47]
    # Son 15 dakika = 3 M5 bar
    recent_jumps_15m = [j for j in jumps if j["idx"] >= n - 2]
    
    return {
        "all_jumps_count": len(jumps),
        "recent_jumps_4h": len(recent_jumps_4h),
        "jump_in_last_15min": len(recent_jumps_15m) > 0,
        "last_jump_idx": jumps[-1]["idx"] if jumps else None,
        "last_jump_time": jumps[-1]["datetime"] if jumps else None,
        "last_jump_L": jumps[-1]["L_stat"] if jumps else None,
        "beta_star_threshold": beta_star,
        "fade_blocked": len(recent_jumps_15m) > 0,
        "academic_basis": "Lee-Mykland (2008) RFS 21(6)",
    }

def compute_cot_williams_index(cot_data, lookback_weeks=156):
    """
    Williams COT %R Index.
    
    Bilimsel temel:
        Williams (1979) - COT %R orijinal
        Bessembinder & Chan (1992, Journal of Finance) - speculator positioning predicts returns
        Sanders, Boris & Manfredo (2004, Energy Economics) - crude oil için.
    
    Hesap:
        WI = (Current - 3yr_Min) / (3yr_Max - 3yr_Min) × 100
        > 85 → aşırı LONG (kalabalık, fade riski yüksek)
        < 15 → aşırı SHORT (dip sinyali)
        15-85 → normal
    
    Args:
        cot_data: fetch_cot()'tan gelen liste, her biri {"date":..., "mm_net":...}
        lookback_weeks: bant pencere (default 156 = 3 yıl)
    
    Returns dict veya None.
    """
    if not cot_data or len(cot_data) < 26:  # En az 6 ay
        return None
    
    # Sadece son lookback_weeks kullan
    relevant = cot_data[-lookback_weeks:] if len(cot_data) >= lookback_weeks else cot_data
    
    nets = [c["mm_net"] for c in relevant]
    if not nets:
        return None
    
    current = nets[-1]
    min_3y = min(nets)
    max_3y = max(nets)
    
    if max_3y == min_3y:
        wi = 50.0
    else:
        wi = ((current - min_3y) / (max_3y - min_3y)) * 100
    
    if wi >= 85:
        signal = "EXTREME_LONG"
        interpretation = "🔴 Speculator'lar aşırı LONG — fade riski yüksek, breakdown olası"
        fade_risk = "high"
    elif wi <= 15:
        signal = "EXTREME_SHORT"
        interpretation = "🟢 Speculator'lar aşırı SHORT — dip sinyali, rally olası"
        fade_risk = "low"
    elif wi >= 75:
        signal = "ELEVATED_LONG"
        interpretation = "🟡 Long pozisyonlar yüksek — dikkat"
        fade_risk = "elevated"
    elif wi <= 25:
        signal = "ELEVATED_SHORT"
        interpretation = "🟡 Short pozisyonlar yüksek — alt destek güçlü"
        fade_risk = "elevated"
    else:
        signal = "NEUTRAL"
        interpretation = "⊝ Pozisyonlar normal aralıkta"
        fade_risk = "normal"
    
    return {
        "williams_index": wi,
        "signal": signal,
        "interpretation": interpretation,
        "fade_risk": fade_risk,
        "current_net": current,
        "min_3y": min_3y,
        "max_3y": max_3y,
        "weeks_analyzed": len(nets),
        "academic_basis": "Williams (1979), Bessembinder & Chan (1992) JoF",
    }

def compute_vol_index_percentile(vol_data, lookback=252):
    """
    GVZ/OVX percentile ranking.
    
    Bilimsel temel:
        Whaley (2000, Journal of Derivatives) - VIX rejim sinyali
        Bouri et al. (2021, IRFA) - GVZ ve VIX kriz dönemlerinde flight-to-safety
    
    Yorumlama:
        Top decile (≥90%): vol patlaması, breakout favori, FADE ETME
        Bottom decile (≤10%): vol sıkışması, breakout patlama yaklaşıyor olabilir
        Normal: orta bant, fade güvenli
    
    Args:
        vol_data: GVZ/OVX günlük listesi
        lookback: percentile penceresi
    
    Returns dict.
    """
    if not vol_data or len(vol_data) < 30:
        return None
    
    recent = vol_data[-lookback:] if len(vol_data) >= lookback else vol_data
    closes = sorted([c["close"] for c in recent])
    current = vol_data[-1]["close"]
    
    # Percentile rank
    rank = 0
    for i, v in enumerate(closes):
        if current >= v:
            rank = i + 1
        else:
            break
    percentile = (rank / len(closes)) * 100
    
    if percentile >= 90:
        signal = "EXTREME_HIGH"
        interpretation = "🔴 Vol top decile — breakout zamanı, FADE ETME"
        fade_safe = False
    elif percentile >= 75:
        signal = "ELEVATED"
        interpretation = "🟡 Vol yüksek — dikkat, lot küçült"
        fade_safe = False
    elif percentile <= 10:
        signal = "EXTREME_LOW"
        interpretation = "🟢 Vol bottom decile — sıkışma, patlama yaklaşıyor olabilir"
        fade_safe = True
    elif percentile <= 25:
        signal = "LOW"
        interpretation = "✓ Vol düşük — fade güvenli rejim"
        fade_safe = True
    else:
        signal = "NORMAL"
        interpretation = "⊝ Vol normal aralık"
        fade_safe = True
    
    return {
        "current": current,
        "percentile": percentile,
        "signal": signal,
        "interpretation": interpretation,
        "fade_safe": fade_safe,
        "min_period": min(closes),
        "max_period": max(closes),
        "median": closes[len(closes) // 2],
        "days_analyzed": len(closes),
        "academic_basis": "Whaley (2000) JoD, Bouri et al. (2021) IRFA",
    }
