# 动量多空1号实盘 — 研究与复用证据

日期：2026-10-19（Asia/Singapore）。状态：独立交叉核对及文本复核已完成；架构待审批。范围：冻结策略落到真实执行，不重新优化参数。方案建议与事实分列。

## 1. 结论

建议采用Finance专用Python执行器，借鉴成熟框架的恢复和对账方式。成熟框架仍须适配本策略的组合调仓、成本翻倍止损、冷却及时钟；不能因支持Binance就判定可以直接上线。NautilusTrader作为正式备选。

最重要的工程差距不是评分，而是成交状态、交易所保护单与账户事实。主分支目前没有币安实盘执行链。冻结研究未模拟强平，所以不能从历史回撤推出实盘损失上限。

## 2. 本次核验事实

- 冻结版本：momentum-ls-01 / 2026-10-19.v1。
- 原工作区：`/Users/owen/CC workspace/Finance`；本设计worktree：`/Users/owen/.codex/worktrees/momentum-ls-01-live/Finance`。
- 对原工作区`reports/momentum-ls-01/2026-10-19/manifest.json`逐项复算SHA256：33/33匹配，规则文件哈希匹配。
- 发布包SHA256：`a9dfb0d44f3a63e3507e94b07309214f0f28e9a1574775337acdf1f4ac89c373`，与`releases/SHA256SUMS`相符。
- 历史`audit/checks.json`记载1499项PASS、零failures。这是已封存审计，本次没有重跑1499项或完整回测。
- 本次串行公开GET `/fapi/v1/time`及`/fapi/v1/exchangeInfo`均返回成功。样例ETHUSDT提供PRICE_FILTER、LOT_SIZE、MARKET_LOT_SIZE、MIN_NOTIONAL、PERCENT_PRICE及triggerProtect。此处证明公开元数据可读，不证明账户可交易或服务器生产连通。
- 未读取凭证、未访问签名账户接口、未修改交易账户或云端服务。

## 3. 可复用代码与陷阱

路径行号按本次主工作区读取定位。开发前须对分支再次核对。

|代码|可复用内容|边界|
|---|---|---|
|`scripts/crypto_trend_rankings.py:35,68,86`|V4评分与方向资格|先完整池评分，再按方向筛选|
|同文件`:133,184`|逐日成交额排名与14日入池|BTC占Top100名额后排除，不补第101名|
|`scripts/crypto_trend_metrics.py:56`|趋势指标|保持85收盘价/84区间|
|`scripts/compare_crypto_relative_momentum.py:20`|窗口检查|已收盘UTC窗口|
|`src/indicators/fisher.py:46`|递归Fisher内核|实盘另验应生效周是否完整，不能只merge_asof|
|`scripts/crypto_holding_policy.py:50,75,109`|周锚点、排名、入场资格|冻结副本一致，但方向读取缺过期周检查|
|`scripts/crypto_trend_market.py:192,252,336`|历史生命周期重建/当前池/行情读取|current_catalog不等于历史候选集合|
|冻结`source/stop_engine.py:9`|成交后均价纯函数|拟迁入正常源码并对拍；不从reports直接生产import|
|`src/telegram_bot.py`|私人通知通道|通知失败不改变成交状态；业务执行不依赖消息送达|

main `scripts/crypto_momentum_backtest.py:49`只有fisher/both/balanced。冻结shared版本的AsymmetricFisher仍需`source/run_study.py:10`的wrapper才得到空100%＋多50%；`stop100`还需`threshold=1.0/execution=ohlc`。只运行旧CLI或asymmetric会跑错策略。

冻结Fisher适配的`merge_asof`只检查方向非空，不能证明应更新周没有缺失。实盘需要显式`expected_week_close/effective_at`门，保留旧研究工件不改。

金额、数量与交易所过滤使用十进制精度；信号纯函数继续沿用经过对拍的数值口径，不能为统一类型顺手重写指标。

## 4. Binance协议事实

核验日期为2026-10-19。普通单与保护单分流；全仓位条件单不同时发送quantity/reduceOnly。保护单可以用CONTRACT_PRICE，价差保护会增加触发条件。[官方交易接口](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/trade)

条件单已迁入Algo服务；旧下单入口可能返回`-4120`。官方变更说明条件单暂不支持改单，因此保护价更新需要专门设计更替和竞态处理。[官方更新记录](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/change-log)

超时和部分503响应可能代表已接受但结果未知，应先查订单或用户流。429继续施压可能触发418。官方demo与生产使用不同端点；测试成功不能替代生产能力验收。[官方General Info](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/general-info)

用户流有生命周期和重连要求；账户事件只推送变化的持仓，不能把未出现的币归零。条件状态、普通委托和成交需关联核对。[官方用户数据流](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/user-data-streams)

官方网页有版本混杂：旧事件独立URL可能跳转主页，综合页仍列已退役事件；必须用变更日志、实际接口和固定协议夹具交叉核对。不能以HTTP 200或菜单存在当作语义通过。具体生产请求协议在适配器验收时固定。

## 5. 保证金研究

官方以标记价格计算强平相关状态；全仓仓位共享账户权益，维持保证金与档位有关。逐仓则使用单独分配的保证金。[强平机制](https://www.binance.com/en/support/faq/detail/360033525271)、[公式与档位](https://www.binance.com/en/support/faq/detail/b3c689c1f50a44cabb3a84e663b81d93)

**本项目推导，非账户实测：**2倍设置、满额1.5倍gross时，简化初始保证金约占净值75%，未计委托占用、费用与规则差异。逐仓仅放初始保证金的2倍空头，忽略维持保证金时价格上涨约50%已耗尽保证金，不能保证等到上涨100%的策略止损。全仓可共享缓冲，但多个空头同涨仍可能先强平。

Boss已选择专用子账户内全仓、2倍设置。验收必须覆盖单币上涨、空头共同上涨、多空同时不利、资金费扣减、退出未成交及周内gross漂移。读取真实档位和账户值后才可确认计算有效；场景是否存活单独报告，不能把已接受的全损场景自动变成缩仓门。不能偷偷增设仓位上限、降低目标或改变止损以通过验收。若冻结目标无法满足交易所当前保证金/下单要求，应报告失败并回到设计。

## 6. 三个参考系统

### Freqtrade

循环管理Trade/Order并保存数据库；启动查询已知未结订单。原生止损、失败紧急退出与成交后止损调整可借鉴。其stoploss涉及交易损失、费用和杠杆，不等于本项目的成本×2。适配组合目标、跨周成本和冷却需要额外工作。

来源：[循环](https://www.freqtrade.io/en/stable/bot-basics/)、[止损](https://www.freqtrade.io/en/stable/stoploss/)、[成交后调整](https://www.freqtrade.io/en/stable/strategy-callbacks/#adjust-stoploss-after-position-adjustments)、[固定提交的启动恢复](https://github.com/freqtrade/freqtrade/blob/1f394eaebc2f46a83d26971388628707802f8602/freqtrade/freqtradebot.py#L570)。

独立核对指出，该固定提交捕获InvalidOrderException后存在假定已撤分支。本项目只借鉴启动查询，不继承“查不到即已撤”的终态推断。

### Hummingbot

Controller产动作，Executor管理执行；Connector先跟踪订单再发请求。PositionExecutor现成止损在本地检查净盈亏后发送退出，不能代替进程失联仍生效的原生保护。订单跟踪恢复也不能代替策略周次与冷却恢复。

来源：[V2架构](https://hummingbot.org/strategies/v2-strategies/)、[订单生命周期](https://hummingbot.org/connectors/connectors/architecture/order_lifecycle/)、[固定提交PositionExecutor](https://github.com/hummingbot/hummingbot/blob/9af100d6822da7d2d0291a906c730ef172284ee2/hummingbot/strategy_v2/executors/position_executor/position_executor.py#L208)、[持久化](https://github.com/hummingbot/hummingbot/blob/9af100d6822da7d2d0291a906c730ef172284ee2/hummingbot/connector/markets_recorder.py#L330)。

### NautilusTrader

策略、风险、执行、适配器和缓存分离；启动与运行对账可阻止状态差异下开仓。Binance latest文档描述Algo、close_position及未知提交处理，最贴近本需求。但latest不等于可安装版本，须固定发行版验证。引入其事件与持久化体系会增加现有Finance运维负担。

来源：[架构](https://nautilustrader.io/docs/latest/concepts/architecture/)、[对账](https://nautilustrader.io/docs/latest/concepts/execution/reconciliation/)、[Binance集成](https://nautilustrader.io/docs/latest/integrations/binance/)、[缓存](https://nautilustrader.io/docs/latest/concepts/cache/)。

## 7. 本地知识的使用边界

已读[[Arcus 永续刷量与高频做市系统可行性 — 研究摘要]]：[原卡](</Users/owen/Documents/Obsidian Vault/Cards/Research/Arcus 永续刷量与高频做市系统可行性 — 研究摘要.md>)。继承其“先故障验收、后执行”“独立看交易所事实”“一个主实现者＋独立审查”原则。该卡的Arcus暂停决定、50美元示例和高频策略不移植到本项目；本项目资金与运行授权以本次需求为准。

## 8. 风险清单与验收方向

|失败模式|必须验证的行为|
|---|---|
|提交成功但响应丢失|持久化意图，查询恢复；不凭超时重发|
|部分成交后崩溃|逐成交恢复币数/成本，先恢复保护，再考虑目标差额|
|先撤旧止损后新单失败|保护更替状态可恢复；明确无保护窗口与受影响币处置|
|旧止损触发同时加仓成交|取消剩余加仓，等待终态，对账；残仓按已批准故障权限退出|
|本地以为平仓但交易所尚未成交|核对实际仓位归零后结束周期，清理旧保护再允许后续新周期|
|用户流静默丢事件|新鲜度监测、REST快照与成交补拉；差异暂停新增|
|全池缺数据却仍产榜单|完整性门在评分前阻止；不能用缩小后的池悄悄重排|
|缺最新Fisher周却沿用旧值|比较应生效周而非仅检查非空|
|未知状态触发逐币全平|账户级处置范围门，不用循环绕过Boss全平确认|
|全仓相关冲击|独立压力测算、可用保证金检查与告警，不宣称止损保证本金封顶|
|外部手动操作|识别非系统订单与配置变动，暂停并明确接管，不静默接纳|
|长时间停机后追单|只恢复已有意图；错过周次不新造旧锚点交易|

## 9. 研究轮次与未验证项

Round 1：本地冻结规则与三个参考架构，满足≥3架构、≥10术语、≥5失败模式、≥2技术路线。

Round 2：补强Algo迁移、保护单不能直接改单、503未知状态、保证金/强平、Fisher过期周与主分支缺口。公开API做了只读样例验证。

Round 3：独立交叉核对已完成，9项挑战及逐项处理记录到review.md。文档混杂与参考系统不适合继承的行为已识别；依赖实际行为的保护单更替和账户权限仍列上线阻塞验收，不伪装为已验证。

未验证：账户身份/产品资格、签名权限、实际保证金档位、生产IP连接、原生保护更替与触发、真实成交质量、通知送达。没有代码实现或部署证据。
