# Live issuer identity implementation — 2026-09-25

Status: implementation complete for Tasks 1–3; Task 4 evidence partially complete; **source blockers remain, no production rollout**.

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
