# Crypto PMARP breadth acceptance — 2026-09-28

Implementation complete in `codex/crypto-pmarp-breadth`; not merged or deployed.
The production scheduler and Telegram sending have not been changed.

## Accepted definition and preview

Historical daily **single-coin** Crypto USDT perpetual membership, including
BTC and retired contracts; excludes TradFi and `INDEX` contracts (DEFI/BTCDOM/
ALL). The latter boundary was surfaced separately to the user and remains
an explicit implementation assumption pending a response. No turnover filter.
Membership is evaluated at daily close. New coins need 150 prior daily bars.

Last closed UTC day: **2026-09-27**, closing 2026-09-28 08:00 Asia/Shanghai.
525 eligible contracts; 506 valid, 19 in warmup.

| Series | Count / valid | Breadth | Prior 365-day percentile |
|---|---:|---:|---:|
| PMARP >=98 | 70 / 506 | 13.83399209486166% | 94.52054794520548 |
| PMARP <=2 | 1 / 506 | 0.1976284584980237% | 7.123287671232877 |

Percentile comparison: 2025-09-27 through 2026-09-26, excludes today;
right-inclusive ties. The counts at or below today's breadth are 345/365
and 26/365 respectively. Reporting text warns that ties can give P100 for
an all-zero series.

## Verification

- `python -m pytest tests/test_crypto* -q`: **300 passed in 8.41s**.
- Full suite (before the final pending-contract/date-input regression tests):
  **4317 passed, 4 skipped, 12 failed**, 319.75s. All failures are outside
  Crypto, in `test_breadth_buy_quality` (7) and `test_morning_report` (5).
  The first group reads ignored research fixtures and live DB state absent
  from a fresh worktree; rerun in the main checkout: **16 passed**.
  Main-checkout morning tests: **171 passed, 1 different frozen-data parity
  failure**. These results are environment/data-dependent; the full suite
  is explicitly **not green**, and unrelated tests/code were not changed.
- Python 3.10 AST syntax and `git diff --check`: PASS.
- Independent review: two review rounds plus targeted pending-state review;
  no unresolved findings. The two agents used were historical-evidence /
  independent-verifier and code-review agents.
- Independent stdlib scalar EMA/PMARP verifier: **PASS, 0 discrepancies**,
  610 historical contracts, 328,045 bars, all 366 daily counts and both
  365-day percentiles. See `independent_verification.json`, which includes
  report/input hashes. It does not import the producer/scanner calculation.
- Real source preview executed only in cloud scratch
  `/tmp/crypto-pmarp-breadth-preview-20260928`, with `--dry-run`.

## Historical exceptions and evidence

The manifest `config/crypto_pmarp_breadth_sources.json` gives official launch /
retirement sources for AERGO, BDXN and SXP. SXP launch is date-precision only
and predates the whole EMA seed interval. BTCST's official 2021 futures
retirement resolves its misleading padded archive directory before this
window. Supplements fill missing records, never override existing catalogs.

Three generated retired-price list JSONs are pinned by SHA256. They match
all `rows` from the original checksum-verified research objects exactly;
prelisting and post-retirement padding is clipped by lifecycle. The deployment
inputs are `breadth_cache/retired_prices/*.json`, **not** the original research
object files named by the manifest's `origin` fields.

GAIB is `PENDING_TRADING` and returned explicit exchange -1122; its complete
archive evidence contains no activity from seed start to cutoff. It is
recorded in `confirmed_unopened`. The independent verifier checks pending
classification and archived XML evidence; the raw REST error response was
not persisted, so independent verification does not claim to re-prove that
HTTP response. Unit and independent boundary tests separately verified that
ordinary API failure, archive failure, or archive activity blocks publication.

## Artifacts and deployment

- `crypto_pmarp_breadth_2026-09-27.md`: actual message preview.
- `crypto_pmarp_breadth_2026-09-27.json`: report plus 366-day history and metadata.
- `independent_verify.py` / `independent_verification.json`: separate computation.
- `crypto-tests.log`, `fullsuite.log`, main-checkout comparison logs.
- `breadth_cache/`: original input evidence (large; not all committed).

Runbook: `docs/runbooks/crypto-pmarp-breadth.md`. After merge/deployment approval,
copy the three verified retired-price files to the production daily report's
`breadth_cache/retired_prices` directory. The existing 08:06 Quant entry will
send the 10/14-day rankings first and breadth afterward. On failure breadth
sends an explicit unavailable notice rather than biased numbers. No new cron
or scheduler is required.
