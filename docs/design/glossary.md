# Extended Primary Universe — 领域术语表

> **日期**: 2026-08-18
> **用途**: 架构/plan 文档的术语基准。「定义」句来自标注来源可直接引用；「误用」为工程判断（观点）。
> 待人工复核：Yale/Goizueta 论文与 Quantopian 定义仅据检索摘要；S&P PIT vs Lagged 白皮书正文 403。

## A. 时间模型基础

1. **Point-in-Time (PIT) Data** — 由两个问题定义：「信息何时被知晓」+「当时知道的是什么」；锚定实际申报/披露日，原始值永不被覆盖。误用：把「保存了历史数据」或「加固定 lag」当成 PIT。
2. **Vintage（数据版次）** — 某序列在历史某天所呈现的完整快照（ALFRED/Croushore & Stark 传统）。本项目：`(security_id, fiscal_period, vintage_date)` 唯一确定一行不可变事实。误用：把 vintage_date 等同 filing date — vintage 是「我的库那天存了什么」，filing 是「公司那天报了什么」，vendor 修订/回填时分叉。
3. **Valid Time** — 事实在现实中为真的时间（SQL:2011 application time）。基本面的锚 = 财季/期末日。误用：同一列混装期末日和披露日。
4. **Transaction Time** — 事实被写入库的时间（system time）。只能向前追加。误用：允许 UPDATE 覆盖历史行 — PIT 靠 append-only 不变式，不靠加字段。
5. **Bitemporal Model** — 双轴建模，可查「在 valid-time X 上、以 transaction-time Y 的认知，值是多少」。重述 = retroactive write（valid 不变，transaction 新增行）。误用：单轴宣称 PIT。
6. **As-Of Query / Timeslice** — 给定 `(as_of_valid, as_of_transaction)` 取二维切片。理想读接口只有这一种形状，研究与生产共用。误用：as_of 默认 now，最新视图和历史视图走两条代码路径 → 无声分叉。
7. **As-Of Join** — 左表每行取右表「时间上最近的、不晚于它」的行（kdb+ `aj` / DuckDB ASOF）。误用：(a) inner 变体让无财报股票静默消失（隐性 survivorship）；(b) 忘按 security 分区；(c) 边界含当日把盘后披露用于当日收盘。
8. **SCD Type 2** — 变化时插新行不更新旧行，携带 `effective_from/effective_to/current_flag`。security master 与 membership 的标准落地。误用：所有查询走 current_flag（退回 Type 1）；effective_to 混用 NULL 和 9999-12-31。
9. **asof_date / timestamp 双字段模式**（Quantopian）— `asof_date`=数据对应日（valid），`timestamp`=系统学到时刻（transaction），回测引擎在 sim date 超过 timestamp 前不暴露。误用：timestamp 设成文件写入时间，历史回填时全塌缩成同一天，PIT 保证归零而 schema 看上去完好。

## B. 申报时间语义

10. **Period End Date / datadate** — 报告期结束日（valid time 锚点）。误用：当成可用日（Q3 期末 9/30，市场 10 月下旬才知道）。
11. **Filing Date** — EDGAR 官方申报日；17:30 ET 前接受得当天，之后多数得次营业日 06:00 ET 且当天不分发。PIT gate 最保守可辩护选择。
12. **Acceptance Datetime** — EDGAR 实际接受时刻，**不等于** filing date（跨 cutoff 差一天）。最接近「公众可抓取时刻」。建议两个都存。
13. **Earnings Announcement Date（RDQ）** — EPS 首次公开报告日（8-K/新闻稿），通常早于 10-Q 归档 1-4 周。误用：只用 filing date 做 gate → PEAD 窗口被切掉；只用 RDQ → 用上新闻稿里没有的明细科目。观点：按字段分级 — EPS/营收用 release date，明细科目用 filing date。
14. **Reporting Lag / Lag Convention** — 期末到可得的间隔。近似约定：季末+15 营业日（实务）或 FF 年报 6 个月。是降级方案 + PIT 上线后的 sanity-check baseline，不是 PIT 等价物。

## C. 数据修订语义

15. **Big R vs little r restatement** — Big R 对前期重大，须 8-K Item 4.02；little r 不重大，一般**无 8-K**，占重述比例 2020 年已达 **76%**。含义：管道很可能在无公告信号下收到改过的数字 — 变更捕获必须靠值比对，不能靠事件监听。
16. **As-First-Reported vs Restated** — vendor 的 "unrestated" 实为**首次标准化**结果（相对 SEC 原文已 adjusted），且可能是 preliminary 也可能是 final。这定义了 vintage #1 的语义边界。
17. **Re-standardization** — vendor 改标准化规则回溯改历史值：公司没改数字，数据变了。vintage 轴上的第三类事件，只有 transaction time 能区分。
18. **Preliminary vs Final** — 8-K 初步数字 vs 10-Q/K 最终数字可能不一致。同一字段混装两种口径必须打来源标记。

## D. Universe 与身份

19. **As-Of Universe Membership** — 某历史日确切属于 universe 的证券集合；筛选条件必须用**当日可得**数据评估。误用：今天的名单回溯历史（survivorship + look-ahead 复合）。
20. **Reconstitution vs Rebalance；AD vs ED** — reconstitution 增删成分，rebalance 只调权重；Announcement Date（收盘后公告）与 Effective Date 通常隔约一周，membership 表两个都要存。
21. **Security Master** — 金融工具标识与元数据的权威库：内部 primary key + 外部标识（ticker/CUSIP/ISIN/FIGI）映射 + 每次变更记 effective date。PIT 平台的身份层；没有它 ticker 复用会让 vintage 链无声断裂。
22. **Symbology / Symbol Mapping** — vendor 标识 → 内部标识对照表，多数据源拼接唯一正确的接缝位置。误用：做成无时间维度的字典。
23. **Permanent Identifier（PERMNO/GVKEY/FIGI）与 Ticker Reuse** — 只有 permanent id 撑得起跨年时间序列（FIGI 永不复用）。关键陷阱：PERMNO 也非绝对永久 — 重组会产生新号（Manville 16707→90100），证券级永久性 ≠ 实体级连续性（还需 successor 链）。
24. **Issuer vs Instrument vs Share Class** — 基本面挂 issuer 层（一份 10-Q 覆盖全公司），价格/市值挂 instrument 层。误用：income 直接挂 ticker → 多 class 公司（GOOG/GOOGL、BRK.A/B）财报重复计数或市值漏计。
25. **Corporate Action 与 per-share 追溯调整** — ASC 260 要求拆股时对所有列示期间的 EPS **追溯调整** — 这是 vintage 轴上一类**合法的**回溯变更，必须记成新 vintage 而非改写旧 vintage。本地实例：issue035（KLAC 10:1 拆股跨表失真）。
26. **Delisting Return / Delisting Bias** — Shumway：业绩性退市样本 99.8% 的退市回报在 CRSP 缺失且平均大幅为负，Nasdaq 建议用 −55% 填补。仅把退市股加回名单只修一半 survivorship。
27. **Look-Ahead Bias** — 三形态：direct（用未来价格）/ data revision（用最终修订值）/ knowledge（以未来事件为条件）。vintage 层针对 data-revision，filing-date 查询针对 direct。检测：shift test。
28. **Survivorship Bias** — 样本只含存活者。与 look-ahead 分工：survivorship 抬高**样本质量**（谁在名单里），look-ahead 抬高**入场质量**（那天你知道什么）；独立发生、独立修复。
29. **Index Reconstitution Bias** — 当前成分回溯套历史会吃到纳入前的超额收益。

## 易混淆术语对照

| 术语 A | 术语 B | 核心区别 |
|---|---|---|
| Point-in-Time | As-Reported / Restated | PIT 是**数据集属性**（保留全部版次）；后两者是**单个版次**（≈vintage #1 / 最新 vintage）|
| Valid time | Transaction time | 事实为真的时间（财季）vs 被写入库的时间（认知）|
| Survivorship | Look-ahead | 样本质量 vs 入场质量；独立修复 |
| Period end | Filing date | 期末 vs 申报日，通常隔 4-8 周 |
| Filing date | Acceptance datetime | 法定日期（17:30 cutoff）vs 实际接受时刻 |
| Filing date (10-Q) | RDQ (8-K) | 归档 vs EPS 首次公开，差 1-4 周 |
| Vintage date | Filing date | 我的库存了什么 vs 公司报了什么 |
| 公司 restatement | Vendor re-standardization | 公司改数字 vs vendor 改映射规则 |
| Big R | little r | 须 8-K vs 无 8-K（76%）|
| Reconstitution | Rebalance | 增删成分 vs 只调权重 |
| AD | ED | 公告日 vs 生效日，隔约一周 |
| Ticker/CUSIP | PERMNO/GVKEY/FIGI | 会变会复用 vs 永久（但重组换新号）|
| Issuer | Instrument (share class) | 基本面归属 vs 价格/市值归属 |
| Lag convention | 真 PIT | 统计近似 vs 事实记录，回测结果显著不同 |
| SCD Type 2 | Bitemporal | 单轴（有效期）vs 双轴（无法用 SCD2 表达同一财季不同认知时点）|
| current_flag 视图 | As-of 查询 | 「最新」vs「截至某认知时点的最新」|

## 来源（精选）

- [S&P Global: PIT vs. Lagged Fundamentals](https://www.spglobal.com/market-intelligence/en/news-insights/research/point-in-time-vs-lagged-fundamentals)（正文 403，据摘要）
- [ALFRED Help](https://alfred.stlouisfed.org/help) / [Philadelphia Fed Real-Time Data Set](https://www.philadelphiafed.org/surveys-and-data/real-time-data-research/real-time-data-set-for-macroeconomists)
- [XTDB: Bitemporality](https://v1-docs.xtdb.com/concepts/bitemporality/) / [Wikipedia: Temporal database](https://en.wikipedia.org/wiki/Temporal_database)
- [DuckDB: AsOf Join](https://duckdb.org/docs/current/guides/sql_features/asof_join) / [kdb+ aj](https://code.kx.com/q/ref/aj/)
- [SEC Webmaster FAQ](https://www.sec.gov/about/webmaster-frequently-asked-questions)（403，据摘要）
- [WilmerHale: little r Restatements](https://www.wilmerhale.com/en/insights/blogs/keeping-current-disclosure-and-governance-developments/20220317-sec-oca-waves-big-red-flag-about-little-r-restatements)
- [Intrinio: Modern Security Master Architecture](https://intrinio.com/blog/modern-security-master-architecture-unifying-ticker-cusip-isin-and-figi-data-at-scale)
- [NYU WRDS Linking](https://guides.nyu.edu/wrds/linking-suite) — Manville 断裂实例
- [PwC Viewpoint 7.6](https://viewpoint.pwc.com/dt/us/en/pwc/accounting_guides/financial_statement_/financial_statement___18_US/chapter_7_earnings_p_US/76_change_in_capital_US.html) — ASC 260
- [Shumway 1997](https://www.tylergshumway.org/Shumway-DelistingBiasCRSP-1997.pdf) / [1999](https://tylergshumway.org/Shumway-DelistingBiasCRSPs-1999.pdf)
- [Susan Potter: Taxonomy of Backtest Lies](https://www.susanpotter.net/quant/backtest-bias-taxonomy/)
- [S&P DJI: Index Effect](https://www.spglobal.com/spdji/en/documents/research/research-what-happened-to-the-index-effect.pdf)
- [Quantopian Data Reference](https://www.quantopian.com/docs/data-reference/overview)（站点停运，据摘要）
