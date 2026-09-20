# Crypto weekly report

Status: implemented in codex/crypto-weekly-report; NOT deployed. Historical coverage gate blocks live rollout. See docs/issues/2026-09-20-crypto-weekly-historical-coverage.md.

## Schedule and entry

Keep existing daily08:06 job. Intended new line under existing CRON_TZ=Asia/Shanghai:

```cron
0 9 * * 1 /root/workspace/Quant/scanners/run_weekly_scan.sh
```

Versioned files: scripts/quant/run_weekly_scan.sh and scripts/quant/weekly_scan_all.py, installed in Quant/scanners. Shim imports sibling Finance/scripts/crypto_weekly_report.py. Wrapper exports Quant/.env, uses the SAME /tmp/quant-cron-locks/quant_daily_scan.lock with1800s finite wait, logs Quant/logs/quant_weekly_scan.log, alerts/exits nonzero on failure. No service restart needed.

Outputs: Quant/results/weekly_report/crypto_weekly_<MondayUTC>.json and numberedMarkdown messages; raw cache under weekly_cache/. Daily catalogs are read-only inputs; daily outputs/cache not modified. Five messages in order: RVOLTop20, Fisher9 crossing counts/names/percentages,30w/14w/7w trendTop10. A long message may split.

## Validation

```bash
python3 -m scripts.crypto_weekly_report --scanner-dir /root/workspace/Quant/scanners --output-dir /tmp/crypto-weekly-check --as-of 2026-09-13 --dry-run
```

Explicit as-of must be a completed UTC Sunday; default derives most recent closed week. Only public marketdata and own artifacts are involved. dry-run must never send Telegram. Entire report is built and saved before first send. Current implementation does not deduplicate successful manual replays; rerun default with dry-run when inspecting.

RVOL needs53completeweeklybars, excludes current from52mean/std. Fisher9 uses weeklyHL2,Trigger=Fisher[1], reports NEW crossings only; all percentages divide by selectedTop100, not merely validhistorycount. Unknown remains explicit. Trend pools independently require4/7,8/14,16/30weeklyTop100 memberships, prices are daily1dcloses.

## Rollout gate and rollback

1. Resolve every historical-universe metadata/turnover gap over210days. Do not hand-edit unknown volume tozero or omit delisted competitors. Current AERGOterminalday remainsUNKNOWN; policy decision pending.
2. Run focused tests, Python3.10 compile, bash-n, full real dry-run. VerifyTop100/membership/metrics independently. Review diff before installing.
3. Under shared Quant lock back up Finance commit, both old/new shim/wrapper paths and crontab. Install only new weekly files; chmod+xwrapper. Preserve existing daily files and cron line.
4. Export crontab tofile, edit exact weeklyline once, installfromfile, reread/verify. Never pipefilteredoutput directly into crontab. Avoid duplicatejobs. Record hashes, tests,previouscommit and backup path.
5. Production dry-run must produce same checked result without send. First naturalrun is separate from implementation success.

Rollback: remove only the added weeklycron line via export/edit/install/verify, restore backed-up weeklyfiles or retain them unused, revert the associated Finance code throughgit if required. Preserve artifacts and every existingcronentry. No restart.
