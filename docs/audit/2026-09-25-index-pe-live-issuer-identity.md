# Live issuer identity implementation — 2026-09-25

Status: implementation plus eight review-boundary fixes complete; 57 current securities reviewed. **Two source blockers remain, no production rollout.** The first checkpoint below records the initial 42-security batch; the follow-up section supersedes its calendar and inheritance-boundary descriptions.

## Implementation and evidence

- Rebalance-weekend fetches defer until the conservative first weekday close boundary; missing prices after that boundary still fail. Deferral diagnostics survive the source-stage report.
- Identity selection first picks the latest same-basket PIT disclosure, including unresolved rows, then matches CUSIP. It never revives an older successful holding. Valid LEI / reviewed override / disclosure evidence merge symmetrically; explicit conflicts reject. Raw identities remain intact.
- Producer and verifier implement the bridge independently. A full physical-source replay checks 634 equity rows: SPY 504, QQQ 101, SOXX 29; all producer/verifier decisions agree, including the two rejections.
- Initial online preflight found 44 unresolved source rows. The plan's estimate missed foreign equities whose formal disclosures carry missing-CUSIP sentinel `000000000`.
- 42 reviewed overrides are backed by frozen primary evidence: 21 GLEIF ISIN query records and 21 SEC issuer-role records. Exact CUSIP in SEC identifies the issuer; the live ISIN remains additional matching scope and is not claimed to be independently confirmed in every SEC filing. Available issuer LEIs were separately corroborated. STE/EG subsidiary LEIs were rejected.
- XOM and OKE new CUSIPs were checked against June 30 issuer LEIs and agree. June 30 XOM's raw ticker is blank; lookup by its disclosed issuer identity finds the old security.

Artifacts: [summary](index-pe-live-issuer-20260925/summary.json), [initial unresolved rows](index-pe-live-issuer-20260925/initial-unresolved.json), raw live samples in the same directory, frozen sources under `docs/references/index-pe-{issuer,sec-issuer}-evidence-20260925/`.

## Bounded online acceptance

| Basket | Before: unresolved rows | After: identity errors | After: HTTP requests |
|---|---:|---:|---:|
| SPY | 37 | 1 | 2 |
| QQQ | 2 | 0 | 8 |
| SOXX | 5 | 1 | 2 |

QQQ's overall dry-run exits nonzero on the approved eight-request ceiling **after** passing identity. This is identity-only acceptance, not valuation/product certification. No local authoritative market database writes; test dependencies use isolated SQLite backups.

## Remaining blockers

1. SPY `2602335D`, `436CVR021`, empty ISIN, vendor name `TPG INC`, weight 0.00000299%. The current exact-security override schema cannot certify it. The identifier suggests a CVR; Hologic's [issuer acquisition release](https://www.hologic.com/about/press-release/blackstone-and-tpg-complete-acquisition-hologic) establishes that its stockholders received a non-tradable CVR, but does not itself bind this exact CUSIP. Do not infer it is TPG common equity or silently discard it by its small weight. Exact security-type evidence and the normalization treatment remain to be decided.
2. SOXX `0EDE.L`, vendor CUSIP `F2933A109`, ISIN `NL0009538784`. NXP's [issuer FAQ](https://www.nxp.com/company/about-nxp/investor-relations/investor-faqs%3AINVESTORS-FAQS) explicitly binds that ISIN to `N6596X109`; the [June 2026 SEC issuer filing](https://www.sec.gov/Archives/edgar/data/1413447/000001961726000314/primary_doc.xml) confirms the latter CUSIP. An exact SPY NXPI override is valid; SOXX's contradictory identifier must remain rejected. Correcting the vendor/source evidence is outside this plan's raw-preserving inheritance change.

Three-basket identity acceptance and full-window restoration are **not complete**. Production continues serving the previous completed product. No merge/push/deployment/recovery has occurred.

## Validation

- Calendar TDD: 2 failing deferral scenarios before implementation; 36 source-stage tests passed afterward.
- Producer TDD: 17 failing bridge cases before implementation; 94 focused producer/history/evidence tests passed afterward.
- Verifier TDD: 7 failing new cases before implementation; 128 verifier tests, including 20 producer parity cases, passed afterward.
- Window adapter regression: fails without passing the complete source inventory; passes with it.
- Evidence TDD: newly expected records absent → test failure; archived sources + scoped records → pass.
- Final focused suite: `python -m pytest tests/test_backfill_soxx_historical_pe.py tests/test_backfill_index_pe_history.py tests/test_index_pe_source_identity.py tests/test_verify_index_pe_history.py tests/test_reviewed_issuer_evidence.py tests/test_canonical_issuer_identity.py tests/test_sec_issuer_evidence.py -q` → **282 passed in 6.77s**.
- Final full suite: `python -m pytest -q` → **3988 passed, 1 failed, 1 skipped, 17 warnings in 470.49s**. The unmodified `tests/test_macro.py::TestIntegration::test_fetch_macro_snapshot_e2e` failed because FRED DGS10 HTTPS returned `SSLEOFError`, leaving `us10y=None`.
- Targeted FRED recheck, loading the same `.env` before pytest collection: **1 passed in 17.40s**. This does not turn the earlier full invocation into a fully green result. No unrelated macro code was changed.
- Main-thread `/cr`: no actionable implementation findings; source-blocked deployment decision remains. One subagent independently implemented verifier/test coverage and later gathered bounded primary SEC evidence; all returned changes and issuer bindings were checked in the main thread.
- All four production Python files pass Python 3.10 grammar parsing. `git diff --check` clean.


## Review follow-up: boundary corrections

- Missing CUSIP: current overrides explicitly allow only `null`, `""`, `N/A`, `000000000` with exact ISIN. Real historical sentinel rows projected to the future quarter test the failure before patch and both resolvers after it. STE's previously reviewed wrong raw LEI remains a scoped correction.
- Historical as-of: a current live response cannot contribute before its fetch day; skip it with `live_skipped` diagnostics, without inferring freshness from a truncated calendar.
- Quarterly session boundary: Good Friday, observed Juneteenth starting 2022, weekends and New York 16:00 close are covered, including holiday Monday and winter time. Only the four configured quarterly months are supported. Nominal stored rebalance metadata remains unchanged. Sources: [NYSE holiday calendar](https://www.nyse.com/trade/hours-calendars), [Juneteenth adoption](https://www.nyse.com/publicdocs/nyse/markets/nyse/rule-filings/sec-approvals/2021/%28SR-NYSE-2021-56%29%2034-93183.pdf).
- Duplicate evidence: one valid key plus an unresolved row is unavailable inheritance, not an affirmative conflict. Two different valid keys/nonempty ISINs or an explicit row conflict still reject.
- Expiry: reconstruct original proof with reviews valid on the source date, then require the same identity to remain supported by reviews also active on the live date. A raw LEI independent of expired reviews remains usable; an expired correction cannot turn its wrong raw LEI into inherited evidence.
- Canonical keys: FER/APTV/TEL retain historical SEC keys with reviewed LEI equivalence; no arbitrary switch to LEI keys.
- Refresh: a requested live refresh which must defer or lies outside the window now fails explicitly.
- Runtime: inheritance resolves only selected snapshots and caches by live date. Ordinary historical row verification remains required.

Enforcing expiry exposed **15 securities / 17 live rows** formerly certified through expired proof: CVX, CTAS, TDG, BKR, EXPE, EXR, LH, PHM, KHC, TPL, J, INVH, CSGP, TKO, MTSI. All were independently checked against fresh SEC issuer-role sources; six previously supported issuer LEIs were rechecked with GLEIF. New bounded review records were added, preserving the historical records and canonical identities. The review batch is now **57**. CSGP's legacy wrong raw LEI is still not an identified legal entity (GLEIF 404); its exact-security correction is supported by independently verified correct CIK/LEI, and is never treated as an equivalent LEI.

Frozen 634-row replay remains producer/verifier-consistent: SPY 503/504 resolved, QQQ 101/101, SOXX 28/29. The two original source blockers remain. This is offline replay of retained primary inputs, not a claim of a new published valuation.

Follow-up TDD evidence: 12 missing-CUSIP/canonical regression failures; 5 producer mixed/expiry/laziness failures; 8 calendar/refresh failures reproduced before correction. Validation: main eight-finding fix full suite **4078 passed, 1 skipped, 17 warnings in 538.87s** (`python -m pytest -q`). The final Christmas Eve close correction was then tested red-to-green with three additional cases; final focused suite **374 passed in 6.94s** across the seven affected test files. The full suite number is the checkpoint before that final small close-time correction, not a claim that its three added tests were in that invocation. Python 3.10 grammar and diff checks also pass.

- Final calendar review also covers Christmas Eve as the first effective session (December 21 can be the third Friday): 13:00 New York early close, with pre-close / exact-close / post-close regression cases.


Final follow-up online preflight (57-review configuration): SPY 2 HTTP requests / one unresolved CVR-like source row; QQQ 8 HTTP requests / zero identity errors, then expected budget exhaustion; SOXX 2 HTTP requests / one conflicting-CUSIP row. Aggregate 12 requests, each process capped at 8. No source gate was relaxed to remove either blocker; no production writes, merge, push or deployment.
