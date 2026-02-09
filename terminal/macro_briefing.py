"""
Macro Briefing — cross-asset signal detection + briefing prompt generation.

Stage 0 of the analysis pipeline: runs BEFORE the 6 lens analyses.
Detects cross-asset patterns from MacroSnapshot data (pure rules, no LLM),
then generates a structured prompt for Claude to produce a macro narrative.

detect_signals()          → rule-based, milliseconds, testable
generate_briefing_prompt() → template + data → prompt string for Claude
"""
import logging
from dataclasses import dataclass, field
from typing import List, Optional

from terminal.macro_snapshot import MacroSnapshot

logger = logging.getLogger(__name__)


@dataclass
class CrossAssetSignal:
    """A cross-asset pattern detected from macro data."""
    name: str           # e.g. "carry_trade_unwind"
    label: str          # e.g. "Yen Carry Trade Unwind"
    fired: bool         # whether conditions are met
    strength: str       # "STRONG" / "MODERATE" / "WEAK"
    evidence: list = field(default_factory=list)
    narrative_hint: str = ""


# ---------------------------------------------------------------------------
# Signal Detectors (pure functions, each returns a CrossAssetSignal)
# ---------------------------------------------------------------------------

def _detect_carry_trade_unwind(s: MacroSnapshot) -> CrossAssetSignal:
    """
    Yen carry trade unwind: BOJ hiking + USD/JPY falling.
    Strong: BOJ rate > 0.25% AND USDJPY dropped > 5 in 30d
    Moderate: BOJ rate > 0 AND USDJPY dropped > 2 in 30d
    """
    signal = CrossAssetSignal(
        name="carry_trade_unwind",
        label="Yen Carry Trade Unwind",
        fired=False,
        strength="WEAK",
        narrative_hint=(
            "BOJ tightening triggers carry trade unwinding. Yen strengthens, "
            "forcing leveraged positions to de-risk. Watch for: tech/growth selling, "
            "volatility spike, flight to quality."
        ),
    )

    if s.japan_rate is None or s.usdjpy_30d_chg is None:
        return signal

    evidence = []
    boj_hiking = s.japan_rate > 0
    yen_strengthening = s.usdjpy_30d_chg < -2  # USDJPY falling = yen strengthening

    if boj_hiking:
        evidence.append(f"BOJ rate at {s.japan_rate:.2f}%")
    if s.usdjpy_30d_chg < 0:
        evidence.append(f"USD/JPY {s.usdjpy_30d_chg:+.1f} in 30d")

    if boj_hiking and yen_strengthening:
        signal.fired = True
        signal.evidence = evidence
        if s.japan_rate > 0.25 and s.usdjpy_30d_chg < -5:
            signal.strength = "STRONG"
        else:
            signal.strength = "MODERATE"

    return signal


def _detect_credit_stress(s: MacroSnapshot) -> CrossAssetSignal:
    """
    Credit stress: HY spread widening or already wide.
    Fired if: HY > 4% OR HY 30d change > 0.5pp (50bp).
    """
    signal = CrossAssetSignal(
        name="credit_stress",
        label="Credit Stress",
        fired=False,
        strength="WEAK",
        narrative_hint=(
            "High yield spreads widening signals credit risk repricing. "
            "Corporate borrowing costs rising, weaker balance sheets under pressure. "
            "Watch for: financials underperformance, defensive rotation, cash hoarding."
        ),
    )

    if s.hy_spread is None:
        return signal

    evidence = []
    wide = s.hy_spread > 4.0
    spiking = s.hy_spread_30d_chg is not None and s.hy_spread_30d_chg > 0.5

    if wide:
        evidence.append(f"HY spread at {s.hy_spread:.2f}% ({s.hy_spread * 100:.0f}bp)")
    if spiking:
        evidence.append(f"HY spread +{s.hy_spread_30d_chg:.2f}pp in 30d")

    if wide or spiking:
        signal.fired = True
        signal.evidence = evidence
        if wide and spiking:
            signal.strength = "STRONG"
        elif s.hy_spread > 5.0:
            signal.strength = "STRONG"
        else:
            signal.strength = "MODERATE"

    return signal


def _detect_liquidity_drain(s: MacroSnapshot) -> CrossAssetSignal:
    """
    Liquidity drain: Fed balance sheet shrinking + DXY strengthening.
    Fed QT reducing reserves while strong dollar tightens global liquidity.
    """
    signal = CrossAssetSignal(
        name="liquidity_drain",
        label="Liquidity Drain",
        fired=False,
        strength="WEAK",
        narrative_hint=(
            "Fed balance sheet shrinking + strong dollar = global liquidity squeeze. "
            "Risk assets face headwinds from reduced reserves and tighter dollar funding. "
            "Watch for: EM stress, small-cap underperformance, funding rate volatility."
        ),
    )

    if s.fed_bs_30d_chg_pct is None or s.dxy_trend is None:
        return signal

    evidence = []
    fed_shrinking = s.fed_bs_30d_chg_pct < -0.5  # BS shrinking > 0.5% in 30d
    dxy_strong = s.dxy_trend == "STRENGTHENING"

    if fed_shrinking:
        evidence.append(f"Fed BS {s.fed_bs_30d_chg_pct:+.2f}% in 30d")
    if dxy_strong:
        evidence.append(f"DXY {s.dxy_trend}")
        if s.dxy_30d_chg is not None:
            evidence[-1] += f" ({s.dxy_30d_chg:+.1f} in 30d)"

    if fed_shrinking and dxy_strong:
        signal.fired = True
        signal.evidence = evidence
        if s.fed_bs_30d_chg_pct < -1.0:
            signal.strength = "STRONG"
        else:
            signal.strength = "MODERATE"

    return signal


def _detect_reflation(s: MacroSnapshot) -> CrossAssetSignal:
    """
    Reflation trade: rising inflation + rising rates + strong GDP.
    CPI accelerating, 10Y yields rising, economy growing above trend.
    """
    signal = CrossAssetSignal(
        name="reflation",
        label="Reflation Trade",
        fired=False,
        strength="WEAK",
        narrative_hint=(
            "Inflation re-accelerating with strong growth = reflation. "
            "Nominal growth benefits cyclicals, commodities, financials. "
            "Growth/duration stocks face headwind from rising discount rates. "
            "Watch for: value > growth rotation, commodity strength, steepening curve."
        ),
    )

    if s.cpi_yoy is None or s.us10y_30d_chg_bp is None or s.gdp_growth is None:
        return signal

    evidence = []
    cpi_rising = s.cpi_yoy > 3.0
    rates_rising = s.us10y_30d_chg_bp > 20  # 10Y up > 20bp in 30d
    strong_gdp = s.gdp_growth > 2.0

    if cpi_rising:
        evidence.append(f"CPI YoY {s.cpi_yoy:.1f}%")
    if rates_rising:
        evidence.append(f"10Y {s.us10y_30d_chg_bp:+d}bp in 30d")
    if strong_gdp:
        evidence.append(f"GDP {s.gdp_growth:.1f}%")

    if cpi_rising and rates_rising and strong_gdp:
        signal.fired = True
        signal.evidence = evidence
        if s.cpi_yoy > 4.0 and s.us10y_30d_chg_bp > 40:
            signal.strength = "STRONG"
        else:
            signal.strength = "MODERATE"

    return signal


def _detect_risk_rally(s: MacroSnapshot) -> CrossAssetSignal:
    """
    Risk-on rally: low VIX + steepening curve + strong GDP.
    Goldilocks conditions for risk assets.
    """
    signal = CrossAssetSignal(
        name="risk_rally",
        label="Risk-On Rally",
        fired=False,
        strength="WEAK",
        narrative_hint=(
            "Low vol + positive curve + strong growth = goldilocks for risk assets. "
            "Broad risk appetite, beta outperforms. "
            "Watch for: momentum crowding, vol compression complacency, leverage buildup."
        ),
    )

    if s.vix is None or s.spread_10y_2y is None or s.gdp_growth is None:
        return signal

    evidence = []
    low_vix = s.vix < 15
    steep_curve = s.spread_10y_2y > 0.5
    strong_gdp = s.gdp_growth > 2.0

    if low_vix:
        evidence.append(f"VIX {s.vix:.1f}")
    if steep_curve:
        evidence.append(f"10Y-2Y spread {s.spread_10y_2y:+.2f}%")
    if strong_gdp:
        evidence.append(f"GDP {s.gdp_growth:.1f}%")

    if low_vix and steep_curve and strong_gdp:
        signal.fired = True
        signal.evidence = evidence
        if s.vix < 12:
            signal.strength = "STRONG"
        else:
            signal.strength = "MODERATE"

    return signal


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

ALL_DETECTORS = [
    _detect_carry_trade_unwind,
    _detect_credit_stress,
    _detect_liquidity_drain,
    _detect_reflation,
    _detect_risk_rally,
]


def detect_signals(snapshot: MacroSnapshot) -> List[CrossAssetSignal]:
    """
    Run all cross-asset signal detectors against current macro data.

    Returns all signals (fired and not fired) for transparency.
    Pure rules, no LLM calls, runs in milliseconds.
    """
    signals = []
    for detector in ALL_DETECTORS:
        try:
            signal = detector(snapshot)
            signals.append(signal)
        except Exception as e:
            logger.warning("Signal detector %s failed: %s", detector.__name__, e)
    return signals


def generate_briefing_prompt(
    snapshot: MacroSnapshot,
    signals: List[CrossAssetSignal],
) -> str:
    """
    Generate the macro briefing prompt for Claude to respond to.

    This prompt is run BEFORE the 6 lens analyses. Claude's response
    becomes the macro narrative injected into all subsequent lens prompts.
    """
    # Format fired signals
    active = [s for s in signals if s.fired]
    if active:
        signal_lines = []
        for s in active:
            signal_lines.append(
                f"**{s.label}** ({s.strength})\n"
                f"  - Evidence: {'; '.join(s.evidence)}\n"
                f"  - Hint: {s.narrative_hint}"
            )
        formatted_signals = "\n\n".join(signal_lines)
    else:
        formatted_signals = "No strong cross-asset signals detected. Markets may be in a transitional or range-bound state."

    regime = snapshot.regime

    prompt = f"""你是未来资本的交易台晨会宏观策略师。你不是学者，你是交易员的大脑——
你的每句话都要回答一个问题："这对我的仓位意味着什么？"

规则：
- 不讲教科书理论，只讲市场正在交易的叙事
- 每个叙事必须有明确的"谁在买/谁在卖"和"什么价格行为验证了这个叙事"
- 必须给出可操作的结论：哪些 sector/factor 受益、受损
- 如果数据互相矛盾，直说"市场在纠结"，不要强行编故事

## 宏观数据快照
{snapshot.format_for_prompt()}

## 检测到的跨资产信号
{formatted_signals}

## 任务
1. 当前市场在交易什么叙事？(1-2 个，用交易员的语言)
2. 这些叙事对风险资产整体是 TAILWIND / NEUTRAL / HEADWIND？
3. 哪些 sector 和 factor 直接受影响？(具体到 long/short bias)
4. 什么信号出现会让叙事反转？(给出具体价格/数据水平)

## 输出格式
### Macro Briefing
**一句话**: [市场现在在交易什么]
**Regime**: {regime} | **Risk Bias**: [TAILWIND/NEUTRAL/HEADWIND]

**叙事 1: [标题]**
- 因果链: A → B → C → D
- 谁在买/卖: [机构行为、资金流向]
- 价格验证: [哪些价格行为确认了这个叙事]
- 受益: [sector/factor/个股特征]
- 受损: [sector/factor/个股特征]
- 反转信号: [具体数据水平或事件]

**叙事 2: [标题]** (如果有)
- ...

**交易台行动指引**:
- 仓位偏好: [进攻/防守/观望]
- 加仓方向: [具体 sector 或特征]
- 减仓方向: [具体 sector 或特征]
- 本周关注: [关键数据发布/事件]"""

    return prompt
