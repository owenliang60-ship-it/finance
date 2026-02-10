"""
Portfolio monitoring — sweep all positions for alerts, drift, and staleness.

Combines portfolio exposure alerts with company DB kill condition checks.
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from terminal.company_db import get_kill_conditions, list_all_companies

logger = logging.getLogger(__name__)


@dataclass
class MonitorReport:
    """Full monitoring sweep result."""
    generated_at: str = ""
    position_count: int = 0
    total_value: float = 0.0

    # Exposure alerts (from portfolio/exposure/alerts.py)
    exposure_alerts: List[dict] = field(default_factory=list)

    # Kill condition status per position
    kill_condition_status: List[dict] = field(default_factory=list)

    # Weight drift (current vs OPRMS target)
    weight_drift: List[dict] = field(default_factory=list)

    # Stale reviews (no review in 30+ days)
    stale_reviews: List[dict] = field(default_factory=list)

    # Missing kill conditions
    missing_kill_conditions: List[str] = field(default_factory=list)

    # Analysis freshness (from terminal.freshness)
    analysis_freshness: List[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        stale_analyses = sum(
            1 for f in self.analysis_freshness
            if f.get("level") in ("RED", "YELLOW")
        )
        return {
            "generated_at": self.generated_at,
            "position_count": self.position_count,
            "total_value": self.total_value,
            "exposure_alerts": self.exposure_alerts,
            "kill_condition_status": self.kill_condition_status,
            "weight_drift": self.weight_drift,
            "stale_reviews": self.stale_reviews,
            "missing_kill_conditions": self.missing_kill_conditions,
            "analysis_freshness": self.analysis_freshness,
            "summary": {
                "total_alerts": len(self.exposure_alerts),
                "positions_with_kill_conditions": len(self.kill_condition_status),
                "positions_with_drift": len(self.weight_drift),
                "stale_count": len(self.stale_reviews),
                "missing_kc_count": len(self.missing_kill_conditions),
                "stale_analyses": stale_analyses,
            },
        }


def run_full_monitor() -> dict:
    """
    Execute the full monitoring sweep.

    1. Load holdings, refresh prices
    2. Run 7 exposure alert rules
    3. Check company DB kill conditions per position
    4. Calculate weight drift (current vs OPRMS target)
    5. Flag stale reviews (>30 days)

    Returns MonitorReport as dict.
    """
    report = MonitorReport(generated_at=datetime.now().isoformat())

    # 1. Load holdings
    try:
        from portfolio.holdings.manager import load_holdings, refresh_prices
        positions = load_holdings()
    except Exception as e:
        logger.error(f"Failed to load holdings: {e}")
        return {"error": f"Failed to load holdings: {e}"}

    if not positions:
        report.position_count = 0
        return report.to_dict()

    # Refresh prices
    try:
        positions = refresh_prices(positions)
    except Exception as e:
        logger.warning(f"Price refresh failed: {e}")

    report.position_count = len(positions)
    report.total_value = sum(p.market_value for p in positions)

    # 2. Run exposure alerts
    try:
        from portfolio.exposure.alerts import run_all_checks
        alerts = run_all_checks(positions)
        report.exposure_alerts = [a.to_dict() for a in alerts]
    except Exception as e:
        logger.error(f"Exposure alerts failed: {e}")
        report.exposure_alerts = [{"error": str(e)}]

    # 3. Check kill conditions from company DB
    for p in positions:
        kc = get_kill_conditions(p.symbol)
        if kc:
            report.kill_condition_status.append({
                "symbol": p.symbol,
                "conditions": kc,
                "count": len(kc),
            })
        else:
            report.missing_kill_conditions.append(p.symbol)

    # 4. Weight drift
    for p in positions:
        if p.target_weight > 0:
            drift = p.current_weight - p.target_weight
            drift_pct = (drift / p.target_weight * 100) if p.target_weight > 0 else 0
            if abs(drift_pct) > 10:  # Flag if >10% drift from target
                report.weight_drift.append({
                    "symbol": p.symbol,
                    "current_weight": round(p.current_weight * 100, 2),
                    "target_weight": round(p.target_weight * 100, 2),
                    "drift_pct": round(drift_pct, 1),
                    "direction": "overweight" if drift > 0 else "underweight",
                })

    # 5. Stale reviews
    now = datetime.now()
    for p in positions:
        if not p.last_review_date:
            report.stale_reviews.append({
                "symbol": p.symbol,
                "last_review": None,
                "days_since": None,
                "status": "never_reviewed",
            })
        else:
            try:
                last = datetime.strptime(p.last_review_date, "%Y-%m-%d")
                days = (now - last).days
                if days > 30:
                    report.stale_reviews.append({
                        "symbol": p.symbol,
                        "last_review": p.last_review_date,
                        "days_since": days,
                        "status": "overdue",
                    })
            except ValueError:
                pass

    # 6. Analysis freshness
    try:
        from terminal.freshness import check_freshness
        for p in positions:
            try:
                fr = check_freshness(p.symbol)
                report.analysis_freshness.append(fr.to_dict())
            except Exception as e:
                logger.warning(f"Freshness check failed for {p.symbol}: {e}")
    except Exception as e:
        logger.error(f"Freshness module import failed: {e}")

    return report.to_dict()
