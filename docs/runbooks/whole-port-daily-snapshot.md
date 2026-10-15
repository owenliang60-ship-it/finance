# Whole Port daily history

Purpose: retain the observed `Whole Port!B2:C9` table each calendar day for later
NAV and performance reviews. Aligns with North Star data layer and CIO-A history.

- Schedule: every day, including weekends, 09:00 Asia/Shanghai (UTC+8).
- Source: existing read-only XLSX export, `PORTFOLIO_SHEET_ID` from private env.
- Preserve all eight rows (header, five asset buckets, total, Net Total), original
  labels and unrounded cached numeric values. This is observation time, not proof
  that every underlying quote was refreshed at 09:00.
- Reject missing tab, malformed export, label drift at totals, duplicate labels,
  missing/error/non-finite amounts. Negative/zero amounts are valid.
- One successful snapshot per local calendar date. Duplicate calls leave the
  original untouched. Failure never claims the date; no historical backfill from
  today's values. A manual retry records its actual observation timestamp.

## Runtime

Code is installed as an immutable Git archive in
`/root/workspace/Finance-jobs/whole-port-<commit>/`, separate from the main checkout
and its auto-pull. The exact revision is in the installed cron line. The existing
Finance cron wrapper provides job locking and start/end/failure logs.

```sh
0 9 * * * FINANCE_ENV_FILE=/root/workspace/Finance/data/whole_port/.env /root/workspace/Finance/scripts/cron_wrapper.sh finance_whole_port cron_whole_port.log /usr/bin/python3 /root/workspace/Finance-jobs/whole-port-<commit>/scripts/snapshot_whole_port.py --db /root/workspace/Finance/data/whole_port/history.db
```

The dedicated env contains only `PORTFOLIO_SHEET_ID` (mode 0600); the data directory
is 0700. No outbound notifications; cron failures are recorded in the log.
No changes to `market.db`, `company.db`, Google Sheet, or existing PI behavior.

## History and inspection

Database: `/root/workspace/Finance/data/whole_port/history.db`

Table `whole_port_daily`: `snapshot_date` (primary key), `captured_at` (ISO timestamp
with offset), `timezone`, `sheet_tab`, `cell_range`, `cells_json` (8 x 2 matrix).
Always order by `snapshot_date` for analysis. Data and credentials must never be
committed to the public repository.

```sql
SELECT snapshot_date, captured_at, json_extract(cells_json, '$[7][1]') AS net_total
FROM whole_port_daily ORDER BY snapshot_date;
```

Log: `/root/workspace/Finance/logs/cron_whole_port.log`. Expected result `recorded`
or `already_recorded`; no balances or source URL in logs. Inspect dates to identify
gaps. Existing Finance sync does not pull this separate database: copy it using
SQLite's backup API to a temporary snapshot before transfer, not a live file copy.
History has no automatic expiry or deletion.

## Daily drawdown push

`scripts/whole_port_drawdown.py` opens `history.db` read-only and sends one message
per run to the private chat (`TELEGRAM_CHAT_ID`, same as Portfolio Intelligence).

- Tracks Net Total, 美股, BTC and 独立团资金, found by label, not row position.
- HWM = highest value in snapshots from 2026-09-23 through today. Every run recomputes
  it from the append-only history: no stored state, no manual start value, so it
  never falls. A tie keeps the earliest date.
- Per item: current value, HWM and its date, gap to HWM in amount and percent
  (`n/a` when HWM <= 0). Net Total also shows the room above the HWM x 0.8 line
  (Tokamak 20% total-drawdown limit), or how far it is below that line.
- The whole message is UNKNOWN with a reason when the database or table cannot be
  read, today's Shanghai-date snapshot is missing, or any row has a missing or
  duplicate tracked label or a non-numeric value. It never uses an earlier day.
- Fixed last line: `按余额计，未扣除资金划转`.
- stdout is `whole_port_drawdown sent ok` or `sent unknown: <reason>`, never amounts.
  A failed Telegram send exits 1, so `cron_wrapper.sh` raises its alert.

It runs in the same marked cron block, chained with `;` after the snapshot command,
so it always starts after the 09:00 write attempt, including a failed one. It uses
the main Finance `.env` for Telegram; the snapshot keeps its dedicated env.

```sh
0 9 * * * <snapshot command above>; /root/workspace/Finance/scripts/cron_wrapper.sh finance_whole_port_drawdown cron_whole_port_drawdown.log /usr/bin/python3 /root/workspace/Finance-jobs/whole-port-<commit>/scripts/whole_port_drawdown.py --db /root/workspace/Finance/data/whole_port/history.db
```

## Deploy / rollback

Run the snapshot tests and existing sheet reader tests, compile with cloud Python,
then use the real Sheet with a temporary database to verify 8 x 2 cells and daily
deduplication. Production is first populated by the scheduled 09:00 run.

Export `crontab -l` to a timestamped backup, append only this job to a candidate
file, install with `crontab <file>`, and compare installed content to the candidate.
Before installation confirm the live cron still equals the captured backup.
Keep prior entries intact. To disable, remove only the marked Whole Port cron block
from a fresh export and reinstall the file; retain all collected history.
Never restore a stale full crontab over later unrelated changes.

## Deployment evidence — 2026-09-23

- Runtime revision `e38565154fb2a6e4bec5b1b7b8cd7d3f2d1dd99e`, installed as
  `/root/workspace/Finance-jobs/whole-port-e3856515`.
- Local and cloud: `python -m pytest tests/test_whole_port_snapshot.py
  tests/test_portfolio/test_sheet_book.py tests/test_portfolio/test_intelligence.py
  tests/test_telegram_routing.py -q` → **108 passed** on each host.
- Cloud `py_compile` passed. Main-thread `/cr`: no blocking findings.
- Live Sheet via the real cron wrapper in a minimal cron environment: **8 x 2**,
  one snapshot, second call leaves database bytes unchanged, `quick_check=ok`.
  Test database was temporary; no pre-09:00 production observation was inserted.
- Archive SHA-256 matched on both hosts:
  `ec9f8f8a2b42e5941c06052229a2f721eb5863f136a4701cba97b0a453b3e8a3`.
- Cron daemon active, timezone Asia/Shanghai, installed crontab exactly matched
  candidate, one new job, all earlier entries retained.
- Prior cron backup:
  `/root/workspace/Finance/data/whole_port/crontab-before-20260923T075718.txt`.
- First scheduled observation: **2026-09-23 09:00 UTC+8**. Natural execution was
  still pending at installation verification (07:57); not claimed as completed.
- Work is retained on `codex/whole-port-daily-snapshot`; no main merge or Git push.
