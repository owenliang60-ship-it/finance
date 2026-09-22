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
