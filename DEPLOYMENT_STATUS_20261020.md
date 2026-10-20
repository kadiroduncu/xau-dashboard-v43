# XAU deployment verification — 20 October 2026

## Working independent cloud profile

https://xau-v43-cloud.streamlit.app/ is running. Live UI verified a fresh Cloudflare check at 10:42:05 UTC, M5 reference-price monitoring, zones/candidate/history panels, and a WAIT state. This profile uses Twelve Data reference prices; it does not supply XM bid/ask, real account spread or position tracking. No broker execution is connected.

## Full analysis application

https://xau-dashboard-v43.streamlit.app/ loads the original full analysis panels and current reference quotes. Daily analysis is not fully available: the provider's 500-bar daily history contains conflicting OHLC rows at 2026-10-04T00:00:00. Preserving the data veto leaves only 16 bars after this conflict.

Repairs merged in PRs #8–#11 add bounded closed-date revalidation, rejection of ambiguous/new/invalid candidate prices, safe diagnostics and cache-version invalidation. Last live check: requesting 2026-10-04T00:00:00 through 2026-10-04T23:59:59 returned HTTP 200 with one row dated 2026-10-05T00:00:00. This cannot verify the conflicting October 4 candle. The daily-data veto remains. Trend, probability and support/resistance panels must not be called fully operational until consistent D1 data is supplied.

## Additional repairs at 14:40 Istanbul time

The missing-data direction gate now blocks both directions instead of displaying "both directions allowed". Both the inline dashboard gate and shared trend module are covered by a regression.

A second, full-date-window request can verify a broken single-date response only when its complete date coverage and every price match the original candidates. Missing dates, changed prices and continuing duplicates are rejected; histories are never joined across the conflict.

The live application subsequently reported Twelve Data daily quota exhaustion. This prevents immediate validation of the full-window fallback. A shared cooldown across all timeframes now stops repeated requests after a quota response; daily exhaustion retries after the next UTC midnight (03:00 Istanbul), and a per-minute limit retries after the next minute. Account identifiers are hashed in memory; no credential is written to diagnostics.

50 public-repository tests pass, including complete-history recovery, missing-date veto, price consistency, missing-direction gates and quota reset/isolation regressions. No trade gate was relaxed, synthetic price inserted, broker order submitted, or notification sent. Full daily analysis remains externally blocked until the quota resets and consistent D1 history is available. The fallback is tested but has not been demonstrated against the live provider because its quota is exhausted.


## Final cloud display hardening (20 October, approximately 15:00 Istanbul)

The independent cloud screen now validates the latest M5 candle independently of the worker heartbeat. A fresh check with stale prices is an explicit data error, not an ordinary WAIT. Invalid/future/duplicate candle timestamps, malformed OHLC and inverted or non-finite signal levels cannot display a green candidate. The latest closed M5 timestamp is shown in Istanbul time.

57 repository tests pass. Live verification confirmed the cloud screen shows a new check with the last M5 at 14:35 Istanbul and explicitly rejects stale prices. The full analysis screen confirms the provider daily quota is exhausted across timeframes. Neither screen is fully operational for fresh XAU analysis while that external outage remains.

Operational dependencies still open: sufficient Twelve Data quota plus consistent daily history; independent-cloud reference prices are not XM account data. The dashboard validates provider recovery on its normal refresh; a healthy response must still pass the data gates. No service purchase, broker connection or order was made.
