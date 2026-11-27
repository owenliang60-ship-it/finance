"""Portfolio holdings enrichment — company + OPRMS metadata from company.db.

The local holdings / cash / option ledger tables are retired. The book of
record is the read-only Google Sheet (portfolio.holdings.sheet_book); this
module only enriches sheet-sourced rows with company.db research data.
"""
from __future__ import annotations

import logging
from typing import Dict

from portfolio.holdings.schema import (
    Position,
    InvestmentBucket,
    OPRMS_DNA_LIMITS,
    OPRMS_TIMING_DEFAULTS,
)

logger = logging.getLogger(__name__)


class PortfolioManager:
    """Enriches sheet holding rows with company.db research data (read-only)."""

    def __init__(self, store=None):
        if store is None:
            from terminal.company_store import get_store
            store = get_store()
        self._store = store

    # ---- Enrichment ----

    def enrich_holding_row(self, row: Dict) -> Position:
        """Public wrapper: build an enriched Position from a raw holding-row dict.

        Used by sheet-sourced callers (PI) that bypass company.db holdings.
        Required row keys: symbol / avg_cost / shares / open_date / position_id / status.
        """
        return self._enrich(row)

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


def calculate_target_weight(dna_rating: str, timing_rating: str) -> float:
    """Pure calculation — no DB access needed."""
    dna_limit = OPRMS_DNA_LIMITS.get(dna_rating, 0.02)
    timing_coeff = OPRMS_TIMING_DEFAULTS.get(timing_rating, 0.3)
    return round(dna_limit * timing_coeff, 4)
