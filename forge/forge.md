# Forge Prompt

你在做 BTC 择时策略的受控优化。

目标：
- 最大化 `visible_score`
- 维持每个 visible window 的回撤和暴露门槛
- 不要试图推断或优化 holdout；它对你不可见

硬规则：
- stdout 第一行必须是 `HYPOTHESIS: <一句话实验假设>`
- `parameter` 模式只能改 `candidate_params.json`
- `structural` 模式才允许改 `candidate.py`
- 永远不要修改 `runner.py`、`evaluator.py`、campaign lock 文件、manifest、日志
- 保持导出契约：`StrategyConfig` / `run_backtest`

输出要求：
- 第一行给出本轮假设
- 后面简要说明你改了什么
