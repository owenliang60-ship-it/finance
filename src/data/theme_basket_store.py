"""股票主题分类 M2：云端权威五表存储引擎（theme_baskets 等）。

只在云端 data/market.db 上写入；本地生产库在连接前被硬拒（ProductionWriteRefused）。
所有写入用 INSERT ... ON CONFLICT DO UPDATE，绝不物理删除篮子或历史。

兼容约束：云端 Python 3.10 + SQLite 3.37.2。本模块不得导入 src.data.market_store，
也不属于 terminal/prosperity/version.py 的 CODE_VERSION_SOURCES。
"""
import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, NamedTuple, Optional, Sequence, Set, Tuple, Union

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


# ---------------------------------------------------------------- hashing

def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256(obj: Any) -> str:
    return hashlib.sha256(_canonical_json(obj).encode("utf-8")).hexdigest()


def _sorted_rows(rows: Iterable[Mapping[str, Any]]) -> List[Mapping[str, Any]]:
    return sorted(rows, key=lambda r: str(r.get("symbol", "")))


def compute_payload_id(rows: Sequence[Mapping[str, Any]]) -> str:
    """Content hash of the classification rows, independent of row order."""
    return _sha256([dict(r) for r in _sorted_rows(rows)])


def compute_taxonomy_sha256(baskets_doc: Mapping[str, Any], overrides_doc: Mapping[str, Any]) -> str:
    return _sha256({"baskets": baskets_doc, "overrides": overrides_doc})


# ---------------------------------------------------------------- shared helpers

@dataclass(frozen=True)
class ApplyResult:
    payload_id: str
    taxonomy_sha256: str
    replayed: bool
    inserted: int
    updated: int
    quarantined: List[Tuple[str, str]]
    history_appended: int


class _Snap(NamedTuple):
    """One stock's classification state; field order = stock_theme_history columns."""
    asset_kind: str
    in_us_basket_calc: int
    is_superseded: int
    l1_id: Optional[str]
    l2_id: Optional[str]
    cross_themes_json: str
    pi_bucket: str
    source: str


def _utc_ts(now: Optional[datetime]) -> str:
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be a timezone-aware datetime")
    return now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@contextmanager
def _immediate(conn: sqlite3.Connection):
    """BEGIN IMMEDIATE ... COMMIT; any exception rolls back without masking the cause.

    BEGIN runs outside the try block, so a caller that is already inside its own
    transaction gets an error and keeps its transaction untouched.
    """
    _require_pragmas(conn)
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


def _snapshot(conn: sqlite3.Connection, symbol: Optional[str] = None) -> Dict[str, _Snap]:
    where, params = ("WHERE symbol = ?", (symbol,)) if symbol is not None else ("", ())
    cross: Dict[str, List[Dict[str, str]]] = {}
    for sym, theme_id, role in conn.execute(
            f"SELECT symbol, theme_id, role FROM stock_cross_themes {where}", params):
        cross.setdefault(sym, []).append({"role": role, "theme_id": theme_id})
    snap = {}
    for sym, kind, in_calc, superseded, l1, l2, pi, source in conn.execute(
            "SELECT symbol, asset_kind, in_us_basket_calc, is_superseded, l1_id, l2_id, "
            f"pi_bucket, source FROM stock_themes {where}", params):
        items = sorted(cross.get(sym, []), key=lambda d: d["theme_id"])
        snap[sym] = _Snap(kind, in_calc, superseded, l1, l2, _canonical_json(items), pi, source)
    return snap


def _envelope_reason(old: Optional[_Snap], new: _Snap) -> str:
    if old is None:
        return "override_lock" if new.source == "override" else "new_classification"
    if old.source == "override" and new.source != "override":
        return "override_removed"
    if new.source == "override" and (old.source != "override" or old.l2_id != new.l2_id
                                     or old.cross_themes_json != new.cross_themes_json):
        return "override_lock"
    return "taxonomy_reparent"


def _append_history(conn: sqlite3.Connection, before: Mapping[str, _Snap], after: Mapping[str, _Snap],
                    ts: str, reason) -> int:
    """Append one snapshot row per symbol whose state changed; returns rows appended."""
    appended = 0
    for sym in sorted(after):
        new = after[sym]
        old = before.get(sym)
        if old == new:
            continue
        conn.execute(
            "INSERT INTO stock_theme_history (symbol, asset_kind, in_us_basket_calc, "
            "is_superseded, l1_id, l2_id, cross_themes_json, pi_bucket, source, "
            "effective_from, change_reason, changed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (sym, *new, ts[:10], reason(old, new) if callable(reason) else reason, ts),
        )
        appended += 1
    return appended


def _cross_pairs(items: Any) -> List[Tuple[str, str]]:
    """[{theme_id, role}, ...] → [(theme_id, role), ...]; strict keys/types, no duplicates."""
    if items is None:
        return []
    if not isinstance(items, (list, tuple)):
        raise TypeError("cross_themes must be a list")
    pairs = []
    for item in items:
        if not isinstance(item, Mapping) or set(item) != {"theme_id", "role"}:
            raise ValueError(f"cross theme must have exactly theme_id and role: {item!r}")
        if type(item["theme_id"]) is not str or type(item["role"]) is not str:
            raise TypeError(f"cross theme fields must be str: {item!r}")
        pairs.append((item["theme_id"], item["role"]))
    if len({t for t, _ in pairs}) != len(pairs):
        raise ValueError("duplicate theme_id in cross_themes")
    return pairs


def _sync_cross(conn: sqlite3.Connection, symbol: str, pairs: Sequence[Tuple[str, str]],
                source: str, ts: str) -> None:
    """Make the symbol's cross themes exactly `pairs`, all tagged with `source`."""
    current = {t: (role, src) for t, role, src in conn.execute(
        "SELECT theme_id, role, source FROM stock_cross_themes WHERE symbol = ?", (symbol,))}
    wanted = dict(pairs)
    for theme_id in sorted(current.keys() - wanted.keys()):
        conn.execute("DELETE FROM stock_cross_themes WHERE symbol = ? AND theme_id = ?",
                     (symbol, theme_id))
    for theme_id, role in pairs:
        if theme_id not in current:
            conn.execute(
                "INSERT INTO stock_cross_themes (symbol, theme_id, role, source, assigned_at) "
                "VALUES (?,?,?,?,?)", (symbol, theme_id, role, source, ts))
        elif current[theme_id] != (role, source):
            conn.execute(
                "UPDATE stock_cross_themes SET role = ?, source = ? WHERE symbol = ? AND theme_id = ?",
                (role, source, symbol, theme_id))


def _l2_info(conn: sqlite3.Connection, l2_id: Any):
    """(parent_l1_id, default_pi_bucket, is_active) of an L2 basket, or None."""
    return conn.execute(
        "SELECT parent_l1_id, default_pi_bucket, is_active FROM theme_baskets "
        "WHERE basket_id = ? AND tier = 'l2'", (l2_id,)).fetchone()


def _upsert_lock(conn: sqlite3.Connection, symbol: Any, l2_id: Any, pairs: Sequence[Tuple[str, str]],
                 note: Optional[str], source: str, ts: str) -> None:
    """Create or force-lock one symbol (source 'override' or 'db_locked').

    New rows get pi_bucket = the L2's default bucket and identity from security_master
    when that table exists; existing rows keep pi_bucket and a non-empty business_note.
    """
    if type(symbol) is not str:
        raise TypeError(f"symbol must be str: {symbol!r}")
    info = _l2_info(conn, l2_id)
    if info is None or info[2] != 1:
        raise ValueError(f"{symbol}: l2_id {l2_id!r} is not an active L2 basket")
    parent_l1, default_pi, _ = info
    cik = name = None
    has_sm = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'security_master'").fetchone()
    if has_sm:
        found = conn.execute(
            "SELECT cik, company_name FROM security_master WHERE symbol = ?", (symbol,)).fetchone()
        if found:
            cik, name = found
    conn.execute(
        """INSERT INTO stock_themes (symbol, cik, asset_kind, in_us_basket_calc, is_superseded,
               l1_id, l2_id, pi_bucket, business_note, company_name_snapshot, source,
               agreement_status, assigned_at, updated_at)
           VALUES (?, ?, 'us_equity', 1, 0, ?, ?, ?, ?, ?, ?, 'locked', ?, ?)
           ON CONFLICT(symbol) DO UPDATE SET
               l1_id = excluded.l1_id,
               l2_id = excluded.l2_id,
               source = excluded.source,
               agreement_status = 'locked',
               business_note = CASE WHEN coalesce(stock_themes.business_note, '') = ''
                                    THEN excluded.business_note
                                    ELSE stock_themes.business_note END,
               updated_at = excluded.updated_at
           WHERE stock_themes.l1_id IS NOT excluded.l1_id
              OR stock_themes.l2_id IS NOT excluded.l2_id
              OR stock_themes.source IS NOT excluded.source
              OR stock_themes.agreement_status IS NOT 'locked'
              OR (coalesce(stock_themes.business_note, '') = ''
                  AND coalesce(excluded.business_note, '') <> '')""",
        (symbol, cik, parent_l1, l2_id, default_pi, note, name, source, ts, ts),
    )
    _sync_cross(conn, symbol, pairs, source, ts)


def _downgrade(conn: sqlite3.Connection, symbol: str, from_source: str, ts: str) -> None:
    conn.execute(
        "UPDATE stock_themes SET source = 'agy_llm', agreement_status = 'needs_spot_check', "
        "updated_at = ? WHERE symbol = ? AND source = ?", (ts, symbol, from_source))
    conn.execute(
        "UPDATE stock_cross_themes SET source = 'agy_llm' WHERE symbol = ? AND source = ?",
        (symbol, from_source))


# ---------------------------------------------------------------- envelope: dictionary

_TIER_ORDER = ("pi_bucket", "l1", "l2", "cross")

_UPSERT_BASKET = """
INSERT INTO theme_baskets (basket_id, label_zh, tier, parent_l1_id, default_pi_bucket, sort_order,
    min_active_members, narrative_start_date, inclusion_rule, exclusion_rule, is_active, updated_at)
VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
ON CONFLICT(basket_id) DO UPDATE SET
    label_zh = excluded.label_zh,
    tier = excluded.tier,
    parent_l1_id = excluded.parent_l1_id,
    default_pi_bucket = excluded.default_pi_bucket,
    sort_order = excluded.sort_order,
    min_active_members = excluded.min_active_members,
    narrative_start_date = excluded.narrative_start_date,
    inclusion_rule = excluded.inclusion_rule,
    exclusion_rule = excluded.exclusion_rule,
    is_active = CASE WHEN excluded.is_active = 1 THEN 1 ELSE theme_baskets.is_active END,
    updated_at = excluded.updated_at
WHERE theme_baskets.label_zh IS NOT excluded.label_zh
   OR theme_baskets.tier IS NOT excluded.tier
   OR theme_baskets.parent_l1_id IS NOT excluded.parent_l1_id
   OR theme_baskets.default_pi_bucket IS NOT excluded.default_pi_bucket
   OR theme_baskets.sort_order IS NOT excluded.sort_order
   OR theme_baskets.min_active_members IS NOT excluded.min_active_members
   OR theme_baskets.narrative_start_date IS NOT excluded.narrative_start_date
   OR theme_baskets.inclusion_rule IS NOT excluded.inclusion_rule
   OR theme_baskets.exclusion_rule IS NOT excluded.exclusion_rule
   OR (excluded.is_active = 1 AND theme_baskets.is_active = 0)
"""


def _sync_baskets(conn: sqlite3.Connection, baskets_doc: Mapping[str, Any], ts: str) -> None:
    """Upsert in pi_bucket → l1 → l2 → cross order, re-derive l1_id, then deactivate in reverse."""
    items = baskets_doc["baskets"]
    ids = [b["basket_id"] for b in items]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate basket_id in baskets_doc")
    for b in items:
        if b["tier"] not in _TIER_ORDER:
            raise ValueError(f"{b['basket_id']}: unknown tier {b['tier']!r}")
        if b["is_active"] not in (0, 1):
            raise ValueError(f"{b['basket_id']}: is_active must be 0 or 1")
    ordered = sorted(items, key=lambda b: _TIER_ORDER.index(b["tier"]))
    for b in ordered:
        conn.execute(_UPSERT_BASKET, (
            b["basket_id"], b["label_zh"], b["tier"], b.get("parent_l1_id"),
            b.get("default_pi_bucket"), b.get("sort_order", 0), b.get("min_active_members", 0),
            b.get("narrative_start_date"), b["inclusion_rule"], b["exclusion_rule"],
            int(b["is_active"]), ts,
        ))
    conn.execute(
        """UPDATE stock_themes
           SET l1_id = (SELECT b.parent_l1_id FROM theme_baskets b WHERE b.basket_id = stock_themes.l2_id),
               updated_at = ?
           WHERE l2_id IS NOT NULL
             AND l1_id IS NOT (SELECT b.parent_l1_id FROM theme_baskets b
                               WHERE b.basket_id = stock_themes.l2_id)""", (ts,))
    for b in reversed(ordered):
        if b["is_active"] == 0:
            conn.execute(
                "UPDATE theme_baskets SET is_active = 0, updated_at = ? "
                "WHERE basket_id = ? AND is_active = 1", (ts, b["basket_id"]))


# ---------------------------------------------------------------- envelope: rows

_ROW_REQUIRED = frozenset({"symbol", "asset_kind", "in_us_basket_calc", "pi_bucket", "agreement_status"})
_ROW_OPTIONAL = frozenset({
    "cik", "is_superseded", "superseded_by", "l2_id", "business_note", "profile_sector",
    "profile_industry", "company_name_snapshot", "cross_themes",
})
_ROW_TEXT = ("cik", "superseded_by", "l2_id", "business_note", "profile_sector",
             "profile_industry", "company_name_snapshot")


def _insert_row(conn: sqlite3.Connection, row: Any, ts: str) -> None:
    """Plain INSERT of one new classification row (source='agy_llm'); never overwrites."""
    if not isinstance(row, Mapping):
        raise TypeError("row must be a mapping")
    missing = sorted(_ROW_REQUIRED - set(row))
    unknown = sorted(set(row) - _ROW_REQUIRED - _ROW_OPTIONAL)
    if missing or unknown:
        raise ValueError(f"missing keys {missing}, unknown keys {unknown}")
    for key in ("symbol", "asset_kind", "pi_bucket", "agreement_status"):
        if type(row[key]) is not str:
            raise TypeError(f"{key} must be str")
    for key in _ROW_TEXT:
        if row.get(key) is not None and type(row[key]) is not str:
            raise TypeError(f"{key} must be str or null")
    in_calc, superseded = row["in_us_basket_calc"], row.get("is_superseded", 0)
    if type(in_calc) is not int or type(superseded) is not int:
        raise TypeError("in_us_basket_calc and is_superseded must be int")
    pairs = _cross_pairs(row.get("cross_themes"))
    symbol = row["symbol"]
    if conn.execute("SELECT 1 FROM stock_themes WHERE symbol = ?", (symbol,)).fetchone():
        raise ValueError(f"{symbol} already exists; the row segment never overwrites")
    l1_id = None
    if row.get("l2_id") is not None:
        info = _l2_info(conn, row["l2_id"])
        if info is None:
            raise ValueError(f"unknown l2_id {row['l2_id']!r}")
        l1_id = info[0]
    conn.execute(
        "INSERT INTO stock_themes (symbol, cik, asset_kind, in_us_basket_calc, is_superseded, "
        "superseded_by, l1_id, l2_id, pi_bucket, business_note, profile_sector, profile_industry, "
        "company_name_snapshot, source, agreement_status, assigned_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'agy_llm',?,?,?)",
        (symbol, row.get("cik"), row["asset_kind"], in_calc, superseded, row.get("superseded_by"),
         l1_id, row.get("l2_id"), row["pi_bucket"], row.get("business_note"),
         row.get("profile_sector"), row.get("profile_industry"), row.get("company_name_snapshot"),
         row["agreement_status"], ts, ts),
    )
    for theme_id, role in pairs:
        conn.execute(
            "INSERT INTO stock_cross_themes (symbol, theme_id, role, source, assigned_at) "
            "VALUES (?,?,?,'agy_llm',?)", (symbol, theme_id, role, ts))


def _insert_rows(conn: sqlite3.Connection, rows: Sequence[Any], ts: str) -> List[Tuple[str, str]]:
    """Each row in its own SAVEPOINT: a bad row is rolled back and quarantined alone."""
    quarantined = []
    for row in rows:
        conn.execute("SAVEPOINT sp_row")
        try:
            _insert_row(conn, row, ts)
        except (sqlite3.Error, KeyError, TypeError, ValueError) as exc:
            if not conn.in_transaction:
                raise  # SQLite already dropped the whole transaction: abort the envelope
            conn.execute("ROLLBACK TO sp_row")
            conn.execute("RELEASE sp_row")
            label = str(row.get("symbol", "")) if isinstance(row, Mapping) else ""
            quarantined.append((label, f"{type(exc).__name__}: {exc}"))
            continue
        conn.execute("RELEASE sp_row")
    return quarantined


# ---------------------------------------------------------------- envelope: overrides

def _sync_overrides(conn: sqlite3.Connection, overrides_doc: Mapping[str, Any], ts: str) -> None:
    locked = overrides_doc["locked_stocks"]
    symbols = [item["symbol"] for item in locked]
    if len(set(symbols)) != len(symbols):
        raise ValueError("duplicate symbol in overrides_doc.locked_stocks")
    for item in locked:
        _upsert_lock(conn, item["symbol"], item["l2_id"], _cross_pairs(item.get("cross_themes")),
                     item.get("rationale"), "override", ts)
    keep = set(symbols)
    stale = [s for (s,) in conn.execute(
        "SELECT symbol FROM stock_themes WHERE source = 'override' ORDER BY symbol") if s not in keep]
    for symbol in stale:
        _downgrade(conn, symbol, "override", ts)


# ---------------------------------------------------------------- public write API

def apply_envelope(conn: sqlite3.Connection, *, baskets_doc: Mapping[str, Any],
                   overrides_doc: Mapping[str, Any], rows: Sequence[Mapping[str, Any]],
                   payload_id: str, now: Optional[datetime] = None) -> ApplyResult:
    """Apply one sync envelope in a single BEGIN IMMEDIATE transaction.

    Dictionary + public locks always run; the new-row segment runs only the first time
    `payload_id` is seen. `inserted` counts new stock_themes rows, `updated` counts
    existing rows whose classification snapshot changed.
    """
    rows = list(rows)
    for name, doc in (("baskets_doc", baskets_doc), ("overrides_doc", overrides_doc)):
        version = doc.get("schema_version")
        if type(version) is not int or version != 1:
            raise ValueError(f"{name}.schema_version must be 1, got {version!r}")
    if compute_payload_id(rows) != payload_id:
        raise ValueError("payload_id does not match the rows")
    ts = _utc_ts(now)
    taxonomy = compute_taxonomy_sha256(baskets_doc, overrides_doc)

    with _immediate(conn):
        before = _snapshot(conn)
        replayed = conn.execute(
            "SELECT 1 FROM theme_sync_commits WHERE payload_id = ?", (payload_id,)).fetchone() is not None
        _sync_baskets(conn, baskets_doc, ts)
        quarantined = [] if replayed else _insert_rows(conn, _sorted_rows(rows), ts)
        _sync_overrides(conn, overrides_doc, ts)
        after = _snapshot(conn)
        history = _append_history(conn, before, after, ts, _envelope_reason)
        inserted = len(after.keys() - before.keys())
        updated = history - inserted
        conn.execute(
            "INSERT INTO theme_sync_commits (payload_id, taxonomy_sha256, applied_at, "
            "inserted_count, updated_count, quarantined_count) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(payload_id) DO UPDATE SET taxonomy_sha256 = excluded.taxonomy_sha256, "
            "applied_at = excluded.applied_at",
            (payload_id, taxonomy, ts, inserted, updated, len(quarantined)),
        )
    return ApplyResult(payload_id, taxonomy, replayed, inserted, updated, quarantined, history)


def lock_private_symbol(conn: sqlite3.Connection, symbol: str, *, l2_id: str, cross_themes=(),
                        business_note: Optional[str] = None, now: Optional[datetime] = None) -> None:
    """Lock a private (non-public) holding as source='db_locked'. Refuses public overrides.

    An explicit business_note replaces the stored note; None keeps it.
    """
    ts = _utc_ts(now)
    pairs = _cross_pairs(list(cross_themes))
    with _immediate(conn):
        existing = conn.execute("SELECT source FROM stock_themes WHERE symbol = ?", (symbol,)).fetchone()
        if existing is not None and existing[0] == "override":
            raise ValueError(f"{symbol} is a public override; edit overrides.json instead")
        before = _snapshot(conn, symbol)
        _upsert_lock(conn, symbol, l2_id, pairs, business_note, "db_locked", ts)
        if business_note is not None:
            conn.execute("UPDATE stock_themes SET business_note = ?, updated_at = ? "
                         "WHERE symbol = ? AND business_note IS NOT ?", (business_note, ts, symbol, business_note))
        _append_history(conn, before, _snapshot(conn, symbol), ts, "db_lock")


def unlock_private_symbol(conn: sqlite3.Connection, symbol: str, *, now: Optional[datetime] = None) -> None:
    """Release a db_locked symbol back to agy_llm / needs_spot_check."""
    ts = _utc_ts(now)
    with _immediate(conn):
        existing = conn.execute("SELECT source FROM stock_themes WHERE symbol = ?", (symbol,)).fetchone()
        if existing is None or existing[0] != "db_locked":
            raise ValueError(f"{symbol} is not db_locked")
        before = _snapshot(conn, symbol)
        _downgrade(conn, symbol, "db_locked", ts)
        _append_history(conn, before, _snapshot(conn, symbol), ts, "db_unlock")
