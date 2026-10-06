# XAU Dashboard v43 — risk gates / paper research

This branch is based on v42 commit `a8247d6`. Original desktop checkout and the separate
`v43_calibration` project are untouched. No live broker execution exists in the new code.
Telegram output is disabled in this review build, so running it does not send notifications.

## Run

Python 3.11+ and the existing requirements are required:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m streamlit run xau_dashboard.py
```

Use your existing `.streamlit/secrets.toml` configuration in this checkout (it is ignored by
Git). Required legacy names: `TWELVEDATA_KEY`, `FINNHUB_KEY`, `GEMINI_KEY`; `FRED_KEY` is optional.
Do not commit credentials. No keys were copied, printed, invented or requested by this change.
The original cloud entry `xau_dashboard_v28.py` remains a compatibility shim.

The EWS calculations are now packaged in `ews_calculations.py` and checked against
AST hashes of the desktop v18 source. No external `xau.py` or desktop path is needed.
JUMP uses shared closed M5 bars; COT uses CFTC's Disaggregated Futures Only data;
GVZ uses Cboe's published daily history (daily, not an intraday quote).
`feed_config.json` controls report-age limits. API failures remain RED with a source reason.

## Important current integration boundary

The v42 `/price` endpoint does not provide a verified quote observation time or bid/ask;
its FRED daily macro series are not intraday DXY/2Y/10Y quotes. They cannot honestly satisfy
a 90/180-second freshness check. No broker credentials or substitute symbols were guessed.

`XAU_RISK_SNAPSHOT=/absolute/path/risk_snapshot.json` connects the new engine to a normalized,
time-stamped feed. See `snapshot.example.json` and `SNAPSHOT_CONTRACT.md`. Without this feed,
v43 intentionally displays **WHY NOT TRADE**, records missing-data reasons, and blocks entry.
The example is historical synthetic test data; never relabel it as live data. Production
feed adapters are still required for bid/ask, synchronized cross markets and verified calendar
coverage. The dashboard is a review/paper build, not a completed live market-data service.

`XAU_RISK_CONFIG` optionally selects a complete config JSON. Defaults: `risk_config.json`.
`XAU_SETUP_DB` optionally selects the SQLite database; default `data/setups.sqlite`.
Keep that database on persistent storage; an ephemeral cloud disk cannot provide durable history.

## Decision and preservation contract

The v42 MASTER GATE, trend direction, SMA50 distance, five-day return, Hurst, macro,
JUMP/COT/GVZ/NEWS, Donchian veto, Camarilla/confluence, magnets and probability panel remain.
Tests compare core function ASTs and whole legacy modules against the baseline commit.

v43 can only tighten the existing gate. Its reasons feed the top MASTER GATE, confluence,
V10 and limit-plan panels. Only the evaluated direction receives approval. D1/H1 Camarilla
and legacy magnet levels are combined with the supplied obstacle map for room-to-TP.
An approved current setup does not certify future fills at a different pending-order level.
No order is submitted, canceled or closed by this build.

* HARD VETO: existing veto, suppression, unknown/red channels, missing/stale/future data,
  NFP/CPI/PCE/FOMC window, spread shock, M5 true-range shock, M1 return jump,
  adverse synchronized DXY and both yields, acceptance, unconfirmed rejection, blocked room,
  quality below A, or Tail Risk HIGH/EXTREME.
* Tier-1 window: inclusive T−45 through T+15 minutes. Post-event spread/volatility
  normalization is required through the configurable 120-minute watch period. Shock checks
  always remain active. US event-name matching does not depend on an unreliable impact label.
* **Existing v42 whole-day Tier-1 suppression is retained.** It can therefore block longer
  than the new timed window. v43 never clears it automatically; relaxing it is a separate
  behavior change requiring validation.
* M5 ATR20 and M1 rolling sigma exclude the current tested bar. Only consecutive closed
  bars are accepted. Spread history precedes the current quote and cannot use future data.
* Acceptance: configured count of M5 closes beyond the setup level plus buffer; rejection:
  recent touch followed by a close back on the favorable side. This is an initial rule,
  not a fully validated failed-retest classifier.
* Tail Risk: weighted, normalized heuristic in [0,100]. Missing critical inputs force 100
  as a conservative sentinel. It is **not a loss probability**.
* Trade Quality: six 0/2 components, total 0–12; A+/A/B/NO TRADE thresholds in config.
  Regime/macro/location evidence must be explicit upstream booleans; unknown earns zero.
* Kill rules: configured M5 structure acceptance, elapsed time, and volatility/news/data
  failures. These generate paper exits at the next valid observed quote only. Stale quotes
  never produce fictitious fills. The engine does not protect real broker positions.

## Logging and Large-Loser Probability

`setup_store.py` defines SQLite decisions, positions and observations. Decision snapshots
and config hashes are immutable, repeat ingestion is idempotent, and paper entry requires
an allowed decision plus the explicit UI button. One open paper position is allowed.
Bid/ask markouts include observed spread. MAE/MFE are in USD/oz price distance, not account P/L.
Exit target is stored at entry; changing TP configuration cannot move an existing target.

The large-loser label is `observed MAE >= configured large_loser_distance` at closing.
It is NULL while open. The default threshold is 30 USD/oz and is a research parameter.
The recorded path is **sampled quotes**, not full tick/bar extremes. There is no price
collection while the dashboard is closed; its legacy refresh is seven minutes, so missing
intermediate excursions must not be treated as zero. Complete event-path collection is
required before model training. No full-history MAE/MFE or calibrated probability is claimed.

The minimum sample/loss counts (500/50 by default) only indicate a first data-volume hurdle.
No model is fitted or activated, even above those counts. A future model must validate
coverage, independent setups, regime diversity, chronological train/test split, horizon
purging, calibration, costs and out-of-sample results. The UI returns no numeric probability.

## Probability TypeError analysis

`get_ohlc()` returns `None` when the provider response lacks `values`. The previous panel
unconditionally executed `_df_d1_v10['close']`; this deterministically raises
`TypeError: 'NoneType' object is not subscriptable`. Its broad exception handler hid the
message and traceback, leaving only `TypeError` visible. This failure path is reproduced
in a regression test. The original user's failing response/traceback was not available,
so it cannot be proved to be the only cause of that historical incident.

`probability.forecast_inputs()` now validates the frame, required history and finite,
positive numeric closes, accepts numeric strings, and returns an actionable missing-data
message. The EWMA/volatility equations are unchanged, verified to numerical tolerance.
No fake fallback probability is displayed. Unexpected errors now include their message.

## Verification

```sh
python -m unittest discover -s tests -v
python backtest_risk_smoke.py tests/replay.example.jsonl
```

Tests cover window boundaries, post-news normalization, both directions, stale/future data,
malformed inputs, acceptance/rejection, room-to-TP, cross-market shock, no lookahead,
legacy preservation, quote-based paper excursions, kills, logging and full offline Streamlit
startup. HTTP responses in the UI test are mocked; no API keys or outbound messages are used.

The replay command is a chronological gate smoke test, **not a profitability backtest**.
Available desktop D1/H1 history lacks simultaneous historical spread/M1/cross-market/news
snapshots. No v43 win rate or edge is inferred from it. See `TEST_RESULTS.md` for actual runs.


GitHub reconciliation: current main `812fbb17bb12eb83a0f71590139a2f5fc091d74c` was downloaded through the authorized Chrome session. Its JSON export, price-fetch metadata, read-only mode, read_signal.py and Streamlit static routing are preserved. The offline UI test also verifies exported RED gate, empty allowed directions and all three probability bands. CLI access remains unavailable; browser publication uses the existing GitHub session.


## Cloud feed repair (2026-10-02)

All D1 panels share one 500-bar request, H1 panels one 200-bar request; M1/M5
are cached by timeframe. This removes duplicate size-specific requests that could
exhaust quota before the probability panel. Provider error categories are visible
without exposing response bodies or keys. The legacy analytics dependencies
(hmmlearn, scikit-learn, arch, yfinance, pytrends) are included. Third-party service
availability can still limit those context panels. Missing D1 data no longer displays
V10 as safe/ready. Missing risk feeds display “Veri eksik”, not a measured 100/100 risk.

For a normalized external live feed on Streamlit Cloud, configure
`XAU_RISK_SNAPSHOT_URL` in Streamlit secrets (HTTPS) and optionally
`XAU_RISK_SNAPSHOT_TOKEN` (Bearer). The schema in SNAPSHOT_CONTRACT.md still applies.
Only a configured URL is used; no provider, symbol, account or token is inferred.
Redirects are not followed, errors are generic, snapshots must be current, and
no broker order endpoint exists. A configured but failing source is never replaced
with made-up or partial executable prices. Local file configuration takes precedence.

Source references: [CFTC COT](https://publicreporting.cftc.gov/stories/s/r4w3-av2u),
[Cboe daily GVZ](https://www.cboe.com/tradable_products/vix/vix_historical_data),
[Twelve Data history/caching](https://support.twelvedata.com/en/articles/5656039-how-to-get-historical-prices).

## Partial analysis diagnostics

When an executable quote or cross-market feed is absent, the dashboard now keeps
available M1 jump and M5 TR/ATR measurements visible, reports incomplete risk coverage,
and displays the legacy direction/filter result separately from v43 approval.
Acceptance remains an explicit veto even if no quote exists. Indicative obstacle
distance uses a fresh closed M1 price, is labelled as chart analysis, and never creates
an executable entry, paper fill, or trading approval. Daily macro feeds remain daily.

Verification of this repair: 42 unit/integration tests passed, including the full
mocked dashboard. The one-record replay smoke test passed; it does not measure profit.

Timeframe refresh intervals are configurable in feed_config.json: D1 one hour, H1 thirty minutes, H4 two hours; M1/M5 one minute. Original market observation timestamps are retained. Live provider verification returned Twelve Data HTTP 429 with a daily quota message, while CFTC/Cboe succeeded. Existing v42 and v43 share the same provider quota. No quota or account plan was changed.

## Official calendar fallback (2026-10-06)

Finnhub calendar authorization failures now fall back to free official schedules:
BEA Personal Income and Outlays (PCE), Federal Reserve FOMC meetings/press conferences/minutes,
and FRED release IDs 50 (BLS Employment Situation) and 10 (CPI), using existing `FRED_KEY`.
Sources: https://www.bea.gov/news/schedule , https://www.federalreserve.gov/newsevents/calendar.htm ,
https://fred.stlouisfed.org/docs/api/fred/release_dates.html . No new subscription is needed.

The fallback covers scheduled NFP/CPI/PCE/FOMC only, not all economic news or unscheduled events.
FRED provides dates, not clock times: these events are explicitly date-only, blocking the entire
America/New_York day plus the configured pre/post news buffer. No 08:30 time is fabricated.
BEA timestamps retain their published UTC offsets; Fed times use US Eastern daylight-saving rules.
Each source must return a schedule bracketing the requested window; errors, truncation, expired
schedules and missing FRED keys remain incomplete and block approval. Refresh is cached for 5 minutes.
The unchanged v42 NEWS function receives its expected UTC format via an adapter; the risk engine
retains timezone-aware timestamps. Spread and intraday cross-market checks still require measured
inputs and are not marked successful merely because the calendar is available.

Validation: `python -m unittest discover -s tests -v` and
`python backtest_risk_smoke.py tests/replay.example.jsonl` (replay sanity only, not profitability).

## Logging and partial-analysis repair (2026-10-06)

NumPy boolean values from Hurst/location comparisons were not JSON serializable and did not
pass `is True` quality checks. The local adapter now emits native booleans; the audit writer
also converts nested NumPy scalars. Non-finite measurements (for example a jump after a flat
baseline) retain explicit `nonfinite` tags in stored JSON rather than crashing or becoming zero.
Risk decisions are not relaxed by serialization. Storage failures show their exception category
and write a server traceback; no failed write is reported as a successful record.

Acceptance/rejection and level validation run independently of executable bid/ask availability.
Missing bid/ask is reported as an entry-quote issue, not a broken level map. An unmeasured
room-to-target receives zero quality credit. Incomplete data no longer adds a misleading
measured-high-tail-risk reason, while full trade approval still remains blocked.

58 local tests passed, including five new regression tests covering native/NumPy values,
infinite jumps, missing quotes, repeated Streamlit runs and failed storage. The public repo's
standalone regression can be run with `python -m unittest test_logging_repair -v`.
