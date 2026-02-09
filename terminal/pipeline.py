"""
Analysis pipeline — ticker → data → lens prompts → debate prompts → memo → score → OPRMS.

Three depth levels:
- quick:    Data + indicators only (~5 sec, $0)
- standard: + 6-lens analysis prompts (~1 min, ~$2)
- full:     + 5-round debate + memo + scoring (~5 min, ~$13-15)

Claude IS the analyst. This module generates structured prompts with injected data
context. Claude responds in conversation to complete the analysis.
"""
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from knowledge.philosophies.base import get_all_lenses, format_prompt, InvestmentLens
from knowledge.debate.protocol import generate_round_prompt, get_round
from knowledge.debate.director_guide import get_director_prompt
from knowledge.memo.template import generate_memo_skeleton, INVESTMENT_BUCKETS
from knowledge.memo.scorer import (
    SCORING_RUBRIC,
    check_completeness,
    check_writing_standards,
)
from knowledge.oprms.models import DNARating, TimingRating, OPRMSRating
from knowledge.oprms.ratings import calculate_position_size

from terminal.company_db import get_company_record, CompanyRecord
from terminal.scratchpad import AnalysisScratchpad

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data Collection (Phase 1)
# ---------------------------------------------------------------------------

@dataclass
class DataPackage:
    """All data we have about a ticker, assembled for analysis."""
    symbol: str
    collected_at: str = ""

    # From Data Desk
    info: Optional[dict] = None       # Stock pool info (name, sector, market cap)
    profile: Optional[dict] = None    # Company profile (description, CEO, etc.)
    fundamentals: Optional[dict] = None  # P/E, ROE, margins
    ratios: list = field(default_factory=list)  # Financial ratios history
    income: list = field(default_factory=list)  # Income statement history
    price: Optional[dict] = None      # Recent price data

    # From Indicators
    indicators: Optional[dict] = None  # PMARP, RVOL signals

    # From Macro Desk
    macro: Optional[Any] = None  # MacroSnapshot (imported at runtime to avoid circular)

    # From Company DB
    company_record: Optional[CompanyRecord] = None

    # FMP enrichment (P0)
    analyst_estimates: Optional[list] = None
    earnings_calendar: Optional[list] = None
    insider_trades: list = field(default_factory=list)
    news: list = field(default_factory=list)

    @property
    def has_financials(self) -> bool:
        return self.fundamentals is not None or len(self.ratios) > 0

    @property
    def latest_price(self) -> Optional[float]:
        if self.price and self.price.get("latest_close"):
            return self.price["latest_close"]
        return None

    def format_context(self) -> str:
        """Format all data as a readable context block for Claude prompts."""
        sections = []

        # Company info
        if self.info:
            name = self.info.get("companyName", self.symbol)
            mcap = self.info.get("marketCap")
            mcap_str = f"${mcap / 1e9:.0f}B" if mcap else "N/A"
            sections.append(
                f"### Company: {name} ({self.symbol})\n"
                f"- Sector: {self.info.get('sector', 'N/A')}\n"
                f"- Industry: {self.info.get('industry', 'N/A')}\n"
                f"- Market Cap: {mcap_str}\n"
                f"- Exchange: {self.info.get('exchange', 'N/A')}"
            )

        # Profile description
        if self.profile and self.profile.get("description"):
            desc = self.profile["description"][:500]
            sections.append(f"### Business Description\n{desc}")

        # Fundamentals
        if self.fundamentals:
            f = self.fundamentals
            lines = ["### Key Fundamentals"]
            for key in ["pe", "roe", "grossMargin", "operatingMargin", "netMargin",
                        "currentRatio", "debtToEquity", "revenueGrowth", "epsGrowth"]:
                val = f.get(key)
                if val is not None:
                    lines.append(f"- {key}: {val}")
            sections.append("\n".join(lines))

        # Recent ratios (latest 2 periods)
        if self.ratios:
            latest = self.ratios[:2]
            sections.append(
                "### Financial Ratios (Recent)\n"
                + json.dumps(latest, indent=2, default=str)
            )

        # Income statement (latest 2 periods)
        if self.income:
            latest = self.income[:2]
            sections.append(
                "### Income Statement (Recent)\n"
                + json.dumps(latest, indent=2, default=str)
            )

        # Price
        if self.price:
            sections.append(
                f"### Price Data\n"
                f"- Latest: ${self.price.get('latest_close', 'N/A')} "
                f"({self.price.get('latest_date', 'N/A')})\n"
                f"- Records: {self.price.get('records', 0)} days"
            )

        # Indicators
        if self.indicators:
            ind = self.indicators
            lines = ["### Technical Indicators"]
            if "pmarp" in ind:
                pmarp = ind["pmarp"]
                lines.append(
                    f"- PMARP: {pmarp.get('current', 'N/A')}% "
                    f"(signal: {pmarp.get('signal', 'N/A')})"
                )
            if "rvol" in ind:
                rvol = ind["rvol"]
                lines.append(
                    f"- RVOL: {rvol.get('current', 'N/A')}σ "
                    f"(signal: {rvol.get('signal', 'N/A')})"
                )
            signals = ind.get("signals", [])
            if signals:
                lines.append(f"- Active signals: {', '.join(signals)}")
            sections.append("\n".join(lines))

        # Existing OPRMS rating
        if self.company_record and self.company_record.oprms:
            r = self.company_record.oprms
            sections.append(
                f"### Existing OPRMS Rating\n"
                f"- DNA: {r.get('dna', 'N/A')} | Timing: {r.get('timing', 'N/A')}\n"
                f"- Coefficient: {r.get('timing_coeff', 'N/A')}\n"
                f"- Bucket: {r.get('investment_bucket', 'N/A')}\n"
                f"- Updated: {r.get('updated_at', 'N/A')}"
            )

        # Existing kill conditions
        if self.company_record and self.company_record.kill_conditions:
            kc = self.company_record.kill_conditions
            lines = ["### Active Kill Conditions"]
            for i, c in enumerate(kc, 1):
                desc = c.get("description", str(c))
                lines.append(f"{i}. {desc}")
            sections.append("\n".join(lines))

        # Macro environment (injected into all 6 lens prompts)
        if self.macro is not None:
            sections.append(self.macro.format_for_prompt())

        # Analyst consensus
        if self.analyst_estimates:
            lines = ["### Analyst Consensus"]
            for est in self.analyst_estimates[:2]:
                date = est.get("date", "N/A")
                eps_avg = est.get("estimatedEpsAvg", "N/A")
                eps_low = est.get("estimatedEpsLow", "N/A")
                eps_high = est.get("estimatedEpsHigh", "N/A")
                rev = est.get("estimatedRevenueAvg")
                rev_str = f"${rev / 1e9:.1f}B" if rev else "N/A"
                lines.append(
                    f"- {date}: EPS {eps_low}/{eps_avg}/{eps_high} (low/avg/high), "
                    f"Revenue {rev_str}"
                )
            sections.append("\n".join(lines))

        # Upcoming earnings
        if self.earnings_calendar:
            ec = self.earnings_calendar[0]
            lines = ["### Upcoming Earnings"]
            lines.append(
                f"- Date: {ec.get('date', 'N/A')}\n"
                f"- EPS Estimate: {ec.get('epsEstimated', 'N/A')}\n"
                f"- Revenue Estimate: {ec.get('revenueEstimated', 'N/A')}"
            )
            sections.append("\n".join(lines))

        # Recent insider activity
        if self.insider_trades:
            lines = ["### Recent Insider Activity"]
            sorted_trades = sorted(
                self.insider_trades,
                key=lambda t: abs(t.get("securitiesTransacted", 0) * (t.get("price", 0) or 0)),
                reverse=True,
            )[:5]
            for t in sorted_trades:
                date = t.get("filingDate", t.get("transactionDate", "N/A"))
                name = t.get("reportingName", "Unknown")
                tx_type = t.get("transactionType", "N/A")
                shares = t.get("securitiesTransacted", 0)
                price = t.get("price", 0)
                value = abs(shares * (price or 0))
                value_str = f"${value:,.0f}" if value else "N/A"
                lines.append(f"- {date}: {name} — {tx_type} {abs(shares):,.0f} shares @ ${price:.2f} ({value_str})")
            sections.append("\n".join(lines))

        # Recent news
        if self.news:
            lines = ["### Recent News"]
            for n in self.news[:5]:
                date = n.get("publishedDate", "N/A")
                if "T" in str(date):
                    date = str(date).split("T")[0]
                title = n.get("title", "N/A")
                lines.append(f"- {date}: {title}")
            sections.append("\n".join(lines))

        return "\n\n".join(sections)


def collect_data(
    symbol: str,
    price_days: int = 60,
    scratchpad: Optional[AnalysisScratchpad] = None,
) -> DataPackage:
    """
    Phase 1: Collect all available data for a ticker.

    Calls Data Desk + Indicators + Company DB. No API calls to LLM.

    Args:
        symbol: Stock ticker
        price_days: Number of days of price history
        scratchpad: Optional scratchpad for logging
    """
    symbol = symbol.upper()
    pkg = DataPackage(
        symbol=symbol,
        collected_at=datetime.now().isoformat(),
    )

    if scratchpad:
        scratchpad.log_reasoning(
            "data_collection_start",
            f"Starting data collection for {symbol} ({price_days} days price history)"
        )

    # Data Desk: stock data
    try:
        from src.data.data_query import get_stock_data
        stock = get_stock_data(symbol, price_days=price_days)

        if scratchpad:
            scratchpad.log_tool_call(
                "get_stock_data",
                {"symbol": symbol, "price_days": price_days},
                {
                    "has_info": stock.get("info") is not None,
                    "has_profile": stock.get("profile") is not None,
                    "has_fundamentals": stock.get("fundamentals") is not None,
                    "ratios_count": len(stock.get("ratios", [])),
                    "income_count": len(stock.get("income", [])),
                    "price_days": len(stock.get("price", [])) if stock.get("price") else 0,
                }
            )

        pkg.info = stock.get("info")
        pkg.profile = stock.get("profile")
        pkg.fundamentals = stock.get("fundamentals")
        pkg.ratios = stock.get("ratios", [])
        pkg.income = stock.get("income", [])
        pkg.price = stock.get("price")
    except Exception as e:
        logger.warning(f"Data Desk query failed for {symbol}: {e}")
        if scratchpad:
            scratchpad.log_reasoning("error", f"Data Desk query failed: {e}")

    # Indicators
    try:
        from src.indicators.engine import run_indicators
        pkg.indicators = run_indicators(symbol)

        if scratchpad and pkg.indicators:
            scratchpad.log_tool_call(
                "run_indicators",
                {"symbol": symbol},
                {
                    "indicator_count": len(pkg.indicators),
                    "indicators": list(pkg.indicators.keys()) if isinstance(pkg.indicators, dict) else None,
                }
            )
    except Exception as e:
        logger.warning(f"Indicator run failed for {symbol}: {e}")
        if scratchpad:
            scratchpad.log_reasoning("error", f"Indicator run failed: {e}")

    # Company DB
    pkg.company_record = get_company_record(symbol)

    # Macro environment (FRED data)
    try:
        from terminal.macro_fetcher import get_macro_snapshot
        pkg.macro = get_macro_snapshot()
        if scratchpad and pkg.macro:
            scratchpad.log_tool_call(
                "get_macro_snapshot",
                {},
                {
                    "data_sources": pkg.macro.data_source_count,
                    "regime": pkg.macro.regime,
                    "vix": pkg.macro.vix,
                }
            )
    except Exception as e:
        logger.warning(f"Macro data fetch failed: {e}")
        if scratchpad:
            scratchpad.log_reasoning("error", f"Macro data fetch failed: {e}")

    # FMP enrichment (analyst estimates, insider trades, news, earnings calendar)
    try:
        from terminal.tools.registry import get_registry
        registry = get_registry()
    except Exception as e:
        logger.warning(f"Tool registry not available: {e}")
        registry = None

    if registry:
        # Analyst estimates
        try:
            result = registry.execute("get_analyst_estimates", symbol=symbol, period="quarter", limit=4)
            if result.get("success"):
                pkg.analyst_estimates = result.get("data", [])
                if scratchpad:
                    scratchpad.log_tool_call(
                        "get_analyst_estimates", {"symbol": symbol},
                        {"count": len(pkg.analyst_estimates or [])}
                    )
        except Exception as e:
            logger.warning(f"Analyst estimates fetch failed for {symbol}: {e}")
            if scratchpad:
                scratchpad.log_reasoning("error", f"Analyst estimates fetch failed: {e}")

        # Insider trades
        try:
            result = registry.execute("get_insider_trades", symbol=symbol, limit=20)
            if result.get("success"):
                pkg.insider_trades = result.get("data", [])
                if scratchpad:
                    scratchpad.log_tool_call(
                        "get_insider_trades", {"symbol": symbol},
                        {"count": len(pkg.insider_trades)}
                    )
        except Exception as e:
            logger.warning(f"Insider trades fetch failed for {symbol}: {e}")
            if scratchpad:
                scratchpad.log_reasoning("error", f"Insider trades fetch failed: {e}")

        # Stock news
        try:
            result = registry.execute("get_stock_news", tickers=symbol, limit=10)
            if result.get("success"):
                pkg.news = result.get("data", [])
                if scratchpad:
                    scratchpad.log_tool_call(
                        "get_stock_news", {"tickers": symbol},
                        {"count": len(pkg.news)}
                    )
        except Exception as e:
            logger.warning(f"News fetch failed for {symbol}: {e}")
            if scratchpad:
                scratchpad.log_reasoning("error", f"News fetch failed: {e}")

        # Earnings calendar
        try:
            from datetime import timedelta
            today = datetime.now()
            from_date = (today - timedelta(days=30)).strftime("%Y-%m-%d")
            to_date = (today + timedelta(days=30)).strftime("%Y-%m-%d")
            result = registry.execute("get_earnings_calendar", from_date=from_date, to_date=to_date)
            if result.get("success"):
                all_earnings = result.get("data", [])
                pkg.earnings_calendar = [e for e in all_earnings if e.get("symbol") == symbol]
                if scratchpad:
                    scratchpad.log_tool_call(
                        "get_earnings_calendar", {"from_date": from_date, "to_date": to_date},
                        {"total": len(all_earnings), "filtered": len(pkg.earnings_calendar or [])}
                    )
        except Exception as e:
            logger.warning(f"Earnings calendar fetch failed for {symbol}: {e}")
            if scratchpad:
                scratchpad.log_reasoning("error", f"Earnings calendar fetch failed: {e}")

    if scratchpad:
        has_data = pkg.company_record and pkg.company_record.has_data
        scratchpad.log_reasoning(
            "data_collection_complete",
            f"Data collection complete. Has financials: {pkg.has_financials}, "
            f"Company DB record: {has_data}, "
            f"Price data points: {len(pkg.price) if pkg.price else 0}, "
            f"Macro: {'yes' if pkg.macro else 'no'}"
        )

    return pkg


# ---------------------------------------------------------------------------
# Lens Analysis Prompts (Phase 2)
# ---------------------------------------------------------------------------

def prepare_lens_prompts(
    symbol: str,
    data_package: DataPackage,
    lenses: Optional[List[InvestmentLens]] = None,
) -> List[Dict[str, str]]:
    """
    Phase 2: Generate 6 lens analysis prompts with injected data context.

    Returns list of {lens_name, prompt} dicts. Claude runs each in conversation.
    """
    if lenses is None:
        lenses = get_all_lenses()

    context = {"Financial Data": data_package.format_context()}

    # Add existing analyses for reference
    if data_package.company_record and data_package.company_record.analyses:
        recent = data_package.company_record.analyses[:3]
        ref_lines = [f"- {a['filename']} ({a['modified']})" for a in recent]
        context["Previous Analyses"] = "\n".join(ref_lines)

    prompts = []
    for lens in lenses:
        prompt = format_prompt(lens, symbol, context)
        prompts.append({
            "lens_name": lens.name,
            "horizon": lens.horizon,
            "core_metric": lens.core_metric,
            "prompt": prompt,
        })

    return prompts


# ---------------------------------------------------------------------------
# Debate Prompts (Phase 3)
# ---------------------------------------------------------------------------

def prepare_debate_prompts(
    symbol: str,
    lens_analyses: Dict[str, str],
    tensions: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Phase 3: Generate 5-round debate prompts.

    Args:
        symbol: Ticker
        lens_analyses: {lens_name: analysis_text} from Phase 2
        tensions: 3 key tensions (identified by Claude after Phase 2)

    Returns list of {round, analyst_prompt, director_prompt} dicts.
    """
    if tensions is None:
        tensions = [
            "[Tension 1: to be identified after lens analyses]",
            "[Tension 2: to be identified after lens analyses]",
            "[Tension 3: to be identified after lens analyses]",
        ]

    # Build summary of lens analyses for context
    summary_parts = []
    for lens_name, text in lens_analyses.items():
        preview = text[:300] + "..." if len(text) > 300 else text
        summary_parts.append(f"**{lens_name}**: {preview}")
    previous_summary = "\n\n".join(summary_parts)

    rounds = []
    for round_num in range(1, 6):
        analyst_prompt = generate_round_prompt(
            round_num=round_num,
            ticker=symbol,
            lens_name="[All lenses]",
            tensions=tensions,
            previous_summary=previous_summary if round_num >= 2 else "",
        )

        director_prompt = get_director_prompt(symbol, round_num)

        rounds.append({
            "round": round_num,
            "phase": get_round(round_num).phase,
            "title": get_round(round_num).title,
            "analyst_prompt": analyst_prompt,
            "director_prompt": director_prompt,
        })

    return rounds


# ---------------------------------------------------------------------------
# Memo Skeleton & Scoring (Phase 4)
# ---------------------------------------------------------------------------

def prepare_memo_skeleton(symbol: str, bucket: str = "Long-term Compounder") -> str:
    """Generate a memo skeleton pre-filled with ticker and bucket."""
    if bucket not in INVESTMENT_BUCKETS:
        logger.warning(f"Unknown bucket '{bucket}', defaulting to Long-term Compounder")
        bucket = "Long-term Compounder"
    return generate_memo_skeleton(symbol, bucket)


def score_memo(memo_text: str) -> dict:
    """
    Run automated memo quality checks.

    Returns {completeness, writing_standards, rubric_reference}.
    Claude does the actual 5D scoring in conversation.
    """
    completeness = check_completeness(memo_text)
    writing = check_writing_standards(memo_text)

    return {
        "completeness": completeness,
        "all_sections_present": all(completeness.values()),
        "missing_sections": [k for k, v in completeness.items() if not v],
        "writing_standards": writing,
        "rubric": {
            dim_id: {
                "weight": dim["weight"],
                "description": dim["description"],
            }
            for dim_id, dim in SCORING_RUBRIC.items()
        },
    }


# ---------------------------------------------------------------------------
# OPRMS Position Sizing (Phase 5)
# ---------------------------------------------------------------------------

def calculate_position(
    symbol: str,
    dna: str,
    timing: str,
    timing_coeff: Optional[float] = None,
    total_capital: float = 1_000_000,
    evidence_count: int = 0,
    apply_regime: bool = True,
) -> dict:
    """
    Calculate OPRMS position size with evidence gate and regime adjustment.

    Regime adjustment: RISK_OFF → ×0.7, CRISIS → ×0.4 (applied before evidence gate).
    Evidence threshold: 3+ primary sources for full position.
    """
    from terminal.regime import get_current_regime, get_regime_adjustment

    dna_enum = DNARating(dna)
    timing_enum = TimingRating(timing)

    result = calculate_position_size(
        total_capital=total_capital,
        dna=dna_enum,
        timing=timing_enum,
        timing_coeff=timing_coeff,
    )
    result.symbol = symbol

    output = result.to_dict()

    # Regime adjustment (applied before evidence gate)
    if apply_regime:
        regime_assessment = get_current_regime()
        regime_mult = get_regime_adjustment(regime_assessment.regime)
        if regime_mult < 1.0:
            output["regime_adjustment"] = {
                "regime": regime_assessment.regime.value,
                "multiplier": regime_mult,
                "confidence": regime_assessment.confidence,
                "rationale": regime_assessment.rationale,
            }
            output["pre_regime_position_pct"] = output["target_position_pct"]
            output["target_position_pct"] = round(
                output["target_position_pct"] * regime_mult, 2
            )
            output["target_position_usd"] = round(
                output["target_position_usd"] * regime_mult, 2
            )

    # Evidence gate
    if evidence_count < 3:
        output["evidence_warning"] = (
            f"Only {evidence_count} sources. Need 3+ primary sources for full position. "
            f"Consider scaling to {evidence_count}/3 = {evidence_count/3:.0%} of target."
        )
        output["evidence_adjusted_pct"] = round(
            output["target_position_pct"] * min(evidence_count / 3, 1.0), 2
        )

    return output
