# Premium Pool fundamental cron repair — 2026-09-19

## Fault and bounded repair

Cloud base commit: `a4835b5`. At 10:28 CST, `finance_fundamental` failed after the legacy Core update. The Extended Premium gate had **872/919=94.8857%**, below the unchanged 95% requirement (874 required). The health check's 909/919 measured three-table existence, not five-quarter continuity and current derived metrics.

11 actionable targets were frozen before any write:
- Never collected: AVTR, FPS, FRO, GME, HUT, MOD, TWST, XP.
- New income quarter but stale metrics and corresponding BS/CF: CASY, CPRT, KR.

The weekly wrapper now runs a bounded preparation step between the Core update and Premium build. It resolves the current Extended base, refreshes only never-collected income or income/metrics date mismatches through the existing five-dataset collector, then uses the existing metrics calculator. It stops before collection above 50 targets. CLI is read-only by default; writes require the shared writer lock, and the cron lock inheritance is verified. Known failed/empty income states and structural quarter-history gaps are not reclassified as healthy. Builder failures now include the exact coverage statistics.

Architecture alignment: north-star first layer, Extended base + shared current/vintage/coverage kernel; no new owner/schema, no change to Premium selection rules. This incident repair does not replace the pending Extended earnings-event/weekly reconciliation rollout (Stop E), and cannot discover a new quarter absent from every local table.

## Cloud recovery evidence

A separate cloud worktree ran the 202 targeted tests successfully before restoration. The recovery held `market_db_writer`, backed up SQLite through its backup API, verified `quick_check=ok`, and archived the old Premium snapshot before collection.

- Backup/audit directory: `/root/workspace/Finance/data/backups/premium-repair-20260919T024023Z/`.
- All 11 targets: five datasets `ok`; **122 metric rows** recomputed.
- CASY / CPRT: income, BS, CF, metrics all **2026-07-31**.
- KR: income, BS, CF, metrics all **2026-08-15**.
- Post-write `quick_check=ok`; remaining actionable targets **0**.
- Independent SQL `EXCEPT` in both directions against the backup: **0 non-target changes** in all eight checked tables (income, balance, cashflow, metrics, profile, ratios, coverage, vintage); see `scope-verification.json` in the audit directory.
- Original production Premium builder produced a candidate, fully validated before atomic publication.
- Published at **10:44:02 CST**, as_of **2026-09-18**; fundamental **883/919=96.0827%**, beta **909/919=98.9119%**, **56 members**.
- Shared lock released before the 10:45 forward job.

## Validation

- Targeted collector / metrics / Premium / selection / wrapper tests: **202 passed** locally and on cloud Python 3.10; the additional error-message regression subsequently passed with the full Premium file (**20 passed**).
- Full local suite: **3594 passed, 12 failed, 4 skipped** (263.13s). All 12 failing node IDs were rerun in a separate untouched `a4835b5` worktree: **the same 12 failed**. Seven depend on missing historical breadth input fixtures; five are existing morning-report grouping expectations. No baseline failures were changed in this task.
- Python compilation, shell syntax, diff whitespace checks passed.
- Main-thread `/cr`: reviewed implementation, direct callers and tests; no unresolved findings. No subagents used.

## Rollback

Code can be reverted as one isolated fix. Keep the database backup for audit; do not restore the entire old database over newer cron writes. If data reversal is needed, archive current affected rows first and perform a target-scoped restore under the writer lock. The old Premium snapshot is archived as `premium.before.json`; it is historical and should not be relabeled as current.

## Deployment completion

Code commit **b6a827f** was fast-forwarded to local `main`, pushed to GitHub and pulled by the cloud checkout. Production compilation and shell syntax passed; read-only preparer reported `{}`. Final targeted run: **203 passed in 3.99s**.

Production `--apply` while the forward job held the lock returned **75**, leaving the published Premium file byte-identical. A separate temporary-database smoke test using a real inherited fd 8 passed and confirmed the parent shell retained its lock. The normal forward cron started at **10:45:01 CST** and was not skipped. Premium loader validation returned available=true, as_of=2026-09-18, 56 members. The next natural weekly run remains the routine operational observation; no extra automation was created.
