# FMP Forward EPS 估值项目 — Brainstorm WIP

> **状态**: 🟡 BRAINSTORM 未完成 — 下个 session 从「下一步」继续
> **发起**: 2026-07-09 | **阶段**: 需求澄清已完成 §1 数据层设计已过，§2 指标层未展开
> **背景研究**: `docs/research/2026-07-02-forward-eps-quarterly-data-sources.md`（30+ 源深度调研）
> **skill**: superpowers:brainstorming（尚未到 writing-plans 阶段）

---

## 一句话立项

升级 FMP 订阅拿到**季度颗粒度** forward EPS consensus，配合价格计算个股（扩展池 ~949）和指数/篮子（SOX 起步）的 **forward P/E**，周频快照 append-only 自建 PIT 库，最终替代现有 yfinance forward estimates 线。

---

## 已确认决策（Boss 拍板，2026-07-09）

| # | 决策项 | 结论 |
|---|--------|------|
| 1 | **核心用途** | 三个：① 当前估值快照（查个股/指数 forward P/E）② 时间序列追踪（估值压缩/扩张 + 预期修正，自建 PIT 起点）③ 晨报/现有管线集成 |
| 2 | **指数/篮子范围** | SOX 费城半导体（~30 成分）起步 + 自定义篮子（**篮子暂未定，未来接 concept registry L3；但 concept registry 要先重构，往后放**） |
| 3 | **个股范围** | 扩展池 ~949 只（FMP $10B+，自动含核心池 + SOX 成分） |
| 4 | **EPS 口径** | 季度序列本身（+1q…+4q/+9q）+ 派生多种展示：NTM、逐季、FY1/FY2。要求"提供多种分析展示方法" |
| 5 | **更新频率** | 周频快照（随现有周六 forward cron 一起跑） |
| 6 | **新 FMP key** | Boss 已提供升级 plan 的新 key → **替换全局 `FMP_API_KEY`**（本地 + 云端），现有全部 FMP 调用自动受益于更高限额，无需改代码。<br>⚠️ key 明文**不入 repo**；已暂存本地 `.env` 的 `FMP_UPGRADED_API_KEY`（gitignored），接入时才覆盖 `FMP_API_KEY` |
| 7 | **yfinance forward 线去留** | **FMP 替代 yfinance**——但先对拍 4 周验证质量，再下线 yfinance 那条线（0q/+1q/0y/+1y，Refinitiv 口径） |
| 8 | **架构方案** | **方案 B：新表 + 独立派生指标层**（下详） |
| 9 | **snapshot 存储粒度**（2026-07-09 二次 session 拍板） | **方案 A+**：周频 cron 只存未来行（`fiscal_date >= snapshot_date`，~15 行/股/周）；首次接入时做**一次性**历史 backfill（`snapshot_date = backfill 日`），范围 **2021-01-01 起**，之后零增量。schema 不变，`snapshot_date` 天然区分 backfill 批与周频批 |
| 10 | **NTM 财季对齐规则**（§2 决策点①） | **Rule A 日历严格**：取 `fiscal_date >= as_of` 最近 4 季求和。锯齿 artifact（财报前 2-6 周提前滚动）文档标注；对拍期偏差大再升级 Rule B（未报告严格，依赖 income 表） |
| 11 | **逐季 YoY 分母口径**（§2 决策点②） | **(a) FMP estimates 历史行**（A+ backfill 2021+ 覆盖）+ 前置验证步：抽 AAPL/MU/ONTO 对比 FMP 历史行 vs street actual，不收敛则 fallback (b) earnings-surprises 端点。绝不用 income GAAP 当分母（issue036） |
| 12 | **指数 forward P/E 落库 + 篮子扩为 6 个** | Boss 新增需求：**SPY / QQQ / SOX / MAGS / IGV / XLF** 六篮子指数级 forward P/E **周频落库**（个股仍 compute-on-read）。落库合理性：成分清单周频漂移，不落库则历史指数 P/E 无法 PIT 重建 |
| 13 | **P/E 一共算两种**（Boss 拍板 2026-07-09） | ① **PE_blend** = 过去 3 个**已报告**财季街道 actual + 未来 1 季预测；② **PE_ntm** = 未来 4 季预测。过去季 actual **必须用街道口径**（FMP `stable/earnings` 的 `epsActual`，已实测可用，未报告季为 null 天然当分界信号），**绝不用 income GAAP**（issue036）。街道 actual 到手后决策 11 的 YoY 分母直接用它（原 (a)+验证步方案作废，升级为确定方案） |

---

## FMP 季度 estimates 实测结论（2026-07-09，用新 key 已验证 — 下个 session 免重打）

端点：`GET https://financialmodelingprep.com/stable/analyst-estimates?symbol=X&period={quarter|annual}`

- **`period=quarter` 可用**（Starter 上被 "Premium Query Parameter" 挡，新 plan 解锁）
- 每条记录 = 一个财季，字段：
  - `date`（财季截止日，如 `2028-09-28`）
  - `epsAvg / epsHigh / epsLow`
  - `revenueAvg/High/Low`、`netIncomeAvg/High/Low`、`ebitdaAvg/High/Low`、`ebitLow/High/Avg`、`sgaExpense*`
  - `numAnalystsEps`、`numAnalystsRevenue`
- **深度**：`limit=100` 时 AAPL 返回 100 条，覆盖 **2001-12 → 2028-09**（过去 + 未来 **约 +9~10 季**）
- **小票覆盖**：ONTO（SOX 成分小票）也有未来 +8 季，但 `numAnalystsEps` 仅 2–6 人（远期尤薄）
- **annual 深度**：AAPL 到 FY2030（FY1..FY5），分析师数远期递减（FY2030 仅 8 人 vs FY2026 31 人）
- **已知质量折扣（issue036 同类风险）**：FMP consensus 样本面明显偏薄（AAPL EPS 分析师 9–13 vs Yahoo/IBES ~40）；口径为"街道口径"non-GAAP，接入前必须与 yfinance（Refinitiv 口径）+ income 表（FMP GAAP）三方对拍确认基准，避免 GLW core≈GAAP×1.35 式失真

---

## 架构方案 B（已选定）

### 数据层
- **FMP client 扩展**：新方法 `fetch_analyst_estimates(symbol, period)`，打 `stable/analyst-estimates`
- **新表 `fmp_estimates`**（落 market.db，云端独占写）：
  ```
  PK = (symbol, snapshot_date, fiscal_date, period_type)
    snapshot_date  抓取日（周频快照锚 / append-only 的关键 / PIT 库基础）
    fiscal_date    财季或财年截止日
    period_type    'Q' | 'FY'
  字段: eps_avg/high/low, rev_avg/high/low, net_income_avg, ebitda_avg,
        num_analysts_eps, num_analysts_rev
  ```
- **append-only 快照**：每周写一批带当日 `snapshot_date` 的行，历史快照永不覆盖 → 自然积累 PIT 库
- 现有 yfinance `forward_estimates` 表**完全不动**，对拍期两表并存
- **首次 backfill**：接入当天对扩展池 ~949 只各抓 quarter + annual，建第一个 snapshot
- **key 替换**：`FMP_API_KEY` 本地 + 云端换新 key

### 计算层（新 terminal 模块，从最新快照现算）
- **NTM EPS**：未来 4 财季滚动求和 → `forward P/E = 价格 ÷ NTM EPS`
- **逐季序列** +1q…+9q（支持 8q8q 图、逐季增速）
- **FY1 / FY2** 年度口径

### 聚合层（可插拔篮子）
- SOX 静态成分清单起步，未来接 concept registry L3
- **指数 forward P/E = Σ市值 ÷ Σ NTM 净利润**（用市值/净利润聚合绕开股本、divisor、成分权重问题）

### 三阶段推进
1. **①数据层 + cron**：client 方法 + `fmp_estimates` 表 + backfill + 周频 cron + key 替换
2. **②指标 + SOX + 查询工具**：NTM/逐季/FY 计算 + SOX 篮子聚合 + 查询 CLI
3. **③晨报集成 + 对拍**：接入晨报；对拍 4 周确认质量后下线 yfinance 线

---

## 待决项

- [x] ~~snapshot 存储粒度~~ → **已拍板方案 A+**（见决策 9，2026-07-09 二次 session）：周频只存未来行 + 一次性历史 backfill（2021-01-01 起）

---

## 下一步（续 brainstorm）

1. ~~敲定 snapshot 存储粒度~~ ✅ 方案 A+（决策 9）
2. 展开 **§2 计算/指标层设计**：NTM 滚动求和的财季对齐规则、逐季序列展示形态、forward P/E 的 stale-price 处理
3. 展开 **§3 聚合层设计**：SOX 成分清单来源（已有 SOXX holdings？还是硬编码）、Σ市值/Σ净利润聚合口径、缺失成分处理
4. 三个设计段逐一 Boss 确认 → 写正式 spec `docs/design/` 或 `docs/superpowers/specs/` → 自审 → Boss review → **invoke writing-plans skill 出实施 plan**

## 注意事项（下个 session 提醒项）

- **提问方式**：AskUserQuestion 弹框会遮挡正文，Boss 看不到设计细节 → 设计内容直接作为消息正文发出，只在真需要多选时用弹框
- **concept registry 重构是前置依赖**（自定义篮子接 L3 前）——Boss 已明确要重构，但排在此项目之后
- **key 安全**：接入时才把新 key 写进 `FMP_API_KEY`（本地 + 云端 .env）；文档/repo 绝不落明文（参照 2026-06-10 隐私敞口教训）
- **口径对拍是第一质量门**：接入前 FMP quarter vs yfinance +1q vs income GAAP 三方抽样对拍
