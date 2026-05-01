# 017: Deep Analysis OPRMS 仓位入库使用原始值而非 Alpha Debate 最终值

**日期**: 2026-04-30
**严重程度**: 中（dashboard/OPRMS current rating 可能高估建议仓位）
**根因**: `compile_deep_report()` 提取 `oprms_position_pct` 时抓取 OPRMS 原始公式仓位，未用 `debate_conviction_modifier` 重算最终仓位

---

## 发生了什么

跑 NXPI deep-analysis 时，报告里的 OPRMS 写出了三层口径：

- 原始仓位：`DNA 15% × Timing 0.45 = 6.8%`
- Alpha Layer 调整：`6.8% × 0.75 = 5.1%`
- Alpha Debate 最终调整：`6.8% × 0.8 = 5.4%`

但 `compile_deep_report()` 自动写入 `company.db` 后，`oprms_ratings.position_pct`
仍是 `6.8`，虽然 `conviction_modifier` 已正确变成 `0.8`。Dashboard 读取
`position_pct` 展示时会把原始仓位当最终仓位。

本次 NXPI 已手动校正 current OPRMS 为 `position_pct=5.4`，并重新生成 dashboard。

## 根因分析

`terminal/deep_pipeline.py` 的结构化提取逻辑分两步：

1. 从 `oprms.md` 里匹配 `最终仓位`，写入 `oprms_position_pct`
2. 从 `alpha_debate.md` 里匹配 `debate_conviction_modifier`，覆盖 `conviction_modifier`

第二步只覆盖信念系数，不会同步重算 `oprms_position_pct`。因此 DB 中同时存在：

- `position_pct = 原始 OPRMS 仓位`
- `conviction_modifier = Alpha Debate 最终系数`

这对数据结构本身不是矛盾，但对 dashboard/commands 这类只读 `position_pct`
的消费者是错的。

## 修复方向

后续修代码时应明确一个口径：

1. `oprms_position_pct_raw` 保存原始 `DNA × Timing`
2. `position_pct` 保存最终可执行仓位，即 `raw × final conviction_modifier`
3. dashboard 和 `/options` 等消费者只展示最终口径，必要时附带 raw/conviction 拆分

最小修复是在 `extract_structured_data()` 或 `compile_deep_report()` 保存前：

```python
if data.get("oprms_position_pct") is not None and data.get("conviction_modifier") is not None:
    data["oprms_position_pct"] = round(data["oprms_position_pct"] * data["conviction_modifier"], 1)
```

但更好的修复是新增 raw 字段，避免丢失原始 OPRMS 推导。

## 教训

- Deep Analysis 里“最终仓位”这个词出现多次，正则只抓第一个会误导入库。
- 有 `conviction_modifier` 字段不等于消费者会自动理解它；dashboard 展示字段必须直接是最终口径。
- 跑完 deep-analysis 后要抽查 `company.db` current OPRMS，不只看报告文件。
