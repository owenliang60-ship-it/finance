# Crypto daily PMARP breadth

Implementation branch: `codex/crypto-pmarp-breadth`. Deployment requires approval.
Aligned with north-star technical analysis / market-environment observation.
This report describes participation, not a trading signal or position rule.

## Definition

For the last closed UTC day, take all historical Crypto USDT perpetuals
(`underlyingType=COIN`, `quoteAsset=USDT`, `contractType=PERPETUAL`) whose
lifecycle includes that day's close. Include BTC; do not apply the momentum
Top100, volume, price or direction filters. A contract settling intraday is
excluded on that day. A newly listed partial first day follows Binance's
native daily bar convention.

Reuse Quant's `calculate_pmarp` with EMA20 and 150 prior daily PMAR values.
The first 150 actual listing bars are warmup, outside the valid denominator.
Fetch 365 seed days before the first breadth date. This fixes the EMA seed
for each dated report; it uses the same kernel/parameters as the scanner,
but does not copy its Top50 universe or its shorter downloaded price window.

Compute separately:

- strong breadth = count(PMARP >=98) / valid PMARP count *100;
- weak breadth = count(PMARP <=2) / valid PMARP count *100;
- each percentile = count(previous 365 breadth values <= today's value) /365 *100.

The comparison excludes today, uses calendar days (crypto trades every day),
and uses unrounded breadth values. Ties count fully: an all-zero historical
series gives P100 for zero today. Both raw breadth and percentile are shown.

A gap, duplicate, malformed/nonpositive close, invalid PMARP, absent lifecycle,
or zero valid denominator stops numeric publication. New-coin warmup is
explicitly counted; failures never silently reduce the denominator. A pending
contract is excluded only after an explicit exchange inactive-status error
(or an empty successful response) and no archived trading anywhere in the
seed/report interval; `confirmed_unopened` records these exceptions.

## Historical evidence

`config/crypto_pmarp_breadth_sources.json` contains source-linked historical
supplements for AERGO, BDXN and SXP. Supplements fill absent records only;
current exchangeInfo and saved catalogs take precedence. These are
retrospective effective-date reconstructions, not original PIT snapshots.
SXP's launch is known to date precision only, and supported runs must start
the EMA seed window after that date's upper bound.

An official 2021 futures retirement excludes BTCST only when it predates the
entire requested membership window. It does not exempt any in-window unknown
contract from the official archive audit. Archived directory presence alone
is not proof of active trading: archives may contain padding after retirement.

Three immutable derived JSON files contain just the original Binance row
arrays, with SHA256 pinned by the manifest. `origin` identifies the original
research object file; **do not copy that object as the deployment file**.
Use generated files from:

`reports/crypto-pmarp-breadth-2026-09-28/breadth_cache/retired_prices/*.json`

AERGO prelisting rows and BDXN post-retirement rows are retained for provenance
but clipped before indicator calculation. Other contracts use the existing
REST adapter, bounded by the last closed day. A newly retired contract whose
REST history disappears requires a verified price artifact before numeric
publication resumes; it is not silently omitted.

## Execution and artifacts

`crypto_daily_rankings.run` sends existing 10/14-day trend reports first,
then invokes breadth with their identical `as_of` day. The existing Quant
wrapper and 08:06 entry are reused; no new scheduler is needed. All traffic
is serial and uses existing API throttling. Full history is task-cached per
report day; first run requests up to one 731-bar REST response per relevant
contract, plus the historical archive audit (allow roughly 10–20 minutes,
subject to network/retries). No market.db/company.db writes are involved.

Preview without sending:

```bash
python3 -m scripts.crypto_pmarp_breadth \
  --scanner-dir /root/workspace/Quant/scanners \
  --output-dir /tmp/breadth-preview \
  --retired-dir /path/to/verified/retired_prices \
  --as-of 2026-09-27 --dry-run
```

Read-only historical catalogs must be available in `output-dir/trend_cache`.
The production daily output already has this directory. A standalone scratch
preview should copy existing catalogs there before running.

Artifacts: `crypto_pmarp_breadth_DATE.json` and `.md`, including 366 daily
counts, current per-symbol PMARP, metadata evidence, seed dates, request
counts, comparison dates and two percentiles. On failure they instead carry
`status=unavailable` and the concrete reason; the message contains no numbers.
Artifacts are written before sending. Send failures propagate. A breadth
failure happens after existing rankings have been published and makes the
Quant scanner return failure.

## Deployment / rollback

After approval, deploy the reviewed Finance revision and put the three
hash-verified derived files under:

`/root/workspace/Quant/results/daily_rankings/breadth_cache/retired_prices/`

Run a cloud `--dry-run` against the deployed code and verify manifest hashes
before permitting the normal entry to execute. Publishing a separate live
preview is not required. The existing wrapper/cron stays in place.

Rollback the Finance revision (in particular the breadth call in
`crypto_daily_rankings.run`). Task artifacts can remain for inspection;
existing ranking outputs and shared Quant caches are not modified.
