# 2026-10-10 Finance cron recovery

## Scope and current state

Prepared in `codex/cron-recovery-oct10`, based on `db8ee59c`. Aligns with the
north-star data layer: cloud-only market writer, raw vintage observations kept,
reviewed current-data repair, shared collector and existing valuation gates.
Production has **not** been changed by this repair task. Merge, push and cloud
recovery await the project's separate promotion approval.

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

Production sequence after approval:

1. Merge/push the reviewed patch and deploy under the existing shared writer lock.
2. Under `market_db_writer`, take a consistent SQLite backup, recheck the 11
   hashes, apply the frozen plan and verify the 234 existing AEM/CRH vintage rows
   are unchanged (count alone is insufficient; compare contents).
3. Reuse `collect_fundamentals_for_symbol` for CRH's three statements at 40
   quarters and AEM's three at 8 quarters. Recompute their full metrics with the
   existing repair helper. Six requests before any retry; cap this phase at 12.
4. Recover the SPY/QQQ five-year windows with new run IDs, then reverify SOXX
   because shared source data may have changed. Recompute SOXX only if needed.
   Run the six-basket valuation phase against the **already complete** 10/10
   ingestion; do not resume/recollect the full ingestion.
5. Require both valuation verifiers, read-only fundamental audit and SQLite
   `quick_check`. Preserve prior products and report any newly exposed blocker.

Proposed recovery API hard cap: 1,000 requests total including targeted collection,
history and retries; persist request reservations across attempts as in the
existing September recovery wrapper. Primary expected demand is 613 SPY + 152
QQQ split requests, with remaining budget for incomplete historical sources.
Do not enlarge the budget or relax a gate to force a pass. No manual report
send, cron change or unrelated production cleanup is part of this recovery.

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
