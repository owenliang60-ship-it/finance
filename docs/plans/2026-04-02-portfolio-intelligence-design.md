# Portfolio 持仓管理 + Intelligence 情报引擎 设计文档

> Brainstorm session: 2026-04-02
> 北极星对齐: CIO 层（第四层）— 组合构建 + 行为纠偏
> Review round 1: 2026-04-02 — 6 issues fixed (4×P1, 2×P2)
> Review round 2: 2026-04-02 — coupling fixes integrated

## 一、问题定义

系统有 35 层基建（数据/分析/回测/策略），但不知道 Boss 实际持有什么。
没有真实持仓数据，所有分析都是纸上谈兵。

**两个需求：**
1. **持仓录入** — 系统知道真实持仓，算损益，追踪交易流水
2. **Portfolio Intelligence** — 基于真实持仓，每日推送技术信号/基本面变化/风险预警

## 二、数据模型与持仓录入

### 2.1 存储方案：SQLite（company.db 新表）

沿用现有 `portfolio/holdings/` 骨架，但从 JSON 持久化迁移到 company.db。
理由：Intelligence 需要 holdings JOIN oprms_ratings JOIN daily_price，JSON 做不到。

**`holdings` 表：**

| 字段 | 类型 | 说明 |
|------|------|------|
| position_id | INTEGER PK | 自增，每轮建仓一个独立 ID |
| symbol | TEXT | 股票代码（允许同 symbol 多行，每轮一个生命周期）|
| shares | REAL | 持有股数 |
| avg_cost | REAL | 平均成本价 |
| open_date | TEXT | 首次建仓日期 |
| close_date | TEXT | 清仓日期（OPEN 时为 NULL）|
| realized_pnl | REAL | 实现盈亏（OPEN 时为 NULL）|
| status | TEXT | OPEN / CLOSED |
| last_updated | TEXT | 最后更新时间 |

> **设计决策（P1-2 修复）**：`symbol` 不再做主键。同一 ticker 卖光再买回是独立的一轮，
> 各自有 `position_id`。清仓时记录 `realized_pnl` 和 `close_date`，状态改为 CLOSED。
> 查询当前持仓：`WHERE status = 'OPEN'`。历史回合查询：`WHERE symbol = ? ORDER BY open_date`。
>
> **耦合约束（Review round 2）**：同一 `symbol` 在任意时刻最多只允许 **1 行 OPEN 持仓**。
> 这样现有 `get_position(symbol)` / `update_position(symbol)` / `remove_position(symbol)` 的单行语义才能保持成立。
> 落地时用 DB 约束保证：
> - 优先：partial unique index `UNIQUE(symbol) WHERE status='OPEN'`
> - 兜底：manager 层插入前显式检查，发现第二行 OPEN 直接报错

**`transactions` 表（交易流水）：**

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 自增 |
| position_id | INTEGER FK | 关联 holdings.position_id |
| symbol | TEXT | 股票代码（冗余，方便查询）|
| action | TEXT | BUY / SELL / ADD / TRIM |
| shares | REAL | 交易股数 |
| price | REAL | 成交价格 |
| date | TEXT | 交易日期 |
| notes | TEXT | 备注（可选）|
| created_at | TEXT | 记录时间 |

**`portfolio_cash` 表（现金流水 + 余额）：**

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 自增 |
| action | TEXT | SET / DEPOSIT / WITHDRAW |
| amount | REAL | 变动金额（DEPOSIT > 0, WITHDRAW < 0, SET = 新余额 - 旧余额）|
| balance_after | REAL | 操作后现金余额 |
| notes | TEXT | 备注 |
| updated_at | TEXT | 更新时间 |

> **设计决策（P2-6 修复）**：`amount` 是 delta，`balance_after` 是操作后余额，语义明确。
> 当前现金 = 最新一行的 `balance_after`。审计回放：逐行累加 `amount` 应等于 `balance_after`。

### 2.2 权重计算：总 NAV 为分母

> **设计决策（P1-1 修复）**：所有权重以 `total_NAV = Σ(position_market_value) + cash` 为分母。

```
position_weight = position_market_value / total_NAV
invested_pct = Σ(position_market_value) / total_NAV
cash_pct = cash / total_NAV
```

影响范围：
- `manager.py refresh_prices()` — `current_weight` 计算改用 total_NAV
- `manager.py get_portfolio_summary()` — summary 从“持仓市值”升级为“total_NAV 语义”
- `exposure/analyzer.py` — 所有浓度计算基于 total_NAV
- `exposure/alerts.py` — DNA 上限检查基于 total_NAV
- `terminal/commands.py portfolio_status()` — summary 输出需包含 total_NAV / invested_pct / cash_pct
- `terminal/monitor.py run_full_monitor()` — `total_value` 需改成 total_NAV 口径，避免和 Intelligence 口径冲突
- `/trade` skill — 交易后仓位检查基于 total_NAV
- Intelligence 推送 — 组合概览的仓位/现金占比

> **设计决策（Review round 2）**：不要把旧的 `get_portfolio_value()` 静默改成含现金语义。
> Manager 层拆成两个清晰概念：
> - `invested_value = Σ(position_market_value)`
> - `total_nav = invested_value + cash`
> 上层 summary / monitor / Intelligence 统一展示 `total_nav`，避免旧调用方把“持仓市值”误当“总资产”。

### 2.3 现有骨架复用

| 文件 | 状态 | 改动 |
|------|------|------|
| `schema.py` | ✅ 保留 | Position dataclass 设计干净，OPRMS 公式正确 |
| `manager.py` | 🔧 改造 | JSON → SQLite，profile 改读 company.db，权重改用 total_NAV |
| `history.py` | 🔧 改造 | JSON → transactions 表 |
| `exposure/analyzer.py` | ✅ 保留 | 多维度暴露分析，权重已注入（调用方传入 total_NAV 权重即可）|
| `exposure/alerts.py` | ✅ 保留 | 7 条告警规则，权重已注入 |
| `exposure/report.py` | ✅ 保留 | Markdown 报告生成 |
| `benchmark/engine.py` | ⚠️ 需改造（Phase 2） | 当前用静态权重回看历史，接入 transactions 后需改为时间加权收益 |
| `benchmark/attribution.py` | ⚠️ 需改造（Phase 2） | Brinson 归因需要时间序列权重，当前用快照权重会失真 |
| `benchmark/review.py` | ⚠️ 需改造（Phase 2） | 依赖 refresh_prices() 灌入权重，不能简单删掉 |

> **设计决策（P1-4 修复）**：benchmark 模块标记为"Phase 2 改造"而非"保留"。
> 当前 Phase 1 不动 benchmark，Intelligence 推送的 Beta 和行业集中度用实时快照计算即可。
> 真正的业绩归因需要 transactions 时间序列，留到 Phase 2。

### 2.4 录入方式

- **初始导入**: Boss 提供 ticker/股数/均价列表，批量写入
- **日常维护**: `/trade` skill 规范录入（见第五节）

## 三、Portfolio Intelligence（持仓感知情报引擎）

### 3.1 架构

不是新建独立系统，而是一个**编排脚本**，把已有数据基建针对持仓过滤和聚合。

```
scripts/portfolio_intelligence.py
    │
    ├── 1. 加载持仓 (company.db holdings WHERE status='OPEN')
    ├── 2. 加载现金 (company.db portfolio_cash 最新 balance_after)
    ├── 3. 拉最新价格 (market.db daily_price)
    ├── 4. 计算 total_NAV + 权重 + P&L
    ├── 5. 逐只跑技术信号 (PMARP / RVOL / EMA120)
    ├── 6. 组合级指标 (QQQ Beta / 行业集中度)
    ├── 7. 查 kill conditions (company.db kill_conditions)
    ├── 8. 格式化报告
    └── 9. Telegram 推送
```

### 3.2 信号定义

**技术预警：**

| 信号 | 阈值 | 数据源 | 实现 |
|------|------|--------|------|
| PMARP 超涨 | ≥ 98% | market.db daily_price | `src/indicators/` 已有 |
| PMARP 超跌 | ≤ 2% | market.db daily_price | `src/indicators/` 已有 |
| RVOL 异常放量 | ≥ 2σ | market.db daily_price | `src/indicators/` 已有 |
| 跌破 EMA120 | close < EMA(120) | market.db daily_price | 新增，简单 |

**成本预警（按 OPRMS DNA 分级）：**

| DNA 等级 | 仓位上限 | 浮亏告警线 |
|----------|---------|-----------|
| S 圣杯 | 20-25% | -30% |
| A 猛将 | 15% | -20% |
| B 黑马 | 7% | -15% |
| C 跟班 | 2% | -10% |

**组合级指标：**

| 指标 | 计算方式 |
|------|---------|
| 等效 QQQ Beta | 每只股票 60 日滚动 Beta (vs QQQ)，按持仓权重（total_NAV 基准）加总 |
| 行业集中度 | 按 sector 分组求和（total_NAV 基准），>40% 标记 ⚠️ |

**基本面：**

| 信号 | 来源 | 实现 |
|------|------|------|
| Kill conditions | company.db kill_conditions 表，每日全列 | 直接查询 |
| Timing 评级变化 | oprms_ratings 表最近两条 diff | 查 `WHERE symbol=? ORDER BY created_at DESC LIMIT 2`，比较 dna/timing 字段 |

> **设计决策（P1-3 + Review round 2）**：Kill conditions 统一到 SQLite 为 SSOT，
> 但 **不直接让调用方绕过 `terminal.company_db` facade**。
> 当前 `freshness` / `monitor` / `pipeline` / `commands` / `auto_deep_analyze.sh`
> 都经由 `terminal.company_db` 读写；因此 Phase 1 的正确做法是：
> 1. 保持 `terminal.company_db` 的 public API 不变
> 2. 把其中的 `get_kill_conditions()` / `save_kill_conditions()` 改成 SQLite-first
> 3. JSON 仅保留为迁移期 fallback / 历史归档
>
> 这样现有调用链不用大面积改 import，避免读写路径再次分叉。

> **设计决策（P2-5 修复）**：Timing 变化检测不依赖 `is_current` flag，
> 而是查最近两条记录做 diff。SQL：`SELECT * FROM oprms_ratings WHERE symbol=? ORDER BY created_at DESC LIMIT 2`。
> 如果两条的 `dna` 或 `timing` 字段不同，触发变化提醒。

### 3.3 推送格式

**区块一：🚨 需要行动（仅有信号时出现）**
```
🚨 行动信号

NVDA | PMARP 98.7% ⬆️ 超涨预警
MSFT | 浮亏 -21.3% (DNA=A, 阈值-20%) ⚠️
TSM  | RVOL 2.4σ 异常放量 | 当日 -3.2%
AMZN | 跌破 EMA120 ($178.50 < $182.30)
```

**区块二：📊 组合概览（每日固定）**
```
📊 组合概览

总资产: $2,145,000 | 仓位 82% | 现金 18%
QQQ等效β: 1.25
累计: +$345,000 (+19.2%)

行业集中度:
  Technology 58% ⚠️ | Healthcare 15% | Financial 9%

持仓: 12 只 | S×2 A×5 B×4 C×1
```

**区块三：📋 Kill Conditions Checklist（每日固定）**
```
📋 退出条件审视

NVDA (S): 数据中心收入增速连续两季<30%
NVDA (S): 毛利率跌破70%
MSFT (A): Azure增速连续两季<20%
...
```

### 3.4 部署

- **脚本**: `scripts/portfolio_intelligence.py`
- **云端 cron**: 22:00 SGT（= 22:00 北京时间 = 14:00 UTC）
- **推送渠道**: Telegram（独立于 07:00 晨报）
- **依赖**: market.db（云端所有权）+ company.db（本地所有权，需 push 到云端）

> **时区注意**：22:00 SGT 在美东夏令时 = ET 10:00（开盘后 30 分钟 ✓），
> 但冬令时 = ET 09:00（开盘前 30 分钟 ✗）。当前保持 22:00 SGT，
> 冬令时切换时（11 月第一个周日）手动调整 cron 到 23:00 SGT。

## 四、/trade Skill 交易录入

### 4.1 流程

1. 解析意图：BUY/SELL/ADD/TRIM + ticker + 股数 + 价格
2. 查 holdings 表确认当前状态（新开仓 or 加仓 or 减仓）
3. 展示确认摘要（权重基于 total_NAV）：

```
确认交易:
NVDA | 加仓 100 股 @ $135.00
现有: 200 股 @ $120.00 → 新均价: $125.00 (300 股)
OPRMS: DNA=S, Timing=A → 目标仓位 20%
当前仓位: 12% → 交易后: 15% (total NAV: $2.1M)
```

4. Boss 确认 → 写入 transactions（关联 position_id）+ 更新 holdings
5. 清仓 → 计算 realized_pnl，holdings 标记 CLOSED + close_date，新买同 ticker 开新行

### 4.2 现金联动

- 买入自动扣减现金（portfolio_cash 插入 WITHDRAW 行）
- 卖出自动增加现金（portfolio_cash 插入 DEPOSIT 行）
- 支持直接更新现金余额（SET 操作：入金/出金）

### 4.3 事务与并发控制

> **设计决策（Review round 2）**：`/trade` 不是 3 次独立写入，而是一次原子事务。
> `holdings + transactions + portfolio_cash` 必须在同一个 SQLite transaction 内提交；
> 任一环节失败则整笔 rollback，不允许出现"持仓更新了但现金没扣"或"流水写了但持仓没变"。

事务边界：
1. 读取当前 OPEN 持仓 + 最新现金余额
2. 计算交易后状态（新均价、realized_pnl、cash balance_after、post-trade weight）
3. Boss 确认后，开启单个 transaction
4. 写 `transactions`
5. 写/更新 `holdings`
6. 写 `portfolio_cash`
7. commit 成功后才返回“交易完成”

并发控制：
- `/trade` 本地写库与 `sync_to_cloud.sh --push/--pull` 必须串行，不能并发访问同一个 `company.db`
- 复用统一锁（例如 `/tmp/finance-companydb-sync.lock`），覆盖 3 类操作：
  - `/trade` commit 后 auto-push
  - 手动 `sync_to_cloud.sh --push/--pull`
  - 定时 `scheduled_pull.sh`
- auto-push 在 **transaction commit 之后** 才触发；push 失败只影响云端同步，不回滚本地已提交交易
- `portfolio_intelligence.py` 只读已提交状态，不读未提交中间态

### 4.4 防遗漏 Checklist（skill 自动检查）

- [ ] 交易后仓位是否超过 DNA 上限？→ 警告（基于 total_NAV）
- [ ] 交易后行业集中度是否超标（>40%）？→ 警告
- [ ] 该股票有没有 OPRMS 评级？→ 没有则提醒先做分析

## 五、数据源统一与基础设施收口（P1 前置修复）

### Kill Conditions 迁移

当前状态：
- `terminal/company_db.py save_kill_conditions()` → 写 JSON（`data/companies/{SYMBOL}/kill_conditions.json`）
- `terminal/company_store.py CompanyStore.save_kill_conditions()` → 写 SQLite（`company.db kill_conditions` 表）
- `auto_deep_analyze.sh` / `freshness.py` / `monitor.py` / `pipeline.py` / `commands.py` 都依赖 `terminal.company_db` facade

迁移步骤：
1. 批量导入：遍历 `data/companies/*/kill_conditions.json`，写入 `company.db kill_conditions` 表
2. 修改 `terminal.company_db.py`：
   - `save_kill_conditions()` → dual-write or SQLite-first
   - `get_kill_conditions()` → SQLite-first，JSON fallback
3. 保持上层 import 不变，所有旧调用方自动切到 SQLite 读链
4. 验证：同一 symbol 在 `company_lookup` / `monitor` / `freshness` / `portfolio_intelligence` 中读取结果一致
5. JSON 文件保留但不再是 SSOT

### company.db Sync / WAL 安全

当前耦合风险：
- `CompanyStore` 以 WAL 模式打开 `company.db`
- `sync_to_cloud.sh --push` 目前直接 rsync `company.db` 主文件
- `data_guardian.snapshot()` 目前直接打包 `company.db`

> **设计决策（Review round 2）**：在 `company.db` 承载真实持仓后，push / backup 前必须先做 WAL checkpoint。
> 否则最近已提交的 trade/cash 写入可能仍停留在 `company.db-wal`，云端或快照拿到的是旧状态。

落地要求：
1. 提供统一 helper：`checkpoint_company_db()`（可放在 `terminal/company_store.py` 或独立 util）
2. `sync_to_cloud.sh --push` 在 rsync `company.db` 前先本地 checkpoint
3. `data_guardian.snapshot()` 在打包 `company.db` 前也调用同一个 checkpoint helper
4. `/trade` commit 完成后再触发 auto-push；push 过程沿用第 4.3 节全局锁
5. 验证：一笔 trade 提交后立即 push / snapshot，云端与备份都能看到最新持仓和现金余额

## 六、实现路线（分 3 步）

### Step 1: 数据模型 + 持仓录入
- 前置：Kill conditions facade 迁移 + company.db WAL checkpoint 接入
- company.db 建表（holdings / transactions / portfolio_cash）
- manager.py 改造 JSON → SQLite，权重改用 total_NAV
- `get_portfolio_summary()` / `portfolio_status()` / `run_full_monitor()` 统一 total_NAV 口径
- 初始导入 Boss 的真实持仓 + 现金余额
- 验证：P&L 计算正确，exposure 分析基于 total_NAV，现有 Terminal 读接口口径一致

### Step 2: Portfolio Intelligence 编排脚本
- `scripts/portfolio_intelligence.py` 核心逻辑
- 信号计算（PMARP/RVOL/EMA120/Beta/浮亏）
- Kill conditions 查询 + Timing 变化 diff
- Telegram 格式化 + 推送
- 本地验证通过

### Step 3: 云端部署 + /trade Skill
- 云端 cron 22:00 SGT
- `/trade` skill 开发
- `/trade` 单事务提交 + 全局 sync lock 接入
- `sync_to_cloud.sh --push` / `data_guardian.snapshot()` 接入 company.db checkpoint
- E2E 验证

### Phase 2（后续）: Benchmark 改造
- benchmark/engine.py 改为时间加权收益（读 transactions 时间序列）
- benchmark/attribution.py Brinson 归因接入真实权重历史
- benchmark/review.py 适配新数据流

### 最小验证清单（防耦合回归）

1. 同一 ticker 清仓后再买回，生成新的 `position_id`，且任意时刻只有 1 行 OPEN
2. `/trade` 一次提交同时更新 holdings / transactions / cash；任一步失败则整笔回滚
3. `portfolio_status()` / `run_full_monitor()` / Intelligence 三处的 total_NAV、cash_pct、invested_pct 一致
4. `company_lookup()` / `freshness` / `monitor` / `pipeline` 读取到同一份 kill conditions（SQLite SSOT）
5. 一笔 trade commit 后立即执行 push 和 snapshot，云端与备份都包含最新 company.db 状态

## 七、风险与约束

| 风险 | 缓解 |
|------|------|
| company.db 是本地所有权，云端需要最新持仓 | 每次 /trade 后 auto push，或 cron 前 pull |
| 技术指标依赖 market.db 数据新鲜度 | 云端 06:30 已有量价更新，22:00 跑时数据是当天的 |
| Kill conditions 读写链分叉 | 保留 `terminal.company_db` facade，内部统一 SQLite-first（第五节）|
| Kill conditions 是文本，无法自动判断触发 | 作为 checklist 提醒人工审视 |
| OPRMS 评级可能过时 | Intelligence 报告标注评级日期，过期 >90 天提醒 review |
| 冬令时 cron 早于美股开盘 | 11 月手动调整到 23:00 SGT，3 月调回 22:00 SGT |
| `/trade` 与 push/pull 并发，导致 company.db 状态撕裂 | `/trade` 单 transaction + 全局 sync lock；push 仅在 commit 后触发 |
| WAL 模式下直接复制 company.db，导致云端/快照缺最新提交 | push / snapshot 前统一 checkpoint company.db |
| 同一 symbol 出现两行 OPEN，打破现有 manager API 语义 | DB partial unique index + manager 显式检查 |
| Benchmark 用静态权重失真 | Phase 2 改造，Phase 1 不依赖 benchmark 模块 |
