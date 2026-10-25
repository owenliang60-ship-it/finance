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
- 存量：同一脚本把 VMRK 设为 ok、EQR 设为 secondary_share_class，再用共享 collector 在 VMRK 名下重拉三表 40 季（3 次 FMP 请求）。原因：VMRK 只有利润表；资产负债表、现金流量表和 vintage 都在 EQR 名下，不重拉的话 VMRK 进池后缺两张表。采集失败可用 `--collect-only` 单独重试。

## 3. 上市前市值

`historical_market_cap` 有 FMP 往前补出来的市值。BMNR 价格从 2025-06-05 开始，2021-04 起却有 687 天 ≥ $10B；SEC 记录 2025-07 只有 430 万股（约 $3300 万）。UHAL-B 是 2022-11 发行的股票，此前市值同样是补出来的。

`approximate_members_as_of` 忽略第一个价格日之前的市值行；完全没有价格的股票（多为已退市）全部保留，避免幸存者偏差。真实库对照：各回放期只去掉 BMNR、UHAL-B，没有新增。不删 `market.db` 数据。

## 本地演练（真实库副本）

- `rows_updated=16`，`metrics_rows=269`，`quick_check` ok；孤立日期检测 8 → 0。
- VMRK：live 合格；2021-09-04、2024-08-31、2026-08-29 回放池都有。
- 同一 plan 重跑：哈希不符，拒绝，不改库。

## 代码审查（/code-review high，2026-10-25）

| # | 问题 | 处理 |
|---|---|---|
| 1 | 别名翻成 VMRK 后，资产负债表、现金流量表和 vintage 仍在 EQR 名下 | 已修：脚本在 VMRK 名下重拉三表 |
| 2 | 脚本默认写本地 `market.db`，会被下一次 pull 覆盖 | 已修：默认改为云端路径 |
| 3 | 日期修复先提交，VMRK 一步失败后无法重跑 | 已修：VMRK 前置检查移到改库之前；采集失败用 `--collect-only` 重试 |
| 7 | 新测试只覆盖 9 条日期更正中的 3 条 | 已修：逐条测试全部 9 条 |
| 4 | 价格表如果被缩短，上市前过滤会静默缩小回放池 | 未改，记入数据管线优化 |
| 5 | 另外三处读 `historical_market_cap` 的地方（bootstrap、广扫、晨报）仍读到假市值 | 未改，不影响景气，记入数据管线优化 |
| 6 | 每次调用多扫一遍价格表，约 +0.65 秒，一次回放约 +13 秒 | 未改，耗时可接受 |
