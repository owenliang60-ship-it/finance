# Selection Compass rollout — 2026-09-05

Status: complete. Scheduled report sent at 08:01:22 CST; corrected Dollar Volume version sent at 08:03:12 CST.

## Delivered behavior

- Scope: active Extended membership intersect eligible security master.
- Latest diluted EPS must be positive, with both YoY and QoQ at least 25%; a nonpositive comparison quarter is displayed as turnaround.
- Average of existing revenue and net-income four-quarter compound growth rates must be at least 15%. Existing four-quarter fields use three quarterly transitions.
- Any of the last seven trading days must have volume z-score at least 2, using the preceding 120 observations as baseline.
- Report includes current market cap and six-month beta relative to SPY, ordered by market cap descending.
- Fundamental and RVOL coverage are shown against the complete Extended denominator. Either below 95% makes the section unavailable. Healthy zero-hit sections are hidden.

## Backfill and data evidence

- Canary `canary-2026-09-04`: 125/125 jobs done.
- Full `full-2026-09-04`: 4,669 done and six attributed terminal failures out of 4,675 jobs; exact-run verifier passed. All failures are TXT/VG income, balance and cash-flow duplicate fiscal-date content conflicts, retried three times and rolled back atomically.
- Metrics recomputation under the shared writer lock: 966 symbols, 8,088 rows, zero exceptions; current Extended coverage 933/935. Wrapper `manual_selection_compass_metrics` finished at 01:46:49 CST with OK.
- Three-statement coverage 99.79%, profile coverage 100%, forward coverage 96.89%, unattributed gaps zero, SQLite quick_check ok.
- Global eight-quarter/three-statement continuity remains 94.55%; see issue 056. This gate was not changed. The compass requires five consecutive income quarters; its own readiness was 896/935 (95.83%) in the production dry run.

## Code and rendering evidence

- Feature commits: `766441e`, `0dca9c6`, `34bdb30`, `8dbce9d`, `017aeec`, `6a42168`.
- Merge: `bb5a06a`; deployed release: `aa12d2b`.
- Worktree suite after final code fix: 2,811 passed, four skipped. Post-merge feature suite: 195 passed, one mutable-data parity test deselected.
- The deselected legacy volume-concentration test compares today's mutable DB with a July frozen research CSV: 47.8217712509 versus 47.8039169220. AST comparison confirms the loader, calculation, regime and percentile functions are unchanged from `0c374fa`; always-run frozen-fixture tests passed. No tolerance was changed.
- Python 3.10 compilation and engine tests passed on aliyun.
- Production HTML dry run at 01:57:34: available=true, fundamental 896/935, RVOL 924/935, 11 market-cap-ordered hits.
- Real local PNG inspection confirms all ten columns, ordered rows and rightmost beta are visible. Synthetic HTML send-failure exercise generated and passed a PDF containing the compass page.
- Cloud PNG/PDF render at 07:58: compass PNG 186,134 bytes; PDF 2,362,074 bytes. Both were generated from the production dry-run JSON.

## Scheduled delivery

Existing cron is unchanged: 08:00 Tue–Sat runs `finance_market_report` through `run_market_report_pipeline.sh`, which invokes `morning_report.py --no-social --image-report --image-delivery html`.

- `cron_market_report.log`: BEGIN 08:00:01; Telegram file sent to group 08:01:22; pipeline DONE and wrapper OK, duration 81s.
- Saved JSON: `data/scans/morning_20260905_080120.json`; signals as of 2026-09-04; compass available=true.
- Fundamental-ready 896/935 (95.83%); RVOL-ready 924/935 (98.82%).
- Ten hits, in descending market-cap order: AVGO, SU, HPE, OKTA, NVT, AFRM, BEKE, NTNX, FIVE, GNRC. ZM's qualifying volume day rolled out of the seven-session window after the new close.
- Independently recomputed all ten hits' EPS comparisons, CAGR endpoints and RVOL windows from raw SQL rows, without importing the screen engine; all values matched. Market caps matched latest as-of observations.
- Verified all ten HTML columns and exact eligible universe denominator; current code at scheduled execution was `aa12d2b`.

## Dry-run side effect and correction

The 01:57 production `--no-telegram` run also collected Dollar Volume while the US session was open. The 08:00 routine collection skipped that local calendar date. This affected the pre-existing Dollar Volume section, not the compass, which used fresh daily_price rows.

At 08:02, backed up dollar_volume.db to `/tmp/dollar_volume.before-compass-dryrun-correction-20260905.db`, fetched current data using the existing collector, validated 200 rankings, and replaced only the 2026-09-05 ranking set. SQLite quick_check passed. The corrected full report reused the scheduled run's compass data and included the refreshed closing Dollar Volume rankings.

- Corrected report send: Telegram group accepted the file at 08:03:12 CST; caption explicitly labels it 更正版 and explains the updated Dollar Volume section.
- Cloud artifact: `reports/rendered/selection_compass_20260905_corrected/morning_report_2026-09-04.html`.
- Local copy: `reports/selection_compass/2026-09-05/morning_report_2026-09-04-corrected.html`.
- SHA256, identical locally/cloud: `194faa0193afd56138363e9f7777e3d63b49a7c3b1dc84fb852ac8f3c21a61c9`.
- Root cause and future dry-run procedure: issue 057.

No heartbeat was created: the attempted creation failed validation, and the final scheduled run was observed directly in this task. No recurring follow-up remains.
