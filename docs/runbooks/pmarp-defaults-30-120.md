# PMARP default migration: EMA30 / lookback120

Implementation branch: `feat/pmarp-defaults-30-120`, based on main `df1778a7`.
User requested all scanner/backtest defaults to match the2026-10-21 study.
**LIVE: merged, pushed and deployed to Finance+Quant on2026-10-21 at04:37 UTC.**
Runtime change commit: `dea9952b`. Both live defaults are30/120.
Deployment evidence: `docs/audit/2026-10-21-pmarp-default-deployment.md`.

## Effective configuration

`src/indicators/pmarp.py` owns `DEFAULT_PMARP_EMA_PERIOD=30` and
`DEFAULT_PMARP_LOOKBACK=120`. Scan engine, morning report, theme scan and
factor studies inherit those defaults. Timing, pipeline, research configs,
CLI defaults and Forge import the shared constants; Forge's shipped lookback
parameter/manifest are120. Explicit CLI/research overrides remain possible
for reproducibility. Other trend EMA, BBWP, RVOL and signal thresholds unchanged.

Pipeline resolves implicit PMARP parameters before hashing the spec and writes
`resolved_spec.json`, so20/150 and30/120 cannot share the old implicit spec
identity. Scores are recomputed; no historical backtest results are rewritten.
The `_legacy_reference` tree and separate frozen research branch are historical.

Crypto breadth passes30/120 explicitly to the existing Quant kernel, checks
its CONFIG matches, stores the parameters, and renders the stored values.
It uses a parameter-specific history request label. `rolling_1d` and legacy
`ema20_pmarp150` snapshot imports contain OHLCV only and remain safe to reuse;
no computed PMARP cache is reused. Do not delete historical prices/results.
Quant matures from120 prior bars, whereas Finance's existing minimum-input
check requires30+120 total bars; those existing API semantics are preserved.

## Coordinated rollout procedure

1. Deploy the reviewed Finance commit after merge/push approval using the
   existing repository deployment workflow. Avoid the running Quant job;
   existing scanners are serialized by the Quant resource lock. Do not run
   publisher jobs merely to validate configuration.
2. The separate non-Git Quant source is
   `/root/workspace/Quant/scanners/binance_pmarp_scanner.py`.
   The migration changes only CONFIG ema_period/lookback and function defaults.
   On the cloud host, first run:
   ```bash
   cd /root/workspace/Finance
   python3 scripts/quant/migrate_pmarp_30_120.py /root/workspace/Quant/scanners/binance_pmarp_scanner.py
   ```
   It defaults to dry-run and prints only hashes/parameters, never source or credentials.
   Original hash must be
   `2759f2f23bedf4ce72e2e797adb0d3f03e6055eb8ebf971fd2eea7555859f8f4`.
   Proposed new hash is
   `371785c1d0298bb57878add9467b67a1710cde6bbb73da27ef60fc802b3a75a8`.
   After approval, repeat with `--apply`. It preserves CRLF and file mode,
   makes an exclusive0600 `.py.pre-pmarp-30-120.bak` backup, and atomically
   replaces the source. Unexpected hash, duplicate parameter lines or an
   existing backup stop the mutation. Do not force through a mismatch.
3. Verify Finance and Quant effective parameters both30/120 before next daily
   job. Their configuration guard deliberately rejects a partial rollout.
   No cron changes or Telegram sends are required by this migration.
4. The original daily entry is Quant `run_daily_scan.sh`→`daily_scan_all.py`;
   it calls the Quant PMARP module, then Finance crypto reports. Finance US
   reporting uses `scripts/run_market_report_pipeline.sh`.

## Rollback

Roll back the Finance commit and restore the protected Quant source backup
in the same coordinated window. Verify the original hash and both20/150
parameters. Keep dated results, deployment evidence and receipts intact.
Existing same-day delivery receipts can reject changed message text; do not
clear them or publish a correction without explicit message authorization.

## Verification and scope

- Main regression covers parameter computation,150-bar minimum input, timing,
  new-listing120-bar Crypto warmup, actual backtest/Forge parameter validator,
  pipeline provenance, morning/scanner consumers and migration dry-run/backup.
- Cloud migration was dry-run and then applied after explicit user approval.
 31 cloud checks passed with network blocked and synthetic data only;
 no API/Telegram calls were made.
- Independent review found a Forge parameter-key regression during work; it
  was reproduced and fixed by keeping the manifest's existing key set.
- Legacy `terminal/freshness.py` Timing refresher has an unrelated pre-existing
  DataFrame/Series interface issue; this task does not claim to repair it.
