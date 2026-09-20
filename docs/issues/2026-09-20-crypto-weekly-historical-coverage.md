# Weekly crypto horizon exposes removed-contract history

2026-09-20, weekly report development, branch codex/crypto-weekly-report.

The daily trend catalog audits 30 calendar days. Moving its window to 30 weeks requires a 210-day audit, not merely multiplying scoring lookback. AERGOUSDT and BDXNUSDT are absent from current exchangeInfo but traded during that interval. Current-only rankings would silently promote other contracts.

Previously researched official lifecycle evidence, rechecked against issuer pages in this task:
- AERGO launch 2025-04-16 11:00 UTC: https://www.binance.com/en/support/announcement/detail/82f730b7ef444a38b323ab7a2e56b757
- AERGO delivery 2026-07-24 06:30 UTC: https://www.binance.com/en/support/announcement/detail/f0e3fc835b9545759e4279c64b200389
- BDXN delivery 2026-03-17 09:00 UTC: https://www.binance.com/en/support/announcement/detail/f6ebc17e4dbd451b873d9ef25c801237

The frozen crypto-alpha research data contains AERGO daily bars through 2026-07-23 only. Live queries in this task: AERGO REST klines returns -1121; July24 official daily 1h/1m klines, aggTrades and trades archive paths all return404. Missing terminal-day volume is UNKNOWN, never zero. Partial delivery-week rows still compete in historical turnover; wholly post-delivery zero-volume padded candles must be filtered by official lifecycle.

Independent preliminary bounds for cutoff2026-09-14 (using a separate stdlib implementation): treating AERGO's uncertain week as either outside or inside Top100 produced identical current 7/14/30-week pools. This is NOT source completeness and NOT authority to loosen the production gate. User choice on allowing explicit invariant-pool proof was requested; until answered, preserve strict failure. Future weekly cutoffs need their own proof, never reuse this result as a blanket exception.

Validation setup also requires the full backtest package: importing the existing window helper loads backtest/__init__.py, which imports config, etc. A minimal archive containing only metrics.py failed with ModuleNotFoundError; adding the existing tracked backtest package fixed the environment setup rather than rewriting imports.

Strict cloud smoke additionally flags BTCSTUSDT and SXPUSDT archive metadata; directory existence must not be treated as a live contract or sufficient lifecycle proof. No whitelist or guessed delisting timestamp was added.

Both DSH runs hit the40-step limit despite writing tested code. Codex inspected actual files, completed docs, fixed catalog snapshot chronological merging and wrapper execute permission, and ran acceptance directly; harness status was not treated as success. Fullsuite12failureIDs exactly match the previous validated baseline. An interrupt during a slow curl callback produced a warning; the affected test_pipeline_scratchpad::test_collect_data_without_scratchpad was rerun separately:1passed in4.48s.
