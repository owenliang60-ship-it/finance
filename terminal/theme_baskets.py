"""Short-connection read-only query layer for stock theme baskets (M6).

Every database read opens ``market.db`` with ``?mode=ro`` inside
``contextlib.closing`` and closes immediately so local ``market.db`` never
holds lingering file handles.

Not part of ``terminal/prosperity/version.py:CODE_VERSION_SOURCES``.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

from config.settings import MARKET_DB_PATH
from src.data.symbol_aliases import resolve_alias

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ALIASES_PATH = REPO_ROOT / "config" / "symbol_aliases.json"
DEFAULT_OVERRIDES_PATH = REPO_ROOT / "config" / "theme_baskets" / "overrides.json"
DEFAULT_PI_BUCKET_LABEL = "其他"
SHANGHAI = timezone(timedelta(hours=8))

PathLike = Union[str, Path]

_WRAPPER_RE = re.compile(r"^(.+?)\s+(LEAPS|CALL|PUT)$")
_US_EXCHANGE_RE = re.compile(r"^(?:NASDAQ|NYSE|NYSEARCA|NYSEAMERICAN|AMEX|BATS|ARCA|CBOE):")
_MARKET_PREFIXES = {
    "HKG": ("HK", 4), "HK": ("HK", 4), "HKEX": ("HK", 4),
    "KRX": ("KR", 6), "KR": ("KR", 6), "KOSPI": ("KR", 6), "KOSDAQ": ("KR", 6),
    "SSE": ("CN", 6), "SZSE": ("CN", 6), "SHA": ("CN", 6), "SHE": ("CN", 6), "CN": ("CN", 6),
    "CRYPTO": ("CRYPTO", 0),
}
_MARKET_BY_COL = {"HK": ("HK", 4), "KR": ("KR", 6), "CN": ("CN", 6), "CRYPTO": ("CRYPTO", 0)}
_MAX_ALIAS_HOPS = 5
_ACTIVE_HMC_MIN_USD = 10_000_000_000  # $10B 10-day fallback floor


@dataclass(frozen=True)
class InstrumentRef:
    lookup_key: str
    wrapper_kind: str   # equity | leaps | call | put
    raw_symbol: str
    market: str         # US | HK | KR | CN | CRYPTO


def _today_shanghai() -> str:
    return datetime.now(timezone.utc).astimezone(SHANGHAI).date().isoformat()


def _open_ro(db_path: PathLike) -> sqlite3.Connection:
    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _split_market(sym: str, market: str) -> Optional[Tuple[str, str, int]]:
    if ":" in sym:
        prefix, rest = sym.split(":", 1)
        if prefix in _MARKET_PREFIXES:
            name, width = _MARKET_PREFIXES[prefix]
            return name, rest, width
    if sym.endswith(".HK"):
        return "HK", sym[:-3], 4
    if sym.endswith((".KS", ".KQ")):
        return "KR", sym[:-3], 6
    if sym.endswith((".SS", ".SZ")):
        return "CN", sym[:-3], 6
    if market in _MARKET_BY_COL:
        name, width = _MARKET_BY_COL[market]
        return name, sym, width
    return None


def _load_aliases_safe(path: Optional[PathLike]) -> List[Dict[str, Any]]:
    doc = _load_json_safe(path)
    if isinstance(doc, Mapping) and isinstance(doc.get("aliases"), list):
        return list(doc["aliases"])
    if isinstance(doc, list):
        return list(doc)
    return []


def _load_json_safe(path: Optional[PathLike]) -> Any:
    if not path:
        return None
    p = Path(path)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _load_db_alias_context(db_path: Optional[PathLike]) -> Tuple[Dict[str, str], set]:
    if not db_path or not Path(db_path).is_file():
        return {}, set()
    try:
        with closing(_open_ro(db_path)) as conn:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "security_master" not in tables:
                return {}, set()
            share_class: Dict[str, str] = {}
            known: set = set()
            for row in conn.execute("SELECT symbol, share_class_of FROM security_master"):
                sym = row[0]
                known.add(sym)
                if row[1]:
                    share_class[sym] = row[1]
            return share_class, known
    except (sqlite3.DatabaseError, OSError):
        return {}, set()


def parse_instrument_symbol(
    raw_symbol: str,
    market: str = "US",
    *,
    db_path: Optional[PathLike] = MARKET_DB_PATH,
    as_of_date: Optional[str] = None,
    aliases_path: Optional[PathLike] = DEFAULT_ALIASES_PATH,
    overrides_path: Optional[PathLike] = DEFAULT_OVERRIDES_PATH,
    _aliases_cache: Optional[Sequence[Mapping[str, Any]]] = None,
    _supplemental_cache: Optional[Sequence[Mapping[str, Any]]] = None,
    _share_class_cache: Optional[Mapping[str, str]] = None,
    _known_us_cache: Optional[set] = None,
) -> Optional[InstrumentRef]:
    """Normalize raw symbol + market into canonical stock_themes lookup key."""
    sym = str(raw_symbol or "").strip().upper()
    mkt = str(market or "US").strip().upper()
    wrapper = "equity"
    match = _WRAPPER_RE.match(sym)
    if match:
        sym, wrapper = match.group(1).strip(), match.group(2).lower()
    if not sym:
        return None

    split = _split_market(sym, mkt)
    if split is not None:
        name, code, width = split
        code = code.strip()
        if not code:
            return None
        if width and code.isascii() and code.isdigit():
            code = "%04d" % int(code) if name == "HK" else code.zfill(width)
        return InstrumentRef(f"{name}:{code}", wrapper, raw_symbol, name)

    sym = _US_EXCHANGE_RE.sub("", sym)
    if _share_class_cache is not None and _known_us_cache is not None:
        share_class_map, known_us = _share_class_cache, _known_us_cache
    else:
        share_class_map, known_us = _load_db_alias_context(db_path)

    if "." in sym and sym.replace(".", "-") in known_us:
        sym = sym.replace(".", "-")

    as_of = as_of_date or _today_shanghai()
    aliases = list(_aliases_cache) if _aliases_cache is not None else _load_aliases_safe(aliases_path)
    if _supplemental_cache is not None:
        supp_raw = _supplemental_cache
    else:
        ov = _load_json_safe(overrides_path) or {}
        supp_raw = ov.get("supplemental_aliases") or []
    supplemental = {
        a["alias_symbol"]: a["canonical_symbol"]
        for a in supp_raw
        if a.get("effective_from") is None or a["effective_from"] <= as_of
    }
    for _ in range(_MAX_ALIAS_HOPS):
        before = sym
        sym = resolve_alias(supplemental.get(sym, sym), as_of, aliases)
        if sym is None:
            return None
        sym = share_class_map.get(sym, sym)
        if sym == before:
            break
    if not sym:
        return None
    return InstrumentRef(sym, wrapper, raw_symbol, "US")


def active_us_member_sql(
    table_alias: str = "s",
    *,
    has_hmc: bool = True,
    max_hmc_date: Optional[str] = None,
) -> str:
    """Unified active US basket-member predicate (with 10-day $10B market-cap fallback).

    Shared by ``get_basket_members(scope='active')`` and ``data_health._theme_baskets_result``.
    """
    a = table_alias
    strict_clause = (
        f"EXISTS (SELECT 1 FROM extended_membership m "
        f"JOIN security_master sm ON sm.symbol = m.symbol "
        f"WHERE m.symbol = {a}.symbol AND m.effective_to IS NULL AND sm.eligible = 1)"
    )
    if not has_hmc or not max_hmc_date:
        return f"{a}.in_us_basket_calc = 1 AND {a}.is_superseded = 0 AND {strict_clause}"
    # Validate ISO date format before interpolating into SQL literal.
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", max_hmc_date):
        raise ValueError(f"Invalid max_hmc_date: {max_hmc_date!r}")
    fallback_clause = (
        f"(EXISTS (SELECT 1 FROM security_master sm WHERE sm.symbol = {a}.symbol AND sm.eligible = 1) "
        f"AND EXISTS (SELECT 1 FROM extended_membership m WHERE m.symbol = {a}.symbol) "
        f"AND EXISTS (SELECT 1 FROM historical_market_cap h "
        f"WHERE h.symbol = {a}.symbol AND h.date >= date('{max_hmc_date}', '-10 days') "
        f"AND h.market_cap >= {_ACTIVE_HMC_MIN_USD}))"
    )
    return f"{a}.in_us_basket_calc = 1 AND {a}.is_superseded = 0 AND ({strict_clause} OR {fallback_clause})"


def _max_hmc_date(conn: sqlite3.Connection, tables: set) -> Optional[str]:
    if "historical_market_cap" not in tables:
        return None
    row = conn.execute("SELECT MAX(date) FROM historical_market_cap").fetchone()
    return row[0] if row and row[0] else None


def get_stock_themes(
    symbol: str,
    market: str = "US",
    *,
    db_path: PathLike = MARKET_DB_PATH,
) -> Optional[Dict[str, Any]]:
    """Short-connection lookup of a single symbol's theme classification."""
    p = Path(db_path)
    if not p.is_file():
        return None
    ref = parse_instrument_symbol(symbol, market=market, db_path=p)
    if ref is None:
        return None
    try:
        with closing(_open_ro(p)) as conn:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {"theme_baskets", "stock_themes"}.issubset(tables):
                return None
            row = conn.execute(
                """SELECT s.*,
                          b1.label_zh AS l1_label_zh,
                          b2.label_zh AS l2_label_zh,
                          bp.label_zh AS pi_bucket_label_zh
                   FROM stock_themes s
                   LEFT JOIN theme_baskets b1 ON b1.basket_id = s.l1_id
                   LEFT JOIN theme_baskets b2 ON b2.basket_id = s.l2_id
                   LEFT JOIN theme_baskets bp ON bp.basket_id = s.pi_bucket
                   WHERE s.symbol = ?""",
                (ref.lookup_key,),
            ).fetchone()
            if row is None:
                return None
            cross_rows: List[Dict[str, Any]] = []
            if "stock_cross_themes" in tables:
                for cr in conn.execute(
                    """SELECT x.theme_id, b.label_zh, x.role, x.source
                       FROM stock_cross_themes x
                       JOIN theme_baskets b ON b.basket_id = x.theme_id
                       WHERE x.symbol = ?
                       ORDER BY b.sort_order, x.theme_id""",
                    (ref.lookup_key,),
                ):
                    cross_rows.append({
                        "theme_id": cr["theme_id"],
                        "label_zh": cr["label_zh"],
                        "role": cr["role"],
                        "source": cr["source"],
                    })
            return {
                "symbol": row["symbol"],
                "raw_symbol": ref.raw_symbol,
                "market": ref.market,
                "wrapper_kind": ref.wrapper_kind,
                "cik": row["cik"],
                "asset_kind": row["asset_kind"],
                "in_us_basket_calc": bool(row["in_us_basket_calc"]),
                "is_superseded": bool(row["is_superseded"]),
                "superseded_by": row["superseded_by"],
                "l1_id": row["l1_id"],
                "l1_label_zh": row["l1_label_zh"],
                "l2_id": row["l2_id"],
                "l2_label_zh": row["l2_label_zh"],
                "pi_bucket": row["pi_bucket"],
                "pi_bucket_label_zh": row["pi_bucket_label_zh"],
                "business_note": row["business_note"],
                "source": row["source"],
                "agreement_status": row["agreement_status"],
                "cross_themes": cross_rows,
                "assigned_at": row["assigned_at"],
                "updated_at": row["updated_at"],
            }
    except (sqlite3.DatabaseError, OSError) as exc:
        logger.warning("get_stock_themes(%s) failed: %s", symbol, exc)
        return None


def get_basket_members(
    basket_id: str,
    *,
    scope: str = "active",
    as_of: Optional[str] = None,
    roles: Sequence[str] = ("core", "platform_giant"),
    db_path: PathLike = MARKET_DB_PATH,
) -> Dict[str, Any]:
    """Return members of a theme basket under ``scope='active'`` or ``scope='as_of'``."""
    if scope not in ("active", "as_of"):
        raise ValueError(f"Unsupported scope: {scope!r}")
    if scope == "as_of":
        if not as_of or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", as_of):
            raise ValueError("scope='as_of' requires as_of='YYYY-MM-DD'")

    p = Path(db_path)
    if not p.is_file():
        raise FileNotFoundError(f"market.db not found: {p}")

    with closing(_open_ro(p)) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        b = conn.execute(
            "SELECT basket_id, label_zh, tier, narrative_start_date, is_active "
            "FROM theme_baskets WHERE basket_id = ?",
            (basket_id,),
        ).fetchone()
        if b is None:
            raise KeyError(f"Unknown basket_id: {basket_id!r}")

        tier = b["tier"]
        narrative_start_date = b["narrative_start_date"]

        if scope == "active":
            max_hmc = _max_hmc_date(conn, tables)
            pred = active_us_member_sql("s", has_hmc=bool(max_hmc), max_hmc_date=max_hmc)
            if tier == "l1":
                rows = conn.execute(
                    f"SELECT s.symbol FROM stock_themes s WHERE s.l1_id = ? AND {pred} ORDER BY s.symbol",
                    (basket_id,),
                ).fetchall()
                members = [r[0] for r in rows]
            elif tier == "l2":
                rows = conn.execute(
                    f"SELECT s.symbol FROM stock_themes s WHERE s.l2_id = ? AND {pred} ORDER BY s.symbol",
                    (basket_id,),
                ).fetchall()
                members = [r[0] for r in rows]
            elif tier == "cross":
                role_tuple = tuple(roles)
                if not role_tuple:
                    members = []
                else:
                    placeholders = ",".join("?" for _ in role_tuple)
                    rows = conn.execute(
                        f"""SELECT x.symbol FROM stock_cross_themes x
                            JOIN stock_themes s ON s.symbol = x.symbol
                            WHERE x.theme_id = ? AND x.role IN ({placeholders}) AND {pred}
                            ORDER BY x.symbol""",
                        (basket_id, *role_tuple),
                    ).fetchall()
                    members = [r[0] for r in rows]
            else:  # pi_bucket
                rows = conn.execute(
                    "SELECT s.symbol FROM stock_themes s WHERE s.pi_bucket = ? AND s.is_superseded = 0 ORDER BY s.symbol",
                    (basket_id,),
                ).fetchall()
                members = [r[0] for r in rows]
            return {
                "basket_id": basket_id,
                "tier": tier,
                "label_zh": b["label_zh"],
                "scope": "active",
                "as_of": None,
                "pit_basis": "live",
                "narrative_start_date": narrative_start_date,
                "members": members,
            }

        # scope == "as_of"
        assert as_of is not None
        min_hist = None
        if "stock_theme_history" in tables:
            row = conn.execute("SELECT MIN(effective_from) FROM stock_theme_history").fetchone()
            min_hist = row[0] if row else None

        active_on_date: Optional[set] = None
        if "extended_membership" in tables:
            active_on_date = {
                r[0]
                for r in conn.execute(
                    """SELECT DISTINCT symbol FROM extended_membership
                       WHERE effective_from <= ? AND (effective_to IS NULL OR effective_to > ?)""",
                    (as_of, as_of),
                )
            }

        pit_basis = "history_exact"
        if min_hist is None or as_of < min_hist:
            pit_basis = "backfill_approximate"
            # Earliest known history snapshot per symbol (or current stock_themes if history empty)
            if min_hist is not None:
                hist_rows = conn.execute(
                    """SELECT h.* FROM stock_theme_history h
                       JOIN (
                           SELECT symbol, MIN(id) AS first_id
                           FROM stock_theme_history
                           GROUP BY symbol
                       ) f ON f.first_id = h.id"""
                ).fetchall()
            else:
                hist_rows = []
        else:
            hist_rows = conn.execute(
                """SELECT h.* FROM stock_theme_history h
                   JOIN (
                       SELECT symbol, MAX(id) AS latest_id
                       FROM stock_theme_history
                       WHERE effective_from <= ?
                       GROUP BY symbol
                   ) l ON l.latest_id = h.id""",
                (as_of,),
            ).fetchall()

        role_set = set(roles)
        members = []
        for hr in hist_rows:
            sym = hr["symbol"]
            if tier in ("l1", "l2", "cross"):
                if not hr["in_us_basket_calc"] or hr["is_superseded"]:
                    continue
                if active_on_date is not None and sym not in active_on_date:
                    continue
            else:
                if hr["is_superseded"]:
                    continue

            if tier == "l1" and hr["l1_id"] == basket_id:
                members.append(sym)
            elif tier == "l2" and hr["l2_id"] == basket_id:
                members.append(sym)
            elif tier == "pi_bucket" and hr["pi_bucket"] == basket_id:
                members.append(sym)
            elif tier == "cross":
                try:
                    ct_list = json.loads(hr["cross_themes_json"] or "[]")
                except ValueError:
                    ct_list = []
                if any(
                    isinstance(c, Mapping)
                    and c.get("theme_id") == basket_id
                    and c.get("role") in role_set
                    for c in ct_list
                ):
                    members.append(sym)

        members.sort()
        return {
            "basket_id": basket_id,
            "tier": tier,
            "label_zh": b["label_zh"],
            "scope": "as_of",
            "as_of": as_of,
            "pit_basis": pit_basis,
            "narrative_start_date": narrative_start_date,
            "members": members,
        }


class ThemeBasketAdapter:
    """In-memory snapshot loaded via one short ``?mode=ro`` read for morning_report & PI."""

    def __init__(
        self,
        db_path: PathLike = MARKET_DB_PATH,
        *,
        aliases_path: PathLike = DEFAULT_ALIASES_PATH,
        overrides_path: PathLike = DEFAULT_OVERRIDES_PATH,
    ) -> None:
        self.db_path = Path(db_path)
        self._aliases = _load_aliases_safe(aliases_path)
        ov = _load_json_safe(overrides_path) or {}
        self._supplemental = list(ov.get("supplemental_aliases") or [])
        self._share_class: Dict[str, str] = {}
        self._known_us: set = set()
        self._baskets: Dict[str, Dict[str, Any]] = {}
        self._stocks: Dict[str, Dict[str, Any]] = {}
        self._cross: Dict[str, List[Tuple[str, str]]] = {}
        self.l2_bucket_order: List[str] = []
        self.pi_bucket_order: List[str] = []
        self.is_available: bool = False
        self._load()

    def _load(self) -> None:
        if not self.db_path.is_file():
            return
        try:
            with closing(_open_ro(self.db_path)) as conn:
                tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not {"theme_baskets", "stock_themes"}.issubset(tables):
                    return
                for r in conn.execute(
                    "SELECT basket_id, label_zh, tier, parent_l1_id, default_pi_bucket, sort_order, is_active "
                    "FROM theme_baskets ORDER BY sort_order, basket_id"
                ):
                    self._baskets[r["basket_id"]] = dict(r)
                for r in conn.execute(
                    "SELECT symbol, asset_kind, in_us_basket_calc, is_superseded, l1_id, l2_id, "
                    "pi_bucket, business_note FROM stock_themes"
                ):
                    self._stocks[r["symbol"]] = dict(r)
                if "stock_cross_themes" in tables:
                    for r in conn.execute(
                        """SELECT x.symbol, x.theme_id, b.label_zh
                           FROM stock_cross_themes x
                           JOIN theme_baskets b ON b.basket_id = x.theme_id
                           ORDER BY b.sort_order, x.theme_id"""
                    ):
                        self._cross.setdefault(r["symbol"], []).append((r["theme_id"], r["label_zh"]))
                if "security_master" in tables:
                    for r in conn.execute("SELECT symbol, share_class_of FROM security_master"):
                        self._known_us.add(r[0])
                        if r[1]:
                            self._share_class[r[0]] = r[1]
        except (sqlite3.DatabaseError, OSError) as exc:
            logger.warning("ThemeBasketAdapter failed to load %s: %s", self.db_path, exc)
            return

        if not self._baskets or not self._stocks:
            return

        l2_labels = [
            b["label_zh"]
            for b in self._baskets.values()
            if b["tier"] == "l2" and b["is_active"]
        ]
        # Append ETF/macro and fallback '其他' at the tail for morning_report grouping
        for tail in ("ETF/宏观工具", DEFAULT_PI_BUCKET_LABEL):
            if tail not in l2_labels:
                l2_labels.append(tail)
        self.l2_bucket_order = l2_labels

        pi_labels = [
            b["label_zh"]
            for b in self._baskets.values()
            if b["tier"] == "pi_bucket" and b["is_active"]
        ]
        if DEFAULT_PI_BUCKET_LABEL not in pi_labels:
            pi_labels.append(DEFAULT_PI_BUCKET_LABEL)
        self.pi_bucket_order = pi_labels
        self.is_available = True

    def _resolve_row(self, item: Union[Mapping[str, Any], str], market: Optional[str] = None) -> Optional[Dict[str, Any]]:
        if isinstance(item, str):
            raw_sym = item
            mkt = market or "US"
        else:
            raw_sym = str(item.get("symbol") or "")
            mkt = market or str(item.get("market") or "US")
        ref = parse_instrument_symbol(
            raw_sym,
            market=mkt,
            db_path=None,
            _aliases_cache=self._aliases,
            _supplemental_cache=self._supplemental,
            _share_class_cache=self._share_class,
            _known_us_cache=self._known_us,
        )
        if ref is None:
            return None
        row = self._stocks.get(ref.lookup_key)
        if row is not None:
            return row
        # Fallback: direct raw uppercase key if already canonical
        return self._stocks.get(raw_sym.strip().upper())

    def classify_l2(self, item: Union[Mapping[str, Any], str], market: Optional[str] = None) -> str:
        row = self._resolve_row(item, market=market)
        if row is None:
            sym = item if isinstance(item, str) else item.get("symbol", "?")
            logger.warning("theme_baskets: symbol %r not found in stock_themes, fallback to %s", sym, DEFAULT_PI_BUCKET_LABEL)
            return DEFAULT_PI_BUCKET_LABEL
        l2_id = row.get("l2_id")
        if l2_id and l2_id in self._baskets:
            return self._baskets[l2_id]["label_zh"]
        pi_id = row.get("pi_bucket")
        if pi_id and pi_id in self._baskets:
            return self._baskets[pi_id]["label_zh"]
        return DEFAULT_PI_BUCKET_LABEL

    def classify_pi(self, item: Union[Mapping[str, Any], str], market: Optional[str] = None) -> str:
        row = self._resolve_row(item, market=market)
        if row is None:
            sym = item if isinstance(item, str) else item.get("symbol", "?")
            logger.warning("theme_baskets: PI symbol %r (market=%s) not found in stock_themes, fallback to %s",
                           sym, market or (item.get("market") if isinstance(item, Mapping) else "US"),
                           DEFAULT_PI_BUCKET_LABEL)
            return DEFAULT_PI_BUCKET_LABEL
        pi_id = row.get("pi_bucket")
        if pi_id and pi_id in self._baskets:
            return self._baskets[pi_id]["label_zh"]
        logger.warning("theme_baskets: PI bucket %r missing from theme_baskets, fallback to %s",
                       pi_id, DEFAULT_PI_BUCKET_LABEL)
        return DEFAULT_PI_BUCKET_LABEL

    def display_tags(self, item: Union[Mapping[str, Any], str], market: Optional[str] = None) -> str:
        row = self._resolve_row(item, market=market)
        if row is None:
            return ""
        parts: List[str] = []
        l1_id, l2_id = row.get("l1_id"), row.get("l2_id")
        if l1_id and l1_id in self._baskets:
            parts.append(self._baskets[l1_id]["label_zh"])
        if l2_id and l2_id in self._baskets:
            l2_lbl = self._baskets[l2_id]["label_zh"]
            if l2_lbl not in parts:
                parts.append(l2_lbl)
        cross = self._cross.get(row["symbol"]) or []
        if cross:
            c_lbl = cross[0][1]
            if c_lbl not in parts:
                parts.append(c_lbl)
        if parts:
            return " / ".join(parts)
        pi_id = row.get("pi_bucket")
        if pi_id and pi_id in self._baskets:
            return self._baskets[pi_id]["label_zh"]
        return ""

    def business_role(self, item: Union[Mapping[str, Any], str], market: Optional[str] = None) -> str:
        row = self._resolve_row(item, market=market)
        if row is None:
            return ""
        note = (row.get("business_note") or "").strip()
        if note:
            return note
        l2_id = row.get("l2_id")
        if l2_id and l2_id in self._baskets:
            return self._baskets[l2_id]["label_zh"]
        pi_id = row.get("pi_bucket")
        if pi_id and pi_id in self._baskets:
            return self._baskets[pi_id]["label_zh"]
        return ""

    def group_items(self, items: Sequence[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for item in items:
            bucket = self.classify_l2(item)
            grouped.setdefault(bucket, []).append(item)
        ordered: Dict[str, List[Dict[str, Any]]] = {}
        for bucket in self.l2_bucket_order:
            if bucket in grouped:
                ordered[bucket] = grouped.pop(bucket)
        for bucket, bucket_items in grouped.items():
            ordered[bucket] = bucket_items
        return ordered

    # Compatibility aliases for morning_report grouping/display
    def _active_bucket_order(self) -> List[str]:
        return list(self.l2_bucket_order)

    def classify(self, item: Union[Mapping[str, Any], str], market: Optional[str] = None) -> str:
        return self.classify_l2(item, market=market)

    def _grouping_bucket(self, item: Union[Mapping[str, Any], str], market: Optional[str] = None) -> str:
        return self.classify_l2(item, market=market)


_DEFAULT_ADAPTER: Optional[ThemeBasketAdapter] = None


def get_theme_basket_adapter() -> ThemeBasketAdapter:
    global _DEFAULT_ADAPTER
    if _DEFAULT_ADAPTER is None:
        _DEFAULT_ADAPTER = ThemeBasketAdapter()
    return _DEFAULT_ADAPTER


def reset_theme_basket_adapter() -> None:
    global _DEFAULT_ADAPTER
    _DEFAULT_ADAPTER = None
