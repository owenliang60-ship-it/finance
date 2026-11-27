"""股票主题分类 M2：云端权威五表存储引擎（theme_baskets 等）。

只在云端 data/market.db 上写入；本地生产库在连接前被硬拒（ProductionWriteRefused）。
所有写入用 INSERT ... ON CONFLICT DO UPDATE，绝不物理删除篮子或历史。

兼容约束：云端 Python 3.10 + SQLite 3.37.2。本模块不得导入 src.data.market_store，
也不属于 terminal/prosperity/version.py 的 CODE_VERSION_SOURCES。
"""
import os
import sqlite3
from pathlib import Path
from typing import Set, Union

_REPO_ROOT = Path(__file__).resolve().parents[2]

PathLike = Union[str, Path]


class ProductionWriteRefused(PermissionError):
    pass


_TABLES = """
CREATE TABLE IF NOT EXISTS theme_baskets (
    basket_id TEXT NOT NULL PRIMARY KEY,
    label_zh TEXT NOT NULL UNIQUE,
    tier TEXT NOT NULL CHECK (tier IN ('l1','l2','cross','pi_bucket')),
    parent_l1_id TEXT REFERENCES theme_baskets(basket_id),
    default_pi_bucket TEXT REFERENCES theme_baskets(basket_id),
    sort_order INT NOT NULL DEFAULT 0,
    min_active_members INT NOT NULL DEFAULT 0 CHECK (min_active_members >= 0),
    narrative_start_date TEXT,
    inclusion_rule TEXT NOT NULL,
    exclusion_rule TEXT NOT NULL,
    is_active INT NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
    updated_at TEXT NOT NULL,
    CHECK ((tier = 'l2') = (parent_l1_id IS NOT NULL)),
    CHECK ((tier = 'l2') = (default_pi_bucket IS NOT NULL)),
    CHECK ((tier = 'cross') = (narrative_start_date IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS stock_themes (
    symbol TEXT NOT NULL PRIMARY KEY CHECK (symbol <> '' AND symbol = trim(symbol)),
    cik TEXT,
    asset_kind TEXT NOT NULL CHECK (asset_kind IN
        ('us_equity','etf','fund_cef','non_us_equity','crypto','custom_basket','other')),
    in_us_basket_calc INT NOT NULL CHECK (in_us_basket_calc IN (0,1)),
    is_superseded INT NOT NULL DEFAULT 0 CHECK (is_superseded IN (0,1)),
    superseded_by TEXT,
    l1_id TEXT REFERENCES theme_baskets(basket_id),
    l2_id TEXT REFERENCES theme_baskets(basket_id),
    pi_bucket TEXT NOT NULL REFERENCES theme_baskets(basket_id),
    business_note TEXT,
    profile_sector TEXT,
    profile_industry TEXT,
    company_name_snapshot TEXT,
    source TEXT NOT NULL CHECK (source IN ('override','db_locked','agy_llm')),
    agreement_status TEXT NOT NULL CHECK (agreement_status IN
        ('locked','dual_agreed','single_pass_weekly','needs_spot_check','disagreed')),
    assigned_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (in_us_basket_calc = 0 OR (asset_kind = 'us_equity' AND is_superseded = 0
           AND l1_id IS NOT NULL AND l2_id IS NOT NULL)),
    CHECK (asset_kind NOT IN ('crypto','non_us_equity') OR instr(symbol, ':') > 1),
    CHECK (asset_kind NOT IN ('etf','fund_cef','crypto','custom_basket') OR l2_id IS NULL),
    CHECK ((l1_id IS NULL) = (l2_id IS NULL)),
    CHECK ((is_superseded = 1) = (superseded_by IS NOT NULL)),
    CHECK (superseded_by IS NULL OR (in_us_basket_calc = 0 AND superseded_by <> symbol)),
    CHECK ((agreement_status = 'locked') = (source IN ('override','db_locked')))
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_stock_themes_active_cik ON stock_themes(cik)
    WHERE cik IS NOT NULL AND cik <> '' AND in_us_basket_calc = 1 AND is_superseded = 0;

CREATE TABLE IF NOT EXISTS stock_cross_themes (
    symbol TEXT NOT NULL REFERENCES stock_themes(symbol),
    theme_id TEXT NOT NULL REFERENCES theme_baskets(basket_id),
    role TEXT NOT NULL CHECK (role IN ('core','platform_giant')),
    source TEXT NOT NULL CHECK (source IN ('override','db_locked','agy_llm')),
    assigned_at TEXT NOT NULL,
    PRIMARY KEY (symbol, theme_id)
);

CREATE TABLE IF NOT EXISTS stock_theme_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    asset_kind TEXT NOT NULL,
    in_us_basket_calc INT NOT NULL,
    is_superseded INT NOT NULL,
    l1_id TEXT,
    l2_id TEXT,
    cross_themes_json TEXT NOT NULL,
    pi_bucket TEXT NOT NULL,
    source TEXT NOT NULL,
    effective_from TEXT NOT NULL,
    change_reason TEXT NOT NULL,
    changed_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_stock_theme_history_symbol_eff
    ON stock_theme_history(symbol, effective_from, id);

CREATE TABLE IF NOT EXISTS theme_sync_commits (
    payload_id TEXT NOT NULL PRIMARY KEY CHECK (length(payload_id) = 64),
    taxonomy_sha256 TEXT NOT NULL CHECK (length(taxonomy_sha256) = 64),
    applied_at TEXT NOT NULL,
    inserted_count INT NOT NULL CHECK (inserted_count >= 0),
    updated_count INT NOT NULL CHECK (updated_count >= 0),
    quarantined_count INT NOT NULL CHECK (quarantined_count >= 0)
);
"""

# Reference rule shared by the two theme_baskets ref triggers: an active basket must
# point at an active parent of the right tier; an inactive (retired) basket only
# needs the right tier, so a retired L2 can keep hanging under a retired L1.
_BASKET_REFS_WHEN = """
    (NEW.parent_l1_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM theme_baskets p WHERE p.basket_id = NEW.parent_l1_id
           AND p.tier = 'l1' AND (p.is_active = 1 OR NEW.is_active = 0)))
    OR (NEW.default_pi_bucket IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM theme_baskets p WHERE p.basket_id = NEW.default_pi_bucket
           AND p.tier = 'pi_bucket' AND (p.is_active = 1 OR NEW.is_active = 0)))
"""

_STOCK_VALIDATE_WHEN = """
    NOT EXISTS (
        SELECT 1 FROM theme_baskets b WHERE b.basket_id = NEW.pi_bucket
           AND b.tier = 'pi_bucket' AND (b.is_active = 1 OR NEW.is_superseded = 1))
    OR (NEW.l2_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM theme_baskets b WHERE b.basket_id = NEW.l2_id
           AND b.tier = 'l2' AND b.parent_l1_id = NEW.l1_id
           AND (b.is_active = 1 OR NEW.is_superseded = 1)))
"""

_CROSS_VALIDATE_WHEN = """
    NOT EXISTS (
        SELECT 1 FROM stock_themes s WHERE s.symbol = NEW.symbol
           AND s.in_us_basket_calc = 1 AND s.is_superseded = 0)
    OR NOT EXISTS (
        SELECT 1 FROM theme_baskets b WHERE b.basket_id = NEW.theme_id
           AND b.tier = 'cross' AND b.is_active = 1)
"""

_TRIGGERS = f"""
CREATE TRIGGER IF NOT EXISTS trg_theme_baskets_no_delete
BEFORE DELETE ON theme_baskets
BEGIN
    SELECT RAISE(ABORT, 'theme_baskets: delete is forbidden, set is_active = 0');
END;

CREATE TRIGGER IF NOT EXISTS trg_theme_baskets_identity
BEFORE UPDATE ON theme_baskets
WHEN NEW.basket_id IS NOT OLD.basket_id OR NEW.tier IS NOT OLD.tier
BEGIN
    SELECT RAISE(ABORT, 'theme_baskets: basket_id and tier are immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_theme_baskets_refs_ins
BEFORE INSERT ON theme_baskets
WHEN {_BASKET_REFS_WHEN}
BEGIN
    SELECT RAISE(ABORT, 'theme_baskets: parent_l1_id/default_pi_bucket must reference an active basket of the right tier');
END;

CREATE TRIGGER IF NOT EXISTS trg_theme_baskets_refs_upd
BEFORE UPDATE ON theme_baskets
WHEN {_BASKET_REFS_WHEN}
BEGIN
    SELECT RAISE(ABORT, 'theme_baskets: parent_l1_id/default_pi_bucket must reference an active basket of the right tier');
END;

CREATE TRIGGER IF NOT EXISTS trg_theme_baskets_deactivate_guard
BEFORE UPDATE ON theme_baskets
WHEN OLD.is_active = 1 AND NEW.is_active = 0 AND (
    EXISTS (SELECT 1 FROM theme_baskets c WHERE c.is_active = 1
               AND c.basket_id <> NEW.basket_id
               AND (c.parent_l1_id = NEW.basket_id OR c.default_pi_bucket = NEW.basket_id))
    OR EXISTS (SELECT 1 FROM stock_themes s WHERE s.is_superseded = 0
               AND (s.l1_id = NEW.basket_id OR s.l2_id = NEW.basket_id
                    OR s.pi_bucket = NEW.basket_id))
    OR EXISTS (SELECT 1 FROM stock_cross_themes x WHERE x.theme_id = NEW.basket_id))
BEGIN
    SELECT RAISE(ABORT, 'theme_baskets: cannot deactivate a basket that still has active references');
END;

CREATE TRIGGER IF NOT EXISTS trg_stock_themes_validate_ins
BEFORE INSERT ON stock_themes
WHEN {_STOCK_VALIDATE_WHEN}
BEGIN
    SELECT RAISE(ABORT, 'stock_themes: pi_bucket/l2_id must reference an active basket of the right tier with matching l1_id');
END;

CREATE TRIGGER IF NOT EXISTS trg_stock_themes_validate_upd
BEFORE UPDATE ON stock_themes
WHEN {_STOCK_VALIDATE_WHEN}
BEGIN
    SELECT RAISE(ABORT, 'stock_themes: pi_bucket/l2_id must reference an active basket of the right tier with matching l1_id');
END;

CREATE TRIGGER IF NOT EXISTS trg_stock_themes_exit_basket
AFTER UPDATE ON stock_themes
WHEN (OLD.in_us_basket_calc = 1 AND NEW.in_us_basket_calc = 0)
  OR (OLD.is_superseded = 0 AND NEW.is_superseded = 1)
BEGIN
    DELETE FROM stock_cross_themes WHERE symbol = NEW.symbol;
END;

CREATE TRIGGER IF NOT EXISTS trg_stock_cross_themes_validate_ins
BEFORE INSERT ON stock_cross_themes
WHEN {_CROSS_VALIDATE_WHEN}
BEGIN
    SELECT RAISE(ABORT, 'stock_cross_themes: symbol must be an in-basket US equity and theme_id an active cross basket');
END;

CREATE TRIGGER IF NOT EXISTS trg_stock_cross_themes_validate_upd
BEFORE UPDATE ON stock_cross_themes
WHEN {_CROSS_VALIDATE_WHEN}
BEGIN
    SELECT RAISE(ABORT, 'stock_cross_themes: symbol must be an in-basket US equity and theme_id an active cross basket');
END;

CREATE TRIGGER IF NOT EXISTS trg_stock_theme_history_no_update
BEFORE UPDATE ON stock_theme_history
BEGIN
    SELECT RAISE(ABORT, 'stock_theme_history: append-only, update is forbidden');
END;

CREATE TRIGGER IF NOT EXISTS trg_stock_theme_history_no_delete
BEFORE DELETE ON stock_theme_history
BEGIN
    SELECT RAISE(ABORT, 'stock_theme_history: append-only, delete is forbidden');
END;
"""

_SCHEMA = _TABLES + _TRIGGERS


# ---------------------------------------------------------------- write guard

def _main_repo_root(repo_root: Path):
    """Linked worktree → main checkout root; plain checkout → None.

    A `.git` *file* that cannot be parsed raises ProductionWriteRefused: without the
    main checkout path the guard cannot prove a target is not the production db.
    """
    git = repo_root / ".git"
    if not git.is_file():
        return None
    text = git.read_text(encoding="utf-8").strip()
    if not text.startswith("gitdir:"):
        raise ProductionWriteRefused(f"cannot parse worktree pointer {git}")
    gitdir = Path(text[len("gitdir:"):].strip())
    if not gitdir.is_absolute():
        gitdir = repo_root / gitdir
    commondir = gitdir / "commondir"
    if not commondir.is_file():
        raise ProductionWriteRefused(f"worktree gitdir has no commondir: {gitdir}")
    common = Path(commondir.read_text(encoding="utf-8").strip())
    if not common.is_absolute():
        common = gitdir / common
    return Path(os.path.realpath(common)).parent


def _protected_paths() -> Set[str]:
    import config.settings as settings

    protected = {os.path.realpath(settings.MARKET_DB_PATH)}
    main_root = _main_repo_root(_REPO_ROOT)
    if main_root is not None:
        protected.add(os.path.realpath(main_root / "data" / "market.db"))
    return protected


def _guard_write(db_path: PathLike, cloud_authoritative: bool) -> None:
    if str(db_path) == ":memory:":
        return
    if os.path.realpath(db_path) not in _protected_paths():
        return
    if cloud_authoritative is True and os.environ.get("FINANCE_ENV") == "cloud":
        return
    raise ProductionWriteRefused(
        f"refusing to write production market.db: {db_path} "
        "(cloud writer needs cloud_authoritative=True and FINANCE_ENV=cloud)")


def open_writer(db_path: PathLike, *, cloud_authoritative: bool = False) -> sqlite3.Connection:
    """Guard → connect (isolation_level=None) → FK + recursive triggers → schema."""
    _guard_write(db_path, cloud_authoritative)
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA recursive_triggers = ON")
        conn.executescript(_SCHEMA)
        _require_pragmas(conn)
    except BaseException:
        conn.close()
        raise
    return conn


def _require_pragmas(conn: sqlite3.Connection) -> None:
    fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    rt = conn.execute("PRAGMA recursive_triggers").fetchone()[0]
    if fk != 1 or rt != 1:
        raise sqlite3.OperationalError(
            f"theme_basket_store needs foreign_keys=1 and recursive_triggers=1 (got {fk}, {rt})")
