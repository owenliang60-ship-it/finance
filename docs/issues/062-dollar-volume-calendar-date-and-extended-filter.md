# 062 — Dollar Volume used collection date and filtered the total ranking

Date: 2026-09-06
Status: resolved and deployed in merge `6efd88d` + test hardening `94e3fcc`; 2026-09-04 data repaired from a verified backup.

The Friday 2026-09-04 report displayed the row stored under `daily_rankings.date=2026-09-04`, whose collection log shows only574 screened stocks/95 stored and begins DELL/HPE/ORCL. The Saturday 2026-09-05 collection scanned1011/stored200 and begins MU/NVDA/SNDK/TSLA/AAPL;13 of its top15 overlap an independent `market.db` 2026-09-04 close×volume top15 after excluding benchmark ETFs. Root cause: `collect_daily()` defaulted to Singapore calendar date rather than the market signal session passed by the already-built morning payload.

Separately, `_normalize_dv_items()` applied `_layer_for_symbol()` and discarded symbols outside the current Extended base. Even a valid full-marketTop50 therefore rendered as `Top48`; this contradicts “真正的交易额总排名”. Premium membership should style rows, never filter or rerank the Dollar Volume total list.

Repair: pass `market_signals.as_of` into `collect_daily(date=...)`; preserve original ranks and all raw Top50 rows during normalization; keep metadata/concept enrichment and authoritative Premium highlighting. Back up `dollar_volume.db` before rebuilding the 2026-09-04 row from current post-close screener data.

Verification before deployment: six focused RED tests failed against the old behavior, then passed after the fix. Related172 passed/1 skipped; full suite2939 passed/4 skipped. Main-thread diff review found no remaining Critical/Important issue. HTML, visual and text paths all retain low-cap/non-Extended rows and their source ranks.

## Production repair evidence

- Code: main/origin/cloud `94e3fcc`; cloud related173 passed/1 skipped.
- Backup: `/root/workspace/Finance/data/dollar_volume.db.before-session-date-fix-20260906T130831Z`, SQLite backup API + quick_check.
- Exact migration under the `finance_market_report` flock: stale9/4 row count95 and mislabeled9/5 row count200 → verified Friday rows9/4 count200,9/5 count0; collection log date moved with them. Top5 is MU/NVDA/SNDK/TSLA/AAPL. Source top15 overlaps independent market.db9/4 close×volume in13 names after benchmark ETFs are excluded.
- Corrected report preserves all raw Top50, including non-Extended names. Premium styling appears on14 DV rows; no filtering or reranking. Artifact: `reports/rendered/premium-friday-20260904-corrected/`.
