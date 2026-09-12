# Issue 046: 市值 sanity 对渐进漂移与首行污染仍有盲区

**Status**: OPEN — 一次性 SOXX 研究已记录边界；若改为 recurring pipeline，必须升级
**Date**: 2026-07-14
**Severity**: MEDIUM — 当前实测数据未命中，但新上市成员或缓慢供应商漂移可穿透
**Related**: `terminal/historical_market_cap_sanity.py` · issue035

## 现象

当前状态机以 `abs(daily mcap return) > 40%` 或 split 相邻日作为候选触发，并把最后一个 accepted implied-shares regime 作为下一行锚点。因此仍有两个不可由单日规则无歧义解决的边界：

1. 市值每天漂移 30%、价格不变时，每一步都低于 jump threshold，accepted 后逐日重新锚定，数日内可累计成倍偏差；
2. 每个 symbol 的首行没有前序参照，只能无条件作为 bootstrap anchor。若首行已污染，首日可能进入估值，之后正确数据反而会暂时被判断为异常。

## 本轮已关闭的相邻漏洞

若 split 当天 price 与 market cap 同步除以拆股比例，旧逻辑会误判为 `already_back_adjusted`。现已新增经济连续性约束：市值收益、split-adjusted price 收益及两者差值都必须在门槛内；否则切换到 post-split expected-shares regime，并持续 quarantine，直到市值恢复。

## 当前证据与边界

- KLAC/MCHP 的真实大幅污染窗口能被现有状态机识别并强制重拉；
- NVDA/AVGO/LRCX 的已知拆股日经济连续，不会被新约束误伤；
- 老成员在研究起点前通常有历史缓冲，首行风险主要集中在 IPO/新纳入成员；
- 本问题不代表 2026-07-14 dry-run 数值已污染，而是检测边界仍不完备。

## Recurring pipeline 关闭条件

- 用多行 bootstrap 或可信 shares-outstanding/source manifest 验证首个 regime；
- 增加 rolling-anchor / cumulative implied-shares drift 检查，并用真实增发、回购、并购样本校准误报；
- verifier 对上述规则具备独立输入或冻结 manifest；
- 新增渐进漂移、首行污染、真实增发/回购不误伤的回归测试。
