# Index PE source normalization asks for a future trading date at rebalance weekend

- Date:2026-09-19; status:diagnosed, not repaired in this diagnostic turn.
- Production: dcd9720; failed run:index-pe-2026-09-19-45a0bd47; SPY/QQQ/SOXX all failed preflight at12:23 CST.

## Evidence

Cloud `daily_price` calendars for each ETF end2026-09-18. `infer_basket_rebalance_close("2026-09-19")` returns2026-09-18. `_fetch_sources` attempts a newly dated live snapshot. `normalize_fund_disclosure_snapshot` calls `next_trading_date` for its composition_effective_date and raises `no trading date after2026-09-18` before the run's window filter can exclude a not-yet-effective composition. The next post-rebalance session has not occurred; this is not evidence that Friday prices failed to collect.

Direct read-only reproduction with each actual ETF calendar yields the same exception. No future session should be invented or appended to observed prices to make the normalizer pass.

## Impact

- yfinance forward collection completed (942 successes); FMP weekly ingestion manifest2026-09-19 is complete. Wrapper source-stage summary returnedrc0.
- History stage made6 source requests and all baskets recorded run_failed withrows_written=false. Existing basket_weekly_pe_history remains latest2026-09-11:SPY261/QQQ261/SOXX251 rows.
- `set -e` stops before valuation-phase PIT freeze and the downstream verifiers. The already completed ingestion should not be blindly rerun.
- This path is separate from the new fundamental-quality checker; that change did not modify the disclosure normalizer/history runner.

## Repair constraints / next step

Handle source-window admissibility before trying to normalize a live composition whose first effective trading date lies beyond this run's observed valuation window. Deferral must be explicit and narrowly proved; a genuinely stale/missing calendar must still fail. Historical/source identity gates, aliases, weights and the old product remain unchanged. Once a later trading session is in scope, the new live source must undergo the full normalizer and issuer gate (issue077); never silently retain an older composition to bypass that gate.

Regression coverage should include rebalance Friday/weekend, later session present, stale calendar and historical as-of windows. After code acceptance, recovery is history full-window →PIT valuation phase →verifiers with a new run_id, not another complete forward ingestion.

The existing runbook reports ~46min for SPY alone. Do not launch a long recovery under the shared writer lock across the newly approved14:00 fundamental-quality job without coordinating timing. No recovery was launched by this diagnostic turn.

## 2026-09-25 follow-up: calendar fix alone would not recover — live issuer gate is structurally unpassable

- Status: diagnosed; repair approved as plan A (live rows resolve issuer via CUSIP/ISIN→LEI from the latest formal disclosure), not yet implemented. Morning report `0d` still shows data through 2026-09-11.
- Read-only preflight on cloud (production 5751b79, `backfill_index_pe_history.py --dry-run --allow-network --as-of 2026-09-26 --max-api-requests 30`, one basket per process, no writes/lock): the calendar error no longer fires because `daily_price` now contains sessions after 2026-09-18. All three baskets instead fail `source issuer identity gate` on the new live snapshot `holding_date=2026-09-25`: SPY 504/504, QQQ 101/101, SOXX 29/29 rows `issuer_identity_unresolved` (including NVDA/AAPL).
- Root cause: `resolve_issuer_identity` (`src/data/fund_issuer_identity.py`) accepts only a disclosed LEI or a reviewed override matched by CUSIP/ISIN. FMP live ETF holdings carry `securityCusip`/`isin` but never `lei` (cloud 2026-09-11 live snapshots: SPY 505 rows / QQQ 107 / SOXX 33, all with 0 LEI; the 2026-06-30 disclosures carry LEI on every row). Overrides cover only ~two dozen exceptional securities.
- Why 9/12 passed: the then-effective composition had an earlier-public formal disclosure, so issue077 excluded the live snapshot from the computation domain. After the 2026-09-18 quarterly rebalance the live snapshot is the only source for the new composition, so it must pass the gate itself and cannot. Waiting for the 9/30 N-PORT does not reliably self-heal: per issue077 a live source that was available before the disclosure still has to pass the full issuer gate.
- Consequence: the Saturday 2026-09-26 run is expected to fail again at this gate; `set -e` also keeps the PIT consensus valuation and verifiers blocked. Every future quarterly rebalance would reproduce this.
- Repair direction (plan A): resolve a live row's issuer from the most recent formal disclosure row with the same CUSIP/ISIN (security-level identity, never ticker-only); live securities absent from that disclosure (rebalance entrants) stay unresolved and go through reviewed overrides. Fix the calendar boundary above in the same change. Recovery after acceptance: history full window → PIT valuation phase → verifiers with a new run_id.

## 2026-09-25 implementation checkpoint (not deployed)

Tasks 1–3 implemented and independently replayed in `codex/index-pe-live-issuer-identity`. Initial real preflight produced 44 unresolved rows, including missing-CUSIP foreign disclosure rows; 42 reviewed overrides now have frozen primary evidence. QQQ passes identity; SPY `2602335D` (empty ISIN / possible CVR) and SOXX `0EDE.L` (CUSIP conflicts with issuer-published NXP identifiers) remain fail-closed. Status remains **open / source-blocked**, not repaired in production. No merge, push or recovery. Full evidence and validation: `docs/audit/2026-09-25-index-pe-live-issuer-identity.md`.
