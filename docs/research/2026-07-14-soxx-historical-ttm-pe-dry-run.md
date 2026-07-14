# SOXX Historical GAAP TTM PE — Read-only Dry-run Result

**Date:** 2026-07-14

**Status:** implementation dry-run passed; not yet persisted, verified, merged, pushed, or deployed

**Method:** fixed retrospective disclosure weights + strict as-of GAAP TTM net income + 7-calendar-day HMC lookup

**Scope:** 2021-09-20 through latest local SOXX trading date, 2026-07-13

## Executive result

The final network-enabled dry-run produced a complete 1,207-trading-day series and passed the 95% publication target:

| Metric | Result |
|---|---:|
| SOXX trading dates | 1,207 |
| Computed rows | 1,207 |
| Publishable rows | 1,207 |
| Publishable coverage | 100.00% |
| Current primary PE proxy | 51.07x |
| Primary historical percentile | 79.20% |
| Current secondary uncapped basket PE | 40.80x |
| Secondary historical percentile | 52.20% |

The result is a **SOXX rebalance-weighted GAAP TTM PE proxy using fixed retrospective snapshot weights**. It is neither official SOXX P/E nor historical forward P/E.

## Current reading and range

The current row uses the live snapshot fetched on 2026-07-14, applied from the inferred 2026-06-22 effective date, and valued on the latest local SOXX trading date, 2026-07-13. Because the live weights have drifted since rebalance, the row is explicitly labeled `live_tail_weaker` / `live_snapshot_backcast_proxy`.

| Series | Current | Percentile | Median | Minimum | Maximum | Observations |
|---|---:|---:|---:|---:|---:|---:|
| Primary: fixed-weight earnings-yield inverse | 51.07x | 79.20% | 40.60x | 13.40x on 2022-10-14 | 72.78x on 2025-11-06 | 1,207 |
| Secondary: uncapped aggregate basket PE | 40.80x | 52.20% | 39.22x | 14.05x on 2022-10-14 | 63.03x on 2024-11-07 | 1,207 |

Current primary weight coverage is effectively 100%, with no missing current member. Across all daily rows, weight coverage was 95.34% minimum, 99.43% median, and 100.00% maximum.

## Observed snapshot anchors

These are actual quarter-end disclosure observations when the date exists in the SOXX trading calendar; weekend/holiday holding dates use the preceding available valuation date. The last row is the weaker live-tail snapshot.

| Holding date | Valuation date | Primary PE | Secondary PE | Weight coverage | Evidence tier |
|---|---|---:|---:|---:|---|
| 2021-09-30 | 2021-09-30 | 27.43x | 29.14x | 95.47% | disclosure |
| 2021-12-31 | 2021-12-31 | 29.48x | 31.84x | 95.34% | disclosure |
| 2022-03-31 | 2022-03-31 | 24.32x | 26.31x | 98.48% | disclosure |
| 2022-06-30 | 2022-06-30 | 15.87x | 17.42x | 98.83% | disclosure |
| 2022-09-30 | 2022-09-30 | 14.02x | 14.89x | 98.01% | disclosure |
| 2022-12-31 | 2022-12-30 | 16.88x | 17.37x | 98.75% | disclosure |
| 2023-03-31 | 2023-03-31 | 23.09x | 24.61x | 99.11% | disclosure |
| 2023-06-30 | 2023-06-30 | 32.45x | 33.72x | 99.21% | disclosure |
| 2023-09-30 | 2023-09-29 | 32.82x | 32.12x | 99.43% | disclosure |
| 2023-12-31 | 2023-12-29 | 42.02x | 38.91x | 99.41% | disclosure |
| 2024-03-31 | 2024-03-28 | 45.28x | 47.88x | 99.62% | disclosure |
| 2024-06-30 | 2024-06-28 | 46.28x | 52.50x | 99.71% | disclosure |
| 2024-09-30 | 2024-09-30 | 47.89x | 50.65x | 100.00% | disclosure |
| 2024-12-31 | 2024-12-31 | 52.94x | 52.69x | 100.00% | disclosure |
| 2025-03-31 | 2025-03-31 | 41.74x | 40.00x | 100.00% | disclosure |
| 2025-06-30 | 2025-06-30 | 55.89x | 49.35x | 100.00% | disclosure |
| 2025-09-30 | 2025-09-30 | 62.64x | 52.31x | 100.00% | disclosure |
| 2025-12-31 | 2025-12-31 | 43.96x | 44.49x | 100.00% | disclosure |
| 2026-03-31 | 2026-03-31 | 42.68x | 38.72x | 100.00% | disclosure |
| 2026-07-14 | 2026-07-13 | 51.07x | 40.80x | 100.00% | live tail, weaker |

## Source and identity evidence

- Loaded 19 historical disclosure snapshots plus one live snapshot.
- Eligible equity membership ranged from 26 to 30; raw equity weights were 99.99999998%–100.00000000%. All snapshots passed the blocking 25–31 member and 99.5%–100.5% raw-weight gates.
- The union contains 42 disclosed raw members and 41 economic evaluation symbols. `TER` and `WOLF` are alias support symbols.
- FMP disclosure identifies Teradyne as `TERN`, but FMP market/fundamental data for `TERN` belongs to Terns Pharmaceuticals. Exact CUSIP `880770102`, ISIN `US8807701029`, vendor CIK, and raw-symbol matching therefore trigger an authoritative `TERN → TER` correction. The wrong `TERN` series is not requested or evaluated.
- `CREE → WOLF` remains a raw-first fallback: a member uses one complete source key, never a splice of market cap from one ticker and income from another.
- Cash-fund rows such as BISXX are retained as raw evidence but excluded before the equity snapshot gate and valuation universe.
- Full-range completeness finished at 39/41 symbols for fundamentals (`ALAB`, `ARM` incomplete relative to the 2021 start) and 35/41 for HMC (`ALAB`, `ARM`, `CRDO`, `CREE`, `WOLF`, `XLNX`). Both rates remained below the strict `>20%` fuse; empty API responses were zero. These are mainly pre-listing/corporate-history gaps, not current-row gaps: current member coverage remained 100% and every trading date still published.
- Non-September membership deltas are retained and warned rather than silently attributed to the scheduled rebalance. Observed warnings occurred on 2021-12-31, 2022-03-31, 2023-06-30, 2025-06-30, and the 2026-07-14 live snapshot.

## Market-cap sanity outcome

The dry-run re-ran jump, price alignment, split, and implied-shares checks on all 41 evaluation symbols.

- **KLAC / issue035:** detected the 2026-06-10 through 2026-06-23 divide-by-ten window, planned and executed a forced in-memory refresh for 2026-06-03 through 2026-06-30, and left zero post-refresh quarantined dates. Bad cached rows did not enter the PE proxy.
- **MCHP:** detected the 2026-02-02 through 2026-02-06 doubling window, refreshed 2026-01-26 through 2026-02-13, and left zero post-refresh quarantined dates.
- **SLAB:** the approximately 49% move was price-aligned and accepted as plausible rather than mechanically quarantined.
- **NVDA/AVGO/LRCX and other split candidates:** split ratios change the implied-share anchor only when the observed share count changes by that ratio. Vendor price and HMC histories already back-adjusted together are not adjusted twice.
- **CREE:** ten raw dates from 2021-09-20 through 2021-10-01 remained quarantined after refresh, but the complete WOLF fallback series supplied the economic member.
- **XLNX:** 102 dates through 2022-02-11 remained quarantined. No unproven XLNX→AMD mapping was made; the resulting early-period missing weight is visible in the 95.34% minimum coverage.

## Comparison with official fund characteristic

The [official iShares SOXX page](https://www.ishares.com/us/products/239705/ishares-semiconductor-etf) reported a P/E ratio of 72.88 as of 2026-07-10. The dry-run primary proxy is 51.07x, 21.81 turns or approximately 29.9% lower.

This difference is non-blocking and should not be read as an error by itself. The official characteristic uses BlackRock's portfolio methodology, while this reconstruction explicitly uses GAAP TTM net income, fixed retrospective snapshot weights, a defined treatment of losses/missing data, seven-day HMC staleness, and a 2026Q2 live-tail proxy. The official value is a useful magnitude check, not an equality target.

## Read-only proof and remaining gate

The dry-run opened `/Users/owen/CC workspace/Finance/data/market.db` read-only. Before and after file metadata were identical:

```text
mtime=1783982579 size=882147328
```

No backup was created because no database write occurred. The persisted-table verifier was intentionally not run: dry-run computed results exist only in memory and `/tmp/soxx_postreview_dry_run_20260714.json`, not in `basket_ttm_valuation`. Production acceptance still requires a separately approved locked write/backfill followed by the independent `mode=ro` verifier and export. No cron is proposed in this phase.

Runtime provenance: the final network dry-run was executed after all valuation, fuse, identity, sanity, and database-safety fixes. The subsequent commit only added explicit `empty_responses` / `incomplete` report fields, the query command's last-publishable Markdown line, documentation, and tests; it did not change the computed valuation, fuse decision, or read-only behavior. Consequently, the retained JSON is functionally representative of the final implementation but is not a byte-for-byte artifact from the branch's final documentation commit.

## Limitations

- Historical holdings are fixed retrospective quarter-end fund snapshots mapped to inferred rebalance intervals, not official daily index weights.
- Disclosure availability is retained; rows before `composition_available_date` are ex-post composition proxies.
- FMP financial history can contain later restatements and is not a complete vintage database.
- The current live tail uses fetch-date drifted weights because the next historical disclosure is not yet available.
- The verifier independently recomputes persisted membership evidence and the final/base market-cap sanity classification. After a forced range refresh replaces source rows, however, it cannot reconstruct the historical fact that the refetch occurred; `pre_refresh_status`, refresh-window, and refresh-attempt fields remain producer-side audit evidence in this one-time pipeline. A recurring production pipeline would need an immutable run manifest for independent repair-event attestation.
- This is trailing GAAP valuation. It must not be labeled or combined with the forward-EPS history that only begins with auditable snapshots in July 2026.
