"""Portable v18 channels with unchanged calculations and explicit source failures."""
from datetime import datetime, timezone
from pathlib import Path
import json
from ews_calculations import compute_lee_mykland_jumps, compute_cot_williams_index, compute_vol_index_percentile
from market_data import cot_history, gvz_history, closed_bars


def _age_ok(date, key):
    c = json.loads(Path(__file__).with_name('feed_config.json').read_text())
    age = (datetime.now(timezone.utc).date()-datetime.fromisoformat(date).date()).days
    return 0 <= age <= c[key]


def jump_channel(frame=None):
    try:
        bars = closed_bars(frame, 5)
        if len(bars) < 280:
            return 'RED', 'JUMP için en az 280 kapanmış M5 mum gerekli'
        age = (datetime.now(timezone.utc)-datetime.fromisoformat(bars[-1]['closed_at'])).total_seconds()
        if age > 390:
            return 'RED', 'M5 mumları eski; JUMP güncel değil'
        jd = compute_lee_mykland_jumps(bars)
        if jd['jump_in_last_15min']:
            return 'RED', f"15dk jump L={jd['last_jump_L']:.2f}"
        rj = jd['recent_jumps_4h']
        return ('YELLOW' if rj > 2 else 'GREEN'), f'{rj} jump/4h · Twelve Data M5'
    except Exception:
        return 'RED', 'JUMP verisi veya hesabı geçersiz'


def cot_channel():
    rows, error = cot_history()
    if error:
        return 'RED', error
    try:
        if not _age_ok(rows[-1]['date'], 'cot_max_age_days'):
            return 'RED', f"COT raporu eski: {rows[-1]['date']}"
        wi = compute_cot_williams_index(rows)['williams_index']
        return ('YELLOW' if wi >= 75 or wi <= 25 else 'GREEN'), f"WI {wi:.0f} · CFTC {rows[-1]['date']}"
    except Exception:
        return 'RED', 'COT hesabı için geçmiş yetersiz'


def gvz_channel():
    rows, error = gvz_history()
    if error:
        return 'RED', error
    try:
        if not _age_ok(rows[-1]['date'], 'gvz_max_age_days'):
            return 'RED', f"GVZ günlük veri eski: {rows[-1]['date']}"
        pct = compute_vol_index_percentile(rows)['percentile']
        return ('RED' if pct >= 90 else 'YELLOW' if pct >= 75 else 'GREEN'), f"GVZ %{pct:.0f} · günlük {rows[-1]['date']}"
    except Exception:
        return 'RED', 'GVZ hesabı için geçmiş yetersiz'
