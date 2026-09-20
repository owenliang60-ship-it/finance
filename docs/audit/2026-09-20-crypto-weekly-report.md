# Crypto weekly report — deployed 2026-09-20

Runtime commit 4632cd7 is merged into main, pushed and deployed. Weekly reports use currently tradable Binance COIN USDT perpetuals; Boss explicitly excluded delisted contracts. Existing daily reports retain their universe and schedule.

## Live behavior

- Monday 09:00 Asia/Shanghai: `0 9 * * 1 /root/workspace/Quant/scanners/run_weekly_scan.sh`.
- Last closed Binance week ends Monday 00:00 UTC / 08:00 Beijing. Latest completed-week quote-turnover Top100 selects RVOL Top20 (previous 52 weeks, population sigma) and Fisher9 new Trigger crossings, with a fixed denominator of 100.
- Trend windows are 7/14/30 weeks using daily closes, strict-majority 4/8/16 weekly Top100 memberships, the same four metrics and 40/20/20/20 weights, and Top10 per window.
- Every weekly ranking excludes current delisted, SETTLING and PENDING contracts. Current-contract data gaps still fail explicitly.
- Shared daily Quant lock, finite 1800-second wait, own log and failure alert. Artifacts are saved before five ordered messages. No manual Telegram sends during rollout.

## Acceptance

- Codex independently ran 217 focused tests locally. Isolated cloud: 217 passed. Production: 217 passed in 12.16 seconds. Python 3.10 compile and bash syntax checks passed.
- Final full suite: 3789 passed, 12 failed, 4 skipped, 17 warnings in 260.48 seconds. All 12 failure IDs match the prior baseline: seven missing local research-data/price fixtures and five morning concept-classification expectations. Details: `reports/crypto_weekly/2026-09-20-deployment/full-suite-summary.json`.
- 526 current contracts fetched serially, 527 prefetch requests, zero failures. An independent standard-library verifier reproduced 3000 weekly Top100 records, 1040 metric values, percentiles, pool memberships and every Top10 rank.
- At cutoff 2026-09-14, RVOL had 77 valid members. Fisher had 96 valid members, with two new upcrosses and five downcrosses: 2% and 5% of the fixed 100-member pool.
- Pools for 7/14/30 weeks: 78/73/60; valid price histories: 77/72/59; positive trends: 60/41/20. Incomplete histories remain unavailable, without filling or replacement.
- A real Linux wrapper fixture verified lock timeout exit 75, subsequent successful exit 0, and environment export. Telegram variables were blank, so no alert delivery occurred.
- A production dry-run used the real Finance module and Quant adapter. The independent verifier checked the production raw cache and saved exchangeInfo snapshot again.

## Deployment evidence

Backup: `/root/workspace/Quant/backups/crypto-weekly-20260920T032749Z/`. It contains the old head 7bbdad6, prior changed runtime archive, crontab before/after, daily hashes before/after, tests, production preview, verification and PASS status.

Exactly one Monday 09:00 line and its comment were added; every previous crontab byte was retained. The SHA256 hashes of `run_daily_scan.sh` and `daily_scan_all.py` are unchanged. New wrapper and shim match their versioned sources. Existing user edits in CLAUDE.md and cio-b/CLAUDE.md were preserved by hash checks. No service restart or additional automation was created.

First natural run: 2026-09-21 09:00 Beijing, not yet observed. All acceptance runs were dry-runs. Preview: `reports/crypto_weekly/2026-09-20-current-only/preview.md`. Production evidence: `reports/crypto_weekly/2026-09-20-deployment/`.

## Implementation provenance

DSH used three invocations across the task. Initial implementation and correction reached their 40-step limits. The later user-approved current-only scope completed in 50 steps with a 60-step allowance. Codex reviewed the actual changes, completed corrections and documentation, and verified the result independently. No additional subagents were spawned.

The AERGO historical-coverage issue remains documented as dated evidence. The user's current-universe choice superseded that blocker; no missing-volume fallback was introduced.
