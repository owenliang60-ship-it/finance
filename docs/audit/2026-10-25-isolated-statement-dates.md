# 2026-10-25 孤立财季日期 + EQR→VMRK + 上市前市值

景气 T7 部署前的数据修复。三项都影响冻结榜内容，修完后 `code_version` 变化，冻结链按新版本重建。

## 1. 孤立财季日期（16 行）

检测：`reports/prosperity/kernel-decisions-20260930/scripts/isolated.py`（前后一年同季都对不上、前后两年彼此相差约两年）。全库 8 个，均为同一财季只有一行、日期错，`apply_fiscal_repairs` 不适用。

| 股票 | FMP 日期 | 正确期末 | 依据 | 表 |
|---|---|---|---|---|
| KR | 2021-04-28 | 2021-05-22 | SEC 10-Q report date | income |
| ACI | 2023-05-23 | 2023-06-17 | SEC 10-Q report date | income |
| AGL | 2021-02-18 | 2020-12-31 | SEC 10-K report date | 三表 |
| LB | 2024-08-03 | 2024-06-30 | SEC 10-Q report date | income |
| AEM | 2023-01-27 | 2022-12-31 | 12 月财年外国发行人，自然季末 | 三表 |
| TECK | 2024-10-23 | 2024-09-30 | 同上 | income |
| RIO | 2017-05-31 | 2017-06-30 | 同上（半年报） | 三表 |
| BEP | 2018-08-03 | 2018-06-30 | 同上 | 三表 |

- 采集侧：`src/data/fiscal_repair.py:REVIEWED_STATEMENT_DATES` 精确匹配（代码、CIK、日期、财年、季度、币种），以后 FMP 再返回同样的错日期会自动改正。
- 存量：`docs/audit/isolated-dates-20261025/identity-plan.json` 冻结 16 行哈希，由 `scripts/apply_isolated_date_repair.py` 经 `apply_reviewed_identity_updates` 执行。只改日期，不改财务数值；旧行归档到 `fundamental_current_archive`，指标重算。
- vintage 原始数据不改。strict 模式（2026-09-29 起）读 vintage，这些日期都早于该日，不影响。

## 2. EQR→VMRK

SEC EDGAR CIK 0000906107：2026-08-12 由 Equity Residential 改名 Vivmark Residential，代码 VMRK。此前两个代码同 CIK、不同名称，被判 `identity_conflict`，当前池和回放池都没有这家公司。

- `config/symbol_aliases.json`：EQR → VMRK，rename，verified。
- `config/share_class_overrides.json`：CIK 0000906107 → VMRK，以后 bootstrap 重判也得到同样结论。
- 存量：同一脚本把 VMRK 设为 ok、EQR 设为 secondary_share_class。

## 3. 上市前市值

`historical_market_cap` 有 FMP 往前补出来的市值。BMNR 价格从 2025-06-05 开始，2021-04 起却有 687 天 ≥ $10B；SEC 记录 2025-07 只有 430 万股（约 $3300 万）。UHAL-B 是 2022-11 发行的股票，此前市值同样是补出来的。

`approximate_members_as_of` 忽略第一个价格日之前的市值行；完全没有价格的股票（多为已退市）全部保留，避免幸存者偏差。真实库对照：各回放期只去掉 BMNR、UHAL-B，没有新增。不删 `market.db` 数据。

## 本地演练（真实库副本）

- `rows_updated=16`，`metrics_rows=269`，`quick_check` ok；孤立日期检测 8 → 0。
- VMRK：live 合格；2021-09-04、2024-08-31、2026-08-29 回放池都有。
- 同一 plan 重跑：哈希不符，拒绝，不改库。
