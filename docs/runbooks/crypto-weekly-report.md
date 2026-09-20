# Crypto weekly report

Status: implemented in codex/crypto-weekly-report; NOT deployed; live isolated smoke and independent verification PASS. Weekly universe is **current-tradable-only** (2026-09-20 user decision 「下架的就不需要了」): only Binance COIN USDT PERPETUAL `status=TRADING` with `onboard ≤ cutoff < delivery`, replayed over the 7/14/30 historical weekly Top100. Delisted/SETTLING/PENDING/historical-only contracts are excluded from all rankings/indicators/trend pools; this is not an all-market point-in-time snapshot. The earlier historical-coverage gate is superseded for the weekly report; dated evidence remains in docs/issues/2026-09-20-crypto-weekly-historical-coverage.md. Daily historical-universe behavior is unchanged.

## Schedule and entry

Keep existing daily08:06 job. Intended new line under existing CRON_TZ=Asia/Shanghai:

```cron
0 9 * * 1 /root/workspace/Quant/scanners/run_weekly_scan.sh
```

Versioned files: scripts/quant/run_weekly_scan.sh and scripts/quant/weekly_scan_all.py, installed in Quant/scanners. Shim imports sibling Finance/scripts/crypto_weekly_report.py. Wrapper exports Quant/.env, uses the SAME /tmp/quant-cron-locks/quant_daily_scan.lock with1800s finite wait, logs Quant/logs/quant_weekly_scan.log, alerts/exits nonzero on failure. No service restart needed.

Outputs: Quant/results/weekly_report/crypto_weekly_<MondayUTC>.json and numberedMarkdown messages; raw cache under weekly_cache/. The weekly path does NOT read daily catalogs and does NOT audit the removed-contract archive; daily outputs/cache are not modified. Five messages in order: RVOLTop20, Fisher9 crossing counts/names/percentages,30w/14w/7w trendTop10. A long message may split.

## Validation

```bash
python3 -m scripts.crypto_weekly_report --scanner-dir /root/workspace/Quant/scanners --output-dir /tmp/crypto-weekly-check --as-of 2026-09-13 --dry-run
```

Explicit as-of must be a completed UTC Sunday; default derives most recent closed week. Only public marketdata and own artifacts are involved. dry-run must never send Telegram. Entire report is built and saved before first send. Current implementation does not deduplicate successful manual replays; rerun default with dry-run when inspecting.

RVOL needs53completeweeklybars, excludes current from52mean/std. Fisher9 uses weeklyHL2,Trigger=Fisher[1], reports NEW crossings only; all percentages divide by selectedTop100, not merely validhistorycount. Unknown remains explicit. Trend pools independently require4/7,8/14,16/30weeklyTop100 memberships, prices are daily1dcloses. Universe is current-tradable-only: the weekly catalog fetches exchangeInfo once, saves its own snapshot and returns explicit current-universe evidence; delisted/SETTLING/PENDING contracts are filtered out before any history is fetched. A currently-eligible contract with a real gap or API failure still stops publication.

## Rollout gate and rollback

1. Current-universe requirement (2026-09-20): only current TRADING contracts participate, so the historical AERGO/BDXN metadata/turnover gap is no longer a weekly rollout blocker. Do not re-introduce delisted/historical competitors, extrapolate unknown volume or weaken genuine current-contract failure. Verify the weekly catalog performs no archive audit/historical-catalog read and that actual current-contract gaps still fail.
2. Run focused tests, Python3.10 compile, bash-n, full real dry-run. VerifyTop100/membership/metrics independently. Review diff before installing.
3. Under shared Quant lock back up Finance commit, both old/new shim/wrapper paths and crontab. Install only new weekly files; chmod+xwrapper. Preserve existing daily files and cron line.
4. Export crontab tofile, edit exact weeklyline once, installfromfile, reread/verify. Never pipefilteredoutput directly into crontab. Avoid duplicatejobs. Record hashes, tests,previouscommit and backup path.
5. Production dry-run must produce same checked result without send. First naturalrun is separate from implementation success.

Rollback: remove only the added weeklycron line via export/edit/install/verify, restore backed-up weeklyfiles or retain them unused, revert the associated Finance code throughgit if required. Preserve artifacts and every existingcronentry. No restart.
