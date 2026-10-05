# XAU Dashboard v43

Standalone Streamlit dashboard with configurable risk vetoes and paper setup tracking. No live broker execution is enabled.

Use Python 3.11. Install requirements.txt and run `streamlit run xau_dashboard.py`.

Configure TWELVEDATA_KEY, FINNHUB_KEY, FRED_KEY, and GEMINI_KEY in Streamlit Secrets. Never commit credentials. Provider endpoint permissions are required in addition to valid credentials.

Risk thresholds in risk_config.json and feed_config.json are starting parameters, not validated universal rules. Missing or stale required feeds block trading eligibility. Daily yield data cannot substitute for intraday yields; a price quote without bid/ask cannot establish an executable spread.

Paper setup logs use local SQLite storage. Streamlit Community Cloud local files are not a durable database. Export or externally persist observations before relying on a long-term dataset. MAE/MFE reflects sampled observations, not complete tick coverage. No large-loser probability model is represented as trained.
