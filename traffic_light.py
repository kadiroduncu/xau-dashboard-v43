"""4-kanal traffic light"""
from datetime import datetime

def news_channel(calendar_events, news_sentiment):
    now = datetime.utcnow().replace(tzinfo=None)
    for e in calendar_events:
        try:
            t_str = e.get('time', '')
            event_time = datetime.strptime(t_str, '%Y-%m-%d %H:%M:%S')
            delta_min = (event_time - now).total_seconds() / 60
            if -30 < delta_min < 15 and e.get('impact') == 'high':
                return ('RED', f"{e.get('event','?')} ({delta_min:+.0f}dk)")
            if -30 < delta_min < 15 and e.get('impact') == 'medium':
                return ('YELLOW', f"{e.get('event','?')} ({delta_min:+.0f}dk)")
        except Exception:
            continue
    if news_sentiment and news_sentiment.get('n', 0) > 0:
        score = news_sentiment.get('score', 0)
        conf = news_sentiment.get('confidence', 0)
        if abs(score) > 1.5 and conf > 0.6:
            dir_str = "yukari" if score > 0 else "asagi"
            return ('YELLOW', f"Yuksek sentiment {dir_str} (n={news_sentiment['n']})")
    return ('GREEN', 'Clear')

def combined_signal(channels):
    colors = [v[0] for v in channels.values()]
    if 'RED' in colors:
        return ('RED', 'TRADE YOK')
    if colors.count('YELLOW') >= 2:
        return ('YELLOW', 'Dikkat - lot azalt')
    if 'YELLOW' in colors:
        return ('YELLOW', 'Hafif dikkat')
    return ('GREEN', 'Trade OK')
