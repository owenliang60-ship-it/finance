# Issue 044: FMP fund-disclosure `cik` 不能假定为 SEC issuer CIK

**Status**: RESOLVED
**Date**: 2026-07-14
**Severity**: MEDIUM — 错误假设会让历史 disclosure backfill 在 corporate alias 上 fail-closed
**Related**: `config/soxx_symbol_aliases.json` · `normalize_fund_disclosure_snapshot`

## 触发

SOXX historical PE 的真实只读 dry-run 在 2021Q3 `CREE` 行报 `CIK mismatch for configured alias CREE`。配置使用了 SEC Wolfspeed/Cree issuer CIK `0000895419`，但 FMP fund-disclosure endpoint 在 2021Q3 CREE 与后续 WOLF 行上连续返回 `0001100663`。

## 根因

把第三方 endpoint 的同名 `cik` 字段直接当成 SEC issuer CIK。对于这个数据集，字段值与 SEC EDGAR issuer CIK 不一致；但它在 CREE→WOLF 前后保持连续，因此仍可作为该 endpoint 内部的 alias evidence。

## 修复

- alias 配置改用 FMP disclosure 实际连续值 `0001100663`；reason 明确这是 vendor CIK。
- 保留 exact symbol + exact vendor identifier 的双门，继续禁止 fuzzy alias。
- 测试明确写出 namespace caveat，防止以后“纠正”为 SEC CIK 后再次让 live dry-run失败。

## 教训

第三方 API 的同名标识符不能凭字段名推断权威 namespace。配置 alias 前必须用目标 endpoint 的历史前后行验证连续性；若要声称是 SEC CIK，另行与 EDGAR primary source 对拍。
