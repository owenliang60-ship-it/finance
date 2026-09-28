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
current exchangeInfo and genuine saved exchange rows take precedence. Derived
supplements are not persisted as observed exchange rows. Older derived rows
are recognized by their source fields and discarded on load, so a corrected
manifest takes effect on the next run. Audit evidence lists effective supplements. These are
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
The default reads the following versioned, bundled files directly; there is
no separate manual cloud copy. Startup checks every file and hash before any
API call, and reports all preflight errors together. `--retired-dir` is an
explicit override and is checked equally strictly:

`reports/crypto-pmarp-breadth-2026-09-28/breadth_cache/retired_prices/*.json`

AERGO prelisting rows and BDXN post-retirement rows are retained for provenance
but clipped before indicator calculation. Other contracts use the existing
REST adapter, bounded by the last closed day, through a rolling per-contract
cache. A retired contract with a known lifecycle and complete cached prices
needs no REST call. If necessary prices or lifecycle evidence are genuinely
missing, publication remains unavailable until repaired; it is not silently omitted.

## Execution and artifacts

`crypto_daily_rankings.run` runs trends and breadth independently for one fixed
closed UTC day. An error (including failure to send its error notice) in one
part does not skip the other. Both errors are summarized in
`crypto_daily_status_DATE.json`, and the job exits unsuccessfully if either
part failed. A trend failure produces its own unavailable notice.

Each successful 10d/14d/breadth message has a separate acknowledged-send
receipt in `delivery_receipts/`. A retry skips only the same key and same
text hash; changed content fails explicitly so a correction can be approved
instead of silently pretending it was delivered. An unavailable breadth
notice and a successful recovery have separate keys. Dry-run never writes
receipts. The existing Quant resource lock serializes executions; do not run
concurrent manual publishers outside that lock. These receipts cannot promise
exactly-once Telegram delivery: the remote service has no idempotency key, and
a lost acknowledgment or crash between sending and writing the receipt may
still duplicate a message. Review delivery state before manually resolving
such ambiguous failures. Explicit corrections require preserving the old
receipt as evidence and authorizing a replacement send.

The existing Quant wrapper/08:06 entry stays in place. All traffic is serial
and uses existing throttling. Cold initialization fetches up to 731 bars per
contract. Thereafter `breadth_cache/rolling_1d/SYMBOL.json` holds at most 731
validated rows with a checksum; only missing spans are requested (normally
one new bar per active contract). The old dated snapshots are imported once,
read-only, and are not deleted. A failed update leaves the prior cache intact;
a past replay cannot replace a newer cache. Symbols retired before the first
comparison day's close are not fetched at all. Per-symbol failures are
collected across the pool before numeric publication is rejected.

Incremental fetching reduces payload and repeated disk storage, not the
per-symbol request count: approximately 500–600 requests and the one-second
serial throttle can still take 10+ minutes. Do not call this a seconds-long
daily job. The previous full daily snapshot measured about 52 MB; rolling
prices occupy roughly that order of space in total instead of adding it each
day. Small dated reports, catalogs and archive-audit evidence still accumulate;
no automatic deletion of old evidence is introduced. No market.db/company.db
writes are involved.

Preview without sending:

```bash
python3 -m scripts.crypto_pmarp_breadth \
  --scanner-dir /root/workspace/Quant/scanners \
  --output-dir /tmp/breadth-preview \
  --as-of 2026-09-27 --dry-run
```

Read-only historical catalogs must be available in `output-dir/trend_cache`.
The production daily output already has this directory. A standalone scratch
preview should copy existing catalogs there before running.

Artifacts: `crypto_pmarp_breadth_DATE.json` and `.md`, including 366 daily
counts, current per-symbol PMARP, metadata evidence, seed dates, request
counts, comparison dates and two percentiles. On failure they instead carry
`status=unavailable` and the concrete reason; the message contains no numbers.
Artifacts are written before sending. Send failures propagate into the
independent-task summary; successfully acknowledged messages are not repeated
on an ordinary retry.

## Deployment / rollback

After approval, deploy the reviewed Finance revision, including the three
versioned bundled price files. No manual copy to an output cache is required.
Run cloud `--dry-run` first; bundled asset/hash checks happen before collection.
The existing wrapper/cron stays in place. First startup can import the already
verified dated snapshots; later runs use rolling caches.

Rollback the Finance revision (in particular the breadth call in
`crypto_daily_rankings.run`). Task artifacts can remain for inspection;
existing ranking outputs and shared Quant caches are not modified.
