"""Multi-TF Camarilla seviyeler"""

def camarilla_levels(prev_high, prev_low, prev_close):
    rng = prev_high - prev_low
    return {
        'PP': (prev_high + prev_low + prev_close) / 3,
        'R4': prev_close + rng * 1.1 / 2,
        'R3': prev_close + rng * 1.1 / 4,
        'R2': prev_close + rng * 1.1 / 6,
        'R1': prev_close + rng * 1.1 / 12,
        'S1': prev_close - rng * 1.1 / 12,
        'S2': prev_close - rng * 1.1 / 6,
        'S3': prev_close - rng * 1.1 / 4,
        'S4': prev_close - rng * 1.1 / 2,
    }

def trade_signal(price, levels, atr14, tolerance=2.0):
    tp = max(10, 0.15 * atr14)
    sl = max(18, 0.60 * atr14)
    for L in ['R3', 'R4']:
        if abs(price - levels[L]) < tolerance:
            return {'side': 'SHORT', 'level': L,
                    'entry': levels[L],
                    'tp': levels[L] - tp,
                    'sl': levels[L] + sl}
    for L in ['S3', 'S4']:
        if abs(price - levels[L]) < tolerance:
            return {'side': 'LONG', 'level': L,
                    'entry': levels[L],
                    'tp': levels[L] + tp,
                    'sl': levels[L] - sl}
    return None

def confluence_signal(d1_sig, h4_sig, h1_sig):
    """Multi-TF onay degerlendirmesi.

    Mantik:
    - D1 ana sinyal (Sharpe 1.85 backtest)
    - H4/H1 confluence olarak eklenir
    - D1 onayi yoksa, H4/H1 tek basina trade vermiyoruz (uyari)

    Returns dict:
        level: 'VERY_STRONG' | 'STRONG' | 'OK' | 'WAIT' | 'NONE'
        side: 'LONG' | 'SHORT' | None
        emoji: '🟢🟢🟢' | '🟢🟢' | '🟢' | '🟡' | '⚪'
        reason: aciklama
        entry/tp/sl: D1 sinyalden (varsa)
    """
    if not d1_sig:
        partials = []
        if h4_sig:
            partials.append(f"H4 {h4_sig['side']} @ ${h4_sig['entry']:.2f}")
        if h1_sig:
            partials.append(f"H1 {h1_sig['side']} @ ${h1_sig['entry']:.2f}")
        if partials:
            return {
                'level': 'WAIT', 'side': None, 'emoji': '🟡',
                'reason': 'D1 ONAYI YOK - mikro sinyal: ' + ', '.join(partials) + '. Trade onerilmez.',
            }
        return {
            'level': 'NONE', 'side': None, 'emoji': '⚪',
            'reason': 'Setup yok - hicbir TF Camarilla seviyesinde degil',
        }

    d1_side = d1_sig['side']
    h4_match = (h4_sig and h4_sig['side'] == d1_side)
    h1_match = (h1_sig and h1_sig['side'] == d1_side)

    base = {
        'side': d1_side,
        'entry': d1_sig['entry'],
        'tp': d1_sig['tp'],
        'sl': d1_sig['sl'],
    }

    if h4_match and h1_match:
        return {**base, 'level': 'VERY_STRONG', 'emoji': '🟢🟢🟢',
                'reason': f"D1+H4+H1 TAM confluence ({d1_side} fade)"}
    if h4_match or h1_match:
        which = 'H4' if h4_match else 'H1'
        return {**base, 'level': 'STRONG', 'emoji': '🟢🟢',
                'reason': f"D1 + {which} confluence ({d1_side} fade)"}
    return {**base, 'level': 'OK', 'emoji': '🟢',
            'reason': f"Sadece D1 sinyali ({d1_side}) - kucuk lot onerilir"}
