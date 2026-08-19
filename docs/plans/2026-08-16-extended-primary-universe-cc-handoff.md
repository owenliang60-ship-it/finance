# Extended Primary Universe — CC Handoff

> **日期**: 2026-08-16
> **接手对象**: Finance CC
> **状态**: 需求已确认；architecture Phase 2 尚未开始；禁止直接进入实现
> **Worktree**: `/Users/owen/CC workspace/Finance/.worktrees/extended-primary-universe`
> **Branch**: `codex/extended-primary-universe`

## 一句话任务

把当前 `$10B+` Extended Pool 升级为 Finance 唯一默认主股票池和讨论宇宙，补齐全池基础研究数据，保留显式 overlay 与按需昂贵数据，并在完成兼容迁移后软退役 Core Pool。

## Boss 已确认的决策（不可重新猜测）

1. **唯一主池**：选择原讨论中的 C——最终取消 Core Pool，Extended 成为唯一默认 base universe。
2. **严格定义**：Extended 继续严格保持 `$10B+`；NYSE/NASDAQ、active、排除 ETF/Fund。
3. **Overlay**：真实持仓、手动关注股、基准/行业 ETF 通过 overlay 加入具体任务，不污染 Extended 定义。
4. **数据范围**：Extended 全覆盖价格、市值、Profile、三张财报、派生 metrics、Earnings、FMP/yfinance forward estimates 与 Concept Registry。
5. **昂贵数据**：IV 和期权链需要时现场拉取；新闻、LLM 深度分析也不得因 base universe 扩大而自动全池运行。
6. **更新机制**：首次全量 backfill；以后按财报公布事件增量更新，周末做全池 coverage reconciliation 和漏数修复。
7. **PIT**：current tables 保持最新查询，同时新增不可变 fundamental vintage；上线后的历史排名必须严格可重放，上线前历史明确标记 approximate PIT。
8. **Core 退役**：两阶段软退役。先兼容层和调用方迁移、对拍、验收，再删除 Core 文件、配置和刷新逻辑。
9. **覆盖目标**：清洗后的可分析公司中，三张财报覆盖率 ≥95%，至少 8 个连续季度可用率 ≥95%，Profile ≥98%，forward 在确认有分析师覆盖的分母中 ≥95%。
10. **失败策略**：局部失败不阻塞整批；短期使用最后可信数据并显式 stale，超过一个正常财报周期则排除并告警；禁止静默 fallback。
11. **开发方法**：Boss 明确要求实施使用 **Superpowers TDD**。每个实现任务必须先写失败测试，再做最小实现和回归验证。

完整、已确认需求见：

- `docs/design/requirements.md`

## 当前真实数据覆盖（云端审计，2026-08-16）

| 数据层 | 总覆盖 | Extended 覆盖 |
|---|---:|---:|
| Extended membership | — | 1,003 |
| Daily price | 2,909 symbols | 969/1,003 |
| Historical market cap | 3,104 symbols | 969/1,003 |
| Income statement | 223 symbols | 202/1,003 |
| Balance sheet | 223 symbols | 202/1,003 |
| Cash flow | 223 symbols | 202/1,003 |
| `metrics_quarterly` | 223 symbols | 202/1,003 |
| FMP estimates | 1,092 symbols | 984/1,003 |
| FMP earnings | 1,091 symbols | 984/1,003 |
| yfinance forward estimates | 1,072 symbols | 1,003/1,003 |
| Profile JSON | 183 symbols | 171/1,003 |

关键运行事实：

- 当前周六 fundamental cron 默认只跑 Core：168 家约 28 分钟。
- 按当前串行 2 秒限速，首次补齐 Extended 三张表线性估算约 2.5–3 小时。
- 最新 FMP forward：1,094 targets，FMP 段约 81 分钟；完整 forward wrapper 约 109 分钟。
- `market.db` 约 887MB；云盘只余约 6.8GB，必须给 vintage/forward snapshots 做容量预算。
- `market.db` 云端独占写入，所有相关任务共享 `market_db_writer` 锁。
- 云端 Python 3.10。

## 本次对话暴露出的数据问题

此前以 2026 Q1 为观察点的 backward 景气排名只有 82 家，主要因为：

1. `$50B+` 非金融候选 275 个证券代码中，只有 97 个有季度财报；
2. 三张财报目前实质上只覆盖 Core 附近；
3. 原算法错误地用“季度结束日在 Q1”代替“截至 Q1 末已经公布”，既漏掉 12 月结季公司，也引入了 Q1 以后才披露的季度。

现有库若改用 `accepted_date/filing_date <= 2026-03-31`，可用样本从 82 调整到 90，但仍受财报横截面覆盖和 restatement leakage 限制。

因此新架构必须同时解决：

- 默认 universe 不一致；
- 三张财报覆盖不足；
- security identity 污染；
- as-of membership survivorship bias；
- filing availability 与 immutable vintage。

## 已建立的隔离环境

```text
Worktree: /Users/owen/CC workspace/Finance/.worktrees/extended-primary-universe
Branch:   codex/extended-primary-universe
Base:     main @ 8ca3da9
Baseline: 79 passed
```

基线命令：

```bash
/Users/owen/CC\ workspace/Finance/.venv/bin/python -m pytest \
  tests/test_extended_universe_manager.py \
  tests/test_update_data_scope.py \
  tests/test_market_store.py \
  tests/test_market_store_fmp_forward.py -q
```

主工作区存在大量 Boss 的未提交/未跟踪文件。**不要在主工作区开发，不要覆盖或整理这些文件。**

## CC 接手后的强制流程

### Stop 1 — 完成 architecture，不写业务代码

1. 读取 `docs/design/requirements.md`。
2. 审计 Core/Extended/Broad 的所有定义和调用方：`get_symbols()`、`UNIVERSE_FILE`、`extended_universe.json`、所有 cron、health check、晨报、深度分析、Options、Portfolio、Concept Registry、回测和研究脚本。
3. 研究并写入：
   - `docs/design/research.md`
   - `docs/design/glossary.md`
4. 对现有 `docs/design/north-star.md` 做 **Data Layer 定向修订**，不要另建互相竞争的 Finance 北极星。
5. 架构必须至少比较三种方案，并明确推荐：
   - 直接让 legacy `get_symbols()` 返回 Extended；
   - 新建统一 universe resolver，兼容层迁移后删 Core；
   - 保留双池但把 Extended 设默认（已被 Boss 否决，只作对比）。
6. 架构必须覆盖 current materialization、immutable vintage、security master、coverage manifest、三态/多态失败语义、事件驱动更新、staging/atomic promotion、writer lock 与容量治理。
7. 把架构图、数据流、需求覆盖矩阵、pre-mortem 和反向挑战写入北极星修订草案，交 Boss 审批。

### Stop 2 — 写实施计划，仍不写业务代码

架构经 Boss 审批后，用 `/writing-plans` 产出：

- `docs/plans/2026-08-16-extended-primary-universe-implementation.md`

Plan 必须：

- 对齐 `docs/design/north-star.md` 的 Data Layer 与 R1–R14；
- 有架构图、业务流程图、至少两个替代方案、风险自证、回滚方案、验收标准和细粒度 checklist；
- 精确列出每个受影响文件、测试文件、命令和预期结果；
- 拆分“代码完成 / merge / 云端部署 / 首次 backfill / cron 切换 / Core 删除”多个独立 stop point；
- 明确不允许 plan 审批前实施。

### Stop 3 — Boss 批注通过后，Superpowers TDD 实现

优先调用 `superpowers:test-driven-development`（或安装后实际暴露的同义 TDD skill）。每个 task 严格执行：

1. 写一个会失败的测试；
2. 运行并保存预期失败证据；
3. 写使其通过的最小实现；
4. 运行 targeted tests；
5. 跑相关回归；
6. 独立 review；
7. 小步 commit。

当前 Codex 会话已发起 `Superpowers` 插件安装请求，但截至 handoff 时尚未确认安装完成。CC 开始实现前必须检查实际 skill 是否可用；不可用则先让 Boss 完成安装，不要伪称已使用。

## 推荐模块顺序（供 architecture/plan 验证，不代表已审批）

1. **Security Master / Eligible Universe**：清除 ETF、Fund、优先股、重复 share class、vendor symbol 错配；输出可审计 denominator。
2. **Unified Universe Resolver**：`base=extended` + explicit overlays；贵价调用必须显式 scope。
3. **Coverage State Model**：`ok / not_applicable / provider_empty / fetch_failed / stale / identity_blocked`。
4. **Fundamental Current + Vintage Schema**：保留 current 查询性能，新增不可变采集版本和 as-of resolver。
5. **Chunked Backfill + Resume Manifest**：staging、幂等、分片、失败隔离、atomic promotion。
6. **Event-Driven Incremental Update**：财报事件触发 + 周末 reconciliation。
7. **Consumer Migration**：横截面研究先迁移；IV/options/deep analysis 改 explicit targets；逐一消除 legacy Core 依赖。
8. **Production Backfill & Verification**：覆盖率、8Q 连续性、身份、PIT、磁盘、cron 时序。
9. **Core Retirement**：只有全部调用方对拍通过后才删除。
10. **Docs & Memory**：更新北极星、ARCHITECTURE、CLAUDE、ongoing、L2 investing/system memory 与 runbook。

## 明确禁止

- 不得在 plan 审批前修改生产代码或 cron。
- 不得把 `get_symbols()` 一步硬切为 1,003 家后再补救昂贵调用方。
- 不得把缺失基本面默认为 0、neutral 或沿用旧值而不打 stale 标签。
- 不得用当前 Extended 成分直接回测历史排名并称为 PIT。
- 不得把 IV、期权链、LLM deep analysis 自动扩到全池。
- 不得直接在生产 `market.db` 上做无 staging、无备份、不可恢复的全量回填。
- 不得自动 merge、push、部署或修改 crontab；每一步单独让 Boss 审批。

## 接手启动命令

```bash
cd "/Users/owen/CC workspace/Finance/.worktrees/extended-primary-universe"
cc
```

启动后给 CC 的第一句话：

> 读取 `docs/plans/2026-08-16-extended-primary-universe-cc-handoff.md` 和 `docs/design/requirements.md`，从 architecture Phase 2 开始。先完成 research/glossary 和现有北极星 Data Layer 修订草案，不要写业务代码。实施阶段必须使用 Superpowers TDD。
