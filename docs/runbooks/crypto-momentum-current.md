# Crypto动量统一规则与默认回测入口

2026-09-21 Boss批准实装。规则版本v4，唯一权重来源为scripts/crypto_trend_rankings.py的V4_WEIGHTS：绝对收益45%、ER12.5%、R²12.5%、当前回撤5%、成交额25%。CURRENT_SCORING_VERSION同时控制日报默认、周线趋势计算及HoldingPolicy默认。文字权重说明也由同一映射生成。

先在完整有效候选池计分，再过滤方向。多头收益和log回归斜率必须均正，且D≤30%；D=1−末收盘/窗口最高收盘。超过30%移入观察区，按原分顺序递补，不足名额保留空槽。空头收益/斜率均负，按强度从高到低；不套多头回撤门。BTC永久不入候选/评分分位，参考市场Top100中的BTC名额不人为补第101名。

成交额V统一为对应窗口的平均每日USDT合约金额，金额分100V/(V+10亿USDT)。日报10/14日仍按4h收盘；既有周线趋势窗口仍按日线收盘、V为其窗口日均额。RVOL综合榜和Fisher统计属于各自指标，不改变其定义。30%门在原重选时点生效，未新增持仓期间止损。

## 后续新回测

从main或其新worktree调用统一入口，而非继续使用历史研究分支的临时runner：

```bash
PYTHONPATH=. .venv/bin/python -m scripts.crypto_momentum_backtest \
  --analysis /absolute/frozen-analysis \
  --out /absolute/new-output \
  --mode fisher
```

模式fisher/both/balanced分别为单侧Fisher、双向固定币数、双向每日侧间平衡。默认H14/周选币/v4，仅OOS（默认2025-02-01 04UTC至2026-09-20 04UTC）；新数据明确传--end，子区间可传--start。资金费完全不读不计，10bp/边默认；缺价、资格缺证据、净值非正显式失败，不填零。out必须是新目录。输入需有opens/ohlcv/signals/regimes parquet及生命周期、结算、资格/源质量JSON。

旧信号缓存不可信赖其score_v1/v2/v3列；入口从原始因子和日均成交额经共享score_signal_frame重算score_v4，再用共享direction_eligible执行门槛。manifest写入版本、权重、门槛、时间范围、输入/代码SHA。

已验证参考：Finance/reports/crypto-weight-balance-2026-09-21的r45三条OOS。主干统一入口的NAV/ledger/funding需逐项精确复现参考，再通过独立原始成交价/现金核验。执行账本及统计函数来自已验证研究，未重写计算逻辑。

## 历史复现与运行边界

v1/v2/v3只有显式指定版本才用于历史复现；旧结果和旧研究分支保持原样，不改标签冒充v4。新的优化默认从本入口扩展，后续本研究路径只比较OOS；反复观察的OOS仍为回顾优化样本。

日报仍由Quant原08:06入口导入Finance主干；无需新增cron或重启服务。发布在quant_daily_scan共享锁中备份旧HEAD/代码/入口哈希后更新，并在暂存区及生产目录分别验证。未明确要求时不手动重发当日报告。
