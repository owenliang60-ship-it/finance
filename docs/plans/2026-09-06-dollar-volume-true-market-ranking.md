# Dollar Volume True Market Ranking Fix

**Confidence: 99%**
**Goal:** Bind Dollar Volume to the US market session and display the full-market Top50 without Extended filtering.
**North Star:** Data layer session identity + morning report analysis layer; Premium remains presentation-only metadata.

```mermaid
flowchart LR
  Market[market_signals.as_of] --> Collect[collect_daily(date=as_of)]
  Collect --> DB[dollar_volume.db Top200]
  DB --> Top50[raw total-market Top50]
  Premium[weekly Premium symbols] --> Style[red/bold only]
  Top50 --> Style --> Report[text/HTML/PNG/PDF]
```

Alternatives: renaming existing dates would mislabel unknown historical rows; using `market.db close×volume` would change the established FMP source and include ETFs. The scoped fix retains FMP and repairs only the known Friday row via a fresh force collection.

Risks: a stale market signal date must not trigger a fresh fetch—`is_collected(as_of)` skips it; metadata failure must not drop rows; original rank order/count must survive rendering; rebuild requires backup because storage uses DELETE+INSERT for the target date.

Acceptance:

- [x] Morning orchestration passes market signal `as_of` to Dollar Volume collection.
- [x] Normalization returns every raw Top50 row in original rank order, including non-Extended names.
- [x] Premium only adds style metadata; normal rows and ranks remain unchanged.
- [x] Six focused RED/GREEN tests cover session date, count/order, no-filter and text/HTML/visual output; existing Premium tests remain green.
- [x] Related172 passed/1 skipped; full2939 passed/4 skipped; main-thread review无Critical/Important。
- [ ] Cloud backup, deploy and forced9/4 rebuild produce true FridayTop50; regenerate report without Telegram.
