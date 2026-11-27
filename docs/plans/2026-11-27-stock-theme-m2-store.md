# Stock Theme Classification M2 (5-Table Store & Health Check) Implementation Plan

**Goal:** 实现双端 SQLite（云端 `3.37.2` / 本地 `3.53.4`）100% 兼容、零触碰景气哈希的主题篮子存储模块 `src/data/theme_basket_store.py` 并接入 `src/data/data_health.py` 只读健康检查。
**North Star / Spec:** 对齐 `docs/design/stock-theme-north-star.md`（§1、§3、§4 第二层、§5 M2）与 `docs/design/stock-theme-requirements.md`（R1–R5, R7, R9, R11）。
**Architecture & Tradeoffs:** 采用纯函数式短连接模块（`open_writer` + `apply_envelope` + `lock_private_symbol` / `unlock_private_symbol`），砍掉无状态收益的 `Store` 封装类、连接池与 `dry_run` 分支（预演直接传 `":memory:"`）。采用“单事务首尾 `before/after` 归属快照对比”集中生成 `stock_theme_history`，一处逻辑覆盖新票入库、公开锁定、移除降级与父级重挂，避免多分支漏记或重复刷历史。信封先跑新票插入段（逐行 `SAVEPOINT` 隔离坏行与存量冲突）、后跑 `overrides.json` 锁定覆盖段，兼顾新票元数据保留与 `override > db_locked > agy_llm` 最高优先级。
**Tech Stack & Constraints:**
- 本机 Python `3.13.1` + SQLite `3.53.4`；云端 Python `3.10.12` + SQLite `3.37.2`。
- 严禁修改或被 `terminal/prosperity/version.py:CODE_VERSION_SOURCES`（11 项路径，含 `config/settings.py` 与 `src/data/market_store.py`）导入；实施前后 `code_version()` 必须恒等于 `4c9da30bd5001102`。
- 严禁出现云端 SQLite `3.37.2` / Python `3.10` 不兼容或违禁语法：`OR REPLACE`、`->>`、`IS DISTINCT FROM`、`unixepoch`、`subsec`、`concat(`、`format(`、`string_agg`、`RIGHT JOIN`、`FULL JOIN`、`FULL OUTER JOIN`、聚合函数内 `ORDER BY`、`RAISE` 动态表达式、`autocommit`、`sqlite_errorcode`。空值安全不等比较统一用 `IS NOT`，`cross_themes_json` 一律在 Python 中序列化。
- 文件范围限定 5 个文件：新建 `src/data/theme_basket_store.py`、`tests/test_theme_basket_store.py`、本计划文档，修改 `src/data/data_health.py`、`tests/test_data_health.py`。

## 不变量与故障边界

- **状态合并点**：
  1. **字典与公开锁定合并（`baskets.json` + `overrides.json` → `theme_baskets`, `stock_themes`, `stock_cross_themes`）**：真值源为当前信封携带的 JSON；按 `pi_bucket → l1 → l2 → cross` 顺序 `INSERT ... ON CONFLICT(basket_id) DO UPDATE`（禁改 `tier`、禁物理 `DELETE`）；L2 父级变更自动重算存量 `stock_themes.l1_id`；`locked_stocks` 强制覆盖 `db_locked` 与 `agy_llm`（新建锁定行时若同库存在 `security_master` 自动回填 `cik` 与 `company_name_snapshot`，并以 `rationale` 兜底空 `business_note`）；从 `overrides.json` 移除的旧 `override` 行自动降级为 `source='agy_llm', agreement_status='needs_spot_check'`。任何字典/锁定违例整事务 `ROLLBACK` fail-closed。
  2. **新票分类行合并（`rows` + `payload_id` → `stock_themes`, `stock_cross_themes`, `theme_sync_commits`）**：入事务前校验 `compute_payload_id(rows) == payload_id`（不符抛 `ValueError` 零写入）；若 `payload_id` 已在 `theme_sync_commits` 中则整段跳过行写入（仅刷新 `applied_at` 与 `taxonomy_sha256`，天然兼容断线重试幂等与每周 0 行心跳）；首次见到 `payload_id` 时逐行 `SAVEPOINT` 纯 `INSERT`（撞存量票、缺键/多键、违反 `CHECK`/触发器均回滚该行记入 `quarantined`，不连坐同批合格行）。
  3. **只追加历史（`stock_theme_history`）**：单事务首尾对比每只股票的 `(asset_kind, in_us_basket_calc, is_superseded, l1_id, l2_id, cross_themes_json, pi_bucket, source)`，仅对发生实质变化的股票追加 1 行快照，触发器硬拒任何 `UPDATE/DELETE`。
- **故障模型（三档划界）**：
  1. **必须自动处理的单故障**：行段单票字段非法/撞存量票/触发器报错（`SAVEPOINT` 隔离进 `quarantined`）；网络重发相同 `payload_id`（幂等跳过行段并刷新心跳）；本地未建主题表时跑 `data_health.py`（返回 `WARN` 退出码 `0`）。
  2. **只停机/抛错、不自愈的故障**：尝试写本地生产 `data/market.db`（连接前抛 `ProductionWriteRefused`）；`payload_id` 校验不匹配或 `schema_version != 1`（`ValueError`）；`baskets.json`/`overrides.json` 自身违反外键/唯一键/停用保护（整事务回滚抛错）；对 `source='override'` 标的调 `lock_private_symbol`（`ValueError`）。
  3. **明确不支持的拓扑**：不支持多进程无外部 `flock` 并发直写同一 SQLite 文件（云端并发互斥由 M3 的外层 `flock` 负责）。

## 验收标准

1. **契约与不变量单测全绿**：在 M2 worktree 下运行 `../../.venv/bin/python -m pytest tests/test_theme_basket_store.py tests/test_data_health.py tests/test_theme_baskets_m1_taxonomy.py tests/test_prosperity_schemes.py -q` 退出码 `0`。
2. **景气哈希恒等**：实施前后 `code_version()` 均严格输出 `4c9da30bd5001102`，且静态扫描确认 `src/data/theme_basket_store.py` 不含任何 SQLite 3.37/Py3.10 禁用语法且未导入 `src.data.market_store`。
3. **真实配置幂等落库**：用仓库真实 `config/theme_baskets/{baskets,overrides}.json` 对临时库执行 `apply_envelope`（0 行新票）：写入 99 个篮子、49 只锁定龙头、49 条初始历史、1 条提交心跳；立即以更晚时间戳重跑后历史行数仍为 49（零冗余历史），心跳 `applied_at` 前进。
4. **本地生产库零触碰实测**：对当前本地生产 `data/market.db`（经 worktree 软链）调用 `open_writer` 立即抛 `ProductionWriteRefused`；调用 `data_health._check_theme_baskets()` 返回 `WARN`，调用前后 `-wal` 大小恒为 `0` 且进程 `lsof` 无残留 `market.db` 句柄。
5. **云端 Python 3.10 + SQLite 3.37.2 内存冒烟**：通过只读 SSH 将 `theme_basket_store.py` 经标准输入喂给云端 `/usr/bin/python3` 在 `:memory:` 上实跑建表、真实配置信封落库、重放、坏行隔离与私锁/解锁（零云端文件落盘、零触碰云端生产库）全过。

## 任务拆解（Tasks 1..3）

### Task 1: 5 表 Schema、12 触发器与本地生产库拒写守卫
- **Files:**
  - Create: `src/data/theme_basket_store.py`
  - Create: `tests/test_theme_basket_store.py`
- **Contract:**
  - `class ProductionWriteRefused(PermissionError): pass`
  - `open_writer(db_path: str | Path, *, cloud_authoritative: bool = False) -> sqlite3.Connection`：
    1. `_guard_write(db_path, cloud_authoritative)`：解析 `os.path.realpath` 比对保护集 `{realpath(config.settings.MARKET_DB_PATH)}` ∪（若 `.git` 为 worktree 文件则解析 `gitdir` + `commondir` 推出主仓 `data/market.db` 的 `realpath`）。命中保护集且未同时满足 `cloud_authoritative is True and os.environ.get("FINANCE_ENV") == "cloud"` 时，在 `sqlite3.connect` 前抛 `ProductionWriteRefused`。`":memory:"` 直接放行。
    2. `sqlite3.connect(str(db_path), isolation_level=None)` → 执行 `PRAGMA foreign_keys = ON` 与 `PRAGMA recursive_triggers = ON` → `conn.executescript(_SCHEMA)` → 读回校验两条 PRAGMA 均为 `1`（任一步异常先 `conn.close()` 再上抛；严禁改 `journal_mode`）。
  - **5 张表与索引精确 DDL**：
    - `theme_baskets(basket_id TEXT PK, label_zh TEXT NOT NULL UNIQUE, tier TEXT NOT NULL CHECK (tier IN ('l1','l2','cross','pi_bucket')), parent_l1_id TEXT REFERENCES theme_baskets(basket_id), default_pi_bucket TEXT REFERENCES theme_baskets(basket_id), sort_order INT NOT NULL DEFAULT 0, min_active_members INT NOT NULL DEFAULT 0 CHECK (min_active_members >= 0), narrative_start_date TEXT, inclusion_rule TEXT NOT NULL, exclusion_rule TEXT NOT NULL, is_active INT NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)), updated_at TEXT NOT NULL, CHECK ((tier = 'l2') = (parent_l1_id IS NOT NULL)), CHECK ((tier = 'l2') = (default_pi_bucket IS NOT NULL)), CHECK ((tier = 'cross') = (narrative_start_date IS NOT NULL)))`
    - `stock_themes(symbol TEXT PK CHECK (symbol <> '' AND symbol = trim(symbol)), cik TEXT, asset_kind TEXT NOT NULL CHECK (asset_kind IN ('us_equity','etf','fund_cef','non_us_equity','crypto','custom_basket','other')), in_us_basket_calc INT NOT NULL CHECK (in_us_basket_calc IN (0,1)), is_superseded INT NOT NULL DEFAULT 0 CHECK (is_superseded IN (0,1)), superseded_by TEXT, l1_id TEXT REFERENCES theme_baskets(basket_id), l2_id TEXT REFERENCES theme_baskets(basket_id), pi_bucket TEXT NOT NULL REFERENCES theme_baskets(basket_id), business_note TEXT, profile_sector TEXT, profile_industry TEXT, company_name_snapshot TEXT, source TEXT NOT NULL CHECK (source IN ('override','db_locked','agy_llm')), agreement_status TEXT NOT NULL CHECK (agreement_status IN ('locked','dual_agreed','single_pass_weekly','needs_spot_check','disagreed')), assigned_at TEXT NOT NULL, updated_at TEXT NOT NULL, CHECK (in_us_basket_calc = 0 OR (asset_kind = 'us_equity' AND is_superseded = 0 AND l1_id IS NOT NULL AND l2_id IS NOT NULL)), CHECK (asset_kind NOT IN ('crypto','non_us_equity') OR instr(symbol, ':') > 1), CHECK (asset_kind NOT IN ('etf','fund_cef','crypto','custom_basket') OR l2_id IS NULL), CHECK ((l1_id IS NULL) = (l2_id IS NULL)), CHECK ((is_superseded = 1) = (superseded_by IS NOT NULL)), CHECK (superseded_by IS NULL OR (in_us_basket_calc = 0 AND superseded_by <> symbol)), CHECK ((agreement_status = 'locked') = (source IN ('override','db_locked'))))`
    - 部分唯一索引：`CREATE UNIQUE INDEX IF NOT EXISTS ux_stock_themes_active_cik ON stock_themes(cik) WHERE cik IS NOT NULL AND cik <> '' AND in_us_basket_calc = 1 AND is_superseded = 0;`
    - `stock_cross_themes(symbol TEXT NOT NULL REFERENCES stock_themes(symbol), theme_id TEXT NOT NULL REFERENCES theme_baskets(basket_id), role TEXT NOT NULL CHECK (role IN ('core','platform_giant')), source TEXT NOT NULL CHECK (source IN ('override','db_locked','agy_llm')), assigned_at TEXT NOT NULL, PRIMARY KEY (symbol, theme_id))`
    - `stock_theme_history(id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, asset_kind TEXT NOT NULL, in_us_basket_calc INT NOT NULL, is_superseded INT NOT NULL, l1_id TEXT, l2_id TEXT, cross_themes_json TEXT NOT NULL, pi_bucket TEXT NOT NULL, source TEXT NOT NULL, effective_from TEXT NOT NULL, change_reason TEXT NOT NULL, changed_at TEXT NOT NULL)` + 索引 `ix_stock_theme_history_symbol_eff(symbol, effective_from, id)`
    - `theme_sync_commits(payload_id TEXT PRIMARY KEY CHECK (length(payload_id) = 64), taxonomy_sha256 TEXT NOT NULL CHECK (length(taxonomy_sha256) = 64), applied_at TEXT NOT NULL, inserted_count INT NOT NULL CHECK (inserted_count >= 0), updated_count INT NOT NULL CHECK (updated_count >= 0), quarantined_count INT NOT NULL CHECK (quarantined_count >= 0))`
  - **12 个触发器（精确共 12 个；`RAISE(ABORT, '静态字面量消息')`）**：
    1. `trg_theme_baskets_no_delete`（`BEFORE DELETE` 无条件拒删）
    2. `trg_theme_baskets_identity`（`BEFORE UPDATE` 禁改 `basket_id` 或 `tier`）
    3-4. `trg_theme_baskets_refs_ins` / `trg_theme_baskets_refs_upd`（校验非空 `parent_l1_id` 指向活跃 `tier='l1'`，非空 `default_pi_bucket` 指向活跃 `tier='pi_bucket'`）
    5. `trg_theme_baskets_deactivate_guard`（`BEFORE UPDATE WHEN OLD.is_active=1 AND NEW.is_active=0`：若被其他 `is_active=1` 篮子、`is_superseded=0` 的 `stock_themes(l1_id/l2_id/pi_bucket)` 或 `stock_cross_themes(theme_id)` 引用则拒停用）
    6-7. `trg_stock_themes_validate_ins` / `trg_stock_themes_validate_upd`（校验 `pi_bucket` 为 `tier='pi_bucket'` 且活跃或 `NEW.is_superseded=1`；非空 `l2_id` 为 `tier='l2'`、`parent_l1_id = NEW.l1_id` 且活跃或 `NEW.is_superseded=1`）
    8. `trg_stock_themes_exit_basket`（`AFTER UPDATE WHEN (OLD.in_us_basket_calc=1 AND NEW.in_us_basket_calc=0) OR (OLD.is_superseded=0 AND NEW.is_superseded=1)` 自动 `DELETE FROM stock_cross_themes WHERE symbol=NEW.symbol`）
    9-10. `trg_stock_cross_themes_validate_ins` / `trg_stock_cross_themes_validate_upd`（校验 `symbol` 在 `stock_themes` 中 `in_us_basket_calc=1 AND is_superseded=0`，且 `theme_id` 为活跃 `tier='cross'`）
    11-12. `trg_stock_theme_history_no_update` / `trg_stock_theme_history_no_delete`（禁改禁删，配合 `recursive_triggers=ON` 连同 `INSERT OR REPLACE` 一并封死）
- **TDD Steps:**
  1. 在 `tests/test_theme_basket_store.py` 先写全部 5 表结构、PRAGMA 校验、每条 `CHECK`/唯一索引/12 个触发器正反例、`INSERT OR REPLACE` 拦截及 `ProductionWriteRefused`（含软链与伪造 linked worktree `commondir`）测试，运行确认 FAIL。
  2. 在 `src/data/theme_basket_store.py` 实现 `ProductionWriteRefused`、`_SCHEMA` 与 `open_writer`，运行测试确认 PASS。
  3. 显式 `git add src/data/theme_basket_store.py tests/test_theme_basket_store.py && git commit -m "feat(theme-baskets): add 5-table schema, triggers, and write guard"`。

### Task 2: 双段信封幂等落库与私有锁定原语
- **Files:**
  - Modify: `src/data/theme_basket_store.py`
  - Modify: `tests/test_theme_basket_store.py`
- **Contract:**
  - `@dataclass(frozen=True) class ApplyResult: payload_id: str; taxonomy_sha256: str; replayed: bool; inserted: int; updated: int; quarantined: list[tuple[str, str]]; history_appended: int`
  - `compute_payload_id(rows: Sequence[Mapping[str, Any]]) -> str`：按 `str(r.get("symbol", ""))` 稳定排序后做紧凑 canonical JSON SHA256（64 位十六进制；空列表确定性哈希）。
  - `compute_taxonomy_sha256(baskets_doc: Mapping[str, Any], overrides_doc: Mapping[str, Any]) -> str`：对 `{"baskets": baskets_doc, "overrides": overrides_doc}` 做紧凑 canonical JSON SHA256。
  - `apply_envelope(conn: sqlite3.Connection, *, baskets_doc: Mapping[str, Any], overrides_doc: Mapping[str, Any], rows: Sequence[Mapping[str, Any]], payload_id: str, now: datetime | None = None) -> ApplyResult`：
    - 预检：`baskets_doc["schema_version"] == 1`、`overrides_doc["schema_version"] == 1`、`compute_payload_id(rows) == payload_id`；`now` 默认为 `datetime.now(timezone.utc)`（传入无时区 `datetime` 抛 `ValueError`），格式化为 `"%Y-%m-%dT%H:%M:%SZ"`，`effective_from = ts[:10]`。
    - `BEGIN IMMEDIATE` 单事务（异常回滚前先判 `if conn.in_transaction: conn.execute("ROLLBACK")` 避免盖住原异常）：
      1. 采集全表归属 `before` 快照（在 Python 中排序并序列化 `cross_themes_json` 为紧凑 JSON `'[{"role":"core","theme_id":"cross_..."}]'`，空为 `'[]'`）。
      2. 查 `payload_id` 是否已在 `theme_sync_commits`（记为 `replayed`）。
      3. 字典两步同步（防父子同批停用撞守卫）：先按 `pi_bucket → l1 → l2 → cross` 正序 `INSERT ... ON CONFLICT(basket_id) DO UPDATE` 写入新增/激活与元数据（此步将 `0 -> 1` 激活，但对 `excluded.is_active = 0` 的存量行暂保原 `is_active`）；随后级联更新 `l1_id` 与新 `parent_l1_id` 不一致的存量 `stock_themes` 行；最后对 `is_active = 0` 的条目按 **`cross → l2 → l1 → pi_bucket` 逆序** 执行 `UPDATE theme_baskets SET is_active = 0, updated_at = ? WHERE basket_id = ? AND is_active = 1`。
      4. 若 `not replayed`：逐行 `SAVEPOINT sp_row` 处理 `rows`（仅允许必填键 `{symbol, asset_kind, in_us_basket_calc, pi_bucket, agreement_status}` 与可选键 `{cik, is_superseded, superseded_by, l2_id, business_note, profile_sector, profile_industry, company_name_snapshot, cross_themes}`；未知键、非法类型、存量 `symbol` 已存在或任意 `(sqlite3.Error, KeyError, TypeError, ValueError)` 均在 `conn.in_transaction` 下执行 `ROLLBACK TO sp_row; RELEASE sp_row` 记入 `quarantined`，其中 `source` 固定写 `'agy_llm'`，`l1_id` 由 `l2_id` 父级派生）。
      5. 同步 `overrides_doc["locked_stocks"]`（`_upsert_lock(..., source='override')`：先查 `l2_id` 对应活跃 L2 的 `(parent_l1_id, default_pi_bucket)`，不存在则抛 `ValueError`；新建行固定填 `asset_kind='us_equity', in_us_basket_calc=1, is_superseded=0, pi_bucket=default_pi_bucket, assigned_at=ts, updated_at=ts`，若同库存在 `security_master` 则查填 `cik` 与 `company_name`，并以 `rationale` 作初始 `business_note`；已存在行用 `ON CONFLICT(symbol) DO UPDATE` 强制覆盖 `l1_id, l2_id, source='override', agreement_status='locked'` 并在 `business_note` 为空时补填 `rationale`，**保持已有 `pi_bucket` 不变**；同步增删 `stock_cross_themes`）；对原先 `source='override'` 但已从 `locked_stocks` 移除的票降级为 `source='agy_llm', agreement_status='needs_spot_check'`（其 `source='override'` 跨界行同步改 `'agy_llm'`）。
      6. 采集 `after` 快照对比 `before`：仅为有实质变化的票追加 1 行 `stock_theme_history`（`change_reason` 区分 `new_classification` / `override_lock` / `override_removed` / `taxonomy_reparent`）。
      7. `INSERT INTO theme_sync_commits ... ON CONFLICT(payload_id) DO UPDATE SET taxonomy_sha256=excluded.taxonomy_sha256, applied_at=excluded.applied_at`（重放时保留首次 `inserted_count, updated_count, quarantined_count`）。`COMMIT`。
  - `lock_private_symbol(conn, symbol: str, *, l2_id: str, cross_themes=(), business_note: str | None = None, now: datetime | None = None) -> None` 与 `unlock_private_symbol(conn, symbol: str, *, now: datetime | None = None) -> None`：自带 `BEGIN IMMEDIATE ... COMMIT`（异常同样判 `conn.in_transaction` 回滚）；拒锁 `source='override'` 票，拒解锁非 `source='db_locked'` 票，实质变更自动追加 `stock_theme_history`（`db_lock` / `db_unlock`）。
- **TDD Steps:**
  1. 先写 `apply_envelope`（真实 M1 JSON 全量落库、重复运行 0 新增历史、0 行心跳时间推进、3 行载荷首次落库与重放幂等、坏行夹好行隔离、存量防覆盖、同批新票叠加 override、字典调父级、移除 override 降级、非法 override 整批回滚）、`lock_private_symbol`/`unlock_private_symbol` 及源码禁用词静态扫描测试，运行确认 FAIL。
  2. 实现上述函数，运行确认 PASS。
  3. 显式 `git add src/data/theme_basket_store.py tests/test_theme_basket_store.py && git commit -m "feat(theme-baskets): implement idempotent envelope apply and private locks"`。

### Task 3: 只读健康检查接入 `data_health.py`
- **Files:**
  - Modify: `src/data/data_health.py`
  - Modify: `tests/test_data_health.py`
- **Contract:**
  - 在 `src/data/data_health.py` 新增 `_check_theme_baskets() -> CheckResult`（检查名 `"主题篮子"`，常量 `THEME_SYNC_STALE_DAYS = 14`），并在 `health_check()` 的 `_check_extended_coverage()` 后调用 `report.add(_check_theme_baskets())`。
  - 严禁调用 `current_base_universe()` / `get_store()`，严禁导入 `theme_basket_store`；仅在 `MARKET_DB.exists()` 时用 `closing(sqlite3.connect(Path(MARKET_DB).resolve().as_uri() + "?mode=ro", uri=True))` 短连接完成只读查询。
  - 状态全部限定为 `PASS` 或 `WARN`（捕获 `sqlite3.DatabaseError` / `OSError` 一律转 `WARN`，绝不向上抛异常或返回 `FAIL`）：缺库/缺任一主题表/空表 → `WARN`；`MAX(applied_at)` 为空或超 14 天 → `WARN`；若 `extended_membership` 或 `security_master` 表缺失则报 `WARN`（「主池表未就绪」）；否则检查 `extended_membership(effective_to IS NULL) ∩ security_master(eligible=1)` 存在未入 `stock_themes` 的缺口票 → `WARN`（列数量与前 10 只），以及活跃 `l1/l2/cross` 篮子活跃成员数 `< min_active_members` → `WARN`；全部正常 → `PASS`。
  - 在 `tests/test_data_health.py` 的 `mock_healthy_data` 夹具末尾补建最小池表（`extended_membership`, `security_master`），并通过 `open_writer` + `apply_envelope` 灌入 **3 篮子迷你字典（1 `pi_bucket` + 1 `l1` + 1 `l2`，`min_active_members=5`）** 与 100 只 mock 股票健康分类行，保持 `test_healthy_data_passes` 原断言一字不改通过；新增 `TestCheckThemeBaskets` 覆盖全部 `PASS/WARN` 分支（含缺池表、数据库损坏异常兜底）、零 `get_store` 调用断言及 `mode=ro` 零残留句柄/零 `-wal` 断言。
- **TDD Steps:**
  1. 先写 `tests/test_data_health.py` 中的 `mock_healthy_data` 扩展与 `TestCheckThemeBaskets` 全部断言，运行确认 FAIL。
  2. 在 `src/data/data_health.py` 实现 `_check_theme_baskets()` 并接入 `health_check()`，运行全部验收测试与本地真实 `market.db` 只读冒烟确认 PASS。
  3. 显式 `git add src/data/data_health.py tests/test_data_health.py docs/plans/2026-11-27-stock-theme-m2-store.md && git commit -m "feat(data-health): add read-only theme basket health check"`。

## 实施记录（2026-11-27，计划未写明处的取舍）

- `ApplyResult.inserted` = 本次新增的 `stock_themes` 行数；`updated` = 存量行中归属快照发生变化的行数（= `history_appended - inserted`）。
- `lock_private_symbol(business_note=...)`：显式传入的备注覆盖旧备注；传 `None` 保留旧备注。备注不属于历史快照字段，单改备注不追加历史。
- 已存在的票被公开锁定时，只改 `l1_id/l2_id/source/agreement_status`（按计划）；若该行是 ETF 等不允许 `l2_id` 的类型，`CHECK` 会拒绝，整个信封回滚。
- `_check_theme_baskets()` 在表齐全且非空后，把心跳、主池缺口、成员不足三类问题合并进一条 `WARN` 详情；主池表缺失时只跳过缺口检查，成员数检查照跑。
- 云端冒烟：把模块源码、真实 JSON 和断言脚本拼成一个文件，经 SSH 标准输入喂给 `/usr/bin/python3 -I -`（Python 3.10.12 / SQLite 3.37.2），只用 `:memory:`，云端零落盘。
