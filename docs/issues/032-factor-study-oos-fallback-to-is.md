# Issue 032: Factor-Study OOS 缺失静默退回 IS —— "看着 OOS 验过、其实没有"

**Status**: **计划阶段 review 捕获（未进生产）**。Plan `docs/plans/2026-06-02-momentum-factor-study-plan.md` 三处已全部改为 OOS-only / fail-closed
**Date**: 2026-06-02（动量因子 plan 第 2–3 轮 review 发现）
**Severity**: P1 静默 fail-open —— 让未经 OOS 验证的因子冒充"已验证"，污染研究结论（但 fails-open，不污染数据）
**Related**: `backtest/factor_study/runner.py:256`（结果层 `has_oos`）· `ic_analysis.py:122`（单 horizon <5 日返回 None）· winner_selection · daily_sensitivity · issue 030/031（同类 fails-open 安全网失效）

## 触发

动量因子赛马 plan 里反复出现一个习惯写法：`oos_结果 or is_结果`。审查时发现它在 OOS 数据缺失时会**悄悄退回 IS**，并把结果标成"已验证"。

## 根因

OOS IC 的缺失有**两层**，且都是正常现象、不报错：

1. **结果层**：`runner.py:256` `has_oos = len(oos_dates) >= min_oos_dates(50)`。OOS 日数不够 → `oos_ic_results = None`，整组无 OOS。
2. **单 horizon 层**：即便 `has_oos=True`，`ic_analysis.py:122` 在某 horizon 的 OOS 共同日 `<5` 时对**该 horizon** 返回 `None`。最现实的触发是 **30d horizon**：长 horizon 的 OOS forward-return 在窗口末端截断，可用日数掉到阈值下 → 该 horizon 的 OOS IC 缺失。

此时 `oos.get(h) or is.get(h)` / `res.oos_ic_results or res.ic_results` 会取到 **IS** 值，但下游把它当 OOS 用。研究契约是"OOS IC_IR 优先"，这就破了。

## 三处实例（同一反模式）

| # | 位置 | 旧写法 | 后果 |
|---|------|--------|------|
| 1 | winner selection 组装 `oos_ic_ir` | `oos_ir.get(h, is_ir.get(h, 0.0))` | 无 OOS 的候选用 IS IC 参与选胜 |
| 2 | winner selection tie-break `spread` | `res.oos_ic_results or res.ic_results or []` | IS 价差在 tie-break 里冒充 OOS |
| 3 | daily sensitivity 读 `daily_ic` | `res.oos_ic_results or res.ic_results or []` | 无 daily OOS 时退回 IS，并把 `found_daily_ic=True`、`consistent=True` |

## 修复（plan 内，全部 OOS-only + fail-closed）

- `CandidateRecord` 加 `has_oos_ic` / `oos_n_obs`；`oos_ic_ir` 改 `Optional`，缺失=`None`，**绝不退回 IS**。
- `pick_winner_for_horizon` 要求 `has_oos_ic and oos_n_obs >= MIN_OOS_OBS(20)` 才入选；`spread` 也取 OOS-only。
- `run_daily_sensitivity` 遍历改 `res.oos_ic_results or []`；无 OOS → `daily_ic=None` → `found_daily_ic=False` → `consistent=False`。
- `MIN_OOS_OBS=20` 是 per-horizon 兜底，叠加在框架结果层 `min_oos_dates=50` 之上。
- Task 12.2 加审计输出：列出缺 OOS 的 candidate-horizon。

## 教训

1. **`oos_x or is_x` 在因子研究里是反模式**：OOS 缺失必须 fail-closed（None / 排除），绝不能用 IS 顶替。"看着验证过"比"没验证"更危险——它会进结论。
2. OOS 缺失有结果层 + 单 horizon 两层，长 horizon（30d）因 forward-return 截断最易触发，且**不报错**。
3. 与 issue 030/031 同源：fails-open 的安全网（coverage sidecar / OOS gate）失效都不报错，必须**主动可见**（删行测试 / 缺 OOS 审计输出），不能假设"有 OOS 字段 = 真的在 OOS 验过"。
4. 同一反模式在一份 plan 里出现 3 次——发现一处后要全局 grep 同款（`or .*ic_results` / `.get(.*, is_`），一次清干净。
