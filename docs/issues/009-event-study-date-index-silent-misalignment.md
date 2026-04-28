# 009: 事件研究里日期归一化和 Pandas 对齐会静默污染 cohort

**日期**: 2026-04-22
**严重度**: HIGH（不会报错，但会直接污染上下文分类和 benchmark 对齐）
**恢复时间**: ~20 分钟定位 + 修复 + 补测试

## 发生了什么

在 `PMARP + BBWP` 日频研究里，遇到了两个不会抛异常、但会悄悄把结果带偏的问题：

1. `prior_excess_20d` 作为 `Series` 赋值进 `DataFrame` 时，`Series` 的 index 是日期，`DataFrame` 是 `RangeIndex`，Pandas 按 label 对齐后整列几乎全变成 `NaN`
2. benchmark `SPY` 的日期有时是 `YYYY-MM-DD 00:00:00`，个股日期是 `YYYY-MM-DD`，字符串不归一时会导致日期过滤和对齐静默失配

## 根因

两个根因都属于“Pandas 太聪明”：

1. 赋值时默认按 index label 对齐，而不是按行位置写入
2. 日期字段看起来像同一天，但字符串层面并不相等

这类问题最危险的地方是：结果仍然能跑完，甚至还能产出看起来合理的 CSV。

## 修复

这次采用了两个硬规则：

1. 需要按行写入的列统一用 `.to_numpy()`
2. 参与 join / filter / map 的日期统一先做 `astype(str).str[:10]`

## 教训

- 事件研究里“没报错”不代表没前视、没错配
- 跨 symbol / benchmark 对齐时，日期字段必须先归一化，再做任何映射
- 往 `DataFrame` 填时间序列特征时，不要默认相信 Pandas 的自动对齐语义
