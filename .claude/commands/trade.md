---
name: trade
description: 录入交易 — 解析意图、展示确认、原子写入
user-invocable: true
---

## /trade — 交易录入

解析用户交易意图并执行原子写入。

### 流程

1. **解析意图**：从用户输入提取 action (BUY/SELL/ADD/TRIM) + ticker + shares + price
   - 示例: `/trade NVDA BUY 100@135`, `/trade NVDA SELL 50@150`
   - 如果信息不完整，追问缺失字段

2. **查询当前状态**：
   ```python
   from portfolio.holdings.manager import PortfolioManager
   from terminal.company_store import get_store
   store = get_store()
   mgr = PortfolioManager(store=store)
   position = mgr.get_position(symbol)
   cash = store.get_cash_balance()
   oprms = store.get_current_oprms(symbol)
   ```

3. **展示确认摘要**（必须等 Boss 确认才能执行）：
   - 当前持仓 / 无持仓
   - 交易后均价、股数、仓位占比 (基于 total_NAV)
   - OPRMS DNA 上限 vs 交易后仓位
   - 现金变化
   - 格式示例：
   ```
   📝 交易确认
   NVDA BUY 100 @ $135.00
   
   当前: 600 shares @ $176.09 (DNA=S, Timing=A)
   交易后: 700 shares, 均价 $170.22
   仓位: 7.1% → 8.3% (DNA上限 25%)
   现金: $750,000 → $736,500
   
   确认执行？
   ```

4. **Boss 确认后执行**：
   ```python
   result = mgr.execute_trade(symbol, action, shares, price, date)
   ```

5. **交易后自动检查**（不需要 Boss 确认，直接展示）：
   - [ ] 交易后仓位是否超过 DNA 上限？ → 警告
   - [ ] 交易后行业集中度是否超标（>40%）？ → 警告
   - [ ] 该股票有没有 OPRMS 评���？ → 没有则提醒先做分析

6. **Auto-push**：
   交易 `execute_trade()` 返回成功后，执行：
   ```python
   store.checkpoint()
   ```
   ```bash
   ./sync_to_cloud.sh --push
   ```
   push 失败只 warning，不影响已提交的本地交易。

### 期权交易（lifecycle-aware）

期权交易**必须**走 `PortfolioManager` 的期权 lifecycle 引擎，不再直接 `store.insert_option_position()`。
原子写入 `option_transactions` + `option_positions` + `portfolio_cash` 三处，保证 NAV 不漂。

#### 1. 用户语法（broker-style，强烈建议）

| 用户动作 | 含义 | quantity 方向 | cash 方向 |
|----------|------|---------------|-----------|
| `BTO` | Buy To Open — 买入开多 | +qty | -premium |
| `STC` | Sell To Close — 卖出平多 | -qty | +premium |
| `STO` | Sell To Open — 卖出开空 | -qty | +premium |
| `BTC` | Buy To Close — 买入平空 | +qty | -premium |

允许自然语言别名，但**必须先归一化**到上面四种再进入 preview/execute。常见映射：

| Boss 说法 | 归一化 |
|-----------|--------|
| 买/买开/开多 | `BTO` |
| 卖/卖平/平多 | `STC` |
| 卖开/开空/Sell to open | `STO` |
| 买平/平空/buy back | `BTC` |
| 平仓 | 看当前 leg 方向：long → `STC`；short → `BTC` |

如果 Boss 说"卖 QQQ put"但不能确定是 STO 还是 STC（既无现有 leg 又无明确开/平表态），**追问**。

#### 2. 解析合约

```python
from terminal.options.occ_symbol import parse_option_contract
contract = parse_option_contract(text)  # {symbol, expiration, strike, side}
```

支持的输入：
- `QQQ 2026-09-18 410P`（ISO 日期）
- `QQQ 260918 410P`（紧凑 YYMMDD）
- `QQQ260918P00410000`（OCC 标准 symbol）

合约解析失败 → **追问 exact contract，禁止猜**。

#### 3. Preview → 确认 → Execute

```python
from portfolio.holdings.manager import PortfolioManager
from terminal.company_store import get_store

mgr = PortfolioManager(store=get_store())

preview = mgr.preview_option_trade(
    symbol=contract["symbol"],
    expiration=contract["expiration"],
    strike=contract["strike"],
    side=contract["side"],
    action=action_normalized,        # 'BTO' | 'STO' | 'BTC' | 'STC'
    quantity=qty,
    premium=premium,
    date=date,
    strategy_tag=tag,                # 必传，否则按 "" 处理
)
# 展示确认（见模板），等 Boss "确认"
result = mgr.execute_option_trade(**same_kwargs_as_preview)
```

`execute_option_trade()` 内部 `BEGIN IMMEDIATE`：
- 写一条 `option_transactions` 流水
- 更新或关闭 `option_positions` 当前 leg
- 写一条 `portfolio_cash`（premium 现金联动）

任一子步骤失败 → 整笔回滚，DB 状态保持交易前。

#### 4. 期权确认模板（必须包含）

```text
📝 期权交易确认
QQQ 2026-09-18 410P  BTC 10 @ $2.10  [tail_hedge]

当前: -10 张 @ $4.78 (tail_hedge)
交易后: 0 张 (平仓)
现金: $837,000.00 → $834,900.00
本次 realized PnL: +$2,680.00

确认执行？
```

字段要求：
- `当前 → 交易后` 的张数变化
- `cash delta`（before → after）
- `realized PnL`（仅 STC/BTC，OPEN/ADD 不显示）
- `strategy_tag`（如有）

#### 5. ROLL（1-out-1-in）

显式 roll：

```python
result = mgr.execute_option_roll(
    close_leg=dict(symbol=..., expiration="2026-06-18", strike=520.0, side="PUT",
                   action="BTC", quantity=10, premium=2.10, strategy_tag="tail_hedge"),
    open_leg=dict(symbol=..., expiration="2026-09-18", strike=490.0, side="PUT",
                  action="STO", quantity=10, premium=6.50, strategy_tag="tail_hedge"),
    date=today,
    notes="ROLL QQQ 260618 520P -> 260918 490P",
)
```

`close_leg` + `open_leg` 在同一 SQLite transaction 内执行；任一失败 → 全回滚，**不会**留下"旧腿已平、新腿没开"的中间态。

确认模板要把 close + open 两条都展示，再让 Boss 一次性确认。

#### 6. 强制追问规则（违反就不可执行）

| 情况 | 必须 |
|------|------|
| 合约解析失败 | 追问 exact contract |
| 需要 CLOSE（STC/BTC）但找不到 OPEN leg | 追问或拒绝；**禁止自动新建反向 leg** |
| 命中多条不同 `strategy_tag` 的 OPEN leg | 要求 Boss 补 `strategy_tag` |
| 同方向重复仓位但 `strategy_tag` 不同 | 默认按 `""` 查；找不到则追问应该归到哪个 tag |

#### 7. 交易后处理（与股票一致）

成功后：

```python
store.checkpoint()
```

```bash
./sync_to_cloud.sh --push
```

push 失败只 warning，不影响已提交的本地交易。

### 注意事项

- 所有交易必须等 Boss 确认，禁止自动执行
- 日期默认今天，除非 Boss 指定
- SELL/TRIM 时检查持仓够不够
- 港股 ticker 带 .HK 后缀
- 期权 quantity 永远是 contracts 数（每张 = 100 shares），不要传股数
- 期权 premium 是 per share 报价（broker quote），cash 计算自动 ×100
