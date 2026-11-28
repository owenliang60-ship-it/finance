#!/usr/bin/env python3
"""股票主题分类 M3：仓外三明治同步 CLI。

Step A（本机）  --detect-delta      只读扫 Extended ∪ Sheet ∪ watchlist，写 inbox/delta.json
                                    （0 待标且 0 异动时同时写 0 行 outbox/payload.json）。
Step C（本机）  --commit-outbox     硬门控 → 覆盖确定性字段 → :memory: 预演 → 先重放 pending/
                                    → SSH 发双段信封；锁忙/断网则封存到 pending/<payload_id>.json。
               --sync-config       只发最新字典 + 公开锁定（0 行）。
               --replay-pending    原样重发 pending/ 封存包（拼最新字典）。
云端           --apply-cloud-stdin 锁外条件备份 → 15 s market_db_writer 锁 → apply_envelope
                                    → 释放锁后发 Telegram；stdout 只有一行 JSON。
私密锁定       --lock-private / --unlock-private（本机经 SSH；云端加 --cloud-local）。

兼容约束：云端 /usr/bin/python3 3.10 + SQLite 3.37.2。src.data.* 只在函数内导入：
`src/data/__init__.py` 会初始化整个数据包并打日志，不能在导入本模块时触发。
本文件不属于 terminal/prosperity/version.py 的 CODE_VERSION_SOURCES。
"""
import argparse
import contextlib
import fcntl
import gzip
import hashlib
import io
import json
import logging
import math
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import AbstractSet, Any, Callable, Dict, List, Mapping, NamedTuple, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DEFAULT_EXCHANGE_DIR = Path.home() / ".local" / "share" / "finance-theme-sync"
EXCHANGE_SUBDIRS = ("inbox", "outbox", "pending", "db_locked_backup")
DEFAULT_BASKETS = REPO_ROOT / "config" / "theme_baskets" / "baskets.json"
DEFAULT_OVERRIDES = REPO_ROOT / "config" / "theme_baskets" / "overrides.json"
DEFAULT_ALIASES = REPO_ROOT / "config" / "symbol_aliases.json"
DEFAULT_REPORT_CONCEPTS = REPO_ROOT / "config" / "concepts" / "report_concepts.json"
DEFAULT_MARKET_DB = REPO_ROOT / "data" / "market.db"
DEFAULT_COMPANY_DB = REPO_ROOT / "data" / "company.db"

SHANGHAI = timezone(timedelta(hours=8))  # Asia/Shanghai has no DST
SCHEMA_VERSION = 1
EXIT_OK = 0
EXIT_FAIL = 1
EXIT_LOCK_BUSY = 75                # scripts/backfill_extended_fundamentals.py:EXIT_LOCK_BUSY

LOCK_TIMEOUT_SEC = 15.0
BACKUP_LABEL = "auto-theme-sync"
BACKUP_SPACE_FACTOR = 1.5          # scripts/build_company_concept_registry.py:BACKUP_SPACE_FACTOR
REMOTE_HOST = "aliyun"             # sync_to_cloud.sh:REMOTE_HOST
REMOTE_DIR = "/root/workspace/Finance"
REMOTE_PYTHON = "/usr/bin/python3"
SSH_TIMEOUT_SEC = 1800             # covers a 1.5 GB backup + gzip of the previous one
LSOF = "/usr/sbin/lsof" if Path("/usr/sbin/lsof").exists() else (shutil.which("lsof") or "/usr/sbin/lsof")

logger = logging.getLogger("theme_basket_sync")

SHEET_TAB = "Summary_OSV"          # portfolio/holdings/sheet_book.py:SUMMARY_TAB
SHEET_HEADER_SCAN_ROWS = 15        # sheet_book.py:MAX_HEADER_SCAN_ROWS
SHEET_COLUMNS = ("Stock Ticker", "Market", "Shares")
ALLOWED_SHEET_MARKETS = frozenset({"US", "HK", "KR", "CN", "CRYPTO"})

AUTO_KINDS = frozenset({"etf", "fund_cef", "crypto", "custom_basket", "other"})
ASSET_KINDS = AUTO_KINDS | {"us_equity", "non_us_equity"}   # stock_themes.asset_kind CHECK
CUSTOM_BASKETS = frozenset({"DRAM", "SOX"})
PLACEHOLDER_SYMBOLS = frozenset({"TEST"})
# US ETFs held or watched without a security_master / report_concepts.json row (e.g. "TQQQ PUT").
COMMON_US_ETFS = frozenset({
    "SPY", "QQQ", "IWM", "DIA", "SOXX", "SMH", "XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP",
    "XLU", "XLB", "XLRE", "XLC", "TQQQ", "SQQQ", "SOXL", "SOXS", "TLT", "GLD", "SLV", "IBIT",
    "FBTC", "ETHA", "ARKK", "BTC", "ETH"})  # BTC/ETH: Grayscale mini trusts; Market=CRYPTO → CRYPTO:
ROW_META_FIELDS = ("symbol", "cik", "asset_kind", "in_us_basket_calc", "profile_sector",
                   "profile_industry", "company_name_snapshot")
DEFAULT_AGREEMENT = "single_pass_weekly"


# ---------------------------------------------------------------- small helpers

def today_shanghai(now: Optional[datetime] = None) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(SHANGHAI).date().isoformat()


def utc_stamp(now: Optional[datetime] = None) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_delta_sha256(delta: Mapping[str, Any]) -> str:
    body = {k: v for k, v in delta.items() if k != "delta_sha256"}
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def load_json(path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def atomic_write_json(path, obj: Any) -> None:
    """Write via a 0600 temp file in the same directory, fsync, then os.replace."""
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, str(path))
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


def ensure_exchange_dirs(exchange_dir) -> Dict[str, Path]:
    root = Path(exchange_dir).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    dirs = {}
    for name in EXCHANGE_SUBDIRS:
        sub = root / name
        sub.mkdir(exist_ok=True)
        os.chmod(sub, 0o700)
        dirs[name] = sub
    return dirs


def ro_connect(path) -> sqlite3.Connection:
    """Read-only URI connection; callers wrap it in contextlib.closing."""
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)


def _text(value: Any) -> Optional[str]:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _norm_cik(val: Any) -> Optional[str]:
    """SEC CIK as 10 zero-padded digits, so '123456' and '0000123456' compare equal.

    Anything else ('N/A', '-', 11+ digits) is None: two unknown CIKs must never dedupe.
    """
    s = "" if val is None else str(val).strip()
    if s.isascii() and s.isdigit() and len(s) <= 10 and s.lstrip("0"):
        return s.lstrip("0").zfill(10)
    return None


def main_repo_root() -> Path:
    from src.data.theme_basket_store import _main_repo_root

    return _main_repo_root(REPO_ROOT) or REPO_ROOT


def default_pull_runner() -> int:
    root = main_repo_root()
    return subprocess.run([str(root / "sync_to_cloud.sh"), "--pull"], cwd=str(root)).returncode


# ---------------------------------------------------------------- symbol normalization

class NormalizedInstrument(NamedTuple):
    canonical_symbol: str
    wrapper_kind: str   # equity / leaps / call / put
    raw_symbol: str
    market: str         # US / HK / KR / CN / CRYPTO


_WRAPPER_RE = re.compile(r"^(.*\S)\s+(LEAPS|CALL|PUT)$")
_US_EXCHANGE_RE = re.compile(r"^(?:NASDAQ|NYSE|AMEX|NYSEAMERICAN|NYSEARCA|BATS|ARCA|CBOE|OTC|OTCMKTS):")
# (market, prefixes, suffixes, zero-pad width for numeric codes)
_MARKET_RULES = (
    ("HK", ("HKG:", "HK:"), (".HK",), 4),
    ("KR", ("KRX:", "KOSDAQ:", "KR:"), (".KS", ".KQ"), 6),
    ("CN", ("SHA:", "SHE:", "SSE:", "SZSE:", "CN:"), (".SS", ".SZ"), 6),
    ("CRYPTO", ("CRYPTO:",), (), 0),
)
_MAX_ALIAS_HOPS = 5


def _split_market(sym: str, market: str) -> Optional[Tuple[str, str, int]]:
    """(market, code, pad width) for a non-US listing; explicit prefix/suffix beats `market`."""
    for name, prefixes, suffixes, width in _MARKET_RULES:
        for prefix in prefixes:
            if sym.startswith(prefix):
                return name, sym[len(prefix):], width
        for suffix in suffixes:
            if sym.endswith(suffix):
                return name, sym[:-len(suffix)], width
    for name, _, _, width in _MARKET_RULES:
        if market == name:
            return name, sym, width
    return None


def normalize_instrument(raw_symbol: str, market: str = "US", *, as_of_date: str,
                         aliases: Sequence[Mapping[str, Any]],
                         supplemental_aliases: Sequence[Mapping[str, Any]],
                         share_class_map: Mapping[str, str],
                         known_us_symbols: AbstractSet[str] = frozenset()) -> Optional[NormalizedInstrument]:
    """Raw sheet/watchlist/pool code → canonical stock_themes key; None = drop the code."""
    from src.data.symbol_aliases import resolve_alias

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
        return NormalizedInstrument(f"{name}:{code}", wrapper, raw_symbol, name)

    sym = _US_EXCHANGE_RE.sub("", sym)
    if "." in sym and sym.replace(".", "-") in known_us_symbols:
        sym = sym.replace(".", "-")
    supplemental = {a["alias_symbol"]: a["canonical_symbol"] for a in supplemental_aliases
                    if a.get("effective_from") is None or a["effective_from"] <= as_of_date}
    alias_list = list(aliases)
    for _ in range(_MAX_ALIAS_HOPS):
        before = sym
        sym = resolve_alias(supplemental.get(sym, sym), as_of_date, alias_list)
        if sym is None:
            return None
        sym = share_class_map.get(sym, sym)
        if sym == before:
            break
    if not sym:
        return None
    return NormalizedInstrument(sym, wrapper, raw_symbol, "US")


_NON_COMMON_RE = re.compile(r"^[A-Z0-9]+-(P[A-Z0-9]*|WS|WT|U|UN)$")


def classify_asset_kind(symbol: str, *, sm_row: Optional[Mapping[str, Any]],
                        profile_meta: Optional[Mapping[str, Any]],
                        extra_etfs: AbstractSet[str]) -> Tuple[str, int]:
    """(asset_kind, in_us_basket_calc), decided only from deterministic metadata."""
    if symbol.startswith(("HK:", "KR:", "CN:")):
        return "non_us_equity", 0
    if symbol.startswith("CRYPTO:"):
        return "crypto", 0
    if symbol in CUSTOM_BASKETS:
        return "custom_basket", 0
    sm = sm_row or {}
    prof = profile_meta or {}
    if (sm.get("is_etf") == 1 or sm.get("reason") == "etf" or prof.get("isEtf") is True
            or symbol in extra_etfs or symbol in COMMON_US_ETFS):
        return "etf", 0
    if sm.get("is_fund") == 1 or sm.get("reason") == "fund" or prof.get("isFund") is True:
        return "fund_cef", 0
    if (symbol in PLACEHOLDER_SYMBOLS or sm.get("reason") == "non_common_instrument"
            or _NON_COMMON_RE.match(symbol)
            or (sm.get("reason") == "missing_profile" and not sm.get("company_name")
                and profile_meta is None)):
        return "other", 0
    # No security_master / profile row: a sub-$10B US stock outside the pool (e.g. GRAL).
    return "us_equity", 1


# ---------------------------------------------------------------- Google Sheet (read-only)

def scan_sheet_tickers_tolerant(xlsx_bytes: bytes) -> List[Tuple[str, str]]:
    """(raw_ticker, market) of Summary_OSV rows with Shares > 0.

    Reads only Stock Ticker / Market / Shares; price, cost and value columns are never
    touched, so '#N/A' prices cannot fail the scan. Raises ValueError on a bad workbook.
    """
    import openpyxl

    try:
        wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes), read_only=True, data_only=True)
    except Exception as exc:
        raise ValueError(f"sheet content is not a valid xlsx: {type(exc).__name__}") from None
    try:
        if SHEET_TAB not in wb.sheetnames:
            raise ValueError(f"sheet tab missing: {SHEET_TAB}")
        header = None
        found = []
        for i, row in enumerate(wb[SHEET_TAB].iter_rows(values_only=True)):
            if header is None:
                if i >= SHEET_HEADER_SCAN_ROWS:
                    break
                cells = [str(c).strip() if c is not None else "" for c in row]
                if set(SHEET_COLUMNS) <= set(cells):
                    header = [cells.index(name) for name in SHEET_COLUMNS]
                continue
            ticker, market, shares = (row[col] if col < len(row) else None for col in header)
            if ticker is None or not str(ticker).strip():
                continue
            if isinstance(shares, str):
                shares = shares.replace(",", "").strip()  # "1,250"
            try:
                value = float(shares)
            except (TypeError, ValueError):
                continue
            if not (math.isfinite(value) and value > 0):
                continue
            raw, mkt = str(ticker).strip(), str(market or "").strip().upper()
            if not mkt and not _US_EXCHANGE_RE.match(raw.upper()) and _split_market(raw.upper(), "") is None:
                raise ValueError(f"missing Market in {SHEET_TAB}")  # never guess: "BTC" is both
            if mkt and mkt not in ALLOWED_SHEET_MARKETS:
                raise ValueError(f"unknown Market in {SHEET_TAB}")
            found.append((raw, mkt or "US"))
        if header is None:
            raise ValueError(f"header row with {', '.join(SHEET_COLUMNS)} not found in {SHEET_TAB}")
        return found
    finally:
        wb.close()


def _portfolio_sheet_id() -> str:
    """PORTFOLIO_SHEET_ID from the environment, else from the main checkout's .env (never logged)."""
    value = os.environ.get("PORTFOLIO_SHEET_ID", "").strip()
    if value:
        return value
    env_path = main_repo_root() / ".env"
    if not env_path.is_file():
        return ""
    for line in env_path.read_text(encoding="utf-8").splitlines():
        key, sep, val = line.strip().partition("=")
        if sep and key.strip().removeprefix("export ").strip() == "PORTFOLIO_SHEET_ID":
            return val.strip().strip("'\"")
    return ""


def default_sheet_fetcher() -> bytes:
    from portfolio.holdings.sheet_book import download_sheet_xlsx

    return download_sheet_xlsx(_portfolio_sheet_id())


# ---------------------------------------------------------------- Step A: --detect-delta

def _check_freshness(market_db_path: Path, max_age_hours: float, auto_pull: bool,
                     pull_runner: Callable[[], int], warnings: List[str]) -> str:
    def age() -> float:
        return (time.time() - Path(market_db_path).stat().st_mtime) / 3600.0

    if age() <= max_age_hours:
        return "OK"
    if auto_pull:
        try:
            rc = pull_runner()
        except Exception as exc:  # the pull is best effort; the run continues on WARN
            rc = f"{type(exc).__name__}"
        if age() <= max_age_hours:
            return "OK"
        warnings.append(f"market_db_pull_failed: rc={rc}")
    warnings.append(f"market_db_stale: mtime age {age():.1f}h > {max_age_hours:g}h")
    return "WARN"


def _read_market(market_db_path: Path, today: str):
    with contextlib.closing(ro_connect(market_db_path)) as conn:
        sm = {r[0]: {"cik": r[1], "company_name": r[2], "is_etf": r[3], "is_fund": r[4],
                     "share_class_of": r[5], "eligible": r[6], "reason": r[7]}
              for r in conn.execute(
                  "SELECT symbol, cik, company_name, is_etf, is_fund, share_class_of, eligible, "
                  "reason FROM security_master")}
        extended = [r[0] for r in conn.execute(
            "SELECT DISTINCT symbol FROM extended_membership ORDER BY symbol")]
        active = {r[0] for r in conn.execute(
            "SELECT DISTINCT symbol FROM extended_membership "
            "WHERE effective_to IS NULL OR effective_to > ?", (today,))}
        profiles = {}
        for symbol, payload in conn.execute("SELECT symbol, payload FROM company_profile"):
            try:
                doc = json.loads(payload)
            except ValueError:
                continue
            if isinstance(doc, dict):  # keep only short fields; description never leaves here
                profiles[symbol] = {k: doc.get(k) for k in
                                    ("cik", "companyName", "sector", "industry", "isEtf", "isFund")}
        existing = {}
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' "
                        "AND name = 'stock_themes'").fetchone():
            for r in conn.execute("SELECT symbol, cik, asset_kind, in_us_basket_calc, is_superseded, "
                                  "profile_sector, profile_industry FROM stock_themes"):
                existing[r[0]] = {"cik": _norm_cik(r[1]), "asset_kind": r[2], "in_us_basket_calc": r[3],
                                  "is_superseded": r[4], "profile_sector": r[5],
                                  "profile_industry": r[6]}
    return sm, extended, active, profiles, existing


def _read_watchlist(company_db_path: Path) -> List[str]:
    if not Path(company_db_path).exists():
        return []
    with contextlib.closing(ro_connect(company_db_path)) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' "
                            "AND name = 'watchlist'").fetchone():
            return []
        return [r[0] for r in conn.execute("SELECT symbol FROM watchlist ORDER BY symbol")]


def _is_active_us(row: Mapping[str, Any]) -> bool:
    return (row["asset_kind"] == "us_equity" and row["in_us_basket_calc"] == 1
            and not row.get("is_superseded"))


def _cik_losers(new_active: Sequence[Mapping[str, Any]], existing: Mapping[str, Mapping[str, Any]],
                locked: AbstractSet[str], sm: Mapping[str, Mapping[str, Any]],
                active_ext: AbstractSet[str]) -> Dict[str, str]:
    """loser symbol → winner for new active US equities sharing a non-empty CIK.

    A CIK already held by an active stock_themes row keeps that row as the winner.
    """
    in_db = {row["cik"]: sym for sym, row in sorted(existing.items())
             if _text(row["cik"]) and _is_active_us(row)}
    groups: Dict[str, List[str]] = {}
    for meta in new_active:
        if meta["cik"]:
            groups.setdefault(meta["cik"], []).append(meta["symbol"])
    losers = {}
    for cik, symbols in groups.items():
        winner = in_db.get(cik) or min(symbols, key=lambda s: (
            s not in locked, (sm.get(s) or {}).get("eligible") != 1, s not in active_ext, s))
        losers.update({s: winner for s in symbols if s != winner})
    return losers


def _auto_row(meta: Mapping[str, Any], *, pi_bucket: str, l2_id: Optional[str] = None,
              cross_themes: Sequence[Mapping[str, str]] = (),
              superseded_by: Optional[str] = None) -> Dict[str, Any]:
    row = {k: meta[k] for k in ROW_META_FIELDS}
    row.update(l2_id=l2_id, cross_themes=[dict(c) for c in cross_themes], pi_bucket=pi_bucket,
               business_note=None, agreement_status=DEFAULT_AGREEMENT,
               is_superseded=0, superseded_by=None)
    if superseded_by is not None:
        row.update(in_us_basket_calc=0, is_superseded=1, superseded_by=superseded_by,
                   l2_id=None, cross_themes=[])
    return row


def detect_delta(*, market_db_path, company_db_path, exchange_dir, baskets_path, overrides_path,
                 aliases_path, report_concepts_path=None, today: Optional[str] = None,
                 max_db_age_hours: float = 36.0, auto_pull_stale: bool = True,
                 pull_runner: Optional[Callable[[], int]] = None,
                 sheet_xlsx_fetcher: Optional[Callable[[], bytes]] = None,
                 skip_sheet: bool = False) -> Dict[str, Any]:
    """Step A: clear outbox/, scan the three sources read-only, write inbox/delta.json."""
    from src.data.symbol_aliases import load_symbol_aliases

    dirs = ensure_exchange_dirs(exchange_dir)
    for item in dirs["outbox"].iterdir():  # a running Step C owns its .inflight.* claim
        if (item.is_file() or item.is_symlink()) and not item.name.startswith(".inflight."):
            item.unlink()
    today = today or today_shanghai()
    warnings: List[str] = []
    db_status = _check_freshness(market_db_path, max_db_age_hours, auto_pull_stale,
                                 pull_runner or default_pull_runner, warnings)

    baskets = load_json(baskets_path)
    overrides = load_json(overrides_path)
    aliases_path = Path(aliases_path)
    if aliases_path.name != "symbol_aliases.json":
        raise ValueError("aliases_path must point at symbol_aliases.json")
    aliases = load_symbol_aliases(aliases_path.parent)
    report_concepts_path = report_concepts_path or DEFAULT_REPORT_CONCEPTS
    extra_etfs = (frozenset(load_json(report_concepts_path).get("etf_symbols", []))
                  if Path(report_concepts_path).is_file() else frozenset())
    default_pi = baskets["asset_kind_default_pi_bucket"]
    l2_default_pi = {b["basket_id"]: b["default_pi_bucket"] for b in baskets["baskets"]
                     if b["tier"] == "l2"}
    locked = {item["symbol"]: item for item in overrides["locked_stocks"]}

    sm, extended, active_ext, profiles, existing = _read_market(Path(market_db_path), today)
    watchlist = _read_watchlist(Path(company_db_path))
    sheet: List[Tuple[str, str]] = []
    if skip_sheet:
        sheet_status = "SKIPPED"
    else:
        try:
            sheet = scan_sheet_tickers_tolerant((sheet_xlsx_fetcher or default_sheet_fetcher)())
            sheet_status = "OK"
        except Exception as exc:  # class name only: messages may carry the sheet URL
            sheet_status = "UNKNOWN"
            warnings.append(f"sheet_unavailable: {type(exc).__name__}")

    share_class_map = {s: r["share_class_of"] for s, r in sm.items() if _text(r["share_class_of"])}
    known = frozenset(sm) | frozenset(extended)
    candidates: Dict[str, set] = {}
    for raw, market, source in ([(s, "US", "extended") for s in extended]
                                + [(t, m, "overlay") for t, m in sheet]
                                + [(s, "US", "overlay") for s in watchlist]):
        inst = normalize_instrument(raw, market, as_of_date=today, aliases=aliases,
                                    supplemental_aliases=overrides.get("supplemental_aliases", []),
                                    share_class_map=share_class_map, known_us_symbols=known)
        if inst is not None:
            candidates.setdefault(inst.canonical_symbol, set()).add(source)

    metas = []
    for sym in sorted(candidates):
        sm_row, prof = sm.get(sym), profiles.get(sym)
        if sym in locked:  # public giants are us_equity by contract (store refuses other kinds)
            kind, in_calc = "us_equity", 1
        else:
            kind, in_calc = classify_asset_kind(sym, sm_row=sm_row, profile_meta=prof,
                                                extra_etfs=extra_etfs)
        sm_row, prof = sm_row or {}, prof or {}
        metas.append({
            "symbol": sym, "cik": _norm_cik(sm_row.get("cik")) or _norm_cik(prof.get("cik")),
            "asset_kind": kind, "in_us_basket_calc": in_calc,
            "profile_sector": _text(prof.get("sector")),
            "profile_industry": _text(prof.get("industry")),
            "company_name_snapshot": _text(prof.get("companyName")) or _text(sm_row.get("company_name")),
        })

    new_active = [m for m in metas if m["symbol"] not in existing and _is_active_us(m)]
    losers = _cik_losers(new_active, existing, frozenset(locked), sm, active_ext)
    missing, auto_rows, pivots = [], [], []
    for meta in metas:
        sym = meta["symbol"]
        old = existing.get(sym)
        if old is not None:
            if _is_active_us(old) and any(
                    meta[f] and _text(old[f]) and meta[f] != _text(old[f])
                    for f in ("profile_sector", "profile_industry")):
                pivots.append({"symbol": sym,
                               "old_profile_sector": old["profile_sector"],
                               "old_profile_industry": old["profile_industry"],
                               "new_profile_sector": meta["profile_sector"],
                               "new_profile_industry": meta["profile_industry"]})
        elif sym in losers:
            auto_rows.append(_auto_row(meta, pi_bucket=default_pi[meta["asset_kind"]],
                                       superseded_by=losers[sym]))
        elif sym in locked:
            l2_id = locked[sym]["l2_id"]
            auto_rows.append(_auto_row(
                meta, pi_bucket=l2_default_pi.get(l2_id) or default_pi["us_equity"],
                l2_id=l2_id, cross_themes=locked[sym].get("cross_themes") or []))
        elif meta["asset_kind"] in AUTO_KINDS:
            auto_rows.append(_auto_row(meta, pi_bucket=default_pi[meta["asset_kind"]]))
        else:
            missing.append(dict(meta, sources=sorted(candidates[sym])))

    delta = {
        "schema_version": SCHEMA_VERSION,
        "generated_date": today,
        "generated_at": utc_stamp(),
        "market_db_status": db_status,
        "sheet_status": sheet_status,
        "warnings": warnings,
        "counts": {"candidates": len(metas), "existing": sum(m["symbol"] in existing for m in metas),
                   "missing_symbols": len(missing), "auto_rows": len(auto_rows),
                   "pivot_candidates": len(pivots)},
        "missing_symbols": missing,
        "auto_rows": auto_rows,
        "pivot_candidates": pivots,
    }
    delta["delta_sha256"] = compute_delta_sha256(delta)
    atomic_write_json(dirs["inbox"] / "delta.json", delta)
    if not missing and not pivots:
        atomic_write_json(dirs["outbox"] / "payload.json", {
            "schema_version": SCHEMA_VERSION, "generated_date": today,
            "delta_sha256": delta["delta_sha256"], "classifications": []})
    return delta


# ---------------------------------------------------------------- cloud: backup + writer lock

def default_lock_path() -> Path:
    """Same file cron_wrapper.sh and backfill_extended_fundamentals.py lock for market.db writers."""
    return (Path(os.environ.get("FINANCE_CRON_LOCK_DIR", "/tmp/finance-cron-locks"))
            / "resource-market_db_writer.lock")


class LockBusy(Exception):
    pass


@contextlib.contextmanager
def market_db_writer_lock(lock_path, timeout_sec: float = LOCK_TIMEOUT_SEC):
    """flock(LOCK_EX) polled until `timeout_sec`; raises LockBusy. Closing the file releases it."""
    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a") as fh:
        deadline = time.monotonic() + timeout_sec
        while True:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise LockBusy(str(lock_path)) from None
                time.sleep(0.1)
        yield


def _gzip_backup(path: Path) -> None:
    tmp = path.with_name(path.name + ".gz.partial")
    try:
        with open(path, "rb") as src, open(tmp, "wb") as raw:
            with gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=6) as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
            raw.flush()
            os.fsync(raw.fileno())
        os.replace(tmp, path.with_name(path.name + ".gz"))
        path.unlink()
    except OSError as exc:  # the raw backup stays in place
        tmp.unlink(missing_ok=True)
        logger.warning("backup gzip failed for %s: %s", path.name, exc)


def _prune_backups(db_path: Path, label: str, final: Path, keep: int) -> None:
    """Keep `final` raw + the newest keep-1 older same-label backups gzipped; delete the rest."""
    pattern = re.compile(rf"^{re.escape(db_path.name)}\.backup-(\d{{14}})-{re.escape(label)}(\.gz)?$")
    older = sorted((p for p in db_path.parent.iterdir() if p != final and pattern.match(p.name)),
                   key=lambda p: pattern.match(p.name).group(1), reverse=True)
    for index, old in enumerate(older):
        try:
            if index >= keep - 1:
                old.unlink()
            elif not old.name.endswith(".gz"):
                _gzip_backup(old)
        except OSError as exc:  # the new backup is already published
            logger.warning("backup prune failed for %s: %s", old.name, exc)


def backup_sqlite_wal_safe(db_path, *, label: str = BACKUP_LABEL, keep: int = 2,
                           now: Optional[datetime] = None) -> Optional[Path]:
    """WAL-safe snapshot `<db>.backup-<UTC14>-<label>`; None when db_path is missing.

    Refuses (RuntimeError) when free disk < logical size x1.5. Copies with the SQLite
    backup API from a read-only source into a `.partial` temp file, requires
    quick_check == 'ok', then publishes with os.link (never overwrites).
    """
    db_path = Path(db_path)
    if not db_path.exists():
        return None
    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y%m%d%H%M%S")
    final = db_path.with_name(f"{db_path.name}.backup-{stamp}-{label}")
    tmp = None
    try:
        with contextlib.closing(ro_connect(db_path)) as src:
            logical = (src.execute("PRAGMA page_count").fetchone()[0]
                       * src.execute("PRAGMA page_size").fetchone()[0])
            need = int(logical * BACKUP_SPACE_FACTOR)
            free = shutil.disk_usage(str(db_path.parent)).free
            if free < need:
                raise RuntimeError(f"insufficient disk for backup: free={free} need={need}")
            fd, name = tempfile.mkstemp(dir=str(db_path.parent), prefix=f"{final.name}.",
                                        suffix=".partial")
            os.close(fd)
            tmp = Path(name)
            with contextlib.closing(sqlite3.connect(str(tmp))) as dst:
                src.backup(dst)
        with contextlib.closing(sqlite3.connect(str(tmp))) as check:
            result = check.execute("PRAGMA quick_check").fetchone()[0]
        if result != "ok":
            raise RuntimeError(f"backup quick_check={result!r}")
        os.link(str(tmp), str(final))
    finally:
        if tmp is not None:
            for suffix in ("", "-journal", "-wal", "-shm"):
                Path(str(tmp) + suffix).unlink(missing_ok=True)
    _prune_backups(db_path, label, final, keep)
    return final


# ---------------------------------------------------------------- cloud: --apply-cloud-stdin

def build_envelope(*, rows: Sequence[Mapping[str, Any]], payload_id: str,
                   baskets_doc: Mapping[str, Any], overrides_doc: Mapping[str, Any],
                   send_telegram: bool = True,
                   local_summary: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "payload_id": payload_id, "rows": [dict(r) for r in rows],
            "baskets_doc": baskets_doc, "overrides_doc": overrides_doc,
            "send_telegram": bool(send_telegram), "local_summary": dict(local_summary or {})}


def _env_refusal(db_path, allow_noncloud: bool) -> Optional[str]:
    """None when this process may write `db_path`; otherwise the refusal message."""
    target = os.path.realpath(str(db_path))
    if target in {os.path.realpath(str(root / "data" / name)) for root in {REPO_ROOT, main_repo_root()}
                  for name in ("company.db", "prosperity.db")}:
        return "refusing: theme tables never go into company.db or prosperity.db"
    if os.environ.get("FINANCE_ENV") == "cloud":
        return None
    if not allow_noncloud:
        return "refusing: FINANCE_ENV is not 'cloud'"
    from src.data.theme_basket_store import _protected_paths

    if target in _protected_paths():
        return "refusing: production market.db needs FINANCE_ENV=cloud"
    return None


def _parse_envelope(envelope: Any):
    from src.data.theme_basket_store import compute_payload_id

    if not isinstance(envelope, Mapping):
        raise TypeError("envelope must be a JSON object")
    if type(envelope.get("schema_version")) is not int or envelope["schema_version"] != SCHEMA_VERSION:
        raise ValueError(f"schema_version must be {SCHEMA_VERSION}")
    rows, payload_id = envelope["rows"], envelope["payload_id"]
    baskets_doc, overrides_doc = envelope["baskets_doc"], envelope["overrides_doc"]
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise TypeError("rows must be a list of objects")
    if not isinstance(baskets_doc, Mapping) or not isinstance(overrides_doc, Mapping):
        raise TypeError("baskets_doc and overrides_doc must be objects")
    if type(payload_id) is not str or compute_payload_id(rows) != payload_id:
        raise ValueError("payload_id does not match the rows")
    return rows, payload_id, baskets_doc, overrides_doc


def _needs_backup(db_path: Path, rows: Sequence[Any], payload_id: str, taxonomy: str) -> bool:
    """Read-only probe: back up only before a substantive change (not for a pure heartbeat)."""
    if not Path(db_path).exists():
        return False
    try:
        with contextlib.closing(ro_connect(db_path)) as conn:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' "
                                "AND name = 'theme_sync_commits'").fetchone():
                return True
            if rows and not conn.execute("SELECT 1 FROM theme_sync_commits WHERE payload_id = ?",
                                         (payload_id,)).fetchone():
                return True
            last = conn.execute("SELECT taxonomy_sha256 FROM theme_sync_commits "
                                "ORDER BY applied_at DESC, rowid DESC LIMIT 1").fetchone()
            return last is None or last[0] != taxonomy
    except sqlite3.Error:
        return True


def _md_escape(text: str) -> str:
    """Escape Telegram legacy-Markdown control characters (send_message uses parse_mode=Markdown)."""
    return re.sub(r"([_*`\[])", r"\\\1", text)


def format_weekly_summary(result: Mapping[str, Any], local: Mapping[str, Any]) -> str:
    local_q = [str(s) for s in local.get("local_quarantined") or []]
    cloud_q = [str(s) for s, _ in result["quarantined"]]
    lines = [
        "主题篮子周同步：" + ("重放已提交载荷" if result["replayed"] else "载荷已提交"),
        f"新增 {result['inserted']} · 更新 {result['updated']} · "
        f"云端隔离 {len(cloud_q)} · 本地隔离 {len(local_q)}",
        f"老票异动候选 {int(local.get('pivot_candidates') or 0)}",
        "备份：" + ("已完成" if result["backup"] else "跳过（无实质变更）"),
        f"载荷 {result['payload_id'][:12]}",
    ]
    if cloud_q or local_q:
        lines.append("隔离代码：" + ", ".join((cloud_q + local_q)[:20]))
    unclassified = [str(s) for s in local.get("unclassified_symbols") or []]
    if unclassified:
        lines.append(f"未标代码 {len(unclassified)}：" + ", ".join(unclassified[:20]))
    pivot_symbols = [str(s) for s in local.get("pivot_symbols") or []]
    if pivot_symbols:
        lines.append("异动代码：" + ", ".join(pivot_symbols[:20]))
    lines += [f"异动建议：{n.get('symbol')}: {n.get('note')}"
              for n in (local.get("pivot_notes") or [])[:10] if isinstance(n, Mapping)]
    lines += ["告警：" + str(w) for w in (local.get("warnings") or [])[:5]]
    return _md_escape("\n".join(lines))


def default_send_telegram(text: str, channel: str = "private") -> None:
    from src.telegram_bot import send_message

    if not send_message(text, channel=channel):
        raise RuntimeError("telegram send_message returned False")


def apply_cloud_envelope(envelope: Mapping[str, Any], *, db_path, lock_path,
                         lock_timeout_sec: float = LOCK_TIMEOUT_SEC,
                         send_telegram_fn: Optional[Callable[..., Any]] = None,
                         force_no_telegram: bool = False, allow_noncloud: bool = False,
                         now: Optional[datetime] = None) -> Tuple[int, Dict[str, Any]]:
    """Cloud side: validate → (backup) → 15 s writer lock → apply_envelope → unlock → Telegram."""
    refusal = _env_refusal(db_path, allow_noncloud)
    if refusal:
        return EXIT_FAIL, {"ok": False, "error": refusal}
    from src.data import theme_basket_store as store

    try:
        rows, payload_id, baskets_doc, overrides_doc = _parse_envelope(envelope)
    except (KeyError, TypeError, ValueError) as exc:
        return EXIT_FAIL, {"ok": False, "error": f"bad_envelope: {type(exc).__name__}: {exc}"}
    db_path = Path(db_path)
    backup = None
    if _needs_backup(db_path, rows, payload_id, store.compute_taxonomy_sha256(baskets_doc, overrides_doc)):
        try:
            backup = backup_sqlite_wal_safe(db_path, keep=2, now=now)
        except Exception as exc:
            return EXIT_FAIL, {"ok": False, "error": f"backup_failed: {type(exc).__name__}: {exc}"}
    try:
        with market_db_writer_lock(lock_path, lock_timeout_sec):
            with contextlib.closing(store.open_writer(db_path, cloud_authoritative=True)) as conn:
                res = store.apply_envelope(conn, baskets_doc=baskets_doc, overrides_doc=overrides_doc,
                                           rows=rows, payload_id=payload_id, now=now)
    except LockBusy:
        return EXIT_LOCK_BUSY, {"ok": False, "error": "lock_busy"}
    except Exception as exc:
        return EXIT_FAIL, {"ok": False, "error": f"apply_failed: {type(exc).__name__}: {exc}"}

    result = {"ok": True, "payload_id": res.payload_id, "taxonomy_sha256": res.taxonomy_sha256,
              "replayed": res.replayed, "inserted": res.inserted, "updated": res.updated,
              "quarantined": [list(q) for q in res.quarantined],
              "history_appended": res.history_appended,
              "backup": str(backup) if backup else None, "telegram": "skipped"}
    if envelope.get("send_telegram", True) and not force_no_telegram:
        local = envelope.get("local_summary")
        try:
            (send_telegram_fn or default_send_telegram)(
                format_weekly_summary(result, local if isinstance(local, Mapping) else {}),
                channel="private")
            result["telegram"] = "sent"
        except Exception as exc:  # the commit stands; report only
            result.update(telegram="failed", telegram_error=f"{type(exc).__name__}: {exc}")
    return EXIT_OK, result


# ---------------------------------------------------------------- private locks (db_locked)

_LOCKED_COLUMNS = ("symbol", "cik", "asset_kind", "in_us_basket_calc", "l1_id", "l2_id", "pi_bucket",
                   "business_note", "profile_sector", "profile_industry", "company_name_snapshot",
                   "source", "agreement_status", "assigned_at", "updated_at")


def _db_locked_rows(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    rows = [dict(zip(_LOCKED_COLUMNS, r)) for r in conn.execute(
        f"SELECT {', '.join(_LOCKED_COLUMNS)} FROM stock_themes "
        "WHERE source = 'db_locked' ORDER BY symbol")]
    cross: Dict[str, List[Dict[str, str]]] = {}
    for symbol, theme_id, role in conn.execute(
            "SELECT x.symbol, x.theme_id, x.role FROM stock_cross_themes x "
            "JOIN stock_themes s ON s.symbol = x.symbol WHERE s.source = 'db_locked' "
            "ORDER BY x.symbol, x.theme_id"):
        cross.setdefault(symbol, []).append({"role": role, "theme_id": theme_id})
    for row in rows:
        row["cross_themes"] = cross.get(row["symbol"], [])
    return rows


def _parse_cross(item: str) -> Dict[str, str]:
    theme_id, _, role = item.partition(":")
    return {"theme_id": theme_id.strip(), "role": role.strip() or "core"}


def cloud_private_lock(*, db_path, lock_path, action: str, symbol: str, l2_id: Optional[str] = None,
                       cross: Sequence[str] = (), note: Optional[str] = None,
                       lock_timeout_sec: float = LOCK_TIMEOUT_SEC, allow_noncloud: bool = False,
                       now: Optional[datetime] = None) -> Tuple[int, Dict[str, Any]]:
    """Cloud side of --lock-private / --unlock-private; returns the full db_locked snapshot."""
    refusal = _env_refusal(db_path, allow_noncloud)
    if refusal:
        return EXIT_FAIL, {"ok": False, "error": refusal}
    from src.data import theme_basket_store as store

    symbol = symbol.strip().upper()
    try:
        with market_db_writer_lock(lock_path, lock_timeout_sec):
            with contextlib.closing(store.open_writer(db_path, cloud_authoritative=True)) as conn:
                if action == "lock":
                    store.lock_private_symbol(conn, symbol, l2_id=l2_id,
                                              cross_themes=[_parse_cross(c) for c in cross],
                                              business_note=note, now=now)
                else:
                    store.unlock_private_symbol(conn, symbol, now=now)
                rows = _db_locked_rows(conn)
    except LockBusy:
        return EXIT_LOCK_BUSY, {"ok": False, "error": "lock_busy"}
    except Exception as exc:
        return EXIT_FAIL, {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return EXIT_OK, {"ok": True, "action": action, "symbol": symbol, "db_locked_rows": rows}


# ---------------------------------------------------------------- local → cloud transport

def resolve_ssh_host(remote_args: Sequence[str]) -> str:
    """Use THEME_SYNC_SSH_HOST (e.g. restricted-key alias) only for --apply-cloud-stdin."""
    if remote_args and remote_args[0] == "--apply-cloud-stdin":
        override = os.environ.get("THEME_SYNC_SSH_HOST", "").strip()
        if override:
            return override
    return REMOTE_HOST


def default_ssh_runner(remote_args: Sequence[str], stdin_bytes: Optional[bytes] = None):
    command = (f"cd {shlex.quote(REMOTE_DIR)} && {REMOTE_PYTHON} scripts/theme_basket_sync.py "
               + shlex.join(list(remote_args)))
    host = resolve_ssh_host(remote_args)
    proc = subprocess.run(["ssh", "-o", "BatchMode=yes", host, command],
                          input=stdin_bytes or b"", capture_output=True, timeout=SSH_TIMEOUT_SEC)
    return proc.returncode, proc.stdout, proc.stderr


def call_cloud(ssh_runner, remote_args: Sequence[str],
               stdin_bytes: Optional[bytes] = None) -> Tuple[int, Dict[str, Any]]:
    """(rc, reply). Transport failures and non-JSON stdout come back as rc=1, ok=False."""
    try:
        rc, out, err = ssh_runner(list(remote_args), stdin_bytes)
    except Exception as exc:
        return EXIT_FAIL, {"ok": False, "error": f"transport_failed: {type(exc).__name__}: {exc}"}
    try:
        reply = json.loads(out.decode("utf-8"))
        if not isinstance(reply, dict):
            raise ValueError("reply is not an object")
    except ValueError:
        tail = (err or b"").decode("utf-8", "replace").strip()[-300:]
        return EXIT_FAIL, {"ok": False, "error": f"bad_cloud_reply: rc={rc} stderr={tail!r}"}
    if rc == EXIT_OK and reply.get("ok") is True:
        return EXIT_OK, reply
    return (rc if rc in (EXIT_FAIL, EXIT_LOCK_BUSY) else EXIT_FAIL), dict(reply, ok=False)


def _local_db_busy(market_db_path) -> Optional[str]:
    """Why sync_to_cloud.sh --pull would refuse right now (issue 059), or None."""
    path = Path(market_db_path).resolve()  # a worktree's data/market.db is a symlink
    wal = Path(str(path) + "-wal")
    if wal.exists() and wal.stat().st_size > 0:
        return "local market.db-wal is not empty"
    if not Path(LSOF).exists():
        return f"{LSOF} not found"
    for item in (path, wal, Path(str(path) + "-shm")):
        if item.exists() and subprocess.run([LSOF, "--", str(item)], stdout=subprocess.DEVNULL,
                                            stderr=subprocess.DEVNULL).returncode == 0:
            return f"{item.name} is open by a process"
    return None


def pull_after_commit(market_db_path, pull_runner: Callable[[], int], warnings: List[str]) -> None:
    busy = _local_db_busy(market_db_path)
    if busy:
        warnings.append(f"pull_skipped: {busy}")
        return
    try:
        rc = pull_runner()
    except Exception as exc:
        rc = type(exc).__name__
    if rc != 0:
        warnings.append(f"pull_failed: rc={rc}")


def run_private_lock(*, action: str, symbol: str, l2_id: Optional[str] = None, cross: Sequence[str] = (),
                     note: Optional[str] = None, exchange_dir=DEFAULT_EXCHANGE_DIR,
                     market_db_path=DEFAULT_MARKET_DB, ssh_runner=None, pull_runner=None,
                     skip_pull: bool = False) -> int:
    """Local side: run the cloud lock over SSH, then save the db_locked snapshot (0600) and pull."""
    symbol = symbol.strip().upper()
    remote = ["--cloud-local", "--lock-private" if action == "lock" else "--unlock-private", symbol]
    if action == "lock":
        remote += ["--l2", str(l2_id)]
        for item in cross:
            remote += ["--cross", item]
        if note is not None:
            remote += ["--note", note]
    rc, reply = call_cloud(ssh_runner or default_ssh_runner, remote)
    if rc != EXIT_OK:
        _emit({"ok": False, "mode": f"{action}-private", "symbol": symbol, "error": reply.get("error")})
        return rc
    dirs = ensure_exchange_dirs(exchange_dir)
    atomic_write_json(dirs["db_locked_backup"] / "db_locked_latest.json", {
        "saved_at": utc_stamp(), "action": action, "symbol": symbol,
        "db_locked_rows": reply.get("db_locked_rows", [])})
    warnings: List[str] = []
    if not skip_pull:
        pull_after_commit(market_db_path, pull_runner or default_pull_runner, warnings)
    _emit({"ok": True, "mode": f"{action}-private", "symbol": symbol,
           "db_locked_count": len(reply.get("db_locked_rows", [])), "warnings": warnings})
    return EXIT_OK


# ---------------------------------------------------------------- Step C: --commit-outbox / --sync-config / --replay-pending

ALLOWED_AGREEMENT = frozenset({"single_pass_weekly", "dual_agreed", "needs_spot_check", "disagreed"})
# Model fields: absent or null → default; any other value must pass its check (no `or` fallback).
_MODEL_FIELD_OK = {
    "l2_id": lambda v: isinstance(v, str) and v != "",
    "pi_bucket": lambda v: isinstance(v, str) and v != "",
    "business_note": lambda v: isinstance(v, str),
    "cross_themes": lambda v: isinstance(v, list),
    "agreement_status": lambda v: isinstance(v, str) and v in ALLOWED_AGREEMENT,
}


class GateError(Exception):
    """A Step C hard gate failed: nothing is sent and outbox/ is left for inspection."""


def _gate_outbox(dirs: Mapping[str, Path], payload_path: Path, today: str):
    delta_path = dirs["inbox"] / "delta.json"
    if not delta_path.is_file() or not payload_path.is_file():
        raise GateError("inbox/delta.json and outbox/payload.json must both exist")
    try:
        delta, payload = load_json(delta_path), load_json(payload_path)
    except ValueError as exc:
        raise GateError(f"unreadable json: {type(exc).__name__}") from None
    if not isinstance(delta, dict) or not isinstance(payload, dict):
        raise GateError("delta.json and payload.json must be JSON objects")
    if delta.get("generated_date") != today:
        raise GateError(f"delta.json generated_date {delta.get('generated_date')!r} is not today {today}")
    digest = compute_delta_sha256(delta)
    if digest != delta.get("delta_sha256") or digest != payload.get("delta_sha256"):
        raise GateError("delta_sha256 mismatch between inbox/delta.json and outbox/payload.json")
    items = payload.get("classifications")
    if not isinstance(items, list) or not all(
            isinstance(c, dict) and isinstance(c.get("symbol"), str) for c in items):
        raise GateError("classifications must be a list of objects with a string symbol")
    symbols = [c["symbol"] for c in items]
    if len(set(symbols)) != len(symbols):
        raise GateError("duplicate symbol in classifications")
    extra = sorted(set(symbols) - {m["symbol"] for m in delta["missing_symbols"]})
    if extra:
        raise GateError(f"classifications outside missing_symbols: {extra[:10]}")
    notes = payload.get("pivot_notes") or []
    pivots = {p["symbol"] for p in delta["pivot_candidates"]}
    if not isinstance(notes, list) or not all(
            isinstance(n, dict) and isinstance(n.get("symbol"), str) and isinstance(n.get("note"), str)
            and n["symbol"] in pivots for n in notes):
        raise GateError("pivot_notes must be [{symbol, note}] for symbols in pivot_candidates")
    return delta, payload


def merge_classifications(delta: Mapping[str, Any], items: Sequence[Mapping[str, Any]],
                          baskets_doc: Mapping[str, Any]) -> Tuple[List[Dict[str, Any]], List[List[str]]]:
    """Model rows → store rows: identity fields always come from delta.json.

    Only l2_id / cross_themes / pi_bucket / business_note / agreement_status are taken
    from the model. Returns (rows, locally rejected [symbol, reason]).
    """
    missing = {m["symbol"]: m for m in delta["missing_symbols"]}
    l2_pi = {b["basket_id"]: b["default_pi_bucket"] for b in baskets_doc["baskets"] if b["tier"] == "l2"}
    kind_pi = baskets_doc["asset_kind_default_pi_bucket"]
    rows, rejected = [], []
    for item in items:
        meta = missing[item["symbol"]]
        bad = sorted(k for k, ok in _MODEL_FIELD_OK.items() if item.get(k) is not None and not ok(item[k]))
        if bad:
            rejected.append([meta["symbol"], "invalid fields: " + ", ".join(bad)])
            continue
        l2_id, pi, cross, status = (item.get(k) for k in ("l2_id", "pi_bucket", "cross_themes",
                                                          "agreement_status"))
        if pi is None and l2_id is not None:
            pi = l2_pi.get(l2_id)
        row = {k: meta[k] for k in ROW_META_FIELDS}
        row.update(
            l2_id=l2_id,
            cross_themes=cross if cross is not None and meta["in_us_basket_calc"] == 1 else [],
            pi_bucket=pi if pi is not None else kind_pi[meta["asset_kind"]],
            business_note=item.get("business_note"),
            agreement_status=status if status is not None else DEFAULT_AGREEMENT)
        rows.append(row)
    return rows, rejected


def rehearse(baskets_doc: Mapping[str, Any], overrides_doc: Mapping[str, Any],
             rows: Sequence[Mapping[str, Any]]) -> Tuple[List[Dict[str, Any]], List[List[str]]]:
    """Dry-run the envelope in :memory:. A broken dictionary raises; bad rows come back quarantined."""
    from src.data import theme_basket_store as store

    rows = [dict(r) for r in rows]
    with contextlib.closing(store.open_writer(":memory:")) as conn:
        res = store.apply_envelope(conn, baskets_doc=baskets_doc, overrides_doc=overrides_doc,
                                   rows=rows, payload_id=store.compute_payload_id(rows))
        active_pi = {b for (b,) in conn.execute(
            "SELECT basket_id FROM theme_baskets WHERE tier = 'pi_bucket' AND is_active = 1")}
    defaults = baskets_doc.get("asset_kind_default_pi_bucket")
    if not isinstance(defaults, Mapping) or any(
            not isinstance(defaults.get(kind), str) or defaults[kind] not in active_pi for kind in ASSET_KINDS):
        raise ValueError("asset_kind_default_pi_bucket must map all 7 asset kinds to active pi_bucket baskets")
    bad = {symbol for symbol, _ in res.quarantined}
    return [r for r in rows if r["symbol"] not in bad], [list(q) for q in res.quarantined]


def _send_sealed(sealed: Mapping[str, Any], baskets_doc, overrides_doc, ssh_runner,
                 send_telegram: bool) -> Tuple[int, Dict[str, Any]]:
    envelope = build_envelope(rows=sealed["rows"], payload_id=sealed["payload_id"],
                              baskets_doc=baskets_doc, overrides_doc=overrides_doc,
                              send_telegram=send_telegram, local_summary=sealed.get("local_summary"))
    return call_cloud(ssh_runner, ["--apply-cloud-stdin"],
                      json.dumps(envelope, ensure_ascii=False).encode("utf-8"))


def _load_sealed(path: Path) -> Dict[str, Any]:
    from src.data.theme_basket_store import compute_payload_id

    sealed = load_json(path)
    if not isinstance(sealed, dict) or not isinstance(sealed.get("rows"), list) \
            or not all(isinstance(r, dict) for r in sealed["rows"]) \
            or not isinstance(sealed.get("local_summary", {}), dict) \
            or sealed.get("payload_id") != compute_payload_id(sealed["rows"]):
        raise ValueError("not a sealed payload (rows/payload_id mismatch)")
    return sealed


def _replay_files(pending_dir: Path, baskets_doc, overrides_doc, ssh_runner, send_telegram: bool,
                  warnings: List[str]):
    """Send pending/*.json in (created_at, filename) order; stop at the first cloud failure.

    Any corrupt file stops the run before the first send (never out of order); every file
    stays on disk. Returns (rc, files sent, symbols sent, cloud quarantine of the sent
    files, symbols still queued in valid pending files).
    """
    queue, corrupt = [], False
    for path in pending_dir.glob("*.json"):
        try:
            sealed = _load_sealed(path)
        except ValueError as exc:
            warnings.append(f"pending_corrupt: {path.name}: {type(exc).__name__}")
            corrupt = True
            continue
        queue.append((str(sealed.get("created_at", "")), path.name, path, sealed))
    queue.sort(key=lambda q: (q[0], q[1]))
    rc, sent, sent_symbols, quarantined = (EXIT_FAIL if corrupt else EXIT_OK), 0, set(), []
    for _, _, path, sealed in ([] if corrupt else queue):
        rc, reply = _send_sealed(sealed, baskets_doc, overrides_doc, ssh_runner, send_telegram)
        if rc != EXIT_OK:
            warnings.append(f"pending_replay_failed: {path.name}: {reply.get('error')}")
            break
        path.unlink()
        sent += 1
        sent_symbols |= ({r.get("symbol") for r in sealed["rows"]}
                         - {sym for sym, _ in reply.get("quarantined") or []})
        quarantined += [list(q) for q in reply.get("quarantined") or []]
    queued = {r.get("symbol") for _, _, _, sealed in queue[sent:] for r in sealed["rows"]}
    return rc, sent, sent_symbols, quarantined, queued


def commit_outbox(*, exchange_dir, baskets_path, overrides_path, market_db_path,
                  today: Optional[str] = None, ssh_runner=None, pull_runner=None,
                  send_telegram: bool = True, skip_pull: bool = False) -> int:
    """Step C: claim → gate → merge → :memory: rehearsal → replay pending/ → seal the rest → send → pull."""
    from src.data.theme_basket_store import compute_payload_id

    ssh_runner = ssh_runner or default_ssh_runner
    dirs = ensure_exchange_dirs(exchange_dir)
    payload_path = dirs["outbox"] / "payload.json"
    # Claim the file before reading it: a payload.json written after this belongs to the next run.
    claimed = dirs["outbox"] / f".inflight.{os.getpid()}.{time.monotonic_ns()}.json"
    try:
        try:
            os.replace(str(payload_path), str(claimed))
        except FileNotFoundError:
            raise GateError("inbox/delta.json and outbox/payload.json must both exist") from None
        delta, payload = _gate_outbox(dirs, claimed, today or today_shanghai())
        baskets_doc, overrides_doc = load_json(baskets_path), load_json(overrides_path)
        merged, local_q = merge_classifications(delta, payload["classifications"], baskets_doc)
        clean, quarantined = rehearse(baskets_doc, overrides_doc, list(delta["auto_rows"]) + merged)
    except Exception as exc:
        if claimed.exists():  # put it back for inspection, but never over a newer payload.json
            try:
                os.link(str(claimed), str(payload_path))
            except FileExistsError:
                os.replace(str(claimed), str(dirs["outbox"] / f"rejected.{os.getpid()}.{time.monotonic_ns()}.json"))
            claimed.unlink(missing_ok=True)
        error = f"gate: {exc}" if isinstance(exc, GateError) else f"rehearsal_failed: {type(exc).__name__}: {exc}"
        _emit({"ok": False, "mode": "commit-outbox", "error": error})
        return EXIT_FAIL
    local_q += quarantined
    warnings: List[str] = []
    rc, _, sent_symbols, replayed_q, queued = _replay_files(
        dirs["pending"], baskets_doc, overrides_doc, ssh_runner, send_telegram, warnings)
    # Rows already sent or still queued in pending/ would only come back as "already exists".
    rows = [r for r in clean if r["symbol"] not in sent_symbols | queued]
    sealed = {"schema_version": SCHEMA_VERSION, "payload_id": compute_payload_id(rows), "rows": rows,
              "created_at": utc_stamp(), "delta_sha256": delta["delta_sha256"],
              "local_summary": {
                  "local_quarantined": [s for s, _ in local_q],
                  "pivot_candidates": len(delta["pivot_candidates"]),
                  "warnings": list(delta["warnings"]),
                  "unclassified_symbols": sorted({m["symbol"] for m in delta["missing_symbols"]}
                                                 - {c["symbol"] for c in payload["classifications"]}),
                  "pivot_symbols": [p["symbol"] for p in delta["pivot_candidates"]],
                  "pivot_notes": [{"symbol": n["symbol"], "note": n["note"].strip()[:200]}
                                  for n in payload.get("pivot_notes") or []]}}
    summary = {"mode": "commit-outbox", "payload_id": sealed["payload_id"], "rows": len(rows),
               "local_quarantined": local_q, "replayed_quarantined": replayed_q,
               "unclassified": len(sealed["local_summary"]["unclassified_symbols"])}
    reply: Dict[str, Any] = {"ok": False, "error": "pending replay failed first"}
    if rc == EXIT_OK:
        rc, reply = _send_sealed(sealed, baskets_doc, overrides_doc, ssh_runner, send_telegram)
    if rc != EXIT_OK:
        # The local gate and rehearsal passed: park every cloud failure for --replay-pending.
        if rows or sealed["local_summary"]["pivot_notes"] or not any(dirs["pending"].glob("*.json")):
            target = dirs["pending"] / f"{sealed['payload_id']}.json"
            if target.exists():
                try:
                    _load_sealed(target)
                except ValueError:  # never overwrite a corrupt file: it is the evidence
                    target = dirs["pending"] / f"{int(time.time())}-{sealed['payload_id']}.json"
            atomic_write_json(target, sealed)
        claimed.unlink(missing_ok=True)
        _emit(dict(summary, ok=False, parked=True, error=reply.get("error"), warnings=warnings))
        return rc
    claimed.unlink(missing_ok=True)
    (dirs["pending"] / f"{sealed['payload_id']}.json").unlink(missing_ok=True)
    if not skip_pull:
        pull_after_commit(market_db_path, pull_runner or default_pull_runner, warnings)
    _emit(dict(summary, ok=True, cloud={k: reply.get(k) for k in (
        "replayed", "inserted", "updated", "quarantined", "backup", "telegram")}, warnings=warnings))
    return EXIT_OK


def _validated_docs(baskets_path, overrides_path):
    baskets_doc, overrides_doc = load_json(baskets_path), load_json(overrides_path)
    rehearse(baskets_doc, overrides_doc, [])
    return baskets_doc, overrides_doc


def sync_config(*, baskets_path, overrides_path, market_db_path, ssh_runner=None, pull_runner=None,
                send_telegram: bool = True, skip_pull: bool = False) -> int:
    """Send the latest dictionary + public locks with zero rows (e.g. right after editing overrides.json)."""
    from src.data.theme_basket_store import compute_payload_id

    try:
        baskets_doc, overrides_doc = _validated_docs(baskets_path, overrides_path)
    except Exception as exc:
        _emit({"ok": False, "mode": "sync-config", "error": f"rehearsal_failed: {type(exc).__name__}: {exc}"})
        return EXIT_FAIL
    rc, reply = _send_sealed({"rows": [], "payload_id": compute_payload_id([])}, baskets_doc,
                             overrides_doc, ssh_runner or default_ssh_runner, send_telegram)
    if rc != EXIT_OK:
        _emit({"ok": False, "mode": "sync-config", "error": reply.get("error")})
        return rc
    warnings: List[str] = []
    if not skip_pull:
        pull_after_commit(market_db_path, pull_runner or default_pull_runner, warnings)
    _emit({"ok": True, "mode": "sync-config", "updated": reply.get("updated"), "warnings": warnings})
    return EXIT_OK


def replay_pending(*, exchange_dir, baskets_path, overrides_path, market_db_path, ssh_runner=None,
                   pull_runner=None, send_telegram: bool = True, skip_pull: bool = False) -> int:
    """Resend sealed pending/ payloads unchanged, each with the latest dictionary; one pull at the end."""
    dirs = ensure_exchange_dirs(exchange_dir)
    if not any(dirs["pending"].glob("*.json")):
        _emit({"ok": True, "mode": "replay-pending", "sent": 0, "replayed_quarantined": [], "warnings": []})
        return EXIT_OK
    try:
        baskets_doc, overrides_doc = _validated_docs(baskets_path, overrides_path)
    except Exception as exc:
        _emit({"ok": False, "mode": "replay-pending", "error": f"rehearsal_failed: {type(exc).__name__}: {exc}"})
        return EXIT_FAIL
    warnings: List[str] = []
    rc, sent, _, replayed_q, _ = _replay_files(dirs["pending"], baskets_doc, overrides_doc,
                                               ssh_runner or default_ssh_runner, send_telegram, warnings)
    if rc == EXIT_OK and sent and not skip_pull:
        pull_after_commit(market_db_path, pull_runner or default_pull_runner, warnings)
    _emit({"ok": rc == EXIT_OK, "mode": "replay-pending", "sent": sent,
           "replayed_quarantined": replayed_q, "warnings": warnings})
    return rc


# ---------------------------------------------------------------- CLI

def _emit(obj: Mapping[str, Any], stream=None) -> None:
    stream = stream or sys.stdout
    stream.write(json.dumps(obj, ensure_ascii=True, sort_keys=True) + "\n")
    stream.flush()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--detect-delta", action="store_true", help="Step A (local)")
    mode.add_argument("--commit-outbox", action="store_true", help="Step C (local)")
    mode.add_argument("--sync-config", action="store_true",
                      help="local: push baskets.json + overrides.json with zero rows")
    mode.add_argument("--replay-pending", action="store_true", help="local: resend pending/*.json")
    mode.add_argument("--apply-cloud-stdin", action="store_true",
                      help="cloud: read one envelope from stdin, print one JSON line")
    mode.add_argument("--lock-private", metavar="SYMBOL", help="lock a private holding as db_locked")
    mode.add_argument("--unlock-private", metavar="SYMBOL", help="release a db_locked holding")
    parser.add_argument("--exchange-dir", type=Path, default=DEFAULT_EXCHANGE_DIR)
    parser.add_argument("--market-db", type=Path, default=DEFAULT_MARKET_DB,
                        help="local read-only market.db")
    parser.add_argument("--company-db", type=Path, default=DEFAULT_COMPANY_DB)
    parser.add_argument("--baskets", type=Path, default=DEFAULT_BASKETS)
    parser.add_argument("--overrides", type=Path, default=DEFAULT_OVERRIDES)
    parser.add_argument("--aliases", type=Path, default=DEFAULT_ALIASES)
    parser.add_argument("--skip-sheet", action="store_true", help="Step A: do not read the Google Sheet")
    parser.add_argument("--no-pull", action="store_true", help="never run sync_to_cloud.sh --pull")
    parser.add_argument("--cloud-local", action="store_true",
                        help="run --lock-private/--unlock-private directly on this (cloud) host")
    parser.add_argument("--l2", help="--lock-private: active L2 basket id")
    parser.add_argument("--cross", action="append", default=[], metavar="THEME_ID[:ROLE]")
    parser.add_argument("--note", help="--lock-private: business note")
    parser.add_argument("--db", type=Path, default=DEFAULT_MARKET_DB, help="cloud writer target")
    parser.add_argument("--lock-path", type=Path, default=None)
    parser.add_argument("--no-telegram", action="store_true")
    parser.add_argument("--allow-noncloud-test-db", action="store_true",
                        help="tests only: allow a non-production --db without FINANCE_ENV=cloud")
    return parser


def _run_cloud(args) -> Tuple[int, Dict[str, Any]]:
    import config.settings  # noqa: F401  (loads <repo>/.env, which sets FINANCE_ENV=cloud)

    lock_path = args.lock_path or default_lock_path()
    if args.apply_cloud_stdin:
        try:
            envelope = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            return EXIT_FAIL, {"ok": False, "error": f"bad_envelope: {type(exc).__name__}"}
        return apply_cloud_envelope(envelope, db_path=args.db, lock_path=lock_path,
                                    force_no_telegram=args.no_telegram,
                                    allow_noncloud=args.allow_noncloud_test_db)
    return cloud_private_lock(db_path=args.db, lock_path=lock_path,
                              action="lock" if args.lock_private else "unlock",
                              symbol=args.lock_private or args.unlock_private, l2_id=args.l2,
                              cross=args.cross, note=args.note,
                              allow_noncloud=args.allow_noncloud_test_db)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.lock_private and not args.l2:
        parser.error("--lock-private needs --l2")
    if args.apply_cloud_stdin or args.cloud_local:
        out = sys.stdout  # the JSON reply is the only thing allowed on stdout
        with contextlib.redirect_stdout(sys.stderr):
            try:
                rc, reply = _run_cloud(args)
            except Exception as exc:
                rc, reply = EXIT_FAIL, {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        _emit(reply, out)
        return rc
    if args.lock_private or args.unlock_private:
        return run_private_lock(
            action="lock" if args.lock_private else "unlock",
            symbol=args.lock_private or args.unlock_private, l2_id=args.l2, cross=args.cross,
            note=args.note, exchange_dir=args.exchange_dir, market_db_path=args.market_db,
            skip_pull=args.no_pull)
    common = {"market_db_path": args.market_db, "send_telegram": not args.no_telegram,
              "skip_pull": args.no_pull, "baskets_path": args.baskets, "overrides_path": args.overrides}
    if args.commit_outbox:
        return commit_outbox(exchange_dir=args.exchange_dir, **common)
    if args.sync_config:
        return sync_config(**common)
    if args.replay_pending:
        return replay_pending(exchange_dir=args.exchange_dir, **common)
    if args.detect_delta:
        try:
            delta = detect_delta(
                market_db_path=args.market_db, company_db_path=args.company_db,
                exchange_dir=args.exchange_dir, baskets_path=args.baskets,
                overrides_path=args.overrides, aliases_path=args.aliases,
                auto_pull_stale=not args.no_pull, skip_sheet=args.skip_sheet)
        except Exception as exc:
            _emit({"ok": False, "mode": "detect-delta", "error": f"{type(exc).__name__}: {exc}"})
            return EXIT_FAIL
        _emit({"ok": True, "mode": "detect-delta", "generated_date": delta["generated_date"],
               "market_db_status": delta["market_db_status"], "sheet_status": delta["sheet_status"],
               "counts": delta["counts"], "warnings": delta["warnings"],
               "auto_payload": not delta["missing_symbols"] and not delta["pivot_candidates"]})
        return EXIT_OK
    return EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
