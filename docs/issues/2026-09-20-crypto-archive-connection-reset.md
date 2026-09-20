# Crypto trend cron: Binance archive connection reset

## Incident and evidence

- On 2026-09-20, `quant_daily_scan` exited 1 at 08:09:28 China time. PMARP, RVOL and BTC NUPL completed; the new Crypto trend stage aborted with `ConnectionResetError(104, 'Connection reset by peer')`.
- Production log: `aliyun:/root/workspace/Quant/logs/scan_20260920.log`. Code: `7cefd71` (implementation merge `d7cb067`).
- The trend stage started at 08:08:53. Its dedicated archive cache contains 24 successful XML pages for as-of 2026-09-19, last written 08:09:26. The corresponding catalog and ranking artifacts were absent. Catalog construction had therefore not completed.
- `TrendMarket.catalog()` fetches exchange metadata through Quant's existing JSON retry helper, then audits historical Binance S3 listings. `archive_keys()` used a single direct `requests.get()` without retries. A transport reset in this audit aborts the entire report, correctly preventing incomplete membership evidence from being published.
- A read-only probe of the same S3 listing endpoint later returned HTTP 200, 88,778 bytes, in 0.51 seconds. This supports transient transport failure; the original wrapper did not retain a traceback, so the exact failing prefix is unknown.

## Repair

Bounded change in the existing data-layer adapter, aligned with `docs/design/north-star.md` first-layer data reliability. Retry the identical archive page up to three total attempts on connection errors, timeouts, and HTTP 429/500/502/503/504, waiting 2 then 4 seconds. Preserve the existing 30-second timeout and request pacing. Log attempt, prefix and marker; retain the original exception as the cause when exhausted.

Permanent HTTP errors and malformed XML still fail. Cache only validated XML through the existing atomic write. Never replace failed archive evidence with an empty listing or stale result. Ranking definitions and Telegram delivery code are unchanged. Quant's existing JSON helper cannot be used directly because this endpoint returns XML.

## Validation and delivery status

- TDD before implementation: **10 failed, 15 passed**, including the exact connection-reset class.
- Relevant local Python 3.13 suite: **126 passed in 1.23s**; `py_compile` passed.
- Same suite in cloud Python 3.10 isolated worktree: **126 passed in 3.59s**; `py_compile` passed.
- Tests cover recovery, same-page retry, bounded exhaustion, preserved exception, no failed cache, permanent HTTP failure, and successful cache reuse. Existing ranking tests cover stop-before-publish and dry-run delivery boundaries.
- Main-thread `/cr` review: no actionable findings; `git diff --check` passed.
- Cloud full dry-run **PASS (exit 0)** under the existing Quant job lock, in `/tmp/finance-crypto-archive-retry-20260920`, using a copy of the dedicated trend cache and read-only shared Quant inputs. Telegram was replaced with a function that raises if called; **0 sends**. As-of 2026-09-19 means complete UTC data through 2026-09-20 08:00 China time.
- Output verification: 30 daily Top100 lists / **3,000 rows**; three Top10 reports. Pools 7/14/30 = **90/85/81**, valid prices **90/84/79**. Existing unavailable rows remain explicit: PONSUSDT (14d), MARSCOINUSDT and 牛来USDT (30d). No missing-price substitution or universe padding. 127 exchange API helper calls; serial pacing retained.
- Local dry-run evidence: `reports/crypto-trend-incident-20260920/` in the fix worktree (JSON, three Markdown reports, log and verification summary). Production source and cron were not changed, and today's production output files remain absent.
- Production code and cron remain unchanged. Deployment and actual catch-up delivery are pending Boss approval.

## Lesson

Validate resilience of each newly introduced upstream dependency, including XML archive audits. Successful happy-path deployment does not cover transient transport failures. Preserve fail-closed data checks while making safe GET retries local to the failing page.
