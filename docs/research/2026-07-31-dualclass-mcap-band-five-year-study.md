# 双股权组 5 年市值分歧测量（1% band 只读体检）

- 数据源：FMP `historical-market-capitalization`（stable 端点），单 symbol 单次调用即返回全窗口日频数据，无需分页
- 窗口：2021-07-29 ~ 2026-07-30，1256 个交易日
- 配置来源：`.worktrees/index-pe-morning-chart/config/baskets/share_class_groups.json`
- 脚本 + 原始 JSON + summary.json + CSV：`/private/tmp/claude-501/-Users-owen-CC-workspace-Finance/e21f818c-263a-47d8-879e-e356be4928bb/scratchpad/dualclass_band/`
- **未写入 market.db / company.db / 任何仓库文件**

## 结论一览

| 组 | Convention | 5年 P50 | P90 | P99 | Max | 1% band 超限天数/占比 | 建议 |
|---|---|---|---|---|---|---|---|
| GOOGL/GOOG | full_company_per_class | 0.0000% | 0.0000% | 0.0000% | 0.0000% | **0 / 1256 (0%)** | band 维持不变，5年零误触发，convention 稳定 |
| FOXA/FOX | split_across_classes | ratio_A 均值 52.05%（不适用 divergence 概念） | — | — | — | N/A（未被 band 覆盖） | 维持 split 分类；若误套 1% band 会 100% 天数超限 |
| NWSA/NWS | split_across_classes | ratio_A 均值 48.53% | — | — | — | N/A（未被 band 覆盖） | 维持 split 分类；若误套 1% band 会 86.3% 天数超限 |
| HEI/HEI.A | split_across_classes | — | — | — | — | N/A（HEI.A 全 5 年 0 行数据） | 无数据可测，fail-closed 是数据缺口不是 band 问题 |

## 逐组细节

### GOOGL/GOOG（唯一实际被 1% band 覆盖的组）

FMP 对 GOOGL 和 GOOG 每个交易日返回**完全相同**的市值数字（非近似，是 bit-for-bit 相同），已用 6 个跨越全窗口的日期抽样验证（2021-08-02 / 2022-07-18 / 2023-03-15 / 2024-11-01 / 2025-06-20 / 2026-07-28 全部 MATCH），全 1256 天的 divergence 序列 P50=P90=P99=Max=0。

2022-07-18 Alphabet 实际发生 20:1 拆股，该端点在拆股前后无市值跳变（正确做了股数回溯调整），不像 `daily_price` 端点在 KLAC 拆股上出现的 10x 断崖（issue035 的教训）。

**1% band 五年内 0 次误触发，没有任何时期需要担心。** convention 本身就是"每类股票各自携带一份全公司市值"，只要 FMP 这条数据线的口径不变，band 不会因为真实市场分歧而误杀，也不会因为长期漂移而放过错误数据——因为正确值本来就该是精确相等，band 反而比实际需要的宽松（可以收紧到近乎 0 的 epsilon 而不会增加误杀风险，但没有历史证据表明需要收紧）。

### FOXA/FOX（split_across_classes，band 目前不适用）

这组的市值本来就是"按类别分别计价"，不是同一个数字。5 年里 FOXA 占 FOXA+FOX 合并市值的比例稳定在 51.2%~53.3%（均值 52.05%，std 0.39pp），日间漂移中位数仅 0.04pp、最大单日漂移 0.62pp（无跳变，无断裂）。

**关键验证**：如果误把这组当成 full_company_per_class 套用 1% band（naive divergence = |mcap_FOXA/mcap_FOX − 1|），会在全部 1256 天（100%）触发超限，中位 naive divergence 8.32%，P99 12.70%，最大 14.28%（2025-12-23）。这证明该组当前的 split_across_classes 分类是刚性正确的——一旦分类错误，band 会永久性把这组判成"冲突剔除"，没有任何时期能通过。

### NWSA/NWS（split_across_classes，band 目前不适用）

NWSA 占合并市值比例 46.0%~51.2%（均值 48.53%，std 1.37pp，比 Fox 组更接近对称、也更波动），日间漂移中位数 0.04pp、最大单日漂移 1.27pp，同样无跳变。

若误套 1% band：1% 超限占比 86.3%（1084/1256 天）、2% 超限 63.5%、5% 超限 40.8%，中位 naive divergence 3.47%，P99 13.82%，最大 14.88%（2025-10-28）。比 FOXA/FOX 更值得警惕的一点：由于比例更接近 50/50，个别交易日 naive divergence 会短暂落入 1% 以下（约 13.7% 的天数),如果有人只看单点观测就可能误判这组符合 full_company_per_class——这也印证了当前 config 用固定 evidence 文本 + convention 标签而非动态阈值判断的做法是对的，不能靠"今天看起来差不多"来分类。

### HEI/HEI.A

HEI 本身 5 年 1256 行数据完整；HEI.A 在 FMP `historical-market-capitalization` 端点上**全 5 年窗口 0 行返回**，与 config 里记录的 evidence（"FMP 无 HEI.A 市值历史，该组 fail closed 直到有数据"）完全一致。这不是 band 阈值的问题，是 vendor 数据缺口——没有 HEI.A 序列就无法计算任何 divergence 分布，band 逻辑对这组从未真正运行过。

## 数据质量核查（KLAC issue035 式跳变扫描）

对全部 8 个 symbol（GOOGL/GOOG/FOXA/FOX/NWSA/NWS/HEI/HEI.A）、共 ~8800 个日频观测点，扫描日间 >20% 市值跳变（疑似拆股未回溯或数据污染）：

**零命中。** 5 年窗口内这 8 个 symbol 在 `historical-market-capitalization` 端点上没有发现任何可疑断崖，包括 GOOGL 2022-07-18 的真实 20:1 拆股也被正确处理，没有产生断层。这条数据源在这 8 个 symbol 上是干净的，不需要排除任何日期。

## 对 band 设计的回应

Boss 问的"1% band 会在哪些时期误触发"——对当前实际被 band 覆盖的唯一组（GOOGL/GOOG），答案是**从未误触发，5 年零次**，不需要分时期调整。FOXA/FOX、NWSA/NWS 两组不受 band 约束（分类为 split_across_classes），但测量证实：如果未来有人打算把 band 泛化套用到所有双股权组而不看 convention 标签，这两组会几乎永久性/大部分时间触发冲突剔除——这不是 band 阈值需要调的问题，是 convention 分类必须先行、band 只能在 full_company_per_class 组内生效的设计前提被再次验证。HEI/HEI.A 是纯数据缺口，band 无从谈起。
