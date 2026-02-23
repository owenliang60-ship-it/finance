# OPRMS 评级系统

双维度评级 → 仓位计算。

## 核心公式

```
最终仓位 = 总资产 x DNA上限 x Timing系数
```

## 完整规范

**详见**: [`models.py`](models.py) — 包含完整数据模型和文档字符串

- `DNARating`: Y 轴（资产基因）— S/A/B/C 等级 + 仓位上限
- `TimingRating`: X 轴（时机系数）— S/A/B/C 等级 + 系数范围
- `OPRMSRating`: 单只股票的评级
- `PositionSize`: 仓位计算结果

## 快速示例

```python
from knowledge.oprms import calculate_position_size, DNARating, TimingRating

# NVDA: S 级资产，A 级时机，系数 0.9
result = calculate_position_size(
    total_capital=3_000_000,
    dna=DNARating.S,
    timing=TimingRating.A,
    timing_coeff=0.9,
)
# result.target_position_pct = 22.5%
# result.target_position_usd = $675,000
```

## 模块结构

| 文件 | 职能 |
|------|------|
| **`models.py`** | **数据模型（单一数据源）** |
| `ratings.py` | 核心引擎: 仓位计算, 灵敏度表, JSON 读写 |
| `changelog.py` | 变更日志: JSONL 追加, 按 symbol 查询历史 |
| `integration.py` | 集成规范: Portfolio Desk JSON schema + 导出 |

## 数据文件

- 评级数据: `data/ratings/oprms.json`
- 变更日志: `data/ratings/oprms_changelog.jsonl`
- Portfolio 导出: `data/ratings/portfolio_export.json`

## 知识镜像

代码中的 `models.py` 是单一数据源。
