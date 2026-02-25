"""
Analysis pipeline — ticker → data → lens prompts → debate prompts → memo → score → OPRMS.

Four depth levels:
- quick:    Data + indicators only (~5 sec, $0)
- standard: + macro briefing + 5-lens analysis prompts (~1 min, ~$2)
- full:     + 5-round debate + memo + scoring (~5 min, ~$13-15)
- alpha:    + Layer 2 second-order thinking (3 prompts, ~$3-5 extra)

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

    # Macro Briefing (Stage 0)
    macro_briefing: Optional[str] = None  # Claude-generated macro narrative
    macro_signals: list = field(default_factory=list)  # CrossAssetSignal dicts (fired only)

    # FMP enrichment (P0)
    analyst_estimates: Optional[list] = None
    analyst_recommendations: Optional[list] = None
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

        # Recent ratios (last 4 quarters)
        if self.ratios:
            latest = self.ratios[:4]
            sections.append(
                "### Financial Ratios (Last 4 Quarters)\n"
                + json.dumps(latest, indent=2, default=str)
            )

        # Income statement (last 4 quarters)
        if self.income:
            latest = self.income[:4]
            sections.append(
                "### Income Statement (Last 4 Quarters)\n"
                + json.dumps(latest, indent=2, default=str)
            )

        # Price
        if self.price:
            source = self.price.get("price_source", "cache")
            price_line = (
                f"- Latest: ${self.price.get('latest_close', 'N/A')} "
                f"({self.price.get('latest_date', 'N/A')}) [source: {source}]"
            )
            if source == "realtime" and self.price.get("price_deviation"):
                rt = self.price.get("realtime_price", "N/A")
                dev = self.price.get("price_deviation", 0)
                # Show what the cache had before replacement
                price_line += f"\n- Cache was stale (deviation: {dev}%, replaced with realtime ${rt})"
            sections.append(
                f"### Price Data\n"
                f"{price_line}\n"
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

        # Macro environment (injected into all lens prompts)
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

        # Analyst rating distribution (from grades data)
        if self.analyst_recommendations:
            # Deduplicate: keep latest grade per analyst firm
            latest_by_firm: dict = {}
            for rec in self.analyst_recommendations:
                firm = rec.get("gradingCompany", "")
                if firm and firm not in latest_by_firm:
                    latest_by_firm[firm] = rec.get("newGrade", "").strip()

            if latest_by_firm:
                # Map grade strings to buckets
                buy_keywords = {"buy", "outperform", "overweight", "positive", "accumulate", "strong buy", "strong-buy", "top pick"}
                hold_keywords = {"hold", "neutral", "equal-weight", "equal weight", "market perform", "sector perform", "peer perform", "in-line", "inline"}
                sell_keywords = {"sell", "underperform", "underweight", "negative", "reduce", "strong sell"}

                buy_count = hold_count = sell_count = 0
                for grade_str in latest_by_firm.values():
                    g = grade_str.lower()
                    if any(kw in g for kw in buy_keywords):
                        buy_count += 1
                    elif any(kw in g for kw in sell_keywords):
                        sell_count += 1
                    elif any(kw in g for kw in hold_keywords):
                        hold_count += 1
                    # else: unknown grade, skip

                total = buy_count + hold_count + sell_count
                if total > 0:
                    lines = ["### Analyst Rating Distribution"]
                    lines.append(f"- Buy/Outperform: {buy_count} ({buy_count/total:.0%})")
                    lines.append(f"- Hold/Neutral: {hold_count} ({hold_count/total:.0%})")
                    lines.append(f"- Sell/Underperform: {sell_count} ({sell_count/total:.0%})")
                    lines.append(f"- Total Analysts: {total}")
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
                key=lambda t: abs((t.get("securitiesTransacted") or 0) * (t.get("price") or 0)),
                reverse=True,
            )[:5]
            for t in sorted_trades:
                date = t.get("filingDate", t.get("transactionDate", "N/A"))
                name = t.get("reportingName", "Unknown")
                tx_type = t.get("transactionType", "N/A")
                shares = t.get("securitiesTransacted") or 0
                price = t.get("price") or 0
                value = abs(shares * price)
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

    # Auto-admit: ensure ticker is in pool and fundamentals are cached
    try:
        from src.data.pool_manager import ensure_in_pool
        from src.data.fundamental_fetcher import ensure_fundamentals_cached
        pool_info = ensure_in_pool(symbol)
        if pool_info:
            ensure_fundamentals_cached(symbol)
            if scratchpad:
                source = pool_info.get("source", "screener")
                scratchpad.log_reasoning(
                    "auto_admit",
                    f"{symbol} in pool (source: {source})"
                )
    except Exception as e:
        logger.warning(f"Auto-admit failed for {symbol}: {e}")
        if scratchpad:
            scratchpad.log_reasoning("error", f"Auto-admit failed: {e}")

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
                    "price_days": stock.get("price", {}).get("records", 0) if stock.get("price") else 0,
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

    # Realtime price sanity check
    if pkg.price and pkg.price.get("latest_close"):
        try:
            from src.data.fmp_client import fmp_client as _fmp
            realtime = _fmp.get_realtime_price(symbol)
            if realtime:
                cached_price = pkg.price["latest_close"]
                deviation = abs(cached_price - realtime) / realtime
                pkg.price["realtime_price"] = realtime
                pkg.price["price_deviation"] = round(deviation * 100, 2)
                if deviation > 0.02:  # >2% deviation
                    logger.warning(
                        f"{symbol}: 缓存价 ${cached_price:.2f} vs 实时 ${realtime:.2f} "
                        f"(偏差 {deviation:.1%}), 使用实时价格"
                    )
                    pkg.price["latest_close"] = realtime
                    pkg.price["price_source"] = "realtime"
                else:
                    pkg.price["price_source"] = "cache"
                if scratchpad:
                    scratchpad.log_tool_call(
                        "get_realtime_price",
                        {"symbol": symbol},
                        {
                            "realtime": realtime,
                            "cached": cached_price,
                            "deviation_pct": pkg.price["price_deviation"],
                            "source": pkg.price["price_source"],
                        }
                    )
        except Exception as e:
            logger.warning(f"Realtime price check failed for {symbol}: {e}")
            if scratchpad:
                scratchpad.log_reasoning("error", f"Realtime price check failed: {e}")

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

    # Cross-asset signal detection (runs after macro fetch)
    if pkg.macro:
        try:
            from terminal.macro_briefing import detect_signals
            signals = detect_signals(pkg.macro)
            pkg.macro_signals = [s.__dict__ for s in signals if s.fired]
            if scratchpad and pkg.macro_signals:
                scratchpad.log_reasoning(
                    "macro_signals",
                    f"Detected {len(pkg.macro_signals)} active signals: "
                    + ", ".join(s['label'] for s in pkg.macro_signals)
                )
        except Exception as e:
            logger.warning(f"Macro signal detection failed: {e}")

    # FMP enrichment (analyst estimates, insider trades, news, earnings calendar)
    try:
        import terminal.tools  # noqa: F401 — ensure auto-registration
        from terminal.tools.registry import get_registry
        registry = get_registry()
    except Exception as e:
        logger.warning(f"Tool registry not available: {e}")
        registry = None

    if registry:
        # Analyst estimates
        try:
            result = registry.execute("get_analyst_estimates", symbol=symbol, period="quarter", limit=4)
            if result:
                pkg.analyst_estimates = result
                if scratchpad:
                    scratchpad.log_tool_call(
                        "get_analyst_estimates", {"symbol": symbol},
                        {"count": len(pkg.analyst_estimates)}
                    )
        except Exception as e:
            logger.warning(f"Analyst estimates fetch failed for {symbol}: {e}")
            if scratchpad:
                scratchpad.log_reasoning("error", f"Analyst estimates fetch failed: {e}")

        # Analyst recommendations (rating distribution)
        try:
            result = registry.execute("get_analyst_recommendations", symbol=symbol)
            if result:
                pkg.analyst_recommendations = result
                if scratchpad:
                    scratchpad.log_tool_call(
                        "get_analyst_recommendations", {"symbol": symbol},
                        {"count": len(pkg.analyst_recommendations)}
                    )
        except Exception as e:
            logger.warning(f"Analyst recommendations fetch failed for {symbol}: {e}")
            if scratchpad:
                scratchpad.log_reasoning("error", f"Analyst recommendations fetch failed: {e}")

        # Insider trades
        try:
            result = registry.execute("get_insider_trades", symbol=symbol, limit=20)
            if result:
                pkg.insider_trades = result
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
            if result:
                pkg.news = result
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
            if result:
                pkg.earnings_calendar = [e for e in result if e.get("symbol") == symbol]
                if scratchpad:
                    scratchpad.log_tool_call(
                        "get_earnings_calendar", {"from_date": from_date, "to_date": to_date},
                        {"total": len(result), "filtered": len(pkg.earnings_calendar or [])}
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
            f"Price data points: {pkg.price.get('records', 0) if pkg.price else 0}, "
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
    Phase 2: Generate 5 lens analysis prompts with injected data context.

    Macro analysis is handled by Stage 0 briefing (not a lens).
    Returns list of {lens_name, prompt} dicts. Claude runs each in conversation.
    """
    if lenses is None:
        lenses = get_all_lenses()

    from knowledge.prompts.provenance import DATA_PROVENANCE_INSTRUCTIONS

    context = {"Financial Data": data_package.format_context()}

    # Add existing analyses for reference
    if data_package.company_record and data_package.company_record.analyses:
        recent = data_package.company_record.analyses[:3]
        ref_lines = [f"- {a['filename']} ({a['modified']})" for a in recent]
        context["Previous Analyses"] = "\n".join(ref_lines)

    prompts = []
    for lens in lenses:
        prompt = format_prompt(lens, symbol, context) + DATA_PROVENANCE_INSTRUCTIONS
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


# ---------------------------------------------------------------------------
# Alpha Layer — Layer 2 Second-Order Thinking (Phase 6)
# ---------------------------------------------------------------------------

def prepare_alpha_prompts(
    symbol: str,
    data_package: DataPackage,
    l1_memo_summary: str,
    l1_verdict: str,
    l1_key_forces: str,
    l1_oprms: Optional[dict] = None,
) -> List[Dict[str, Any]]:
    """
    Phase 6: Generate 3 sequential Layer 2 prompts (standard pipeline path).

    NOTE: This is the **standard pipeline** alpha prompt path, used in interactive
    analysis. The **deep pipeline** has a separate path in
    deep_pipeline.build_alpha_agent_prompt() that embeds frameworks with <<PLACEHOLDER>>
    markers for file-driven agents. Both paths use the same underlying generators
    (knowledge.alpha.*) but with different orchestration strategies.

    Returns list of prompt dicts. Prompts B and C include a prompt_generator
    callable + prompt_args so Claude can render them after receiving previous outputs.

    Args:
        symbol: Ticker
        data_package: Full DataPackage from Phase 1
        l1_memo_summary: Executive summary from L1 memo
        l1_verdict: BUY/HOLD/SELL from L1
        l1_key_forces: 3 key forces from L1 debate
        l1_oprms: Current OPRMS rating dict (or None)
    """
    from knowledge.alpha.red_team import generate_red_team_prompt
    from knowledge.alpha.cycle_pendulum import generate_cycle_prompt
    from knowledge.alpha.asymmetric_bet import generate_bet_prompt

    data_context = data_package.format_context()
    sector = ""
    if data_package.info:
        sector = data_package.info.get("sector", "Unknown")

    macro_briefing = data_package.macro_briefing or ""

    # Prompt A: Red Team — fully rendered, no dependencies
    red_team_prompt = generate_red_team_prompt(
        symbol=symbol,
        memo_summary=l1_memo_summary,
        l1_verdict=l1_verdict,
        l1_key_forces=l1_key_forces,
        data_context=data_context,
    )

    prompts = [
        {
            "phase": 1,
            "phase_cn": "红队试炼",
            "name": "Red Team Gauntlet",
            "sequence": "A",
            "prompt": red_team_prompt,
            "depends_on": None,
        },
        {
            "phase": 2,
            "phase_cn": "周期钟摆定位",
            "name": "Cycle & Pendulum",
            "sequence": "B",
            "prompt": None,  # Deferred — needs red_team_summary
            "depends_on": "A",
            "prompt_generator": "generate_cycle_prompt",
            "prompt_args": {
                "symbol": symbol,
                "sector": sector,
                "data_context": data_context,
                "macro_briefing": macro_briefing,
                # red_team_summary: Claude fills after Phase A
            },
        },
        {
            "phase": 3,
            "phase_cn": "非对称赌注",
            "name": "Asymmetric Bet",
            "sequence": "C",
            "prompt": None,  # Deferred — needs red_team + cycle summaries
            "depends_on": "B",
            "prompt_generator": "generate_bet_prompt",
            "prompt_args": {
                "symbol": symbol,
                "data_context": data_context,
                "l1_oprms": l1_oprms,
                "l1_verdict": l1_verdict,
                "current_price": data_package.latest_price,
                # red_team_summary, cycle_summary: Claude fills after Phases A & B
            },
        },
    ]

    return prompts
