#!/usr/bin/env python3
"""股票主题分类 M3：仓外三明治同步 CLI。

Step A（本机）  --detect-delta   只读扫 Extended ∪ Sheet ∪ watchlist，写 inbox/delta.json
                                 （0 待标且 0 异动时同时写 0 行 outbox/payload.json）。

兼容约束：云端 /usr/bin/python3 3.10 + SQLite 3.37.2。src.data.* 只在函数内导入：
`src/data/__init__.py` 会初始化整个数据包并打日志，不能在导入本模块时触发。
本文件不属于 terminal/prosperity/version.py 的 CODE_VERSION_SOURCES。
"""
import argparse
import contextlib
import hashlib
import io
import json
import os
import re
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

SHEET_TAB = "Summary_OSV"          # portfolio/holdings/sheet_book.py:SUMMARY_TAB
SHEET_HEADER_SCAN_ROWS = 15        # sheet_book.py:MAX_HEADER_SCAN_ROWS
SHEET_COLUMNS = ("Stock Ticker", "Market", "Shares")

AUTO_KINDS = frozenset({"etf", "fund_cef", "crypto", "custom_basket", "other"})
CUSTOM_BASKETS = frozenset({"DRAM", "SOX"})
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
            or symbol in extra_etfs):
        return "etf", 0
    if sm.get("is_fund") == 1 or sm.get("reason") == "fund" or prof.get("isFund") is True:
        return "fund_cef", 0
    if (sm.get("reason") == "non_common_instrument" or _NON_COMMON_RE.match(symbol)
            or (sm_row is None and profile_meta is None)
            or (sm.get("reason") == "missing_profile" and not sm.get("company_name")
                and profile_meta is None)):
        return "other", 0
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
            ticker, market, shares = (row[i] if i < len(row) else None for i in header)
            if ticker is None or not str(ticker).strip():
                continue
            try:
                held = float(shares) > 0
            except (TypeError, ValueError):
                continue
            if held:
                found.append((str(ticker).strip(), str(market or "US").strip().upper() or "US"))
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
                existing[r[0]] = {"cik": r[1], "asset_kind": r[2], "in_us_basket_calc": r[3],
                                  "is_superseded": r[4], "profile_sector": r[5],
                                  "profile_industry": r[6]}
    return sm, extended, active, profiles, existing


def _read_watchlist(company_db_path: Path) -> List[str]:
    with contextlib.closing(ro_connect(company_db_path)) as conn:
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
    for item in dirs["outbox"].iterdir():
        if item.is_file() or item.is_symlink():
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
            "symbol": sym, "cik": _text(sm_row.get("cik")) or _text(prof.get("cik")),
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
                    meta[f] and _text(old[f]) and meta[f] != old[f]
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


# ---------------------------------------------------------------- CLI

def _emit(obj: Mapping[str, Any], stream=None) -> None:
    stream = stream or sys.stdout
    stream.write(json.dumps(obj, ensure_ascii=True, sort_keys=True) + "\n")
    stream.flush()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--detect-delta", action="store_true", help="Step A (local)")
    parser.add_argument("--exchange-dir", type=Path, default=DEFAULT_EXCHANGE_DIR)
    parser.add_argument("--market-db", type=Path, default=DEFAULT_MARKET_DB)
    parser.add_argument("--company-db", type=Path, default=DEFAULT_COMPANY_DB)
    parser.add_argument("--baskets", type=Path, default=DEFAULT_BASKETS)
    parser.add_argument("--overrides", type=Path, default=DEFAULT_OVERRIDES)
    parser.add_argument("--aliases", type=Path, default=DEFAULT_ALIASES)
    parser.add_argument("--skip-sheet", action="store_true", help="Step A: do not read the Google Sheet")
    parser.add_argument("--no-pull", action="store_true", help="never run sync_to_cloud.sh --pull")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
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
