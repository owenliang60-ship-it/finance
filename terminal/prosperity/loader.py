"""The engine's only I/O: member resolution and whole-history reads (read-only store)."""
from __future__ import annotations

import json
from typing import FrozenSet, List, Tuple

from src.data.market_store import MarketStore
from terminal.prosperity.config import BETA_BENCHMARK, LIVE_MEMBERSHIP_FROM
from terminal.prosperity.types import SymbolHistory


def resolve_members(store: MarketStore, as_of: str) -> Tuple[List[str], str, FrozenSet[str]]:
    """(symbols, membership_basis, identity_unverified) for `as_of`."""
    if as_of[:10] >= LIVE_MEMBERSHIP_FROM:
        eligible = store.get_security_eligibility()
        symbols = [s for s in store.get_members_as_of(as_of[:10]) if eligible.get(s) is True]
        return sorted(symbols), "extended_membership", frozenset()
    approx = store.approximate_members_as_of(as_of[:10])
    return sorted(approx["symbols"]), "approximate_mcap", frozenset(approx["unverified"])


def _closes(rows, key: str) -> List[Tuple[str, float]]:
    return sorted((r["date"][:10], r[key]) for r in rows if r.get(key) is not None)


def load_history(store: MarketStore, symbol: str, *, with_vintage: bool) -> SymbolHistory:
    conn = store._get_conn()
    by_date = lambda rows: sorted(rows, key=lambda r: r["date"])
    vintage = {}
    if with_vintage:
        for r in conn.execute("SELECT statement, observed_at, filing_date, accepted_date, payload "
                              "FROM fundamental_vintage WHERE symbol = ? ORDER BY fiscal_date, observed_at",
                              (symbol,)).fetchall():
            table = MarketStore._VINTAGE_STATEMENT_TABLES.get(r["statement"])
            if table is None:
                continue
            row = store._convert_row(json.loads(r["payload"]), table)
            for key in ("filing_date", "accepted_date"):
                if row.get(key) is None:
                    row[key] = r[key]
            row["_observed_at"] = r["observed_at"]
            vintage.setdefault(r["statement"], []).append(row)
    estimates = [dict(r) for r in conn.execute(
        "SELECT * FROM fmp_estimates WHERE symbol = ? AND snapshot_kind = 'weekly' "
        "ORDER BY snapshot_date, fiscal_date, period_type", (symbol,)).fetchall()]
    caps = [dict(r) for r in conn.execute(
        "SELECT date, market_cap FROM historical_market_cap WHERE symbol = ? ORDER BY date", (symbol,)).fetchall()]
    prof = conn.execute("SELECT payload FROM company_profile WHERE symbol = ?", (symbol,)).fetchone()
    sm = conn.execute("SELECT is_adr FROM security_master WHERE symbol = ?", (symbol,)).fetchone()
    return SymbolHistory(
        symbol=symbol,
        income=by_date(store.get_income(symbol, limit=0)),
        balance=by_date(store.get_balance_sheet(symbol, limit=0)),
        cashflow=by_date(store.get_cash_flow(symbol, limit=0)),
        vintage=vintage,
        earnings=store.get_fmp_earnings(symbol),
        estimates=estimates,
        splits=store.get_stock_splits(symbol),
        closes=_closes(store.get_daily_prices(symbol), "close"),
        market_caps=_closes(caps, "market_cap"),
        profile=json.loads(prof["payload"]) if prof else None,
        is_adr=bool(sm["is_adr"]) if sm else None,
    )


def load_benchmark_closes(store: MarketStore) -> List[Tuple[str, float]]:
    return _closes(store.get_daily_prices(BETA_BENCHMARK), "close")
