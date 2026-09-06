# 062 — Dollar Volume used collection date and filtered the total ranking

Date: 2026-09-06
Status: code fixed and full tests passed; deployment/data repair pending.

The Friday 2026-09-04 report displayed the row stored under `daily_rankings.date=2026-09-04`, whose collection log shows only574 screened stocks/95 stored and begins DELL/HPE/ORCL. The Saturday 2026-09-05 collection scanned1011/stored200 and begins MU/NVDA/SNDK/TSLA/AAPL;13 of its top15 overlap an independent `market.db` 2026-09-04 close×volume top15 after excluding benchmark ETFs. Root cause: `collect_daily()` defaulted to Singapore calendar date rather than the market signal session passed by the already-built morning payload.

Separately, `_normalize_dv_items()` applied `_layer_for_symbol()` and discarded symbols outside the current Extended base. Even a valid full-marketTop50 therefore rendered as `Top48`; this contradicts “真正的交易额总排名”. Premium membership should style rows, never filter or rerank the Dollar Volume total list.

Repair: pass `market_signals.as_of` into `collect_daily(date=...)`; preserve original ranks and all raw Top50 rows during normalization; keep metadata/concept enrichment and authoritative Premium highlighting. Back up `dollar_volume.db` before rebuilding the 2026-09-04 row from current post-close screener data.

Verification before deployment: six focused RED tests failed against the old behavior, then passed after the fix. Related172 passed/1 skipped; full suite2939 passed/4 skipped. Main-thread diff review found no remaining Critical/Important issue. HTML, visual and text paths all retain low-cap/non-Extended rows and their source ranks.
