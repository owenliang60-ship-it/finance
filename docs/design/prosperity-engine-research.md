# 美股景气引擎 — 领域研究汇总（Phase 2）

> **日期**: 2026-09-26
> **状态**: Boss 已审阅，D1–D8 已拍板（2026-09-26）
> **输入**: 需求文档 `docs/design/prosperity-engine-requirements.md`（已确认，含第 9 节融合方案）
> **原始报告**（含全部来源链接）：`docs/research/prosperity-engine-2026-09-26/`
> - `01-factor-literature.md`：基本面动量因子的证据与陷阱（环比、EPS、预期、PE、口径）
> - `02-pit-and-sector.md`：点时数据、财年对齐、行业处理、数据质量、一致预期的点时特性
> - `03-reference-systems.md`：参考系统拆解、技术选型、本地可复用部件、周频节奏与网页
> **本地实测说明**：研究 2、3 的实测数字来自本地 `market.db` 副本（2026-09-26 06:34），该副本的 `historical_market_cap` 表已损坏，涉及该表的数字要在重拉后复核。

---

## 1. 一页结论

1. **大盘池是基本面动量效应最弱的区域。** PEAD 在大盘股中约 2006 年归零（Martineau），异象发表后收益平均下降 58%（McLean-Pontiff），SUE 在最大市值五分位只剩约 0.26%/月（Novy-Marx 2015）。在 $10B+ 池里，这个雷达的价值主要是**缩小研究范围、尽早发现转弱**，不是靠分数本身取得超额收益。这和"筛股工具、不靠回测"的定位一致。
2. **本地数据证明不了任何单个因子，也拟合不了参数。** 22 个季度要达到 t>3，季度 ICIR 需 ≥0.64（研究 1 估算），远高于基本面因子的常见水平。权重、阈值只能取自先验文献并注明出处；体检报告只看方向、单调性、集中度和与价格动量的重叠。
3. **"环比"在对数口径下与"同比加速度"是同一个量。** 本季环比 − 去年同季环比 ≡ 本季同比 − 上季同比。原始环比季节性极强（43% 的公司某个财季环比中位数 >10%），不能直接打分；季节调整后又与加速度重合。需要 Boss 决定环比以什么形式进入（见第 4 节 D1）。
4. **净利润和 EPS 高度共线**；**NTM/TTM PE 比值 ≡ 预期 EPS 增速**。这两组都不能重复计分。
5. **预期类因子**：历史强度 surprise ≈ 修正幅度 > 修正广度；**预期增速水平是负向的**（La Porta）。在分析师密集覆盖的大盘股里，修正幅度基本失效，广度仍显著（Guerard，Russell 1000）。最适合做风控信号（负修正、连续不及预期）。
6. **PE 适合做护栏和展示，不适合进景气分。** 排名用 E/P（盈利跨零时连续）；"高 NTM PE × 负修正或负 surprise"亮红旗（Skinner-Sloan 的 earnings torpedo）。
7. **EPS 口径已核实**：FMP 一致预期是 street 口径（GLW/NVDA/META 抽查与 `fmp_earnings.eps_estimated` 一致）。TTM 必须用 `fmp_earnings.eps_actual`（street）求和；GAAP 只作展示列。本地 street 与 GAAP 相差 ≤1 美分的只有 25.5%。
8. **仓库已有约 70% 的底座**：精选池 JSON 的 schema/新鲜度/原子发布、Premium 门槛纯函数、PIT 成员与 PIT 基本面读取器、质量审计、NTM 计算内核、IC/分位收益/统计工具、HTML 生成器。不需要任何新依赖。

---

## 2. 参考系统（研究 3）

| 系统 | 值得借鉴 | 不要照搬 |
|---|---|---|
| Alphalens | 长表输入契约（期 × 股票 → 因子值 / 前瞻收益）；体检报告分区：收益、IC、换手、分组 | 依赖本身（日频设计、只有标准误没有置信区间） |
| Zipline Pipeline | Factor / Filter / Classifier 三分法；**z 值只在过了门槛的股票（mask）上算** | DAG / 惰性计算引擎 |
| Qlib | 一份配置 → 一次运行 → 产物挂在 run 下；截面处理器串联 | `.bin` 存储、表达式引擎、MLflow Recorder；PIT 库（我们已有 `fundamental_vintage`） |
| MSCI Quality / Barra USE4 | 5/95 winsorize 或 3σ 截断；行业中性在**板块层**做并截断 ±3；避免"薄行业"；缺一个因子时用剩余因子平均 | — |
| Piotroski / Mohanram | 规则计数适合做门槛层；G-score 更擅长识别输家 | 开源实现基本不处理 PIT |
| OpenBB | 与本项目无关（纯数据层） | — |

---

## 3. 领域陷阱清单（跨三份研究汇总）

| # | 陷阱 | 量级 / 证据 | 应对 |
|---|---|---|---|
| P1 | 用重述后的数据回溯 | Compustat 数据点平均被改写 6 次，改动时季度销售平均变化 144%（LSY 2024）；本地 vintage 从 2026-08-22 才开始 | 2021–2026-08 的榜单全部标 `approximate`；同比类因子受影响小于水平类 |
| P2 | 公告日与 10-Q 提交日混用 | Q1–Q3 大盘股中位 0 天、p90 9 天；Q4 中位 7 天；银行晚 16–22 天；公告稿通常没有完整 BS/CF | 可得时间用 `accepted_date`，缺失退回 `filing_date`；BS/CF 字段禁用公告日 |
| P3 | 53 周财年的 14 周季度 | 本地 75 只；营收同比 / 环比虚增约 7.7%，下一期再虚减；加速度出现 ±7.7pp 假信号 | 流量科目按 91/季度天数折算，打 `nonstandard_quarter` 标签 |
| P4 | 16/12 周季度结构 | COST、KR、AZO、PEP、DPZ、AAP；环比机械地 −25% / +33% | 环比必须先折算再做季节差分，否则禁用 |
| P5 | 低基数与符号翻转 | 加速度同时依赖 t−4 和 t−5 两个基期，低基数被放大两次；本地 street TTM EPS ≤0 的有 40/885 | 利润类用"差值 ÷ 股价或自身 σ"，不除以 \|基数\|；盈利→盈利 / 扭亏 / 转亏三分类 |
| P6 | 原始环比的季节成分 | 股票回报与销售季节反向（Grullon 2020，年化 alpha 8.4%） | 原始环比只展示，不打分 |
| P7 | 共线因子重复计权 | 加速度 ≡ 季调环比；净利润 ≈ EPS；NTM/TTM ≡ 预期增速；SUE ≈ EPS 同比变化 | 同族因子合计权重设上限，或只留一个 |
| P8 | 行业口径不可比 | 季度毛利率跳变 >15pp：公用事业 15.7%、能源 13.3%、金融 12.0%、科技 3.0%；银行"毛利率"是利率周期的函数 | 口径不成立的因子按行业豁免；剔除对照版按 industry 清单剔，不整板块剔（保留 V、MA、SPGI、ICE） |
| P9 | 薄行业 | FMP 128 个 industry 里 65 个不足 5 只 | 中性化只在 11 个 sector 层做 |
| P10 | 一致预期的点时陷阱 | FMP 公告后仍改 12% 的 EPS 预期、25% 的 NI 预期；从不替换为实际值；`netIncomeAvg` 是 EPS × 股本推导的，股本周度跳变 >30% 有 27 次 | 公告前一致预期只取本地严格早于公告日的最后一个快照；NI 预期不独立使用 |
| P10b | FMP 预期表的财季日期会漂移 | 同一预期的 fiscal_date 在周快照之间被改写 5,963 次（954 只），多为整体提前一天 | 预期与实际按财季 ±10 天匹配，禁止精确日期连接 |
| P11 | 修正的目标期错位 | NTM 窗口每出一季财报就滚动一次，产生台阶 | 修正只比较同一组固定财季的预期 |
| P12 | 拆股口径不同步 | MNST 拆股后一周 NI 预期翻倍；APH 的 eps_actual 序列口径混合；splits 表混入分拆因子（FDX、DD、FTV…）；KLAC 市值钉死旧股本（issue035） | PE 用"同一时点每股价格 ÷ 每股 EPS"；只认整数比真拆股；拆股 14 天窗口内校验，不通过当周置空 |
| P13 | street 与 GAAP 混用 | 本地 street 与 GAAP 相差 ≤1¢ 只有 25.5%，符号相反 5.6%；issue036（GLW 1.35 倍） | 所有 NTM/TTM 比值两边都用 street；GAAP 只展示并标注 |
| P14 | 周频混合时点截面 | 8/10 那周 76% 的公司已报 6 月季，其余停在 3–5 月季；财报季中每周 20–30% 的公司更新 | 周榜用各自最新季度 + `quarter_bucket` / `data_age_days` / `reported_this_week`；z 值参数沿用最近一次季末冻结榜，不在混合截面上重估 |
| P15 | 历史成员不全 | `historical_market_cap` 2021-04 那周只有 536 只；VMW、HZNP 完全缺失 | 首个成员日不早于 2021-09（重拉后复核） |
| P16 | 数据错误冲进 Top N | rev − COR ≠ GP 53 行、revenue<0 18 行、GP>revenue 84 行、单位错误（YPF）、filing_date 早于季末 7 行 | 质量规则缺口 Q8–Q17、E1–E7 补齐，异常打标不静默吞掉 |
| P17 | 与价格动量重叠 | Novy-Marx 2015：盈利动量几乎解释了价格动量；修正常跟着股价走 | 体检报告必做与价格动量 Top N 的重叠度 |
| P18 | 小样本过度解读 | 22 季、forward 只有 11 周 | 逐期统计 → t 区间；短史因子标 UNKNOWN（n=x），不给显著性结论 |

---

## 4. 需要 Boss 拍板的设计分歧

研究之间或研究与需求第 9 节之间存在分歧，每项给出推荐。

| # | 问题 | 选项 | 推荐 | 理由 |
|---|---|---|---|---|
| D1 | 环比怎么进打分 | (a) 把"营收加速"改名为"季调环比"，EPS 同样处理，只算一次；(b) 另加"多年同季基准环比"（本季环比 − 过去 3 年同季环比中位数），与加速度同族、合计权重设上限；(c) 原始环比直接打分 | **(b)**，上线前先测它与加速度的截面秩相关，ρ>0.7 就退回 (a) | 满足你要的"环比"维度，又不重复计权；(c) 有文献反证 |
| D2 | 净利润是否单独计分 | (a) 只用 EPS 计分，净利润展示；(b) 两者都计分，同族合计权重设上限 | **(a)** | 两者只差股本；EPS 与一致预期、PE 同口径 |
| D3 | 标准化方式 | (a) 保持原框架 winsorize z；(b) 改为排名后取 z（QMJ） | **(a) 作主方案，(b) 作并列方案** | 你要求其他保持原框架；方案注册表支持并列对比 |
| D4 | 主榜是否对银行 / REIT / 公用事业豁免毛利率等口径不成立的因子 | (a) 不豁免，完全跨行业；(b) 因子级豁免，缺失的因子按覆盖率摊权 | **(b)** | 银行"毛利率"不是噪声，而是另一个量；摊权机制原框架就有 |
| D5 | forward 因子怎么用 | 候选：修正幅度（同财季、÷股价）、surprise（有长历史，`fmp_earnings` 可回溯）、预期增速（只作门槛 >0）、PE（只作护栏和展示） | **surprise + 修正幅度进打分，同族小权重；预期增速作门槛；PE 只作护栏和展示** | 大盘股里 forward 效应衰减最多、本地无法验证；surprise 是唯一有长历史的 forward 族因子 |
| D6 | NTM 构造 | (A) 下 4 季求和；(B) 时间加权 FY1/FY2 | **A 默认，第 3、4 季分析师 <3 人时退回 B 并打标签** | A 与本地已审计的 `compute_forward_member` 一致；约 40% 的公司远季覆盖薄 |
| D7 | 首个成员日 | (a) 2021-09 起；(b) 先回补 2021 年 4–8 月市值 | **(a)**，重拉后复核 | 少 2 期换不来回补成本 |
| D8 | 毛利率水平与 FCF 率变化的证据弱 | (a) 保持原框架；(b) 改为 GP/A、CbOP | **(a) 作主方案；(b) 列入 R15 改良候选** | 你要求其他保持原框架 |
| D9 | **历史财报深度不足（研究 2 补充，主线程复核）**：当前 919 只成员里，季度利润表 2021 年只有 518 只有数据，2024 年起才到 911 只。约 400 只（扩展池后加入的）只回填到 2024 | (a) 先做一次数据侧补数，把这约 400 只的季度三表回填到 2019；(b) 历史榜单从 2025 年起（需要 5–8 季历史才能算加速度和 8 季斜率），只有约 5 期；(c) 2021–2023 只用有数据的约 520 只，并标注样本偏差 | **(a)**，作为北极星里的数据前置任务，单独走 plan | (b) 样本太短；(c) 这 520 只基本是旧 Core 池，偏科技和超大盘，偏差大 |

---

### 拍板记录（2026-09-26）

- **D1 → 加速度即环比**：不新增环比因子；"营收加速"明确定义为季调环比（先按天数折算），EPS 同样加一个加速度因子。原始环比和多年同季基准环比只展示。
- **D2 → 只用 EPS 计分**：净利润增速和"净利润增速 − EPS 增速"放展示层。
- **D3 → 按推荐**：winsorize z 为主方案，排名 z 为并列方案。
- **D4 → 因子级豁免**：银行、保险、REIT、公用事业等口径不成立的因子记为缺失，按覆盖率摊权。
- **D5 → surprise + 修正幅度进打分（同族小权重），预期增速作门槛（>0），PE 只作护栏和展示。**
- **D6 / D7 / D8 → 按推荐**（NTM 用 A、远季覆盖薄时退回 B；2021-09 起；GP/A、CbOP 列入改良候选）。
- **D9 → 先补历史财报**：约 400 只成员的季度三表回填到 2019，作为数据侧前置任务单独走 plan（云端写入，拿 `market_db_writer` 锁）。

## 5. 研究补出的盲点

1. **修正广度在 FMP 里拿不到。** `fmp_estimates` 只有分析师人数，没有上调 / 下调家数。唯一来源是 yfinance `forward_estimates` 的 `eps_rev_up/down_7d/30d`，而 yfinance 线原计划四周对拍后退役。要用广度，就要保留 yfinance 线。
2. **surprise 是唯一有长历史的"预期"类因子。** `fmp_earnings` 有 `eps_estimated` 和 `eps_actual`，可回溯到 1985 年，可以做 20 期检验。但 `eps_estimated` 与本地公告前最后快照一致的只有 65%，两者要并列标注来源。
3. **PDD 类 ADR 的币种。** 抽查发现 PDD 的一致预期约 18.5（人民币口径），实际值 2.85（美元）。`compute_forward_member` 有 FX 处理，Phase 3 要确认景气引擎走同一条路径。
4. **云端磁盘 97%，剩 1.6G。** 景气引擎只新增约 20 万行的小库，但 `market.db` 备份无限累积（issue046）会让所有 cron 写库失败，属于上线前置风险。
5. **SEC 半年报提案（2026-05）** 如果落地，部分公司的 Q1/Q3 只剩 8-K，季度框架要有"数据来源等级"字段。当前只需预留字段。

---

## 6. 技术选型（研究 3）

| 决策点 | 推荐 | 备选 | 取舍 |
|---|---|---|---|
| 方案注册 | Python 冻结 dataclass + `scheme_hash` + `changes`；hash 写进每行分数和池 JSON，读取端对不上就 fail-closed | JSON 文件 + loader；MLflow | 5–10 个方案、零依赖；照抄 `premium_pool.py` 的 criteria 校验 |
| 存储 | 独立 `prosperity.db`（云端 live 单写者）+ 本地 `prosperity_research.db` | 写进 `market.db` | 不碰 P3 所有权 |
| 表形态 | 长表：runs / schemes / stock_period / factor_values / scheme_scores | 宽表 | 方案和因子可配置，不需要改 schema |
| 统计 | 逐期统计（IC、分位 spread、Top N 超额）→ 对约 20 个期值做 t 检验和 t 区间；重叠窗口用 NW；多方案做 BH-FDR | 股票级混合 bootstrap | 后者在截面相关下严重低估不确定性；函数本地都有 |
| 网页 | f-string + 内联 JSON + Chart.js（jsdelivr），复用 `html_report.py` 的 CSS，自包含单文件 | Jinja2 / Plotly | 零新依赖 |
| 调度 | 新 job `finance_prosperity`，周六 16:30，同一把 `market_db_writer` 锁，锁忙返回 75；输入门控不满足就沿用旧榜并标 stale；可 `--as-of` 补跑 | 塞进 `run_weekly_fundamentals.sh` | 独立 job 失败不牵连 Premium |
| 财报季冻结 | 季末 60 天后第一个覆盖率 ≥95% 的周六冻结，80 天硬上限；冻结榜不可覆盖 | 固定日历日；纯覆盖率 | 实测：65–75 天进入 94–97% 平台，Q4 慢约一周 |
| 网页访问 | 待 Phase 3 比较：A 云端静态 HTML → 本地 pull 打开；B Telegram 附件；C claude.ai 私有页面 | 公网 nginx / GitHub Pages（排除） | — |
| 运行环境 | 云端 Python 3.10.12 + pandas 2.3.3（已核实）；新代码不用 pandas 3 或 3.11+ 标准库 | — | `requirements.txt` 写的 `pandas>=3.0` 与云端不符，属既有问题 |

**不做**：表达式引擎 / DSL、DAG 引擎、MLflow、方案管理后台、可插拔 transform 框架（一个枚举 + if 分支）、策略回测（成本、仓位）。

---

## 7. 本地可复用部件（研究 3 盘点，节选）

| 用途 | 部件 |
|---|---|
| 精选池 JSON | `src/data/premium_pool.py`（schema、8 天新鲜度、覆盖率 ≥0.95、原子发布、四种不可用原因） |
| Premium 规则 | `terminal/selection_compass.py`（EPS 同比 / 环比 ≥20% 含扭亏语义、4Q CAGR、β） |
| PIT 成员 | `market_store.get_members_as_of` / `approximate_members_as_of` |
| PIT 基本面 | `market_store.known_as_of` / `approximate_as_reported`（带 `approximate` 标记） |
| NTM / street blend | `terminal/forward_valuation.py:101 compute_forward_member`；`terminal/hindsight_ntm_valuation.py` |
| 质量审计 | `src/data/fundamental_quality.py`（稳定 issue 码） |
| 统计 | `backtest/factor_study/ic_analysis.py`、`backtest/event_study/stats.py`（date-cluster t、BH-FDR）、`evaluation.py:316 newey_west_tstat` |
| 报告 | `terminal/html_report.py`、`backtest/factor_study/report.py` |
| 晨报接入 | `scripts/morning_report.py` 的 `load_premium_pool()` 调用点 |
| 独立 SQLite 先例 | `src/timing/state_store.py`（`btc_timing.db`） |

完整清单（约 18 项，含行号）见 `03-reference-systems.md` §7、§9.4–9.6。

---

## 8. 质量门槛自评

| 维度 | 门槛 | 结果 |
|---|---|---|
| 参考系统 | ≥3 个有价值的参考架构 | ✅ Alphalens、Zipline、Qlib，另有 MSCI / Barra 方法论 |
| 领域概念 | 能解释 10 个核心术语 | ✅ 见 `prosperity-engine-glossary.md`（约 30 条） |
| 常见坑 | ≥5 个高频失败模式 | ✅ 18 条（第 3 节） |
| 技术选型 | ≥2 种方案及优劣 | ✅ 第 6 节，每个决策点至少 2 个选项 |

**交叉验证**（主线程完成，未另开 subagent）：
- 三份研究在财报季冻结规则上有出入：研究 2 建议固定第 60 天，研究 3 用覆盖率实测校准为 60 天 + ≥95% + 80 天硬上限。采用研究 3。
- 研究 1 建议合成改用排名，研究 2、3 沿用 winsorize z。按你的"保持原框架"取 winsorize z 为主，排名作并列方案（D3）。
- 研究 2 的 S2 让净利润和 EPS 都进环比，研究 1 建议只用 EPS。列为 D2。
- FMP 预期口径（研究 1 未核实）已由主线程抽查确认为 street。

**仍未核实**：S&P 的毛利趋势研究、Thomas-Zhang 全文、Petersen 标准误论文只读到摘要；Qlib、OpenBB 官方文档站点被网络拦截；所有依赖 `historical_market_cap` 的本地数字待重拉后复核。

---

## 9. Phase 3 状态（2026-09-26）

> **当前状态**：北极星 `docs/design/prosperity-engine-north-star.md` 五层、建设路径、依赖图、需求覆盖矩阵、Pre-mortem 均已写完；Codex 审批的 11 条意见已全部处理并记入北极星「反向挑战记录」。**2026-09-26 Boss 定稿**；下一步按建设路径从阶段 0（M0 磁盘清理）开始，每个模块单独走 dev-loop plan。

**已定**
- 架构师角色：买方量化投研基础设施架构师（点时数据正确性 > 统计显著性 > 功能丰富）。
- 方案 A：独立引擎 `finance_prosperity` + 派生库 `prosperity.db`，云端周六 16:30 / 20:30；本地 `prosperity_research.db` 跑实验方案，与生产共用纯函数内核。否决方案 B（嵌进 Premium）、方案 C（本地计算）。
- 各层细节、关键决策与 Codex 审批回应以北极星为准，本节不再重复。

**开工前置 / 待核实**
- 本地 `market.db` 副本损坏（`historical_market_cap`），坏副本已克隆为 `data/market.db.corrupt-20260926`。等云端写入任务结束后重拉，跑 `PRAGMA quick_check`，并复核 2021-09 起点、财报季覆盖率（北极星 P3）。
- 云端磁盘 97%（剩约 1.6G）：清理方案已定为北极星 P0（周备份留 2 份、手动快照清空、脚本加上限），执行时删前逐份列清单给 Boss 确认。
- 云端覆盖率、磁盘和耗时数字未在审批后重新实测，由 P3 复核。
- 云端 Python 3.10.12 + pandas 2.3.3，新代码不得依赖 pandas 3。
