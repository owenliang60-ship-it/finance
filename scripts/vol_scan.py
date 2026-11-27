#!/usr/bin/env python3
"""Deterministic volatility scan (/vol-scan).

Pulls one live options chain per symbol (cloud only; MarketData.app is
bound to the cloud IP), reads market.db strictly read-only, and prints
plain metrics + gate flags. No scoring, no win-rate claims.

Usage (cloud):
    ssh aliyun "cd /root/workspace/Finance && python3 scripts/vol_scan.py --json"
    ... --symbols NVDA --legs "NVDA:2026-12-18:C:150:-1;NVDA:2027-01-15:C:150:+1"
Offline replay / no chain:
    python scripts/vol_scan.py --chain-dir DIR --json      # DIR/{SYMBOL}.json
    python scripts/vol_scan.py --no-chain --json

Chain replay file: {"as_of": "YYYY-MM-DD", "data": <MarketData chain response>}.
Writes nothing: no market.db / company.db writes, no snapshot saves.
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config import settings  # noqa: E402
from terminal.options import vol_scan as vs  # noqa: E402
from terminal.options.chain_analyzer import (  # noqa: E402
    ETF_NO_EARNINGS_SYMBOLS,
    get_earnings_proximity,
    parse_chain_response,
)

logger = logging.getLogger("vol_scan")

CHAIN_DTE_FROM = 7
CHAIN_DTE_TO = 100
CHAIN_STRIKE_LIMIT = 30
PRICE_ROWS = 400
PRICE_STALE_DAYS = 4

CLOUD_GATE_MSG = (
    "vol_scan: live option chains come from MarketData.app, which is bound to the "
    "cloud server IP. Run on the cloud:\n"
    "  ssh aliyun \"cd /root/workspace/Finance && python3 scripts/vol_scan.py --json\"\n"
    "or pass --chain-dir DIR (offline replay) or --no-chain (skip chain metrics)."
)


class _NoFMP:
    """Stand-in when FMP_API_KEY is empty: lookups fail -> gate UNKNOWN."""

    def get_earnings(self, symbol):
        raise RuntimeError("FMP_API_KEY not configured")


# ---------------------------------------------------------------------------
# argument parsing
# ---------------------------------------------------------------------------

def parse_legs(spec: str) -> List[Dict[str, Any]]:
    """'SYM:YYYY-MM-DD:C|P:STRIKE:+N;...' -> leg dicts. Raises ValueError."""
    legs = []
    for part in [p.strip() for p in spec.split(";") if p.strip()]:
        bits = part.split(":")
        if len(bits) != 5:
            raise ValueError("leg '%s': expected SYM:EXPIRY:C|P:STRIKE:QTY" % part)
        sym, exp, cp, strike, qty = bits
        datetime.strptime(exp, "%Y-%m-%d")
        if cp.upper() not in ("C", "P"):
            raise ValueError("leg '%s': side must be C or P" % part)
        q = int(qty)
        if q == 0:
            raise ValueError("leg '%s': qty must be non-zero" % part)
        legs.append({"symbol": sym.upper(), "expiration": exp,
                     "side": "call" if cp.upper() == "C" else "put",
                     "strike": float(strike), "qty": q})
    if not legs:
        raise ValueError("empty --legs")
    return legs


# ---------------------------------------------------------------------------
# IO: market.db (read-only) + chain
# ---------------------------------------------------------------------------

def open_market_db(path: Path) -> Tuple[Optional[sqlite3.Connection], Dict[str, Any]]:
    if not path.exists():
        return None, {"status": "MISSING", "path_name": path.name}
    conn = sqlite3.connect("file:{}?mode=ro".format(path), uri=True)
    conn.row_factory = sqlite3.Row
    return conn, {"status": "OK", "path_name": path.name, "mode": "ro"}


def read_bars(conn, symbol: str) -> List[Dict[str, Any]]:
    if conn is None:
        return []
    try:
        rows = conn.execute(
            "SELECT date, open, high, low, close FROM daily_price WHERE symbol = ? "
            "ORDER BY date DESC LIMIT ?", (symbol, PRICE_ROWS)).fetchall()
    except sqlite3.Error as exc:
        logger.warning("%s: daily_price read failed: %s", symbol, exc)
        return []
    return [dict(r) for r in reversed(rows)]


def read_iv_history(conn, symbol: str) -> List[Dict[str, Any]]:
    if conn is None:
        return []
    try:
        rows = conn.execute(
            "SELECT date, iv_30d FROM iv_daily WHERE symbol = ? ORDER BY date DESC LIMIT 252",
            (symbol,)).fetchall()
    except sqlite3.Error as exc:
        logger.warning("%s: iv_daily read failed: %s", symbol, exc)
        return []
    return [dict(r) for r in rows]


def _credit_meta(headers: Dict[str, Any]) -> Dict[str, Any]:
    from portfolio.holdings.live_quote_provider import _extract_credit_headers
    used, remaining = _extract_credit_headers(headers or {})
    return {"credits_used": used, "credits_remaining": remaining,
            "credit_header_available": bool(used or remaining)}


def load_chain(symbol: str, mode: str, chain_dir: Optional[Path], client,
               today: date) -> Dict[str, Any]:
    """-> {status LIVE|REPLAY|MISSING|ERROR|SKIPPED, contracts, spot, as_of, meta}"""
    if mode == "none":
        return {"status": "SKIPPED", "contracts": [], "spot": None, "as_of": today}
    if mode == "dir":
        f = chain_dir / ("%s.json" % symbol)
        if not f.exists():
            return {"status": "MISSING", "contracts": [], "spot": None, "as_of": today}
        raw = json.loads(f.read_text(encoding="utf-8"))
        data = raw.get("data", raw)
        as_of = vs._to_date(raw.get("as_of")) or today
        status, meta = "REPLAY", {"source_file": f.name}
    else:
        params = {
            "from": (today + timedelta(days=CHAIN_DTE_FROM)).isoformat(),
            "to": (today + timedelta(days=CHAIN_DTE_TO)).isoformat(),
            "strikeLimit": CHAIN_STRIKE_LIMIT,
        }
        try:
            data, headers = client._request_with_meta("options/chain/{}".format(symbol), params)
        except Exception as exc:
            return {"status": "ERROR", "contracts": [], "spot": None, "as_of": today,
                    "meta": {"error": type(exc).__name__}}
        as_of, status = today, "LIVE"
        meta = dict(_credit_meta(headers), request_count=1, params=params)
    if not isinstance(data, dict) or data.get("s") != "ok":
        return {"status": "MISSING", "contracts": [], "spot": None, "as_of": as_of,
                "meta": meta}
    contracts = parse_chain_response(data, symbol)
    spots = [p for p in data.get("underlyingPrice", []) if p]
    return {"status": status, "contracts": contracts, "spot": float(spots[0]) if spots else None,
            "as_of": as_of, "meta": meta}


# ---------------------------------------------------------------------------
# scan
# ---------------------------------------------------------------------------

def _price_status(latest: Optional[str], today: date) -> str:
    d = vs._to_date(latest)
    if d is None:
        return "MISSING"
    return "FRESH" if (today - d).days <= PRICE_STALE_DAYS else "STALE"


def _legs_from_chain(legs: List[Dict[str, Any]], contracts: List[Dict[str, Any]]
                     ) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
    out = []
    for leg in legs:
        match = next((c for c in contracts if vs._exp_key(c.get("expiration")) == leg["expiration"]
                      and c.get("side") == leg["side"]
                      and vs._num(c.get("strike")) == leg["strike"]), None)
        if match is None:
            return None, "contract not in chain: %s %s %g" % (
                leg["expiration"], leg["side"], leg["strike"])
        out.append({"expiration": leg["expiration"], "side": leg["side"],
                    "strike": leg["strike"], "qty": leg["qty"], "iv": match.get("iv"),
                    "bid": match.get("bid"), "ask": match.get("ask"),
                    "open_interest": match.get("open_interest"), "volume": match.get("volume")})
    return out, None


def _grid_or_error(legs, spot, r, as_of) -> Dict[str, Any]:
    try:
        return {"available": True, "grid": vs.evaluate_strategy_grid(legs, spot, r, as_of)}
    except ValueError as exc:
        return {"available": False, "reason": str(exc)}


def scan_symbol(symbol: str, chain: Dict[str, Any], bars: List[Dict[str, Any]],
                iv_rows: List[Dict[str, Any]], earnings: Dict[str, Any],
                sector_events: Dict[str, Dict[str, Any]], r: float, today: date,
                custom_legs: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """All metrics + gate flags for one symbol. No scoring."""
    as_of = chain.get("as_of") or today
    contracts, spot = chain.get("contracts") or [], chain.get("spot")
    hv = vs.compute_hv_panel(bars) if bars else None
    rc = vs.compute_range_contraction(bars) if bars else None

    atm = ts = skew = template = None
    if contracts and spot:
        atm = vs.atm_iv_by_expiry(contracts, spot, as_of)
        is_etf = symbol in ETF_NO_EARNINGS_SYMBOLS
        events = [dict(earnings, symbol=symbol)]
        if is_etf:
            events += [dict(e, symbol=s) for s, e in sector_events.items()]
        ts = vs.term_structure_metrics(atm["rows"], events)
        skew = vs.compute_chain_25d_skew(contracts)
        template = vs.build_atm_calendar_template(contracts, spot)
        if template.get("available"):
            template["evaluation"] = _grid_or_error(template["legs"], spot, r, as_of)

    iv30 = atm["iv_30d_live"] if atm else None
    iv_hist = vs.evaluate_iv_history(iv_rows, iv30, as_of)
    exps = sorted({vs._exp_key(c.get("expiration")) for c in contracts})
    sector_flags = vs.build_sector_event_flags(symbol, earnings, sector_events, exps)

    custom = None
    if custom_legs:
        if not contracts or not spot:
            custom = {"available": False, "reason": "no chain"}
        else:
            legs, err = _legs_from_chain(custom_legs, contracts)
            custom = _grid_or_error(legs, spot, r, as_of) if legs else {
                "available": False, "reason": err}

    latest_price = hv["latest_price_date"] if hv else None
    return {
        "symbol": symbol,
        "as_of": as_of.isoformat(),
        "spot": spot,
        "chain": {"status": chain.get("status"), "contracts": len(contracts),
                  "expirations": exps, "meta": chain.get("meta")},
        "hv": hv,
        "range_contraction": rc,
        "atm_iv": atm,
        "term_structure": ts if ts else {"available": False, "reason": "no chain"},
        "skew_25d": skew,
        "iv_history": iv_hist,
        "earnings": earnings,
        "sector_events": sector_flags,
        "atm_calendar_template": template,
        "custom_structure": custom,
        "gates": {
            "chain": chain.get("status"),
            "earnings": earnings.get("zone", "UNKNOWN"),
            "earnings_date_status": earnings.get("date_status", "UNKNOWN"),
            "iv": iv_hist["freshness"]["status"],
            "iv_30d_live": "OK" if iv30 is not None else "UNAVAILABLE",
            "price_data": _price_status(latest_price, today),
            "split_suspect": bool(hv and hv["split_anomalies"]),
            "sector_unknown_events": sector_flags["unknown"],
        },
    }


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Deterministic volatility scan (read-only)")
    p.add_argument("--symbols", default=",".join(vs.VOL_SCAN_SYMBOLS))
    p.add_argument("--legs", default=None,
                   help='e.g. "NVDA:2026-12-18:C:150:-1;NVDA:2027-01-15:C:150:+1"')
    src = p.add_mutually_exclusive_group()
    src.add_argument("--chain-dir", type=Path, default=None)
    src.add_argument("--no-chain", action="store_true")
    p.add_argument("--market-db", type=Path, default=settings.MARKET_DB_PATH)
    p.add_argument("--json", action="store_true")
    return p


def _text_summary(out: Dict[str, Any]) -> str:
    lines = ["vol_scan as of {} ET".format(out["meta"]["today_et"])]
    for sym, s in out["symbols"].items():
        g, hv, ts = s["gates"], s["hv"] or {}, s["term_structure"]
        iv30 = (s["atm_iv"] or {}).get("iv_30d_live")
        fmt = lambda v: "—" if v is None else "{:.1f}%".format(v * 100)  # noqa: E731
        lines.append("{:<5} spot={} IV30={} HV20={} TS={} earnings={}[{}] iv_hist={} chain={}".format(
            sym, s["spot"] if s["spot"] is not None else "—", fmt(iv30), fmt(hv.get("hv20")),
            ts.get("state", "—"), g["earnings"], g["earnings_date_status"], g["iv"], g["chain"]))
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr,
                        format="%(levelname)s %(name)s: %(message)s")
    args = build_parser().parse_args(argv)

    if args.chain_dir is None and not args.no_chain and not settings.IS_CLOUD:
        print(CLOUD_GATE_MSG, file=sys.stderr)
        return 2

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    legs_by_sym: Dict[str, List[Dict[str, Any]]] = {}
    if args.legs:
        try:
            for leg in parse_legs(args.legs):
                legs_by_sym.setdefault(leg["symbol"], []).append(leg)
        except ValueError as exc:
            print("vol_scan: bad --legs: %s" % exc, file=sys.stderr)
            return 2
        for sym in legs_by_sym:
            if sym not in symbols:
                symbols.append(sym)

    today = datetime.now(ZoneInfo("America/New_York")).date()
    mode = "none" if args.no_chain else ("dir" if args.chain_dir else "live")
    client = None
    if mode == "live":
        from src.data.marketdata_client import MarketDataClient
        client = MarketDataClient()

    fmp = None
    if not settings.FMP_API_KEY:
        fmp = _NoFMP()
    earnings_cache: Dict[str, Dict[str, Any]] = {}

    def _earn(sym: str) -> Dict[str, Any]:
        if sym not in earnings_cache:
            earnings_cache[sym] = get_earnings_proximity(sym, fmp_client=fmp)
        return earnings_cache[sym]

    from terminal.options.risk_free_rate import get_risk_free_rate
    r = get_risk_free_rate(today.isoformat())

    conn, db_meta = open_market_db(args.market_db)
    results = {}
    try:
        for sym in symbols:
            chain = load_chain(sym, mode, args.chain_dir, client, today)
            sector = {s: _earn(s) for s in vs.SECTOR_CORE_SYMBOLS if s != sym}
            results[sym] = scan_symbol(
                sym, chain, read_bars(conn, sym), read_iv_history(conn, sym),
                _earn(sym), sector, r, today, legs_by_sym.get(sym))
    finally:
        if conn is not None:
            conn.close()

    out = {
        "meta": {
            "today_et": today.isoformat(),
            "chain_mode": mode,
            "risk_free_rate": r,
            "market_db": db_meta,
            "chain_requests": sum(1 for s in results.values() if s["chain"]["status"] in ("LIVE", "ERROR")),
            "model_assumptions": vs.MODEL_ASSUMPTIONS,
            "conventions": "T = dte/365; HV annualized sqrt(252); IV decimals; P&L per share",
        },
        "symbols": results,
    }
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
    else:
        print(_text_summary(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
