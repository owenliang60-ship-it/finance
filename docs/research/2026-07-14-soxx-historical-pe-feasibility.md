# SOXX 历史 PE 可行性研究

**日期**: 2026-07-14
**范围**: 只读验证 FMP 数据契约、现有 Finance 数据覆盖与 SOXX 调仓语义；未修改生产数据库或 cron。

## 结论

可以按历史持仓权重重建 **SOXX rebalance-weighted GAAP TTM PE proxy**，建议同时输出季度观察点和季度快照驱动的日频 fixed-weight proxy。现有数据不足以可靠重建 2026-07-13 之前的历史 forward PE；那会把今天的分析师预期回填到过去，造成严重的 point-in-time 偏差。

推荐口径：

```text
earnings_yield_i(t) = Σq net_income_i,q × CURUSD_currency_i,q(t) / market_cap_i(t)
SOXX weighted EY(t) = Σcovered weight_i × earnings_yield_i / Σcovered weight_i
SOXX weighted PE(t) = 1 / SOXX weighted EY(t)
```

FMP `pctVal` 经非股票过滤和 `covered_by` 权重归并后作为权重 SSOT。负净利润保留为负 earnings yield；成分股只有在市值、连续四季度净利润及必要汇率全部可用时才进入计算。主指标按有效覆盖权重重新归一化，并记录权重覆盖率。`Σ market cap / Σ net income` 只作为次要的 `uncapped_mcap_basket_pe`，不能称为 SOXX PE，因为 SOXX 有 8%/4%/ADR 权重上限。

## 数据源实证

### 1. 历史持仓必须使用 fund disclosure

- FMP `funds/disclosure-dates` 对 SOXX 返回 19 个季度披露日，范围为 2021-09-30 至 2026-03-31。
- FMP `funds/disclosure` 能返回指定年度、季度的历史持仓，典型每期 33–34 条原始行，字段含 `symbol`、`pctVal`、`valUsd`、`date`、`acceptedDate`、`cik`、`cusip`、`isin`。
- 2025Q3 样本的 `pctVal` 合计为 100%，持仓日为 2025-09-30，披露接受日为 2025-11-26。
- 普通 `etf/holdings` endpoint 的历史 `date` 参数实测没有返回历史快照，而是重复返回当前持仓，因此不能用于历史重建。

官方文档：

- FMP disclosure dates: <https://site.financialmodelingprep.com/developer/docs/stable/disclosures-dates>
- FMP historical fund disclosures: <https://site.financialmodelingprep.com/developer/docs/stable/mutual-fund-disclosures>
- FMP current ETF holdings: <https://site.financialmodelingprep.com/developer/docs/stable/etf-holdings>

### 2. SOXX 调仓时点可按官方规则推导

iShares 官方材料说明 SOXX：

- 每年 9 月第三个星期五收盘后重构；
- 每年 3 月、6 月、12 月第三个星期五收盘后再平衡，新权重从下一个 SOXX 交易日开始使用；
- 目标约为 30 家美国上市半导体公司，采用自由流通市值加权，先施加 8% 上限、再对前五名之外施加 4% 上限，并对 ADR 合计施加 10% 上限；
- 3/6/12 月季度再平衡原则上只重算权重，不增加或删除成员；企业行动仍可能改变实际 ETF 持仓。

因此，季度末 disclosure 的权重快照可回映到第三个星期五收盘后的下一个交易日，但只能标记为 `fixed_rebalance_weight_proxy`。3/6/12 月若 disclosure 出现成员变化，必须报警，不能默认为官方季度调仓增删。系统同时保留 disclosure 的 `holding_date` 和 `acceptedDate`，避免把“推定生效时间”“实际观察日期”和“外部可获得日期”混为一谈。

官方材料：

- iShares product overview: <https://www.ishares.com/us/literature/presentation/ishares-sector-and-industry-etfs.pdf>
- iShares SAI / NYSE Semiconductor Index methodology: <https://www.ishares.com/us/literature/sai/sai-ishares-trust-3-31.pdf>

### 3. 历史市值覆盖足够

19 期 disclosure 的实测结果：

- 原始 ticker 并集：41；
- ticker × snapshot 共 564 个组合；
- 在持仓日向前 10 天内能找到历史市值：539 个，覆盖率 95.57%；
- 12 个 snapshot 存在少量缺口，单期最多 3 个 ticker。

生产库中 SOXX 本身已有 1,308 个交易日价格，范围 2021-04-26 至 2026-07-10，可作为日频估值序列的交易日历。

### 4. 历史利润需要回填

现有 `income_quarterly` 对主要成分股通常只有最近 9–10 个季度，约从 2024 年开始。按严格的 `accepted_date <= valuation_date` 口径：

- 2025Q3：26/26 可形成四季度 TTM；
- 2024Q4：季度末仅 4/29，披露接受日口径为 21/29；
- 2023Q3 与 2021Q3：0。

FMP `income-statement?period=quarter&limit=40` 实测可返回足够长的季度历史，因此需为历史成分股并集回填约 40 个季度，并复用现有 `income_quarterly` schema/upsert，不另建重复利润表。

### 5. 币种是阻塞性正确性约束

`historical_market_cap` 是美元，但 `income_quarterly.net_income` 可能是本币：

- ASML：EUR；
- TSM、ASX：TWD；
- ARM、NXPI、NVDA：USD。

把本币净利润直接和美元市值相除会制造数量级错误。FMP 历史 FX endpoint 实测支持 `EURUSD`、`TWDUSD` 等直接货币对。`accepted_date` 只决定一份财报在估值日是否可见；通过可见性门控后的本币 TTM 净利润应按最新 `fx_date <= valuation_date` 的即期汇率换算，才能与同日美元市值匹配。实际使用的 FX 日期和汇率必须写入审计证据。

### 6. ticker 变更需要证据化 alias

历史样本含 CREE/WOLF 等更名情形。FMP 对两者均返回历史季度利润且 CIK 相同。处理原则：

1. 优先使用 disclosure 原始 ticker；
2. 仅当缺数时，才使用同 CIK 或显式配置证明的 alias；
3. 成员级证据中记录 `raw_symbol`、`resolved_symbol` 与 alias 原因；
4. 不做模糊名称猜测。

## 时间语义

历史重建同时存在三个日期：

| 日期 | 含义 | 用途 |
|---|---|---|
| `holding_date` | FMP disclosure 的季度末持仓日 | 原始数据 provenance |
| `composition_effective_date` | 第三个星期五收盘后的下一个 SOXX 交易日 | proxy 权重开始日 |
| `composition_available_date` | FMP `acceptedDate` | 说明外部何时能知道该完整快照 |

在 `holding_date` 上计算的季度点使用当天实际观察到的 `pctVal`，解释最直接。把该权重固定回映到 `composition_effective_date` 并持有到下一次调仓的日频曲线是 **ex-post fixed-rebalance-weight descriptive proxy**，不是官方指数历史，也不是可直接回测的实时信号。若将来要做可交易回测，应单独构建“截至当日已知的 composition”序列，不能把晚于调仓日披露的完整成员表提前使用。

利润时间约束：每个估值日只允许使用 `accepted_date <= valuation_date` 的季度记录。此约束消除公告日 look-ahead，但 FMP 当前返回的历史财报可能包含后续重述，并不等同于完整 vintage database；结果必须标注这一限制。

## 推荐实现边界

本轮做：

- 保存 19 期历史 disclosure + 当前 live snapshot；
- 回填历史成分股的季度利润与必要 FX；
- 生成 19 个 `holding_date` 的观察权重 PE，并生成从 2021-09-20 起的 SOXX 日频 fixed-rebalance-weight TTM PE proxy；
- 提供查询/export、只读 verifier 和实际结果报告；
- 一次性生产 backfill，带 dry-run、资源锁、备份、可恢复和审计证据。

本轮不做：

- 不伪造历史 forward PE；
- 不修改现有 forward EPS 周六 cron；
- 不把新口径塞进 `fmp_basket_valuation`；
- 不宣称该序列是完全 point-in-time 或可直接回测；
- 不做 SOXX methodology 的完整指数复刻、权重日间漂移或企业行动日内追踪。

## Go / No-Go

**Go**。数据契约、历史持仓、市值、利润与 FX 都已验证存在，主要风险可通过独立 schema、日期语义和覆盖率门控控制。真正不可接受的路线是把当前 forward estimates 回填到过去，或把本币利润直接与美元市值相除。
