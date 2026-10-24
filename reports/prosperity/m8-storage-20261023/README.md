# M8 存储验收 — 2026-10-23

状态：M8 实现与验收完成，含 Boss 验收后的修订（见“验收修订”）；全量测试无新增失败。M9 未实施。

## 验收修订（Boss 2026-10-23）

Boss 验收时补跑 `/code-review high`，并用真实快照只读冒烟。复现 4 处缺陷，以及输入归档在四方案间重复导致的容量问题。Boss 同意全部修复，并改为“每周一份输入”。

- **冻结时点**：写入路径要求引用的冻结参数在榜单日期已生效（与读取规则一致）。冻结参数按最新季度选取，不再按最晚冻结时间。
- **季末榜唯一**：同一方案、版本、季度只有一份季末榜，由部分唯一索引保证。
- **数值校验**：超大整数报 ValueError。
- **共享输入**：新增 `input_set`/`stock_input`。同一周四方案与冻结副本共用一份输入；同批各方案输入必须逐字段一致。表数七 → 九。

复现与真实数据证据：`review_repro.py`、`real_smoke.py`、`replay_smoke.py`、`real-*.json`。真实冒烟只读 `data/backups/prosperity/phase0-accepted-input-20260929/market.db`，结果写入随后删除的临时库；快照文件时间与大小未变。

| 真实快照 | 成员 | 往返相等 | 幂等 | 修订前 | 修订后 |
|---|---|---|---|---|---|
| live 2026-09-29 | 915，建包错误 0 | 是 | 是 | 33,955,840 B | 12,320,768 B |
| 回放 2024-08-17 | 817，建包错误 0 | 是 | 是 | 26,329,088 B | 9,973,760 B |

按真实 live 周 12.3 MB 线性估算：每年（52 周 + 4 份季末榜）约 **0.69 GB**；季末榜只增加评分行，实际略低。2021-09 起约 260 周历史重建约 **3.2 GB**。下文“体积与耗时”的合成数据结果已按修订后代码重跑。

修订测试：先写 10 个失败测试并确认按原因失败，原 65 项仍通过；实现后 **75 passed**，景气全套 **351 passed**。

## Codex 审核修订（2026-10-23）

Codex 审核报 1 项 P2：`get_frozen_params` 先用未校验的 `frozen_at` 筛选候选，再校验内容。冻结时间被改坏后，查询会返回 `None`，调用方会误判为冷启动并重新估参。

- 修复：候选按季末榜 as_of 从新到旧逐个完整校验，校验通过后才用 `frozen_at` 判断是否生效；找到第一个生效的即返回。损坏的候选一律报错。更旧的季度不影响结果，不再读取。
- 回归测试：`test_corrupt_freeze_time_is_an_error_not_a_cold_start`（冻结时间改晚 15 分钟、改到次日两种）先失败，修复后通过。
- 按 Codex 建议，两个回滚测试补上 `input_set`、`stock_input` 的行数断言。
- 结果：M8 **77 passed**，景气全套 **353 passed**，全量 **4851 passed、12 failed**，12 个失败与基线一致，无新增失败。

## 交付范围

- `src/data/prosperity_storage.py`：固定输入投影、DTO、深拷贝、数值规范化、完整性校验与内容摘要。
- `src/data/prosperity_schema.sql`：九张表；原始因子与方案值分列；存在位区分无键与 NULL；每周一份共享输入。
- `src/data/prosperity_store.py`：显式路径，默认只读；整批事务；不可覆盖榜单；冻结参数查询；独立发布回执。
- 四方案与同日周榜/冻结榜可整批写入。只读查询不使用当前方案注册表替换历史配置。
- 只使用临时数据库和合成输入。无真实数据库写入、外部 API、cron、发送或生产部署。
- 保留未提交 worktree diff；未 commit、merge、push。

## 验证证据

解释器：`/Users/owen/CC workspace/Finance/.venv/bin/python`。执行目录为本报告所在 M8 worktree。

| 检查 | 证据 |
|---|---|
| T1 首个 RED（存储模块缺失） | `t1-red.txt` |
| T2 RED（归档模块缺失） | `t2-red.txt` |
| T3 RED（save/get 缺失） | `t3-red.txt` |
| T4 RED（历史/冻结查询缺失） | `t4-red.txt` |
| T5 RED（发布接口缺失） | `t5-red.txt` |
| T6 集成 | `target-tests.txt`；集成测试首次即通过，没有伪造 RED |
| M8 目标测试 | 最终 77 passed（修订前 `target-tests.txt`：65 passed） |
| 景气全部回归 | 最终 353 passed（修订前 `prosperity-tests.txt`：341 passed） |
| 实施前全量基线 | `baseline.txt`：4774 passed、12 failed、4 skipped |
| 最终全量对照 | `full-tests.txt`（Codex 修订后重跑）：4851 passed、12 failed、4 skipped；`full-suite-comparison.json` 确认 12 个失败 ID 与基线完全一致，无新增失败 |
| 编译与版本 | `static-checks.txt`、`versions-before.json`、`versions-after.json` |
| 合成规模验收 | `benchmark.py`、`benchmark.json`、`benchmark.txt` |

执行命令：

```bash
python -m pytest tests/test_prosperity_storage.py tests/test_prosperity_store.py -q
python -m pytest tests/test_prosperity_*.py -q
python -m compileall -q src/data/prosperity_storage.py src/data/prosperity_store.py
python -m pytest tests/ -q
python reports/prosperity/m8-storage-20261023/benchmark.py
```

这里的 `python` 代表上面的绝对解释器路径。本机为 Python 3.13；额外用 `ast.parse(feature_version=(3,10))` 检查 Python 3.10 语法，不宣称已在云端 Python 3.10 运行。

## A1–A10 覆盖

| 验收 | 可观察证据 |
|---|---|
| A1 schema / 只读 | 缺库不创建；未知版本/残缺 schema/其它库/view-only/sqliteX 用户表拒绝；关闭后连接不可用 |
| A2 完整往返 | 四方案全部行、输入投影、参数、原始因子、方案值、z/权重/贡献、缺失原因逐字段比较 |
| A3 幂等 | 重复写入不增加行；0/0.0/-0.0 哈希一致；新旧混合批冲突回滚；保留首次计算时间 |
| A4 原子性 | 第三方案 score 插入触发 ABORT；冻结半批失败；KeyboardInterrupt；新增记录全回滚，旧批保留 |
| A5 冻结恢复 | 版本/日期/生效时点过滤；UTC offset 等价；同日周榜与冻结榜；跨周财报参数不变、预期参数变化 |
| A6 发布 | 无榜/已计算/已发布三态；失败无回执；重复确认保留首次时间；写回不清除发布状态 |
| A7 损坏拒绝 | 参数 JSON、明细删除/数值更改、存在位、in_scheme、冗余列、顺序及冻结依赖损坏均报错 |
| A8 两库隔离 | 临时 research/production 数据相互独立；无 market.db 访问 |
| A9 输入归档 | archive 必需键、EPS statement_fiscal、缺失原因保留；三表数值不存；真实 build_packet 链路兼容 |
| A10 打分重放 | 跨周读回 FactorRow 与冻结参数，调用现有 score_board，与原 BoardResult 比较 |

独立 SQL 核对测试直接查询数据库表，不 import 存储的 serializer/hash helper；核对成员集合、counts、NULL、z×weight、合成分、基础分、总分、rank、FK。它不声称独立验证整个评分模型。

## 独立审阅

当时运行时没有可调用的本地 `/code-review high` 入口，采用 2 个独立审阅代理作为替代：一个审归档契约，一个审 SQLite 事务/查询/发布。不是内置 code-review 工具的执行记录。Boss 验收时已补跑 `/code-review high`，结果见文首“验收修订”。

三处 P2 均已用失败测试复现、修复并由原审阅者复核关闭：

1. schema 判空必须包含 view 等用户对象；SQLite 系统前缀用 GLOB 字面下划线，防止误吞 `sqliteXuser_data`。证据：`review-fixes-red.txt`、`schema-prefix-red.txt`。
2. `listing_days` 是现有内核的原始年龄，允许负整数；存储层不改变历史 profile 的既有解释。证据：真实 build_packet → factor → score → store 的回归。
3. Scheme / FactorSpec 数值、整数与布尔字段严格区分；拒绝 `grade_strict=True`、布尔权重、布尔 min_quarters。证据：`config-bool-red.txt`。

## 体积与耗时

同一台机器、1000 股 × 4 方案 × 8 周，每股 16 季合成输入。**以下为验收修订（共享输入）后重跑的结果**：

- board：32 行；input_set：8 行；stock_input：8,000 行；stock_period / score 各 32,000 行；factor_value：320,000 行。
- checkpoint 后：94,838,784 bytes = **90.4 MiB**（修订前 228.8 MiB）。
- checkpoint 前 WAL：12,141,672 bytes。WAL 高水位受自动 checkpoint 影响，不当作每年线性增量。
- 关闭重开后再次读回所有榜单：35.19 秒。
- 按每年 52 周 + 4 份季末榜线性估算：**约 0.62 GiB/年**。合成输入比真实数据小，真实估算以文首真实快照为准（约 0.69 GB/年）。
- 两份备份加主库约 2 GB/年数据量，另需 WAL、临时备份与其它业务余量。这不是已核实的云端可用磁盘预算。

M9 生产部署前仍须核实云端磁盘与备份预算，并计入约 3.2 GB 的历史重建。没有引入删除历史的策略。

## 剩余边界

- M9 未实现：生产路径、锁、备份/同步、周更门控、冻结日判断、历史回放及发布协调。
- M10–M13 输出未实现；SQLite 回执不能与文件或外部发送原子提交。
- strict 三表重算仍依赖原始 vintage；approximate 不保证原始三表逐字节重放。
- 全量基线 12 个失败：7 个 breadth 测试（缺本地历史 CSV/价格样本），5 个晨报分类测试（期待细分类而当前环境返回旧分类）。不挂载真库，不改无关历史失败。
- 留给 M9 的审查项（不阻塞 M8）：读取时整榜复验偏慢（真实 live 一批写 25.7 秒、读 4.8 秒）；旧 code_version 的数据结构演进会让旧榜无法读取；两个进程并发初始化新库会报未处理错误。
