# Extended Primary Universe — 需求文档

> **日期**: 2026-08-16
> **状态**: 已确认
> **范围**: Finance Data Desk 的股票池语义、基础研究数据覆盖、历史可重放与 Core Pool 退役

## 1. 动机

当前 Finance 同时存在 Core、Extended、Broad 三套 universe 语义，但不同采集器和分析器的默认范围不一致：价格、市值与 forward estimates 已接近 Extended 全覆盖，三张历史财报与派生指标却主要覆盖 Core。结果是横截面基本面排名看似面向全市场，实际只在一个偏科技、主动排除多个行业的子集中排序。

本项目要消除这种语义分叉：以 FMP screener 生成的 `$10B+` Extended Pool 作为唯一主股票池和默认讨论宇宙，停止把 Core Pool 当成独立基础集合。此后景气度、估值、预期、横截面筛选和普通公司讨论默认基于 Extended；持仓、手动关注股和 ETF 通过 overlay 进入具体任务，不改变 Extended 的严格定义。

## 2. 用户

- **Boss**：提出公司、行业、景气度、估值和历史排名问题时，默认得到 `$10B+` 全行业横截面，而不是隐含的 Core 子集。
- **Finance AI/研究 Agent**：通过统一 universe resolver 获得明确、可审计的 base universe 与 overlay，禁止各模块自行猜测股票池。
- **Data Desk 定时任务**：按同一 Extended SSOT 维护基础研究数据，并提供覆盖率、失败状态和 freshness 证据。
- **Portfolio/Options 工作流**：即使持仓或标的低于 `$10B`、属于 ETF，也能通过 overlay 被分析；但昂贵的 IV、期权链和深度研究保持按需执行。

## 3. 场景

1. **日常讨论与横截面筛选**：未指定 universe 时，默认对 Extended Pool 做基本面、景气度、估值、预期和概念排名。
2. **历史时点评估**：以某一 as-of 日期重放当时已知的财报与当时满足市值门槛的公司，避免报告日期前视和当前成分股 survivorship bias。
3. **首次扩容**：对清洗后的 Extended 公司一次性回填 Profile、至少 8 个连续季度的三张财报，并计算派生指标。
4. **日常增量更新**：财报发布后按事件更新相关公司；周末执行全池 coverage reconciliation 与漏数修复，而非每周无差别重抓 1,003 家。
5. **局部失败**：单家公司采集失败不拖垮整批；在允许窗口内使用最后可信数据并显式标记 stale，超出一个正常财报周期后从相关排名排除并告警。
6. **Overlay 分析**：真实持仓、手动关注股、基准/行业 ETF 可以加入特定请求；IV 和期权链需要时现场拉取，不做 Extended 全池持久化。
7. **Core 退役**：先通过兼容层迁移所有调用方，完成对拍和验收后再删除 Core 文件、配置与刷新逻辑。

## 4. 产品形式

本项目是现有 Data Desk 的架构升级，不新增独立 UI：

- 一个统一的 universe SSOT/resolver，明确区分 `base=extended` 与 `overlays`；
- 云端分批、可恢复的 backfill 与事件驱动增量 cron；
- SQLite 中的 current materialization、不可变 fundamental vintage 与派生 metrics；
- 覆盖率 manifest、三态失败语义、freshness gate 和只读 verifier；
- 现有 CLI、晨报、研究脚本和 Agent 工具逐步迁移到统一 resolver。

## 5. 输入与输出

### 输入

- FMP company screener：NYSE/NASDAQ、active、market cap ≥ `$10B`、排除 ETF/Fund；
- FMP Profile、Income Statement、Balance Sheet、Cash Flow、Earnings、Analyst Estimates；
- yfinance forward estimates 对拍线；
- `historical_market_cap`：历史 as-of 市值门槛；
- Portfolio holdings、manual watchlist、benchmark/industry ETFs：overlay 来源；
- Concept Registry：Extended 公司概念分类与展示标签。

### 输出

- 唯一主池：严格 `$10B+` 的 Extended membership 与可审计生成元数据；
- 任务级 universe：`Extended base + explicit overlays`，并携带来源标签；
- Extended 全池的 Profile、三张财报、季度派生指标、Earnings 与 forward estimates；
- 最新口径 current tables + 从本项目上线起不可变的采集 vintage；
- 历史排名查询所需的 as-of eligibility、filing availability 和 freshness 状态；
- 覆盖率、连续季度数、失败原因、staleness 和身份异常报告。

## 6. 约束

- FMP 调用默认串行且受 2 秒间隔、套餐能力和空返回影响；首次回填必须断点续传，日常任务必须增量化。
- `market.db` 由云端独占写入，所有任务共用 `market_db_writer` 资源锁；不得让长任务阻塞日常价格与报告关键路径。
- 云端 Python 3.10；实现与验证必须兼容该运行时。
- 当前 `market.db` 约 887MB，云盘仅余约 6.8GB；vintage 与 forward snapshot 必须有容量预算、索引和保留策略。
- 当前 statement 表按 `(symbol, fiscal_date)` 覆盖写，无法严格重放 restatement 前状态；新 vintage 只能保证上线后的严格 PIT，过去历史只能标注为 approximate PIT。
- Broad Pool 继续承担 `$1B+` 价格/市值研究底座，不升级为默认讨论宇宙。
- IV、期权链、新闻和 LLM 深度分析不得因为主池扩大而自动全量运行。
- 所有开发在独立 git worktree 中完成；代码、cron、回填和部署分别验收，不自动连成不可逆流水线。

## 7. 成功标准

- Extended 是唯一默认 base universe；Core 不再参与任何默认范围判断，最终完成软退役。
- 清洗后的可分析公司中：
  - 三张季度财报覆盖率 ≥95%；
  - 至少 8 个连续季度的可用率 ≥95%；
  - Profile 覆盖率 ≥98%；
  - forward estimates 在“FMP 确认有分析师覆盖”的分母中覆盖率 ≥95%。
- 所有缺失均被归类为 `not_applicable`、`provider_empty`、`fetch_failed`、`stale` 或 `identity_blocked`，禁止静默缺失。
- 历史查询严格使用 `accepted_date/filing_date <= as_of`；本项目上线后的 vintage 可重复得到相同输入数据与排名。
- 持仓、手动关注和 ETF overlay 不改变 Extended SSOT，且所有输出能说明标的是 base 还是 overlay。
- IV/期权请求继续按需现场拉取，不产生 1,003 家全池抓取。
- 生产回填可暂停、恢复、重跑且幂等；失败不会损坏现有数据库或覆盖可信缓存。

## 8. 风险

- **证券身份污染**：优先股、ETF、重复 share class、ADR 与 vendor symbol 错配进入分母；必须在采集前经过 security master gate。
- **当前池 survivorship bias**：用今天的 Extended 回看过去会漏掉历史成分；历史排名必须由 as-of 市值与身份状态生成候选集。
- **restatement leakage**：旧表只保存最新重述值；历史输出必须区分 approximate PIT 与 strict PIT。
- **长任务挤占 writer lock**：全量 backfill 与 forward 周更可能阻塞其他 cron；需要 staging、分片、resume 与独立时窗。
- **局部失败伪装成低景气**：缺失值不能默认为 0 或中性分；必须由 freshness/coverage gate 决定保留、降级或排除。
- **Core 硬切断链**：旧调用方直接依赖 `get_symbols()`；必须先建立兼容层、完成调用方清单与对拍再删除。
- **磁盘持续增长**：vintage 和 weekly forward snapshot 会长期累积；必须测量每周增量并设容量告警与保留策略。
- **昂贵数据范围蔓延**：Options/IV/LLM 若误继承 Extended 默认范围会导致成本或运行时间失控；需要显式禁止全池默认调用。

---

## 需求清单

- **R1**: Extended Pool 是唯一默认 base universe，定义为 FMP NYSE/NASDAQ active、非 ETF/Fund、当前市值 ≥ `$10B` 的可分析公司集合。
- **R2**: Portfolio holdings、manual watchlist 与 benchmark/industry ETF 通过显式 overlay 加入任务，不污染 Extended 定义。
- **R3**: 所有横截面基本面、景气度、估值、预期和普通公司讨论默认使用 Extended，不再隐式使用 Core。
- **R4**: Extended 全覆盖 Profile、季度三张财报、派生 metrics、Earnings、FMP/yfinance forward estimates 与 Concept Registry。
- **R5**: IV 和期权链按需现场拉取；新闻与 LLM 深度研究不做 Extended 全池默认运行。
- **R6**: 首次回填可分片、断点续传、幂等和安全恢复；日常基本面按财报事件增量更新，周末全池对账补漏。
- **R7**: current tables 保持高效最新查询，同时从上线日起保存不可变 fundamental vintage，支持严格 PIT 重放。
- **R8**: 历史候选集按 as-of 市值与身份生成，并使用当时已公布的 filing；过去无 vintage 的数据明确标记 approximate PIT。
- **R9**: 财报覆盖率、连续季度深度、Profile 与 forward coverage 达到已确认阈值，并由独立 verifier 验证。
- **R10**: 数据缺失和失败采用显式多状态语义；局部失败可降级，超过一个正常财报周期则排除并告警。
- **R11**: Core 采用两阶段软退役：兼容层与调用方迁移完成后，才删除旧文件、配置和刷新逻辑。
- **R12**: Broad Pool 保持 `$1B+` 价格/市值研究底座定位，不承担默认讨论和全量基本面采集。
- **R13**: 任何昂贵或实时数据调用必须显式指定小范围目标，不得仅因默认 base universe 扩大而全池执行。
- **R14**: 云端任务遵守单 writer 所有权、Python 3.10、磁盘容量和 cron 时序约束，并提供回滚与审计证据。
