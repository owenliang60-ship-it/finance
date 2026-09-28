# Deep Analysis 接入五年股价 + 季度业绩 QoQ + 未来四季共识图

日期：2026-09-28 · 北极星层级：分析层（Terminal 深度分析管线的确定性数据准备 + 报告汇编）
来源：`docs/handoffs/2026-09-28-company-financial-charts/README.md`（主仓未跟踪目录）第 10 节
模式：Boss 授权 autopilot；不 merge / push / 部署。

## 范围

1. 新模块 `terminal/financial_history.py`：只读 market.db（`mode=ro`）+ 必要时实时 FMP 补缺（原始响应落 `research_dir/chart_sources/`），
   产出 `financial_history.{json,csv,md}`、`financial_history_manifest.json`，入口
   `prepare_financial_history(symbol, research_dir, as_of=None, years=5, forecast_quarters=4, ...)`。
2. 新模块 `terminal/financial_history_chart.py`：只从冻结 JSON 画 `price_fundamentals_5y_4q.png`（三栏：日线+季度价格收益 / 营收 QoQ+金额 / 净利润 QoQ+金额），布局沿用 AMD 原型，全局开关改显式参数。
3. 接入：
   - `analyze_ticker`：Phase 0 生成（在 profiler prompt 之前），摘要追加进 `data_context.md`，返回 `financial_history` 状态；失败显式为 blocked，不静默跳过。
   - lens / synthesis prompt：引用冻结数据，禁止重算另一套 QoQ，要求区分 actual/forecast、基数效应、一次性项。
   - `compile_deep_report`：MD 新章节（相对路径图片 + 口径警示 + 季度表）；只编入 Phase 0 已冻结的数据，不重新取数。
   - `html_report`：新 section + TOC，PNG base64 内嵌；PDF 中图表独占横向页。
   - `deep-analysis` SKILL：Phase 0 简报图表状态、research-earnings 补"数据核对"、5d 交付 PNG 路径。`auto_deep_analyze.sh` 复用同两个入口自动覆盖。

## 关键规则（对应 handoff §4–§5、§9）

- 财季身份用 `fiscal_year/period`；预测季先剔除与已披露季期末相差 ≤10 天者，再取紧接最新实际的连续 4 季（60–120 天间隔），同一快照；不足则 partial。
- 快照：as_of 前最新 `status=complete` 的 weekly 批次且本公司有 Q 行；否则实时 FMP（整组取、标检索时点）；快照早于最新财报 → 财报前共识警示。
- 净利润：历史 GAAP（FMP net_income），预测 basis=unknown；跨口径首季 n/a；两负=亏损收窄/扩大（不画柱），跨盈亏=扭亏/转亏；零/缺/缺季 = n/a+原因；原始值保留 null，显示截断 ±200%。
- 银行/券商：营收用 `fmp_earnings.revenue_actual`（与共识同口径的净营收），不机械减利息支出；非银行做两表营收交叉校验。
- 股价收益 = 上季末收盘 → 本季末收盘（不含股息）；首季/IPO=partial，当季=QTD；单日 ±45% 以上疑似未复权拆股 → 警示。
- 显示季 = 期末所在自然季度；仅当期末落在季度首 7 天且整体序列因此更连续时才回拨一季（52/53 周），逐行记录。
- 不写 market.db；不做 Q4 = 全年 − 9M 差分（无同版本证据）。

## 验收

- 单测：手算期望（100→120=+20%；-100→-60=亏损收窄40%；-60→20=扭亏；GAAP100→unknown150=n/a；6/29 实际 vs 6/30 预测=同季跳过；只有 2 季预测=partial；IPO 不足五年；银行净营收；缺季）。
- 真实数据：AMD/CRDO/BAC/MRVL 生成 PNG，独立脚本（纯 sqlite + 手工算术，不 import 生产计算函数）对拍 CSV。
- 用 fixture 研究目录跑 `compile_deep_report` 路径的 MD/HTML/PDF，打开 PNG 与 PDF 页面截图核对。
