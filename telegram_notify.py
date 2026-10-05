"""Telegram notification - v19"""
import requests
from datetime import datetime, date
import streamlit as st


def send_telegram(token, chat_id, message):
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
    msg = f"\U0001f6a8 *Confluence {signal_level}*"
    if price:
        msg += f"\nFiyat: ${price:.2f}"
    if send_telegram(token, chat_id, msg):
        st.session_state[key] = now


def notify_macro_red(token, chat_id):
    """Macro RED - 1 saat cooldown"""
    key = "last_macro_red"
    now = datetime.now()
    last = st.session_state.get(key)
    if last and (now - last).total_seconds() < 3600:
        return
    if send_telegram(token, chat_id, "\U0001f534 *Macro RED* - islem iptal"):
        st.session_state[key] = now


def notify_daily_status(token, chat_id, plan):
    """Gunluk sabah plani - gunde 1 kez"""
    key = f"daily_status_{date.today().isoformat()}"
    if st.session_state.get(key):
        return
    capital = plan.get('capital', 0)
    price = plan.get('price', 0)
    d1 = plan.get('d1_levels') or {}
    lines = [
        "\U0001f4ca *v19 Sabah Plani*",
        f"Tarih: {datetime.now().strftime('%d.%m.%Y %H:%M')}",
        f"Sermaye: ${capital:.0f}",
        f"Fiyat: ${price:.2f}",
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

    price = snapshot.get('price', 0)
    overall = snapshot.get('overall', '?')
    channels = snapshot.get('channels', {})
    d1 = snapshot.get('d1') or {}
    h4 = snapshot.get('h4') or {}
    h1 = snapshot.get('h1') or {}
    final_level = snapshot.get('final_level', 'NONE')
    final_reason = snapshot.get('final_reason', '')

    emoji_map = {'GREEN': '\U0001f7e2', 'YELLOW': '\U0001f7e1', 'RED': '\U0001f534'}
    karar_emoji = emoji_map.get(overall, '\u26aa')

    lines = [
        f"\U0001f4ca *v19 Saatlik Ozet* {now.strftime('%H:%M')}",
        "",
        f"\U0001f4b0 Fiyat: ${price:.2f}",
        f"\U0001f3af KARAR: {karar_emoji} {overall}",
        "",
    ]

    # Kanallar
    ch_line = "Kanallar: "
    for name in ['JUMP', 'COT', 'GVZ', 'NEWS']:
        ch_val = channels.get(name)
        if isinstance(ch_val, tuple):
            color = ch_val[0]
        else:
            color = '?'
        ch_line += f"{name}{emoji_map.get(color, '\u26aa')} "
    lines.append(ch_line.strip())
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
            f"  R3: ${r3:.2f} ({r3-price:+.1f})",
            f"  R1: ${r1:.2f} ({r1-price:+.1f})",
            f"  S1: ${s1:.2f} ({s1-price:+.1f})",
            f"  S3: ${s3:.2f} ({s3-price:+.1f})",
            "",
        ]

    lines += tf_block("D1", d1, "(ana)")
    lines += tf_block("H4", h4, "(excluded - bilgi)")
    lines += tf_block("H1", h1, "(mikro)")

    lines.append(f"Confluence: {final_level}")
    if final_reason:
        lines.append(f"  {final_reason}")

    if send_telegram(token, chat_id, "\n".join(lines)):
        st.session_state[key] = now
