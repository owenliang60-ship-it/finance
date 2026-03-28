# Forge

Forge 是 BTC 策略的受控自动调优沙箱，支持多策略并行优化。

- `runner.py`: 控制循环、agent 调用、mutation guard、晋级、日志、停机。
- `evaluator.py`: 唯一评分裁判，输出 visible windows，并对 holdout 保密。
- `campaign.lock.json` / `campaign_*.lock.json`: 实验配置锁文件。
- `manifests/`: Level 1 参数白名单（每策略一个）。
- `strategies/`: champion / candidate 及其参数覆盖文件。
- `logs/{campaign_id}/`: 按 campaign 隔离的 public/private 运行日志。

## 策略合约

每个策略文件必须导出：
- `StrategyConfig` — frozen dataclass，所有可调参数
- `run_backtest(symbol, price_4h_df, price_daily_df, config, ...)` → `ContinuousTimingResult`

## 用法

```bash
# 评估 champion
python forge/evaluator.py --strategy-path forge/strategies/helen_champion.py --campaign forge/campaign.lock.json

# 跑 Forge 优化
python forge/runner.py --strategy helen --campaign forge/campaign.lock.json --rounds 50
python forge/runner.py --strategy dual_ma --campaign forge/campaign_dual_ma.lock.json --rounds 50
```
