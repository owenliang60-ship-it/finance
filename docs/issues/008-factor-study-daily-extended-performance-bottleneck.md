# 008: factor_study 在日频 extended universe 上性能瓶颈严重

**日期**: 2026-04-12
**严重度**: MEDIUM（研究可做，但单轮日频 run 接近不可交互）
**恢复时间**: ~10 分钟后人工中断，切换到 ad-hoc 研究脚本

## 发生了什么

在 `extended` universe（实际载入 529 只）上尝试用 `FactorStudyRunner` 跑 `PMARP cross_up_2.0` 的日频长窗研究：

- `IS`: `2021-07-01` ~ `2023-12-31`
- `OOS`: `2024-01-01` ~ `2026-04-10`

单阈值 run 在进入 `开始因子研究: PMARP` 后长时间无中间结果，累计运行约 `10 分钟` 仍未完成，交互效率接近不可用。

## 根因

`factor_study` 当前实现对这种场景的复杂度太高：

1. `runner.py` 会对每个 `computation_date` 调用 `slice_to_date()`
2. `PMARPFactor.compute()` 对每个 symbol 再调用 `analyze_pmarp(df)`
3. `analyze_pmarp()` 每次都会重新计算整段 `EMA + PMARP`

也就是说，`529 symbols × 数百个 computation dates × 每次重算完整指标`，导致大量重复工作。  
这在周频还能忍，但切到日频 extended universe 后就会放大成明显瓶颈。

## 临时修复

这次没有改框架，而是切到一条更轻的 ad-hoc 研究口径：

1. 一次性加载 529 只股票
2. 每只股票只计算一次完整 `PMARP / RVOL` 序列
3. 再在内存里做事件筛选、T+1 开盘收益、日期聚类和 t-test

这样同一类实验可以在约 `1 分钟` 内拿到结果，足够支持策略迭代。

## 教训

- `factor_study` 适合周频/中等 universe，不适合直接硬跑日频 extended
- 日频多股票研究要优先走 `panel/batch` 思路，避免按日期反复重算整段指标
- 当研究目标是“快速比较 3-5 个条件版本”时，轻量 ad-hoc 脚本往往比完整框架更合适
