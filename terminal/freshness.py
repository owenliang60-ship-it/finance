"""
Analysis freshness — version tracking, staleness detection, timing refresh.

Three capabilities:
1. check_freshness()  — is this analysis still valid? GREEN/YELLOW/RED
2. timing refresh     — lightweight re-score (keep DNA, update Timing)
3. evolution timeline — track OPRMS changes over time
"""
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from terminal.company_db import (
    get_oprms,
    get_oprms_history,
    list_all_companies,
    save_oprms,
    get_kill_conditions,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

YELLOW_DAYS = 14
RED_DAYS = 30
YELLOW_PRICE_PCT = 10.0
RED_PRICE_PCT = 20.0


# ---------------------------------------------------------------------------
# AnalysisContext — snapshot of market state at analysis time
# ---------------------------------------------------------------------------

@dataclass
class AnalysisContext:
    """Records the market state when an analysis was performed."""
    analyzed_at: str = ""               # ISO timestamp
    price_at_analysis: Optional[float] = None
    price_date: str = ""                # date of the price data point
    regime: str = ""                    # RISK_ON/NEUTRAL/RISK_OFF/CRISIS
    vix: Optional[float] = None
    fundamental_date: str = ""          # most recent earnings date in data
    earnings_date: str = ""             # next/most recent earnings calendar date
    depth: str = ""                     # quick/standard/full/deep
    source: str = ""                    # pipeline/deep_analysis_v2/timing_refresh
    evidence_count: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "AnalysisContext":
        valid = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in valid}
        return cls(**filtered)


def build_analysis_context(
    data_package: Any,
    depth: str,
    source: str,
) -> dict:
    """
    Extract an AnalysisContext dict from a DataPackage.

    Called at the end of analysis to snapshot the market state.
    """
    ctx = AnalysisContext(
        analyzed_at=datetime.now().isoformat(),
        depth=depth,
        source=source,
    )

    # Price
    if data_package.price:
        ctx.price_at_analysis = data_package.latest_price
        ctx.price_date = data_package.price.get("latest_date", "")

    # Macro
    if data_package.macro:
        ctx.regime = getattr(data_package.macro, "regime", "")
        ctx.vix = getattr(data_package.macro, "vix", None)

    # Fundamentals — latest income statement date
    if data_package.income:
        try:
            ctx.fundamental_date = data_package.income[0].get("date", "")
        except (IndexError, AttributeError):
            pass

    # Earnings calendar
    if data_package.earnings_calendar:
        try:
            ctx.earnings_date = data_package.earnings_calendar[0].get("date", "")
        except (IndexError, AttributeError):
            pass

    # Evidence count from OPRMS
    if data_package.company_record and data_package.company_record.oprms:
        evidence = data_package.company_record.oprms.get("evidence", [])
        ctx.evidence_count = len(evidence)

    return ctx.to_dict()


# ---------------------------------------------------------------------------
# FreshnessLevel + FreshnessReport
# ---------------------------------------------------------------------------

class FreshnessLevel(str, Enum):
    GREEN = "GREEN"     # Analysis still valid
    YELLOW = "YELLOW"   # Some conditions changed, review suggested
    RED = "RED"         # Significant change, reanalysis needed


@dataclass
class FreshnessReport:
    """Result of a freshness check for a single ticker."""
    symbol: str
    level: FreshnessLevel = FreshnessLevel.GREEN
    days_since_analysis: int = 0
    reasons: List[str] = field(default_factory=list)
    price_change_pct: Optional[float] = None
    regime_changed: bool = False
    new_earnings_available: bool = False

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "level": self.level.value,
            "days_since_analysis": self.days_since_analysis,
            "reasons": self.reasons,
            "price_change_pct": self.price_change_pct,
            "regime_changed": self.regime_changed,
            "new_earnings_available": self.new_earnings_available,
        }


# ---------------------------------------------------------------------------
# Core: check_freshness
# ---------------------------------------------------------------------------

def _get_current_price(symbol: str) -> Optional[float]:
    """Get latest price from CSV cache."""
    try:
        from src.data.price_fetcher import get_price_df
        df = get_price_df(symbol, max_age_days=7)
        if df is not None and not df.empty:
            return float(df["close"].iloc[-1])
    except Exception as e:
        logger.warning(f"Failed to get current price for {symbol}: {e}")
    return None


def _get_current_regime() -> Optional[str]:
    """Get current macro regime."""
    try:
        from terminal.macro_fetcher import get_macro_snapshot
        snap = get_macro_snapshot()
        if snap:
            return snap.regime
    except Exception as e:
        logger.warning(f"Failed to get macro regime: {e}")
    return None


def _get_latest_earnings_date(symbol: str) -> Optional[str]:
    """Get the most recent earnings date from FMP."""
    try:
        from terminal.tools.registry import get_tool
        tool = get_tool("get_earnings_calendar")
        if tool:
            result = tool.execute({"symbol": symbol})
            if result and isinstance(result, list) and len(result) > 0:
                return result[0].get("date")
    except Exception:
        pass
    return None


def check_freshness(symbol: str) -> FreshnessReport:
    """
    Check whether a ticker's analysis is still fresh.

    Reads the OPRMS rating's analysis_context, compares against
    current market conditions, and returns GREEN/YELLOW/RED.
    """
    symbol = symbol.upper()
    report = FreshnessReport(symbol=symbol)

    oprms = get_oprms(symbol)
    if not oprms:
        report.level = FreshnessLevel.RED
        report.reasons.append("No OPRMS rating found")
        return report

    ctx_data = oprms.get("analysis_context")
    if not ctx_data:
        report.level = FreshnessLevel.RED
        report.reasons.append("No analysis context recorded (legacy analysis)")
        return report

    ctx = AnalysisContext.from_dict(ctx_data)
    now = datetime.now()

    # --- Age check ---
    try:
        analyzed = datetime.fromisoformat(ctx.analyzed_at)
        days = (now - analyzed).days
        report.days_since_analysis = days
    except (ValueError, TypeError):
        report.level = FreshnessLevel.RED
        report.reasons.append("Invalid analysis timestamp")
        return report

    if days >= RED_DAYS:
        report.level = FreshnessLevel.RED
        report.reasons.append(f"Analysis is {days} days old (>{RED_DAYS}d threshold)")
    elif days >= YELLOW_DAYS:
        report.level = FreshnessLevel.YELLOW
        report.reasons.append(f"Analysis is {days} days old (>{YELLOW_DAYS}d threshold)")

    # --- Price change ---
    if ctx.price_at_analysis:
        current_price = _get_current_price(symbol)
        if current_price:
            pct = (current_price - ctx.price_at_analysis) / ctx.price_at_analysis * 100
            report.price_change_pct = round(pct, 2)
            abs_pct = abs(pct)
            if abs_pct >= RED_PRICE_PCT:
                report.level = FreshnessLevel.RED
                report.reasons.append(
                    f"Price changed {pct:+.1f}% since analysis (>{RED_PRICE_PCT}% threshold)"
                )
            elif abs_pct >= YELLOW_PRICE_PCT:
                if report.level != FreshnessLevel.RED:
                    report.level = FreshnessLevel.YELLOW
                report.reasons.append(
                    f"Price changed {pct:+.1f}% since analysis (>{YELLOW_PRICE_PCT}% threshold)"
                )

    # --- Regime change ---
    if ctx.regime:
        current_regime = _get_current_regime()
        if current_regime and current_regime != ctx.regime:
            report.regime_changed = True
            if report.level != FreshnessLevel.RED:
                report.level = FreshnessLevel.YELLOW
            report.reasons.append(
                f"Regime changed: {ctx.regime} → {current_regime}"
            )

    # --- New earnings ---
    if ctx.earnings_date:
        latest_earnings = _get_latest_earnings_date(symbol)
        if latest_earnings and latest_earnings != ctx.earnings_date:
            try:
                old_date = datetime.strptime(ctx.earnings_date, "%Y-%m-%d")
                new_date = datetime.strptime(latest_earnings, "%Y-%m-%d")
                if new_date > old_date:
                    report.new_earnings_available = True
                    report.level = FreshnessLevel.RED
                    report.reasons.append(
                        f"New earnings available: {latest_earnings} (was {ctx.earnings_date})"
                    )
            except ValueError:
                pass

    # If no reasons found, it's GREEN
    if not report.reasons:
        report.reasons.append("Analysis is current")

    return report


def check_all_freshness() -> List[FreshnessReport]:
    """
    Check freshness for all companies with OPRMS ratings.

    Returns list sorted by severity: RED → YELLOW → GREEN.
    """
    companies = list_all_companies()
    reports = []

    for sym in companies:
        oprms = get_oprms(sym)
        if oprms:  # Only check companies with ratings
            try:
                report = check_freshness(sym)
                reports.append(report)
            except Exception as e:
                logger.warning(f"Freshness check failed for {sym}: {e}")
                reports.append(FreshnessReport(
                    symbol=sym,
                    level=FreshnessLevel.RED,
                    reasons=[f"Check failed: {e}"],
                ))

    # Sort: RED first, then YELLOW, then GREEN
    severity = {FreshnessLevel.RED: 0, FreshnessLevel.YELLOW: 1, FreshnessLevel.GREEN: 2}
    reports.sort(key=lambda r: severity[r.level])
    return reports


# ---------------------------------------------------------------------------
# Timing Refresh — lightweight re-score (keep DNA, update Timing)
# ---------------------------------------------------------------------------

def prepare_timing_refresh_prompt(symbol: str) -> Optional[dict]:
    """
    Build a single LLM prompt to re-evaluate Timing only (preserve DNA).

    Returns a dict with 'prompt' and 'context' keys, or None if no OPRMS exists.
    """
    symbol = symbol.upper()

    oprms = get_oprms(symbol)
    if not oprms:
        return None

    dna = oprms.get("dna", "?")
    old_timing = oprms.get("timing", "?")
    old_coeff = oprms.get("timing_coeff", "?")
    evidence = oprms.get("evidence", [])
    bucket = oprms.get("investment_bucket", "")

    # Current price + indicators
    current_price = _get_current_price(symbol)
    price_at_analysis = None
    ctx_data = oprms.get("analysis_context")
    if ctx_data:
        price_at_analysis = ctx_data.get("price_at_analysis")

    price_change_str = ""
    if current_price and price_at_analysis:
        pct = (current_price - price_at_analysis) / price_at_analysis * 100
        price_change_str = f"价格变动: ${price_at_analysis:.2f} → ${current_price:.2f} ({pct:+.1f}%)"

    # Indicators
    indicators_str = ""
    try:
        from src.data.price_fetcher import get_price_df
        from src.indicators.pmarp import calculate_pmarp
        from src.indicators.rvol import calculate_rvol
        df = get_price_df(symbol, max_age_days=7)
        if df is not None and not df.empty:
            pmarp_result = calculate_pmarp(df)
            rvol_result = calculate_rvol(df)
            lines = []
            if pmarp_result:
                lines.append(f"PMARP: {pmarp_result.get('current', 'N/A')}%")
            if rvol_result:
                lines.append(f"RVOL: {rvol_result.get('current', 'N/A')}σ")
            indicators_str = "\n".join(lines)
    except Exception:
        pass

    # Macro
    macro_str = ""
    current_regime = _get_current_regime()
    if current_regime:
        macro_str = f"当前 Regime: {current_regime}"

    # Kill conditions
    kc = get_kill_conditions(symbol)
    kc_str = ""
    if kc:
        kc_lines = [f"- {c.get('description', str(c))}" for c in kc]
        kc_str = "Kill Conditions:\n" + "\n".join(kc_lines)

    prompt = f"""你是未来资本的 Timing 刷新评估员。

## 任务
只重新评估 **{symbol}** 的 Timing（时机系数），DNA 评级保持不变。

## 当前 OPRMS
- DNA: {dna}（不变）
- Timing: {old_timing}
- Timing 系数: {old_coeff}
- 投资类型: {bucket}
- Evidence: {', '.join(str(e) for e in evidence[:5])}

## 当前市场状态
{price_change_str}
{indicators_str}
{macro_str}
{kc_str}

## Timing 评级标准
- S (千载难逢, 1.0-1.5): 历史性时刻，暴跌坑底或重大突破
- A (趋势确立, 0.8-1.0): 主升浪确认，右侧突破
- B (正常波动, 0.4-0.6): 回调支撑，震荡区间
- C (垃圾时间, 0.1-0.3): 左侧磨底，无催化剂

请输出严格 JSON（无其他文字）：
```json
{{
    "timing": "B",
    "timing_coeff": 0.5,
    "rationale": "简要理由...",
    "needs_full_reanalysis": false,
    "reanalysis_reason": ""
}}
```

如果发现基本面可能已根本性变化（如重大并购、管理层更换、业务模式转型），
请设 `needs_full_reanalysis: true` 并说明原因。"""

    return {
        "symbol": symbol,
        "prompt": prompt,
        "context": {
            "current_price": current_price,
            "price_at_analysis": price_at_analysis,
            "dna": dna,
            "old_timing": old_timing,
            "old_coeff": old_coeff,
            "current_regime": current_regime,
        },
    }


def apply_timing_refresh(symbol: str, refresh_result: dict) -> None:
    """
    Apply timing refresh result — update OPRMS, preserve DNA.

    Args:
        symbol: Stock ticker
        refresh_result: Dict with timing, timing_coeff, rationale, etc.
    """
    symbol = symbol.upper()
    oprms = get_oprms(symbol)
    if not oprms:
        raise ValueError(f"No OPRMS found for {symbol}")

    # Preserve DNA, update timing
    oprms["timing"] = refresh_result["timing"]
    oprms["timing_coeff"] = refresh_result["timing_coeff"]
    oprms["timing_rationale"] = refresh_result.get("rationale", "")

    # Rebuild analysis_context with current data
    current_price = _get_current_price(symbol)
    current_regime = _get_current_regime()

    ctx = AnalysisContext(
        analyzed_at=datetime.now().isoformat(),
        price_at_analysis=current_price,
        price_date=datetime.now().strftime("%Y-%m-%d"),
        regime=current_regime or "",
        depth="timing_refresh",
        source="timing_refresh",
        evidence_count=len(oprms.get("evidence", [])),
    )

    # Preserve old context's fundamental_date and earnings_date
    old_ctx = oprms.get("analysis_context", {})
    if old_ctx:
        ctx.fundamental_date = old_ctx.get("fundamental_date", "")
        ctx.earnings_date = old_ctx.get("earnings_date", "")
        ctx.vix = None  # Will be set from current macro if available
    try:
        from terminal.macro_fetcher import get_macro_snapshot
        snap = get_macro_snapshot()
        if snap:
            ctx.vix = snap.vix
    except Exception:
        pass

    oprms["analysis_context"] = ctx.to_dict()

    # Save (writes to oprms.json + oprms_changelog.jsonl)
    save_oprms(symbol, oprms)
    logger.info(
        f"Timing refresh for {symbol}: {refresh_result.get('timing')} "
        f"(coeff={refresh_result.get('timing_coeff')})"
    )


# ---------------------------------------------------------------------------
# Evolution Timeline
# ---------------------------------------------------------------------------

def get_evolution_timeline(symbol: str) -> dict:
    """
    Build a structured timeline of OPRMS changes from changelog.

    Returns dict with 'timeline' list and 'summary'.
    """
    symbol = symbol.upper()
    history = get_oprms_history(symbol)

    if not history:
        return {
            "symbol": symbol,
            "timeline": [],
            "summary": {"dna_stable": True, "timing_changes": 0},
        }

    timeline = []
    prev_entry = None

    for entry in history:
        row = {
            "date": entry.get("updated_at", "")[:10],
            "timestamp": entry.get("updated_at", ""),
            "dna": entry.get("dna", "?"),
            "timing": entry.get("timing", "?"),
            "timing_coeff": entry.get("timing_coeff"),
            "source": entry.get("analysis_context", {}).get("source", "unknown"),
            "price": None,
            "regime": None,
            "delta": {
                "timing_coeff_delta": None,
                "price_change_pct": None,
            },
        }

        # Extract price and regime from analysis_context
        ctx = entry.get("analysis_context", {})
        if ctx:
            row["price"] = ctx.get("price_at_analysis")
            row["regime"] = ctx.get("regime")

        # Calculate deltas from previous entry
        if prev_entry:
            old_coeff = prev_entry.get("timing_coeff")
            new_coeff = entry.get("timing_coeff")
            if old_coeff is not None and new_coeff is not None:
                row["delta"]["timing_coeff_delta"] = round(new_coeff - old_coeff, 3)

            old_price = prev_entry.get("analysis_context", {}).get("price_at_analysis")
            new_price = ctx.get("price_at_analysis")
            if old_price and new_price:
                pct = (new_price - old_price) / old_price * 100
                row["delta"]["price_change_pct"] = round(pct, 2)

        timeline.append(row)
        prev_entry = entry

    # Summary
    dna_values = {e.get("dna") for e in history if e.get("dna")}
    timing_coeffs = [e.get("timing_coeff") for e in history if e.get("timing_coeff") is not None]
    timing_changes = 0
    for i in range(1, len(timing_coeffs)):
        if timing_coeffs[i] != timing_coeffs[i - 1]:
            timing_changes += 1

    summary = {
        "dna_stable": len(dna_values) <= 1,
        "timing_changes": timing_changes,
        "first_analysis": history[0].get("updated_at", "")[:10] if history else "",
        "latest_analysis": history[-1].get("updated_at", "")[:10] if history else "",
        "total_entries": len(history),
    }

    return {
        "symbol": symbol,
        "timeline": timeline,
        "summary": summary,
    }


def format_evolution_text(timeline_data: dict) -> str:
    """Format evolution timeline as markdown table."""
    symbol = timeline_data.get("symbol", "?")
    timeline = timeline_data.get("timeline", [])
    summary = timeline_data.get("summary", {})

    if not timeline:
        return f"## {symbol} — 无评级历史\n\n尚未进行分析。"

    lines = [
        f"## {symbol} — OPRMS 演变时间线",
        "",
        "| 日期 | DNA | Timing | 系数 | 价格 | Regime | 来源 | 系数Δ | 价格Δ |",
        "|------|-----|--------|------|------|--------|------|-------|-------|",
    ]

    for row in timeline:
        date = row["date"][:10] if row["date"] else "?"
        dna = row.get("dna", "?")
        timing = row.get("timing", "?")
        coeff = f"{row['timing_coeff']:.2f}" if row.get("timing_coeff") is not None else "?"
        price = f"${row['price']:.1f}" if row.get("price") else "—"
        regime = row.get("regime") or "—"
        source = row.get("source", "?")

        delta = row.get("delta", {})
        coeff_d = f"{delta['timing_coeff_delta']:+.3f}" if delta.get("timing_coeff_delta") is not None else "—"
        price_d = f"{delta['price_change_pct']:+.1f}%" if delta.get("price_change_pct") is not None else "—"

        lines.append(f"| {date} | {dna} | {timing} | {coeff} | {price} | {regime} | {source} | {coeff_d} | {price_d} |")

    lines.append("")
    lines.append(f"**DNA 稳定**: {'是' if summary.get('dna_stable') else '否'} | "
                 f"**Timing 变更**: {summary.get('timing_changes', 0)} 次 | "
                 f"**记录**: {summary.get('total_entries', 0)} 条")

    return "\n".join(lines)
