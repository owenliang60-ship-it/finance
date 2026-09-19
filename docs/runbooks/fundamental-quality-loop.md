# Fundamental quality — weekly audit, repair and verification

Runs every Saturday **14:00 Asia/Shanghai**, inside `finance_fundamental`, immediately before Premium. This replaces the earlier narrow Premium preparer in the weekly wrapper. Daily jobs and the 10:45 forward job are unchanged. Boss chose weekly/14:00/200 on 2026-09-19.

## What it checks

The denominator is the current eligible Extended base. It checks all three statements, metrics dates, fiscal alignment, canonical five-quarter Premium inputs, collection statuses and retry timers. Fiscal age over120 days and last successful verification over30 days are separate signals; matching dates cannot make uniformly old inputs fresh. Invalid/future verification timestamps are UNKNOWN. Cached earnings hints require fresh evidence and a >45-day fiscal-date lead (small fiscal-date aliases such as KR's eight-day difference are not a missing quarter).

Quarter gaps, short history and absent/stale earnings evidence remain explicit warnings. No automatic exclusion, raw-row deletion, zero filling, or lowered Premium threshold. Existing provider-empty/failure backoff and terminal identity states are honored even when old financial rows remain.

## Repair and rolling verification

Repair candidates take priority. Spare budget is filled with the oldest successfully checked securities (at least7 days since all required sources were checked), up to **200 total symbols per run**, including periodic checks. Source requests use the existing five-dataset collector, eight-quarter limit and vintage history; metrics use the existing calculator. The same auditor reruns afterwards against a consistent SQLite read snapshot.

`requests_successful` is not `truly_resolved`. If the source still returns an old statement, the report retains the issue, marks it unresolved and uses the seven-day recheck cooldown. Known incomplete history is never rewritten as healthy. At roughly919 base securities and a weekly Core refresh, this budget supports about a month of rolling checks; heavier repair demand or pool growth creates a visible backlog rather than silently claiming this cadence was met.

Worst-case additional request budget:200 ×5 datasets ×2s ≈33 minutes, excluding retries/latency. The Core update precedes it, so the weekly job moved away from the old 10:45 collision window.

## Commands

Read-only (no network/database writes; report writing is explicit):

```bash
python3 scripts/check_fundamental_quality.py --report /tmp/fundamental-quality.json
```

Manual repair, after reviewing the report and checking provider-call budget:

```bash
python3 scripts/check_fundamental_quality.py --repair --max-targets 200 \
  --report data/quality/manual-<timestamp>.json
```

The manual command self-locks. Never pass `--no-lock` by hand: that option requires the actual inherited cron fd8 and matching lock inode. Busy exits75 before opening a writable store. Historical/future `--as-of` is rejected for repairs. Read-only historical audits inspect current storage as of the reference clock; they are not historical PIT reconstructions.

## Reports and exit codes

Weekly reports: `data/quality/fundamentals-<UTC timestamp>.json`, containing before/after, full universe, per-symbol issues/evidence, selected targets, successes, true resolutions, periodic verifications, unresolved cases, budget deferrals and errors. Report writes are atomic. Logs contain compact counts and the report path.

- **0**: audit/repair completed with no currently actionable repair errors; the report may still be **WARN**, never silently CLEAN, for structural/source-pending/cooldown/unknown-evidence cases.
- **1**: due repair backlog or collection/metrics errors remain.
- **2**: execution/store/report error; no valid completion claim.
- **75**: writer lock occupied; no mutation.

The weekly wrapper treats data-quality rc1 separately from the existing Premium95% gate: Premium can still publish if its own inputs pass, but the job retains rc1 and the existing failure alert. Store/lock/execution errors stop publication. This avoids turning one provider failure into an entirely stale Premium snapshot while keeping the failure visible. Warnings and unresolved source cases remain in the JSON even when rc0.

## Boundaries

The maintenance loop does not invent issuer truth or repair vendor identities, ambiguous duplicate fiscal rows or genuine reporting-history shortages by hand. It detects and attributes them, respects retry state and exposes them for review. It does not add a calendar API or a notification system. It provides weekly detection/verification, not a guarantee that every issuer's financial statement updates immediately after publication. Daily event-driven ingestion remains a separate scope if later requested.
