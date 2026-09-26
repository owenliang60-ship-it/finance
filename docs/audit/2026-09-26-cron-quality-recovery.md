# Cron fundamental recovery — 2026-09-26

## Result and scope

Boss approved the diagnosed cooldown repair and bounded supplemental processing.
North-star alignment: existing data layer / shared collector / current, vintage
and coverage ownership. The cloud remains the only authoritative market writer.

The weekly quality selection began at 06:28:07 UTC. Thirteen persistent issues
were still inside the prior run's seven-day timestamp cooldown. Their deadlines
fell between 06:29:21 and 06:59:52, before the 07:01:38 post-audit. The selected
200 all succeeded, but those unselected names became due and triggered rc1.

The patch uses UTC calendar dates in all three successful-check cooldown paths:
missing-source recent checks, deferrable issues, and periodic verification.
Explicit failure retry timestamps, future/invalid timestamp rejection, source
warnings, Premium's 95% gate and the 200-symbol budget retain their semantics.

## Validation

- Added real temporary-SQLite regression cases for stale/missing/healthy sources,
  UTC date boundary, timezone conversion and exact failure retry time. Original
  implementation: **9 failed, 7 passed**. Fixed implementation: all pass.
- Relevant collector/auditor/runner/wrapper/metrics/compass/Premium suite:
  **275 passed in 7.78s**.
- Independent review found no actionable issue; reviewer reran 20 boundary and
  timestamp tests and independently reproduced the old-code failures.
- Cloud Python3.10 imported the candidate from `/tmp`, without replacing
  production code. Read-only replay on the pre-recovery SQLite backup:
  at 06:28:07, old due=0 / new due=13; at 07:01:38, old due=13 / new due=13.
  Exact target sets agreed; zero network requests in this replay.
- `python -m pytest -q` full suite: **4092 passed, 5 failed, 1 skipped** in
  482.71s. All five failures were `FileNotFoundError` for the worktree-missing
  `data/breadth_study_1b/daily_breadth.csv`. Copying that existing input from the
  primary checkout and rerunning `tests/test_breadth_buy_quality.py -q` gave
  **16 passed in 0.97s**. This is a full-run result plus a targeted environment
  correction, not a claim of a second all-green full invocation.
- Python3.10 AST syntax and `git diff --check` pass. Functional commit:
  `a9926c6d` on `codex/cron-quality-boundary`.

## Authorized supplemental cloud run

Before any API request or mutation, the existing shared `market_db_writer`
FileLock was acquired and the current repair set asserted equal to the diagnosed
13. SQLite backup API created
`data/market.db.pre-quality-recovery-20260926` on the cloud. The existing
`run_quality` and collector were reused without changing production code.
Budget: 13 symbols × 5 datasets, client hard cap65; actual **65 requests**.

- Requested/successful: AZO, COST, FDXF, LEN, LYG, NGG, NMR, SAN, TCOM, TCPA,
  UBS, VOD, CTAS — **13/13**; collection/metrics errors **0**.
- Truly resolved: **AZO, COST, LEN, CTAS**.
- Still source-pending/unresolved: **FDXF, LYG, NGG, NMR, SAN, TCOM, TCPA,
  UBS, VOD**. Issues remain in the report under cooldown, not relabeled clean.
- After repair: due targets **0**, readiness **883/917 (96.29%)**, status
  **WARN**, exit code **0**, SQLite `quick_check` **ok**.
- Cloud report: `data/quality/manual-recovery-20260926.json`;
  log: `/tmp/finance-quality-recovery-20260926.log`.
  Local copies: `reports/cron-recovery-20260926/` in the isolated worktree.

The existing published Premium list was not rebuilt by this supplemental run.
No Telegram message or cron modification occurred.

## Cooldown code deployment

After Boss explicitly approved merge/push/deployment, local main fast-forwarded
to `a9926c6d` and origin/main was pushed. Cloud main then fast-forwarded under
the shared writer lock. Production Python3.10 targeted auditor/runner/wrapper
suite: **76 passed in15.81s**; compilation passed. No service restart was needed.
Unrelated local tracked modifications were preserved. This deployment applies
only the cooldown fix; Forward implementation and rollout remain separate.

## Forward remains separate

FMP ingestion on 9/26 is complete (979/1021 quarterly successes); QQQ weekly PE
is updated through9/25. SPY/SOXX weekly products remain through9/11, and six-basket
PIT valuation remains9/12 because history failure stopped the wrapper.
Official sources now identify the SPY instrument as a CVR and NXP's correct
CUSIP; implementation needs an explicit scoped source-correction contract.
See `docs/plans/2026-09-26-index-pe-source-corrections.md` for approval scope.
