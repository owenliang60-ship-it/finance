# Forward source corrections — 2026-09-26

Status: deployed; SPY restored, remaining recovery in progress. Boss approved the two scoped
corrections in `docs/plans/2026-09-26-index-pe-source-corrections.md`.

## Production deployment and recovery (live checkpoint)

Boss subsequently approved merge, push, deployment and a cumulative1,200-request
recovery ceiling. Main/origin/cloud advanced to `3bfd5002`, including functional
commit `4eca9467`. The deployment's Python3.10 imports compiled successfully.
The task-created1,151,180,800-byte pre-quality backup was gzip-archived to
306,739,585 bytes; decompressed SHA256 matched the original before the redundant
uncompressed file was removed. Its `.gz.json` receipt remains beside the archive.

Recovery acquired `market_db_writer`, created a fresh SQLite backup, and reserved
each HTTP attempt in `data/forward-recovery-20260926/budget.json` before sending.
Attempt1 stopped after4 requests on BNY's conflicting fiscal identity; no new
weekly/PIT product was published. A further provider query used request5.

### BNY source-label correction

The existing row for2025-09-30 had `period=Q4`, while the current provider and
[BNY's SEC-filed third-quarter release](https://www.sec.gov/Archives/edgar/data/1390777/000139077725000144/ex991_earningsreleasex3q25.htm)
identify it as Q3; the $1.445bn shareholder net income and $1.88 diluted EPS
match. Current provider comparison found12 income rows whose only changed
field was period (September Q4→Q3; March Q2→Q1). Matching dates in the other two
statements had5 mislabeled rows each. No financial amount was manually changed.

The22-row plan froze exact old-row hashes, date, year and before/after period.
An isolated SQLite simulation archived and corrected the labels, recomputed
metrics and proved the previously blocked40-row income write succeeds. Production
then repeated the hash checks under the shared lock, archived all22 old rows and
old metrics in the same transaction, changed only period, recomputed40 metrics,
and passed `quick_check`. Existing vintages were not relabeled or backdated.
Archive operation: `bny-period-20260926`. The existing recovery backup remains.
Final metrics are to be recomputed once historical collection has added quarters.

Attempt2 uses a new run ID and resumes the **same** durable budget at5. It reuses
the original recovery backup, preserving attempt1's logs/failed manifest. PIT
remains gated on successful certification of all historical products. Status and
results live under `data/forward-recovery-20260926-attempt2/` on the cloud.

### SPY restored; KRW follow-up

SPY attempt2 completed and passed independent certification:261 weekly rows,
all261 TTM and hindsight values, window2021-10-01 through2026-09-25. SOXX then
stopped at FX validation after total853 requests: freshly fetched SKHYV income
uses KRW, which had no allowlisted USD-per-unit range. Request854 retained the
five-year KRWUSD response:1,388 rows, observed range0.000637–0.000852.

Issuer/currency evidence was checked before extending support:
- Nasdaq ETA2026-37 binds CUSIP78392B206 to SK hynix's SKHYV listing and SKHY
  regular-way successor: https://www.nasdaqtrader.com/TraderNews.aspx?id=ETA2026-37
- The issuer's Q1 release reports KRW amounts:
  https://news.skhynix.com/en/q1-2026-business-results/
- Federal Reserve H.10 reports1,387.97 KRW/USD on2026-09-18, reciprocal about
  0.0007205 USD/KRW; vendor close0.000717 is about0.5% different, consistent
  in orientation/order of magnitude, not asserted to be the identical fixing:
  https://www.federalreserve.gov/releases/h10/current/

The added KRW band0.0004–0.0013 permits the reviewed direct quote and rejects
inversion, wrong symbol, zero, NaN and out-of-range values. Other currencies'
bounds and stale/missing-data rules are unchanged. The verifier also requires
KRW support. This does not remap SKHYV to SKHY or certify the temporary ticker's
market-cap freshness; missing/quarantined inputs remain subject to coverage.
Primary vendor response SHA256:
`76f489ef49ed3cfe32d8713ae4015892a11d6880060c877aaa775c77d4d81769`.

The KRW conversion test first failed; independent review found no blocker.
Full-suite checkpoint:4204 passed,1 failed,1 skipped; failure was the old test
using KRW as its deliberately unsupported currency. That fixture now uses CHF,
preserving unknown-currency rejection. Focused post-fix results are retained in
the recovery reports. Production rollout/resumption follows the approved
recovery scope and still uses the same1,200-request cumulative ledger.

## Result

- SPY `2602335D / 436CVR021 / TPG INC / empty ISIN` is a reviewed CVR, excluded
  from ordinary-equity PE while preserving its physical source row and weight.
- SOXX `0EDE.L / F2933A109 / NL0009538784` uses reviewed effective CUSIP
  `N6596X109`; the incorrect raw CUSIP remains unchanged and is not a legal alias.
- Records are limited to the reviewed9/25–9/26 input dates, exact raw security,
  basket and live-source kind. Frozen evidence hashes are verified at load.
  The known9/26 snapshot requires exactly one matching row. This anchor detects
  removal/duplication inside an existing snapshot, not deletion of a whole source
  snapshot or coordinated tampering of both raw and normalized values.
- No database schema migration, financial formula, coverage threshold, source
  overwrite, ingestion rerun or new cron job is introduced. Old daily PE outputs
  are outside this repair's scope.

## Physical evidence

Frozen DoubleLine/ACVF holdings bind `436CVR021 / 2602335D` to RIGHT/CVR. NXP's
official FAQ binds its ISIN to `N6596X109`. Additional SEC N-PORT extracted text
binds the CVR to Hologic rights. The LSE binding is a cached search extraction,
clearly labeled; it is not claimed to be a fresh directly downloaded directory.
Artifacts and hashes are in `docs/references/index-pe-source-corrections-20260926/`.

Cloud read-only inspection also found the6/30 SPY disclosure, available8/28,
already classifies CUSIP436CVR021 as DE/derivative. Its raw ticker is empty, so
it does not independently fill the legacy PIT table's missing security IDs.

The9/26 legacy PIT row has an exact same-day full-source witness: basket/date,
asset, name, weight, market value and source updated time agree. The read view
requires a unique match and available-at/fetched-day validation. Earlier PIT
weeks without this witness remain unchanged; later evidence is not backdated.

## Independent validation

Historical verifier independently reconstructs matching, classification and
effective identifiers from physical rows. New `forward_source_verifier.py`
independently reconstructs the PIT witness/join and checks exclusions and equity
members; it shares only the evidence loader, not producer matching or application.

TDD demonstrated missing correction support and the actual old failures before
implementation. Review exposed and fixed: physical `reviewed_cvr` marker trust,
missing raw/normalized market-value verification, and a verifier error-prefix
path which hid an expired persisted classification in a not-yet-used snapshot.
The last case was reproduced through the full `verify_database` entry point.

- Final relevant suite (11 files): **505 passed in7.45s** locally.
- Same suite on isolated cloud staging, Python3.10: **505 passed in76.11s**.
- Python3.10 syntax checks and `git diff --check` passed.
- Full-suite checkpoint: **4197 passed, 1 skipped, 17 warnings in462.26s**.
  The final review's error-prefix correction and new full-entry negative case
  landed while that suite was running; the final local/cloud505-test runs above
  include them. No second full invocation is claimed.
- Functional commit: `4eca9467` on `codex/cron-quality-boundary`.
- Three subagents were used for evidence, independent verifier implementation,
  and independent review. Main thread checked changes, tests and cloud outputs.

## Cloud source replay / online preflight

The staged candidate read the authoritative cloud database in `mode=ro`:

| Basket | Physical source rows | Resolved selected equity rows | Identity mismatches | Membership errors |
|---|---:|---:|---:|---:|
| SPY | 12,100 | 11,083 | 0 | 0 |
| SOXX | 740 | 612 | 0 | 0 |
| QQQ | 2,447 | 2,224 | 0 | 0 |

Producer source-identity errors were zero for all three. Two intended correction
records fired; QQQ had none. The PIT join preserved row count and identified one
reviewed CVR. This replay used zero HTTP requests and made no production writes.

Separate bounded online dry runs used **8 requests each** for SPY and SOXX.
Both passed source identity and then returned nonzero on the hard request budget
in subsequent collection. They are identity acceptance, not valuation acceptance.
Logs on cloud: `/tmp/finance-forward-{spy,soxx}-preflight-20260926.log`.

## Proposed production recovery budget

Current-cache, zero-HTTP planning through existing collector kernels:

| Basket | Fundamentals | Market caps | Splits | Sanity refresh | FX | Total excluding source |
|---|---:|---:|---:|---:|---:|---:|
| SPY | 25 | 71 | 613 | 78 | 0 | 787 |
| SOXX | 4 | 8 | 43 | 3 | 1 | 59 |

Source-directory calls add about2. Responses, retry paths and newly discovered
sanity windows can change these counts. A proposed **1,200-request total cap**
leaves room to recertify/recompute QQQ if shared source revisions invalidate its
already completed product. API wait alone is roughly28 minutes for848 nominal
calls; historical SPY assembly has previously taken46 minutes. Allow roughly
1.5–2 hours for recovery and certification, with no promise of successful
publication if another source/coverage gate fails.

Before recovery: shared writer lock, disk check and SQLite backup. Cloud currently
has about2GB free, versus a1.1GB database. Compress and validate the backup created
by this task (`data/market.db.pre-quality-recovery-20260926`) before creating the
next consistent backup; preserve the recoverable archive and unrelated files.
Then restore SPY/SOXX history, certify QQQ against any shared revisions, freeze
six-basket PIT, and run both existing verifiers. Do not resume the already
complete9/26 ingestion. Merge/push/deployment and this larger recovery budget
remain awaiting the final release approval specified by the approved plan.
