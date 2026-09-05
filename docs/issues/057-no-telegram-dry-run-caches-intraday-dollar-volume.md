# 057 — No-Telegram morning dry-run writes the daily Dollar Volume cache

Date: 2026-09-05

Status: data corrected and corrected report delivered; no feature-code changes made.

## Cause and impact

During Selection Compass deployment, a real production run at 01:57 CST used
`morning_report.py --no-telegram --image-report --image-delivery html`.
`--no-telegram` suppresses transport only: main still invokes `run_dollar_volume`,
which collects and marks the CST calendar date as complete. US trading was still
in progress. At 08:00 the real cron reused this intraday cache via `is_collected`.

The new compass was unaffected because its prices came from the completed 06:30
daily_price update. The old Dollar Volume section had stale volume/price values.

## Correction and verification

- Preserved the old DB with SQLite backup API at
  `/tmp/dollar_volume.before-compass-dryrun-correction-20260905.db` on aliyun.
- Fetched post-close screener data with the existing collector; validated 200
  rankings before replacing the 2026-09-05 ranking set using its existing writer.
- Confirmed stored rows exactly matched the fetched ranking set and quick_check=ok.
- Regenerated the full HTML from the scheduled compass payload and updated Dollar
  Volume data; sent a clearly labeled 更正版 to the same Telegram group at 08:03:12.

## Prevention

For presentation smoke tests, replay an existing saved market_signals JSON and
read-only cached Dollar Volume rows into the renderers. Do not equate
`--no-telegram` with a no-write dry run. If live collection is required for a smoke
test, use an isolated DB/output context, or schedule it after market close and
explicitly inspect collection side effects before the normal cron.

This incident does not justify changing the production collector or adding new
CLI semantics in the completed compass feature; any such change needs its own
small, tested scope.
