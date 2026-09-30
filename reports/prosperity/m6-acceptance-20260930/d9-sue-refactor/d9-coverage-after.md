# 景气引擎 D9 覆盖率报告

生成 2026-09-30T02:03:45Z · 代码 b2d06f5330f380baf3c81fb954f4a0e3b5f223bb · run_id d9-full-20260928

street EPS 数值门槛：**待 Boss 定**（本报告只给数字）。

输入模式：observed_snapshot；归档观测日：2026-09-29。

三表覆盖率门槛：**90%**。

## 逐截面

| 季末 | 成员 | 三表合格 | 门槛 | EPS 深度≥11 | 深度≥13 | SUE 可算 | ΔSUE 可算 | 三表到达 第60天 | 第80天 | 财报季就绪首次≥95% |
|---|---|---|---|---|---|---|---|---|---|---|
| 2026-09-29 | 915 | 95.3% | PASS | 89.0% | 87.2% | 87.7% | 86.9% | — | — | 未达 |

## 原始计数与质量后可用性

拆股检查为启发式：未发现疑似不代表已证实口径正确；缺元数据单列未知。

| 截止日 | 原始齐全率（旧日期规则） | 当前模式三表可用率 | 三表与SUE均可用 |
|---|---|---|---|
| 2026-09-29 | 95.3% | 95.3% | 791/915 |

## 缺口原因（逐季末计数）

`short_history`（有上市证据的历史不足）与 `sue_degenerate`（EPS 序列本身使 σ 无定义）属于结构性缺失；质量冲突/日期未知应先核实，不能直接通过重复补数解决。

| 季末 | 三表缺口 | street EPS 缺口 | 映射来源 |
|---|---|---|---|
| 2026-09-29 | gap_in_series 32, history_depth_unknown 4, not_attempted 1, short_history 6 | eps_conflicting_quarter 59, eps_split_basis_suspect 5, missing_current_quarter 4, missing_quarters 14, no_earnings_rows 2, short_history 7, statements_history_depth_unknown 3, statements_not_attempted 10, sue_degenerate 4, unmapped 5 | estimates_window 59881, none 11873, statement_window 2109 |

## 待补名单

- 三表可补：37 只
- street EPS 可补：38 只
- EPS需核实（不进入自动补数清单）：64 只
- 同财季重复：349 只
- 拆股口径可疑：12 条（拆股数据只覆盖 385 / 915 只）

逐只明细见同名 JSON。

## 口径

- three_table_gate: has_asof_window (8 contiguous quarters, all three tables, known by validated public date (historical) or archived observation (current)) >= 90% of members
- street_eps_depth: consecutive mapped quarters announced by qe (fiscal dates <=20d apart are one quarter) reaching the newest quarter all three statements had by qe: depth >= 11, full 13
- sue: north-star SUE actually computable at the EPS quarter aligned with the three statements' current quarter: date-paired YoY numerator, sigma = standard deviation of the 8 previous YoY changes, >= 6 obs, finite and != 0; dSUE also at the quarter before
- short_history: only with a profile ipoDate later than the window start (and not contradicted by earlier market caps); otherwise vendor_short / history_depth_unknown (fixable)
- quality: conflicting EPS and mixed split-basis dependencies are unavailable; split checks are retrospective diagnostics, not proof of clean values or strict PIT
- freeze_season: fiscal quarter ending in (prev_qe+7d, qe+7d]; arrival = the day the last of its three statements was known (validated accepted_date, fallback filing_date; placeholders unknown) minus qe; not evaluated for current observation mode
