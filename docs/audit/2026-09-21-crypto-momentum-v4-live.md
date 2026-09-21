# Crypto统一v4 — 已部署

Boss明确批准实装，后续动量榜单和回测全部统一最新规则。代码e8972291、Parquet依赖声明eb3163e8已合并推送并部署aliyun:/root/workspace/Finance。生产完成时间2026-09-21 04:09:58 UTC（北京时间12:09:58）。

## 规则与入口

- |收益|45 / ER12.5 / R²12.5 / 当前回撤5 / 成交额25；金额分100V/(V+10亿USDT)，V为对应窗口平均每日金额。
- 多头收益/斜率均正且当前D≤30%；先完整有效池计分，再过滤递补。深回撤观察区保留证据；空头不套多头回撤门，未新增周内止损。BTC永久不入候选/分位。
- scripts/crypto_trend_rankings.py为唯一权重/方向资格来源；日报10/14日、既有周线趋势、HoldingPolicy默认均v4。RVOL综合和Fisher统计定义不变。
- 新回测统一scripts/crypto_momentum_backtest.py，默认H14/周选币/OOS，资金费不读不计；从原因子重算score_v4，不信任旧缓存分数。执行/绩效/退出辅助函数从已验证研究原样提取。旧版本只有显式历史复现，旧结果不覆盖。

## 验证

- 本地最新291 passed in6.49s；云端暂存291 passed in15.82s；生产291 passed in15.09s。全仓3890通过/12既有失败/4skip；最新边界/文案和集成变更另由291相关检查覆盖。
- 三个统一回测OOS（Fisher/双向固定币数/每日侧间平衡）与批准r45方案的NAV/ledger/funding逐项完全一致；收益+983.5626%/+413.9735%/+318.8108%，原始成交价/现金/NAV独立核验通过。
- 真实2026-09-20 UTC日报快照的161个有效币种窗口独立计分/资格核验，最大分差1.42e−14；本地和云端生产预览完全相同。此为保存快照重算，未重新调用市场API或手动发送。
- 审查发现并修复v4无效ER/R²等被NaN比较降成零百分位的问题，反例先红后绿；已核验正常行情结果不变。主线程/cr无未解决阻塞发现，零subagent；本实装轮未再调用DSH。
- 本地PyArrow23.0.1/云端24.0.0已验证，requirements声明pyarrow>=23.0，未修改全局运行环境。

## 发布与回滚

共享锁/tmp/quant-cron-locks/quant_daily_scan.lock内备份、fast-forward更新和验证。旧HEAD04913597687806420c03a69c8f070acbe266b1b6；新runtime eb3163e8ac195023f34ebf236d2569ec3579c1df。备份/root/workspace/Quant/backups/crypto-momentum-v4-20260921T040938Z含旧代码tar、旧HEAD、cron、测试/预览和deployed.json。

5个Quant日/周入口文件哈希与crontab前后一致，无重启、无新增cron/自动跟进，手动消息数0。下一次自然日报尚未发生；后续入口已默认v4。回滚应审查逆转本次两个runtime提交并保留备份，勿恢复整库或覆盖历史报告。

本地证据：reports/crypto-v4-rollout-2026-09-21/；新规则与调用方式：docs/runbooks/crypto-momentum-current.md。该OOS是反复观察的回顾优化，不声称独立盲测alpha。
