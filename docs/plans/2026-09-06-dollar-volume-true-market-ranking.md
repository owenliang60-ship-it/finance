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
- [x] Cloud SQLite backup、部署和交易日迁移后，9/4为完整200行、Top50以MU/NVDA/SNDK/TSLA/AAPL开头；无9/5重复session。修正版日报不发Telegram。

Rollout: merge `6efd88d`, final main/origin/cloud `94e3fcc`; backup `/root/workspace/Finance/data/dollar_volume.db.before-session-date-fix-20260906T130831Z`. Corrected HTML/PDF/PNG locally at `reports/rendered/premium-friday-20260904-corrected/`; HTML SHA256 `3062a5c6bdb8307f8812aee7adf9ac8348f1c1609587a4fc9fdd4eff03e16f9c`, PDF `ea7fa0dfa613d7d5680518295d0caa3120a6d3a6aef8ddd67f6dc0171da75321`.
