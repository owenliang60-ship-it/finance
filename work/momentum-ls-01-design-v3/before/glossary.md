# 动量多空1号实盘术语 — A-lite

日期：2026-10-19。

|术语|含义|
|---|---|
|A-lite|每周调仓＋每分钟守护，共用小模块/SQLite；原生止损持续在交易所运行|
|并发提交|互不依赖的网络请求一起发起；不表示交易所同时或原子成交|
|单写者|每个运行任务只由一处保存状态；与网络并发不冲突，两个任务靠排他锁互斥|
|周锚点|逻辑周一04UTC；与程序实际唤醒时间分开|
|子账户隔离|资金与其他账户分开；不代表逐币逐仓|
|全仓|子账户内仓位共享保证金，多币损失会相互影响|
|杠杆设置|初始保证金配置；不代替100%/150%策略gross目标|
|账户权益|钱包余额＋未实现盈亏；不是可用保证金，也不是名义敞口|
|entryPrice|交易所入场均价字段，本方案验证后作为成本主来源|
|breakEvenPrice|交易所另列的盈亏平衡价字段；不能替代冻结策略成本|
|CONTRACT_PRICE|合约成交价，本策略原生止损触发源|
|MARK_PRICE|标记价格，用于账户未实现盈亏与强平相关计算|
|priceProtect|额外价差触发保护；本项目关闭|
|MARKET|直接市价委托；仍可能拒绝、部分成交或响应未知|
|STOP_MARKET|交易所条件单，触发后市价退出；本方案优先数量型reduceOnly|
|reduceOnly|只减仓约束；不保证任何重复保护都能被接受，也不能阻止旧单减掉未来的新同向仓|
|closePosition|另一种全仓位条件单模式；本方案不再要求两张该模式保护共存|
|actions|每次交易所请求的持久记录：完整参数、ID、状态与实际结果；不是只存clientOrderId|
|未知提交|请求可能成功但响应丢失；查询恢复，不按超时重发|
|冷却/持仓周期|从零到非零再回零的策略状态与退出限制；必须靠事实恢复|
|前向观察|冻结规则后真实发生的新结果；与反复用于定参的历史样本分开|

依据：[官方交易接口](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/trade)、[官方错误码](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/error-code)。entryPrice的业务语义须先通过协议验收。
