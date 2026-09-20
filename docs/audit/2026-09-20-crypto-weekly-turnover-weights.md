# Crypto weekly turnover-weighted trend score — 2026-09-20

Boss approved a weekly-only trend score change. Daily rankings/messages,
RVOL52, Fisher9, the cron line, the shim and all artifact routes are untouched.

## Approved scheme

- Weekly trend weights: return 50% / ER 15% / R² 15% / drawdown 10% /
  window turnover 10%.
- `quote_volume` = total USDT quote turnover over EACH ranking's matching
  7/14/30-week horizon `[cutoff - 7*weeks days, cutoff)`. Higher is better.
  The initial price baseline bar (`cutoff - 7*weeks - 1 day`) and the current,
  still-open week are excluded.
- Percentiles use the same eligible valid pool as before
  (`100 * count(values <= current) / valid_count`, drawdown reversed and the
  other four positive). Valid downtrends remain in the denominator; the
  direction gate stays `return > 0 and slope > 0` and is applied after scoring.
- Daily weights stay 40/20/20/20 with no turnover component.
- Weekly `schema_version` bumped 1 → 2 (report also carries
  `scoring_version=2` and the approved `weights`).

## Evidence and safety

- Turnover reuses the raw daily frames already fetched by `build_report`; no
  additional `fetch`/`fetch_daily` call is introduced.
- Missing/duplicate/stale/NaN/negative volume marks the row unavailable, or
  fails the whole non-empty pool closed when no valid member remains; numeric
  zero is valid. There is no zero or old-weight fallback.
- Focused suite `tests/test_crypto*.py tests/test_fisher_indicator.py
  tests/test_rvol_sustained.py`: 234 passed. Python 3.10 AST grammar and
  `py_compile` pass for the changed modules and tests.
- Test-first RED is saved in `work/crypto-turnover/red.txt`; green focused
  output is saved in `work/crypto-turnover/focused.txt`.

The 2026-09-20 deployment acceptance evidence (weekly trend 40/20/20/20) is
preserved unchanged in `docs/audit/2026-09-20-crypto-weekly-report.md`. This
note supersedes only the weekly trend weighting, not the current-universe
scope, the schedule or the RVOL/Fisher sections.

## Codex acceptance before rollout

Local focused suite: 234 passed in 6.23s. Cloud Python 3.10: 234 passed in 13.16s. A frozen daily fixture produced byte-identical JSON and all three daily messages before and after the change.

Independent standard-library validation checked all 208 valid coin-window turnover sums, five percentiles, weighted scores and final rankings. Weekly pools, membership counts, signed returns, ER, regression metrics, drawdowns, RVOL and Fisher match the prior production report exactly. Only weekly scores/ranks and the new turnover component change. Verified preview: reports/crypto_weekly/2026-09-20-turnover-score/preview.md. The validation run reuses cached raw inputs and adds no data endpoint beyond the existing metadata request.

## Production rollout

Runtime commit `26c96e9` was merged, pushed and deployed under the shared Quant lock on 2026-09-20. Backup: `/root/workspace/Quant/backups/crypto-weekly-score-20260920T035328Z/`, including the old runtime and previous weekly JSON/Markdown before schema-2 output replaced it.

Production focused tests: **234 passed in 13.09s**. Production dry-run and the independent 208 coin-window turnover/percentile/score/rank verification passed. Prior and new daily fixture JSON/messages are byte-identical; RVOL and Fisher are exactly unchanged. Crontab and all four daily/weekly entrypoint hashes are byte-identical before/after. No manual messages were sent.

Final full suite: **3806 passed, 12 failed, 4 skipped**, 17 warnings in 263.46s. All 12 failure IDs match the earlier baseline; no new failures. Seven depend on missing local research snapshots/price samples, five concern morning-report classification expectations. They were not modified in this task.

DSH handled one implementation invocation for this adjustment. Codex reviewed the code, corrected period/availability wording, verified the results independently and completed rollout. No additional subagents. Evidence: `reports/crypto_weekly/2026-09-20-turnover-score/deployment/`.
