"""Synthetic builders for prosperity engine tests (values cite market.db where noted)."""
from terminal.prosperity.types import EpsQuarter


def est(snap, fiscal, eps, n=10, period="Q"):
    return {"snapshot_date": snap, "fiscal_date": fiscal, "period_type": period,
            "snapshot_kind": "weekly", "eps_avg": eps, "num_analysts_eps": n}


def eq(fiscal, announce, eps):
    return EpsQuarter(fiscal, announce, eps, None, ())
