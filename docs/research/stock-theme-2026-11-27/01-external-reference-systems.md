# 外部参考系统、主题篮子方法论与 LLM 封闭集分类工程研究

> **研究日期**: 2026-11-27
> **服务模块**: `P-OWE-10` 股票主题篮子分类（Data Desk 数据层分类底座）
> **对齐需求**: `docs/design/stock-theme-requirements.md`（R1–R12）
> **研究边界声明**: 本文严格区分**「公开方法论与学术实证事实」**与**「面向本项目的推断与建议」**，并单列反例与公开资料盲点。

---

## 核心结论速览（Executive Summary）

1. **为什么传统行业分类在资金流与拥挤度监控中失效**：GICS、ICB、RBICS Focus 与 BICS 主分类均以**历史会计收入占比（50%–60% 门槛）**为核心判定锚。但二级市场机构资金抱团交易的是**边际利润弹性、资本开支瓶颈与估值乘数锚**，而非历史总营收最大头。这导致 `AMZN` 被锁在零售、`VRT`/`GEV` 与 `CEG` 被拆在工业与公用事业、`MU` 与 `NVDA`/`TXN` 被混在同一半导体子行业，直接扭曲篮子涨跌共振与拥挤度计算。
2. **「L2 互斥赛道 + 跨界大主题」双层模型符合机构最佳实践，但门槛逻辑不同**：公募主题 ETF（如 Global X / Solactive、MSCI）通常设 **20–25 只最低成分股门槛**，主因是美国税法 RIC `25/5/50` 分散化合规硬约束；卖方交易台定制篮子（Goldman Sachs Marquee、Morgan Stanley）无公募税法约束，聚焦 **15–50 只（细分主线 8–15 只）** 高流动性标的。本项目作为内部监控底座，设 **主池 ≥ 4–5 只（理想 8–25 只）** 即可消除单票财报跳空噪音并支撑篮子内两两相关性（$\ge 10$ 对配对）统计。
3. **跨界主题防稀释的核心是「区分供给侧瓶颈与需求侧使用者」**：S&P Kensho（`Core` vs `Non-Core`）、MSCI（`Relevance Score ≥ 25%`）、Global X（`Pure-Play ≥ 50%` vs `Quasi-Play` 替补封顶）与 Morgan Stanley（`Enablers` 基础赋能者 vs `Adopters` 技术采用者）均通过纯度分层防止巨头泛滥。本项目无需为凑够 25 只公募门槛而塞入边缘股，最简有效做法是在准入层**只收 `Core / Enabler`（核心直接受益/瓶颈环节），硬性排除纯 `Adopter`（买算力或用云的大公司）**。
4. **回溯历史表现与拥挤度存在严重前视偏差**：学术实证表明，主题 ETF 上市前靠事后赢家回测包装，上市后 5 年风险调整后累计跑输约 **30%**；投行定制策略指数实盘夏普比率较回测中位数衰减 **73%**。拿 2026 年的「AI 电力缺口」成分股去回算 2023 年拥挤度，既混入了跨期赢家选择偏差，又因主题诞生前跨行业低相关性压低了历史分母。数据层需记录篮子 `narrative_start_date`（叙事起点）与归属记录 `assigned_at` / `is_backfill`，供上层回测阻断前视引用。
5. **LLM 封闭集分类不能信口头置信度，须靠 Schema 字段顺序、正反例边界与双通分歧拦截**：
   - 强约束结构化输出（`strict: true` JSON Schema）必须把**简短推理与排除项核对字段放在分类枚举字段之前**（自回归按字段定义顺序生成，先出枚举等于盲猜后找补）；
   - `l1_id` 应由代码根据 `l2_id` 查字典确定性推导，不让模型独立填以彻底消灭父子层级错配；
   - LLM 自报数值置信度（如 `0.95`）严重虚高且高度扎堆，应改用**「备选次优赛道 `runner_up_l2_id` + 首轮双通比对分歧（Disagreement）」**识别真模糊标的，并对头部多元化巨头直接用 `Overrides` 焊死。

---

## 一、机构行业与业务分类参考系统对比

### 1.1 四大主流机构分类体系对比（公开方法论事实）

| 维度 | GICS（MSCI & S&P DJI） | ICB（FTSE Russell / LSEG） | FactSet RBICS（Focus vs With Revenue） | Bloomberg BICS |
| :--- | :--- | :--- | :--- | :--- |
| **维护机构** | MSCI 与标普道琼斯联合维护（[MSCI GICS 2023 Methodology](https://www.msci.com/documents/1296102/11185224/GICS+Methodology+2023.pdf)） | 富时罗素 / 伦交所集团（[LSEG ICB Overview](https://www.lseg.com/en/ftse-russell/industry-classification-benchmark-icb)） | FactSet（原 Revere Data，[FactSet RBICS Methodology Guide](https://assets.ctfassets.net/lmz2w5z92b9u/67nHF3Io7Zg8Ka1eQSqWsi/73277f7a9bc6250c727c8625bdc55164/factset_rbics_methodology_guide.pdf)） | 彭博终端（[Bloomberg Classification Fact Sheet](https://data.bloomberglp.com/professional/sites/10/131915_CDS_REF_Classification_SFCT_180315_DIG.pdf)） |
| **层级深度与规模** | **4 层**（2023-03 修订后）：11 Sectors → 25 Industry Groups → 74 Industries → **163 Sub-Industries** | **4 层**：11 Industries → 20 Supersectors → 45 Sectors → **173 Subsectors**（[ICB Brochure](https://research.ftserussell.com/products/downloads/ICB_Corporate_Brochure.pdf)） | **6 层矩阵**：14 Economies → 36 Sectors → 105 Sub-Sectors → ~316 Industry Groups → ~759 Industries → **1,600+ L6 Sub-Industries** | **最高 7 层**：覆盖股票、企业债、主权与贷款实体的多资产深层树状结构 |
| **单股归属模式** | **严格单选**（1 家公司仅归属 1 个 Sub-Industry） | **严格单选**（1 家公司仅归属 1 个 Subsector） | **双产品线分离**：<br>1. `RBICS Focus`：严格单选唯一主业<br>2. `RBICS with Revenue`：多行业收入比例拆分 | **主代码单选 + 分部收入比例原生挂载**（支持按分部营收暴露检索供应链与转型敞口） |
| **核心定量门槛** | **> 60% 总营收**直接归入对应子行业；若无单一业务 ≥ 60%，退回「营收与盈利均 ≥ 50%」→「营收+盈利合计最大贡献 + 市场认知」（[MSCI GICS 2023 Methodology](https://www.msci.com/documents/1296102/11185224/GICS+Methodology+2023.pdf)） | 以审计财报**主营收入多数来源（Majority of Revenue）**为核心判据（[ICB Brochure](https://research.ftserussell.com/products/downloads/ICB_Corporate_Brochure.pdf)） | `RBICS Focus`：将公司映射至贡献 **≥ 50% 总营收**的最细颗粒度（L6）节点（[FactSet RBICS Catalog](https://www.factset.com/marketplace/catalog/product/factset-rbics)） | 主代码按首要营收来源确定；同时向下拆分披露分部的精确营收占比 |
| **多元化巨头兜底规则** | 横跨 ≥ 3 个大板块且无任何板块占营收/利润多数时，塞入工业下 `Industrial Conglomerates` 或金融下 `Multi-Sector Holdings` | 归入最大单一营收来源子行业或综合工业类目 | 若 L6 无单一节点达 50%，沿层级树向上聚合或结合营业利润、增长驱动、资产分布与分析师定性判定主业；跨界敞口交由 `RBICS with Revenue` 表达 | 主分类落最大营收板块，副业通过分部营收百分比字段（BICS Revenue Segmentation）保留 |

---

### 1.2 为什么传统会计/产品线分类在「资金流与拥挤度监控」中失效？

#### （1）公开事实与学术实证
1. **会计总营收 ≠ 利润池与市场边际定价锚**：GICS（60% 营收线）与 RBICS Focus（50% 营收线）本质上回答的是“公司过去一年账面流水主要从哪来”，而交易台监控资金流与拥挤度需要回答“机构资金当前按什么叙事和估值锚一起买卖这批股票”。典型实盘错位包括（[MSCI GICS 2023 Methodology](https://www.msci.com/documents/1296102/11185224/GICS+Methodology+2023.pdf)）：
   - **`AMZN`（营收与利润倒挂）**：因自营电商与第三方零售流水占总营收大头（>80%），GICS 将其归入 `Consumer Discretionary -> Broadline Retail`（可选消费—综合零售），但 AWS 云业务长期贡献公司过半营业利润且是 AI 资本开支核心。把市值逾 2 万亿美元的 `AMZN` 算进零售篮子，既淹没了真实百货零售股的消费景气信号，又让云计算/超大规模算力篮子漏掉第一大云厂商。
   - **`GOOGL` / `META`（板块大类混装）**：GICS 将二者归入 `Communication Services -> Interactive Media & Services`，在 L1 板块层与传统高股息电信运营商（`VZ`、`T`、`TMUS`）同池。当市场交易“AI 资本开支”或“防御性红利切换”时，二者多空方向截然相反，L1 涨跌互相抵消。
   - **`TSLA`（估值锚脱离传统制造）**：GICS 归入 `Consumer Discretionary -> Automobile Manufacturers`，与低估值传统车企（`F`、`GM`）同组，无法反映其在智驾、储能与机器人叙事下的动量与散户期权拥挤度。
   - **`VRT`（Vertiv）与 `GEV`（GE Vernova）vs `CEG`（跨一级板块割裂）**：在 AI 数据中心供电与液冷交易主线中，资金高度同步买卖液冷配电设备商 `VRT`（GICS：`Industrials -> Electrical Components & Equipment`）、燃气轮机与电网设备商 `GEV`（GICS：`Industrials -> Heavy Electrical Equipment`）及独立核电运营商 `CEG`/`VST`（GICS：`Utilities`）。在传统分类中，它们被拆散在工业与公用事业两个互不相干的会计抽屉里，还与传统工厂自动化、普通住宅市政电力混在一起。
   - **`NVDA`、`MU`、`TXN`、`INTC`（子行业颗粒度过粗）**：GICS `Semiconductors` 子行业将 AI 逻辑算力芯片（`NVDA`/`AVGO`）、强周期大宗存储（`MU`）、工业汽车模拟芯片（`TXN`/`ADI`）一锅炖。存储涨价周期、模拟芯片去库存周期与 AI GPU 资本开支周期节奏完全不同，合在一个篮子里会导致内部信号钝化。
2. **学术文献对静态行业分类失效的定量证明**：
   - **Hoberg & Phillips (2016)** 在 *Journal of Political Economy* 发表经典研究，基于美股 10-K 业务与产品文本余弦相似度构建动态网络分类 **TNIC（Text-Based Network Industries）**，证明传统静态 SIC / GICS 既无法捕捉跨行业竞争与供应链传导，对**个股收益率同向波动（Return Comovement）与行业动量**的解释力也显著劣于基于实际业务语义的动态同伴集合（[Hoberg & Phillips, NBER w15991](https://www.nber.org/system/files/working_papers/w15991/w15991.pdf)）。
   - **BlackRock AI Labs（Vamvourellis et al., 2023）** 进一步对 SEC 10-K 业务描述使用大模型表征发现：离散 GICS 分类无法表达跨多业务线公司的连续相似性，而语义相似度直接显著对应股票真实收益相关性（[Vamvourellis et al., arXiv:2308.08031](https://arxiv.org/abs/2308.08031)）。

#### （2）FactSet RBICS Focus 的优势与局限
- **优势（事实）**：RBICS 在底层拥有 1,600+ 个 L6 细分产品节点，能把存储芯片、光器件、液冷设备、电网变压器在产品层面精确拆开（[FactSet RBICS Guide](https://assets.ctfassets.net/lmz2w5z92b9u/67nHF3Io7Zg8Ka1eQSqWsi/73277f7a9bc6250c727c8625bdc55164/factset_rbics_methodology_guide.pdf)）。
- **核心局限（事实 + 推断）**：
  1. **仍受制于 ≥ 50% 历史会计营收锚**：对“旧业务占营收 65%、新业务占利润增量与估值弹性 80%”的转型期或平台型公司（如 `AMZN`、早期的云转型软件或转型 HPC 托管的矿企），RBICS Focus 依然把整只股票锁死在旧业务抽屉，直到会计营收跨过 50% 才滞后切换。
  2. **1,600+ 个节点在 ~950 只大盘股池内严重过碎（推断）**：将 1,600 个 L6 节点套在 Finance 的 `$10B+` 美股主池（约 950 只）上，会产生大量仅含 1–2 只股票的“孤儿篮子”（等同于需求文档要砍掉的旧 L3 细碎概念），无法计算稳健的篮子宽度与相关性。

> **【对本项目的推断与建议】**
> 本项目确认的 **「L1 大板块（分母）+ L2 互斥主战赛道（按资金共振切分，40–70 个量级，单篮 ≥ 4–5 只）+ 精选跨界大主题（多选横向叙事）+ 手工 Overrides」** 架构，恰好吸收了 `RBICS Focus`（互斥单主业保证 100% 资金流不重不漏）与 `RBICS with Revenue / Thematic`（横向捕捉跨行业主线）各自的长处，同时用 **Overrides 人工锁定市场定价锚**绕开了“50% 历史会计营收滞后”这一制度性硬伤。

---

## 二、卖方交易台与指数机构「主题篮子」构造方法论

### 2.1 代表性机构主题篮子编制规则对比（公开方法论事实）

| 机构 / 系列 | 选股信号源 | 纯度分层（Purity / Tiering） | 成分股数量下限 / 上限 | 权重与防垄断约束 |
| :--- | :--- | :--- | :--- | :--- |
| **S&P Kensho New Economies**（[S&P DJI Methodology](https://www.spglobal.com/spdji/)） | NLP 扫描 10-K / 20-F 监管文件业务描述 + 分析师复核 | 拆分为 **`Core`（核心主营）** 与 **`Non-Core`（关键上游器件/多元次要）** 两档 | 单一子行业指数**无硬性最低只数下限**（按达标市值与流动性自然浮动）；综合指数 400–500+ 只 | 档内等权为主，`Core` 组相对 `Non-Core` 组系统性超配（超配因子上限 $X = 50\%$），并受 3M ADVT 流动性上限约束 |
| **MSCI Thematic Indexes**（[How MSCI Builds Thematic Indexes](https://www.msci.com/documents/1296102/43829639/How+MSCI+Builds+Thematic+Indexes.pdf/2e437bbd-05a1-ac8f-5146-b8f3bff0d6f2)） | 分部营收直接词条匹配（Direct）+ 业务简介关键词密度折算（Indirect） | 计算 **Relevance Score（0–100%）**，标准主题指数要求 **Relevance Score ≥ 25%** 方可入池 | 设计准则尽量避免 **< 10 只** 的过窄股票池（防止个股特质风险主导） | 按 `自由流通市值 × Relevance Score` 倾斜加权，单只发行人权重上限通常 **5%** |
| **Global X / Solactive 主题 ETF**（以 `DTCR` 数据中心指数为例，[Solactive / Global X](https://www.globalxetfs.com)） | FactSet RBICS L6 分部营收占比筛查 | **`Pure-Play`**（主题相关营收 **≥ 50%**）优先；部分主题设 **`Quasi-Play`**（**20%–50%** 或 **25%–50%**）替补 | 严格执行 **最低 25 只、最高 40 只**（若 Pure-Play 不足 25 只，才按序补入硬件/Quasi-Play 凑满 25 只） | 单股上限 4%–12% 不等；`Quasi-Play` 合计总权重通常严控封顶（如 **≤ 10%**） |
| **Goldman Sachs Marquee Custom / Thematic Baskets**（如 AI 电力篮子 `GSXUPOWR`，[GS Marquee Baskets](https://marquee.gs.com/welcome/our-platform/baskets)） | 卖方行业研究员产业链拆解 + 营收敞口 + 13F/Prime 持仓与日均成交额（ADTV）过滤 | 按产业链瓶颈环节切子篮（如独立发电 IPP、电网变压器、液冷温控、工程承包），剔除泛用型下游客户 | 非公募定制篮子通常 **15–50 只**（高纯度细分交易篮可缩至 **8–15 只**） | 常见流通市值封顶加权、流动性调整加权或等权，服务机构一篮子调仓与拥挤度监控 |
| **Morgan Stanley Thematic Baskets**（*Mapping AI's Diffusion*，[MS AI Enablers & Adopters](https://www.morganstanley.com/content/dam/msdotcom/what-we-do/wealth-management-images/uit/AI-Enablers-Adopters-Fact-Card.pdf)） | 卖方全球行业分析师自下而上业务实质与投资逻辑打分 | 双轴正交分层：<br>1. **生态角色**：`Enablers`（卖铲人）vs `Adopters`（用铲人）<br>2. **逻辑核心度**：`Core to Thesis` vs `Significant` vs `Moderate/Insignificant` | 策略信托/精选篮通常 **20–50 只**（研究覆盖全集可达数百只） | 仅纳入 `Core to Thesis` 与 `Significant` 标的，把“卖基础设施”与“内部用 AI 降本增效”严格分账 |

---

### 2.2 成分股数量上下限的合规与统计学根源

#### （1）为什么公募 ETF 普遍要求 ≥ 20–25 只，而内部量化监控篮只需 ≥ 4–5 只？
- **公募 ETF 的 25 只下限源于税法合规（公开事实）**：美国公募 ETF 若要维持穿透免税的受监管投资公司（RIC）资格，必须每季末通过美国国内税收法典 **IRC Section 851(b)(3) 的「25/5/50 分散化测试」**——单一发行人权重不得超过 **25%**，且所有单股权重 **≥ 5%** 的重仓股合计权重不得超过 **50%**（[ICI RIC Diversification Rules](https://www.ici.org)）。在数学上，一个纯股票等权组合如果少于 11 只股票（每只 ≥ 9.1%），合计 ≥ 5% 的仓位占比就是 100%，直接违法；等权组合至少需要 **20 只股票**（每只 5%）才能在不做阶梯砍仓的前提下自然贴合 `5/50` 线。这就是 Global X / Solactive 等指数哪怕硬凑 `Quasi-Play` 也要凑够 25 只的根本原因。
- **内部交易台监控篮子的统计边界（统计规律 + 本项目推断）**：
  Finance 的 `market.db` 不发公募基金，不受 RIC `25/5/50` 约束，核心约束来自**信噪比与相关性自由度**：
  1. **下限（≥ 4–5 只，主战篮理想 8–25 只）**：假设个股特质波动率为 $\sigma_\epsilon$，等权篮子的个股特质噪音标准差按 $\sigma_\epsilon / \sqrt{N}$ 衰减。当 $N=1\text{--}2$ 时，单只股票财报跳空 $\pm 20\%$ 即主导整个篮子涨跌；当 $N \ge 5$ 时，个股特质方差贡献降至 $1/N \le 20\%$，且篮子内部两两收益率相关系数配对数 $C(N, 2) = N(N-1)/2$ 达到 **10 对**（$N=3$ 时仅 3 对，$N=8$ 时达 28 对），足以支撑后续拥挤度（Comomentum 残差相关性）与内部齐涨率（Breadth）计算。
  2. **上限（单篮原则上 ≤ 40–50 只）**：在全池仅 ~950 只 `$10B+` 美股的约束下，任何超过 50 只（占全市场 > 5%）的篮子，内部多空分化会迅速把篮子收益拉向大盘 Beta（退化为 `SPY`/`QQQ` 影子），丧失主题锐度。

---

### 2.3 跨界主题纯度门槛（Purity Tiering）与反例分析

#### （1）机构防“万物皆 AI”污染的两道闸门（事实）
1. **价值链角色隔离（Morgan Stanley 模式）**：严格区分 **`Enabler`（供给侧赋能/卖铲人：芯片、光互连、液冷、变压器、核电 PPA）** 与 **`Adopter`（需求侧应用者：用 AI 审贷的银行、用云管库存的零售商）**（[MS AI Fact Card](https://www.morganstanley.com/content/dam/msdotcom/what-we-do/wealth-management-images/uit/AI-Enablers-Adopters-Fact-Card.pdf)）。一旦把 `Adopter` 混进基建主题篮子，全美股 500 强都会因年报提一句“部署 AI 提效”而混入篮子。
2. **经济实质敞口门槛（MSCI 25% / Solactive 50% 模式）**：要求该主题对应业务构成公司**当前核心营收（≥ 25%–50%）**或**未来 12–24 个月市场公认的第一估值驱动力（Core to Thesis）**。

#### （2）反例：ARK Invest 的前瞻高信念模式及其双刃剑效应
- **反例事实**：与 Solactive / FactSet 死卡“历史分部营收 ≥ 50%”相反，**ARK Invest** 采用自上而下成本曲线（莱特定律 Wright's Law）+ 自下而上 6 维评分（管理文化、执行力、护城河、产品领导力、论文风险、未来 5 年 15% 年化回报门槛），**不设历史当期分部营收 50% 硬门槛**（[ARK Thematic Investment Process](https://research.ark-invest.com/hubfs/1_Download_Files_ARK-Invest/Marketing_Material/ARK-Invest-Thematic-Investment-Process.pdf)）。
- **正面启示**：在主题爆发早期（例如某传统电力公司刚签下大型数据中心核电长协、或某光通信厂 800G/1.6T 新产品刚开始放量，历史 TTM 会计营收占比尚不足 30%，但股票已完全按新叙事交易），死守 50% 历史营收会把真正的领涨龙头漏在门外。
- **负面教训**：完全放弃业务纯度硬门槛、仅凭主观“颠覆性创新叙事”选股，极易引发**概念漂移（Style Drift）**，把通用大市值软件或流媒体公司塞进垂直主题（如基因编辑或太空探索），导致主题篮子退化为单一高 Beta 成长因子暴露。

#### （3）对本项目数据层是否记录 `tier/role` 的推断与建议
- **核心建议（推断）**：
  1. **准入层做减法（不凑人数）**：Global X 之所以保留 `Quasi-Play`，是因为不凑满 25 只就发不了 ETF；本项目无此限制，因此**跨界大主题默认只允许 `Core / Enabler`（核心直接业务或关键瓶颈供应商）入篮，严禁 `Adopter`（技术采购/内部使用者）入篮**。
  2. **表结构极简权衡**：需求文档 R5/第 5 节已定义简洁输出契约。若遵循 Boss 反过度工程原则，**MVP 阶段直接在字典准入规则中锁死“非 Core 不入跨界篮”即可，无需让上层模块处理复杂的二级权重折算**；若考虑未来 CIO 组合 35% 主题敞口风控可能想区分“纯正单主业核心股（如 `VRT`）”与“局部业务高度受益的大市值巨头（如 `AMZN`/`MSFT` 在 AI 算力篮中）”，最多在跨界关联表预留一个极简枚举列 `role TEXT NOT NULL DEFAULT 'core' CHECK (role IN ('core', 'enabler_giant'))`（默认全读，需要高纯度时加一行 `WHERE`），不引入连续打分或虚拟拆权。

---

### 2.4 回溯拥挤度与表现时的幸存者偏差与前视选择偏差

#### （1）学术与实务界对主题篮子回测失真的定量证据（事实）
1. **主题 ETF 事后选股与追高发行偏差（-30% Alpha 失真）**：
   **Ben-David, Franzoni, Kim & Moussawi (2023, *Review of Financial Studies* / NBER w28369)** 对美股专业化与主题 ETF 的全样本实证发现：发行人普遍在特定叙事媒体热度见顶、成分股已完成大幅上涨后打包推出主题篮子；事后回看发行前模拟曲线极其亮眼，但**正式上市后 5 年内，主题 ETF 风险调整后收益累计跑输约 30%（年化约 -6%）**，且主因不是管理费，而是成分股在成篮时点处于叙事高估极值（[Ben-David et al., NBER w28369](https://www.nber.org/system/files/working_papers/w28369/w28369.pdf)）。
2. **卖方投行定制策略指数的回测过拟合（夏普比率衰减 73%）**：
   **Suhonen, Lennkh & Perez (2017, *Journal of Portfolio Management*)** 追踪了全球投行向机构客户推销的 **215 个定制/另类 Beta 策略指数**（即卖方定制篮子的同类机构产品），发现策略实盘运行后的夏普比率较发布前回测期**中位数暴跌 73%**，规则越复杂、事后筛选自由度越高，实盘衰减越惨烈（[Suhonen et al., DOI:10.3905/jpm.2017.43.2.090](https://doi.org/10.3905/jpm.2017.43.2.090)）。
3. **拥挤度指标（Comomentum）对成分股前视选择的敏感性**：
   **Lou & Polk (2022, *Review of Financial Studies*)** 提出用篮子内部控制基本面因子后的高频残差收益率两两相关性（**Comomentum**）度量交易拥挤度——当套利/趋势资金集中涌入同一篮子时，成分股脱离基本面同涨同跌，Comomentum 飙升并预示后续踩踏反转（[Lou & Polk, DOI:10.1093/rfs/hhab117](https://doi.org/10.1093/rfs/hhab117)）。

#### （2）拿 2026 年名单回算 2023–2025 年拥挤度会产生什么具体失真？（机制推演）
假设在 2026 年 11 月定义了跨界篮子 `AI 电力缺口`（含 `CEG`、`VST`、`GEV`、`VRT`、`ETN`），直接拿这份静态名单去回算 2023–2025 年的收益与拥挤度历史分位，会同时触发三种失真：
1. **跨期赢家幸存偏差（抬高历史收益与动量分位）**：这份名单是我们站在 2026 年“已知谁签下了超大规模数据中心供电合同、谁股价翻了几倍”后挑出来的事后赢家，自动排除了 2023 年同样做电力或工业设备但未拿到 AI 订单的公司，导致 2024–2025 回测净值曲线严重虚高。
2. **分拆/新股生命周期截断（样本量突变）**：例如 `GEV`（GE Vernova）直到 **2024 年 4 月** 才从通用电气独立分拆上市。回算 2023 年时篮子成分股自动缩水，小样本相关性方差剧增。
3. **跨体制分母污染（扭曲历史拥挤度分位数）**：在 2022–2023 年上半年，“AI 电力缺口”尚未成为市场共识叙事，`CEG` 按公用事业电价逻辑交易，`VRT` 按工业设备逻辑交易，二者残差相关性接近于 0。如果把 2022–2023 年这段“主题根本不存在”的时期算进 3 年滚动历史分位数的分母里，那么 2025–2026 年只要这批股票正常按同一主题交易，其相关性分位数就会**永久卡死在 95%–99% 极值区**，产生“假拥挤常亮警报”；反之，若仅截取它们因同涨而被选入篮子的 2024 高潮段做基准，又会人为抬高相关性基线。

#### （3）数据层防前视的最小元数据建议（推断）
为辅助上层 Backtest Desk 与拥挤度模块防前视，同时严守 R1 不过度设计的边界，建议在 `market.db` 数据层仅记录以下**轻量时间戳元数据**：
- **篮子字典表（`theme_baskets`）**：
  - `created_at`：本系统首次建篮日期（如 `2026-11-27`）；
  - `narrative_start_date`（可选日期，针对跨界大主题）：该叙事在二级市场真实形成交易主线的大致起点（如 `AI 电力缺口` 标 `2024-03`；常青 L2 赛道如 `存储` 可为空）。上层算跨界篮历史分位时，严禁跨越 `narrative_start_date` 往前截取“前叙事时代”充当分母。
- **股票归属表（`stock_theme_assignments` / `stock_cross_themes`）**：
  - `assigned_at`：该归属写入数据库的真实日期；
  - `assignment_mode` / `is_initial_backfill`：区分 `2026-11-27` 首轮静态回填快照与后续每周六真实 Point-in-Time（PIT）增量变更。上层回测引擎一旦读到 `assigned_at = '2026-11-27'`，即知晓 `2026-11-27` 之前的成分归属**不具备无偏 PIT 属性**，只能做当期监控与向后实盘积累，不得当成无偏历史因子发回测报告。

---

## 三、LLM 封闭集金融分类的工程实践与常见陷阱（2024–2026）

针对覆盖池 ~1,000 只美股执行 **「L2 封闭集互斥单选 + 跨界主题封闭集多选」**，结合 2024–2026 年大模型工业落地与学术研究，核心陷阱与已验证对策如下：

### 3.1 陷阱一：Structured Outputs 字段顺序反置导致“先盲猜、后编理由”
- **机制事实**：主流 API 的严格结构化输出（如 OpenAI / Anthropic / Gemini 的 `strict: true` JSON Schema 受限解码）通过有限状态机（FSM）按 **JSON Schema 定义的字段先后顺序**逐 token 生成（[OpenAI Structured Outputs Intro](https://developers.openai.com/cookbook/examples/structured_outputs_intro)）。
- **常见错误**：把 schema 写成 `{"l1_id": ..., "l2_id": ..., "cross_theme_ids": ..., "reasoning": ...}`。此时模型在生成第 1 个分类枚举 token 时没有任何思考草稿空间，只能靠表面词频直觉盲选；后面生成的 `reasoning` 退化为替已经选死的错误标签强行圆谎。
- **最佳实践（建议）**：
  1. **Schema 强制推理前置**：字段顺序固定为 `business_anchor_note`（一句话提取主营收与核心定价业务）→ `cross_theme_exclusion_check`（逐一核对候选跨界主题是否仅属下游使用者）→ `l2_id`（封闭枚举单选）→ `runner_up_l2_id`（可空封闭枚举，记录最接近的备选赛道）→ `cross_theme_ids`（封闭枚举数组）。
  2. **`l1_id` 由代码确定性反查，禁止让 LLM 填**：既然 R2 规定每个 `L2` 互斥赛道唯一归属一个 `L1` 大板块，让 LLM 同时输出 `l1_id` 和 `l2_id` 只会白白引入“父子层级张冠李戴”的幻觉风险；LLM 只需选 `l2_id`，落库前由 Python 按字典 `L2 -> L1` 100% 确定性映射填充。

### 3.2 陷阱二：多选任务的「宽松滥标偏差（Liberal Inclusion）」与位置偏差
- **实证事实**：2024–2025 年针对多标签文本分类与系统性筛选的研究表明，经过指令微调（RLHF）的大模型在多选任务中普遍存在两大系统性偏差（[Multi-Label LLM Dynamics, arXiv:2505.17510](https://arxiv.org/html/2505.17510v1)）：
  1. **宽松包含倾向（Over-prediction / Liberal Inclusion）**：遇到公司简介中带有营销热词（如零售银行或餐饮连锁在简介里写 *"utilizing AI-driven cloud analytics"*），模型倾向于“宁可多给不漏标”，忽视排除条款，导致跨界主题纯度坍塌。
  2. **选项位置偏差（Position Bias）**：当枚举候选列表超过 30–50 项时，排在列表最开头和最末尾的类别被选中的概率显著高于中间类别。
- **最佳实践（建议）**：
  - **字典三要素结构化定义**：Git 中的每个篮子（尤其是跨界大主题）必须同时写清：
    1. `inclusion_criteria`（准入实质：直接销售该赛道核心硬件/软件/电力/基础设施，或该业务为市场核心估值锚）；
    2. `exclusion_criteria`（硬性排除：**明确列出“仅采购/使用该技术优化自身内部运营的下游客户一律排除”**，以及易混淆相邻赛道的切分线）；
    3. `anchor_examples` 与 `negative_examples`（各给 2–3 个正例股票与典型蹭概念反例股票）。
  - **两阶段缩小视野或排除先行**：在 Prompt 中先让模型识别大领域再定位细分 `l2_id`，并对跨界大主题显式规定“拿不准或仅为下游客户时输出空数组 `[]`——绝大多数传统消费、医药、金融、工业股的 `cross_theme_ids` 应当为空”。

### 3.3 陷阱三：迷信 LLM 自报数值置信度（Confidence Calibration 失效）
- **实证事实**：
  1. **口头置信度严重过度自信且高度离散扎堆**：**Xiong et al. (ICLR 2024, arXiv:2306.13063)** 系统评测证明，黑盒商业大模型自报的百分比置信度（如 JSON 里填 `"confidence": 0.95`）受对齐训练影响极度自信，大量错误分类同样输出 `0.90` 或 `0.95`，直接拿 `confidence < 0.7` 做人工复核阈值会漏掉绝大多数真实错标（[Xiong et al., arXiv:2306.13063](https://arxiv.org/abs/2306.13063)）。
  2. **受限解码扭曲 Token Logprobs**：在 `strict: true` 语法掩码下，所有非法 token 概率被清零重归一化，且不同标签名分词长度不同（单 token vs 多 token），直接读取首 token logprob 同样会产生虚假高置信度。
- **最佳实践：用「双通一致性 / 次优备选冲突」替代单次虚高分数（建议）**：
  - **首轮全池 ~1,000 只精标（一次性批处理成本极低，约数美元）**：采用 **双通独立校验（Two-Pass Verification）**——例如打乱候选列表顺序跑两次（或用主分类 Prompt + 独立审校 Judge Prompt 复核）。凡出现 **两次 `l2_id` 不一致、两次 `cross_theme_ids` 不一致、模型填出了强竞争 `runner_up_l2_id`、或分部营收跨多板块无 >50% 主业** 的标的，确定性归为 `low/medium` 置信候选集，汇总成一张精简边界表供 Boss 在 S1/S2 拍板或转入 `Overrides`。
  - **周频增量（每周仅数只新进池股票）**：对新票跑「分类器 + 独立审校（或反问排除条件）」，若双方一致且无强竞争 `runner_up_l2_id` 则标 `high` 直接静默入库；若有分歧或存在 `runner_up_l2_id`，按 R8 先落主推荐稳妥分类并在周六 Telegram 摘要单列提示 Boss 一眼扫视。

### 3.4 陷阱四：FMP 公司简介文本陈旧或公关词汇污染
- **工程事实**：第三方金融 API（含 FMP `company_profile`）的文本简介偶有两类噪音：一是转型/分拆公司沿用旧壳历史描述（如前身为比特币矿企现已签百亿算力托管长协的公司，或刚完成业务剥离的工业集团）；二是普通传统企业堆砌 `"AI-enabled"` 公关修饰语。
- **最佳实践（建议）**：
  - 组装给 LLM 的单票上下文不仅传 `company_profile.description`，同时附上 `market.db` 已有的基础结构化锚点（如 FMP 原生 `sector` / `industry`、市值量级；若本库已有分部营收摘要则一并附上），并在 Prompt 明确指示：**“以实际商业变现产品与核心客户类型为准，忽略公关修饰词”**；核心转型票或大市值跨界票一旦确认，立即沉淀进 Git `Overrides` 永久免疫文本噪音。

---

## 四、尚未查清的盲点与后续实盘对拍项

1. **卖方闭源篮子的精确调仓规则盲点**：Goldman Sachs Marquee（如 `GSXUPOWR`）与 Morgan Stanley 交易台内部定制篮子的**实时完整成分股清单、单股流动性折算公式及内部剔除阈值**属于机构客户付费终端（Marquee / Matrix）私有数据，公开渠道仅能查证其研究报告披露的产业链四层框架与编制原则，无法获取逐票精确权重表。这不影响本项目（R1 不复刻卖方权重，且需基于本库 `$10B+` 实盘 950 只股票分布自建字典）。
2. **本库 ~950 只股票在各候选 L2 赛道的真实人数分布（待库内实测）**：外部方法论给出了 ≥ 4–5 只（统计底线）至 8–25 只（理想区间）的成篮尺度，但具体哪些赛道在 `$10B+` 门槛下够 5 只（如“光与高速互连”够不够 5 只、“核电与独立发电”够不够 5 只、“量子计算”在 `$10B+` 主池内是否不足 4 只而应砍掉或并入上级），必须结合本地 `market.db` 现存 `Extended ∪ Overlay` 名单做实盘试切验证。
3. **周频云端持久 API 选型与单次耗时预算**：不同大模型 API（如 OpenAI Structured Outputs vs Anthropic Tool Use vs Gemini responseSchema）在 Python 3.10 云端环境下的 schema 严格枚举上限（部分 API 对单次 `enum` 数量或嵌套复杂度有限制）需在架构设计阶段对照目标 API 实测确认。

---

## 五、参考来源清单

1. MSCI & S&P Dow Jones Indices — [GICS Methodology (Effective March 2023)](https://www.msci.com/documents/1296102/11185224/GICS+Methodology+2023.pdf) & [MSCI GICS Overview](https://www.msci.com/indexes/index-resources/gics)
2. FTSE Russell (LSEG) — [Industry Classification Benchmark (ICB) Overview](https://www.lseg.com/en/ftse-russell/industry-classification-benchmark-icb) & [ICB Corporate Brochure](https://research.ftserussell.com/products/downloads/ICB_Corporate_Brochure.pdf)
3. FactSet — [FactSet RBICS Methodology Guide](https://assets.ctfassets.net/lmz2w5z92b9u/67nHF3Io7Zg8Ka1eQSqWsi/73277f7a9bc6250c727c8625bdc55164/factset_rbics_methodology_guide.pdf) & [FactSet RBICS Product Catalog](https://www.factset.com/marketplace/catalog/product/factset-rbics)
4. Bloomberg L.P. — [Bloomberg Industry Classification Standard (BICS) Reference Fact Sheet](https://data.bloomberglp.com/professional/sites/10/131915_CDS_REF_Classification_SFCT_180315_DIG.pdf)
5. Hoberg, G., & Phillips, G. (2016) — *Text-Based Network Industries and Endogenous Product Differentiation*, Journal of Political Economy / [NBER Working Paper w15991](https://www.nber.org/system/files/working_papers/w15991/w15991.pdf)
6. Vamvourellis, D., Tóth, M., Bhagat, S., Desai, D., Mehta, D., & Pasquali, S. (BlackRock, 2023) — *Company Similarity using Large Language Models*, [arXiv:2308.08031](https://arxiv.org/abs/2308.08031)
7. S&P Dow Jones Indices — [S&P Kensho Indices Methodology Library](https://www.spglobal.com/spdji/)
8. MSCI — [How MSCI Builds Thematic Indexes](https://www.msci.com/documents/1296102/43829639/How+MSCI+Builds+Thematic+Indexes.pdf/2e437bbd-05a1-ac8f-5146-b8f3bff0d6f2) & [MSCI Research Insight: Indexing Change](https://www.msci.com/downloads/web/msci-com/indexes/index-resources/data-partners-for-thematic-indexes/Research_Insight_Indexing_change.pdf)
9. Global X ETFs & Solactive AG — [Global X Data Center & Digital Infrastructure ETF (DTCR) / Solactive Index Guidelines](https://www.globalxetfs.com) & [Solactive FactSet Thematic Index Example](https://www.solactive.com/downloads/Factset_Japan_Cleantech__Energy_Index_v1.5.pdf)
10. Investment Company Institute (ICI) — [IRC Section 851(b)(3) Regulated Investment Company (RIC) Diversification Standards](https://www.ici.org)
11. Goldman Sachs — [Goldman Sachs Marquee Custom & Thematic Baskets](https://marquee.gs.com/welcome/our-platform/baskets) & [GS Research: Generational Growth — AI, Data Centers and Power Demand](https://www.goldmansachs.com/insights/goldman-sachs-research/the-push-for-the-green-data-center)
12. Morgan Stanley — [AI Enablers & Adopters Thematic Framework Fact Card](https://www.morganstanley.com/content/dam/msdotcom/what-we-do/wealth-management-images/uit/AI-Enablers-Adopters-Fact-Card.pdf)
13. ARK Invest — [ARK Invest Thematic Investment Process](https://research.ark-invest.com/hubfs/1_Download_Files_ARK-Invest/Marketing_Material/ARK-Invest-Thematic-Investment-Process.pdf)
14. Ben-David, I., Franzoni, F., Kim, B., & Moussawi, R. (2023) — *Competition for Attention in the ETF Space*, The Review of Financial Studies / [NBER Working Paper w28369](https://www.nber.org/system/files/working_papers/w28369/w28369.pdf)
15. Suhonen, A., Lennkh, M., & Perez, F. (2017) — *Quantifying Backtest Overfitting in Alternative Beta Strategies*, The Journal of Portfolio Management, [DOI:10.3905/jpm.2017.43.2.090](https://doi.org/10.3905/jpm.2017.43.2.090)
16. Lou, D., & Polk, C. (2022) — *Comomentum: Inferring Arbitrage Activity from Return Correlations*, The Review of Financial Studies, [DOI:10.1093/rfs/hhab117](https://doi.org/10.1093/rfs/hhab117)
17. OpenAI — [Introduction to Structured Outputs (`strict: true` JSON Schema)](https://developers.openai.com/cookbook/examples/structured_outputs_intro)
18. Xiong, M., Hu, Z., Lu, X., et al. (ICLR 2024) — *Can LLMs Express Their Uncertainty? An Empirical Evaluation of Confidence Elicitation in LLMs*, [arXiv:2306.13063](https://arxiv.org/abs/2306.13063)
19. Multi-Label LLM Classification Dynamics (2025) — [arXiv:2505.17510](https://arxiv.org/html/2505.17510v1)
