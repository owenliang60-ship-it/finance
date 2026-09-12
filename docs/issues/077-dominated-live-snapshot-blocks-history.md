# Issue 077: Never-selected live holdings blocked a valid disclosure window

- Date: 2026-09-12
- Status: RESOLVED for dominated-source scope; deployed 2026-09-12 (b352520). Actually selected live identity remains pending below.
- Layer: North Star data/analysis boundary; existing index PE selection contract unchanged.

## Reproduction

The newly completed weekly source allowed an as-of 2026-09-12 replay. The retained
2026-09-11 live holdings contain CUSIP/ISIN but no LEI: 636 included/covered rows
across SPY, QQQ and SOXX fail issuer resolution. Earlier acceptance through
2026-09-10 had excluded that snapshot as unavailable.

All three live snapshots share effective date 2026-06-22 with the 2026-06-30
disclosures. Those disclosures were available by August 25/28. The frozen
selector always prefers disclosure over live at the same effective date.
Consequently these live rows can never contribute to any valuation in this run,
but `_window_snapshots` incorrectly included them in the pre-company source gate.

## Correction and boundary

Exclude live from the in-memory run scope only if a same-effective-date
disclosure was already available when live first became usable. Raw storage is
unchanged. If disclosure became available later, or live has a newer effective
date, live remains in scope and must pass the original full issuer gate. No
identity inference, source-date changes, threshold relaxation or fallback from
an invalid selected composition is introduced. Producer/verifier selection and
financial formulas are unchanged.

Three TDD cases cover always-dominated, earlier usable live, and newer-effective
live, plus source non-mutation. The first fails before the correction.
Regression: 183 tests passed locally/cloud; full suite3566 passed/4 skipped with
healthy isolated dependencies. Independent daily winner comparison covered1255
sessions per basket with no changes; all773 production weekly rows certified.

## Remaining live-source readiness

This is not a general live-identity bridge. A future quarter where live really
wins selection still needs exact-security issuer evidence before publication.
Read-only comparison against all retained disclosures found 580/636 live rows
with a unique exact CUSIP+ISIN issuer, 23 with unresolved/conflicting references,
and 33 without an exact pair. These are source-row counts (not unique issuers).
Do not turn them into a ticker-only fallback or claim future rebalances are
certified by the current window's successful run.
