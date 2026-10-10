# 2026-10-10 Finance cron recovery

## Scope and current state

Completed on 2026-10-10 in `codex/cron-recovery-oct10`, based on `db8ee59c`. Aligns with the
north-star data layer: cloud-only market writer, raw vintage observations kept,
reviewed current-data repair, shared collector and existing valuation gates.
Boss explicitly approved merge, push, deployment, repair and recomputation.
Functional commit `dcdc799f` was fast-forwarded to local/origin/cloud main;
cloud post-deployment tests: **95 passed**. The final recovery returned **rc=0**,
both valuation verifiers passed, and SQLite `quick_check=ok`.

| Product | Verified rows | Latest date |
|---|---:|---|
| SPY weekly history, both lines | 261 | 2026-10-09 |
| QQQ weekly history, both lines | 261 | 2026-10-09 |
| SOXX weekly history, both lines | 255 | 2026-10-09 |
| Six consensus baskets | 6 in the new vintage | 2026-10-10 |

Fundamental audit: **WARN / rc0**, **877/910 ready (96.37%)**, repair targets **0**.
Premium was rebuilt through the existing publisher: **61 members**, unchanged
membership, with refreshed readiness metadata. AEM and CRH no longer have
`fetch_failed` coverage. Other vendor warnings remain explicit.

The natural run completed forward ingestion for 2026-10-10. SPY and QQQ history
failed without publishing candidate rows; their last points remain 2026-10-02.
SOXX completed through 2026-10-09. The six consensus baskets remain at
2026-10-03 because the wrapper stopped before valuation. Premium published
61 names with 876/910 fundamental-ready (96.26%).

## Verified causes and changes

### CHTR blocks QQQ splits

The actual FMP response contains one event: 2016-05-18, numerator 4521,
denominator 5000, `splitType=null`. Both
[Nasdaq ETA2016-103](https://www.nasdaqtrader.com/TraderNews.aspx?id=ETA2016-103)
and [Charter's completion announcement](https://ir.charter.com/static-files/203adad5-d75a-48c9-a65f-2f16769f80f4)
identify a parent-merger exchange of 0.9042 new shares per old share.

Only that exact symbol/date/ratio/null tuple receives `merger-exchange`.
Other missing types remain errors. Error text now includes the requested symbol
and row index. The null is retained in the client response as `sourceSplitType`
and frozen in `cron-recovery-20261010/chtr-provider.json`; the database's existing
split schema does not persist that additional field. Date and ratio are unchanged.

### CRH blocks SPY and fundamental collection

Nine existing current rows require period-only identity correction:

| Date | Before | After | Tables |
|---|---|---|---|
| 2024-03-31 | 2024 Q2 | 2024 Q1 | income, balance, cashflow |
| 2024-09-30 | 2024 Q4 | 2024 Q3 | income, balance, cashflow |
| 2025-09-30 | 2025 Q4 | 2025 Q3 | income, balance, cashflow |

Evidence: [2024 Q1 SEC filing](https://www.sec.gov/Archives/edgar/data/849395/000162828024022452/crh-20240331.htm)
(revenue $6,533m, shareholder net income $116m) and
[2025 Q3 SEC release with 2024 comparatives](https://www.sec.gov/Archives/edgar/data/849395/000162828025049547/q3resultsannouncement2025.htm).
Current provider data agrees on these quarterly identities. Normal collection
then refreshes financial values and adds missing quarters. The repair helper
itself cannot change financial fields.

Do not mechanically relabel older half-year history. The existing 2023-09-30/Q4
record remains an unresolved historical source issue, outside these nine rows.
The valuation selector's continuity gate excludes incomplete four-quarter
windows. This work does not certify all CRH historical financial data.

### AEM blocks fundamental collection

The provider uses the release date 2024-10-30 for 2024 Q3; the
[issuer's release](https://www.agnicoeagle.com/English/news-and-media/news-releases/news-details/2024/AGNICO-EAGLE-REPORTS-THIRD-QUARTER-2024-RESULTS-RECORD-FREE-CASH-FLOW-FOR-THE-FOURTH-CONSECUTIVE-QUARTER-BALANCE-SHEET-STRENGTHENED-BY-FURTHER-DEBT-REDUCTION-WELL-POSITIONED-TO-ACHIEVE-GOLD-PRODUCTION-AND-COST-GUIDANCE-ONGOING-EXP-10-30-2024/default.aspx)
establishes September 30 as the period end.

Current/valuation input correction matches AEM + CIK 0000002809 + USD +
2024/Q3 + October 30, changing only the date. The collector still stores raw
provider payloads in vintage; prior strict-PIT observations are not rewritten.
The two already-misdated current income/balance rows require explicit repair.
Cashflow already has the correct current date. Financial amounts continue
through the existing provider/revision policy, not a claim of a new full
statement audit. Backfill memory and stored current inputs use the same date.

## Concrete recovery plan

`cron-recovery-20261010/identity-plan.json` freezes all 11 complete old-row hashes
and their exact field changes. `apply_reviewed_identity_updates` rejects stale
hashes, financial-field changes, duplicate target rows/dates/quarters and failed
metric recomputation. Changes, old-row archival and full metric recomputation
commit together. Replaying a stale plan fails rather than assuming success.

Executed recovery sequence:

1. Merge/push the reviewed patch and deploy under the existing shared writer lock.
2. Under `market_db_writer`, take a consistent SQLite backup, recheck the 11
   hashes, apply the frozen plan and verify the 234 existing AEM/CRH vintage rows
   are unchanged (count alone is insufficient; compare contents).
3. Reuse `collect_fundamentals_for_symbol` for CRH's three statements at 40
   quarters and AEM's three at 8 quarters. Recompute their full metrics with the
   existing repair helper. Six requests before any retry; cap this phase at 12.
4. Recover SPY/QQQ with new run IDs, then reverify SOXX. SOXX required a source-only
   recomputation, described below. Run the six-basket valuation phase against
   the **already complete** 10/10 ingestion; no full ingestion was resumed.
5. Require both valuation verifiers, read-only fundamental audit and SQLite
   `quick_check`. Preserve prior products and report any newly exposed blocker.

Recovery API hard cap: **1,000/1,000 requests**, including targeted collection,
history and retries, reserved durably before every HTTP attempt. The limit was
not increased. No manual report was sent, no cron was changed, and no unrelated
production cleanup was performed.

## Production recovery and independent checks

- Initial preflight stopped before any data/API operation because free space
  was 24MB below the existing backup gate. Only this task's temporary staging
  and pytest-82 directory were removed; the gate was not weakened.
- Consistent backup: 1,424,576,512 bytes, compressed to 363,153,439 bytes. Cloud
  gzip round-trip and local decompression both matched SHA256
  `32abca8537b5e03f84acd8ffc8c6830d7cdc3842f079bbdac399d4a08e009eaa`.
  After recovery completed, identical compressed bytes were also verified and
  the cloud copy was archived to the local long-term location
  `data/backups/cron-recovery-20261010/` in the primary checkout. Its receipt is
  retained both there and at cloud `data/cron-recovery-20261010/backup-local-archive.json`.
  Historical run receipts retain the original cloud path; that file has moved.
- The 11 frozen current-row hashes matched at execution. All old rows were
  archived, labels/dates repaired, and metrics recomputed. The six targeted
  statement requests succeeded through the shared collector.
- All **234 original vintage rows** were independently extracted from the
  backup and compared field-for-field with production after collection. They
  are unchanged; current count is **300**, reflecting 66 appended observations.
- SPY and QQQ completed under `index-pe-recovery-20261010-1-spy/qqq`.
  SOXX's previous 10/9 MU TTM value no longer matched current raw-source visibility:
  the 14:00 fundamental job had changed MU's latest `accepted_date` to 10/9.
  The existing next-trading-day rule therefore uses the prior four visible
  quarters on 10/9: $50.469bn, versus the old product's $84.969bn. SPY/QQQ
  already agreed with the newly visible source selection.
- The first automatic SOXX refresh stopped at request 1,000, with no candidate
  weekly rows published. Its failed run and reports remain intact.
- Attempt 2 reused the backup/checkpoint/ledger and invoked the existing
  `run_backfill(client=None, store=...)` path. SPY/QQQ were recertified; SOXX's
  whole window was rebuilt under `index-pe-recovery-20261010-2-soxx`. Identity,
  quality, sanity quarantine, coverage, and transaction-internal independent
  verification remained active. **Zero additional HTTP requests** were made.
  The source-only wrapper replay was checked offline; the historical backfill
  suite passed **52 tests**, and independent review passed the targeted offline
  contracts. This did not relax a data gate or substitute unverified inputs.
- Both history verification passes and the full forward verifier passed.
  Final result: **complete / rc0 / quick_check ok / 1,000 requests**. Cloud evidence
  is in `data/cron-recovery-20261010/attempt-2/`; the local copy is under
  `reports/cron-recovery-20261010/production/cloud/` in the recovery worktree.
- The standard `sync_to_cloud.sh --pull` completed with identical snapshot
  SHA256, SQLite integrity and local health checks passing. Direct local SQL
  confirmed all three histories through 10/9, six consensus baskets through
  10/10, and no AEM/CRH `fetch_failed` rows. The derived Premium artifact was
  copied atomically and validated separately (61 members, 877 ready).
  Compact machine-readable evidence: `cron-recovery-20261010/production-summary.json`
  and `cron-recovery-20261010/local-summary.json`.

## Verification

- TDD reproduced CHTR's null-type failure and AEM's misdated current writes.
- Real temporary SQLite replay: all 11 identity changes applied; CRH and AEM
  income/balance/cashflow each report `ok`; repeat collection passes;
  `quick_check=ok`. The first repair recalculated 80 metrics, followed by full
  metrics after collection (CRH 45, AEM 40).
- Production read-only preflight: **11/11 row hashes still match**; existing
  AEM/CRH vintage count is 234. Snapshot inputs and receipt are in the worktree's
  `reports/cron-recovery-20261010/`; their hashes are recorded beside the plan.
- Independent review found a lowercase-symbol/zero-row UPDATE issue. Added a
  failing regression, normalized the symbol, and require UPDATE rowcount=1.
  Reviewer rerun: **149 passed**; no remaining blocking finding.
- Final affected suite: **242 passed in 5.31s** locally; the same six-file suite
  in isolated `/tmp/finance-cron-oct10-review` on cloud Python 3.10:
  **242 passed in 41.07s**. A one-request live CHTR replay through the candidate
  client also passed, without any production database writes.
- Full invocation: **4,716 passed, 12 failed, 4 skipped** in 350.01s. Seven
  failures were missing isolated-worktree historical fixtures. After copying
  the existing frozen breadth CSV and 3,882 benchmark price rows into the
  isolated test database, `tests/test_breadth_buy_quality.py`: **16 passed**.
  The remaining five morning-report failures reproduce by name and assertion
  on a separate unmodified `db8ee59c` archive: **166 passed, 5 failed, 1 skipped**.
  They concern missing concept registry/legacy category expectations, not this
  patch. The full run began before the final lowercase-symbol fix; the final
  242-test local/cloud runs and independent 149-test review cover that fix.
  This is not a claim of an all-green second full invocation.
- Python 3.10 AST parsing and `git diff --check` pass. Direct SQLite assertions
  confirm 11 archived identity rows, corrected AEM current date and three raw
  AEM vintage payloads retaining the provider date.

Two subagents were used: official issuer evidence retrieval and independent
code review. Their conclusions were checked against the diff, real source
responses, temporary SQLite results and command output in the main thread.

## Other observed vendor defects retained as explicit warnings

ASML 2025 Q3's incoming date/CIK conflict, SONY's fiscal-year convention and
publication-date changes, WDC's previously rejected wrong-quarter balance row,
and STRF's historical duplicate fiscal alias remain outside the two reported
run failures' repair plan. Their source data is not silently preferred, and no
error status or cooldown is relabeled clean. Future collection can still flag
these unresolved sources. Evidence investigation was read-only.
