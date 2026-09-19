# Crypto four-metric trend rankings with rolling liquidity pools

## Result and scope

Implementation branch `codex/crypto-trend-pool`, based on `dcd9720`. No merge, push, production deployment, cron change, or Telegram delivery in this task.

The existing live `crypto_daily_rankings.run()` now routes to the new trend pipeline. For each of 7/14/30 days, the candidate pool is the intersection of EVERY complete UTC day's USDT quote-turnover Top100. Historical daily competitors include subsequently delisted contracts and their partial final trading day. Current non-tradable contracts cannot be final candidates. All three windows are ranked independently; pools need not contain 100 names and are never padded.

The four components are period return, log-price efficiency ratio, log-price/time regression R², and current drawdown from the window's highest close. Initial weights are 40/20/20/20, descriptive and not optimized. Percentiles use all valid measured pool members; the positive-return AND positive-slope gate applies after percentile computation. Missing price history remains unavailable; incomplete historical turnover prevents publication. New outputs contain no RS/Beta metrics. Historical dual-ranking helpers/runner remain explicitly named for replay regression; live CLI/shim route only to new reports. Other daily scanners are unchanged apart from the trend scanner's display label.

## Implementation responsibilities

Three configured DSH `deepseek-official/deepseek-flash` (DeepSeek-V41-Flash) calls: first two ended at per-response output caps before editing; third completed only the pure metric module and its initial 15 tests (RED then GREEN). Codex implemented historical catalog/collector, dynamic pools, report generation, routing, integration tests, and independent verification. No Codex subagents were used. User was informed of the failed delegation attempts and the narrowed/fallback workflow.

Main-thread review corrected DSH's near-constant variance floor using a reproducing 1e-10/bar case; metadata with missing classification now fails explicitly instead of silently dropping potential competitors. No unresolved review findings after fixes.

## Verification

- Baseline crypto suite: 75 passed.
- Final relevant suite, local Python 3.13: **123 passed in 1.11s**.
- Same 123 tests in isolated cloud `/tmp/crypto-trend-test.8Aa0Ur`, actual Python 3.10: **123 passed in 2.95s**, plus `py_compile` for all five changed runtime modules. No production file replacement or restart.
- Full local repository suite: **3693 passed, 12 failed, 4 skipped** in 263.98s. The same 12 failed node IDs were reproduced on a clean `dcd9720` worktree. They concern existing breadth snapshot inputs and morning-report expectations. Six subsequently added metadata/transport/send-failure tests are included in the final 123-test relevant suite; no repeat full run was necessary for those isolated additions.
- Python 3.10 AST parse and local `py_compile`: pass. `git diff --check`: pass.

Commands:
```
python -m pytest tests/test_crypto_trend_metrics.py tests/test_crypto_trend_market.py tests/test_crypto_trend_rankings.py tests/test_crypto_daily_rankings.py tests/test_crypto_relative_momentum.py tests/test_crypto_beta_scanner.py tests/test_beta_indicator.py -q
python -m pytest tests -q --tb=short --junitxml=work/crypto-trend-pool/verification/full-suite.xml
```

## Independent real-data reconstruction

Frozen reference: `reports/crypto_dual_pit/2026-09-07`, ending **2026-09-06 UTC**, not today's market. Existing catalog/archive/candidate-day evidence was reused. One bounded, public, read-only API request supplied ICP's missing 43 four-hour closes; no historical source files were changed.

An independent standard-library CSV/JSON + Decimal 45-digit oracle imports no producer calculation. Compared with the actual new catalog, collector, pool, scoring and rendering pipeline:

| Check | Result |
|---|---|
| Daily Top100 | 30 dates, 3000 entries, identical |
| Pools 7 / 14 / 30 days | 58 / 47 / 36, identical |
| Valid measured coin-windows | 141 |
| Uptrends 7 / 14 / 30 days | 49 / 20 / 35, identical |
| Component percentiles / composite scores / Top10 | Identical; composite max error 0 |
| Raw metric largest absolute error | 1.12e-14 (ER) |
| Verification live API calls / Telegram sends | 0 / 0 (ICP input acquisition noted separately above) |

Artifacts: `reports/crypto_trend/2026-09-19-verification/` contains three Markdown previews, full JSON, verification summary and independent-input hashes. Private execution logs and oracle programs are in `work/crypto-trend-pool/verification/`; DSH logs remain private under `work/crypto-trend-pool/run-*` and are excluded from the commit.

## Operational limits and next step

Historical eligibility uses current and retained exchange metadata plus official archive discovery. It is an evidence-based reconstruction, not original daily exchangeInfo vintages; the new reports say so. An archived active-window contract with unknown identity, unknown lifecycle/classification, an API failure, or an unexplained candidate-day gap blocks publication. An unlaunched PENDING contract is excluded only with successful empty kline and archive evidence.

The new module writes only its own report/evidence cache, reads the existing Quant daily cache, and reuses the configured scanner's retry/API conversion functions with serialized calls. New artifact names are `crypto_trend_top10_YYYY-MM-DD.json` and `crypto_trend_top10_{30,14,7}d_YYYY-MM-DD.md`. All artifacts are saved before the 30→14→7 message sequence; dry-run never sends, and any send failure propagates.

Deployment remains a separate action. The existing cloud shim already imports the unchanged `run` signature; after an authorized merge/deploy, verify fresh source coverage and a dry-run before allowing the next natural cron delivery. The copied daily wrapper label should also be updated during deployment. No automated follow-up was scheduled.

Cloud test-harness note: the first overlay test run imported baseline modules after repository imports changed sys.path. This caused 11 false failures. Temporary package-path anchors fixed source resolution; no production/source patch was needed. Always inspect traceback source paths when testing an overlay against a live repository.
