# 011: FMP `company-screener` 会把已退市股票继续标成 active，污染 Dollar Volume 排名

**日期**: 2026-04-23
**严重度**: MEDIUM（晨报 Top 10 流动性排名被错误 symbol 污染，但不影响 market.db 主行情库）
**恢复时间**: ~20 分钟定位 + collector 过滤修复

## 发生了什么

Boss 发现云端晨报的 Dollar Volume Top 10 连续多天出现 `HOLX`，但该股票近期已经退市，不应该继续出现在“今日成交额前十”里。

排查云端后确认：

- `data/dollar_volume.db` 在 `2026-04-23` 当天重新采集时，就把 `HOLX` 写进了第 10 名
- 同一条记录从 `2026-04-08` 后几乎每天都在 Top 15 里重复出现
- FMP `delisted-companies` 已明确返回 `HOLX delistedDate=2026-04-08`
- 但 FMP `company-screener` 同时仍返回：
  - `isActivelyTrading=true`
  - `price=76.01`
  - `volume=101,956,189`

也就是说，screener 快照里残留了一条“冻结但仍被标成 active”的脏数据。

## 根因

`scripts/collect_dollar_volume.py` 之前完全信任 FMP `company-screener`：

1. 拉分页 screener
2. 按 `price * volume` 计算 dollar volume
3. 直接排序写入 `daily_rankings`

问题在于：**`isActivelyTrading=true` 不是可靠的退市过滤条件**。当上游 screener 残留 stale snapshot 时，我们没有第二道校验，所以退市股会被当成当天高成交额股票反复写入。

## 修复

在 Dollar Volume collector 增加“近期退市名单过滤”：

1. 每次日采前额外请求 FMP `delisted-companies`
2. 拉取近期窗口内（默认 120 天）的退市 symbol
3. 从 screener 结果中剔除这些 symbol
4. 再计算 Top 200 排名

这样即使 screener 仍错误标记 `isActivelyTrading=true`，collector 也不会把它写进当天榜单。

## 教训

- **不要单点信任第三方 active flag**：上游 API 的“活跃交易”字段可能滞后，尤其是并购退市、私有化、摘牌边界场景
- **快照类榜单必须有第二道真实性过滤**：凡是“today ranking”类型统计，都要防 stale row 被重复当成今天数据
- **发现异常 symbol 时先做双端交叉验证**：同一 provider 的 screener / delisted / historical endpoints 可能互相矛盾，必须交叉看
