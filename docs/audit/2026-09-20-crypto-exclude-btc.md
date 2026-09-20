# Permanent BTC exclusion — deployed 2026-09-20

Boss explicitly authorized excluding BTC from the daily crypto momentum cron and momentum backtests. Runtime `6f5d0af7` is merged/pushed/deployed. `EXCLUDED_TRADING_SYMBOLS={'BTCUSDT'}` applies before candidate pricing and daily percentile scoring; published report metadata/text names the exclusion. BTC remains in raw market turnover observations and as a Fisher/benchmark data source. This does not replace BTC's historical Top100 slot with rank101. Only the existing daily 10/14 output changes; weekly report and other scanner runtimes remain unchanged. Existing v2 weights50/15/15/10/10 remain; proposed30% turnover formula was not deployed.

Validation:
- Added three daily behavior tests. First two were observed RED before implementation, all35 trend-ranking tests now PASS. Cloud staging254PASS and production254PASS; compile and main-thread `/cr` completed with no remaining findings.
- Full local suite:3826PASS,12knownbaselineFAIL,4SKIP in274.67s. Failures are the same7breadth and5morning-report failures recorded in preceding rollout; not introduced here.
- Frozen real-data report independently checked from captured raw inputs:14dailyTop100 observations,2pools,163validcoin-windows. NoBTC candidate; independent percentile/metric/order reconstruction PASS. Production preview hash equals validated stage preview. No Telegram send or overwrite of previously sent production artifacts.
- Research branch `codex/crypto-momentum-v2`: code2b9fc6ce, policy docfc337e6f. Standard frozen input loading removes BTC and recomputesv1/v2 scores; HoldingPolicy plus old target_weights blockBTC. Targeted325PASS plus new legacy selector test (ledger10PASS). Independent raw-factor percentile comparison371628validrows across7/10/14/30days PASS, zeroBTCsignals; original cache/regime hashes unchanged. Existing historical profit figures have not been recomputed/relabelled.

Deployment used Quant shared daily lock. Crontab,5daily/weeklyQuant entrypoints, weekly runtime and market adapter SHA256 all unchanged; no service restart. Next natural08:06run remains pending, no monitoring automation created.

Evidence: reports/crypto_daily_rankings/2026-09-20-ex-btc/ and reports/crypto-fisher-2026-09-20/btc-exclusion/. Cloud backup /root/workspace/Quant/backups/crypto-exclude-btc-20260920/ includes oldHEAD, runtime tar, cron/hash snapshots, validated preview, production test log and deployed.json. Rollback via reviewed revert of6f5d0af7; no schedule restoration needed.
