"""Portfolio holdings manager — SQLite-backed via CompanyStore.

Data is persisted in company.db (holdings / transactions / portfolio_cash tables).
Reads company profiles and OPRMS ratings from CompanyStore for enrichment.
"""
from __future__ import annotations

import json
import logging
from typing import Dict, List, Optional

from portfolio.holdings.schema import (
    Position,
    WatchlistEntry,
    InvestmentBucket,
    OPRMS_DNA_LIMITS,
    OPRMS_TIMING_DEFAULTS,
    OPRMS_TIMING_COEFFICIENTS,
)
from terminal.company_store import _normalize_strategy_tag

logger = logging.getLogger(__name__)

# File paths (kept for watchlist — still JSON-based)
from pathlib import Path
_HOLDINGS_DIR = Path(__file__).parent
_WATCHLIST_FILE = _HOLDINGS_DIR / "watchlist.json"


# ---------------------------------------------------------------------------
# PortfolioManager — SQLite-backed core
# ---------------------------------------------------------------------------

class PortfolioManager:
    """Manages holdings, cash, and NAV — backed by company.db."""

    def __init__(self, store=None):
        if store is None:
            from terminal.company_store import get_store
            store = get_store()
        self._store = store

    # ---- Holdings CRUD ----

    def load_holdings(self) -> List[Position]:
        """Load all OPEN holdings, enriched with company + OPRMS data."""
        rows = self._store.get_all_open_holdings()
        return [self._enrich(row) for row in rows]

    def get_position(self, symbol: str) -> Optional[Position]:
        row = self._store.get_open_holding(symbol)
        if not row:
            return None
        return self._enrich(row)

    def add_position(self, symbol: str, shares: float, avg_cost: float,
                     date: str) -> Position:
        """Add a new position atomically (holding + initial BUY transaction)."""
        import datetime as dt
        symbol = symbol.upper()
        conn = self._store._get_conn()
        now = dt.datetime.now().isoformat()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(
                """INSERT INTO holdings (symbol, shares, avg_cost, open_date, status, last_updated)
                   VALUES (?, ?, ?, ?, 'OPEN', ?)""",
                (symbol, shares, avg_cost, date, now),
            )
            pid = cur.lastrowid
            conn.execute(
                """INSERT INTO transactions (position_id, symbol, action, shares, price, date, notes, created_at)
                   VALUES (?, ?, 'BUY', ?, ?, ?, '', ?)""",
                (pid, symbol, shares, avg_cost, date, now),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return self.get_position(symbol)

    def close_position(self, symbol: str, sell_price: float, date: str) -> float:
        row = self._store.get_open_holding(symbol)
        if not row:
            raise ValueError(f"No open position for {symbol}")
        pid = row["position_id"]
        realized = (sell_price - row["avg_cost"]) * row["shares"]
        self._store.insert_transaction(pid, symbol, "SELL", shares=row["shares"],
                                        price=sell_price, date=date)
        self._store.close_holding(pid, close_date=date, realized_pnl=realized)
        return realized

    # ---- Atomic Trade ----

    def execute_trade(self, symbol: str, action: str, shares: float,
                      price: float, date: str, notes: str = "") -> Dict:
        """
        Execute a trade as a single atomic transaction.
        Actions: BUY (new), ADD (to existing), TRIM, SELL (full close).
        Writes holdings + transactions + cash in one SQLite transaction.
        Raises ValueError if insufficient cash or invalid state.
        """
        import datetime as dt
        symbol = symbol.upper()
        conn = self._store._get_conn()

        try:
            conn.execute("BEGIN IMMEDIATE")

            current = conn.execute(
                "SELECT * FROM holdings WHERE symbol = ? AND status = 'OPEN'",
                (symbol,),
            ).fetchone()
            current = dict(current) if current else None
            cash = self._store.get_cash_balance()
            now = dt.datetime.now().isoformat()

            if action in ("BUY", "ADD"):
                if action == "BUY" and current is not None:
                    raise ValueError(f"Position already open for {symbol}. Use ADD to add shares.")
                cost = shares * price
                if cost > cash:
                    raise ValueError(f"Insufficient cash: need {cost:.2f}, have {cash:.2f}")

                if current is None:
                    cur = conn.execute(
                        """INSERT INTO holdings (symbol, shares, avg_cost, open_date, status, last_updated)
                           VALUES (?, ?, ?, ?, 'OPEN', ?)""",
                        (symbol, shares, price, date, now),
                    )
                    pid = cur.lastrowid
                    new_shares, new_avg = shares, price
                else:
                    pid = current["position_id"]
                    old_shares, old_avg = current["shares"], current["avg_cost"]
                    new_shares = old_shares + shares
                    new_avg = (old_shares * old_avg + shares * price) / new_shares
                    conn.execute(
                        "UPDATE holdings SET shares = ?, avg_cost = ?, last_updated = ? WHERE position_id = ?",
                        (new_shares, new_avg, now, pid),
                    )

                conn.execute(
                    """INSERT INTO transactions (position_id, symbol, action, shares, price, date, notes, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (pid, symbol, action, shares, price, date, notes, now),
                )
                new_balance = cash - cost
                conn.execute(
                    """INSERT INTO portfolio_cash (action, amount, balance_after, notes, updated_at)
                       VALUES ('WITHDRAW', ?, ?, ?, ?)""",
                    (-cost, new_balance, f"{action} {symbol} {shares}@{price}", now),
                )
                conn.commit()
                return {"action": action, "new_shares": new_shares, "new_avg_cost": new_avg, "closed": False}

            elif action in ("TRIM", "SELL"):
                if current is None:
                    raise ValueError(f"No open position for {symbol}")
                pid = current["position_id"]
                old_shares = current["shares"]

                if shares > old_shares:
                    raise ValueError(f"Cannot sell {shares}, only hold {old_shares}")

                proceeds = shares * price
                realized = (price - current["avg_cost"]) * shares

                conn.execute(
                    """INSERT INTO transactions (position_id, symbol, action, shares, price, date, notes, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (pid, symbol, action, shares, price, date, notes, now),
                )

                remaining = old_shares - shares
                closed = remaining == 0

                if closed:
                    # Final close: add this trim's P&L to any accumulated from prior trims
                    total_realized = (current.get("realized_pnl") or 0) + realized
                    conn.execute(
                        """UPDATE holdings SET status = 'CLOSED', close_date = ?,
                           realized_pnl = ?, shares = 0, last_updated = ? WHERE position_id = ?""",
                        (date, total_realized, now, pid),
                    )
                else:
                    # Partial trim: accumulate realized_pnl on open position
                    conn.execute(
                        """UPDATE holdings SET shares = ?,
                           realized_pnl = COALESCE(realized_pnl, 0) + ?,
                           last_updated = ? WHERE position_id = ?""",
                        (remaining, realized, now, pid),
                    )

                new_balance = cash + proceeds
                conn.execute(
                    """INSERT INTO portfolio_cash (action, amount, balance_after, notes, updated_at)
                       VALUES ('DEPOSIT', ?, ?, ?, ?)""",
                    (proceeds, new_balance, f"{action} {symbol} {shares}@{price}", now),
                )
                conn.commit()
                cumulative_pnl = (current.get("realized_pnl") or 0) + realized
                return {
                    "action": action,
                    "remaining_shares": remaining,
                    "realized_pnl": cumulative_pnl,  # total across all trims + this leg
                    "this_leg_pnl": realized,         # this transaction only
                    "closed": closed,
                }

            else:
                raise ValueError(f"Unknown action: {action}")

        except Exception:
            conn.rollback()
            raise

    # ---- Atomic Option Trade ----

    def preview_option_trade(
        self,
        *,
        symbol: str,
        expiration: str,
        strike: float,
        side: str,
        action: str,
        quantity: int,
        premium: float,
        date: str,
        strategy_tag: str = "",
        notes: str = "",
    ) -> Dict:
        """Compute lifecycle effect WITHOUT mutating any DB state.

        Returns a dict with the fields trade.md needs to render its confirmation.
        Raises ValueError on direction conflicts or over-close — same errors that
        execute_option_trade would raise so the prompt can show them to Boss.
        """
        derivation = self._derive_option_lifecycle(
            symbol=symbol, expiration=expiration, strike=strike, side=side,
            action=action, quantity=quantity, premium=premium,
            strategy_tag=strategy_tag,
        )
        return {
            "contract": f"{symbol.upper()} {expiration} {strike:g}{('C' if side.upper() == 'CALL' else 'P')}",
            "action": action.upper(),
            "effect": derivation["effect"],
            "current_quantity": derivation["current_qty"],
            "new_quantity": derivation["new_qty"],
            "current_avg_premium": derivation["current_avg"],
            "new_avg_premium": derivation["new_avg"],
            "cash_delta": derivation["cash_delta"],
            "realized_pnl_this_trade": derivation["realized_pnl"],
            "strategy_tag": strategy_tag,
            "notes": notes,
            "date": date,
        }

    def execute_option_trade(
        self,
        *,
        symbol: str,
        expiration: str,
        strike: float,
        side: str,
        action: str,
        quantity: int,
        premium: float,
        date: str,
        strategy_tag: str = "",
        notes: str = "",
    ) -> Dict:
        """Atomically apply an option trade: ledger row + position update + cash row."""
        conn = self._store._get_conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            result = self._apply_option_trade_in_transaction(
                conn=conn,
                symbol=symbol, expiration=expiration, strike=strike, side=side,
                action=action, quantity=quantity, premium=premium, date=date,
                strategy_tag=strategy_tag, notes=notes,
            )
            conn.execute("COMMIT")
            return result
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def execute_option_roll(
        self,
        *,
        close_leg: Dict,
        open_leg: Dict,
        date: str,
        notes: str = "",
    ) -> Dict:
        """Atomic 1-out-1-in roll. Both legs in the same SQLite transaction."""
        conn = self._store._get_conn()
        roll_marker = notes or "ROLL"
        try:
            conn.execute("BEGIN IMMEDIATE")
            close_args = dict(close_leg)
            close_args.setdefault("date", date)
            close_args["notes"] = (close_args.get("notes") or "") + f" [{roll_marker}]"
            close_result = self._apply_option_trade_in_transaction(conn=conn, **close_args)

            open_args = dict(open_leg)
            open_args.setdefault("date", date)
            open_args["notes"] = (open_args.get("notes") or "") + f" [{roll_marker}]"
            open_result = self._apply_option_trade_in_transaction(conn=conn, **open_args)

            conn.execute("COMMIT")
            return {"close": close_result, "open": open_result}
        except Exception:
            conn.execute("ROLLBACK")
            raise

    # ---- Option lifecycle internals ----

    _OPTION_ACTIONS = {"BTO", "STO", "BTC", "STC"}

    def _derive_option_lifecycle(
        self,
        *,
        symbol: str,
        expiration: str,
        strike: float,
        side: str,
        action: str,
        quantity: int,
        premium: float,
        strategy_tag: str,
    ) -> Dict:
        """Pure function — no DB writes. Reads current OPEN leg for context."""
        action = action.upper()
        side = side.upper()
        symbol = symbol.upper()

        if action not in self._OPTION_ACTIONS:
            raise ValueError(
                f"unknown option action {action!r}. Use BTO / STO / BTC / STC."
            )
        if quantity <= 0:
            raise ValueError(f"option quantity must be positive, got {quantity}")
        if premium < 0:
            raise ValueError(f"option premium must be non-negative, got {premium}")
        if side not in ("CALL", "PUT"):
            raise ValueError(f"option side must be CALL or PUT, got {side!r}")

        current = self._store.get_open_option_position(
            symbol=symbol, expiration=expiration, strike=strike,
            side=side, strategy_tag=strategy_tag,
        )
        current_qty = current["quantity"] if current else 0
        current_avg = current["avg_premium"] if current else None

        # Direction & cash sign
        if action == "BTO":
            qty_delta, cash_sign = +quantity, -1
        elif action == "STO":
            qty_delta, cash_sign = -quantity, +1
        elif action == "STC":
            qty_delta, cash_sign = -quantity, +1
        else:  # BTC
            qty_delta, cash_sign = +quantity, -1

        cash_delta = cash_sign * quantity * premium * 100.0
        new_qty = current_qty + qty_delta

        # Validate effect against current state
        if action in ("BTO", "STO"):
            # Opening / adding
            if current is None:
                effect = "OPEN"
                new_avg = premium
                realized = 0.0
            else:
                same_dir = (current_qty > 0 and action == "BTO") or (current_qty < 0 and action == "STO")
                if not same_dir:
                    raise ValueError(
                        f"direction conflict: current OPEN leg quantity={current_qty}, "
                        f"action={action} would flip sign. Use {'STC' if current_qty > 0 else 'BTC'} "
                        f"to close, or execute_option_roll() for an explicit roll."
                    )
                # Add — weighted avg over absolute sizes
                old_abs = abs(current_qty)
                add_abs = quantity
                new_avg = (old_abs * current_avg + add_abs * premium) / (old_abs + add_abs)
                effect = "ADD"
                realized = 0.0
        else:
            # Closing actions
            if current is None:
                raise ValueError(
                    f"no open leg for {symbol} {expiration} {strike:g}{side[0]} "
                    f"(strategy_tag={strategy_tag!r}); cannot {action}."
                )
            if action == "STC" and current_qty <= 0:
                raise ValueError(
                    f"direction conflict: STC requires a long leg, but current quantity={current_qty}."
                )
            if action == "BTC" and current_qty >= 0:
                raise ValueError(
                    f"direction conflict: BTC requires a short leg, but current quantity={current_qty}."
                )
            # Over-close check: |new_qty| must not exceed |current| AND must not flip sign
            if abs(quantity) > abs(current_qty):
                raise ValueError(
                    f"quantity {quantity} exceeds open quantity |{current_qty}|; "
                    f"split into CLOSE + OPEN or use execute_option_roll()."
                )
            closed_qty = quantity  # absolute
            if action == "STC":  # closing long
                realized = (premium - current_avg) * closed_qty * 100.0
            else:  # BTC, covering short
                realized = (current_avg - premium) * closed_qty * 100.0
            if new_qty == 0:
                effect = "CLOSE"
                new_avg = None
            else:
                effect = "REDUCE"
                new_avg = current_avg  # avg unchanged on partial close

        return {
            "current_position_id": current["id"] if current else None,
            "current_qty": current_qty,
            "current_avg": current_avg,
            "new_qty": new_qty,
            "new_avg": new_avg,
            "cash_delta": cash_delta,
            "realized_pnl": realized,
            "effect": effect,
        }

    def _apply_option_trade_in_transaction(
        self,
        *,
        conn,
        symbol: str,
        expiration: str,
        strike: float,
        side: str,
        action: str,
        quantity: int,
        premium: float,
        date: str,
        strategy_tag: str = "",
        notes: str = "",
    ) -> Dict:
        """Mutating core. Caller owns the BEGIN/COMMIT/ROLLBACK."""
        import datetime as dt

        strategy_tag = _normalize_strategy_tag(strategy_tag)
        derivation = self._derive_option_lifecycle(
            symbol=symbol, expiration=expiration, strike=strike, side=side,
            action=action, quantity=quantity, premium=premium,
            strategy_tag=strategy_tag,
        )
        symbol = symbol.upper()
        side = side.upper()
        action = action.upper()
        now = dt.datetime.now().isoformat()
        cash_balance = self._store.get_cash_balance()

        effect = derivation["effect"]
        new_qty = derivation["new_qty"]
        new_avg = derivation["new_avg"]
        cash_delta = derivation["cash_delta"]
        realized = derivation["realized_pnl"]
        position_id = derivation["current_position_id"]

        if effect == "OPEN":
            cur = conn.execute(
                """INSERT INTO option_positions
                   (symbol, expiration, strike, side, quantity, avg_premium,
                    open_date, status, strategy_tag, notes, realized_pnl, last_updated)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, 0, ?)""",
                (symbol, expiration, strike, side, new_qty, new_avg, date,
                 strategy_tag, notes, now),
            )
            position_id = cur.lastrowid
        elif effect == "ADD":
            conn.execute(
                """UPDATE option_positions
                   SET quantity = ?, avg_premium = ?, last_updated = ?
                   WHERE id = ?""",
                (new_qty, new_avg, now, position_id),
            )
        elif effect == "REDUCE":
            conn.execute(
                """UPDATE option_positions
                   SET quantity = ?,
                       realized_pnl = COALESCE(realized_pnl, 0) + ?,
                       last_updated = ?
                   WHERE id = ?""",
                (new_qty, realized, now, position_id),
            )
        elif effect == "CLOSE":
            conn.execute(
                """UPDATE option_positions
                   SET quantity = 0, status = 'CLOSED', close_date = ?,
                       realized_pnl = COALESCE(realized_pnl, 0) + ?,
                       last_updated = ?
                   WHERE id = ?""",
                (date, realized, now, position_id),
            )
        else:
            raise ValueError(f"internal: unknown effect {effect!r}")

        conn.execute(
            """INSERT INTO option_transactions
               (option_position_id, symbol, expiration, strike, side,
                action, quantity, premium, date, strategy_tag, notes, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (position_id, symbol, expiration, strike, side, action,
             quantity, premium, date, strategy_tag, notes, now),
        )

        new_balance = cash_balance + cash_delta
        cash_action = "DEPOSIT" if cash_delta >= 0 else "WITHDRAW"
        cash_note = f"{action} {symbol} {expiration} {strike:g}{side[0]} {quantity}@{premium}"
        if strategy_tag:
            cash_note += f" [{strategy_tag}]"
        conn.execute(
            """INSERT INTO portfolio_cash (action, amount, balance_after, notes, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            (cash_action, cash_delta, new_balance, cash_note, now),
        )

        return {
            "contract": f"{symbol} {expiration} {strike:g}{side[0]}",
            "action": action,
            "effect": effect,
            "current_quantity": derivation["current_qty"],
            "new_quantity": new_qty,
            "current_avg_premium": derivation["current_avg"],
            "new_avg_premium": new_avg,
            "cash_delta": cash_delta,
            "realized_pnl_this_trade": realized,
            "strategy_tag": strategy_tag,
            "position_id": position_id,
        }

    # ---- NAV & Weights ----

    def get_option_market_value(self, option_prices: Optional[Dict] = None) -> float:
        """Sum market value of all open option positions.

        Each contract = 100 shares. value = quantity * premium * 100.
        option_prices: {(symbol, exp, strike, side): current_premium}
        Falls back to avg_premium (cost) if no live price available.
        """
        positions = self._store.get_open_option_positions()
        total = 0.0
        for p in positions:
            key = (p["symbol"], p["expiration"], p["strike"], p["side"])
            premium = (option_prices or {}).get(key, p["avg_premium"])
            total += p["quantity"] * premium * 100
        return total

    def get_total_nav(self, prices: Dict[str, float],
                      option_prices: Optional[Dict] = None) -> float:
        holdings = self._store.get_all_open_holdings()
        invested = sum(h["shares"] * prices.get(h["symbol"], 0) for h in holdings)
        options = self.get_option_market_value(option_prices)
        cash = self._store.get_cash_balance()
        return invested + options + cash

    def refresh_prices(self, prices: Dict[str, float],
                       option_prices: Optional[Dict] = None) -> List[Position]:
        """Load holdings, apply prices, compute weights based on total_NAV (incl options)."""
        positions = self.load_holdings()
        nav = self.get_total_nav(prices, option_prices)
        for p in positions:
            p.current_price = prices.get(p.symbol, 0)
            p.current_weight = (p.market_value / nav) if nav > 0 else 0
        return positions

    def get_portfolio_summary(self, prices: Dict[str, float],
                              option_prices: Optional[Dict] = None) -> Dict:
        positions = self.refresh_prices(prices, option_prices)
        cash = self._store.get_cash_balance()
        option_mv = self.get_option_market_value(option_prices)
        nav = self.get_total_nav(prices, option_prices)
        stock_invested = sum(p.market_value for p in positions)
        total_invested = stock_invested + option_mv
        total_cost = sum(p.shares * p.cost_basis for p in positions)

        # Option cost basis
        opt_positions = self._store.get_open_option_positions()
        option_cost = sum(p["quantity"] * p["avg_premium"] * 100 for p in opt_positions)
        total_cost += option_cost

        total_pnl = total_invested - total_cost
        return {
            "total_nav": nav,
            "total_value": total_invested,  # legacy field: invested value (no cash)
            "total_cost": total_cost,       # legacy field: total cost basis
            "invested_value": total_invested,
            "stock_value": stock_invested,
            "option_value": option_mv,
            "cash": cash,
            "invested_pct": total_invested / nav if nav > 0 else 0,
            "cash_pct": cash / nav if nav > 0 else 0,
            "total_positions": len(positions),
            "option_positions": len(opt_positions),
            "total_pnl": total_pnl,
            "total_pnl_pct": total_pnl / total_cost if total_cost > 0 else 0,
            "by_bucket": _count_by_bucket(positions),
            "by_dna": _count_by_field(positions, "dna_rating"),
            "positions": [p.to_dict() for p in positions],
            "options": [{
                "symbol": o["symbol"],
                "expiration": o["expiration"],
                "strike": o["strike"],
                "side": o["side"],
                "quantity": o["quantity"],
                "avg_premium": o["avg_premium"],
                "strategy_tag": o.get("strategy_tag", ""),
            } for o in opt_positions],
        }

    # ---- Enrichment ----

    def _enrich(self, row: Dict) -> Position:
        symbol = row["symbol"]
        company = self._store.get_company(symbol) or {}
        oprms = self._store.get_current_oprms(symbol) or {}
        kc = self._store.get_kill_conditions(symbol, active_only=True)

        dna = oprms.get("dna", "")
        timing = oprms.get("timing", "")
        target_weight = calculate_target_weight(dna, timing) if dna and timing else 0.0

        # last_review_date: DB manual override > latest analysis date
        db_review = row.get("last_review_date", "")
        if not db_review:
            latest_analysis = self._store.get_latest_analysis(symbol)
            db_review = latest_analysis.get("analysis_date", "") if latest_analysis else ""

        return Position(
            symbol=symbol,
            company_name=company.get("company_name", ""),
            sector=company.get("sector", ""),
            industry=company.get("industry", ""),
            dna_rating=dna,
            timing_rating=timing,
            investment_bucket=oprms.get("investment_bucket", InvestmentBucket.COMPOUNDER.value),
            cost_basis=row["avg_cost"],
            shares=row["shares"],
            entry_date=row["open_date"],
            target_weight=target_weight,
            last_review_date=db_review,
            notes=row.get("notes", ""),
            kill_conditions=[c["description"] for c in kc],
            position_id=row["position_id"],
            status=row["status"],
            close_date=row.get("close_date"),
            realized_pnl=row.get("realized_pnl"),
        )


# ---------------------------------------------------------------------------
# Backward-compatible module-level API (shim layer)
# ---------------------------------------------------------------------------
# Existing callers (commands.py, monitor.py, benchmark/review.py, __init__.py)
# continue through these functions, no import changes needed.

_default_mgr: Optional[PortfolioManager] = None


def _get_default_mgr() -> PortfolioManager:
    global _default_mgr
    if _default_mgr is None:
        _default_mgr = PortfolioManager()
    return _default_mgr


def load_holdings() -> List[Position]:
    return _get_default_mgr().load_holdings()


def get_position(symbol: str) -> Optional[Position]:
    return _get_default_mgr().get_position(symbol)


def save_holdings(positions: List[Position]) -> None:
    """No-op: SQLite persistence is automatic. Kept for backward compat."""
    pass


def add_position(position: Position) -> None:
    _get_default_mgr().add_position(
        position.symbol, shares=position.shares,
        avg_cost=position.cost_basis, date=position.entry_date or "",
    )


def update_position(symbol: str, **kwargs) -> Optional[Position]:
    p = _get_default_mgr().get_position(symbol)
    if p and p.position_id:
        _get_default_mgr()._store.update_holding(p.position_id, **kwargs)
    return p


def remove_position(symbol: str) -> Optional[Position]:
    """Close position (shim — does not calculate realized P&L)."""
    from datetime import date
    p = _get_default_mgr().get_position(symbol)
    if p and p.position_id:
        _get_default_mgr()._store.close_holding(
            p.position_id, close_date=date.today().isoformat(), realized_pnl=0.0
        )
    return p


def _fetch_latest_prices(symbols: List[str]) -> Dict[str, float]:
    """Fetch latest prices from market.db. Returns descending df, iloc[0] = newest."""
    from src.data.price_fetcher import get_price_df
    prices = {}
    for sym in symbols:
        try:
            df = get_price_df(sym, days=5, max_age_days=0)
            if df is not None and not df.empty:
                prices[sym] = df["close"].iloc[0]
        except Exception:
            pass
    return prices


def refresh_prices(positions: Optional[List[Position]] = None) -> List[Position]:
    """Backward-compatible: fetch prices internally, return updated positions."""
    if positions is None:
        positions = load_holdings()
    if not positions:
        return positions
    prices = _fetch_latest_prices([p.symbol for p in positions])
    return _get_default_mgr().refresh_prices(prices)


def get_portfolio_value(positions: Optional[List[Position]] = None) -> float:
    """Sum of market values (no cash). Kept for benchmark compat."""
    if positions is None:
        positions = load_holdings()
    return sum(p.market_value for p in positions)


def get_portfolio_summary(positions: Optional[List[Position]] = None) -> dict:
    """Backward-compatible: uses refresh_prices internally."""
    if positions is None:
        positions = load_holdings()
    if not positions:
        return {"total_positions": 0, "message": "No holdings found."}
    prices = _fetch_latest_prices([p.symbol for p in positions])
    return _get_default_mgr().get_portfolio_summary(prices)


def calculate_target_weight(dna_rating: str, timing_rating: str) -> float:
    """Pure calculation — no DB access needed."""
    dna_limit = OPRMS_DNA_LIMITS.get(dna_rating, 0.02)
    timing_coeff = OPRMS_TIMING_DEFAULTS.get(timing_rating, 0.3)
    return round(dna_limit * timing_coeff, 4)


def calculate_target_weight_range(dna_rating: str, timing_rating: str) -> tuple:
    """Return (min_weight, max_weight) based on OPRMS timing range."""
    dna_limit = OPRMS_DNA_LIMITS.get(dna_rating, 0.02)
    lo, hi = OPRMS_TIMING_COEFFICIENTS.get(timing_rating, (0.1, 0.3))
    return (round(dna_limit * lo, 4), round(dna_limit * hi, 4))


def get_positions_by_bucket(bucket: InvestmentBucket) -> List[Position]:
    """Filter positions by investment bucket."""
    return [p for p in load_holdings() if p.investment_bucket == bucket.value]


# ---------------------------------------------------------------------------
# Watchlist (still JSON-based)
# ---------------------------------------------------------------------------

def load_watchlist() -> List[WatchlistEntry]:
    """Load watchlist entries."""
    if not _WATCHLIST_FILE.exists():
        return []
    try:
        with open(_WATCHLIST_FILE, "r") as f:
            data = json.load(f)
        return [WatchlistEntry.from_dict(d) for d in data]
    except (json.JSONDecodeError, Exception) as e:
        logger.error(f"Failed to load watchlist: {e}")
        return []


def save_watchlist(entries: List[WatchlistEntry]) -> None:
    """Persist watchlist entries."""
    _HOLDINGS_DIR.mkdir(parents=True, exist_ok=True)
    with open(_WATCHLIST_FILE, "w") as f:
        json.dump([e.to_dict() for e in entries], f, indent=2, ensure_ascii=False)


def add_to_watchlist(entry: WatchlistEntry) -> None:
    """Add a stock to watchlist."""
    entries = load_watchlist()
    existing = {e.symbol for e in entries}
    if entry.symbol in existing:
        raise ValueError(f"{entry.symbol} already on watchlist")
    entries.append(entry)
    save_watchlist(entries)


def remove_from_watchlist(symbol: str) -> Optional[WatchlistEntry]:
    """Remove a stock from watchlist."""
    symbol = symbol.upper()
    entries = load_watchlist()
    for i, e in enumerate(entries):
        if e.symbol == symbol:
            removed = entries.pop(i)
            save_watchlist(entries)
            return removed
    return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _count_by_bucket(positions: List[Position]) -> dict:
    result = {}
    for p in positions:
        bucket = p.investment_bucket
        if bucket not in result:
            result[bucket] = {"count": 0, "value": 0.0}
        result[bucket]["count"] += 1
        result[bucket]["value"] += p.market_value
    return result


def _count_by_field(positions: List[Position], field_name: str) -> dict:
    result = {}
    for p in positions:
        key = getattr(p, field_name, "Unknown")
        if key not in result:
            result[key] = 0
        result[key] += 1
    return result
