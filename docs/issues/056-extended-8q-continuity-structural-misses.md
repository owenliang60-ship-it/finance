# 056 — Extended 8Q continuity gate conflates structural reporters with missing data

**Date:** 2026-09-05
**Status:** Open — independent follow-up required
**Discovered during:** Selection Compass full fundamental backfill

## Symptom

After the full Extended backfill, `verify_fundamental_coverage.py --json` reported:

- three-table coverage: `933/935 = 99.79%` — PASS;
- profile coverage: `935/935 = 100%` — PASS;
- forward coverage: `902/931 = 96.89%` — PASS;
- 8Q continuity: `884/935 = 94.55%` — FAIL against 95%;
- unattributed gaps: `0`.

The continuity result misses the threshold by five symbols even though collection itself is healthy.

## Root cause

The denominator is every active Extended security, while the predicate assumes every issuer provides eight
quarterly rows in all three statements with adjacent fiscal dates at most 120 days apart. At least three
structural classes violate that assumption:

1. foreign/ADR issuers that report semiannually (typical gaps around 183–185 days);
2. newly listed or newly separated companies with fewer than eight public quarters;
3. vendor irregularities or duplicate fiscal dates. TXT and VG each returned differing payloads for one
   duplicate fiscal date across income/balance/cashflow; the vintage PK guard correctly rolled all six
   writes back after three attempts.

The problem is therefore not “API backfill failed for >5% of the pool.” It is that the continuity metric
currently treats structurally non-quarterly reporters and genuine missing data as the same failure class.

## Impact

- The global Extended Stop F continuity gate remains red at 94.55%.
- Selection Compass is not silently widened or weakened: its own contract needs five continuous income
  quarters and exposes exact runtime coverage. Post-backfill it passes at `896/935 = 95.83%`, with RVOL
  coverage `924/935 = 98.82%`.
- No threshold was changed, and no structural miss was rewritten as `ok`.

## Required follow-up

Create a separate plan to define the 8Q continuity denominator and attribution model. At minimum compare:

1. keep all active securities in the denominator but add explicit structural statuses (`semiannual_reporter`,
   `insufficient_public_history`, `vendor_duplicate_fiscal_date`);
2. define an applicability denominator for 8Q quarterly continuity while still reporting excluded counts;
3. retain the current all-in denominator but use a reporting-frequency-aware continuity predicate.

Do not resolve this by merely raising `FUNDAMENTAL_QUARTER_GAP_MAX_DAYS` or lowering the 95% threshold;
either change would hide distinct economic/reporting semantics.
