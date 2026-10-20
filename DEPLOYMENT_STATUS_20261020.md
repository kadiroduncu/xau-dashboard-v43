# XAU deployment verification — 20 October 2026

## Working independent cloud profile

https://xau-v43-cloud.streamlit.app/ is running. Live UI verified a fresh Cloudflare check at 10:42:05 UTC, M5 reference-price monitoring, zones/candidate/history panels, and a WAIT state. This profile uses Twelve Data reference prices; it does not supply XM bid/ask, real account spread or position tracking. No broker execution is connected.

## Full analysis application

https://xau-dashboard-v43.streamlit.app/ loads the original full analysis panels and current reference quotes. Daily analysis is not fully available: the provider's 500-bar daily history contains conflicting OHLC rows at 2026-10-04T00:00:00. Preserving the data veto leaves only 16 bars after this conflict.

Repairs merged in PRs #8–#11 add bounded closed-date revalidation, rejection of ambiguous/new/invalid candidate prices, safe diagnostics and cache-version invalidation. Last live check: requesting 2026-10-04T00:00:00 through 2026-10-04T23:59:59 returned HTTP 200 with one row dated 2026-10-05T00:00:00. This cannot verify the conflicting October 4 candle. The daily-data veto remains. Trend, probability and support/resistance panels must not be called fully operational until consistent D1 data is supplied.

41 public-repository tests pass, including six conflict-resolution regressions. No trade gate was relaxed, synthetic price inserted, broker order submitted, or notification sent during this repair. Existing trading and risk parameters were not changed.
