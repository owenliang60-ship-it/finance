---
name: valuation
description: "估值情景工作流：用只读 market.db 与手写台账，为单只股票生成事实反推矩阵加情景滑块的交互 HTML，带三态门控与熔断。Use when the user runs /valuation SYMBOL or asks 估值、反推、情景估值、现价隐含了什么. Not for 多 agent 深度研究 (deep-analysis) or 期权策略 (options)."
invocation: user
arguments:
  - name: symbol
    required: true
---

# /valuation — 估值情景工作流

本 skill 为一只股票生成一个单文件 HTML。首屏是事实反推矩阵。滑块初始位停在卖方共识。初始情景值等于现价。页面不输出模型结论。

入口：`scripts/valuation.py`。仓库内正本：`docs/skills/valuation/SKILL.md`。改动先改仓库，再复制到 `~/.claude/skills/valuation/`。

## 固定流程（7 步，每步一个动作）

1. 进入 `/Users/owen/CC workspace/Finance`，执行 `./sync_to_cloud.sh --pull`。
2. 检查 `terminal/valuation/ledgers/<SYMBOL>/ledger.toml` 是否存在。不存在则新建（字段见下表，样例见 `tests/fixtures/valuation/ACME/ledger.toml`）。只有当一级收入轴有多种合理划分时才问 Boss（D1）；否则不问。
3. 抓取一手披露原文，存为同目录 `sources/<id>.txt`，计算 sha256，写入 `[[sources]]`。每条事实写逐字引文 `quote`；每条指引另写三个语义字段 `period_type`、`denominator`、`is_lower_bound`。
4. 运行 `.venv/bin/python scripts/valuation.py <SYMBOL> --ledger terminal/valuation/ledgers/<SYMBOL>/ledger.toml`。
5. 按退出码处理（stdout 一行 JSON，见下方退出码表）。
6. `g4_reextract` 非空时，换一个异构模型重新抽取这些条目，再比对。G3 阻断时向 Boss 说明股本跳变、拆股标签或分部重述；Boss 确认后写 `[[acks]]`，再回到第 4 步。
7. 回复只给一屏运行结论和 `file://` 链接。运行结论只含退出码、被剔除项、现价隐含 P/E 与历史分位等事实反推读数。

## 退出码

- **0**：全部门控 VERIFIED。直接交付。
- **10**：降级。交付，并在回复首行列出被剔除项（stdout `non_verified` 的 `id` 与 `detail`）。
- **20**：整页熔断。不交付结论。报告阻断原因（stdout `non_verified` 中的页级条目）。HTML 只含横幅和门控报告。
- **1**：硬失败（台账 TOML 或 schema 错误、数据库不存在、台账 `symbol` 与参数不符、payload 含非有限数）。不生成 HTML，原因在 stderr。修台账或环境后重跑。
- **2**：参数错误（argparse）。改命令后重跑。

## 台账字段表

合约正本：`terminal/valuation/ledger.py`。未知键在任何层级都报错。数值必须是有限数（拒绝 `nan`、`inf`）。日期必须是 `YYYY-MM-DD`。

| 位置 | 键 | 必填 | 规则 |
|---|---|---|---|
| 顶层 | `schema` | 是 | 固定为 1 |
| 顶层 | `symbol` | 是 | 等于命令参数 SYMBOL |
| 顶层 | `reporting_currency` | 是 | 等于 `income_quarterly.reported_currency`，否则 C1 整页阻断 |
| 顶层 | `eps_basis` | 是 | `non_gaap` / `ifrs` / `gaap`；`non_gaap` 要求 USD |
| 顶层 | `cyclical` | 否 | 布尔，默认 false；true 时矩阵加周期股双口径行 |
| 顶层 | `t1_domains` | 否 | 一手披露域名白名单，另含全局 `sec.gov` |
| `[assumptions]` | `incremental_margin`、`note` | 否 | Boss 假设，只作利润率滑块刻度 |
| `[fx]` | `local_per_usd`（必填）、`as_of` | 否 | 仅非 USD；只作汇率滑块刻度，不参与初始位 |
| `[[sources]]` | `id`、`url`、`fetched_at`、`raw`、`sha256` | 是 | `raw` 相对台账目录；`sha256` 为原文文件哈希 |
| `[segments]` | `unit`、`names` | 是 | `unit` 为正数 |
| `[[segments.quarters]]` | `fiscal_date`、`values`、`source`、`quote`；可选 `prior_year_restated` | 是 | `values` 与 `names` 等长；G1a 校验合计 vs 营收 ≤1% |
| `[[eps_facts]]` | `fiscal_date`、`value`、`source`、`quote` | 是 | G1c 校验 vs 街口 EPS ≤1%；非 USD 报告者恒为 UNVERIFIED |
| `[[drivers]]` | `id`、`label`、`base`、`min`、`max`；可选 `basis_note` | 是 | `min <= base <= max`；纯收入增量假设，初始增量为 0 |
| `[[guidance]]` | `id`、`label`、`fiscal_date`、`value`、`period_type`、`denominator`、`is_lower_bound`、`source`、`quote` | 是 | `run_rate` 或下限只作文字标注，不进算术 |
| `[[cycles]]` | `id`、`label`、`start`、`end` | 是 | 窗口内季度须连续且街口 EPS 齐全，否则预设禁用 |
| `[[acks]]` | `key`；可选 `note`、`date` | 是 | 键格式 `G3a:<季末>`、`G3b:<fiscal>`、`G3c`、`G3d:<fiscal>:<分部名>` |

## 币种合约表

R = `income_quarterly.reported_currency`。

| 字段 | 来源 | R=USD | R≠USD（ADR） |
|---|---|---|---|
| 股价 | `daily_price.close` | USD/股 | USD/ADR |
| 街口 EPS 实际/预期 | `fmp_earnings.eps_*` | USD | USD/ADR |
| 街口营收实际/预期 | `fmp_earnings.revenue_*` | USD | USD |
| 营收、净利、摊薄 EPS、摊薄股本 | `income_quarterly` | USD | R；股本为 ADR 等价 |
| 一致预期 EPS/营收/净利 | `fmp_estimates` | USD | R |
| yfinance EPS | `forward_estimates.eps_*` | USD | USD/ADR |
| 汇率 | 不读 `fx_daily` | fx0 = 1，无汇率滑块 | fx0 = 下一季营收共识（R）÷ 同季美元营收预期 |

汇率校验：C2 跨供应商 EPS 隐含比对 fx0 偏差 ≤3%；C3 最新已公告季营收隐含比对 fx0 偏差 ≤10%（USD 报告者 ≤1%）。门控常量见 `terminal/valuation/config.py`，门控规则见 `terminal/valuation/gates.py`。

## 禁止事项

- 不写 `market.db`。引擎只经 `MarketStore(read_only=True)` 打开，连接为 `mode=ro` 加 `query_only`。
- 不读 `fx_daily`（已停更）。
- 不输出估值结论或头寸建议。
- 未通过门控的数字不得引用。门控报告里的非 VERIFIED 条目只能作为「被剔除项」提及。
- 不替 Boss 填情景权重。
