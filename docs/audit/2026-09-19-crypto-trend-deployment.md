# Crypto strict-majority trend rankings — production deployment

Boss authorized deployment on 2026-09-19. Implementation `cd42d4f` was merged into main as **d7cb0674acafbc16b1bff1d0366d2e169dfe5ccd**, pushed to origin and deployed to `/root/workspace/Finance`. Existing user edits in local CLAUDE.md and cio-b/CLAUDE.md were fingerprinted and verified unchanged by the merge. No tracked cloud edits existed.

## Live behavior

- Daily turnover membership: at least4/7,8/14,16/30 complete UTC days in USDT quote-turnover Top100. Full horizon denominators; separate dynamic pools.
- Four-metric ranking: return40%, log-ER20%, positive-direction regression R²20%, current-close drawdown20%. Schema2 records membership-day evidence. New output has no RS/Beta ranking.
- Existing Quant thin shim imports the unchanged `crypto_daily_rankings.run` interface, now routing to the new implementation. The daily runner label was atomically refreshed to Crypto趋势榜; its mode was preserved. Other scanner entries and `run_daily_scan.sh` are unchanged.
- Crontab remained byte-for-byte unchanged: `6 8 * * * /root/workspace/Quant/scanners/run_daily_scan.sh`, `CRON_TZ=Asia/Shanghai`. The next natural run is **2026-09-20 08:06 China time**, with trend messages ordered30→14→7 after the preceding serial scanners.

## Safety and backup

Deployment held `/tmp/quant-cron-locks/quant_daily_scan.lock` throughout backup, code update, testing, wrapper copy and production smoke. Backup: **`/root/workspace/Quant/backups/crypto-trend-20260919T070109Z/`**. It contains old Finance head dcd9720, a tracked-source archive, original Quant daily runner/shim/cron wrapper, crontab before/after, hashes, tests and production smoke evidence.

The new dedicated `results/daily_rankings/trend_cache` was seeded from this session's validated historical metadata, archive listings and100 fixed-endpoint 4h histories. The destination did not exist before deployment. Existing Quant daily cache, old ranking outputs, both Finance databases, secrets and service configuration were not replaced. No service restart was needed for the scheduled Python job.

If rollback is subsequently authorized, revert merge d7cb067 through the normal git workflow and restore the Quant daily runner from this backup under the same lock. The thin shim and cron wrapper are unchanged. The additional trend cache can remain as retained evidence; it is not consumed by the old pipeline.

## Acceptance evidence

- Production Python3.10 relevant suite: **132 passed in3.50s**; all changed runtime files passed `py_compile`.
- Real production Quant shim was imported from `/root/workspace/Quant/scanners/binance_beta_scanner.py`. All four Finance module source paths were asserted to be under `/root/workspace/Finance`, not the earlier temporary test overlay.
- The real entry was called with forced `dry_run=True` and a send function that raises if reached. Output artifacts were written to the real `/root/workspace/Quant/results/daily_rankings/` path. **Telegram sends:0**.
- Live source checks at07:02 UTC /15:02 China time reproduced the same **2026-09-19 08:00 cutoff**: 3000 daily Top100 rows and entire period report objects matched the independently verified snapshot, including pools, metric values, percentiles, scores, ties and unavailable rows.
- Pools7/14/30: **89/82/79**; valid prices **89/81/77**; positive trends **62/24/47**. The adapter reported23 source requests (metadata and daily-gap verification; fixed-endpoint prices reused the validated cache).
- Production shim and daily wrapper hashes match their versioned sources. Crontab before/after and cron-wrapper before/after comparisons passed. `deployment-status.txt` is PASS.

Local evidence copies: `reports/crypto_trend/2026-09-19-deployment/`. Full remote output/backup and private local deployment scripts/logs remain available. No manual Telegram message, new automation or extra scheduled follow-up was created; first natural delivery remains to be observed.
