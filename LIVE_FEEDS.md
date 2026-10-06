# Connected reference feeds

The v43 adapter now reads and records real market observations instead of leaving
`quote`, `dxy`, `yield2` and `yield10` permanently empty. No new keys, account login,
paid subscription or broker execution is introduced.

- XAU/USD bid/ask: Swissquote's public quote service, pinned to AT / AT / standard.
  Other profiles are never mixed. This is a reference quote, not an XM fill price.
- DXY / US 2Y / US 10Y: TradingView public TVC symbols DXY, US02Y, US10Y.
  The adapter checks exact symbols/types and requires streaming update mode.
  Minute-bar start timestamps are conservative freshness bounds, not invented tick times.
- Twelve Data remains the candle source. FRED remains daily macro context.

## Sampling and warm-up

The dashboard refreshes every 60 seconds. A lightweight 30-second Streamlit fragment
samples only the keyless XAU quote. It does not rerun candle calls, news or model panels.
Both schedules run only while the Streamlit session is active. Browser throttling,
sleep and provider failures can delay samples. There is no claimed unattended worker.
Twelve Data subscription quotas still apply and are shared with v42.

`data/market_quotes.sqlite` persists shared observations on the current server disk.
`XAU_QUOTES_DB` optionally overrides the path. Nothing is fabricated or backfilled.
A restart on ephemeral Streamlit storage can require another warm-up.

Spread uses 20 preceding distinct source timestamps, normally about 10 minutes of
active sampling. Repeat responses do not create extra history. Old/future quotes and
conflicting values at one source timestamp cannot seed a successful baseline.

Macro changes compare two simultaneously requested baskets about five minutes apart.
All three observations must be <=180 seconds old at both endpoints. The source times
remain separate from request times. The common `change_period_seconds` is the actual
elapsed time between the baskets (default accepted range 300–390 seconds), not a
fabricated provider timestamp. DXY changes are percent; yield changes are basis points.
No interpolation, daily change substitution, ETF substitution or futures substitution.

Until the baseline exists, values/source times display but cross-market approval is
blocked. Thereafter a genuine shock, stale observation, price/level condition or other
existing veto can still block. Connected data is not a promise of trade approval.
Public endpoint availability is checked at each refresh; there is no guaranteed SLA.

## Verification

Run the full local suite with `python -m unittest discover -s tests` and
`python backtest_risk_smoke.py tests/replay.example.jsonl`.
The public standalone adapter tests use `python -m unittest test_live_feeds -v`
with the accompanying synthetic `fixtures.py`.
The original v42 application is not changed.
