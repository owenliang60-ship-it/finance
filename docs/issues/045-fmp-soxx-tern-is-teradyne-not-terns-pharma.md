# Issue 045: FMP SOXX disclosure 把 Teradyne (`TER`) 标成 `TERN`

**Status**: RESOLVED — authoritative security-identity correction added
**Date**: 2026-07-14
**Severity**: HIGH — raw-first 会把另一家公司 Terns Pharmaceuticals 的财报/市值接入 SOXX
**Related**: `config/soxx_symbol_aliases.json` · `fmp_fund_disclosure_holdings` · historical basket valuation

## 触发

真实只读 dry-run 发现 `TERN` 有 966 个连续 market-cap quarantine 日。原始 SOXX disclosure 行同时给出：

- name/title: `Teradyne Inc`
- CUSIP: `880770102`
- ISIN: `US8807701029`
- vendor symbol: `TERN`

正确上市 ticker 是 `TER`；FMP 的 `TERN` price/HMC/income 数据属于 Terns Pharmaceuticals，是另一家公司。

## 风险

普通 corporate rename 可以遵循 raw-first、缺数才 fallback；这个场景不能。若 `TERN` 原始 key 恰好数据完整，raw-first 会优先选择错误公司的完整数据，覆盖率仍可能超过 90%，从而形成最危险的“绿灯污染”。

## 修复

- alias 增加两种显式模式：`fallback` 与 `authoritative`。
- `TERN → TER` 使用 `authoritative`，且必须同时匹配 exact raw symbol、vendor CIK、CUSIP 与 ISIN；不做名称模糊匹配。
- source 表保留 raw `TERN` 与 correction evidence；evaluation universe 只请求/使用 `TER`，不请求错误公司的 `TERN` 数据。
- `CREE → WOLF` 继续使用 `fallback`，保持 raw-first 语义。
- verifier 独立复算时使用相同的身份模式，并核验输出成员的 `raw_symbol`、`resolved_symbol`、`alias_mode`、`alias_reason`。

## 教训

“ticker 存在且 endpoint 有完整数据”不等于证券身份正确。指数/ETF disclosure 接入必须把 symbol 与 CUSIP/ISIN 一起看；遇到 vendor symbol 指向另一家真实公司时，必须使用 authoritative correction，不能用普通缺数 fallback。
