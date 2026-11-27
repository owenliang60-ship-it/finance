"""Vol Scan — deterministic volatility / options metrics (pure functions, no IO).

Every number here is a plain calculation from the inputs. No scoring, no
win-rate or edge claims. Callers (scripts/vol_scan.py) do all IO.

Conventions:
- DTE is calendar days; option time T = dte / 365.0 everywhere.
- Realized vol annualizes daily log returns with sqrt(252).
- IV values are decimals (0.45 = 45%); "pt" fields are vol points (×100).
- Strategy P&L is per share (multiply by 100 for one contract).
"""
from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from terminal.options.iv_solver import bs_delta, bs_gamma, bs_price, bs_theta, bs_vega

TRADING_DAYS = 252
SPLIT_MOVE_THRESHOLD = 0.40
HV_WINDOWS = (5, 10, 20, 30, 60)
FLAT_BAND_PT = 1.0
SECTOR_CORE_SYMBOLS = ("NVDA", "MU", "AMD", "TSM")
MODEL_ASSUMPTIONS = "European Black-Scholes, zero dividend, no early exercise"
IV_FLOOR = 0.01


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _to_date(value: Any) -> Optional[date]:
    if isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    if isinstance(value, (int, float)):
        # MarketData.app expiration: Unix epoch seconds (16:00 ET)
        if value < 1_000_000_000:
            return None
        return datetime.fromtimestamp(value, ZoneInfo("America/New_York")).date()
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _exp_key(value: Any) -> str:
    """Expiration as "YYYY-MM-DD" when parseable (ISO or epoch), else str()."""
    d = _to_date(value)
    return d.isoformat() if d is not None else str(value)


def _num(value: Any) -> Optional[float]:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _mean(xs: List[float]) -> Optional[float]:
    return sum(xs) / len(xs) if xs else None


def _stdev(xs: List[float]) -> Optional[float]:
    if len(xs) < 2:
        return None
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def _daily_rows(bars: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One row per bar after the first: pct change, log return, intraday
    amplitude vs prev close, split_suspect flag (|pct| > 40%)."""
    rows = []
    for prev, cur in zip(bars, bars[1:]):
        pc, c = _num(prev.get("close")), _num(cur.get("close"))
        if not pc or not c or pc <= 0 or c <= 0:
            continue
        pct = c / pc - 1.0
        hi, lo = _num(cur.get("high")), _num(cur.get("low"))
        amp = (hi - lo) / pc if hi is not None and lo is not None and hi >= lo else None
        rows.append({
            "date": str(cur.get("date"))[:10],
            "close": c,
            "pct_change": pct,
            "log_return": math.log(c / pc),
            "intraday_amp": amp,
            "split_suspect": abs(pct) > SPLIT_MOVE_THRESHOLD,
        })
    return rows


# ---------------------------------------------------------------------------
# realized vol
# ---------------------------------------------------------------------------

def compute_hv_panel(bars: List[Dict[str, Any]]) -> Dict[str, Any]:
    """HV 5/10/20/30/60 from ascending OHLC bars.

    A day with |close change| > 40% is flagged split_suspect and its log
    return is dropped (each window uses the last N valid returns).
    """
    rows = _daily_rows(bars)
    anomalies = [{"date": r["date"], "pct_change": round(r["pct_change"], 6)}
                 for r in rows if r["split_suspect"]]
    rets = [r["log_return"] for r in rows if not r["split_suspect"]]
    out: Dict[str, Any] = {}
    for n in HV_WINDOWS:
        sd = _stdev(rets[-n:]) if len(rets) >= n else None
        out["hv%d" % n] = sd * math.sqrt(TRADING_DAYS) if sd is not None else None
    for n in (10, 20):
        window = rets[-n:] if len(rets) >= n else []
        sq = sorted((x * x for x in window), reverse=True)
        total = sum(sq)
        out["hv%d_top2_var_share" % n] = (sum(sq[:2]) / total) if total > 0 else None
    out["split_anomalies"] = anomalies
    out["latest_price_date"] = str(bars[-1].get("date"))[:10] if bars else None
    out["recent_daily_moves"] = [
        {"date": r["date"], "close": r["close"], "pct_change": r["pct_change"],
         "intraday_amp": r["intraday_amp"], "split_suspect": r["split_suspect"]}
        for r in rows[-5:]
    ]
    return out


def compute_range_contraction(bars: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Intraday range contraction. amplitude = (high - low) / prev_close;
    split_suspect days are skipped (neither counted nor breaking a streak)."""
    rows = [r for r in _daily_rows(bars) if not r["split_suspect"]]

    def _streak(key: str) -> int:
        n = 0
        for r in reversed(rows):
            v = r[key]
            if v is None or abs(v) >= 0.05:
                break
            n += 1
        return n

    amps = [r["intraday_amp"] for r in rows if r["intraday_amp"] is not None]
    r5 = _mean(amps[-5:]) if len(amps) >= 5 else None
    r20 = _mean(amps[-20:]) if len(amps) >= 20 else None
    rolling = [sum(amps[i - 5:i]) / 5 for i in range(5, len(amps) + 1)]
    pctl = None
    if r5 is not None and len(rolling) >= TRADING_DAYS:
        window = rolling[-TRADING_DAYS:]
        pctl = 100.0 * sum(1 for v in window if v <= r5) / len(window)
    return {
        "consec_no_5pct_close": _streak("pct_change"),
        "consec_no_5pct_intraday_amp": _streak("intraday_amp"),
        "range_5d_avg": r5,
        "range_20d_avg": r20,
        "range_5d_over_20d": (r5 / r20) if r5 is not None and r20 else None,
        "range_5d_1y_pctl": pctl,
    }


# ---------------------------------------------------------------------------
# chain: ATM IV per expiry, term structure, skew
# ---------------------------------------------------------------------------

def _valid_iv(c: Dict[str, Any]) -> bool:
    iv = _num(c.get("iv"))
    return iv is not None and iv > 0 and _num(c.get("strike")) is not None


def _interp_side(side_contracts: List[Dict[str, Any]], spot: float) -> Optional[float]:
    by_k: Dict[float, float] = {}
    for c in side_contracts:
        by_k.setdefault(float(c["strike"]), float(c["iv"]))
    below = [k for k in by_k if k <= spot]
    above = [k for k in by_k if k >= spot]
    if not below or not above:
        return None
    k1, k2 = max(below), min(above)
    if k1 == k2:
        return by_k[k1]
    w = (spot - k1) / (k2 - k1)
    return by_k[k1] + w * (by_k[k2] - by_k[k1])


def _spread_pct(bid: Optional[float], ask: Optional[float]) -> Optional[float]:
    if bid is None or ask is None or bid < 0 or ask <= 0 or ask < bid:
        return None
    mid = (bid + ask) / 2
    return (ask - bid) / mid if mid > 0 else None


def _expiry_dte(c: Dict[str, Any], as_of_date: Optional[date]) -> Optional[int]:
    exp = _to_date(c.get("expiration"))
    if exp is not None and as_of_date is not None:
        return (exp - as_of_date).days
    dte = c.get("dte")
    return int(dte) if dte is not None else None


def atm_iv_by_expiry(contracts: List[Dict[str, Any]], spot: float,
                     as_of_date: date) -> Dict[str, Any]:
    """Per-expiry ATM IV (strike-interpolated, call/put averaged) + 30D
    constant-maturity IV by total-variance interpolation between the two
    expiries that bracket 30 DTE. No cross-expiry averaging."""
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for c in contracts:
        groups.setdefault(_exp_key(c.get("expiration")), []).append(c)

    rows = []
    for exp, cs in groups.items():
        dte = _expiry_dte(cs[0], as_of_date)
        if dte is None or dte <= 0:
            continue
        calls = [c for c in cs if c.get("side") == "call" and _valid_iv(c)]
        puts = [c for c in cs if c.get("side") == "put" and _valid_iv(c)]
        call_iv, put_iv = _interp_side(calls, spot), _interp_side(puts, spot)
        if call_iv is not None and put_iv is not None:
            atm_iv, src = (call_iv + put_iv) / 2, "call_put_avg"
        elif call_iv is not None:
            atm_iv, src = call_iv, "call_only"
        elif put_iv is not None:
            atm_iv, src = put_iv, "put_only"
        else:
            atm_iv, src = None, "none"
        strikes = sorted({float(c["strike"]) for c in cs if _num(c.get("strike")) is not None})
        atm_k = min(strikes, key=lambda k: (abs(k - spot), k)) if strikes else None
        quote = next((c for c in cs if c.get("side") == "call"
                      and _num(c.get("strike")) == atm_k), None) or \
            next((c for c in cs if _num(c.get("strike")) == atm_k), None) or {}
        bid, ask = _num(quote.get("bid")), _num(quote.get("ask"))
        rows.append({
            "expiration": exp, "dte": dte, "atm_strike": atm_k,
            "atm_iv": atm_iv, "atm_iv_source": src,
            "call_atm_iv": call_iv, "put_atm_iv": put_iv,
            "atm_quote_side": quote.get("side"),
            "atm_bid": bid, "atm_ask": ask, "atm_spread_pct": _spread_pct(bid, ask),
            "atm_oi": quote.get("open_interest"), "atm_volume": quote.get("volume"),
        })
    rows.sort(key=lambda r: r["dte"])

    iv30, reason, brackets = None, None, None
    valid = [r for r in rows if r["atm_iv"] is not None]
    exact = next((r for r in valid if r["dte"] == 30), None)
    if exact:
        iv30, brackets = exact["atm_iv"], [exact["expiration"]]
    else:
        lo = [r for r in valid if r["dte"] < 30]
        hi = [r for r in valid if r["dte"] > 30]
        if lo and hi:
            a, b = lo[-1], hi[0]
            t1, t2, t30 = a["dte"] / 365.0, b["dte"] / 365.0, 30 / 365.0
            w1, w2 = a["atm_iv"] ** 2 * t1, b["atm_iv"] ** 2 * t2
            w30 = w1 + (w2 - w1) * (t30 - t1) / (t2 - t1)
            brackets = [a["expiration"], b["expiration"]]
            if w30 > 0:
                iv30 = math.sqrt(w30 / t30)
            else:
                reason = "negative_total_variance"
        else:
            reason = "no_bracketing_expiries"
    return {"rows": rows, "iv_30d_live": iv30, "iv_30d_live_reason": reason,
            "iv_30d_brackets": brackets}


def term_structure_metrics(atm_rows: List[Dict[str, Any]],
                           events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Near/mid/far ATM IV, front-minus-back, state, adjacent forward vols,
    and a factual label of whether an earnings event sits before the front
    expiry (CONFIRMED / ESTIMATED / NO_IDENTIFIED_EVENT)."""
    rows = sorted([r for r in atm_rows if r.get("atm_iv") is not None], key=lambda r: r["dte"])
    if len(rows) < 2:
        return {"available": False, "reason": "fewer_than_2_expiries", "rows": rows}

    def _brief(r):
        return {"expiration": r["expiration"], "dte": r["dte"], "atm_iv": r["atm_iv"]}

    near, far = rows[0], rows[-1]
    fmb = (near["atm_iv"] - far["atm_iv"]) * 100.0
    if fmb > FLAT_BAND_PT:
        state = "BACKWARDATION"
    elif fmb < -FLAT_BAND_PT:
        state = "CONTANGO"
    else:
        state = "FLAT"

    dated = [(e, _to_date(e.get("earnings_date"))) for e in events or []]
    dated = [(e, d) for e, d in dated if d is not None]

    forwards = []
    for a, b in zip(rows, rows[1:]):
        t1, t2 = a["dte"] / 365.0, b["dte"] / 365.0
        if t2 <= t1:
            continue
        num = b["atm_iv"] ** 2 * t2 - a["atm_iv"] ** 2 * t1
        d1, d2 = _to_date(a["expiration"]), _to_date(b["expiration"])
        between = [e.get("symbol") for e, d in dated
                   if d1 is not None and d2 is not None and d1 < d <= d2]
        forwards.append({
            "from_expiration": a["expiration"], "to_expiration": b["expiration"],
            "from_dte": a["dte"], "to_dte": b["dte"],
            "forward_iv": math.sqrt(num / (t2 - t1)) if num >= 0 else None,
            "flag": None if num >= 0 else "BAD_QUOTE",
            "events_between": between,
        })

    source, before = None, []
    if state == "BACKWARDATION":
        front = _to_date(near["expiration"])
        before = [e for e, d in dated if front is not None and d <= front]
        if any(e.get("date_status") == "CONFIRMED" for e in before):
            source = "CONFIRMED_EVENT"
        elif any(e.get("date_status") == "ESTIMATED" for e in before):
            source = "ESTIMATED_EVENT"
        else:
            source = "NO_IDENTIFIED_EVENT"

    return {
        "available": True,
        "near": _brief(near), "mid": _brief(rows[len(rows) // 2]), "far": _brief(far),
        "front_minus_back_pt": fmb,
        "state": state,
        "forwards": forwards,
        "inversion_source": source,
        "inversion_events": [{"symbol": e.get("symbol"), "earnings_date": e.get("earnings_date"),
                              "date_status": e.get("date_status")} for e in before],
    }


def compute_chain_25d_skew(contracts: List[Dict[str, Any]], dte_min: int = 20,
                           dte_max: int = 45) -> Optional[Dict[str, Any]]:
    """25Δ put IV − 25Δ call IV on the expiry closest to 30 DTE inside
    [dte_min, dte_max]. None when no expiry in the window has both legs."""
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for c in contracts:
        groups.setdefault(_exp_key(c.get("expiration")), []).append(c)
    cands = []
    for exp, cs in groups.items():
        dte = next((c.get("dte") for c in cs if c.get("dte") is not None), None)
        if dte is None or not (dte_min <= dte <= dte_max):
            continue
        ok = [c for c in cs if _num(c.get("delta")) is not None and _valid_iv(c)]
        puts = [c for c in ok if c.get("side") == "put"]
        calls = [c for c in ok if c.get("side") == "call"]
        if puts and calls:
            cands.append((exp, int(dte), puts, calls))
    if not cands:
        return None
    exp, dte, puts, calls = min(cands, key=lambda x: (abs(x[1] - 30), x[1]))
    p = min(puts, key=lambda c: abs(float(c["delta"]) + 0.25))
    c = min(calls, key=lambda c: abs(float(c["delta"]) - 0.25))
    return {
        "expiration": exp, "dte": dte,
        "put_iv_25d": float(p["iv"]), "call_iv_25d": float(c["iv"]),
        "put_delta": float(p["delta"]), "call_delta": float(c["delta"]),
        "put_strike": float(p["strike"]), "call_strike": float(c["strike"]),
        "skew_25d_pt": (float(p["iv"]) - float(c["iv"])) * 100.0,
    }


# ---------------------------------------------------------------------------
# IV history gate
# ---------------------------------------------------------------------------

def evaluate_iv_history(history_rows: List[Dict[str, Any]], current_iv_30d: Optional[float],
                        as_of_date: date, max_stale_days: int = 3,
                        min_rows: int = 200) -> Dict[str, Any]:
    """30D IV rank / percentile over the last 252 iv_daily rows, gated on
    freshness (calendar days) and row count. Any gate failure -> None + reason."""
    valid = []
    for r in history_rows or []:
        d, v = _to_date(r.get("date")), _num(r.get("iv_30d"))
        if d is not None and v is not None and v > 0:
            valid.append((d, v))
    valid.sort(key=lambda x: x[0], reverse=True)
    window = valid[:TRADING_DAYS]

    if not window:
        fresh = {"status": "MISSING", "date": None, "age_days": None}
    else:
        age = (as_of_date - window[0][0]).days
        fresh = {"status": "FRESH" if age <= max_stale_days else "STALE",
                 "date": window[0][0].isoformat(), "age_days": age}

    values = [v for _, v in window]
    out: Dict[str, Any] = {
        "freshness": fresh,
        "iv_30d_history_rows": len(values),
        "iv_30d_current": current_iv_30d,
        "iv_30d_high_252d": max(values) if values else None,
        "iv_30d_low_252d": min(values) if values else None,
        "iv_30d_rank_252d": None,
        "iv_30d_pctl_252d": None,
        "reason": None,
    }
    if fresh["status"] != "FRESH":
        out["reason"] = "iv_daily %s (latest %s)" % (fresh["status"], fresh["date"])
    elif len(values) < min_rows:
        out["reason"] = "iv_daily rows %d < %d" % (len(values), min_rows)
    elif current_iv_30d is None:
        out["reason"] = "no current 30D IV"
    elif max(values) == min(values):
        out["reason"] = "flat iv history"
    else:
        hi, lo = max(values), min(values)
        out["iv_30d_rank_252d"] = (current_iv_30d - lo) / (hi - lo) * 100.0
        out["iv_30d_pctl_252d"] = 100.0 * sum(1 for v in values if v < current_iv_30d) / len(values)
    return out


# ---------------------------------------------------------------------------
# events
# ---------------------------------------------------------------------------

def build_sector_event_flags(symbol: str, own_event: Dict[str, Any],
                             sector_events: Dict[str, Dict[str, Any]],
                             expirations: List[str]) -> Dict[str, Any]:
    """Where each earnings date (own + sector core) falls relative to the
    listed expirations. Pure position labels, no judgement."""
    exps = sorted(d for d in (_to_date(e) for e in expirations) if d is not None)
    items = [(symbol.upper(), own_event or {})]
    items += [(s, e or {}) for s, e in sorted(sector_events.items()) if s != symbol.upper()]
    events, unknown = [], []
    for sym, ev in items:
        d = _to_date(ev.get("earnings_date"))
        if d is None:
            if ev.get("date_status") not in ("NOT_APPLICABLE",):
                unknown.append(sym)
            continue
        prev_e = max((e for e in exps if e < d), default=None)
        next_e = min((e for e in exps if e >= d), default=None)
        if not exps:
            pos = "NO_EXPIRIES"
        elif d in exps:
            pos = "ON_EXPIRY"
        elif d < exps[0]:
            pos = "BEFORE_FIRST_EXPIRY"
        elif d > exps[-1]:
            pos = "AFTER_LAST_EXPIRY"
        else:
            pos = "BETWEEN_EXPIRIES"
        events.append({
            "symbol": sym, "earnings_date": d.isoformat(),
            "date_status": ev.get("date_status"), "zone": ev.get("zone"),
            "position": pos,
            "prev_expiry": prev_e.isoformat() if prev_e else None,
            "next_expiry": next_e.isoformat() if next_e else None,
        })
    return {"events": events, "unknown": unknown}


# ---------------------------------------------------------------------------
# strategy templates + grid
# ---------------------------------------------------------------------------

def _valid_quote(c: Dict[str, Any]) -> bool:
    bid, ask = _num(c.get("bid")), _num(c.get("ask"))
    return (bid is not None and ask is not None and bid > 0 and ask >= bid
            and _valid_iv(c))


def build_atm_calendar_template(contracts: List[Dict[str, Any]], spot: float,
                                short_target_dte: int = 30,
                                long_target_dte: int = 60) -> Dict[str, Any]:
    """Short call ~30D (15–40 DTE) / long call ~60D (45–90 DTE), same strike
    nearest spot. Missing leg or invalid bid/ask -> available False."""
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for c in contracts:
        if c.get("side") == "call" and c.get("dte") is not None:
            groups.setdefault(_exp_key(c.get("expiration")), []).append(c)

    def _pick(lo, hi, target):
        cands = [(e, int(cs[0]["dte"])) for e, cs in groups.items() if lo <= cs[0]["dte"] <= hi]
        return min(cands, key=lambda x: (abs(x[1] - target), x[1])) if cands else None

    s, l = _pick(15, 40, short_target_dte), _pick(45, 90, long_target_dte)
    if s is None or l is None:
        return {"available": False, "reason": "missing_%s_leg_expiry" % ("short" if s is None else "long")}
    s_by_k = {float(c["strike"]): c for c in groups[s[0]]}
    l_by_k = {float(c["strike"]): c for c in groups[l[0]]}
    common = sorted(set(s_by_k) & set(l_by_k))
    if not common:
        return {"available": False, "reason": "no_common_strike"}
    k = min(common, key=lambda x: (abs(x - spot), x))
    sc, lc = s_by_k[k], l_by_k[k]
    if not _valid_quote(sc) or not _valid_quote(lc):
        return {"available": False, "reason": "invalid_bid_ask_or_iv", "strike": k}

    def _leg(c, qty):
        return {"expiration": _exp_key(c["expiration"]), "dte": int(c["dte"]), "side": "call",
                "strike": k, "qty": qty, "iv": float(c["iv"]),
                "bid": float(c["bid"]), "ask": float(c["ask"]),
                "open_interest": c.get("open_interest"), "volume": c.get("volume")}

    return {"available": True, "strike": k, "legs": [_leg(sc, -1), _leg(lc, 1)]}


def _leg_dte(leg: Dict[str, Any], as_of_date: date) -> int:
    d = _expiry_dte(leg, as_of_date)
    if d is None or d <= 0:
        raise ValueError("leg %s has no positive DTE" % leg.get("expiration"))
    return d


def _value(legs: List[Dict[str, Any]], S: float, t: int, r: float,
           ivs: List[float]) -> Tuple[float, List[float]]:
    per = []
    for leg, iv in zip(legs, ivs):
        T = max(leg["_dte"] - t, 0) / 365.0
        per.append(bs_price(S, leg["strike"], T, r, iv, leg["side"]))
    return sum(leg["qty"] * p for leg, p in zip(legs, per)), per


def _roots(f, lo: float, hi: float, n: int = 400) -> List[float]:
    xs = [lo + (hi - lo) * i / n for i in range(n + 1)]
    ys = [f(x) for x in xs]
    out = []
    for i in range(n):
        a, b, fa, fb = xs[i], xs[i + 1], ys[i], ys[i + 1]
        if fa == 0:
            out.append(a)
        elif fa * fb < 0:
            for _ in range(60):
                m = (a + b) / 2
                fm = f(m)
                if fa * fm <= 0:
                    b, fb = m, fm
                else:
                    a, fa = m, fm
            out.append((a + b) / 2)
    return [round(x, 4) for x in out]


def evaluate_strategy_grid(legs: List[Dict[str, Any]], spot: float, r: float,
                           as_of_date: date,
                           iv_scenarios: Optional[Dict[str, Dict[str, float]]] = None
                           ) -> Dict[str, Any]:
    """Mid and Natural P&L grid for a multi-leg structure.

    legs: {expiration, side 'call'|'put', strike, qty (signed contracts),
    iv, bid, ask, open_interest?, volume?}. Each leg is priced with its own
    expiry and own IV. iv_scenarios: {label: {expiration: absolute vol-point
    shift}} (e.g. -0.10 = down 10 pt; new IV floored at 0.01).

    Natural: entry at the touch (buy at ask, sell at bid); exit pays half
    the leg's bid/ask spread per contract on every leg.
    """
    if not legs:
        raise ValueError("no legs")
    L = []
    for leg in legs:
        bid, ask, iv = _num(leg.get("bid")), _num(leg.get("ask")), _num(leg.get("iv"))
        if bid is None or ask is None or ask < bid or bid < 0 or iv is None or iv <= 0:
            raise ValueError("leg %s %s: invalid bid/ask/iv" % (leg.get("expiration"), leg.get("strike")))
        L.append(dict(leg, side=str(leg["side"]).lower(), strike=float(leg["strike"]),
                      qty=int(leg["qty"]), iv=iv, bid=bid, ask=ask,
                      expiration=str(leg["expiration"]), _dte=_leg_dte(leg, as_of_date)))

    mids = [(l["bid"] + l["ask"]) / 2 for l in L]
    entry_mid = sum(l["qty"] * m for l, m in zip(L, mids))
    entry_nat = sum(l["qty"] * (l["ask"] if l["qty"] > 0 else l["bid"]) for l in L)
    half_spread_cost = sum(abs(l["qty"]) * (l["ask"] - l["bid"]) / 2 for l in L)

    greeks = {"delta": 0.0, "gamma": 0.0, "theta_daily": 0.0, "vega_1pt": 0.0}
    for l in L:
        T = l["_dte"] / 365.0
        greeks["delta"] += l["qty"] * bs_delta(spot, l["strike"], T, r, l["iv"], l["side"])
        greeks["gamma"] += l["qty"] * bs_gamma(spot, l["strike"], T, r, l["iv"])
        greeks["theta_daily"] += l["qty"] * bs_theta(spot, l["strike"], T, r, l["iv"], l["side"])
        greeks["vega_1pt"] += l["qty"] * bs_vega(spot, l["strike"], T, r, l["iv"]) / 100.0

    min_dte = min(l["_dte"] for l in L)
    max_dte = max(l["_dte"] for l in L)
    front_exps = sorted({l["expiration"] for l in L if l["_dte"] == min_dte})
    back_exps = sorted({l["expiration"] for l in L if l["_dte"] == max_dte})

    def _avg_iv(exps):
        vals = [l["iv"] for l in L if l["expiration"] in exps]
        return sum(vals) / len(vals)

    all_exps = sorted({l["expiration"] for l in L})
    exp_iv = {e: _avg_iv([e]) for e in all_exps}
    if iv_scenarios is None:
        back_iv = _avg_iv(back_exps)
        iv_scenarios = {
            "front_only_to_back": {e: back_iv - exp_iv[e] for e in front_exps},
            "parallel_minus_10pt": {e: -0.10 for e in all_exps},
            "both_to_55": {e: 0.55 - exp_iv[e] for e in all_exps},
            "both_to_45": {e: 0.45 - exp_iv[e] for e in all_exps},
        }
    scenarios = {"unchanged": {}}
    scenarios.update(iv_scenarios)

    time_points = sorted({t for t in (0, 1, 7, 14, min_dte - 1) if 0 <= t <= min_dte})
    front_iv = _avg_iv(front_exps)
    sigma = front_iv * math.sqrt(min_dte / 365.0)
    price_points = [("0%", spot)]
    for k in (0.05, 0.10):
        price_points += [("+%d%%" % round(k * 100), spot * (1 + k)),
                         ("-%d%%" % round(k * 100), spot * (1 - k))]
    for n in (1, 2):
        price_points += [("+%dσ" % n, spot * (1 + n * sigma)),
                         ("-%dσ" % n, spot * max(1 - n * sigma, 0.01))]

    grid = []
    for label, shifts in scenarios.items():
        ivs = [max(l["iv"] + float(shifts.get(l["expiration"], 0.0)), IV_FLOOR) for l in L]
        for t in time_points:
            for plabel, S in price_points:
                v, _ = _value(L, S, t, r, ivs)
                grid.append({
                    "scenario": label, "day": t, "price_label": plabel, "price": S,
                    "pnl_mid": v - entry_mid,
                    "pnl_natural": (v - half_spread_cost) - entry_nat,
                })

    breakeven: Dict[str, Any] = {"basis": "mid", "evaluated_at_day": min_dte}
    lo, hi = spot * 0.5, spot * 1.5
    if min_dte == max_dte:
        base_ivs = [l["iv"] for l in L]
        breakeven["at_expiry"] = _roots(
            lambda S: _value(L, S, min_dte, r, base_ivs)[0] - entry_mid, lo, hi)
    else:
        for key, shift in (("back_iv_unchanged", 0.0), ("back_iv_minus_20pt", -0.20)):
            ivs = [l["iv"] if l["_dte"] == min_dte else max(l["iv"] + shift, IV_FLOOR) for l in L]
            breakeven[key] = _roots(
                lambda S, ivs=ivs: _value(L, S, min_dte, r, ivs)[0] - entry_mid, lo, hi)

    return {
        "legs": [{k: v for k, v in l.items() if not k.startswith("_")} for l in L],
        "min_dte": min_dte, "max_dte": max_dte, "r": r, "spot": spot,
        "entry_mid_per_share": entry_mid,
        "entry_natural_per_share": entry_nat,
        "slippage_per_share": entry_nat - entry_mid,
        "exit_half_spread_per_share": half_spread_cost,
        "per_leg_liquidity": [{
            "expiration": l["expiration"], "strike": l["strike"], "side": l["side"],
            "qty": l["qty"], "bid": l["bid"], "ask": l["ask"],
            "spread_pct": _spread_pct(l["bid"], l["ask"]),
            "oi": l.get("open_interest"), "volume": l.get("volume"),
        } for l in L],
        "net_greeks": greeks,
        "sigma_move_front": sigma,
        "time_points": time_points,
        "scenarios": {k: dict(v) for k, v in scenarios.items()},
        "grid": grid,
        "breakeven_at_short_expiry": breakeven,
        "per_contract_multiplier": 100,
        "model_assumptions": MODEL_ASSUMPTIONS,
    }
