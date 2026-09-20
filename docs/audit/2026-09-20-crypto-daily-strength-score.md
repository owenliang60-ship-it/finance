# Daily crypto strength score

User-authorized update to the existing 7/14/30-day cron rankings: absolute mathematical return 50%, log-price ER 15%, R² 15%, current drawdown 10%, matching-window average daily USDT turnover 10%. Percentiles use every price-valid member before the existing positive-return/positive-slope publication gate. Missing turnover fails closed; zero observed turnover is not missing.

The production entry explicitly chooses v2 (schema 3). The default v1 API remains available for reproducibility. The separately deployed weekly scorer and its signed-return weights retain their exact shared scoring helper and pool APIs.

Validation before rollout:
- Focused daily/weekly/Fisher/RVOL suite: 241 passed (6.00s).
- Current production 2026-09-19 metrics/pools rescored with independently acquired daily turnover: 253 valid coin-window rows independently verified against scalar percentile formulas; report JSON and all three messages rendered successfully.
- Preview: Finance/reports/crypto-fisher-2026-09-20/daily_preview/preview.md, verification.json.
- Main-thread review: no outstanding findings in the bounded shared score and rendering change. Source acquisition and historical financial research remain in a separate worktree.

No manual notification is part of this deployment. The cron schedule and scanner entrypoints remain unchanged. Natural next-run delivery is verified only after it occurs.

## Production acceptance
Runtime commit `7b1a9fd` was merged/pushed and deployed under `/tmp/quant-cron-locks/quant_daily_scan.lock`. Backup: `/root/workspace/Quant/backups/crypto-daily-strength-20260920/`. Cloud staging **241 passed/13.04s**, production **241 passed/12.41s**. Production rescoring/rendering matched the independently checked 253-row preview; the shared weekly scoring and pool helper source text is exactly unchanged. Crontab and all four entrypoint hashes match before/after. No manual Telegram messages were sent. Evidence: `reports/crypto-fisher-2026-09-20/daily_preview/deployment/`.
