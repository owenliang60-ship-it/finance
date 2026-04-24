# 012: 期权簿无 lifecycle 引擎导致 PI NAV 静默漂移

**日期**: 2026-04-24
**严重程度**: 高（NAV 报告与真实持仓脱钩，且没有任何告警）
**根因**: `/trade` 期权路径只 `INSERT option_positions`，从不 UPDATE/CLOSE，也不写 cash 流水

---

## 发生了什么

PI 晨报展示了一笔早就在外盘平掉的 short put，按"还在 -10 张"估值，导致：
- option leg 市值算错（按旧 leg 而不是当前真实仓位）
- premium 进出从未触达 `portfolio_cash`，NAV 现金侧也错
- header 只显示 live quote 的时间戳，没有 position-book 的时间戳，肉眼看不出错

错配静默存在了多次跑批，直到 Boss 比对券商对账单才发现。

## 根因分析

### 期权 `/trade` 当时的实现

`.claude/commands/trade.md` 的期权分支直接调：

```python
store.insert_option_position(symbol=..., expiration=..., strike=...,
                             side=..., quantity=qty, avg_premium=premium, ...)
```

只有 INSERT，没有 UPDATE / CLOSE / cash delta。所以：

| 业务事件 | DB 实际行为 | 应有行为 |
|----------|-------------|----------|
| 新开仓 (BTO/STO) | INSERT 一条 OPEN | ✅ |
| 加仓 | 又 INSERT 一条 OPEN | ❌ 应 UPDATE 同 leg + 重算 avg_premium |
| 部分平仓 (STC/BTC) | 不写任何东西 | ❌ 应 UPDATE quantity + 累计 realized_pnl |
| 全平 | 不写任何东西 | ❌ 应 status=CLOSED |
| roll | INSERT 新 leg，旧 leg 永远 OPEN | ❌ 应原子 close + open |
| premium 现金 | 不动 portfolio_cash | ❌ 应同步进出现金 |

### PI 端看不见

`scripts/portfolio_intelligence.py` 头部只暴露 live quote 时间和 signals 时间，
没有 position-book 时间。所以一笔半年前就该 CLOSED 的 leg，与今早刚拉的 live
quote 拼在一起，外观上"看起来很新"。

## 修复

详见 `docs/plans/2026-04-24-trade-skill-option-lifecycle-hardening.md`：

1. **schema additive 升级**（migration 7）：
   - `option_positions.realized_pnl REAL DEFAULT 0`
   - 新表 `option_transactions`（成交流水 ledger）
   - partial unique index on `(symbol, expiration, strike, side, strategy_tag) WHERE status='OPEN'`
2. **PortfolioManager 期权引擎**：
   - `preview_option_trade()` / `execute_option_trade()` / `execute_option_roll()`
   - 在单个 `BEGIN IMMEDIATE` transaction 内写 ledger + UPDATE/CLOSE position + portfolio_cash
   - 同方向加仓重算 avg_premium，反向操作算 realized_pnl
   - 禁止单笔翻方向（必须 CLOSE+OPEN 或 ROLL）
3. **`/trade` skill 改写**（保持 stock path 不动）：
   - `parse_option_contract()` 支持 ISO / 紧凑 / OCC 三种合约写法
   - 用户语法收紧到 `BTO/STO/BTC/STC`，自然语言别名先归一化
   - 4 条强制追问规则（合约模糊、找不到 OPEN leg、多 strategy_tag、tag 缺失）
4. **PI header 加 `positions as of YYYY-MM-DD`**：
   - 取 holdings/option_positions/portfolio_cash 三个 last_updated 的 max
   - 只暴露事实，不做 stale/fresh 主观判断

## 教训

- **只插不维护是 NAV 漂移的同一种 bug，无论资产类别**。股票路径几年前已经做过 lifecycle，期权路径欠了同样的债。
- **现金联动不是装饰性的**。premium 不进 cash，总 NAV 必然在"仓位对了、现金错了"和反过来之间来回飘，且没有任何指标会响。
- **可见性比正确性先到位**。`positions as of` 这种"事实暴露"成本极低，能让人脑层立刻看见错位；只追求正确性而不暴露时间戳，下一次还是会被同样的 drift 偷袭。
- **任何"录入入口"必须有 lifecycle 状态机**，不要让 prompt 文案承担状态推导责任。

## 测试覆盖

新增的 targeted 测试（全绿）：

- `tests/test_portfolio_store.py::TestOptionLifecycleSchema`（8 项）
- `tests/terminal/options/test_occ_symbol.py::TestParseOptionContract`（15 项）
- `tests/test_portfolio/test_manager.py::TestOptionTradeEngine`（15 项）
- `tests/test_portfolio/test_intelligence.py::TestPositionsAsOf`（3 项）
- `tests/test_portfolio/test_intelligence.py::TestFormatReport::test_snapshot_line_includes_positions_as_of`

加上手工 dry-run 走通 BTO / STO / BTC / ROLL 四个场景，preview 与 execute
的 cash delta、realized PnL、effect 全部一致。
