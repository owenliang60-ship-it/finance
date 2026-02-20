"""
Alpha Layer — 数据结构

AlphaLens: Layer 2 分析视角 (parallels InvestmentLens from L1)
AlphaPackage: Layer 2 完整输出，持久化到 data/companies/{SYM}/analyses/
"""
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class AlphaLens:
    """Layer 2 分析视角 (parallels InvestmentLens from L1)."""
    name: str           # e.g. "Red Team Gauntlet"
    name_cn: str        # e.g. "红队试炼"
    phase: int          # 1, 2, or 3
    persona: str        # Chinese persona description
    tags: List[str] = field(default_factory=list)


# Pre-defined lenses for Layer 2
ALPHA_LENSES = [
    AlphaLens(
        name="Red Team Gauntlet",
        name_cn="红队试炼",
        phase=1,
        persona="基金最冷血的风控合伙人，职业建立在'在别人看到机会的地方看到尸体'",
        tags=["adversarial", "risk", "contrarian"],
    ),
    AlphaLens(
        name="Cycle & Pendulum",
        name_cn="周期钟摆定位",
        phase=2,
        persona="霍华德·马克斯的门徒——看的不是公司，是人群围绕公司的行为",
        tags=["cycle", "sentiment", "contrarian", "pendulum"],
    ),
    AlphaLens(
        name="Asymmetric Bet",
        name_cn="非对称赌注",
        phase=3,
        persona="索罗斯——不是学者索罗斯，是亲手按下'执行'按钮的交易员索罗斯",
        tags=["execution", "sizing", "asymmetry", "conviction"],
    ),
    AlphaLens(
        name="Alpha Debate",
        name_cn="终极辩论",
        phase=4,
        persona="索罗斯 vs 马克斯——行动派对耐心派的终极对决",
        tags=["debate", "conviction", "execution", "timing"],
    ),
]


@dataclass
class AlphaPackage:
    """Layer 2 output — persisted per-ticker."""
    symbol: str
    generated_at: str = ""

    # Red Team outputs
    single_point_of_failure: str = ""
    shadow_threat: str = ""
    post_mortem: str = ""
    consensus_fragility: str = ""

    # Cycle & Pendulum outputs
    pendulum_score: Optional[int] = None     # 1-10 (fear→greed)
    pendulum_direction: str = ""             # "toward_greed" / "toward_fear"
    business_cycle_phase: str = ""           # expansion/peak/contraction/trough
    tech_cycle_phase: str = ""               # infrastructure/platform/application/maturity
    cycle_alignment: str = ""                # tailwind/headwind/mixed
    this_time_is_different: List[str] = field(default_factory=list)

    # Asymmetric Bet outputs (MOST IMPORTANT)
    core_insight: str = ""                   # "市场认为X，但真相是Y，因为Z"
    bet_structure: str = ""                  # instrument + rationale
    entry_signal: str = ""
    target_exit: str = ""
    thesis_invalidation: str = ""            # NOT price stop — thesis falsification
    noise_to_ignore: List[str] = field(default_factory=list)
    real_danger_signals: List[str] = field(default_factory=list)

    # Conviction
    conviction_level: str = ""               # HIGH / MEDIUM / LOW
    conviction_modifier: float = 1.0         # 0.5-1.5, adjusts OPRMS timing_coeff
    action: str = ""                         # 执行 / 搁置 / 放弃

    # Alpha Debate outputs (Phase 4 — final verdict overrides bet)
    debate_conviction_modifier: Optional[float] = None   # 0.5-1.5, overrides conviction_modifier
    debate_final_action: str = ""                        # 执行 / 搁置 / 放弃
    debate_key_disagreement: str = ""                    # one-line summary of core disagreement

    def to_dict(self) -> dict:
        """Serialize to dict for JSON persistence."""
        return {
            "symbol": self.symbol,
            "generated_at": self.generated_at,
            # Red Team
            "single_point_of_failure": self.single_point_of_failure,
            "shadow_threat": self.shadow_threat,
            "post_mortem": self.post_mortem,
            "consensus_fragility": self.consensus_fragility,
            # Cycle & Pendulum
            "pendulum_score": self.pendulum_score,
            "pendulum_direction": self.pendulum_direction,
            "business_cycle_phase": self.business_cycle_phase,
            "tech_cycle_phase": self.tech_cycle_phase,
            "cycle_alignment": self.cycle_alignment,
            "this_time_is_different": self.this_time_is_different,
            # Asymmetric Bet
            "core_insight": self.core_insight,
            "bet_structure": self.bet_structure,
            "entry_signal": self.entry_signal,
            "target_exit": self.target_exit,
            "thesis_invalidation": self.thesis_invalidation,
            "noise_to_ignore": self.noise_to_ignore,
            "real_danger_signals": self.real_danger_signals,
            # Conviction
            "conviction_level": self.conviction_level,
            "conviction_modifier": self.conviction_modifier,
            "action": self.action,
            # Alpha Debate
            "debate_conviction_modifier": self.debate_conviction_modifier,
            "debate_final_action": self.debate_final_action,
            "debate_key_disagreement": self.debate_key_disagreement,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AlphaPackage":
        """Deserialize from dict."""
        return cls(
            symbol=d.get("symbol", ""),
            generated_at=d.get("generated_at", ""),
            # Red Team
            single_point_of_failure=d.get("single_point_of_failure", ""),
            shadow_threat=d.get("shadow_threat", ""),
            post_mortem=d.get("post_mortem", ""),
            consensus_fragility=d.get("consensus_fragility", ""),
            # Cycle & Pendulum
            pendulum_score=d.get("pendulum_score"),
            pendulum_direction=d.get("pendulum_direction", ""),
            business_cycle_phase=d.get("business_cycle_phase", ""),
            tech_cycle_phase=d.get("tech_cycle_phase", ""),
            cycle_alignment=d.get("cycle_alignment", ""),
            this_time_is_different=d.get("this_time_is_different", []),
            # Asymmetric Bet
            core_insight=d.get("core_insight", ""),
            bet_structure=d.get("bet_structure", ""),
            entry_signal=d.get("entry_signal", ""),
            target_exit=d.get("target_exit", ""),
            thesis_invalidation=d.get("thesis_invalidation", ""),
            noise_to_ignore=d.get("noise_to_ignore", []),
            real_danger_signals=d.get("real_danger_signals", []),
            # Conviction
            conviction_level=d.get("conviction_level", ""),
            conviction_modifier=d.get("conviction_modifier", 1.0),
            action=d.get("action", ""),
            # Alpha Debate
            debate_conviction_modifier=d.get("debate_conviction_modifier"),
            debate_final_action=d.get("debate_final_action", ""),
            debate_key_disagreement=d.get("debate_key_disagreement", ""),
        )
