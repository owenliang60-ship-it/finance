"""Five-year price + quarterly revenue / net income QoQ + next-4Q consensus.

Deterministic Phase 0 step of the deep-analysis pipeline. It reads market.db
read-only (``mode=ro``), fills gaps from live FMP only when the database lacks
them (raw responses are saved under ``research_dir/chart_sources``), freezes
the dataset to ``financial_history.{json,csv,md}`` + a manifest, and renders
``price_fundamentals_5y_4q.png``. Later phases read these files; nothing here
writes market.db.

Definitions follow docs/handoffs/2026-09-28-company-financial-charts §4–§5:
fiscal identity comes from fiscal_year/period, forecasts are the four
consecutive quarters after the latest reported quarter from one snapshot,
net income QoQ is n/a across basis changes, and price returns are
prior-quarter-end close → quarter-end close (price only, no dividends).
"""
from __future__ import annotations

import csv
import hashlib
import json
import logging
import math
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

import pandas as pd

logger = logging.getLogger(__name__)

PNG_NAME = "price_fundamentals_5y_4q.png"
JSON_NAME = "financial_history.json"
CSV_NAME = "financial_history.csv"
MD_NAME = "financial_history.md"
MANIFEST_NAME = "financial_history_manifest.json"
SOURCES_DIR = "chart_sources"

SAME_QUARTER_DAYS = 10          # vendor fiscal-date drift (CRDO 8/1 vs 8/2, 52/53-week)
ADJACENT_MIN_DAYS, ADJACENT_MAX_DAYS = 60, 120
SNAPSHOT_STALE_DAYS = 21
PRICE_STALE_DAYS = 5
REVENUE_XCHECK_TOL = 0.03       # income_quarterly vs fmp_earnings revenue
BANK_MISMATCH_TOL = 0.15        # median gap that marks gross-vs-net revenue
ESTIMATE_SCALE_RANGE = (0.25, 4.0)
SPLIT_RATIOS = (2, 3, 4, 5, 8, 10, 15, 20)

NA_REASONS = {
    "no_base": "无基期",
    "gap": "缺季",
    "missing": "数据缺失",
    "zero_base": "基期为0",
    "nonpositive_base": "基期≤0",
    "basis_break": "口径切换",
}

REVENUE_BASIS_LABELS = {
    "reported": "FMP 报告营收（GAAP 利润表 revenue）",
    "street_net": "街口径净营收（fmp_earnings.revenue_actual，与共识同口径）",
    "consensus": "分析师营收共识",
}
NET_INCOME_BASIS_LABELS = {
    "gaap": "GAAP 净利润（FMP net_income，归属口径未逐项核实）",
    "unknown": "分析师净利润共识（GAAP/调整后口径未核实）",
}


class FinancialHistoryError(RuntimeError):
    """Raised when the source database cannot be read at all."""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _num(value: Any) -> Optional[float]:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _day(value: Any) -> Optional[date]:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _next_fiscal(fiscal_year: Optional[str], fiscal_quarter: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    if not fiscal_year or not fiscal_quarter or not str(fiscal_quarter).startswith("Q"):
        return None, None
    try:
        fy, q = int(fiscal_year), int(str(fiscal_quarter)[1:])
    except ValueError:
        return None, None
    return (str(fy + 1), "Q1") if q >= 4 else (str(fy), f"Q{q + 1}")


def _adjacent(prev_end: date, cur_end: date) -> bool:
    return ADJACENT_MIN_DAYS <= (cur_end - prev_end).days <= ADJACENT_MAX_DAYS


def format_money(value: Optional[float], currency: Optional[str] = "USD") -> str:
    """Shared by financial_history.md and the PNG so both read identically."""
    if value is None:
        return "—"
    sym = "$" if currency in (None, "USD") else ""
    sign = "-" if value < 0 else ""
    v = abs(value)
    if v >= 1e9:
        return f"{sign}{sym}{v / 1e9:,.2f}B"
    if v >= 1e6:
        return f"{sign}{sym}{v / 1e6:,.1f}M"
    return f"{sign}{sym}{v:,.0f}"


_money = format_money


def _pct(value: Optional[float]) -> str:
    return "—" if value is None else f"{value:+.1f}%"


# ---------------------------------------------------------------------------
# Pure calculations (unit-tested with hand-computed expectations)
# ---------------------------------------------------------------------------

def income_change(prev: Optional[float], cur: Optional[float]) -> Tuple[str, Optional[float]]:
    """Classify a net income change between two comparable adjacent quarters.

    Returns (change_type, pct). ``growth`` pct is the plotted QoQ; loss
    narrowed/widened pct is based on the absolute loss and is not a QoQ bar.
    """
    if prev is None or cur is None:
        return "missing", None
    if prev == 0:
        return "zero_base", None
    if prev > 0 and cur >= 0:
        return "growth", (cur / prev - 1.0) * 100.0
    if prev > 0 > cur:
        return "turned_loss", None
    if prev < 0 < cur:
        return "turned_profit", None
    if prev < 0 and cur == 0:
        return "breakeven", None
    delta = (abs(cur) / abs(prev) - 1.0) * 100.0          # prev < 0, cur <= 0
    return ("loss_widened" if delta > 0 else "loss_narrowed"), delta


def income_change_label(change_type: str, pct: Optional[float]) -> str:
    if change_type == "growth":
        return f"{pct:+.1f}%"
    if change_type == "loss_narrowed":
        return f"亏损收窄{abs(pct):.0f}%"
    if change_type == "loss_widened":
        return f"亏损扩大{pct:.0f}%"
    if change_type == "turned_profit":
        return "扭亏"
    if change_type == "turned_loss":
        return "转亏"
    if change_type == "breakeven":
        return "亏损归零"
    return "n/a " + NA_REASONS.get(change_type, change_type)


def _revenue_comparable(prev_basis: str, cur_basis: str) -> bool:
    return prev_basis == cur_basis or {prev_basis, cur_basis} in (
        {"reported", "consensus"}, {"street_net", "consensus"})


def _income_comparable(prev: Dict[str, Any], cur: Dict[str, Any]) -> bool:
    if prev["net_income_basis"] != cur["net_income_basis"]:
        return False
    # An unverified basis is only self-consistent inside one forecast snapshot.
    return prev["net_income_basis"] != "unknown" or (
        prev["kind"] == cur["kind"] == "estimate")


def compute_changes(periods: List[Dict[str, Any]]) -> None:
    """Fill revenue/net income QoQ fields in place on a fiscal-ordered list."""
    prev = None
    for row in periods:
        row.update(revenue_qoq=None, revenue_na_reason=None, net_income_qoq=None,
                   income_change_type=None, income_change_pct=None,
                   net_income_na_reason=None)
        if prev is None:
            common = "no_base"
        elif not _adjacent(_day(prev["period_end"]), _day(row["period_end"])):
            common = "gap"
        else:
            common = None

        # revenue
        if common:
            row["revenue_na_reason"] = common
        elif prev["revenue"] is None or row["revenue"] is None:
            row["revenue_na_reason"] = "missing"
        elif not _revenue_comparable(prev["revenue_basis"], row["revenue_basis"]):
            row["revenue_na_reason"] = "basis_break"
        elif prev["revenue"] <= 0:
            row["revenue_na_reason"] = "nonpositive_base"
        else:
            row["revenue_qoq"] = (row["revenue"] / prev["revenue"] - 1.0) * 100.0

        # net income
        if common:
            reason = common
        elif prev["net_income"] is None or row["net_income"] is None:
            reason = "missing"
        elif not _income_comparable(prev, row):
            reason = "basis_break"
        else:
            reason = None
        if reason:
            row["income_change_type"] = reason
            row["net_income_na_reason"] = reason
        else:
            kind, pct = income_change(prev["net_income"], row["net_income"])
            row["income_change_type"] = kind
            row["income_change_pct"] = pct
            if kind == "growth":
                row["net_income_qoq"] = pct
            elif kind in NA_REASONS:
                row["net_income_na_reason"] = kind
        row["income_change_label"] = income_change_label(
            row["income_change_type"], row["income_change_pct"])
        prev = row


def assign_display_quarters(period_ends: Sequence[date]) -> List[Tuple[pd.Period, bool]]:
    """Calendar quarter containing each fiscal period end.

    A period ending in the first 7 days of a calendar quarter (52/53-week
    years such as Oct-03 / Jan-02) is moved back one quarter only when doing so
    makes the whole sequence more continuous; each move is flagged.
    """
    ends = [pd.Timestamp(e) for e in period_ends]
    raw = [e.to_period("Q") for e in ends]
    early = [(e - p.start_time).days < 7 for e, p in zip(ends, raw)]
    if not any(early):
        return [(p, False) for p in raw]
    shifted, prev = [], None
    for p, is_early in zip(raw, early):
        cand = p - 1 if is_early and (prev is None or (p - 1).ordinal > prev.ordinal) else p
        shifted.append(cand)
        prev = cand

    def breaks(seq: List[pd.Period]) -> int:
        return sum(1 for a, b in zip(seq, seq[1:]) if b.ordinal - a.ordinal != 1)

    if breaks(shifted) < breaks(raw):
        return [(s, s != p) for s, p in zip(shifted, raw)]
    return [(p, False) for p in raw]


def select_future_estimates(
    latest_actual_end: date,
    actual_ends: Sequence[date],
    estimate_rows: Sequence[Dict[str, Any]],
    n: int,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Pick the next ``n`` consecutive unreported fiscal quarters.

    Rows within SAME_QUARTER_DAYS of any reported period end are the same
    fiscal quarter (already reported) and are skipped, so a 6/30 estimate
    never duplicates a 6/29 actual.
    """
    notes: List[str] = []
    candidates = []
    for row in sorted(estimate_rows, key=lambda r: r["fiscal_date"]):
        fd = _day(row["fiscal_date"])
        if fd is None:
            continue
        if any(abs((fd - a).days) <= SAME_QUARTER_DAYS for a in actual_ends):
            continue
        if fd <= latest_actual_end:
            continue
        candidates.append(row)
    selected: List[Dict[str, Any]] = []
    prev_end = latest_actual_end
    for row in candidates:
        if len(selected) >= n:
            break
        fd = _day(row["fiscal_date"])
        if not _adjacent(prev_end, fd):
            notes.append(
                f"预测序列在 {prev_end.isoformat()} 之后不连续（下一条 {fd.isoformat()}），只保留连续部分")
            break
        selected.append(row)
        prev_end = fd
    return selected, notes


def quarterly_price_returns(
    prices: Sequence[Tuple[date, float]],
    first_quarter: pd.Period,
    as_of: date,
) -> List[Dict[str, Any]]:
    """Price return per calendar quarter: prior quarter-end close → quarter-end close.

    Quarter with no prior-quarter close (IPO, window start without earlier
    data, halt) uses its first close and is flagged ``partial``; the quarter
    containing ``as_of`` is ``QTD`` unless as_of is its last calendar day.
    """
    by_q: Dict[pd.Period, List[Tuple[date, float]]] = {}
    for d, c in prices:
        by_q.setdefault(pd.Timestamp(d).to_period("Q"), []).append((d, c))
    for rows in by_q.values():
        rows.sort()
    last_q = pd.Timestamp(as_of).to_period("Q")
    out = []
    q = first_quarter
    while q <= last_q:
        rows = by_q.get(q)
        if rows:
            flags = []
            prior = by_q.get(q - 1)
            if prior:
                base_date, base = prior[-1]
            else:
                base_date, base = rows[0]
                flags.append("partial")
            end_date, end = rows[-1]
            q_close = q.end_time.date()
            while q_close.weekday() >= 5:
                q_close -= timedelta(days=1)
            if q == last_q and (as_of < q.end_time.date() or end_date < q_close):
                flags.append("QTD")
            out.append({
                "quarter": str(q),
                "base_date": base_date.isoformat(),
                "base_close": base,
                "end_date": end_date.isoformat(),
                "end_close": end,
                "return_pct": (end / base - 1.0) * 100.0 if base else None,
                "flags": flags,
            })
        q += 1
    return out


def apply_recorded_splits(
    prices: Sequence[Tuple[date, float]],
    splits: Sequence[Dict[str, Any]],
) -> Tuple[List[Tuple[date, float]], List[str]]:
    """Back-adjust prices for vendor-recorded splits the series did not absorb.

    A split is treated as unadjusted only when the close on/after the split
    date jumps by roughly the split factor (KLAC 10:1 → -90%); an already
    adjusted series is left untouched.
    """
    prices = list(prices)
    notes = []
    for s in sorted(splits, key=lambda s: s["date"], reverse=True):
        num, den, sd = _num(s.get("numerator")), _num(s.get("denominator")), _day(s.get("date"))
        if not num or not den or sd is None or num == den:
            continue
        factor = num / den
        i = next((k for k, (d, _) in enumerate(prices) if d >= sd), None)
        if not i:
            continue
        ratio = prices[i][1] / prices[i - 1][1]
        if abs(ratio * factor - 1) < 0.25:
            prices = [(d, c / factor) for d, c in prices[:i]] + prices[i:]
            notes.append(f"库内股价未回溯 {sd.isoformat()} {num:g}:{den:g} 拆股（fmp_stock_splits），"
                         "图表已对拆股前价格做复权")
    return prices, notes


def detect_split_like_jumps(prices: Sequence[Tuple[date, float]]) -> List[str]:
    """Unexplained single-day moves that look like an unadjusted split."""
    notes = []
    for (d0, c0), (d1, c1) in zip(prices, prices[1:]):
        if not c0 or not c1:
            continue
        r = c1 / c0
        if 0.55 <= r <= 1.8:
            continue
        for k in SPLIT_RATIOS:
            if abs(r * k - 1) < 0.03 or abs(r / k - 1) < 0.03:
                kind = f"{k}:1 拆股" if r < 1 else f"1:{k} 合股"
                notes.append(
                    f"{d0.isoformat()}→{d1.isoformat()} 单日收盘 {c0:,.2f}→{c1:,.2f}，"
                    f"疑似未复权{kind}；跨该日的季度价格收益不可信")
                break
    return notes


# ---------------------------------------------------------------------------
# Source loading
# ---------------------------------------------------------------------------

def _connect_ro(db_path: Path) -> sqlite3.Connection:
    if not Path(db_path).is_file() or Path(db_path).stat().st_size == 0:
        raise FinancialHistoryError(f"market.db 不存在或为空壳: {db_path}")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _rows(conn: sqlite3.Connection, sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _actual_from_income(row: Dict[str, Any], source: str) -> Optional[Dict[str, Any]]:
    end = _day(row.get("date"))
    if end is None:
        return None
    return {
        "kind": "actual",
        "fiscal_year": str(row.get("fiscal_year") or "") or None,
        "fiscal_quarter": row.get("period"),
        "period_end": end.isoformat(),
        "filing_date": (str(row.get("filing_date"))[:10] if row.get("filing_date") else None),
        "accepted_date": row.get("accepted_date"),
        "currency": row.get("reported_currency"),
        "gross_revenue": _num(row.get("revenue")),
        "net_income": _num(row.get("net_income")),
        "eps": _num(row.get("eps_diluted")) if row.get("eps_diluted") is not None else _num(row.get("eps")),
        "revenue_source": source,
        "net_income_source": source,
        "net_income_basis": "gaap",
    }


def _live_income_to_rows(raw: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    mapped = []
    for r in raw:
        mapped.append({
            "date": r.get("date"), "fiscal_year": r.get("fiscalYear") or r.get("calendarYear"),
            "period": r.get("period"), "filing_date": r.get("filingDate") or r.get("fillingDate"),
            "accepted_date": r.get("acceptedDate"), "reported_currency": r.get("reportedCurrency"),
            "revenue": r.get("revenue"), "net_income": r.get("netIncome"),
            "eps": r.get("eps"), "eps_diluted": r.get("epsDiluted") or r.get("epsdiluted"),
        })
    return mapped


def _live_estimates_to_rows(raw: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = []
    for r in raw:
        fd = _day(r.get("date"))
        if fd is None:
            continue
        rows.append({
            "fiscal_date": fd.isoformat(),
            "rev_avg": _num(r.get("revenueAvg")), "rev_low": _num(r.get("revenueLow")),
            "rev_high": _num(r.get("revenueHigh")),
            "net_income_avg": _num(r.get("netIncomeAvg")),
            "eps_avg": _num(r.get("epsAvg")), "eps_low": _num(r.get("epsLow")),
            "eps_high": _num(r.get("epsHigh")),
            "num_analysts_rev": r.get("numAnalystsRevenue"),
            "num_analysts_eps": r.get("numAnalystsEps"),
        })
    return rows


class _Live:
    """Lazy live-FMP access; every raw response is saved for audit."""

    def __init__(self, symbol: str, sources_dir: Path, client: Any, enabled: bool):
        self.symbol = symbol
        self.sources_dir = sources_dir
        self._client = client
        self.enabled = enabled
        self.calls: List[Dict[str, Any]] = []

    def _get_client(self):
        if self._client is None:
            from src.data.fmp_client import fmp_client
            self._client = fmp_client
        return self._client

    def call(self, name: str, method: str, **kwargs) -> Optional[List[Dict[str, Any]]]:
        if not self.enabled:
            return None
        retrieved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            data = getattr(self._get_client(), method)(self.symbol, **kwargs)
        except Exception as exc:  # network / auth failures are reported, not fatal
            self.calls.append({"name": name, "method": method, "params": kwargs,
                               "retrieved_at": retrieved_at, "error": str(exc)[:300]})
            return None
        data = data if isinstance(data, list) else []
        self.sources_dir.mkdir(parents=True, exist_ok=True)
        path = self.sources_dir / f"fmp_{name}.json"
        path.write_text(json.dumps({"symbol": self.symbol, "method": method, "params": kwargs,
                                    "retrieved_at": retrieved_at, "response": data},
                                   ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        self.calls.append({"name": name, "method": method, "params": kwargs,
                           "retrieved_at": retrieved_at, "rows": len(data),
                           "path": f"{SOURCES_DIR}/{path.name}"})
        return data


def _select_snapshot(conn, symbol: str, as_of: date) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]], List[str]]:
    notes = []
    runs = _rows(conn, """
        SELECT snapshot_date, status, completed_at, target_count, quarter_success,
               target_universe_json
        FROM fmp_forward_runs
        WHERE run_kind = 'weekly' AND status = 'complete' AND snapshot_date <= ?
        ORDER BY snapshot_date DESC LIMIT 6""", (as_of.isoformat(),))
    for i, run in enumerate(runs):
        rows = _rows(conn, """
            SELECT fiscal_date, eps_avg, eps_low, eps_high, rev_avg, rev_low, rev_high,
                   net_income_avg, num_analysts_rev, num_analysts_eps
            FROM fmp_estimates
            WHERE symbol = ? AND snapshot_date = ? AND period_type = 'Q'
              AND snapshot_kind = 'weekly'
            ORDER BY fiscal_date""", (symbol, run["snapshot_date"]))
        if not rows:
            continue
        try:
            in_target = symbol in set(json.loads(run.pop("target_universe_json") or "[]"))
        except (TypeError, ValueError):
            in_target = None
        if i > 0:
            notes.append(f"最新完整周频快照 {runs[0]['snapshot_date']} 无本公司季度预测，改用 {run['snapshot_date']}")
        meta = {"source": "market.db:fmp_estimates", "snapshot_kind": "weekly",
                "snapshot_date": run["snapshot_date"], "run_status": run["status"],
                "run_completed_at": run["completed_at"], "symbol_in_run_target": in_target}
        return meta, rows, notes
    return None, [], notes


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------

def _is_bank(industry: Optional[str]) -> bool:
    text = (industry or "").lower()
    return "bank" in text or "capital markets" in text


def build_financial_history(
    symbol: str,
    *,
    as_of: date,
    db_path: Path,
    years: int = 5,
    forecast_quarters: int = 4,
    industry: Optional[str] = None,
    live: Optional[_Live] = None,
) -> Dict[str, Any]:
    symbol = symbol.upper()
    live = live or _Live(symbol, Path("."), None, enabled=False)
    warnings: List[str] = []
    gaps: List[str] = []
    window_start_q = pd.Timestamp(as_of).to_period("Q") - (4 * years - 1)
    base_q = window_start_q - 1

    with closing(_connect_ro(Path(db_path))) as conn:
        income_db = _rows(conn, """
            SELECT date, fiscal_year, period, filing_date, accepted_date, reported_currency,
                   revenue, net_income, eps, eps_diluted
            FROM income_quarterly WHERE symbol = ? ORDER BY date""", (symbol,))
        earnings = _rows(conn, """
            SELECT announce_date, fiscal_date, match_method, eps_actual, eps_estimated,
                   revenue_actual, revenue_estimated, last_updated
            FROM fmp_earnings WHERE symbol = ? AND announce_date <= ?
            ORDER BY announce_date""", (symbol, as_of.isoformat()))
        price_start = base_q.start_time.date()
        prices_db = _rows(conn, """
            SELECT date, close FROM daily_price
            WHERE symbol = ? AND date BETWEEN ? AND ? AND close IS NOT NULL
            ORDER BY date""", (symbol, price_start.isoformat(), as_of.isoformat()))
        snapshot, estimate_rows, snap_notes = _select_snapshot(conn, symbol, as_of)
        try:
            splits = _rows(conn, """
                SELECT date, numerator, denominator FROM fmp_stock_splits
                WHERE symbol = ? AND date BETWEEN ? AND ?""",
                (symbol, price_start.isoformat(), as_of.isoformat()))
        except sqlite3.OperationalError:
            splits = []
    warnings.extend(snap_notes)
    sources: List[Dict[str, Any]] = [
        {"name": "income_quarterly", "source": "market.db (mode=ro)", "rows": len(income_db)},
        {"name": "fmp_earnings", "source": "market.db (mode=ro)", "rows": len(earnings)},
        {"name": "daily_price", "source": "market.db (mode=ro)", "rows": len(prices_db)},
    ]

    # --- actual quarters: DB first, live fills only missing quarters ------------
    actuals = [a for a in (_actual_from_income(r, "market.db:income_quarterly") for r in income_db) if a]

    def _announced(a: Dict[str, Any]) -> Optional[date]:
        """Earliest public date: earnings release (fmp_earnings) or the filing."""
        end = _day(a["period_end"])
        dates = [_day(a.get("filing_date")) or _day(a.get("accepted_date"))]
        dates += [_day(e["announce_date"]) for e in earnings if _day(e["fiscal_date"])
                  and abs((_day(e["fiscal_date"]) - end).days) <= SAME_QUARTER_DAYS]
        dates = [d for d in dates if d]
        return min(dates) if dates else None

    actuals = [a for a in actuals if not _announced(a) or _announced(a) <= as_of]
    reported_ends = [_day(a["period_end"]) for a in actuals]

    # market.db earnings/income refresh weekly: a quarter that ended before
    # as_of but is not in the DB may have been reported this week.
    last_end = max(reported_ends) if reported_ends else None
    ended_unseen = [r["fiscal_date"] for r in estimate_rows
                    if last_end and _day(r["fiscal_date"])
                    and last_end + timedelta(days=SAME_QUARTER_DAYS) < _day(r["fiscal_date"]) < as_of]
    ended_verified = False
    if ended_unseen:
        from src.data.fmp_forward_ingestion import match_fiscal_date
        raw = live.call("earnings", "get_earnings", limit=12)
        if raw is not None:
            ended_verified = True
            pool = sorted({r["fiscal_date"] for r in estimate_rows} | {a["period_end"] for a in actuals})
            seen = {e["announce_date"] for e in earnings}
            for r in raw:
                ann = _day(r.get("date"))
                if (ann is None or ann > as_of or ann.isoformat() in seen
                        or (r.get("epsActual") is None and r.get("revenueActual") is None)):
                    continue
                earnings.append({
                    "announce_date": ann.isoformat(),
                    "fiscal_date": match_fiscal_date(ann.isoformat(), pool),
                    "match_method": "fmp_live:earnings", "eps_actual": _num(r.get("epsActual")),
                    "eps_estimated": _num(r.get("epsEstimated")),
                    "revenue_actual": _num(r.get("revenueActual")),
                    "revenue_estimated": _num(r.get("revenueEstimated")), "last_updated": None,
                })
            earnings.sort(key=lambda e: e["announce_date"])
    announced_missing = [
        e for e in earnings
        if _day(e["fiscal_date"]) and (e["eps_actual"] is not None or e["revenue_actual"] is not None)
        and not any(abs((_day(e["fiscal_date"]) - r).days) <= SAME_QUARTER_DAYS for r in reported_ends)
        and (not reported_ends or _day(e["fiscal_date"]) > max(reported_ends))
    ]
    history_short = not reported_ends or pd.Timestamp(min(reported_ends)).to_period("Q") > base_q
    if announced_missing or history_short:
        raw = live.call("income_statement", "get_income_statement", period="quarter",
                        limit=4 * years + 8)
        if raw:
            added = 0
            for r in _live_income_to_rows(raw):
                a = _actual_from_income(r, "fmp_live:income-statement")
                if a is None or (_announced(a) and _announced(a) > as_of):
                    continue
                end = _day(a["period_end"])
                if end < (base_q - 1).start_time.date():
                    continue
                match = next((x for x in actuals
                              if abs((_day(x["period_end"]) - end).days) <= SAME_QUARTER_DAYS), None)
                if match is None:
                    actuals.append(a)
                    added += 1
                elif match["gross_revenue"] and a["gross_revenue"] and abs(
                        a["gross_revenue"] / match["gross_revenue"] - 1) > 0.005:
                    warnings.append(
                        f"实时利润表与库内 {match['period_end']} 营收不一致"
                        f"（库 {_money(match['gross_revenue'])} vs 实时 {_money(a['gross_revenue'])}），"
                        "可能是重述；图表沿用库内值")
            if added:
                warnings.append(f"库内缺 {added} 个已发布季度，已用实时 FMP 利润表补入（见 chart_sources）")
    actuals.sort(key=lambda a: a["period_end"])

    # duplicate fiscal labels / near-duplicate period ends: keep latest filing, report the rest
    deduped: List[Dict[str, Any]] = []
    for a in actuals:
        dup = next((d for d in deduped
                    if abs((_day(d["period_end"]) - _day(a["period_end"])).days) <= SAME_QUARTER_DAYS
                    or (a["fiscal_year"] and d["fiscal_year"] == a["fiscal_year"]
                        and d["fiscal_quarter"] == a["fiscal_quarter"])), None)
        if dup is None:
            deduped.append(a)
            continue
        keep = max((dup, a), key=lambda x: (x.get("accepted_date") or x.get("filing_date") or "", x["period_end"]))
        drop = a if keep is dup else dup
        warnings.append(
            f"同一财季多条记录：FY{drop['fiscal_year']} {drop['fiscal_quarter']} 期末 {drop['period_end']} "
            f"与 {keep['period_end']} 冲突，采用披露较新的 {keep['period_end']}，需人工核实版本")
        deduped[deduped.index(dup)] = keep
    actuals = sorted(deduped, key=lambda a: a["period_end"])

    # --- revenue basis: banks and gross/net mixing use street net revenue -------
    def _earning_for(end: date) -> Optional[Dict[str, Any]]:
        cands = [e for e in earnings if _day(e["fiscal_date"])
                 and abs((_day(e["fiscal_date"]) - end).days) <= SAME_QUARTER_DAYS]
        return cands[-1] if cands else None

    gaps_vs_street = []
    for a in actuals[-8:]:
        e = _earning_for(_day(a["period_end"]))
        if e and e["revenue_actual"] and a["gross_revenue"]:
            gaps_vs_street.append(abs(a["gross_revenue"] / e["revenue_actual"] - 1))
    median_gap = sorted(gaps_vs_street)[len(gaps_vs_street) // 2] if gaps_vs_street else 0.0
    street_mode = _is_bank(industry) or median_gap > BANK_MISMATCH_TOL
    if street_mode:
        warnings.append(
            "营收采用街口径净营收（fmp_earnings.revenue_actual，与共识同口径）："
            + ("行业为银行/资本市场" if _is_bank(industry) else f"利润表营收与街口径中位偏差 {median_gap:.0%}")
            + "；FMP 利润表 revenue 对银行可能混有毛收入，不做机械减利息支出")
    xcheck_bad = []
    for a in actuals:
        end = _day(a["period_end"])
        e = _earning_for(end)
        a["announced_at"] = (e["announce_date"] if e else None) or a.get("filing_date")
        a["street_revenue"] = _num(e["revenue_actual"]) if e else None
        if street_mode:
            a["revenue"] = a["street_revenue"]
            a["revenue_basis"] = "street_net"
            a["revenue_source"] = "market.db:fmp_earnings.revenue_actual"
        else:
            a["revenue"] = a["gross_revenue"]
            a["revenue_basis"] = "reported"
            if (end >= base_q.start_time.date() and a["street_revenue"] and a["revenue"]
                    and abs(a["revenue"] / a["street_revenue"] - 1) > REVENUE_XCHECK_TOL):
                xcheck_bad.append(a["period_end"])

    # announced quarters still missing after the live attempt: keep as explicit gaps
    reported_ends = [_day(a["period_end"]) for a in actuals]
    for e in announced_missing:
        fd = _day(e["fiscal_date"])
        if any(abs((fd - r).days) <= SAME_QUARTER_DAYS for r in reported_ends):
            continue
        fy, fq = _next_fiscal(actuals[-1]["fiscal_year"], actuals[-1]["fiscal_quarter"]) if actuals else (None, None)
        actuals.append({
            "kind": "actual", "fiscal_year": fy, "fiscal_quarter": fq,
            "period_end": fd.isoformat(), "filing_date": None, "accepted_date": None,
            "announced_at": e["announce_date"], "currency": actuals[-1]["currency"] if actuals else None,
            "gross_revenue": None, "street_revenue": _num(e["revenue_actual"]),
            "revenue": _num(e["revenue_actual"]),
            "revenue_basis": "street_net", "revenue_source": "market.db:fmp_earnings.revenue_actual",
            "net_income": None, "net_income_source": None, "net_income_basis": "gaap",
            "eps": None, "street_eps": _num(e["eps_actual"]),
        })
        reported_ends.append(fd)
        gaps.append(f"{fd.isoformat()} 季已于 {e['announce_date']} 公布，但利润表未入库且实时补齐失败；"
                     "该季只有街口径营收，净利润缺失")
    actuals.sort(key=lambda a: a["period_end"])
    if xcheck_bad:
        warnings.append(
            f"{len(xcheck_bad)} 个季度利润表营收与 fmp_earnings 实际营收相差 >{REVENUE_XCHECK_TOL:.0%}"
            f"（如 {', '.join(xcheck_bad[-3:])}），可能是口径或重述差异")

    currency = next((a["currency"] for a in reversed(actuals) if a.get("currency")), None)
    if currency and currency != "USD":
        warnings.append(f"财务币种为 {currency}，股价为美元计价；两者分别展示，不做换算")
    if len({a.get("currency") for a in actuals if a.get("currency")}) > 1:
        gaps.append("历史季度财务币种不一致，跨币种 QoQ 不可信")

    # --- forecasts ---------------------------------------------------------------
    latest = actuals[-1] if actuals else None
    estimates: List[Dict[str, Any]] = []
    snapshot_meta = snapshot
    if latest:
        latest_end = _day(latest["period_end"])
        selected, sel_notes = select_future_estimates(
            latest_end, reported_ends, estimate_rows, forecast_quarters)
        if len(selected) < forecast_quarters:
            raw = live.call("analyst_estimates", "get_analyst_estimates", period="quarter", limit=40)
            live_rows = _live_estimates_to_rows(raw or [])
            live_sel, live_notes = select_future_estimates(
                latest_end, reported_ends, live_rows, forecast_quarters)
            if len(live_sel) > len(selected):
                call = live.calls[-1]
                if snapshot:
                    warnings.append(
                        f"周频快照 {snapshot['snapshot_date']} 只覆盖 {len(selected)} 个未来季，"
                        f"四季整体改用实时 FMP 预期（检索于 {call['retrieved_at']}）")
                selected, sel_notes = live_sel, live_notes
                snapshot_meta = {"source": "fmp_live:analyst-estimates", "snapshot_kind": "live",
                                 "snapshot_date": call["retrieved_at"][:10],
                                 "retrieved_at": call["retrieved_at"], "path": call.get("path")}
        warnings.extend(sel_notes)
        fy, fq = latest["fiscal_year"], latest["fiscal_quarter"]
        for r in selected:
            fy, fq = _next_fiscal(fy, fq)
            estimates.append({
                "kind": "estimate", "fiscal_year": fy, "fiscal_quarter": fq,
                "period_end": r["fiscal_date"], "announced_at": None,
                "currency": currency,
                "revenue": _num(r["rev_avg"]), "revenue_low": _num(r.get("rev_low")),
                "revenue_high": _num(r.get("rev_high")),
                "revenue_basis": "consensus",
                "net_income": _num(r["net_income_avg"]), "net_income_basis": "unknown",
                "eps": _num(r["eps_avg"]), "eps_low": _num(r.get("eps_low")),
                "eps_high": _num(r.get("eps_high")),
                "num_analysts_rev": r.get("num_analysts_rev"),
                "num_analysts_eps": r.get("num_analysts_eps"),
                "revenue_source": (snapshot_meta or {}).get("source"),
                "net_income_source": (snapshot_meta or {}).get("source"),
                "period_ended_unreported": _day(r["fiscal_date"]) < as_of,
            })
        first_rev = next((e["revenue"] for e in estimates if e["revenue"] is not None), None)
        if first_rev is not None and latest.get("revenue"):
            ratio = first_rev / latest["revenue"]
            if not ESTIMATE_SCALE_RANGE[0] <= ratio <= ESTIMATE_SCALE_RANGE[1]:
                gaps.append(
                    f"预测营收是最新实际的 {ratio:.2f} 倍，疑似币种/单位不一致，预测已剔除")
                estimates = []
        if len(estimates) < forecast_quarters:
            gaps.append(f"未来预测只有 {len(estimates)}/{forecast_quarters} 季，未用年度数外推补齐")
        if snapshot_meta and latest.get("announced_at") and snapshot_meta["snapshot_date"] < str(latest["announced_at"])[:10]:
            warnings.append(
                f"预测快照 {snapshot_meta['snapshot_date']} 早于最新财报发布 {latest['announced_at']}，"
                "属财报前共识；首个预测季增长是新实际 vs 旧预测的比较")
        if snapshot_meta and snapshot_meta["snapshot_kind"] == "weekly":
            age = (as_of - _day(snapshot_meta["snapshot_date"])).days
            if age > SNAPSHOT_STALE_DAYS:
                warnings.append(f"预测快照已 {age} 天未更新")
        if any(e["period_ended_unreported"] for e in estimates):
            if ended_verified:
                warnings.append("首个预测季已过期末，实时 FMP 财报日历显示尚未发布，仍按共识预测展示")
            else:
                gaps.append("首个预测季已过期末，库内未见其财报且实时核实未执行/失败；"
                            "若公司本周已发布，图中该季仍是财报前共识")
        if street_mode and estimates:
            warnings.append("银行/券商营收预测为街口径净营收共识，与历史净营收同口径")

    # --- ordering, display quarters, QoQ ---------------------------------------
    periods = actuals + estimates
    periods.sort(key=lambda p: p["period_end"])
    for prev, cur in zip(periods, periods[1:]):
        if _adjacent(_day(prev["period_end"]), _day(cur["period_end"])):
            cur["period_start"] = (_day(prev["period_end"]) + timedelta(days=1)).isoformat()
    for (disp, shifted), p in zip(assign_display_quarters([_day(p["period_end"]) for p in periods]), periods):
        p["display_quarter"] = str(disp)
        p["display_shifted"] = shifted
        p.setdefault("period_start", None)
    compute_changes(periods)

    first_in_window = next((i for i, p in enumerate(periods)
                            if pd.Period(p["display_quarter"], "Q") >= window_start_q), None)
    if first_in_window is None:
        visible = []
    else:
        visible = periods[max(first_in_window - 1, 0):]
        if first_in_window > 0:
            visible[0]["role"] = "qoq_base"
    for p in visible:
        p.setdefault("role", "window")
    shown_actuals = [p for p in visible if p["kind"] == "actual" and p["role"] == "window"]
    displays = [pd.Period(p["display_quarter"], "Q") for p in visible]
    if len(set(displays)) != len(displays):
        warnings.append("两个财季落在同一显示季度，图上会重叠；请核对财季日期")
    for p in shown_actuals:
        if p["revenue_na_reason"] == "gap":
            gaps.append(f"{p['period_end']} 之前缺季，QoQ 不计算")
    latest_q = pd.Period(latest["display_quarter"], "Q") if latest else None
    if latest_q is not None:
        expected = latest_q.ordinal - window_start_q.ordinal + 1
        if len(shown_actuals) < expected:
            gaps.append(f"五年窗口应有约 {expected} 个实际季，只有 {len(shown_actuals)} 个"
                        "（上市不足五年、半年报或数据缺口；不插值、不拆半年）")

    # --- prices -------------------------------------------------------------------
    prices = [(_day(r["date"]), float(r["close"])) for r in prices_db if _day(r["date"])]
    price_source = "market.db:daily_price.close"
    if not prices or (as_of - prices[-1][0]).days > PRICE_STALE_DAYS:
        start = prices[-1][0] + timedelta(days=1) if prices else price_start
        raw = live.call("price_range", "get_historical_price_range",
                        from_date=start.isoformat(), to_date=as_of.isoformat())
        extra = sorted((_day(r.get("date")), _num(r.get("close"))) for r in (raw or [])
                       if _day(r.get("date")) and _num(r.get("close")))
        known = {d for d, _ in prices}
        extra = [(d, c) for d, c in extra if d not in known and start <= d <= as_of]
        if extra:
            prices = sorted(prices + extra)
            price_source += " + fmp_live:historical-price-eod"
            warnings.append(f"库内行情截至 {start - timedelta(days=1)}，已用实时 FMP 补 {len(extra)} 个交易日")
    if prices and (as_of - prices[-1][0]).days > PRICE_STALE_DAYS:
        warnings.append(f"股价只到 {prices[-1][0].isoformat()}，距 as_of 超过 {PRICE_STALE_DAYS} 天")
    prices, split_fixes = apply_recorded_splits(prices, splits)
    warnings.extend(split_fixes)
    warnings.extend(detect_split_like_jumps(prices))
    price_q = quarterly_price_returns(prices, window_start_q, as_of) if prices else []
    window_prices = [(d, c) for d, c in prices if pd.Timestamp(d).to_period("Q") >= window_start_q]
    base_prices = [(d, c) for d, c in prices if pd.Timestamp(d).to_period("Q") == base_q]
    price_change = None
    if window_prices:
        base_date, base_close = base_prices[-1] if base_prices else window_prices[0]
        price_change = {
            "base_date": base_date.isoformat(), "base_close": base_close,
            "last_date": window_prices[-1][0].isoformat(), "last_close": window_prices[-1][1],
            "return_pct": (window_prices[-1][1] / base_close - 1) * 100,
            "label": f"{years}年股价变化" if base_prices else "可用行情以来",
        }
        if not base_prices:
            gaps.append(f"股价自 {window_prices[0][0].isoformat()} 起才有数据（上市/复牌晚于窗口起点），不补造")

    if not actuals and not prices:
        status = "blocked"
        gaps.append("库内既无季度财务也无股价")
    elif not shown_actuals or not prices:
        status = "blocked"
        gaps.append("缺季度财务或缺股价，无法出三栏图")
    else:
        status = "partial" if gaps else "complete"

    sources.extend(live.calls)
    if snapshot_meta:
        sources.append({"name": "estimates", **snapshot_meta})
    latest_meta = None
    if latest:
        latest_meta = {k: latest.get(k) for k in (
            "fiscal_year", "fiscal_quarter", "period_end", "announced_at", "filing_date")}
    return {
        "schema_version": 1,
        "symbol": symbol,
        "as_of": as_of.isoformat(),
        "years": years,
        "forecast_quarters": forecast_quarters,
        "window_start_quarter": str(window_start_q),
        "status": status,
        "gaps": gaps,
        "warnings": warnings,
        "currency": currency,
        "unit": "reported currency units (raw, unrounded)",
        "industry": industry,
        "revenue_mode": "street_net" if street_mode else "reported",
        "latest_reported_period": latest_meta,
        "estimate_snapshot": snapshot_meta,
        "periods": visible,
        "price": {
            "source": price_source,
            "adjustment": "vendor close（FMP 拆股调整口径，不含股息；未复权拆股会在 gaps 中提示）",
            "price_as_of": prices[-1][0].isoformat() if prices else None,
            "first_date": window_prices[0][0].isoformat() if window_prices else None,
            "currency": "USD",
            "change": price_change,
            "quarters": price_q,
            "daily": [[d.isoformat(), c] for d, c in window_prices],
        },
        "definitions": {
            "revenue_qoq": "同口径相邻财季：(本季/上季-1)×100%；基期≤0、缺季、缺数据、口径切换为 n/a",
            "net_income_qoq": "两季均盈利才计算 QoQ 柱；均亏损标亏损收窄/扩大（按绝对亏损额）；跨盈亏标扭亏/转亏；GAAP 实际→未核实口径预测为 n/a",
            "price_return": "上季度末收盘→本季度末收盘，仅价格收益不含股息；无上季收盘为 partial，当季未结束为 QTD",
            "display_quarter": "财季按期末所在自然季度归位（非重编自然季度财报）；期末落在季度首 7 天的 52/53 周季在更连续时回拨一季",
        },
        "revenue_basis_labels": REVENUE_BASIS_LABELS,
        "net_income_basis_labels": NET_INCOME_BASIS_LABELS,
        "sources": sources,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


# ---------------------------------------------------------------------------
# Output files
# ---------------------------------------------------------------------------

CSV_FIELDS = [
    "role", "kind", "fiscal_year", "fiscal_quarter", "period_start", "period_end",
    "display_quarter", "display_shifted", "announced_at", "currency",
    "revenue", "revenue_basis", "revenue_source", "revenue_qoq", "revenue_na_reason",
    "net_income", "net_income_basis", "net_income_source", "net_income_qoq",
    "income_change_type", "income_change_pct", "net_income_na_reason",
    "eps", "num_analysts_rev", "num_analysts_eps", "revenue_low", "revenue_high",
    "eps_low", "eps_high", "gross_revenue", "street_revenue",
]


def _write_csv(history: Dict[str, Any], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        for p in history["periods"]:
            w.writerow({k: p.get(k) for k in CSV_FIELDS})


def render_markdown(history: Dict[str, Any], png_name: Optional[str] = PNG_NAME) -> str:
    h = history
    snap = h.get("estimate_snapshot") or {}
    latest = h.get("latest_reported_period") or {}
    price = h["price"]
    lines = [
        f"# {h['symbol']} 五年股价、季度业绩 QoQ 与未来四季共识（冻结数据）",
        "",
        f"- 状态：**{h['status']}**（complete / partial / blocked）",
        f"- as_of：{h['as_of']} · 股价截至：{price.get('price_as_of') or '—'}"
        f" · 最新财报期：FY{latest.get('fiscal_year')} {latest.get('fiscal_quarter')}（期末 {latest.get('period_end')}，公布 {latest.get('announced_at')}）"
        if latest else f"- as_of：{h['as_of']} · 无已披露季度",
        f"- 预测来源：{snap.get('source', '无')} · 快照 {snap.get('snapshot_date', '—')}（{snap.get('snapshot_kind', '—')}）",
        f"- 财务币种：{h.get('currency') or '—'} · 营收口径：{REVENUE_BASIS_LABELS['street_net' if h['revenue_mode'] == 'street_net' else 'reported']}",
        f"- 净利润口径：历史 {NET_INCOME_BASIS_LABELS['gaap']}；预测 {NET_INCOME_BASIS_LABELS['unknown']}",
    ]
    if png_name:
        lines.append(f"- 图：`{png_name}`；可复算数据：`{CSV_NAME}` / `{JSON_NAME}`")
    if h["gaps"]:
        lines += ["", "**缺口（导致 partial/blocked）**"] + [f"- {g}" for g in h["gaps"]]
    if h["warnings"]:
        lines += ["", "**口径与来源警示**"] + [f"- {w}" for w in h["warnings"]]
    lines += ["", "## 季度业绩（实际 + 预测）", "",
              "| 财季 | 期末 | 显示季 | 类型 | 营收 | 营收QoQ | 净利润 | 净利润变化 | EPS | 分析师(营收/EPS) |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for p in h["periods"]:
        kind = "预测" if p["kind"] == "estimate" else ("基期" if p.get("role") == "qoq_base" else "实际")
        rev_q = _pct(p["revenue_qoq"]) if p["revenue_qoq"] is not None else "n/a " + NA_REASONS.get(p["revenue_na_reason"] or "", "")
        analysts = (f"{p.get('num_analysts_rev') or '—'}/{p.get('num_analysts_eps') or '—'}"
                    if p["kind"] == "estimate" else "")
        eps = "—" if p.get("eps") is None else f"{p['eps']:.2f}"
        lines.append(
            f"| FY{p['fiscal_year']} {p['fiscal_quarter']} | {p['period_end']} | {p['display_quarter']}"
            f"{'*' if p.get('display_shifted') else ''} | {kind} | {_money(p['revenue'])} | {rev_q} | "
            f"{_money(p['net_income'])} | {p['income_change_label']} | {eps} | {analysts} |")
    complete_4q = [p for p in h["periods"] if p["kind"] == "estimate"]
    if len(complete_4q) == h["forecast_quarters"] and all(p["revenue"] is not None for p in complete_4q):
        lines += ["", f"未来 {h['forecast_quarters']} 季营收共识合计：{_money(sum(p['revenue'] for p in complete_4q))}"
                  + (f"；净利润共识合计：{_money(sum(p['net_income'] for p in complete_4q))}（口径未核实）"
                     if all(p["net_income"] is not None for p in complete_4q) else "")]
    lines += ["", "## 季度股价收益（仅价格，不含股息）", "", "| 季度 | 基准 | 季末 | 收益 | 标记 |", "|---|---|---|---|---|"]
    for q in price["quarters"]:
        lines.append(f"| {q['quarter']} | {q['base_date']} {q['base_close']:,.2f} | {q['end_date']} {q['end_close']:,.2f} | "
                     f"{_pct(q['return_pct'])} | {' '.join(q['flags'])} |")
    if price.get("change"):
        c = price["change"]
        lines += ["", f"{c['label']}：{c['base_date']} {c['base_close']:,.2f} → {c['last_date']} {c['last_close']:,.2f}，{_pct(c['return_pct'])}"]
    lines += ["", "## 定义", ""] + [f"- {k}：{v}" for k, v in h["definitions"].items()]
    lines += ["", "*显示季带 * 表示 52/53 周财年期末跨界回拨。最新重述序列是回溯数据图，不是历史 point-in-time 可交易信息。*"]
    return "\n".join(lines) + "\n"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare_financial_history(
    symbol: str,
    research_dir: Path,
    as_of: Optional[date] = None,
    years: int = 5,
    forecast_quarters: int = 4,
    *,
    db_path: Optional[Path] = None,
    industry: Optional[str] = None,
    allow_live: bool = True,
    fmp_client: Any = None,
    render_png: bool = True,
) -> Dict[str, Any]:
    """Freeze the five-year price/fundamental dataset into ``research_dir``.

    Returns status, file paths, warnings and gaps. Errors reading the source
    database are reported as ``blocked`` with the error text — never as "the
    company has no financials".
    """
    research_dir = Path(research_dir)
    research_dir.mkdir(parents=True, exist_ok=True)
    as_of = as_of or datetime.now(ZoneInfo("America/New_York")).date()
    if db_path is None:
        from config.settings import MARKET_DB_PATH
        db_path = MARKET_DB_PATH
    live = _Live(symbol.upper(), research_dir / SOURCES_DIR, fmp_client, allow_live)
    try:
        history = build_financial_history(
            symbol, as_of=as_of, db_path=Path(db_path), years=years,
            forecast_quarters=forecast_quarters, industry=industry, live=live)
    except Exception as exc:  # reported as blocked, never as "no financials"
        logger.exception("financial history build failed for %s", symbol)
        history = {
            "schema_version": 1, "symbol": symbol.upper(), "as_of": as_of.isoformat(),
            "years": years, "forecast_quarters": forecast_quarters, "status": "blocked",
            "gaps": [f"数据准备失败（{type(exc).__name__}）：{exc}"], "warnings": [], "periods": [],
            "price": {"quarters": [], "daily": []}, "sources": [{"name": "market.db", "path": str(db_path)}],
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

    json_path = research_dir / JSON_NAME
    csv_path = research_dir / CSV_NAME
    md_path = research_dir / MD_NAME
    png_path: Optional[Path] = None
    if history["status"] != "blocked" and render_png:
        try:
            from terminal.financial_history_chart import render_financial_history_png
            png_path = render_financial_history_png(history, research_dir / PNG_NAME)
        except Exception as exc:
            logger.exception("financial history chart failed for %s", symbol)
            history["gaps"].append(f"绘图失败：{exc}")
            history["status"] = "partial"
    json_path.write_text(json.dumps(history, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    if history["periods"]:
        _write_csv(history, csv_path)
        md_path.write_text(render_markdown(history, PNG_NAME if png_path else None), encoding="utf-8")
    else:
        md_path.write_text(
            f"# {history['symbol']} 五年股价与业绩图：blocked\n\n"
            + "\n".join(f"- {g}" for g in history["gaps"]) + "\n", encoding="utf-8")
    files = [p for p in (json_path, csv_path, md_path, png_path) if p and p.exists()]
    manifest = {
        "symbol": history["symbol"], "as_of": history["as_of"], "status": history["status"],
        "generated_at": history["generated_at"],
        "files": {p.name: _sha256(p) for p in files},
        "png": png_path.name if png_path else None,
        "gaps": history["gaps"], "warnings": history["warnings"],
        "sources": history.get("sources", []),
        "db_path": str(db_path), "db_mode": "ro",
    }
    manifest_path = research_dir / MANIFEST_NAME
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return {
        "status": history["status"],
        "png_path": str(png_path) if png_path else None,
        "json_path": str(json_path),
        "csv_path": str(csv_path) if csv_path.exists() else None,
        "md_path": str(md_path),
        "manifest_path": str(manifest_path),
        "gaps": history["gaps"],
        "warnings": history["warnings"],
        "latest_reported_period": history.get("latest_reported_period"),
        "estimate_snapshot": history.get("estimate_snapshot"),
    }


def load_frozen_history(research_dir: Path) -> Optional[Dict[str, Any]]:
    """Manifest + markdown written in Phase 0, for report compilation."""
    manifest_path = Path(research_dir) / MANIFEST_NAME
    if not manifest_path.exists():
        return None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    md_path = Path(research_dir) / MD_NAME
    manifest["markdown"] = md_path.read_text(encoding="utf-8") if md_path.exists() else ""
    png = manifest.get("png")
    manifest["png_path"] = str(Path(research_dir) / png) if png and (Path(research_dir) / png).exists() else None
    return manifest
