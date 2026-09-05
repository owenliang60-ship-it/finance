# 060 — Fiscal date alias repair loses cross-statement metrics

Date: 2026-09-05
Status: fixed in worktree; targeted tests and read-only data comparison passed. Merge/deployment pending approval.

## Cause and reproduction

`fiscal_repair._recompute_metrics_in_conn` reuses `compute_metrics`, which joins balance-sheet and cash-flow inputs by exact income-statement date. Equivalent representations of one fiscal quarter can use different dates across tables. Moving only income from 2025-09-30 to 2025-10-03 leaves valid BS/CF rows under09-30, so recomputation changes debt/assets0.25, FCF margin0.15 and ROA0.20 to NULL. Existing tests exercised alias replacement without populated counterpart statements.

The live read-only audit also found historical mixed-date quarters SNDK FY2025Q1 and MDT FY2024Q4. Their affected metrics were already NULL in the pre-repair archive; this is not evidence that the recent production repair newly erased those values.

## Fix contract

Match validated, unique FY/quarter identities in the common metrics engine, keeping raw dates immutable. Reuse formulas, including TTM beginning-balance calculations. Reject ambiguous/conflicting matches rather than choosing an arbitrary row. Keep exact-date compatibility only when fiscal identity is incomplete. Fixing only the repair adapter is insufficient because normal weekly recomputation would recreate the NULL values.

Plan: `docs/plans/2026-09-05-fiscal-metrics-alignment.md`.

## Verification

- 17 new cases: 15 failed before implementation, two exact-date/unknown-key compatibility cases already passed. Metrics/repair suites108 passed; wider related suites419 passed/1 skipped; Python3.10 syntax passed.
- Full suite: **2903 passed/4 skipped**, 235.62s. Additional uncapped-history read-only validation across934 current-base symbols generated7711 metric rows in memory with zero exceptions.
- Raw dates remain unchanged. All eight-quarter fixture metrics match the aligned-date baseline exactly, including TTM beginning assets/equity and subsequent return deltas. All nine combinations of income/BS/CF × legacy/collector/manual repair preserve debt/assets25%, FCF margin15%, ROA20%, ROIC16/130; ordinary recomputation preserves those results.
- Read-only local snapshot of934 active eligible stocks, up to20 quarters per stock, compared original4971734 engine with the new engine in memory: no matching conflicts;21 symbols/61 metric rows change only in cross-statement-derived fields. All checked income-derived EPS, revenue/net-income growth and CAGR fields remain equal. No production metrics or raw data were written.
- Affected symbols: AER, ARES, ARGX, ARMK, ATI, BIP, BSBR, CCEP, DAR, HRL, MDLN, MDT, MSTR, NMR, PFE, PGR, RVTY, SNDK, TAK, TECK, WSM. These counts describe the current snapshot/lookback, not an exhaustive all-history rebuild.
- Example restored values: SNDK FY2025Q1 debt/assets3.4197%, FCF margin−10.5151%; MDT FY2024Q4 debt/assets27.8103%, ROA2.9073%. Restoring older beginning balances also restores downstream TTM ROA/ROE and their deltas.
- Post-approval production follow-up: deploy code; use writer lock and the existing archive-aware transaction adapter for scoped metrics recomputation, preserving raw statements/vintages. Re-audit live affected groups before choosing exact rebuild targets.
