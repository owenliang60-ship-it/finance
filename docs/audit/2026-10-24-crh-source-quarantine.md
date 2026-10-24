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

## Isolated evidence

Artifacts are under `reports/cron-recovery-20261024/` in this worktree.
The production connection used `mode=ro` and SQLite's backup API, including WAL.
The frozen baseline is 1,451,376,640 bytes, SHA256
`9156c84cf3d28e2fdb2a86b082160e3b942fd34b547f87fb485417fa776f73a3`.
Cloud and downloaded-copy `quick_check` passed. The baseline is preserved;
mutations target only the separate `trial.db`.

Initial replay: five rows archived, 42 metric rows recomputed, all three CRH
statement collections returned `ok`, all 171 original CRH vintage rows matched
field-for-field afterwards, and `quick_check=ok`. These are source-repair checks;
whole-window and six-basket certification are recorded below when completed.

## Production approval boundary

No merge, push, deployment, production repair, ingestion resume, cron edit or
message delivery is authorized by the current isolated-verification approval.
After acceptance, submit the reviewed patch, exact hash plan, backup/lock steps,
request budget and whole-window/PIT verification results for Boss's approval.
Do not restore an entire old database over unrelated concurrent data.
