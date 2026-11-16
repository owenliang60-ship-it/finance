# 动量多空1号实盘 — 三段实施计划

日期：2026-10-20。状态：§9 Q1–Q3 已由 Boss 按推荐确认（2026-10-20）。**Boss 已批准开工（2026-10-20）**。**2026-11-15 Boss 批准 5 项修订（§13），与下文冲突处以 §13 为准**：门 1 拆成 1a/1b，阶段 2 先行；协议探针改在生产小额子账户；首个实盘周一 1,000 U；下单改串行；分支 push 备份。执行方由 Kiro 改为 CC。
依据：[北极星 v3](../design/momentum-ls-01-live/north-star.md)（D1–D6 已拍板，commit `5e2cb152`）、[需求](../design/momentum-ls-01-live/requirements.md)、[协议验收](../design/momentum-ls-01-live/protocol-validation.md)。
所属层：主北极星「Trading / Risk 执行分支：动量多空1号」（`docs/design/north-star.md:161`）。不改数据层所有权，不写 market.db / company.db。
规则版本：momentum-ls-01 / 2026-10-19.v2。交易规则不改。

## 0. 一眼看懂

~~~mermaid
flowchart LR
  S1["阶段1 离线<br/>金样本对齐冻结内核"] --> M1{"门1a<br/>离线证据"}
  M1 --> S2["阶段2<br/>先写故障夹具<br/>再写两个任务＋CLI"]
  M1 --> PB["生产小额探针 G1–G5<br/>云端发布目录运行"]
  PB --> M1B{"门1b<br/>Boss 审"}
  M1B --> S2
  S2 --> M2{"门2<br/>Boss 审<br/>须门1b 已过"}
  M2 --> S3["阶段3<br/>固定发布目录<br/>生产只读核对＋影子1个周一"]
  S3 --> M3{"门3<br/>Boss 入金并启用"}
  M3 --> L1["实盘首周<br/>1,000 U"]
  L1 --> L["补到 10,000 U<br/>满8周复盘"]
~~~

|阶段|做什么|过门条件|不过怎么办|
|---|---|---|---|
|1 协议与冻结规则|demo 实测止损、换保护、减仓、均价、两波并发；6 项金样本对齐冻结研究|G1–G5 证据 PASS；金样本 GC1–GC6 PASS|保护方式不成立 → 回设计，不进阶段2|
|2 两个任务|SQLite、调仓任务、守护任务、CLI；故障夹具先写、先红|41 个故障夹具全过；云端 Python 3.10 测试过；high 档审查无阻塞|修到全过；架构假设错 → 回设计|
|3 影子与准入|发布包、生产只读核对、同 IP 峰值权重、影子 1 个周一|G9 全 PASS；影子与研究内核逐项一致；Boss 入金并书面批准|不启用；修完再影子一个周一|

最早时间线按周一算：影子＝门2 通过后的第一个周一；首次实盘＝影子 PASS 后的下一个周一。

## 1. 前置条件

|#|条件|负责|何时需要|
|---|---|---|---|
|P1|批准本计划，并回复 §9 的 3 项默认取值（Q1–Q3 已按推荐确认，2026-10-20；开工已批准）|Boss|阶段1 开工前|
|P2|~~demo API key~~ 2026-11-15 取消：不用 demo。探针用 P6/P7 的生产子账户和 key|—|—|
|P3|探针用小额资金：子账户先入约 200 USDT（§13 修订 2）|Boss|Task 1.4 前|
|P4|实现 worktree 和分支（§2）|Kiro|阶段1 开工|
|P5|v2 冻结包在主 checkout 未纳入 git，本分支没有。金样本从主 checkout 抽取，并逐个核 SHA256|Kiro|Task 1.1|
|P6|生产专用子账户；API key 只开读取＋U 本位合约交易，关提现和划转，IP 白名单只含云端出口 IP。子账户须开通 U 本位合约|Boss|Task 1.4 前（2026-11-15 提前）|
|P7|生产 key 文件放云端 `/root/.momentum_ls01/binance.json`（目录 0700，文件 0600）。CC 给命令模板，不经手明文|Boss|Task 1.4 前（2026-11-15 提前）|
|P8|专用 env 文件，只含 `TELEGRAM_BOT_TOKEN`、`TELEGRAM_CHAT_ID`（复用现有 bot 和私人 chat）。由脚本从 Finance `.env` 抽取，不打印值|Kiro，Boss 批准|Task 3.1|
|P9|每次发布和每次 crontab 变更|Boss 批准|阶段3|
|P10|首个实盘周一前，子账户余额补到 1,000 USDT 并确认为基线；干净跑完一周后补到 10,000 USDT（§13 修订 3）|Boss|Task 3.6（影子可不入金，见 §9 Q3）|
|P11|（可选）codex 侧第二道存活检查|Boss|上线后任意时间|

## 2. 开发约定

- worktree：`<主 checkout>/.worktrees/momentum-ls-01-impl`（2026-11-15 重建；原 `~/.codex/worktrees/` 下的目录已丢失）；分支 `codex/momentum-ls-01-impl`，从 `codex/momentum-ls-01-live`（`5e2cb152`）切出。每个 Task 一个 commit。分支 push 到 origin 做备份（Boss 2026-11-15 批准）；不 merge，等 Boss 指示。
- 证据：小证据（goldcheck 结果、探针结果 JSON）提交到 `docs/design/momentum-ls-01-live/evidence/`。大的回放数据放主 checkout `data/momentum_ls01_evidence/`（gitignore，不随 worktree 删除），并在提交的清单里记 SHA256。
- 代码：`trading/momentum_ls01/`。测试平铺：`tests/test_momentum_ls01_*.py`。假交易所：`tests/momentum_ls01_fake_exchange.py`。小夹具：`tests/fixtures/momentum_ls01/`。
- 不加新依赖。只用 requests、pandas、numpy、sqlite3、decimal、concurrent.futures、fcntl。
- 语法兼容云端 Python 3.10：不用 3.11+ 语法和 f-string 内反斜杠。
- 金额和数量用 Decimal。信号纯函数沿用研究的 float 口径，不重写指标。
- 日志、证据、数据库不存 API key、secret 和签名。
- 本机测试命令：`"/Users/owen/CC workspace/Finance/.venv/bin/python" -m pytest tests/test_momentum_ls01_*.py -q`。
- 每阶段末做一次 `/code-review high`（2026-11-15 起由 CC 执行）。

## 3. 模块与存储（本计划冻结）

### 3.1 模块

|文件|职责|复用（已 grep 核对）|
|---|---|---|
|`config.py`|规则版本、规则哈希、§4 全部参数|—|
|`kernel.py`|周 Fisher、选币排名、目标仓位、两波拆分、成本核对|`HoldingPolicy`（`scripts/crypto_holding_policy.py:13`，`ranks` :75，`can_enter` :109，`__call__` :126）；`score_signal_frame`（`scripts/crypto_trend_rankings.py:86`）；`fisher_transform`（`src/indicators/fisher.py:46`）；移植冻结 `weekly_regime`（`source/v1/crypto_fisher_regime.py:7`）、`AsymmetricFisher`（`source/shared/scripts/crypto_momentum_backtest.py:23`）、`Policy` 加倍包装（`source/v1/run_study.py:10`）|
|`market_public.py`|准备阶段公开行情适配器（§9 Q1 默认）|满足 `TrendMarket`（`scripts/crypto_trend_market.py:86`）所需的 scanner 接口；走 `binance.py` 限频|
|`binance.py`|签名 REST 客户端：demo / 生产地址、签名、超时、权重头、429/418、错误分类|—|
|`exchange_rules.py`|exchangeInfo 过滤器、tick/step 舍入、最小名义、合约状态、deliveryDate|—|
|`store.py`|SQLite 四表、WAL、事务、进程锁、备份|—|
|`actions.py`|请求生命周期、客户端 ID、未知恢复、"确认未受理"|—|
|`protection.py`|止损价、先挂新后撤旧、越线即退、拒单分类、D1 权限门|—|
|`reconcile.py`|轻核对、全量核对、不变量、外部资金流、暂停|—|
|`prepare.py`|11:00 准备任务|`build_report(periods=(14,))`（`scripts/crypto_trend_rankings.py:238`）|
|`rebalance.py`|12:00 调仓任务（§4.2 九步）|—|
|`guard.py`|分钟守护；`watchdog`；`alive`|—|
|`notify.py`|待发送通知表＋私人 Telegram|`send_message(channel="private")`（`src/telegram_bot.py:70`）|
|`cli.py`|`prepare` `rebalance` `guard` `watchdog` `alive` `backup` `status` `pause` `reconcile` `resume` `probe` `goldcheck`|—|

主分支 `scripts/crypto_momentum_backtest.py` 已没有 `AsymmetricFisher`。所以多空方向映射和"看多时空头加倍"必须移植进 `kernel.py`，再用金样本证明与冻结版一致。不从 `reports/` 直接 import。

### 3.2 SQLite 四表

库：`<state_dir>/state.db`，`PRAGMA journal_mode=WAL`，`user_version=1`。所有写入用 `BEGIN IMMEDIATE`。

|表|主键|字段|
|---|---|---|
|`weeks`|`anchor_utc`|`rule_version` `rule_hash` `release_commit` `prep_status`(ready/failed) `prep_error` `prep_started_at` `prep_finished_at` `signal_day` `input_hash` `fisher_week_start` `fisher` `trigger` `regime`(-1/1) `short_ranked_json` `long_ranked_json` `run_started_at` `cutoff_ms` `wallet_usdt` `upnl_usdt` `equity_usdt` `targets_json` `wave_b_scale` `status`(prepared/running/done/no_new/deviated) `deviations_json` `report_status`|
|`actions`|`id`|`client_id`(唯一) `kind`(order/algo/cancel/cancel_algo/leverage/margin_type) `purpose` `anchor_utc` `symbol` `side` `qty` `lane`(risk/protect) `request_json`(无签名) `state` `exchange_id`(条件单为 algoId) `child_order_id`(条件单触发后的子订单) `executed_qty` `avg_price` `error_code` `error_msg` `check_count` `replaces_client_id` `created_at` `sent_at` `last_checked_at` `terminal_at`|
|`state`|`key`|`value_json` `updated_at`。键：`episode:<sym>` `cooldown:<sym>` `protection:<sym>` `pause` `account_identity` `authorized_flows` `cursor:trades:<sym>` `cursor:income` `guard_heartbeat` `fail_streak`|
|`evidence`|`id`|`kind`(trade/income/snapshot/notification) `dedupe_key`(唯一) `symbol` `time_ms` `payload_json` `status`(通知用 pending/sent) `created_at`|

`purpose` 取值：`wave_a_reduce` `wave_a_close` `wave_b_open` `wave_b_add` `protect_new` `protect_replace` `protect_cancel` `stop_residual_exit` `crossing_exit` `fault_reduce` `fault_exit` `leverage_set` `margin_set`。

### 3.3 请求状态机

`PLANNED`（已入库）→ `SENDING` → `ACKED` 或 `UNKNOWN` 或 `REJECTED`。
`ACKED`/`UNKNOWN` → 查询 → `FILLED` / `PARTIAL_FINAL` / `CANCELED` / `EXPIRED` / `NOT_ACCEPTED`。
`PLANNED` → `ABANDONED`（只用于重启恢复，见下）。

- 整波先登记为 `PLANNED`。每个请求发出前，单独把它改成 `SENDING` 并提交，然后才发 HTTP。入库失败就不发。
- 所以 `PLANNED` 一定没发出过。`SENDING` 可能已到交易所，也可能没到。

重启恢复（任何进程启动时先做，早于任何新请求）：

|库里状态|含义|处理|
|---|---|---|
|`SENDING`|发出后崩溃，结果未落库|一律按 `UNKNOWN` 处理。用客户端 ID 查询（普通单查订单，条件单查条件单）。按"确认未受理"规则查清前，不重发|
|`UNKNOWN`|发出后结果不明|同上|
|`PLANNED`|从未发出|改为 `ABANDONED`，记偏差。不按旧计划补发。需要的保护和止损退出由核对按当前交易所事实重新生成|
|`ACKED`|已受理，未终结|继续查询到终态|

- `UNKNOWN` 继续占用所在通道的名额和本周预算。不凭超时重发。
- 只有 `NOT_ACCEPTED` 和 `REJECTED` 可以用新 ID 补发。补发关联前一条。
- 终态不再改写。重跑和恢复遇到终态直接跳过。
- 保护更替例外：新保护 `UNKNOWN` 时，不挂第三张。

## 4. 固定参数

|参数|取值|
|---|---|
|准备时间|周一 11:00 新加坡（03:00 UTC）。Boss 修好数据后可在 11:55 前手动重跑|
|首波准入期|12:00:00–12:05:00。超时不自动补跑|
|迟到补跑|Boss 当次批准；限当天 UTC 日内（新加坡次日 08:00 前）；先重跑准备；记偏差|
|统一收敛期限|首波发出后 10 分钟。到期未收敛的请求转持久暂停（原因：未知订单超期），守护继续查询|
|波A 未收敛|统一期限内波A 未收敛，波B 不发，本周记偏差|
|下单方式（2026-11-15 修订 4，取代"两条通道各 5 个在途"）|**串行，逐币处理。** 同一时刻最多 1 个新增请求在途。每个币走完"发单 → 查到终态 → 读回数量和 entryPrice → 挂或换保护并确认"，再处理下一个币。顺序：波A 先空后多，波B 先空后多，各按候选排名。不用线程|
|新增请求结果未知|某个新增请求成为 `UNKNOWN` 时，本波剩余新增请求暂不发，先查询。统一收敛期限内查清（成交或确认未受理）就继续；到期未清转持久暂停，剩余请求记偏差。已成交数量的保护、越线即退、止损残仓退出不受影响，照常发|
|在途的定义|只算"受理结果还没确认"的请求（`SENDING`、`UNKNOWN`）。已受理、正在等触价的条件单不算在途|
|调仓期查询频率|每 2 秒查一次未决订单和保护|
|新成交保护期限|成交确认后立即挂；60 秒内未确认生效 → 走北极星 §5.5|
|波B 保证金|所需初始保证金 ×1.10 > 可用保证金时，波B 全部开仓和加仓乘同一因子 f＝可用÷(所需×1.10)，按 step 向零取整，记偏差和 f（D6）|
|数量舍入|按 MARKET_LOT_SIZE stepSize 向零取整，不超目标。平仓用交易所读回的实际仓位全量|
|止损触发价|entryPrice×2，按 tickSize 向上取整|
|最小数量/名义|差额低于 minQty 或最小名义：跳过并记偏差。reduceOnly 平仓被最小名义拒：记录并告警|
|定价|调仓权益＝同一核对时点钱包余额＋标记价未实现盈亏。下单数量用核对后一次读取的全市场最新成交价|
|客户端 ID|`mls1-<yymmddHH>-<p>-<seq4>-<attempt>`，例 `mls1-26110904-B-0007-1`。p：A 波A、B 波B、P 保护、X 退出、F 故障、S 设置|
|确认未受理|发出后 ≥10 秒、≥30 秒、≥120 秒三次查询都返回"订单不存在"，且同期该币无新成交、仓位不变|
|轻核对|每分钟。持仓、普通挂单、条件单、账户保证金率。权重先估约 50/分钟，Task 1.3 实测回填|
|全量核对|每个整点＋每次调仓前。成交、资金流水、账户模式、逐币杠杆、key 权限、合约状态。先估约 300/次|
|升级持久暂停|守护连续 5 次运行失败|
|心跳|守护每次运行写心跳。调仓持锁期间，由调仓自己每 10 秒写一次心跳|
|心跳过期|`watchdog` **每分钟**运行，不取锁，只读库。心跳超过 3 分钟未更新就告警。最不利相位：3 分钟阈值＋1 分钟检查间隔＋运行时间，最后一次心跳后 5 分钟内必定告警|
|存活消息|每天 09:00 新加坡，由独立 cron 进程 `alive` 发出（D2）|
|SQLite 备份|每天 00:30，用 sqlite3 backup API，保留 30 份|
|现金核对截止|每次全量核对开始时刻记为 `cutoff_ms`；流水和成交只取 ≤ cutoff|
|D1 人工门|只有两种情形停下请求 Boss：一次故障动作涉及 ≥2 个币；或故障属于共同根因（见下一行），不论涉及几个币。**单币故障自动处理，包括账户里只剩这最后一只币的情形**（D1 原问题就是"最后一只币是否一律走人工门"，Boss 选"不一律"）|
|共同根因|同一次运行内 ≥2 个币保护被拒（非越线），或任一拒单属于签名、权限、IP、账户模式类错误|
|自限权重|已用权重 ≥ 上限的 75%（上限以 exchangeInfo 读回为准）：只发保护、止损退出、必要查询|
|429 / 418|429 按 Retry-After 退避，无头则 60 秒。418 停新增、告警、记录解封时间|
|HTTP|连接超时 5 秒，读取超时 10 秒，超时记 UNKNOWN。recvWindow 5000 ms；服务器时差 >1000 ms 停新增|

## 5. 阶段 1：协议与冻结规则

目标：在写完整系统前，证明三件事。一，数量型原生止损能挂、能换、能和减仓共存。二，交易所 entryPrice 符合冻结成本语义。三，实盘内核与冻结研究逐项一致。

### Task 1.0 规则来源同步与文档纠错（只改文档）

- 把主 checkout 的 v2 runbook `docs/runbooks/momentum-ls-01.md` 和 `momentum-ls-01-2026-10-19.v1.md` 同步进分支。本分支现存的 `momentum-ls-01.md` 还是 v1。
- 改掉 D1–D6 拍板后的过期措辞：`protocol-validation.md` G5（"推荐：波B 不发、持久暂停"，与 D6 相反）、G6、参数清单两行；`glossary.md` 波A/波B；`requirements.md` 页首 D4 句和"下一步"。
- 澄清北极星 §6.5 和 §9.2 的 D1 措辞："整个账户"指账户级共同根因，不指"只剩最后一只币"。最后一只币的单币故障自动处理。这是按 Boss 已选的"不一律"写清，不是新决定；改前给 Boss 看 diff。
- 验证：`grep -n "待 D\|待拍板\|若 D2 采纳\|推荐：波B 不发\|待 D4 确认" docs/design/momentum-ls-01-live/*.md` 只剩 `review.md` 的历史记录。两份 runbook 的 SHA256 与主 checkout 相同。

### Task 1.1 金样本夹具

- `goldcheck --frozen-dir <主 checkout>/reports/momentum-ls-01/2026-10-19.v2`：先按冻结 `manifest.json` 的 `sha256` 表（162 项）逐项核哈希，不符 exit 2。
- 从冻结包抽 6 个锚点的小夹具进 `tests/fixtures/momentum_ls01/`：首锚点 2025-02-03、一个看多锚点、一个看空锚点、一个止损后的锚点、一个 Fisher 翻转锚点、末锚点 2026-10-12。含当日信号行、Fisher 行、账本行、止损行。附来源哈希清单。总大小 <1 MB。
- 单元测试只读夹具，不依赖主 checkout 路径。全量对拍由 `goldcheck` 跑，结果存 `work/momentum-ls-01-protocol/goldcheck.json`。

### Task 1.2 策略内核 `kernel.py`

- `weekly_regime_live(btc_weekly, anchor)`：移植冻结 `weekly_regime`。另加显式门：应生效的周（锚点所在周一 00:00 UTC 结束的那一周）必须存在。缺失就报 `fisher_week_missing`，不沿用旧值。
- `LivePolicy`：继承 `HoldingPolicy`，加入冻结 `AsymmetricFisher` 的方向映射和 `Policy` 的空头加倍。每周一新建一次，`start=锚点`，注入 SQLite 的冷却表和交易所的退市时间，只调用一次。
- 冷却口径：所有非退市退出（止损、Fisher 转空平多、故障退出）都写 `cooldown:<sym>=实际退出时间`。冻结引擎止损写冷却在 `source/funding_engine.py:68`。
- `split_waves(current, target)`：返回波A（减、平）和波B（开、加）。出现同币反向就报错（冻结规则禁止同锚点换边，正常不会出现）。
- `cost_after_trade`：Decimal 版，口径同冻结 `average_cost_after_trade`（`source/v1/stop_engine.py:9`），只用于独立核对 entryPrice。

金样本（GC）：

|编号|对拍内容|通过标准|
|---|---|---|
|GC1|BTC 周线 Fisher：公开周线算出的 fisher/trigger/regime 对冻结 `regimes.parquet` 全部行|方向全同，数值差 ≤1e-9；缺周夹具必须报错|
|GC2|目标仓位冷启动：89 个周锚点，每个锚点用冻结的持仓、权益（由冻结账本和资金费流水重建）、开盘价、重建的冷却新建 `LivePolicy`|与冻结 `ledger.csv` 该锚点 `units_after` 逐币一致，相对差 ≤1e-9|
|GC3|成本：冻结账本全部行的 `basis_after`|Decimal 版结果一致|
|GC4|冷却：冻结 8 次止损。分两件事验。① 资格边界：止损所在锚点及之前，该币不能入场；第一个严格晚于止损的周一 04:00 UTC 起，冷却不再阻挡。② 实际入场：是否、何时重新入场，由排名和方向决定，以冻结账本为准，可以一直不入场|① 8 次全对；② 与冻结账本一致：LIGHTUSDT、ESPORTSUSDT（06-02 那次）在最早锚点入场，GRIFFAINUSDT、HUSDT、BSBUSDT、ESPORTSUSDT（06-11 那次）、AKEUSDT 晚于最早锚点，SPACEUSDT 再未入场。不得强制补开|
|GC5|选币池：`prepare` 核心对 2026-09-20、09-27、10-04、10-11 重算 14 日池|池集合、状态、score_v4（差 ≤1e-10）、排名与已发布存档一致|
|GC6|预算：看多 10×10% 空＋10×5% 多；看空 10×10% 空、多头全平；缺槽留现金|夹具逐项一致|

GC2 复用了 `HoldingPolicy` 本身。它证明移植、注入和冷启动接线正确，不独立证明 `HoldingPolicy` 正确。这正是目标：与冻结规则相同。GC5 存档位于研究目录 `crypto-recent-20261019/source/`。

### Task 1.3 交易所客户端与合约规则

- `binance.py`：demo 地址 `https://demo-fapi.binance.com`，生产 `https://fapi.binance.com`。key 文件权限不是 0600 就拒绝启动。每个响应读 `X-MBX-USED-WEIGHT-1M`。错误分为：已受理、明确拒绝（带错误码）、未知（超时、5xx、连接中断）。
- 条件单走 `POST /fapi/v1/algoOrder`（旧入口返回 -4120）。其他端点和参数名在本 Task 从官方交易页读出，写成常量表，附文档地址和读取日期。本计划不预设未核对的名字。
- 条件单查询要区分条件单自身状态和触发后生成的子订单（审阅指出官方字段为 `algoStatus`、`actualOrderId`，本 Task 读官方页确认）。子订单 ID 写入 `actions.child_order_id`。止损触发后先查这张子订单和它的成交，状态不明时不另发平仓（北极星 §5.6）。
- `exchange_rules.py`：解析 PRICE_FILTER、LOT_SIZE、MARKET_LOT_SIZE、MIN_NOTIONAL、PERCENT_PRICE、status、deliveryDate。舍入全部 Decimal。
- 测试：签名向量、舍入边界、错误分类、权重自限、429/418 退避。

### Task 1.4 生产小额协议探针 G1–G5（2026-11-15 修订 2；下方 demo 字样按此读）

- 不用 demo。探针在生产专用子账户上跑，从云端固定发布目录运行（key 只白名单云端 IP）。所以 Task 3.1 的发布目录和 Task 3.2 的生产只读核对提前到本 Task 之前做。
- 硬上限写进代码，不靠调用方自觉：单笔名义 ≤ 25 USDT；探针全程未平名义合计 ≤ 60 USDT；币种必须在命令行白名单内；账户有任何既有持仓或挂单就只读退出；生产写入需要显式开关，默认关闭。
- 每次运行前由 Boss 批准币种、数量和触发价。子账户先入约 200 USDT。预计手续费合计不到 1 USDT。
- G5 改测串行两波（修订 4）：不再测并发。
- 顺手多测一项备选：两张 `closePosition=true` 止损能否共存、第二张的拒单错误码。它不改变首选方案，只在 G2 失败回设计时省一轮探针。
- G4 的 key 权限读回直接在生产测，不再有"demo 不可测"分支。
- `probe g1|g2|g3|g4|g5 --env prod`。每个探针写 `work/momentum-ls-01-protocol/<run-id>/gN.json`：环境、时间、请求用途、非敏感参数、响应、最终订单和仓位、结论。
- 场景完全按[协议验收](../design/momentum-ls-01-live/protocol-validation.md) G1–G5 矩阵。重点三项：G2 先挂新后撤旧是否被接受；G3 挂着保护时 reduceOnly MARKET 减仓是否冲突、超量 reduceOnly 条件单是否只平到零；G4 entryPrice 加减仓语义。
- 每轮先确认 demo 账户空仓。探针结束撤光挂单、平光仓位。
- G4 的 key 权限读回若 demo 不支持，标"demo 不可测"，改到 Task 3.2 用生产只读测。
- 结果汇总进 `docs/design/momentum-ls-01-live/protocol-results.md`，并按 G2/G3 结论定死北极星 §5.2 "减仓换不换保护"。

### Task 1.5 准备阶段核心

- `prepare` 核心：`market_public.py` 取数 → `build_report(market, as_of, periods=(14,))` → 14 日行 → `score_signal_frame` → `LivePolicy.ranks`，加 Fisher 门。
- 输出：双向排序候选、Fisher 状态、输入哈希。阶段1 只写 JSON，不写库。
- 跑 GC5。

### 门 1（2026-11-15 修订 1：拆成 1a 和 1b）

**门 1a（离线）。** GC1–GC6 全部 PASS，阶段 1 单元测试全过。过了就开工阶段 2。2026-10-20 记录为全过；2026-11-15 重跑 GC2/GC3/GC4/GC6 通过，GC1/GC5 的原始数据随旧 worktree 丢失，须重取后重跑并把结果提交。Boss 2026-11-15 批准阶段 2 不等这次重跑；重跑结果是门 2 的必要条件。

**门 1b（生产探针）。** G1–G5 全部 PASS，Boss 审阅 `protocol-results.md`。门 1b 卡三件事：`protection.py` 的"减仓换不换保护"定稿、假交易所与实录的契约测试、门 2。门 1b 之前，`protection.py` 按保守默认实现：数量变化一律换成精确数量。

原条文（保留备查）：
- G1–G5 全部 PASS，或标"demo 不可测"并经 Boss 同意改到阶段3。
- GC1–GC6 全部 PASS。阶段1 单元测试全过。high 档审查无阻塞。
- Boss 审阅 `protocol-results.md` 后批准进入阶段2。

立即停下、回设计的情形：
- G2 先挂新后撤旧不成立，或 G3 挂着保护无法 reduceOnly MARKET 减仓。
- G4 entryPrice 与冻结成本语义不符，且无法解释。
- 系统无法把新币设为 2 倍全仓并读回。
- demo 不支持 Algo 条件单。此时请 Boss 选：生产最小名义实测，或回设计。
- GC2 或 GC5 不一致且原因不在本系统。

## 6. 阶段 2：实现两个任务

目标：两个定时任务、CLI、通知全部完成。北极星 §8 的六项证明全部有测试：未知不重发、重复调度不加仓、两波预算不透支、越线即退、残仓有保护、资金流不混入业绩。

### Task 2.0 假交易所与故障夹具（先写，先红）

- `tests/momentum_ls01_fake_exchange.py`：内存交易所。持仓、普通单、条件单、成交、资金流水、杠杆和模式。可注入：受理后丢响应、429、418、-2022、-4118、越线拒单、部分成交、ADL、强平、退市结算、外部转账、手动下单。
- 响应格式先按官方文档字段（`exchange-api.md`）写。门 1b 的生产探针实录到手后，补契约测试保证假交易所与实录同形（2026-11-15 修订 1）。
- 写下表全部夹具测试。此时应全部失败。

|#|场景|期望|门|
|---|---|---|---|
|F01|同一锚点调仓跑两次|第二次不发任何新增|G6|
|F02|04:00:02 启动|逻辑锚点记 04:00，实际时间另记|G6|
|F03|12:05 后才启动|不发新增，告警，等 Boss|G6|
|F04|周中重启|只恢复和保护，不按旧目标补开|G6|
|F05|守护与调仓撞车|锁互斥；守护退出 0 并记跳过|G6|
|F06|止损与锚点同刻|先处理止损；该币本锚点禁入；按序递补|G6|
|F07|部分止损后崩溃，价格回落|重启后继续 reduceOnly MARKET 到零，不重挂|G6|
|F08|准备失败（缺 Fisher 周或行情不全）|不新增；老仓和止损不动；告警；记偏差（D5）|G6|
|F09|请求已受理但响应丢失|记 UNKNOWN，查询恢复，不重发|G5/G6|
|F10|确认未受理|三次查询后 NOT_ACCEPTED，新 ID 补发一次|G5/G6|
|F11|波A 部分成交或拒单|剩余不追补；记偏差|G5|
|F12|波B 保证金不足|等比缩小，记偏差和 f（D6）|G5|
|F13|挂保护时已越线|reduceOnly MARKET 全平，按正常止损记|G1|
|F14|单币新保护被拒（非越线）|减回本周加仓；新开仓则平掉；记偏差（D1 自动）|G2|
|F15|两币同时被拒，或权限类错误|共同根因：停新增，请求 Boss，不逐币平|G2|
|F16|一次故障动作涉及 ≥2 个币|走人工门（D1），不拆成多笔单币退出|G2|
|F17|设杠杆失败|该币空槽，不递补，记偏差|G4|
|F18|杠杆读回不是 2 或不是全仓|不开该币|G4|
|F19|首次入金|只有 `resume --accept-flow <id>` 后才成为基线|G7|
|F20|未核验的转入或转出|持久暂停|G7|
|F21|资金费、手续费、实现盈亏重复出现|复合键去重，只记一次|G7|
|F22|守护漏跑；最不利相位（心跳刚写完就停，watchdog 刚检查过）|最后一次心跳后 5 分钟内告警，不依赖守护本身|G8|
|F23|429 / 418|退避 / 停新增并告警|G8|
|F24|写库失败|不发该请求；告警|G8|
|F25|通知失败|留 pending，下次重试；交易状态不变|G8|
|F26|持久暂停中出现止损残仓|照常退出；越线即退照常|G8|
|F27|Boss 在 App 手动下单或平仓|识别为外部操作；持久暂停（manual_takeover）；resume 前全量对账|G8|
|F28|持仓币公告退市|保留止损到结算；结算记退市退出，不当未知归零|G10|
|F29|已不可开仓的合约|不进候选，按序递补|G10|
|F30|资格内仍被拒开仓|空槽，记偏差|G10|
|F31|ADL 或部分强平|保留剩余仓位保护；按 G3 结论换或不换|G3|
|F32|entryPrice 与本地成本不一致|停新增，调查；不用本地值覆盖交易所|G4|
|F33|归零后旧保护残留|撤除确认前，该币不开新周期|G3|
|F34|查询无结果、entryPrice=0、事件缺该币|都不当归零证明；停新增|G6|
|F35|整波已登记 `PLANNED`，发出前崩溃|重启后全部 `ABANDONED`，记偏差，不补发；需要的保护由核对重新生成|G6|
|F36|已改 `SENDING`，请求没到交易所就崩溃|重启按 UNKNOWN 查询；按"确认未受理"规则判 `NOT_ACCEPTED` 前不重发|G6|
|F37|交易所已受理（含已成交），响应落库前崩溃|重启查到订单和成交，落为真实状态；成交数量立即被保护覆盖；不重发|G6|
|F38|（2026-11-15 改写）波B 第 3 个币的开仓结果未知|第 4 个币起不发；前 2 个币已各有保护；第 3 个币查清后，成交数量立即有保护；期限内查清则继续剩余币|G5|
|F39|零余额账户上做影子调仓（`--dry-run --assume-equity 10000`）|按模拟账户算出完整目标，D6 不触发；不发任何请求；不写 `state.db`；不形成基线|G9|
|F40|账户只剩 1 个币，它出现单币故障|自动减仓或退出，不停等（D1）；记偏差|G2|
|F41|条件单已触发，子订单状态不明|先查子订单和成交；不明时不另发平仓；终态后仍有残仓才 reduceOnly MARKET|G3|

### Task 2.1–2.8 实现（让夹具变绿）

|Task|内容|重点|
|---|---|---|
|2.1 `store.py`|四表、WAL、`BEGIN IMMEDIATE`、`fcntl.flock`（`<state_dir>/mls01.lock`）、备份与恢复|锁和 cron_wrapper 的 `/tmp/finance-cron-locks` 分开|
|2.2 `actions.py`|§3.3 状态机、ID、启动恢复、恢复查询、确认未受理、新增/保护两条通道|终态不可改写；保护通道不被新增占用|
|2.3 `protection.py`|止损价、先挂新后撤旧、越线即退、拒单分类、D1 门|新保护 UNKNOWN 不挂第三张|
|2.4 `reconcile.py`|轻核对、全量核对、外部资金流、手动操作识别、两档暂停|暂停不挡止损残仓和越线即退|
|2.5 `prepare.py`|写 `weeks`，只在写库时短暂持锁|失败写 `prep_status=failed` 并告警|
|2.6 `rebalance.py`|北极星 §4.2 九步；`--dry-run` 只写计划单不发；`--late-approved` 补跑|波A 收敛后才算保证金和波B|
|2.7 `guard.py`|每分钟轻核对；整点全量核对；心跳；`watchdog` 和 `alive` 不取锁|锁忙时退出 0|
|2.8 `notify.py` `cli.py`|通知表；`status` `pause` `reconcile` `resume [--accept-flow]` `backup`|`resume` 先跑全量核对|

### 门 2

- 门 1b 已过；GC1–GC6 重跑结果已提交（2026-11-15 修订 1）。
- F01–F41 全过。阶段1 测试仍全过。
- 本机全量测试：除本 worktree 已知的数据相关失败外，无新增失败。先记基线数。
- 云端：把候选 commit 的 `git archive` 解到临时目录，用 `/usr/bin/python3` 跑 `py_compile` 和 `tests/test_momentum_ls01_*.py`，全过。不改云端任何任务。
- high 档审查无阻塞。Boss 批准进入阶段3。

## 7. 阶段 3：影子与准入

目标：生产环境就绪，影子 1 个周一（D3）逐项对上，Boss 入金并启用。

### Task 3.1 发布包与目录

- 发布：`git archive <commit>` 解到云端 `/root/workspace/Finance-jobs/momentum-ls-01-<短commit>/`。两端 SHA256 一致。做法同 Whole Port（`docs/runbooks/whole-port-daily-snapshot.md`）。
- 状态目录：`/root/workspace/Finance/data/momentum_ls01/`（0700），含 `state.db`、`logs/`、`evidence/`、`backups/`、`.env`（P8）。`data/` 已被 gitignore，自动 git pull 不碰它。
- 影子期另用 `/root/workspace/Finance/data/momentum_ls01_shadow/`。影子期所有任务只写这里。正式 `state.db` 在 Task 3.6 才新建，从空库开始。
- `config/settings.py:12` 只读发布目录里的 `.env`，发布包里没有。Telegram 变量由 `FINANCE_ENV_FILE` 经 cron_wrapper 注入。
- 每条周记录写入发布 commit 和规则哈希。

### Task 3.2 生产只读核对

`reconcile --full --readonly --env prod`：只读，不下单。核对：
- key 权限读回：无提现、无划转、限 IP、仅合约交易。
- 账户身份是专用子账户；单向持仓；USDT 单资产；全仓。
- 服务器时差、签名接口可达、exchangeInfo 限频上限。

### Task 3.3 crontab（影子版）

- 先导出 `crontab -l` 到带时间戳的备份。只追加一个带标记的块。用 `crontab <file>` 安装。安装后与候选文件逐字比对。不改其他任务。
- 影子版 cron 块（时区与新加坡同为 UTC+8）：

|时间|命令|
|---|---|
|`0 11 * * 1`|`prepare`|
|`0 12 * * 1`|`rebalance --dry-run`（影子期）|
|`* * * * *`|`guard`（影子期 `--readonly`）|
|`* * * * *`|`watchdog`（不取锁，只读库）|
|`0 9 * * *`|`alive`|
|`30 0 * * *`|`backup`|

每行都经 `scripts/cron_wrapper.sh`，从发布目录运行。

### Task 3.4 影子周一与同 IP 权重

在同一个周一完成：
- 08:00–12:00 每分钟用 1 次最轻的只读请求采样 `X-MBX-USED-WEIGHT-1M`，记录同 IP 既有任务峰值。
- 11:00 真实准备。12:00 `rebalance --dry-run`。输出计划单文件，不发单。账户取值按下表：

|项|已入金|未入金（`--assume-equity 10000`，§9 Q3）|
|---|---|---|
|钱包余额、未实现盈亏、权益|交易所读回|模拟：10,000 / 0 / 10,000|
|可用保证金（D6 用）|交易所读回|模拟：10,000|
|持仓、挂单、条件单|交易所读回|模拟：全空|
|key 权限、账户模式、逐币杠杆和保证金类型|交易所读回|交易所读回（空账户也能读）|
|行情、合约规则|交易所读回|交易所读回|

  模拟值在影子库里标 `simulated=1`。它不进正式 `state.db`，不形成资金基线。
- 独立对拍：另一条路径算同一锚点。用云端 Quant 扫描器跑 `build_report`，再用冻结 `Policy`（从冻结 `run_study.py` 读出执行，做法同冻结 `run.py`）。用同一权益比较候选顺序、方向、币种、目标名义和舍入后数量。

影子周内同时演练：
- 通知：发 1 条测试消息到私人 chat。
- 守护漏跑：在影子状态目录放 `drill-stop-guard` 标记文件。只读模式的守护见到它就不写心跳；正式模式忽略这个文件。通过标准：最后一次心跳后 5 分钟内收到告警。演练后删除标记文件。
- 存活：09:00 `alive` 消息到达。
- 备份恢复：备份恢复到临时库，`PRAGMA integrity_check=ok`，四表行数一致。

### Task 3.5 G9 判定

|项|通过标准|
|---|---|
|固定发布目录|cron 只从发布目录运行；一次自动 git pull 后发布目录文件哈希不变|
|状态分离|状态目录在发布目录外；换版本演练不丢状态|
|密钥|Task 3.2 全过；日志和证据 grep 不到 key|
|同 IP 权重|既有峰值＋本系统调仓估算峰值 ≤ 上限 60%。超过 → 回设计讨论独立出口 IP|
|影子|Task 3.4 对拍逐项一致|
|通知与存活|测试消息、watchdog 告警、alive 消息都收到|
|备份|恢复演练通过|

### Task 3.6 Boss 启用

1. Kiro 新建正式状态目录和空 `state.db`。影子目录原样保留作证据，正式任务不读它。
2. Boss 把子账户余额补到 1,000 USDT（2026-11-15 修订 3；探针剩余资金计入）。
3. Boss 运行或批准 `resume --accept-flow <tranId>`，这笔入金成为基线。
3a. 首个实盘周一按约 1,000 USDT 权益运行。一周内无工程失败（北极星 §8 定义）且全量核对干净，Boss 再转入约 9,000 USDT，同样经 `resume --accept-flow` 确认。有工程失败则不加资，修完再跑一个周一。8 周复盘从首个实盘周一起算。
4. Boss 书面批准发布 commit 和 cron 变更。
5. Kiro 备份 crontab，把 `rebalance --dry-run` 改成 `rebalance`，`guard --readonly` 改成 `guard`，状态目录指向正式目录，安装后逐字比对。
6. 首个实盘周一由 Boss 在场观察。

### Task 3.7 对照组回放（§9 Q2：不作为上线门）

- 离线脚本：冻结引擎 v2 `with_funding`（threshold=1.0、execution=ohlc），从上线锚点空仓起跑，用实盘初始 NAV。标记变体 "flat-start@上线锚点"。
- 输入用实盘每周冻结的候选和方向，加公开 4h 行情、历史资金费、止损分钟数据。
- 首个实盘周报前完成。周报单列实盘相对对照组的偏差。

### 门 3

- G9 全部 PASS。Task 3.6 第 2–4 步由 Boss 完成。
- 未通过：不启用。修完后再影子一个周一。

## 8. 上线后

- 每周报告：结果、偏差（D4–D6 及其他）、守护覆盖率、执行完整率、滑点、手续费、资金费、保护覆盖、异常次数、人工维护时间、相对对照组偏差。
- 满 8 周做正式复盘。只出继续、修订或结束的建议，由 Boss 决定。不建提醒自动化。

撤销方法：
- 停新增：`cli pause`。原生止损留在交易所。
- 停系统：从新导出的 crontab 删掉标记块再安装。仓位和止损不动，状态库保留。
- 全部退出：Boss 在币安 App 操作，或批准一次账户级故障全平。

## 9. Boss 已确认（2026-10-20，三项均按推荐）

|编号|问题|Boss 确认（推荐）|未采用的替代|
|---|---|---|---|
|Q1|准备阶段从哪里读行情|**自建公开行情适配器** `market_public.py`。走交易任务自己的限频（北极星 §6.6 要读权重头）；代码随发布包固定；用 GC5 证明与研究同结果|复用云端 Quant 扫描器模块 `binance_pmarp_scanner`。少写代码；但它不在 Finance 仓库，不随发布固定，也不处理 429/418|
|Q2|对照组回放是否作为上线门|**不作上线门**，首个实盘周报前完成|作为门3 的一项，上线推迟到工具完成|
|Q3|影子周一前是否必须入金|**不必**。生产 key 和子账户先开好。影子用模拟账户：权益和可用保证金都按 10,000，持仓全空；权限、模式、杠杆、行情仍读真实值（Task 3.4 表）。模拟值只进影子库|先入金，影子用真实权益和真实保证金|

## 10. 自审记录（写计划时核到的事实）

1. 主分支 `scripts/crypto_momentum_backtest.py` 已没有 `AsymmetricFisher`，只有冻结副本有。→ Task 1.2 移植＋GC2。
2. 主分支 `scripts/crypto_trend_rankings.py` 与冻结副本不同。diff 只涉及取数兜底和榜单发送，评分纯函数未变。`crypto_holding_policy.py`、`crypto_trend_metrics.py`、`fisher.py` 与冻结副本哈希相同。→ 可复用，GC5 兜底。
3. `binance_pmarp_scanner` 不在 Finance 仓库，只在云端 Quant 目录。→ §9 Q1。
4. v2 冻结包在主 checkout 未纳入 git；本分支没有。→ Task 1.1 抽小夹具并核哈希。
5. 本分支 runbook 仍是 v1；主 checkout 已是 v2。→ Task 1.0。
6. `protocol-validation.md` G5 仍写"推荐：波B 不发、持久暂停"，与 D6 相反；术语表和需求页首仍写待定。→ Task 1.0。
7. `config/settings.py:12` 读项目根 `.env`，发布目录里没有。→ 环境变量走 `FINANCE_ENV_FILE`（Whole Port 先例）。
8. cron_wrapper 的锁按任务名分开，挡不住守护和调仓互撞。→ 自带 `flock`。
9. 条件单接口已迁到 `POST /fapi/v1/algoOrder`。其他端点名未在本计划写死。→ Task 1.3 读官方页后固定。

## 11. 审阅修订（2026-10-20，外部审阅 2×P1、3×P2、1 项歧义）

主线程逐条核实后全部接受。

|级别|发现|处理|
|---|---|---|
|P1|`SENDING` 崩溃窗口未定义，`PLANNED` 重启处理未定义|§3.3 加重启恢复表：`SENDING` 一律按未知查询，查清前不重发；`PLANNED` 改 `ABANDONED` 不补发。新增 F35–F37 三个崩溃窗口夹具|
|P1|全局在途上限 5 可能被开仓占满，堵住保护|§4 分新增和保护两条通道，各 5 个，新增不能占保护通道；定义"在途"只算受理未确认的请求，挂着等触价的条件单不算。新增 F38|
|P2|未入金影子只给权益，没给可用保证金，D6 会缩到零|Task 3.4 加账户取值表；影子用独立目录，模拟值标记且不进正式库、不形成基线。新增 F39|
|P2|GC4 混淆冷却资格和实际入场|已核冻结账本：8 次止损只有 2 次在最早锚点入场，SPACEUSDT 再未入场。GC4 分两项验，不得强制补开|
|P2|心跳 5 分钟＋每 5 分钟检查，最坏近 10 分钟才告警|watchdog 改每分钟、阈值 3 分钟，最不利相位 5 分钟内告警；调仓持锁时自己写心跳；F22 和演练按此验收|
|歧义|D1"整个账户"是否包含"最后一只币"|按 Boss 原选"不一律"：最后一只币的单币故障自动处理；人工门只在 ≥2 币或共同根因时触发。§4 和 F16 已改，新增 F40；Task 1.0 澄清北极星措辞。**Boss 2026-10-20 确认：最后一只也自动平**|
|附带|条件单与触发子订单要分开查|Task 1.3 和 `actions.child_order_id`；新增 F41。字段名 Task 1.3 读官方页确认|

## 12. 执行进度（2026-10-20）

- [x] Task 1.0：来源同步与 D1–D6 文档纠错。
- [x] Task 1.1：162 项哈希、6 个独立小夹具。
- [x] Task 1.2：策略内核；GC1–GC4、GC6 全量通过。
- [x] Task 1.3：客户端与合约规则离线实现；真实私有权重待 demo 回填。
- [ ] Task 1.4：G1–G5 基础探针代码和离线测试完成；demo 未运行，完整矩阵待执行。Boss 已告知 demo 稍后提供。
- [x] Task 1.5：准备核心、CLI、GC5 全池重放。
- [ ] 门 1：未通过。不得进入阶段 2。

证据、测试与剩余项见 [protocol-results.md](../design/momentum-ls-01-live/protocol-results.md)。实现分支 `codex/momentum-ls-01-impl`。未 merge、push、部署或下单。

## 13. 修订（2026-11-15，CC 审核，Boss 全部按推荐批准）

背景：阶段 1 离线部分 10-20 完成后停了 26 天，卡在 demo key。Boss 11-15 指示不用 demo，直接按生产开发，并要求审核计划。CC 接手执行。

|#|修订|原因|落点|
|---|---|---|---|
|1|门 1 拆成 1a（离线）和 1b（生产探针）。1a 过了就开工阶段 2；1b 只卡保护模块定稿、契约测试和门 2|状态库、请求状态机、对账、通知、CLI 不依赖交易所实测。整段串行已造成 26 天空转|§0、门 1、Task 2.0、门 2|
|2|协议探针改在生产专用子账户小额运行，从云端发布目录跑|不用 demo。key 只白名单云端 IP，所以发布目录和生产只读核对要提前。生产证据比 demo 更硬，key 权限也只能在生产测|§1 P2/P3/P6/P7、Task 1.4|
|3|首个实盘周一约 1,000 USDT，干净一周后补到 10,000 USDT|影子周只算单不发单。没有 demo 后，调仓任务第一次真发单不应是全额|§1 P10、Task 3.6|
|4|下单由"每波并发、两条通道各 5 个在途"改为串行逐币|每周最多约 30 单，串行约 30 秒，远在 5 分钟准入期内。去掉线程和双通道；"每个新仓先有保护再开下一个"天然成立。这改了 Boss 10-19 定的"并发"，Boss 11-15 明确同意|§4、F38、Task 1.4 的 G5|
|5|分支 push 到 origin；小证据随代码提交，大数据放 worktree 外固定目录|证据目录没进 git，已随旧 worktree 丢过一次|§2|

修订 4 对北极星的影响：§4.2 第 5、7 步和执行规则里的"并发提交"改为串行；D4（固定两波）不变。北极星 §9.4 已同步。

未改的东西：交易规则、D1–D3、D5–D6、Q1–Q3、四表结构、请求状态机、41 个故障夹具中除 F38 外的全部场景。

## 14. 执行进度（2026-11-15 起）

- [x] 重建 worktree；冻结包 162/162 哈希一致。
- [x] 计划修订写入本文件并同步北极星、需求；分支已 push 到 origin。
- [x] 金样本重跑：GC1、GC2、GC3、GC4、GC6 通过。结果在 `docs/design/momentum-ls-01-live/evidence/goldcheck-20261115.json`。GC1 用 2026-11-15 重新拉取的公开 BTC 周线（SHA256 `daf93dd4…aea9e`，存主 checkout `data/momentum_ls01_evidence/`）。
- [ ] GC5 未重跑。回放目录（云端缓存副本 1,058 个文件＋补充日线）和生成它的步骤都没进 git，已随旧 worktree 丢失。要么重建回放目录，要么由 Boss 同意用影子周一的独立对拍（Task 3.4）代替。
- [x] Task 2.0：假交易所（自测 12 项）＋故障夹具 F01–F41（47 个测试，先红后绿）。
- [x] Task 2.1–2.8：`store` `actions` `protection` `reconcile` `prepare_job` `rebalance` `guard` `notify` `cli`。Python 3.10 语法检查通过。
- [x] 阶段末 `/code-review high`（2026-11-15）：10 条发现，全部核实成立并修复，每条先写失败测试（`tests/test_momentum_ls01_review.py`，20 个）。另有自查发现 3 条，同样先测后修。专项测试合计 218 项通过。
- [x] 全仓回归（修审查发现之前的版本）：12 failed / 4,947 passed / 4 skipped。12 个失败全在 `test_breadth_buy_quality` 与 `test_morning_report`，是裸 worktree 的数据依赖失败，与 10-20 基线同数。
- [x] 真实客户端只读冒烟：生产公开接口 exchangeInfo 的 522 个 USDT 永续合约规则全部解析成功；限频上限读回 2400。
- [x] 2026-11-16 外部审查复核：对照源码和官方文档逐条核实。成立的按 Boss 批准范围先测后修（`tests/test_momentum_ls01_review2.py`，30 个）；假交易所补上"已撤或已过期、无成交的条件单，创建 3 天后查不到"。不成立的未改（时钟 1ms 漏跑、精度尾零、冷缓存退市币、字符串错误码、DELETE 参数放 body）。修完再跑 `/code-review high`：10 条，9 条成立并先测后修（含本轮引入的回归：整点全量核对持续失败时不告警也不暂停），1 条不改（见 §14.2 第 10 项）。专项测试合计 254 项通过；全仓 12 failed / 5,001 passed / 4 skipped，12 个失败与基线同一批。
- [ ] 云端 Python 3.10 实跑测试（门 2 条件）。
- [ ] 生产小额探针模式（Task 1.4）与门 1b。等 Boss 的子账户和 key。

### 14.1 实现与本计划的差异

|项|计划写法|实际实现|原因|
|---|---|---|---|
|实盘开关|cron 写 `rebalance` 即实盘|`rebalance` 必须带 `--live` 才发单；不带就是影子运行|误敲命令不会下单|
|冻结信号存放|`weeks` 只有排名两列|评分后的完整信号行存 `evidence`（kind=`snapshot`，键含输入哈希）|调仓要用同一批信号重建 `LivePolicy`；四表结构不变|
|`evidence.kind`|trade / income / snapshot / notification|另加 `exit`（每次退出的原因和时间）|F28 要区分退市退出|
|`state` 键|见 §3.2|另加 `pending_flows`（待 Boss 确认的资金流）、`ip_ban`、`seq:<tag>`（客户端 ID 序号）|F19、F23、ID 唯一|
|保护被拒后|§5.5 第 1 步"停新增"|本次调仓剩余新增全部不发，记 `not_sent`|串行后只会先看到一个币被拒；先停再查最稳妥|
|数量文本|—|数量和触发价一律按定点小数发送|`str(Decimal)` 可能给出 `1.00E+4`，追踪请求时发现并加测试|
|换保护被拒且旧止损仍覆盖|§5.5 第 3 步减回后保留仓位|保留仓位，通知 Boss，1 小时后重试换保护|否则下一分钟守护会把仓位当单币故障平掉|
|挂止损遇 429/418|未写明|不算单币故障，不平仓；守护下一分钟重试；418 记 `ip_ban`|限频是 IP 级原因（审查 #1）|
|限频记账|本地预留和响应头取大值|响应头替换本次请求的预留|每个私有请求预留 40，取大值会让调仓在第 4 个币后被自己拦住（审查 #9）|
|拿不到进程锁|未写明|调仓最多等 90 秒，仍拿不到就告警|分钟守护与调仓在同一秒启动（审查 #4）|
|`no_new` 的周|终态|只要没登记过任何订单或设置，可重跑准备和调仓|否则准备失败修好后无法补跑（审查 #7）|
|每笔增仓发出前|未写明|重新核对暂停、止损是否已触发、该币是否账实一致；不满足就作废，记 `add_voided`|调仓期间止损触发后不得再加仓（审查 #6）|
|通知文本|—|转义 Markdown 字符；一条失败不挡后面的|原因代码里的下划线会让 Telegram 拒收并堵住全部告警（审查 #5）|
|`resume`|先全量核对|默认只结算未决请求、全量核对，通过才清暂停，不改本地账本。`--adopt` 才用交易所仓位和成本覆盖本地账本，且先结算全部已触发止损|旧版先覆盖账本再核对，会吞掉手动单和成本不符，并把暂停期间触发的止损算两次（Boss 11-16 选方案 A）|
|"停新增"|§5.5 第 1 步|只拦波 B。波 A 减仓和平仓照发；持久暂停才拦波 A|旧版一个空头换止损被拒或遇 429，就放弃全部多头平仓|
|调仓中途出错|未写明|任何异常都记 `run_failed` 并告警。没登记过订单的周记 `no_new`，可重跑；登记过的记 `deviated`|旧版周状态卡在 `running`，之后同周全部判重复|
|退出结果|—|退出后没平完就告警；越线退出未平完返回 `exiting`，该币本次不再交易；残仓退出被拒算守护失败|旧版一律当作已退出|
|`fault_reduce` 后|§5.5 第 3–4 步|减回后读仓位，旧止损仍盖不住就整币退出；减回结果未知就持久暂停|旧版不看减回结果，可能留下没有止损的加仓部分|
|守护退出码|—|单次失败退出 0，连续失败第 5 次（转持久暂停那次）退出 1|cron_wrapper 对非零退出每次告警，旧版每分钟一条|
|流水类型|交易类 4 种|另加退市结算 `DELIVERED_SETTELMENT` 和手续费返佣类 `COMMISSION_REBATE` `REFERRAL_KICKBACK` `API_REBATE` `FEE_RETURN`|这些钱由交易本身产生；北极星 §5.8 要求持有到结算，否则每次退市都会暂停（Boss 11-16）|
|准备写周|§4.1 写 weeks 时短暂持锁|算完再拿锁，锁内重查周状态|旧版不拿锁，重跑准备可能把已调仓的周改回 `prepared`|

### 14.2 已知未完成项（不影响阶段 2 夹具，上线前必须处理或由 Boss 明确放行）

1. **现金差额核对没做。** 现在只按流水类型识别外部资金流（非交易类流水 → 暂停，等 `resume --accept-flow`）。G7 写的"未知差额进入持久暂停"（钱包变动 ≠ 流水合计）还没实现。
2. **API key 权限读回没做。** 属 Task 3.2，需要另一个接口；`reconcile` 命令目前报 `NOT_CHECKED`。
3. **限频状态不跨进程。** 418 会写 `ip_ban` 并在下次调仓前拦住新增；429 的冷却只在当次进程内有效。
4. **周报只有摘要。** 状态、权益、目标只数、偏差代码。滑点、手续费、资金费、守护覆盖率等 §8 指标还没算。
5. **保证金率只读不报警。** 轻核对每分钟读账户，但没有设报警线（计划也没给数值）。
6. **条件单查询"不存在"的错误码**假定为 -2013，与普通订单相同；未经生产确认。若不同，未知的止损请求会在 10 分钟后转持久暂停并告警，不会静默。
7. **强平、ADL、结算的订单前缀**（`autoclose-`、`adl_autoclose`、`settlement_autoclose-`）来自官方文档，未经生产实录确认。
8. **门 1b 要实测的条件单与配置行为**（2026-11-16 审查留下）：触发中和触发后的 `algoStatus`（TRIGGERING / FINISHED）、触发后子单被拒时的返回；没交易过的币 `symbolConfig` 有没有行，以及设全仓会不会报 -4046；新账户 v2 `positionRisk` 是否返回零行；`positionAmt` 的字符串格式。
9. **换止损被拒后"旧止损仍覆盖"只比数量，不比触发价。** 加仓后成本变低、减回之后，旧止损的触发价可能高于 2 倍成本，系统保留仓位、每小时重试。是否改成"触发价也要不高于新线，否则整币退出"，等 Boss 定。
10. **`settle_action` 在写事务里查交易所**（最多 3 次 GET，每次超时 15 秒）。这样请求状态和成交入账在同一事务里，崩溃不会丢成交。守护和调仓都持进程锁，不会互相等；只有运维命令 `pause`、`backup` 会等，SQLite 等待上限已调到 60 秒。根治要把每个查询拆成"先查、再短事务写"，改动请求状态机，门 1b 前不做。
