# Weekly composite Top20 — deployed 2026-09-20

Runtime `6adc3d4` is merged into main, pushed and deployed in `/root/workspace/Finance`. User approved making the historical workbook's equal-weight RVOL/turnover ranking the recurring weekly Telegram report.

## Schedule and output

- Reuse the existing single `0 9 * * 1 /root/workspace/Quant/scanners/run_weekly_scan.sh` line under `CRON_TZ=Asia/Shanghai`. Cron service confirmed active. No additional app automation or duplicate cron created.
- Every run sends only the most recent fully closed Binance week, Monday00:00UTC exclusive cutoff (Beijing Monday08:00). Next natural run: 2026-09-21 09:00 Beijing, covering September14–20 UTC.
- Current trading COIN USDT perpetuals, latest-week USDT turnover Top100. Valid RVOL requires 53 complete weeks; current vs preceding52, population std. Delisted contracts excluded as previously approved.
- Composite `50*(N-r)/(N-1)+50*(100-v)/99`, where N counts valid RVOL members, r ranks their RVOL descending, and v is turnover rank in the FULL Top100 including RVOL-unavailable members. N=1 gets RVOL component100, N=0 produces no entries. No fabricated scores, positive-RVOL gate, or outside-pool fill.
- Select Top20 by composite; exact score ties favor higher RVOL, then turnover/symbol. Integer numerator ordering avoids floating tie noise. Telegram rows retain signed raw RVOL and show score, turnover and turnover rank.
- This replaces the existing RVOL message. Existing Fisher and 30/14/7-week trend messages remain. Daily rankings and weekly trend weights remain unchanged. Schema3 identifies changed RVOL rank semantics; trend scoring remains2, RVOL composite scoring1.

## Acceptance

- DSH: one implementation invocation (DS4.1 route), 27 steps, completed. Eight new behavior tests failed before implementation. Codex performed a single main-thread diff review; no blocking findings and no reviewer subagents.
- Local and isolated-cloud command: `python -m pytest -q tests/test_crypto_weekly_report.py tests/test_crypto_trend_rankings.py tests/test_crypto_trend_market.py tests/test_crypto_trend_metrics.py` => **146 passed**, respectively6.18s/12.73s. Python compile and git whitespace checks passed.
- Independent historical verifier compared runtime output against the approved workbook dataset: **155 weeks / 3,100 Top20 records, identical symbols/order/score/raw RVOL**.
- Isolated cloud and production previews used real existing526-contract caches plus one exchangeInfo request, under the shared Quant lock. Latest completed week ended2026-09-14. Both match the workbook; latest Top100, Fisher and trend outputs exactly match the accepted prior production report.
- Delivery path captured all5 Telegram messages without sending, lengths1523/336/1058/1054/1043 (all below3900). Telegram credentials are configured. No manual historical sends during rollout. First natural scheduled delivery remains unobserved.
- Production source matches tested cloud staging byte-for-byte. Crontab before/after is identical. Weekly shim/wrapper, daily shim/wrapper and daily ranking source SHA256 unchanged; existing local CLAUDE edits also preserved by hash.

## Evidence and rollback

- Production backup: `/root/workspace/Quant/backups/crypto-weekly-composite-20260920/` with old/new heads, original runtime archive, cron snapshots, unchanged-file hashes, production preview, verification.json and PASS marker.
- Isolated verification: `/tmp/crypto-weekly-composite-20260920/`; source worktree `/root/workspace/.worktrees/finance-crypto-weekly-composite/`.
- Local task evidence: `/Users/owen/CC workspace/.worktrees/finance-crypto-weekly-composite-cron/work/weekly-composite/` (DSH result, historical verifier, cloud preview and deployment script).
- Revert runtime commit6adc3d4 through the normal reviewed deployment path to restore RVOL-only selection. Existing cron requires no rollback edit.
