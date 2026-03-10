# Options Scenario Analyzer — BS 概率加权定价

> **Status**: Approved, implementing
> **Date**: 2026-03-10

## 问题

当前 `/options` skill 的盈亏分析是到期二元模式（max profit/loss），完全忽略持有期间期权价格随标的价格、时间、IV 变化的动态表现。

## 方案

### 架构

1. **扩展 `iv_solver.py`** — 加 delta / gamma / theta / rho
2. **新建 `scenario_analyzer.py`** — 策略级场景分析引擎
3. **扩展 `formatter.py`** — 场景矩阵 + 概率汇总格式化
4. **更新 `skill.md`** — Phase 2 精简版 + Phase 3 完整分析

### 数据模型

```python
leg = {
    "side": "call" | "put",
    "direction": "long" | "short",
    "strike": 200.0,
    "expiry_dte": 30,
    "iv": 0.35,
    "entry_price": 5.20,
    "contracts": 1,
}
```

### 概率分布（Cornish-Fisher 偏度调整）

基础：`ln(S_T/S) ~ N((r - σ²/2)T, σ²T)`

调整：`adjusted_pdf(x) = lognormal_pdf(x) × (1 + α × (z³ - 3z) / 6)`

α 获取优先级：
1. 用户提供 25Δ skew → `α = -(put_iv - call_iv) / atm_iv`
2. 用户定性描述 → 轻微 -0.1 / 中等 -0.2 / 强烈 -0.3
3. 默认 0

### 场景矩阵

- 股价轴：IV 自适应，`1σ = S × IV × √(T/252)`，7 点（±0.5σ, ±1σ, ±1.5σ, 当前）
- 时间轴：入场日、+7d、+14d、到期日（DTE < 21 自适应减少）
- IV 敏感度：±10%, ±20%（单行，固定 +7d）

### Skill 集成

- Phase 2：自动附带精简版（E[P&L], Win Prob, Median, Worst 25%），α=0
- Phase 3：用户触发完整矩阵，向用户要 skew 数据

### 文件变更

| 文件 | 动作 |
|------|------|
| `terminal/options/iv_solver.py` | 扩展：bs_delta, bs_gamma, bs_theta, bs_rho |
| `terminal/options/scenario_analyzer.py` | 新建：场景引擎 |
| `terminal/options/formatter.py` | 扩展：format_scenario_matrix, format_probability_summary |
| `~/.claude/skills/options/skill.md` | 更新：Phase 2/3 指令 |
| `tests/test_iv_solver.py` | 扩展：Greeks 测试 |
| `tests/test_scenario_analyzer.py` | 新建：场景分析测试 |

### 不做

- 不动 commands.py / prepare_options_context()
- 不动 chain_analyzer.py
- 不加 scipy/numpy（纯 Python + math）
- 不做持仓跟踪/历史 P&L

## Checklist

- [ ] 1. iv_solver.py: 加 bs_delta, bs_gamma, bs_theta, bs_rho
- [ ] 2. tests/test_iv_solver.py: Greeks 单元测试
- [ ] 3. scenario_analyzer.py: build_strategy + price_strategy
- [ ] 4. scenario_analyzer.py: generate_scenario_matrix
- [ ] 5. scenario_analyzer.py: compute_probability_summary (Cornish-Fisher)
- [ ] 6. tests/test_scenario_analyzer.py: 场景分析测试
- [ ] 7. formatter.py: format_scenario_matrix + format_probability_summary
- [ ] 8. skill.md: Phase 2 精简版 + Phase 3 完整分析指令
