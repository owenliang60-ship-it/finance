# Issue 053: 发行人级 market cap 不能用于选择普通股主类

**Status**: 已修复（`codex/extended-stop-d-hardening`，待合并/部署）
**Date**: 2026-08-21
**Severity**: HIGH — active Extended 分母混入优先股/债务证券，并把普通股降为 secondary
**Related**: `src/data/security_master.py` · `src/data/market_store.py`

## 触发与根因

Stop D 前的独立 denominator review 在 943 个 active eligible 中发现：

- `FITB-PM` 被选为 Fifth Third 主类，而普通股 `FITB` 被降为 secondary；
- `MER-PK`（Bank of America 6.45% Notes）和 `SATA`（永久优先股）以 singleton `ok` 入池。

FMP 把发行人级 market cap 复制到同一发行人的 preferred/depositary listing。`FITB-PM`
因此显示 51.4B，高于 `FITB` 的 49.4B；旧算法按 market cap 优先，正好选反。单票分类又只排除 ETF/Fund，明确的债务/优先股 title 没有 gate。

## 修复

- 对明确的 `-P*` preferred ticker、PFD/preferred、notes/debentures、固定票息 title，新增 `non_common_instrument` fail-closed reason；ADR Depositary Shares 与 MLP Common Units 不做泛化误杀。
- share-class primary 改为 security-level `averageVolume/volAvg` 优先，market cap 仅在成交量缺失/打平时兜底。
- `non_common_instrument` 加入 SM 白名单与 historical as-of identity blocked 集。
- 真实 FITB/MER-PK/SATA payload 形状进入回归测试。

## 教训

第三方 profile 中“看起来属于证券”的 market cap 可能实际是发行人级字段；证券身份必须先用 security-level 证据判型，再比较 share class，不能让 issuer metric 替代 instrument identity。
