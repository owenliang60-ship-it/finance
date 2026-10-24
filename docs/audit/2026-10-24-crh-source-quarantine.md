# CRH source quarantine — isolated recovery, 2026-10-24

Production has not been modified or rerun. Boss approved preparation and isolated
verification, with a separate approval required before production changes.
Branch `codex/cron-recovery-oct24` starts at production commit `9f229c71`.
This repairs the north-star data layer without changing valuation formulas,
coverage thresholds, raw vintage history, or the cloud-only writer boundary.

## Failure and reviewed scope

The 10/24 forward ingestion completed. Historical SPY stopped in the income
writer with a fiscal identity conflict; no SPY candidate product was published.
QQQ/SOXX completed through 10/23; SPY remains at 10/16 and six-basket consensus
valuation at 10/17. The historical run is `index-pe-2026-10-24-e7505c98`.

Exactly five CRH current rows are reviewed for archival quarantine:

| Table | Date | Stored period | New provider period |
|---|---|---|---|
| income_quarterly | 2023-09-30 | Q4 | Q3 |
| income_quarterly | 2008-03-31 | Q2 | Q1 |
| income_quarterly | 2007-09-30 | Q4 | Q3 |
| balance_sheet_quarterly | 2023-09-30 | Q4 | Q3 |
| cash_flow_quarterly | 2023-09-30 | Q4 | Q3 |

Every other persisted field matched between the frozen current row and the
new provider response, allowing SQLite REAL/provider integer representation.
The entire persisted field set is frozen in
`config/reviewed_statement_quarantines.json`; it is not a ticker-wide exclusion.
The query used three serial HTTP requests for the CRH statements, capped at six.

## Primary evidence

[CRH's SEC Exhibit 99.1](https://www.sec.gov/Archives/edgar/data/849395/000162828024015698/crhpublishesquarterlyusgaa.htm)
provides the 2023 quarterly US GAAP tables. The
[issuer announcement](https://www.crh.com/media/press-releases/2024/crh-publishes-quarterly-us-gaap-information-for-2023/)
dates their publication to **2024-04-11**.

For September 2023, official quarterly revenue is $10,128m, total net income
$1,318m and parent net income $1,306m. The provider row instead contains
$9,604.8044m and $1,232.844166m. Official balance-sheet assets are $47,631m,
liabilities $24,983m, cash $5,722m and parent equity $21,767m, all different from
the frozen row. Official cash-flow columns are cumulative: subtracting six-month
from nine-month values gives Q3 operating cash flow $1,631m and PPE purchases
−$404m, also different from the provider row. Those later-published numbers
cannot simply replace the old row while retaining its 2023-09-30 acceptance date.

The original
[2023-11-21 earnings update, page 6](https://www.crh.com/wp-content/uploads/2023/11/crh-plc-q3-2023-earnings-update-announcement_dc561f-1.pdf)
describes the forthcoming US GAAP transition and 2024 quarterly reporting.
It does not substantiate a complete GAAP Q3 statement accepted on September 30.

The [2008 interim report, printed page 11](https://www.crh.com/wp-content/uploads/2025/09/2008-interim-results-aug-1.pdf)
reports six months ended June 30: €9,704m revenue and €467m total net income.
The [FY2007 results, printed page 11](https://www.crh.com/wp-content/uploads/2025/09/2007-year-end-results-mar.pdf)
report €20,992m revenue and €1,438m total net income. No reviewed primary record
substantiates the March 2008 €7,659m/€364m or September 2007 €8,324m/€661m
quarterly rows. This establishes an unresolved source/quarter identity, not
proof that the amounts were fabricated or what their replacement dates should be.

## Implementation contract

1. An explicit plan freezes each complete current-row hash. Validate all five
   before writing. Archive each row, remove it from current, and recompute CRH
   metrics in one transaction. Stale plans, duplicate targets and metric failures
   roll back. Raw vintage payloads and timestamps are untouched.
2. Ingestion excludes only the exact frozen field set and permitted observed
   period labels. Any changed payload at those CRH/table/date keys stops for a
   new review. It is not automatically treated as fixed or discarded.
3. The shared current writer filters the reviewed rows. The collector retains
   the full raw vintage and records explicit quarantine IDs in coverage detail.
   A response containing only quarantined rows is a failed dataset.
4. Historical PE refuses unrepaired quarantined current rows, including offline
   runs. For incoming responses it filters before both in-memory valuation and
   storage, retaining the exact rejected rows in its stage report.
5. Financial values, fiscal dates, availability dates, coverage denominators and
   verification thresholds are not replaced or relaxed. Missing CRH history
   remains missing and must pass the existing per-point coverage gates.

Scope: raw `known_as_of`/vintage intentionally still exposes the original
observations; this patch does not rewrite strict historical replay. A later
short-window collection can clear the coverage-detail warning when those old
dates are absent. The archive, frozen configuration and PE report are the
durable quarantine evidence, not a permanent coverage-status badge.

## Isolated evidence

Artifacts are under `reports/cron-recovery-20261024/` in this worktree.
The production connection used `mode=ro` and SQLite's backup API, including WAL.
The frozen baseline is 1,451,376,640 bytes, SHA256
`9156c84cf3d28e2fdb2a86b082160e3b942fd34b547f87fb485417fa776f73a3`.
Cloud and downloaded-copy `quick_check` passed. The baseline is preserved.
`trial.db` is the separate ingestion replay; `recovery.db` is the clean copy
for the exact five-row production proposal. All mutations stay in these copies.

Initial replay: five rows archived, 42 metric rows recomputed, all three CRH
statement collections returned `ok`, all 171 original CRH vintage rows matched
field-for-field afterwards, and `quick_check=ok`. These are source-repair checks;
whole-window and six-basket certification are recorded below when completed.

The ingestion replay also triggered the existing writer's financially equivalent
2012 cash-flow date canonicalization (9/30 → 12/31), with its normal archive.
That extra operation is **not** in the production proposal. The separate
`recovery.db` starts again from the frozen baseline, applies only the five-row
plan, and rebuilds SPY from already stored sources with `client=None`. Final
acceptance compares all symbols in all three current statement tables against
the baseline minus those five exact rows, requires exact CRH vintage equality,
and matches every new quarantine archive payload to its original baseline row.
The redundant historical calculation on the ingestion-test `trial.db` was
stopped before candidate publication; that copy is evidence of ingestion only,
not a completed historical certification. `recovery.db` is the acceptance run.

The 17 other symbols that SPY would request after its preliminary source stage
were fetched serially with a 20-request ceiling (17 used). In a second isolated
SQLite database, 16 responses passed the real current writer; INFO returned an
explicit empty response. No additional fiscal conflict was found. Those inputs
were not written to production or used as an implicit fallback in the trial.
Combined isolated-investigation requests: 20 (3 CRH + 17 other income requests),
excluding the two requests in the preceding diagnostic turn.

Initial independent review: 212 related tests passed; all five frozen rows
matched the baseline and provider payloads field-for-field; the other 37 CRH
income rows were retained. Local initial repair suite: 160 passed. Additional
historical/valuation suite: 256 passed. The same 160-test repair suite passed
on cloud Python 3.10 in `/tmp/finance-crh-oct24-code` (22.39 seconds), with no
production source or database edits.

Full repository regression: `python -m pytest tests -q --tb=short` returned
**4,877 passed, 4 skipped, 17 warnings in 387.38s**. Warnings are from existing
RS rank casting and deprecated universe/concept helpers. Tests use separate
local fixture copies, not the baseline or recovery databases.

Read-only quality audit at 2026-10-24T05:00:00Z has identical per-symbol records
before/after: 856/894 fundamental-ready and 142 pre-existing repair targets.
Both reports remain `FAIL`; this is **no regression**, not a claim that all
unrelated source-quality problems are resolved. CRH remains fundamental-ready.
Evidence: `quality-comparison.json` and the two complete quality reports.

## Final isolated acceptance

Functional commit: `8a42c29a`. The exact five-row proposal in `recovery.db`
completed with **zero HTTP requests**. SPY's five-year run
`index-pe-recovery-trial-20261024-spy` completed in 875.46 seconds with 261
published TTM and hindsight points and no failed basket. Its candidate
transaction's existing independent verifier passed before commit.

| Product in isolated copy | Rows | Latest date |
|---|---:|---|
| SPY historical weekly PE | 261 | 2026-10-23 |
| QQQ historical weekly PE | 261 | 2026-10-23 |
| SOXX historical weekly PE | 257 | 2026-10-23 |
| Consensus NTM baskets | 6 | 2026-10-24 |

All-three history certification passed before and after the PIT phase. The
six-basket candidate transaction and the full forward verifier returned success,
with empty failure lists. SOX/IGV auxiliary blend lines remain NULL/partial;
the valid-empty-set drift warning is retained. These are not claims that every
auxiliary source is complete.

Independent final source assertions passed: all symbols in all three current
statement tables equal the baseline minus the five exact targets; CRH's 171
vintage rows are exactly unchanged, with no additions; every quarantine archive
contains its full baseline row and frozen plan; SQLite `quick_check=ok`; the
baseline SHA256 is unchanged. A separate reviewer also used bidirectional SQL
comparisons and recomputed the archive/plan hashes independently.

Reproducible local acceptance scripts: `run_isolated_recovery.py` and
`verify_trial.py` under the artifact directory. Compact receipt:
`trial-summary.json` (despite the legacy filename, this certifies `recovery.db`).
Full outputs are `recovery-spy-history.json`, `trial-history-before-pit.json`,
`trial-pit-valuation.json`, `trial-pit-verifier.json`, and
`trial-history-final.json`. Two subagents were used: primary evidence research
and independent implementation/acceptance review. Parent checked the source,
diff and actual command outputs.

## Production approval boundary

No merge, push, deployment, production repair, ingestion resume, cron edit or
message delivery is authorized by the current isolated-verification approval.
After acceptance, submit the reviewed patch, exact hash plan, backup/lock steps,
request budget and whole-window/PIT verification results for Boss's approval.
Do not restore an entire old database over unrelated concurrent data.

Proposed production sequence, pending that approval:

1. Acquire `market_db_writer` **before deployment**, and hold it through repair
   and final verification. Confirm free space and create a consistent SQLite
   backup using the existing backup helper. Recheck all five complete-row hashes
   against the frozen plan, then deploy the reviewed commit and configuration
   while still holding the lock. Scheduled writers must not see mixed versions.
2. Call `apply_reviewed_statement_quarantine` with the frozen plan. Require five
   archival removals, unchanged raw vintage, and successful metric recomputation.
   Do **not** replay the full three-statement ingestion experiment in production.
3. Reuse `run_backfill(client=None, store=..., conn=...)` with a fresh run ID,
   `SPY`, `as_of=2026-10-24`, five years. HTTP budget **0**. Missing inputs that
   prevent certification stop recovery; do not increase a budget or loosen gates.
4. Certify SPY/QQQ/SOXX against that window. If already-complete other baskets no
   longer reconcile with current sources, stop and report the new blocker.
5. Run only the valuation phase for the already-complete 10/24 forward ingestion,
   then the full forward verifier and final historical verifier. No ingestion
   resume and no replacement of an immutable pre-existing PIT vintage.
6. Require SQLite `quick_check`, exact current-statement difference of those five
   rows only, original archive payload/hash agreement, and unchanged raw vintage.
   Preserve the original failure manifests and all unrelated data. No cron edits
   or manual report/message sends are part of this proposal.
