# PMARP default migration review and validation

Status: implemented in `feat/pmarp-defaults-30-120`, pending merge and coordinated deployment. No live changes.

## Changed behavior

Active scanner, timing, research/backtest, pipeline and Forge defaults now use EMA30 / previous120 observations. Shared Finance constants live in `src/indicators/pmarp.py`. Existing explicit historical overrides remain valid; historical outputs, frozen research branches and `_legacy_reference` are unchanged. BBWP/RVOL/trend EMA and crossover thresholds unchanged.

Pipeline effective PMARP parameters are resolved into the spec/hash and saved as `resolved_spec.json`. Crypto breadth's report metadata/history labels follow the new values; historical messages render their stored parameters. OHLCV caches remain reusable. Quant's independent source needs the prepared three-line migration at coordinated rollout.

## Verification

- Failing-first tests demonstrated old20/150 defaults, then passed for30/120 and unchanged unrelated parameters.
- Expanded relevant regression: **721 passed, 1 skipped, 5 failed**. Five failures are morning concept-category tests in a checkout without concept registry data. All five were reproduced on a clean archive of unmodified main `df1778a7` with the same missing local data; no new regression among these failures.
- Isolated migration/core PMARP/breadth/cache tests:63 passed; Quant migration tests:4 passed.
- Backtest/Forge/pipeline/factor suites initially169 passed; reviewer then caught a Forge parameter-key mismatch. A new test of the actual parameter-mode validator reproduced it; fix retained the original manifest key set and the relevant40 tests passed. The expanded721 pass run includes that fix.
- `git diff --check` passed. Active-default search finds no unintended PMARP20/150 defaults; remaining numeric150 belongs to other indicators or immutable legacy OHLCV import compatibility.
- Two agents: one implemented backtest migrations; one independently inventoried local/cloud sources and reviewed the full diff. No remaining blocking review findings.

## Cloud dry-run (read-only)

Quant source `/root/workspace/Quant/scanners/binance_pmarp_scanner.py`:
- before SHA256:2759f2f23bedf4ce72e2e797adb0d3f03e6055eb8ebf971fd2eea7555859f8f4
- proposed SHA256:371785c1d0298bb57878add9467b67a1710cde6bbb73da27ef60fc802b3a75a8
- changed lines:3; proposed EMA30/lookback120; status:ready.

The tool was executed on the actual cloud source without `--apply`: no writes, imports of the scanner, API calls or Telegram sends. Cloud Finance remains main@df1778a7; Quant remains20/150 until rollout. Deployment/rollback steps are in `docs/runbooks/pmarp-defaults-30-120.md`.

Unrelated existing `terminal/freshness.py` Timing-refresh interface issue was noted during inventory and not changed. This migration does not claim to fix that UI refresh path.
