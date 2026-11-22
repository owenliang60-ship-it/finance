"""Read-only fact layer: wraps prosperity's public consensus functions; market.db is never written.

Currency is typed per field (plan 2026-11-22 「逐字段币种合约」): street EPS, street revenue and
prices are USD; income rows and fmp_estimates are in the reporting currency. build_consensus and
unit_verified are not used, because they blank NTM/TTM for every ADR.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Mapping, Optional, Tuple

from src.data.market_store import MarketStore
from terminal.financial_history import apply_recorded_splits, detect_split_like_jumps
from terminal.prosperity.config import ESTIMATE_MATCH_DAYS, PRICE_MAX_STALENESS_DAYS
from terminal.prosperity.consensus import ntm_eps, ttm_eps
from terminal.prosperity.loader import load_history
from terminal.prosperity.street_eps import announced_eps, pair_statement_dates
from terminal.prosperity.types import EpsQuarter, NtmResult, SymbolHistory
from terminal.valuation.config import YF_MAX_AGE_DAYS
from terminal.valuation.kernel import fin

SUM_KEYS = ("eps_low", "eps_high", "rev_avg", "rev_low", "rev_high")
FY_STEP_DAYS, FY_STEP_TOLERANCE = 365, 20


@dataclass(frozen=True)
class FactPack:
    symbol: str
    as_of: str
    reporting_currency: Optional[str]          # income 最新行的 reported_currency
    is_adr: Optional[bool]
    price: Optional[float]                     # 距 as_of <= 7 天的最后收盘，否则 None
    price_date: Optional[str]                  # 最后收盘日（过期时也保留，供 P1 说明）
    closes: Tuple[Tuple[str, float], ...]      # 已做记录拆股复权，升序，<= as_of
    price_jump_notes: Tuple[str, ...]          # detect_split_like_jumps 的输出
    eps: Tuple[EpsQuarter, ...]                # announced_eps + pair_statement_dates，USD
    ttm_eps_usd: Optional[float]
    ttm_quarters: Tuple[str, ...]
    ntm: NtmResult                             # 报告币种
    ntm_weights: Tuple[float, ...]             # 与 ntm.quarters 等长；不可解时为 ()
    ntm_sums: Mapping[str, Optional[float]]    # 键：eps_low, eps_high, rev_avg, rev_low, rev_high
    next_q: Optional[Mapping]                  # 同快照下第一未公告季的 Q 行
    upcoming: Optional[Mapping]                # fmp_earnings 中 eps_actual 为空且与 next_q 同季的行
    yf_0q: Optional[Mapping]                   # forward_estimates 最新日期的 '0q' 行，距 as_of <= 14 天
    fy_pair: Optional[Tuple[Mapping, Mapping]] # FY1, FY2
    income: Tuple[Mapping, ...]                # 升序，季末 <= as_of
    earnings: Tuple[Mapping, ...]              # 原始 fmp_earnings 行
    shares: Optional[float]                    # income 最新行 weighted_average_shs_out_dil


def _d(value: str) -> date:
    return date.fromisoformat(value[:10])


def _days(a: str, b: str) -> int:
    return (_d(a) - _d(b)).days


def _closes(history: SymbolHistory, as_of: str):
    raw = [(_d(d), c) for d, c in history.closes if d[:10] <= as_of and fin(c) is not None and c > 0]
    adjusted, _ = apply_recorded_splits(raw, history.splits)
    notes = tuple(detect_split_like_jumps(adjusted))
    return tuple((d.isoformat(), c) for d, c in adjusted), notes


def _weights(ntm: NtmResult) -> Tuple[float, ...]:
    if fin(ntm.value) is None:
        return ()
    if ntm.basis == "quarter_sum":
        return (1.0,) * 4
    if ntm.basis != "ntm_fy_blend" or len(ntm.quarters) != 2:
        return ()
    e1, e2 = fin(ntm.quarters[0][1]), fin(ntm.quarters[1][1])
    if e1 is None or e2 is None or e1 == e2:
        return ()
    w = (ntm.value - e2) / (e1 - e2)
    if not 0 < w <= 1 or abs(w * 4 - round(w * 4)) > 1e-6:
        return ()
    w = round(w * 4) / 4     # prosperity's _fy_blend weight is k/4; drop the float residue
    return (w, 1 - w)


def _sums(snap_rows, ntm: NtmResult, weights) -> dict:
    period = "Q" if ntm.basis == "quarter_sum" else "FY"
    rows = []
    for fiscal, _, _ in ntm.quarters:
        match = [r for r in snap_rows if r.get("period_type") == period and r["fiscal_date"][:10] == fiscal]
        rows.append(match[0] if len(match) == 1 else None)
    out = {}
    for key in SUM_KEYS:
        total = 0.0
        for w, row in zip(weights, rows):
            v = fin(row.get(key)) if row is not None else None
            if v is None:
                total = None
                break
            total += w * v
        out[key] = total if weights and total is not None else None
    return out


def _next_q(snap_rows, eps: Tuple[EpsQuarter, ...]):
    if not eps:
        return None
    anchor = eps[-1].fiscal_date
    cands = sorted((r for r in snap_rows if r.get("period_type") == "Q"
                    and _days(r["fiscal_date"], anchor) > ESTIMATE_MATCH_DAYS
                    and all(abs(_days(r["fiscal_date"], q.fiscal_date)) > ESTIMATE_MATCH_DAYS for q in eps)),
                   key=lambda r: r["fiscal_date"])
    return cands[0] if cands else None


def _upcoming(earnings, next_q):
    if next_q is None:
        return None
    near = [r for r in earnings if r.get("eps_actual") is None and r.get("fiscal_date")
            and abs(_days(r["fiscal_date"], next_q["fiscal_date"])) <= ESTIMATE_MATCH_DAYS]
    return min(near, key=lambda r: abs(_days(r["fiscal_date"], next_q["fiscal_date"]))) if near else None


def _fy_pair(snap_rows, eps: Tuple[EpsQuarter, ...]):
    if not eps:
        return None
    anchor = eps[-1].fiscal_date
    fy = sorted((r for r in snap_rows if r.get("period_type") == "FY"), key=lambda r: r["fiscal_date"])
    fy1 = next((r for r in fy if _days(r["fiscal_date"], anchor) > ESTIMATE_MATCH_DAYS), None)
    if fy1 is None:
        return None
    fy2 = next((r for r in fy if abs(_days(r["fiscal_date"], fy1["fiscal_date"]) - FY_STEP_DAYS)
                <= FY_STEP_TOLERANCE), None)
    return (fy1, fy2) if fy2 is not None else None


def _yf_0q(store: MarketStore, symbol: str, as_of: str):
    for row in store.get_latest_forward_estimates(symbol):
        if row.get("period") == "0q" and row["date"][:10] <= as_of \
                and _days(as_of, row["date"]) <= YF_MAX_AGE_DAYS:
            return row
    return None


def load_facts(store: MarketStore, symbol: str, as_of: str) -> FactPack:
    if store.read_only is not True:
        raise ValueError("valuation requires MarketStore(read_only=True)")
    history = load_history(store, symbol, with_vintage=False)
    cutoff = datetime.fromisoformat(as_of + "T23:59:59+00:00")
    estimates = [r for r in history.estimates if r["snapshot_date"][:10] <= as_of]
    eps = pair_statement_dates(announced_eps(history.earnings, history.income, history.splits, as_of),
                               [r["date"] for r in history.income])
    ttm, ttm_q = ttm_eps([q for q in eps if q.announce_date <= as_of])
    ntm = ntm_eps(estimates, eps, as_of, cutoff=cutoff)
    weights = _weights(ntm)
    live = ntm.snapshot_date if ntm.missing_reason not in ("snapshot_stale", "no_current_snapshot") else None
    snap_rows = [r for r in estimates if live and r["snapshot_date"][:10] == live]
    next_q = _next_q(snap_rows, eps)
    closes, notes = _closes(history, as_of)
    price_date = closes[-1][0] if closes else None
    price = closes[-1][1] if closes and _days(as_of, price_date) <= PRICE_MAX_STALENESS_DAYS else None
    income = tuple(r for r in history.income if r["date"][:10] <= as_of)
    return FactPack(
        symbol=symbol, as_of=as_of,
        reporting_currency=income[-1].get("reported_currency") if income else None,
        is_adr=history.is_adr, price=price, price_date=price_date, closes=closes, price_jump_notes=notes,
        eps=eps, ttm_eps_usd=ttm, ttm_quarters=ttm_q, ntm=ntm, ntm_weights=weights,
        ntm_sums=_sums(snap_rows, ntm, weights), next_q=next_q,
        upcoming=_upcoming(history.earnings, next_q), yf_0q=_yf_0q(store, symbol, as_of),
        fy_pair=_fy_pair(snap_rows, eps), income=income, earnings=tuple(history.earnings),
        shares=fin(income[-1].get("weighted_average_shs_out_dil")) if income else None,
    )
