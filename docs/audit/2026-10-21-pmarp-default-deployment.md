# PMARP30/120 live deployment —2026-10-21

User explicitly authorized merge, push and deployment to Finance and Quant,
then verification that all active scanner/backtest defaults are EMA30/120.

## Deployment

- Local main and origin/main started at `df1778a7`; migration branch `dea9952b`
  fast-forward merged to main and pushed successfully.
- Cloud Finance started clean at `df1778a7`, on main. No active scan/report jobs
  were found. Shared Quant daily/weekly lock `quant_daily_scan.lock` was held
  throughout Finance update, Quant migration and runtime verification.
- Cloud Finance fast-forwarded to runtime commit `dea9952b`.
- Quant's non-Git source changed exactly three lines, preserving CRLF/file mode:
  CONFIG ema_period30/lookback120 and calculate_pmarp default30/120.
- Before SHA256: `2759f2f23bedf4ce72e2e797adb0d3f03e6055eb8ebf971fd2eea7555859f8f4`.
- After SHA256: `371785c1d0298bb57878add9467b67a1710cde6bbb73da27ef60fc802b3a75a8`.
- Protected0600 backup:
  `/root/workspace/Quant/scanners/binance_pmarp_scanner.py.pre-pmarp-30-120.bak`.
- Applied2026-10-21 04:37:09–04:37:15 UTC (12:37 Singapore).

## Verification

**31 cloud checks passed**, using the deployed code and cloud Python3.10.12.
No publisher jobs, market data fetches or production DB access were invoked.
Network connections were blocked during synthetic configuration checks.

- Shared calculate/analyze defaults30/120; default scores equal explicit30/120
  and differ from20/150 on the same420 synthetic prices.
- US scan entry uses the shared engine; synthetic engine execution matches
  explicit30/120. Morning-report and engine callsites inherit the default.
- Dual-engine timing and PMARP/PMARP+BBWP/RVOL research configs resolve30/120.
- Five research CLI parsers resolve30/120 without running their studies.
- Pipeline spec stores effective30/120; synthetic computation matches; minimum
  input150 rows. Both US/Crypto factor adapters use the new minimum.
- Forge effective champion config30/120; JSON keys match surface, surface's
  lookback default is120, unrelated trend EMA unchanged.
- Crypto breadth uses30/120 and its existing external Quant calculator.
- Quant CONFIG and exact deployed function definition both30/120. Its function
  was evaluated in an isolated NumPy/Pandas namespace, without importing
  credentials or executing the scanner; scores match Finance on synthetic data.
- Quant resulting source hash matches the reviewed proposal.

Local verification ran27 Finance checks before deployment. Existing uncommitted
user changes in the primary local checkout were preserved byte-for-byte.
Historical frozen research branches/results, legacy references, and explicit
historical overrides remain intentional exceptions, not active default paths.
Cron schedules, API credentials, caches and notification receipts were not changed.

The31-check JSON and deployment transaction receipt are saved with the chat
artifacts under `outputs/pmarp-default-deployment/`. The documentation-only
follow-up commit does not change the verified runtime code.
