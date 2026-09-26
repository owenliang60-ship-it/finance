# Forward source corrections — 2026-09-26

Status: implementation and independent review complete; not deployed, no weekly
or PIT production valuation restored by this work. Boss approved the two scoped
corrections in `docs/plans/2026-09-26-index-pe-source-corrections.md`.

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
