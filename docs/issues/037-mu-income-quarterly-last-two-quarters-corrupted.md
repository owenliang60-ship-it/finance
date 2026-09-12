# Issue 037: MU FY2026 Q2/Q3 极端净利润疑似污染

**Status**: RESOLVED — FALSE ALARM
**Date**: 2026-07-14
**Severity**: NONE — 经 SEC/公司材料核实为真实超级周期数字
**Related**: `income_quarterly` · SOXX historical TTM PE

## 原始怀疑

MU FY2026 Q2/Q3 的季度净利润约 $28B，显著高于历史常态，初看类似数量级污染。

## 核实结果

该数字已经 SEC filing 与公司材料交叉核实，属于真实的内存超级周期财务结果，不是单位、拆股或重复累计错误。

## 处理

- 不添加 MU 特例、不裁剪、不 winsorize、不剔除。
- 历史篮子估值继续按 GAAP `net_income` 原值聚合。
- 数据异常检测必须结合 primary-source evidence，不能只因数值极端自动判坏。
