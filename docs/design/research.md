# Extended Primary Universe — 领域研究报告

> **日期**: 2026-08-18
> **性质**: 实施 plan 的研究素材（Boss 已简化流程：跳过 /architecture 仪式，直接 superpowers writing-plans → TDD）
> **来源**: 6 个并行研究 subagent（2 内部审计 + 4 外部研究），各 section 独立成文

---

## 1. 参考系统（外部研究）

### 1.1 CRSP / Compustat（CCM）— 学术界的 PIT 黄金标准

**永久标识**：CRSP 用 `PERMNO`（证券）/`PERMCO`（公司）；Compustat 用 `GVKEY`（公司）+ `IID`（证券）。两边关联是一张区间表 `crsp.ccmxpf_lnkhist`（`gvkey, lpermno, linktype, linkprim, linkdt, linkenddt`），本质是 SCD Type 2，查询用 `between(datadate, linkdt, linkenddt)` 做 as-of join。

**为什么不能用 ticker/CUSIP（实证）**：Ian Gow 实测，CCM 官方 link 能匹配但 CUSIP 匹配不上的记录 121,981 条，反向仅 3,271 条。CUSIP 在证券变更时会改。

**as-of join 锚点陷阱（实证）**：用 `datadate`（期末）vs `rdq`（公布日）做 join 锚点，94.5% 一致，但约 4,233 条观测分歧（并购重组导致 GVKEY 存活而 PERMNO 变）。**锚点日期必须由查询语义决定，不能全局定死。**

**vintage 存储模式**：Compustat Point-in-Time snapshot（1987 起）存 `datadate`（会计期）+ `pointdate`（该值被知晓日）+ value，**只在有原始值或发生变化时才落一行**（append-only + observed_at + change-only 压缩），不是每次采集全量快照。口径分三档：Preliminary（新闻稿）/ Original（首份完整申报）/ Current-Restated（最新重述）。

**security master 去污染**：标准做法是**白名单**而非黑名单 — `shrcd IN (10,11)`（普通股）+ `exchcd IN (1,2,3)`。ETF/ADR/REIT/优先股/封闭式基金被白名单自动排除。

### 1.2 Sharadar SF1 — 折叠双时态的极简派

六个 dimension = {AR, MR} × {Q, Y, T}。AR (As Reported) 按 SEC filing 提交日索引（官方定位适合回测）；MR (Most Recent Reported) 按财期索引、就地覆盖最新重述值。**不保存中间每一版修订**；官方承认重述冲击会全部堆到最后一个 MRQ 数据点上。增量更新靠 `lastupdated` 字段。弱点：纯 ticker-keyed，无永久标识体系，文档无法回答 ticker 变更如何处理。"primary class" 措辞本身即去污染规则（只保留主要股份类别）。

### 1.3 QuantRocket — 防前视做成 API 契约

分配 Sid（基于 OpenFIGI），securities master 是全系统脊椎（所有组件从数据采集到订单跟踪都用 Sid）。核心模式 `*_reindexed_like()`：研究代码只能通过"传入价格索引 → 返回已按 filing date **右移 1 天**并前向填充的对齐结果"的访问器读基本面，配 `period_offset`（取上一财期）和 `exclude_restatements` 参数。**PIT 能被执行是因为不合规的读法在 API 上根本不存在。**

### 1.4 Zipline asset DB — SQLite 规模的现成身份层 schema

整数 `sid` 主键 + `equity_symbol_mappings(sid, symbol, start_date, end_date)` 区间表 + `lookup_symbol(symbol, as_of_date)` as-of 解析。相邻区间归一化（前段 end 推到后段 start，时间轴无缝）。`split_delimited_symbol()` 把 symbol 拆成 `(company_symbol, share_class_symbol)` — share class 是结构化字段不是字符串后缀。退市不删行只封 `end_date`。

### 1.5 Norgate — dense boolean membership

成员历史返回按日期索引的布尔时序。对比结论：SQLite 单机场景区间表（SCD-2）远省于日频布尔矩阵（几千行 vs 380 万行），两种表达等价可互转。

### 1.6 开源"穷人版 PIT"样本

- **SEC DERA pipeline (HansjoergW)**：SQLite 只做索引层（`adsh, cik, form, filed, period`），数据落 parquet；每日检查 sec.gov 新 zip 增量。同一财期多次申报靠 `adsh` 区分，不做自动重述检测。
- **pit-fundamentals**：最小可行 PIT schema，long 格式，列 `period_end, first_filed, lag_days, original_value, latest_value, restated, qa_status`（折叠双时态：只存首次+最新+标志位）。基准数字：基本面平均期末后 **43 天**才公开。

### 1.7 ArcticDB — 全量版本化的另一极端

默认版本化每次写入，需要去重引擎 + 企业版 pruning/GC 才可持续 — 对单人 SQLite 项目选全量快照路线的警告。

### 1.8 vintage 模式选型直答

| 模式 | 谁在用 | 代价 |
|---|---|---|
| Full snapshot per collection | ArcticDB | 存储线性膨胀，需去重+pruning |
| **Append-only + observed_at（change-only）** | **Compustat PIT** | 查询需 window function 取 as-of 最新行 |
| 折叠双时态（首次+最新） | Sharadar、pit-fundamentals | 丢失中间修订版，无法严格重放 |
| 完整双时态 | 学术/合规系统 | 复杂度最高，单人项目过度 |

**结论**：单人 SQLite、~1,000 家季频规模，**append-only + observed_at + change-only** 最优：(1) 是商业 PIT 库的实际做法；(2) 唯一能严格重放的模式（折叠方案达不到 R7）；(3) 存储可控（change-only 后季频一年 ~24 万行量级）。配套：vintage 之上物化 current 表（双轨）；`(entity_id, fiscal_period, field, observed_at)` 唯一约束，新值等于上一版则跳过。

**矛盾信息并列**：Tidy Finance 从 SQLite 迁到 Parquet，但理由是跨语言可移植性（R/Python/Julia 混用），非性能或正确性。本项目纯 Python 单栈 + 事务性增量写入 + 唯一约束 + as-of 查询，恰是 Parquet 弱项、SQLite 强项，迁移理由不成立。

### 1.9 对本项目的六条启示

1. **身份层现在就和 ticker 剥离**（哪怕不引 FIGI）：整数 sid + symbol_mappings 区间表 + as-of lookup（抄 Zipline）；share class 结构化，"同一 company 下只保留 primary class"。
2. **vintage 用 append-only + change-only，接受 FMP 给不了历史 vintage**：FMP 提供 `fillingDate/acceptedDate` 但值是最新重述值，严格 PIT 起点 = 上线日（与 R7/R8 一致）。FMP 特定陷阱：`acceptedDate` 对应 10-Q 受理时间而非最早盈利事件（8-K 可能早数分钟到数天）。真历史 vintage 唯一免费路径是 SEC DERA。
3. **防前视做成 API 契约而非文档纪律**：唯一合法读取路径是访问器（filing date +1 天右移写死在里面），前视偏差从"需要 review 发现的 bug"降级为"写不出来的代码"。
4. **membership 用区间表（SCD-2），退市不删行只封口**：`linkenddt` 为空 = 仍有效应 impute 成最大日期；区间归一化避免时间轴出洞；`delisted_large_caps.json` 旁路应收敛进 `end_date` 字段，避免两个真相来源。
5. **as-of join 锚点日期必须显式**：filing date 锚点（基本面）、fiscal period end 锚点（因子）、membership 判断是第三种；resolver 接口把锚点语义作为必填参数。
6. **去污染用白名单不用黑名单**：CRSP/Sharadar/Norgate 三个独立系统同一答案。黑名单新品种出现时静默失效，白名单静默变严 — 后者失败模式远更安全。

（来源清单见文末汇总）

---

## 2. 技术选型 / 实现模式（外部研究）

### 2.0 一个改变决策的事实

FMP 三张财报响应本身带 `filingDate` + `acceptedDate` 字段。**"某季度财报何时对市场可见"这个 PIT 主成分不需要 vintage 重建，直接存 filing_date 即可**。vintage（observed_at）真正且唯一买到的是：防御 FMP 事后静默改写历史期数字（restatement/回填修正）。两件事成本差两个数量级，架构上分开对待。

### 2.1 vintage 存储

| 方案 | 机制 | 判定 |
|---|---|---|
| A. per-collection 全量快照 | 每次采集整体落库 | **否**：周频一年 ~3.1GB，8 个月撞容量线且撞线后 VACUUM 不动（单向门） |
| B. **append-only 行级 observed_at + insert-if-changed** | 主键 `(symbol, statement, fiscal_period_end, observed_at)`，值变化才插行 | **推荐**：存储只随实际修订增长（年修订率 5% ≈ 3MB/年）；Sharadar（reportperiod+datekey+AR/MR）和 Qlib PIT（date/period/value/_next 链表）都是这个骨架 |
| C. 独立 vintage 库文件 | current.db + vintage.db，ATTACH | **否**：WAL 下跨 ATTACH 库事务**不原子**（SQLite 官方），崩溃留半截状态 |

as-of SQL 手写易错（XTDB 文章的观察可信，其结论是软文）→ 缓解：as-of 逻辑封进 view + Python helper，业务代码永不手写时间条件。

三个时间戳语义（抄 Sharadar）：`fiscal_period_end`（哪期的数）+ `filing_date`（何时公开）+ `observed_at`（我们何时看到这版）。

### 2.2 backfill 断点续传

| 方案 | 判定 |
|---|---|
| A. **manifest/job ledger 表** | **推荐**：`backfill_jobs(symbol, statement, status, attempts, last_error, claimed_at, ...)`，`BEGIN IMMEDIATE` 原子认领，数据+状态同一事务；**唯一能表达"这家公司确实没有这张表"（负结果缓存）** — 扩展池必有一批 FMP 真空洞，纯幂等重跑每次都为空洞重烧 2s/call 配额 |
| B. 文件 checkpoint | 否：与 db 写入不同事务，崩溃不一致；多一个状态源 |
| C. 纯幂等重跑 | 作为 A 的补充（写入层 UPSERT 保证重放安全），不作唯一机制 |

注意已知盲点：状态机终态设防 — `failed`/`skipped` 必须是可枚举终态，不能无限循环。

### 2.3 staging → promotion

| 方案 | 判定 |
|---|---|
| A. **同库 staging 表 + 分块事务** | **推荐**：真正原子；块按"一个事务 < 20MB"（约 50–100 家）拆 |
| B. ATTACH 独立 staging 库 | 否：WAL 跨库不原子 |
| C. 写副本+整文件切换 | 否：需 2 倍磁盘；换 inode 让每日 pull 变全量传输 |

SQLite 官方约束：>100MB 事务 WAL 性能反转，>1GB 可能直接失败；长事务期间 checkpoint 无法运行。

### 2.4 容量治理

- **单库不拆** + `dbstat` 逐表监控 + **2GB 软上限告警**。SQLite 开发者立场：拆库无性能收益。
- 真实天花板：VACUUM 需最多 2 倍空闲空间 → 6.8GB 剩余下库体积实际按 2GB 封顶设计；压实用 `VACUUM INTO`（1 倍空间）或 `auto_vacuum=incremental`。
- 隐性约束：本地每日 09:00 pull 全量文件，库涨多少每日同步带宽涨多少。
- 容量风险 100% 来自 vintage 策略选择（基线三张表 8Q 仅 ~12MB，10 年 ~90MB）；行宽是估算，落地前用 dbstat 对现库同类表实测。

### 2.5 运维红线：backfill 与 writer 锁

3 小时 backfill **不能持锁跑全程**：(1) 长事务 WAL 性能反转；(2) checkpoint 饿死、WAL 无界增长；(3) 饿死 06:30 日常量价 cron。必须：分块 commit → 块间**释放文件锁** → 每若干块 `PRAGMA wal_checkpoint(TRUNCATE)` → 排在 06:30 之后 + "检测到锁被占用就整体让路"开关。

### 2.6 研究员推荐 vs R7 的调和

研究员建议"P1 只加 filing_date 列 + drift log，跑 3 个月再决定要不要 vintage"。但 R7（Boss 已确认）要求上线即有不可变 vintage。**调和**：change-only append-only 设计下 vintage 成本极低（~3MB/年），vintage 表本身就是 drift log（insert-if-changed 即 drift 检测），直接实现 R7 无需分期。filing_date/accepted_date 列照加（这是 R8 历史查询的锚点）。

---

## 3. 内部审计 — 数据基础设施现状（audit-data-infra，2026-08-18）

> 实测取自真库 `data/market.db`（929.6MB）及云端。命名纠正：三张财报表实际叫 `income_quarterly` / `balance_sheet_quarterly` / `cash_flow_quarterly`；market.db **没有 profile 表**（profiles 在 `data/fundamental/profiles.json`，183 只）；**没有任何 membership/universe 快照表**（两个 JSON 只存当前态，无历史）。

### 3.1 Schema 关键事实

- Schema 生成于 `src/data/market_store.py`：`_SCHEMA` 拼装 `market_store.py:214-579`，应用 `:645-649`。
- 三张财报表主键 `(symbol, date)`，**`filing_date`/`accepted_date`/`cik` 三列已存在且 100% 填充**（声明 `market_store.py:61/77/100`；实测 2,068 行 income 全非空）。→ **`accepted_date <= as_of` 过滤今天就能做，缺的只是 vintage 维度**。
- **主键无 vintage 维度**：`_build_create_table()` 在 `market_store.py:209` 无条件加 `PRIMARY KEY (symbol, date)`，restatement 静默覆盖不可恢复。
- **vintage 改造两颗地雷**：
  1. `_migrate_add_columns()` 只迁移 `metrics_quarterly`（`market_store.py:653-655`）；给三张财报表加字段线上表拿不到，且 `_convert_row()`（`:677-689`）静默丢弃未知 key — **新字段无声消失不报错**。
  2. `_VALID_TABLES` 硬白名单（`:594-607`），新表必须注册否则 `_validate_table()`（`:610-613`）抛 ValueError。
- 行数实测：三张财报各 ~2,05x-2,068 行 / 223 symbols；`fmp_estimates` 128,355 行 / 1,092 symbols（周频 snapshot_date 即 vintage 键）；`fmp_earnings` 85,055 行 / 1,091 symbols（现成事件触发源）。
- `historical_market_cap` 的 `(TEXT,TEXT)` 主键 autoindex 占 144MB > 表本身 109MB；`daily_price` 索引 100MB。887MB 里 ~250MB 是这个（不在本次范围）。

### 3.2 写入与锁

- 财报写入 `_bulk_upsert()`（`market_store.py:691-734`）：**每 symbol 一个事务**（`:712`）+ `INSERT OR REPLACE`（`:728-731`）— 提交粒度已是合理 staging 单元；缺 date 的行静默跳过（`:720-721`）。
- 值得抄的严格模式：`upsert_fmp_estimates()`（`:948-965`）**开事务前全量校验，坏行整批拒绝**。
- 锁 = flock 非阻塞：`scripts/cron_wrapper.sh:83-96`（FD 9 per-job 锁 + FD 8 resource 锁）；`LOCK_DIR` 在 `/tmp`（`:10`）。Busy 语义 `:71-81`：默认静默 SKIP exit 0；`=75` 则 Telegram 告警 + exit 75。
- **`market_db_writer` 锁只有 fundamental 与 forward 两个 job 持有**；日频价格线（06:30）和 broad 线纯靠时钟错开，无锁保护。
- WAL 已开（`market_store.py:640`）。备份：concept registry 用正确的 backup API（`build_company_concept_registry.py:1406-1429`）**但无保留策略**；`data_guardian` MAX_SNAPSHOTS=10 只管自己的 tar.gz。

### 3.3 采集器能力差距表

| 能力 | 状态 | 证据 |
|---|---|---|
| Scope 选择 | **缺失** | `update_data.py:141` 写死 `get_symbols()`（核心池）；`--scope` flag 只在 forward-estimates 接线（`:166`） |
| 限流 | 已有 | `fmp_client.py:52-57`，2s（`settings.py:129`） |
| 重试 | 部分 | 仅 timeout/429 重试 3 次（`fmp_client.py:67-88`）；**其他非 200 立即返回 None 零重试** |
| 空返回 vs 失败区分 | **缺失** | `get_income_statement()`（`fmp_client.py:304-311`）失败和空 payload 都返回 `[]` — R10 多态语义**不改 client 做不到** |
| 失败记录 | **缺失** | `fundamental_fetcher.py:153-244` 只数成功，无 manifest |
| 幂等性 | 已有 | INSERT OR REPLACE 对 current 安全；对 vintage 幂等过头（销毁旧值） |
| 断点续传 | **缺失** | 循环不写状态，崩溃只能全量重跑 |
| 分片 | **缺失** | 扁平 for 循环（`fundamental_fetcher.py:126/163/200/237`） |
| Staging | **缺失** | 直写生产表 |
| 熔断器 | **缺失** | API key 失效会烧完 5,015 次调用 |
| 事件驱动 | **缺失** | 纯周频；`cron_jobs.md:17-18` 的理由（"无财报日历权限"）**已过期** — `get_earnings_calendar()` 在 `fmp_client.py:336-350`，`fmp_earnings` 表有 85,055 行 announce_date |
| 历史深度 | 部分 | `limit=8` 默认（`fundamental_fetcher.py:144/181/218`）；client 未暴露 page/from/to，深度被单次 limit 卡死；实测 55 只恰好 8 季（limit-8 遗留） |
| metrics 重算 | 部分 | `compute_all_metrics()` 默认跟随 income 表 DISTINCT symbol，会自动扩池；**但 `:364-369` 循环无 per-symbol try/except**，一只异常中断全批 |

**`update_fmp_forward.py` + `fmp_forward_runs` 是全部缺失项的生产验证模板**：不可变 run manifest（`:414-420`）、拒重跑 complete（`:383-386`）、manifest 门禁 `--resume`（`:355-377`）、PIT 写窗守卫（`:333-343`）、20% 熔断（`:422-424`）、失败收尾器保证不留永久 running（`:428-473`）；manifest 不可变性在 store 层强制（`market_store.py:1134-1139`）。财报 backfill 应复用此模式。

### 3.4 data_health 绊线

11 项检查注册于 `data_health.py:429-441`；所有 per-symbol 检查分母来自 `_load_universe_symbols()`（`:74-83`）= **UNIVERSE_FILE 核心池**。切换后第 9 项会假报 100% 覆盖。**更糟：第 1 项在池 >200 只时硬 FAIL**（`data_health.py:106-107`），并经 `update_data.py:88-91` 非零退出 — **切换当天板上钉钉的绊线**。

### 3.5 cron 时序与周六时窗（云端日志实测）

```
Tue-Sat 06:30 finance_market_data   823-844s          [W 无锁]
Sat     08:30 finance_pool          116s              [W]
Sat     09:00 finance_broad_weekly  731s → ~09:12     [W 无锁; 拍 ~0.93GB 备份]
Sat     10:00 finance_fundamental   1686s → 10:28     [W 持锁 busy-rc 75]（五周内 1571→1686s 在爬）
Sat     10:45 finance_forward       6528s → 12:34     [W 持锁 busy-rc 75]（6072→6528s 在爬）
```

- **碰撞警告**：若 extended 基本面直接替换 10:00 job（28min → ~2.8h），10:45 forward 会 exit 75，**当周 PIT 快照永久丢失**（写窗守卫拒绝补拍）。
- **可行排期**：排到 forward 之后（周六 ~13:00 起有 ~66h 零竞争空窗至周二 06:25），或**周日**（更干净，解耦两个都在增长的运行时长）。
- 次级风险：broad_weekly 无 resource 锁，哪天超时跑进 10:00 会和 fundamental 相撞。
- 首次全量 backfill 调用预算：三张财报 3,009 次 = 100min；+profile+ratios 5,015 次 = **167min ≈ 2.8h**（实测 1,686s vs 理论 1,680s 吻合，线性外推可信）。

### 3.6 容量预算（dbstat 实测外推）

- 实测：三张财报+索引 1,079 B/symbol-quarter；+metrics 1,495 B。
- **current 表扩容是零头**：1,003 只 8 季 = 12MB；10 年 = 60MB。
- vintage 策略差三个数量级：change-only ~5MB/年 vs 朴素周频重拍 8 季 451MB/年 vs 重拍 40 季 2.25GB/年。标定：fmp_estimates 周快照 ~186MB/年。
- **真正的磁盘险情与本项目无关**：云端 6.7GB 可用，`market.db.backup-*` ×17 = **13.41GB 无保留策略堆积**（concept weekly sync 每周六拍一份 0.93GB 从不删）。**即使不做本项目，磁盘约 7 周内填满**。裁剪到 2-3 份可释放 ~12.5GB。
- 项目首年总占用 < 250MB（清理后微不足道）；VACUUM 需 ~1.9GB 空闲。

### 3.7 带进 plan 的四件事

1. `accepted_date` 已在库且填满 — 严格 PIT 过滤不需改 schema，只需主键加 vintage 维度；键要基于 filing/accepted date 而非 `date`（财季结束日）。
2. 财报 backfill 复用 `update_fmp_forward.py` 模板，不重新发明。
3. 切换日两条静默绊线：data_health >200 硬 FAIL；`_migrate_add_columns` 不覆盖三张财报表。
4. backfill 排在 forward 之后或周日；先清理云端备份堆积（前置条件）。

---

## 4. 常见坑 / 失败模式（外部研究）

> 可信度标注：[事实] 一手文档/学术/可验证数字；[观点] 业界经验总结；[推断] 基于本项目参数的推导。

### 4.1 数据语义类

1. **用 period_end 当"可用日"**（最高频最致命）：防御 = `first_filed <= as_of` 过滤 + shift test（全特征加 D 天延迟重跑，绩效大幅塌陷说明在吃时间精度）。
2. **用固定 lag 近似披露日有实测反例** [事实]：CrossSection issue #50 实测 Compustat：600+ 条 RDQ 早于期末（逻辑不可能=字段脏）、50,000+ 条 RDQ 晚于期末+90 天（3 个月 lag 假设下未公开）。提议解法：时序矛盾观测置 missing。Fama-French 6 个月惯例是保守极端且作者自认可能过时。
3. **Restatement leakage 量级 — 对本项目是好消息** [事实]：S&P 实测 US Top 1000 的 24 组回测中只有 4 组与 PIT 基准偏离 ≥5bp（美国大市值受严格披露时限约束）。[推断] $10B+ 美股池 restatement bias 已知、有界、可接受 — 但必须文档化 + 做一次敏感性测试，不能假装是 PIT。
4. **Survivorship** [事实]：CRSP 1926-2001 survivorship-free 7.4% vs biased 9.0%；Shumway：业绩性退市收益缺失需用 **−55%** 修正，Nasdaq 偏差是 NYSE/AMEX 的 4.7 倍，修正后 Nasdaq size effect 完全消失。仅把退市股加回名单只修一半 — 最后一跌的 delisting return 仍缺失。
5. **用当前市值阈值定义历史宇宙** ← 本项目最直接的坑：每个日期 t 的合格宇宙只能用严格早于 t 的信息决定；membership 表存 `(symbol, effective_from, effective_to, reason)`，由当时市值决定。
6. **Reconstitution bias**：入池前的超额收益不可计入；`effective_from` = 市值数据可得且穿越阈值那天。
7. **Ticker 复用** [事实]：Visa 2008 年起用 `V`，此前属于已退市的 Vivendi。主键绝不能是裸 ticker；本项目最接近的稳定键是 SEC CIK；FMP 有 Symbol Changes API 可构造 ticker→实体时间映射。
8. **Missing 被当成 0**：铁律"0 永远只代表真零"；数值列允许 NULL + `source_status` 列区分真零/vendor 空/未采集。
9. **口径漂移** [事实]：FMP 自己承认"一次财报事件很少以一行干净记录存在"（8-K、10-Q、预期观测时刻全不同）。防御：每条记录四个独立日期列（`fiscal_period_end` / `earnings_announce_date` / `filing_date` / `accepted_date`）永不复用。本地实例 GLW（issue036）/ KLAC（issue035）应作为 backfill 回归测试固定用例。

### 4.2 工程类

10. **Backfill 时间预算低估**：checkpoint 三要素（step 标识/输出/状态）；先跑 canary backfill 再放量；**FMP Bulk Endpoints（CSV 批量）可能把 N 次调用压成 1 次 — 写单条循环前先评估**。
11. **Silent success（HTTP 200 + 空数组）** [事实]：FMP 用户实测有下载中断/账号冻结/带宽限制投诉。三条硬规则：(a) 空返回区分"确认无数据"与"未知"，落 `fetch_status`；(b) 空返回不进 success checkpoint，进 retry 队列；(c) 跑完后覆盖率断言（<95% 即中止报警），不靠人眼看日志。
12. **非幂等 backfill**：重跑翻倍 = 不幂等。注意：不可变 vintage 下冲突的正确动作是 **append 新 vintage 行**而非 UPDATE — 语义必须 schema 设计时定死。
13. **重试风暴**：区分可重试（429/5xx 退避）与不可重试（404/空数组进"待核查"队列），绝不无脑重试烧配额。
14. **SQLite ALTER TABLE 硬限制** [事实]：不能加 PRIMARY KEY/UNIQUE 约束、不能加 CURRENT_TIMESTAMP 默认列。官方 12 步重建流程顺序至关重要 — **必须先建新表再改名换入**（否则 3.25+ 自动引用重写会破坏 trigger/view/FK），末尾必跑 `PRAGMA foreign_key_check`。
15. **Schema drift 静默破坏**：6 类监控指标（freshness/volume/schema/completeness/distribution/latency）；[推断] 扩池后新增 per-vintage 分布断言（如新 vintage revenue 中位数相对上一版变动 >20% 即人工复核）— 抓 restatement 和 vendor bug 同一探针。

### 4.3 迁移切换类

16. **Dual-write 竞态**：expand/contract 六步（扩展 schema → dual-write → 回填同步 → 读切新 → 停写旧 → 收缩），核心优势是几乎每步可无损回滚。
17. **切换期部分失败** [事实]：16TB 金融迁移教训 — shadow read 差异 **92% 集中在高价值低频路径**。[推断] 本项目验收不要看"1000 只平均误差"（会全绿），专门盯少数重仓票 + 复杂派生指标（PE/EPS 增长/分位数排名）。回滚五问：能否回退版本仍读旧 schema / 移除触发器后旧数据仍正确 / 停写新后旧还能工作 / 有无 PIT 备份 / 能否暂停继续 — 任何一问"我想应该可以"就还没准备好。
18. **结果不可复现**：每份研究报告 header 记 `(universe_snapshot_id, fundamentals_vintage_cutoff, code_git_sha)` 三元组 — 旧池软退役安全的前提，否则退役后历史结论无法重跑对照。
19. **"软退役"退而不役** [推断]：给旧池读路径加调用埋点，退役判据 = "埋点连续 N 周零调用"，不是"我觉得没人用了"（notebook/cron/临时脚本 grep 不全）。

### 4.4 专题一：filing date 缺失/不可靠时的降级阶梯

每级必须在 `date_source` 列显式标注（行级列，非全局假设）：
L0 `acceptedDate`(edgar_accepted) → L1 `filingDate`(vendor_filing) → L2 8-K 公告日(earnings_announce) → L3 该公司过去 8 季实际 lag 中位数(company_median_lag) → L4 法定上限（10-Q 40-45 天 / 10-K 60-75 天）(statutory_deadline) → L5 FF 式 6 个月(ff_conservative) → L6 置 NULL 不参与回测(unavailable)。

卫生检查：`filing_date < period_end` 判脏降级 L3+；`filing_date > period_end + 180d` 判脏；L3 以下占比 >5% 时 PIT 回测结论打警示标签。

### 4.5 专题二：只能拿到最新值时的标注（vintage_quality 三态）

- `as_reported` — 披露后不久首次观测，接近原始值
- `latest_known` — backfill 时的 vendor 当前值，**可能已含未来重述**，历史段全是这个状态
- `revised` — 自建 vintage 生效后观测到的修正

诚实原则：历史段不打 `is_point_in_time=true`；schema 里要有能一眼查出"PIT 保证从哪天开始"的字段。Little r restatement（占 76%）不发 8-K — **必须靠值比对捕获变更，不能靠事件监听**。Susan Potter bias scorecard："问题从来不是有没有偏差，而是存在哪些、多大、扣除后还剩什么信号。"

**如果只记三件事**：(1) 日期比数值重要 — 四个独立日期列 + `date_source` 降级标注；(2) membership 必须由当时的市值决定；(3) 切换验收盯重仓票和复杂派生指标，不看平均值。

---

## 5. 内部审计 — Universe 语义与全调用方清单（audit-universe-callers，2026-08-18）

> Worktree 无 data/（0 行骨架陷阱），所有 live 数字实测自主仓库真实 data root（2026-08-18）。

### 5.1 头条发现

1. **不存在可迁移的"universe resolver"——存在 11 个从不一致的独立 resolver**，"pool"一词在五个子系统里有五种含义。
2. **`data_health._check_pool_integrity()` 今天就已 FAIL**：`data_health.py:106` 池 >200 即 FAIL，Core 当前 209，只读实跑确认 `LEVEL: FAIL`；`sync_to_cloud.sh:74-80` 遇 FAIL 中止整个同步。任何扩池动作都撞在一个已经红的门上。**先修门，再动池。**
3. **`universe.json` 是双端并集 merge（只增不减）**（`sync_to_cloud.sh:178-228` → `pool_manager.merge_universe()` `pool_manager.py:469-546`）。把 extended 名单写进去 = **单向门，无代码路径能缩回**。→ Extended-as-base 设计必须让 Core 文件身份与 resolver 默认值分离，否则软退役没有回滚。

### 5.2 池定义（file:line 级）

| 层 | 定义 | 路径 | 生成方 | cron | live 规模 | schema |
|---|---|---|---|---|---|---|
| Core | `pool_manager.py:27`；阈值 `settings.py:31-32` | `data/pool/universe.json` | `refresh_universe()` :140（$100B 通用 + $10B 科技）| Sat 08:30 | **209** | 裸 JSON list |
| Extended | `settings.py:359,363`（MIN_MCAP_B=10）| `data/pool/extended_universe.json`（**单文件，非目录** — CLAUDE/ARCHITECTURE 文档有误）| `refresh_extended_universe()` :59（floor 800 防空返回）| Sat 09:00 step6 | **955** | dict `{updated,count,symbols[]}` |
| Broad research | `settings.py:367-368` | `broad_universe_seed/broad_universe.json` | $500M seed → $1B final | Sat 09:00 | 云端 | dict |
| Broad scan cache | `broad_market_scan.py:65` | `data/scans/broad_universe.json` | source=market_db 时**忽略文件**直接查 `historical_market_cap ≥$1B` | daily | 云端 | dict `{stocks:{}}` |
| Delisted overlay | `delisted_universe_manager.py:21` | `delisted_large_caps.json` | 手动 backfill 脚本，**无 cron** | — | **21** | dict |

**membership 历史：Core 有（`pool_history.json`，每次 refresh 记 entered/exited，`pool_manager.py:28,90,206-216`），Extended 没有**（`_write_cache()` :52 盲覆盖，唯一时间字段是标量 updated）。恰恰是回测最需要 PIT membership 的那层没有 — resolver 要补的设计缺口，不是继承。

Extended manager API：`refresh_extended_universe():59` / `load:108` / `get_extended_symbols():113`（全 955）/ `get_extended_only_symbols():123`（ext−core）/ `get_cache_age_days():139` / `get_extended_true_symbols()`（`delisted_universe_manager.py:135`，∪overlay）。

### 5.3 调用方清单（按消费域，迁移 checklist 底稿）

**Data Desk 采集器**：
- Core 锁定点（同一 idiom `if symbols is None: get_symbols()`）：`fundamental_fetcher.py:74,119,156,193,230,258`（六处）← `update_data.py:141`；`price_fetcher.py:107`（+benchmarks :110-112）；`update_data.py:129`（价格）/`:148`（metrics 继承）/`:292`（correlation，O(n²)，1003² 是 25 倍格子）；`update_options_iv.py:61-62`（**MarketData 每 symbol 每天 1 链，扩池 = 4.8× credit**）
- 已显式 scope 的：`update_data.py:26-58 _resolve_target_symbols`（core/extended/all 三态——**生产中唯一现存的 scope selector，resolver 的天然种子**）；`update_extended_prices.py:96-114`（ext-only yfinance batch）/`:73-94`（broad−pool）；`fmp_forward_ingestion.py:390-409 resolve_fmp_forward_universe`（core∪ext∪ETF∪MAGS ≈1,075-1,175，两池任一为空即 fail-fast，冻进 manifest 当 verifier 分母）；`verify_forward_coverage.py:88-91`（**双分母分桶**，塌成一桶会破坏 gate）；`fetch_historical_mcap.py:47-70`（**隐式默认 fallthrough → extended**）
- 绕过 manager 直读文件：`backfill_social.py:60-65`；`rs_universe_scan.py:60-68`（**自己调 FMP screener，第 4 个独立定义**）

**验证层 — 每个 gate 都是 Core 分母**：`data_health.py:74-84 _load_universe_symbols`（直读 universe.json，5/11 项检查的分母）；`:98-113` 池完整性（<70 FAIL，>200 FAIL — **已红**）；`:117-143` profile 覆盖（live 168/209=80% WARN）；`:247/:318/:349` mdb price/fundamental/IV 覆盖（live 100%/98%/96%）。`data_guardian.py:57` 只快照 universe.json（Extended/Broad/overlay **不在快照内**）。`data_validator.py:137,190`。

**晨报 — 实质已 Extended，标签仍 Core**：真实扫描宇宙 = `morning_report.py:1131-1140`（market.db PIT ≥$10B）；Core 只用于层标签 + mcap 豁免（`:1113,1141-1147`；`:315-321 _layer_for_symbol`；`:1701`）；`:2825` 的 `get_symbols()` 取完即弃（vestigial）。**最便宜的迁移目标**：Core=Extended 后只是 pool/extend 两层标签合并，扫描内容不变。

**Concept Registry**：CLI 默认 broad（`build_company_concept_registry.py:1629,1854-1859`）；周六 cron 跑的 `weekly_sync` :1063 用 **Extended**；`_load_universe` :1510-1522 是唯一容忍三种 schema 的 reader。

**Backtest/Forge**：`us_stocks.py:277-298` 四路 dispatch（pool/extended/extended_true/**else→market.db 全体 2,909**）；裸 `USStocksAdapter()`（ctor :41 universe=None）全落 else 分支 — `run_factor_study.py:67`、`run_rs_backtest.py:223`、`sweep_vix_spike.py:27`（不可覆盖）。event_study 默认 extended_true（`cli.py:15` + `protocol.py:30` 两处独立默认；`universe.py:182-194` 只接受 extended_true）。`pipeline/universe_builder.py:129-154` 纯 PIT mcap，无池概念。
**三个潜伏 bug（resolver 应吸收不应继承）**：① `runner.py:203` universe_name="pool" 直接 AttributeError（`:354-360` 对裸 list 调 .get）；② `run_rs_backtest.py:223` choices 无法表达 extended_true — 最常用策略路径结构性带 survivorship bias；③ `us_stocks.py:203-209` mcap 覆盖 gate <90% 直接 raise — 新 Extended 票 hmcap 薄会让回测从能跑变 crash。
**命名地雷**：breadth_study 的 `universe_variant`（`core.py:768,1357`）与 `run_breadth_buy_quality.py:40 UNIVERSES` 指 active_only/with_delisted_partial（广度计算变体，非池），~40 处 grep 驱动重构会误伤。

**Terminal/Portfolio/Knowledge**：`risk/`、`trading/`、`cio-b/` 零 Python；`knowledge/` 全单票模板；`portfolio/` 全持仓域。要点：`terminal/pipeline.py:394-397 ensure_in_pool()` → `pool_manager.py:329` **写 Core（source="analysis"）— 全库唯一写侧调用方，必须改指向，否则 Extended 分析会永久污染 Core**；`company_store.py:602-608` N-placeholder IN 子句（1,003 参数超 SQLite pre-3.32 的 999 上限）；`freshness.py:173-191` 每 symbol 拉一次**全市场** 90 天 earnings calendar（全库最差点位，portfolio_status 热路径可达）；`iv_tracker.py:192-219` 每 symbol 一次 MarketData；`llm_concept_prefill.py:100-139` 每 symbol 一个 claude -p 子进程；`live_quote_provider.py:69,115` >50 symbols 直接 raise（正确防护，但 naive 扩池会 crash 而非降级）。

### 5.4 语义分叉实测（market.db distinct symbols）

| 域 | symbols | 隐含 universe | 决定点 |
|---|---:|---|---|
| historical_market_cap | 3,104 | Broad seed $500M | `broad_universe_manager.py:142-170` |
| daily_price | 2,909 | Broad+Ext+Core | `update_extended_prices.py:82-86` + `update_data.py:129` |
| fmp_estimates | 1,092 | core∪ext∪baskets∪MAGS | `fmp_forward_ingestion.py:409` |
| forward_estimates | 1,072 | 同上 yfinance 腿 | `update_data.py:56` |
| company_concept_tags | 1,070 | broad_top∪ext∪watchlist | `build_company_concept_registry.py:1063,1855` |
| **income/ratios/metrics** | **223** | **Core** | `fundamental_fetcher.py:156,119` |
| **iv_daily** | **222** | **Core**+benchmarks | `update_options_iv.py:61-62` |
| profiles.json | 183 | **三个不同 scope 的 writer 竞争一个文件的残渣** | `fundamental_fetcher.py:93`（Core）vs `build_company_concept_registry.py:1758`（Ext）vs weekly_sync :1063（delta）|

**分叉只有一行深**：隐式默认 = Core（`if symbols is None: get_symbols()`），显式 scope = Extended 或更宽。**resolver 的工作 = 反转这个默认值，同时不破坏显式路径。**

### 5.5 迁移危险点 Top 5

1. **健康 gate 是 Core 形状且已经红**：不迁基本面就把 universe.json 指向 1,003 → mdb fundamental 覆盖 98%→~22%、IV 96%→~22% 全 FAIL → `sync_to_cloud.sh:78` 中止所有同步。**先修 gate 再动池。**
2. **周六基本面爆窗**：1,003 × 5 endpoint × 2s ≈ **2h47m**（10:00 起跑到 ~12:47），10:45 forward 因 busy-rc 75 直接失败，**当周 PIT 快照不可补拍**。
3. **日频 MarketData IV 4.8×**：metered plan，翻开关前必须先算 credit。
4. **universe.json 扩容不可逆**（并集 merge 只增不减 + `ensure_in_pool` 持续追加）。
5. **回测结果静默改变**：else 分支从 market.db-all 2,909 改 Extended 是 3× 收窄，RS 百分位排名（`rebalancer.py:65-80` 对加载集排名）随之变 → docs/research/ 所有历史数字。任何翻转需要 before/after parity run，不是 code review。

### 5.6 审计员建议的迁移次序

晨报（实质已 Extended，只改标签）→ Concept Registry（weekly_sync 已 Extended）→ forward（已是 union）→ **硬骨头：fundamentals + IV + health gates** → 回测默认值最后且藏在显式 flag 后面。

---

## 来源汇总

### 参考系统
- [CRSP/Compustat Merged Database Guide](https://www.otago.ac.nz/library/pdf/CRSPCompustatguide09.pdf) — PERMNO/PERMCO/GVKEY+IID、link 表
- [Empirical Research in Accounting ch.7](https://iangow.github.io/far_book/identifiers.html) — CCM link 过滤规则、CUSIP 匹配实证、datadate vs rdq 锚点分歧
- [S&P Global query library (316)](https://www.marketplace.spglobal.com/en/support/query-library/query-(316)) — Compustat PIT change-only 存储语义
- [Compustat brochure](https://www.spglobal.com/marketintelligence/en/documents/spgmi375-03321-compustat_brochure_digital_letter_ss3.pdf) — Snapshot 三档口径
- [Sharadar Fundamentals docs](https://sharadar.com/docs/fundamentals) — AR/MR 六 dimension、重述处理
- [QuantRocket Usage Guide](https://www.quantrocket.com/docs/) — Sid / securities master
- [quantrocket/fundamental.py](https://github.com/quantrocket-llc/quantrocket-client/blob/master/quantrocket/fundamental.py) — `*_reindexed_like()` +1 day shift
- [zipline/assets/assets.py](https://github.com/quantopian/zipline/blob/master/zipline/assets/assets.py) — SQLite 资产库 schema、OwnershipPeriod
- [Concretum Group Norgate 教程](https://concretumgroup.com/how-to-construct-a-survivorship-bias-free-database-in-norgate-using-python/) — 成员布尔时序、退市库
- [ArcticDB FAQ](https://docs.arcticdb.io/4.5.0/faq/) — 版本化存储代价
- [HansjoergW/sec-financial-statement-data-set](https://github.com/HansjoergW/sec-fincancial-statement-data-set) — SQLite 索引 + parquet 载荷
- [pit-fundamentals](https://github.com/christianpichichero-max/pit-fundamentals) — 最小 PIT schema、43 天披露滞后
- [Tidy Finance](https://www.tidy-finance.org/chapters/accessing-and-managing-financial-data.html) — SQLite→Parquet 迁移理由
- [Tidy Finance WRDS/CRSP/Compustat](https://www.tidy-finance.org/python/wrds-crsp-and-compustat.html) — shrcd/exchcd 白名单惯例
- [Data Curator FMP 文档](https://kaxanuk-data-curator.readthedocs.io/en/stable/data_providers/financial_modeling_prep.html) — acceptedDate 对应 10-Q 而非最早盈利事件的陷阱

### 常见坑
- [SQLite ALTER TABLE](https://www.sqlite.org/lang_altertable.html) — 官方 12 步重建流程
- [CrossSection issue #50](https://github.com/OpenSourceAP/CrossSection/issues/50) — RDQ 脏数据实测（600+/50,000+）
- [OSAP FAQ](https://www.openassetpricing.com/faq/) — FF 6 个月 lag 惯例
- [Shumway 1997/1999](https://www.tylergshumway.org/Shumway-DelistingBiasCRSP-1997.pdf) — −55% 修正、4.7 倍偏差
- [S&P: PIT vs Lagged](https://www.spglobal.com/market-intelligence/en/news-insights/research/point-in-time-vs-lagged-fundamentals) — US Top 1000 4/24 偏离实测（403，据摘要）
- [FMP 对齐指南](https://site.financialmodelingprep.com/insights/data/how-to-align-earnings-dates-fiscal-quarters-estimates-and-reported) / [FMP Bulk API](https://site.financialmodelingprep.com/datasets/bulk-data)
- [Susan Potter: Taxonomy of Backtest Lies](https://www.susanpotter.net/quant/backtest-bias-taxonomy/) — bias scorecard
- [Zero-Downtime DB Migrations](https://thebackenddevelopers.substack.com/p/zero-downtime-database-migrations) — expand/contract、shadow read、回滚五问
- [Airbyte: Idempotency](https://airbyte.com/data-engineering-resources/idempotency-in-data-pipelines) — checkpoint 三要素、canary backfill
- [Sqlism: NULL vs Zero](https://sqlism.com/null-vs-blank-vs-zero-sql-reporting) / [QuantLabs FMP 评测](https://www.quantlabsnet.com/post/data-provider-disaster-a-financial-modeling-prep-api-review-alternatives)（个案，打折扣）

### 技术选型
- [SQLite: Write-Ahead Logging](https://www.sqlite.org/wal.html) — 跨 ATTACH 库事务不原子；checkpoint starvation；事务 >100MB 性能反转、>1GB 可能失败
- [SQLite: VACUUM](https://www.sqlite.org/lang_vacuum.html) — VACUUM 需最多 2 倍空闲空间；VACUUM INTO 1 倍空间
- [SQLite forum: Split db into smaller dbs](https://sqlite.org/forum/info/4ee709819ed4905e7c0e6ec77927b1044a4a1868281b245a116ac24fc2e4dd2e) — 拆库无性能收益；VACUUM 只在删 >10% 时做
- [SQLite: UNION Virtual Table](https://sqlite.org/unionvtab.html) / [DBSTAT Virtual Table](https://www.sqlite.org/dbstat.html)
- [Qlib PIT Database](https://qlib.readthedocs.io/en/latest/advanced/PIT.html) — date/period/value/_next 修订链表
- [XTDB: DIY Bitemporality Challenge](https://xtdb.com/blog/diy-bitemporality-challenge) — 手写 as-of SQL 易错（厂商软文，只取观察）
- [FMP Income Statement API](https://site.financialmodelingprep.com/developer/docs/stable/income-statement) — 响应含 filingDate/acceptedDate（来自搜索摘要，落地前实测确认）
- [oldmoe: Backup strategies for SQLite](https://oldmoe.blog/2024/04/30/backup-strategies-for-sqlite-in-production/) — 五种备份方式横评
- [phiresky: SQLite performance tuning](https://phiresky.github.io/blog/2020/sqlite-performance-tuning/) — PRAGMA 组合
- [A SQLite Background Job System](https://jasongorman.uk/writing/sqlite-background-job-system/) / [litequeue](https://github.com/litements/litequeue) — job ledger 原子认领 + visibility timeout
- [Designing for Failure: Idempotency in Data Pipelines](https://medium.com/@ayasc/designing-for-failure-idempotency-in-data-pipelines-f1d6003521f4) — at-least-once + 目的端幂等
- [Data Quality Gate](https://inferensys.com/glossary/data-observability-and-quality-posture/data-quality-metrics/data-quality-gate) / [dbt: pipeline quality checks](https://www.getdbt.com/blog/data-pipeline-quality-checks) — 四维度阈值熔断
