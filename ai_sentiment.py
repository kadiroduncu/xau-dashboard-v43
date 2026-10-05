"""Gemini 2.5 Flash-Lite → news sentiment"""
import json
from google import genai
from google.genai import types

def make_client(api_key):
    if not api_key:
        return None
    return genai.Client(api_key=api_key)

def analyze(client, headline, summary=""):
    if not client:
        return None
    try:
        resp = client.models.generate_content(
            model="gemini-2.5-flash-lite",
            contents=f"""Analyze gold market impact of this news:
Headline: "{headline}"
Summary: "{summary[:400]}"

Return ONLY this JSON, nothing else:
{{
  "direction": "up" | "down" | "neutral",
  "magnitude": "small" | "medium" | "large",
  "confidence": 0.0 to 1.0,
  "reason": "one short sentence"
}}""",
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.2,
                max_output_tokens=200,
            )
        )
        return json.loads(resp.text.strip())
    except Exception as e:
        return {"error": str(e)[:100]}

def aggregate_sentiment(analyses):
    valid = [a for a in analyses if a and 'direction' in a]
    if not valid:
        return {"score": 0, "confidence": 0, "n": 0}
    mag_w = {'small': 1, 'medium': 2, 'large': 4}
    dir_v = {'up': 1, 'down': -1, 'neutral': 0}
    score = 0
    conf_total = 0
    for a in valid:
        w = mag_w.get(a['magnitude'], 1) * a.get('confidence', 0.5)
        score += w * dir_v.get(a['direction'], 0)
        conf_total += a.get('confidence', 0.5)
    return {"score": score / len(valid),
            "confidence": conf_total / len(valid),
            "n": len(valid)}
