"""D1 seviyelerine gore limit emir plani."""

CONFLUENCE_THRESHOLD = 10.0

def check_confluence(d1_price, other_levels, level_names, tolerance=10.0):
    for name in level_names:
        other_price = other_levels.get(name)
        if other_price is None:
            continue
        if abs(d1_price - other_price) <= tolerance:
            return (name, other_price, abs(d1_price - other_price))
    return None

def compute_lot(capital, side_strength, sl_distance=10.0):
    risk_pct = 0.04 if side_strength == 'STRONG' else 0.02
    risk_usd = capital * risk_pct
    lot = risk_usd / (sl_distance * 100)
    lot = round(lot, 2)
    if lot < 0.01:
        lot = 0.01
    return lot

def compute_plan(d1_levels, h4_levels, h1_levels, current_price, capital,
                 confluence_threshold=CONFLUENCE_THRESHOLD):
    plans = []
    long_configs = [
        ('S3', -10, +10, 'Standart (Sharpe ~1.16)'),
        ('S4', -10, +20, 'EN GUCLU (Sharpe 1.85)'),
    ]
    for level_name, sl_offset, tp_offset, strength_label in long_configs:
        d1_price = d1_levels.get(level_name)
        if d1_price is None or d1_price >= current_price:
            continue
        h4_match = check_confluence(d1_price, h4_levels, ['S3', 'S4'], confluence_threshold)
        h1_match = check_confluence(d1_price, h1_levels, ['S3', 'S4'], confluence_threshold)
        confluence = bool(h4_match or h1_match)
        side_strength = 'STRONG' if confluence else 'D1_ONLY'
        lot = compute_lot(capital, side_strength, sl_distance=abs(sl_offset))
        plans.append({
            'side': 'LONG', 'level': level_name,
            'entry': d1_price, 'tp': d1_price + tp_offset, 'sl': d1_price + sl_offset,
            'rr': abs(tp_offset / sl_offset), 'lot': lot, 'confluence': confluence,
            'h4_match': h4_match, 'h1_match': h1_match,
            'strength_label': strength_label, 'distance': current_price - d1_price,
        })
    short_configs = [
        ('R3', +10, -10, 'Standart (Sharpe ~1.0)'),
        ('R4', +10, -20, 'EN GUCLU (Sharpe ~1.4)'),
    ]
    for level_name, sl_offset, tp_offset, strength_label in short_configs:
        d1_price = d1_levels.get(level_name)
        if d1_price is None or d1_price <= current_price:
            continue
        h4_match = check_confluence(d1_price, h4_levels, ['R3', 'R4'], confluence_threshold)
        h1_match = check_confluence(d1_price, h1_levels, ['R3', 'R4'], confluence_threshold)
        confluence = bool(h4_match or h1_match)
        side_strength = 'STRONG' if confluence else 'D1_ONLY'
        lot = compute_lot(capital, side_strength, sl_distance=abs(sl_offset))
        plans.append({
            'side': 'SHORT', 'level': level_name,
            'entry': d1_price, 'tp': d1_price + tp_offset, 'sl': d1_price + sl_offset,
            'rr': abs(tp_offset / sl_offset), 'lot': lot, 'confluence': confluence,
            'h4_match': h4_match, 'h1_match': h1_match,
            'strength_label': strength_label, 'distance': d1_price - current_price,
        })
    return plans

def format_plan_telegram(plans, current_price, capital, today_str, macro_warning=""):
    lines = [
        f"GUNAYDIN! {today_str}",
        "",
        "GUNUN LIMIT EMIR PLANI",
        f"Fiyat: ${current_price:.2f}  Sermaye: ${capital:.2f}",
        "Mode: B.STANDART",
        "",
    ]
    longs = [p for p in plans if p['side'] == 'LONG']
    shorts = [p for p in plans if p['side'] == 'SHORT']
    if longs:
        lines.append("BUY LIMITS (LONG):")
        for p in longs:
            tag = "STRONG" if p['confluence'] else "D1-only"
            lines.append(f"${p['entry']:.2f} D1 {p['level']}  TP ${p['tp']:.2f}  SL ${p['sl']:.2f}")
            lines.append(f"  Lot {p['lot']:.2f}  RR 1:{p['rr']:.1f}  [{tag}]")
            lines.append(f"  {p['strength_label']}  ({p['distance']:+.0f} asagida)")
            if p['h4_match']: lines.append(f"  H4 onay: {p['h4_match'][0]} @ ${p['h4_match'][1]:.2f}")
            if p['h1_match']: lines.append(f"  H1 onay: {p['h1_match'][0]} @ ${p['h1_match'][1]:.2f}")
            lines.append("")
    if shorts:
        lines.append("SELL LIMITS (SHORT):")
        for p in shorts:
            tag = "STRONG" if p['confluence'] else "D1-only"
            lines.append(f"${p['entry']:.2f} D1 {p['level']}  TP ${p['tp']:.2f}  SL ${p['sl']:.2f}")
            lines.append(f"  Lot {p['lot']:.2f}  RR 1:{p['rr']:.1f}  [{tag}]")
            lines.append(f"  {p['strength_label']}  ({p['distance']:+.0f} yukarida)")
            if p['h4_match']: lines.append(f"  H4 onay: {p['h4_match'][0]} @ ${p['h4_match'][1]:.2f}")
            if p['h1_match']: lines.append(f"  H1 onay: {p['h1_match'][0]} @ ${p['h1_match'][1]:.2f}")
            lines.append("")
    if not plans:
        lines.append("Bugun uygun limit emir yok.")
        lines.append("")
    lines.append("UYARILAR:")
    if macro_warning: lines.append(f"  - {macro_warning}")
    lines.append("  - 23:00 NYC kapanis sonrasi yeni seviyeler")
    lines.append("  - Pazartesi 11:00'a kadar acilis volatilitesi var")
    lines.append("  - Silver yasak (<$3000 sermaye)")
    lines.append("")
    lines.append("v19 dashboard")
    return "\n".join(lines)
