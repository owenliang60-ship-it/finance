"""M5 scheme registry: frozen factor specs and scoring schemes, identified by `scheme_hash`.

Weights come from the north star, layer 2 "方案注册表" (docs/design/prosperity-engine-north-star.md);
they are priors, not fitted on the 22-quarter history. Boss decisions 2026-09-29
(docs/plans/2026-09-29-prosperity-m5-m6-scheme-and-kernel.md): D-3 exemption and
exfin lists, D-4 F0 keeps only the original two gates, D-5 PE guard is a switch, not a scheme.

Configuration only; the scoring logic lives in `kernel.py`. `scheme_hash` covers the
config, `version.code_version` covers the code; a reader that sees either change fails closed.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Mapping, Optional, Sequence, Tuple

SCORING_FACTORS = ("revenue_accel", "eps_accel", "revenue_yoy", "eps_sue", "growth_4q",
                   "gm_yoy", "gm_level", "fcf_margin_yoy", "surprise", "revision")
TRANSFORMS = ("winsor_z", "rank_z")
GATES = ("net_margin_down", "new_listing", "ntm_not_above_ttm")
# North star: the expectation family is capped (least verifiable locally, most decay evidence in large caps)
EXPECTATION_CAP = 0.10

# D-3 A: margin-based factors do not describe banks, insurers, REITs or regulated utilities; IPPs keep them
EXEMPT_INDUSTRIES = (
    "Banks*", "Investment - Banking & Investment Services", "Financial - Mortgages",
    "Insurance - Diversified", "Insurance - Life", "Insurance - Property & Casualty",
    "Insurance - Reinsurance", "Insurance - Specialty", "REIT - *",
    "Diversified Utilities", "Regulated Electric", "Regulated Gas", "Regulated Water", "Renewable Utilities",
)
# D-3 A: F1-exfin also drops asset managers, capital markets, insurance brokers and IPPs;
# payment networks / credit services, exchanges & data and real-estate services stay
EXFIN_EXCLUDED_INDUSTRIES = EXEMPT_INDUSTRIES + (
    "Asset Management", "Financial - Capital Markets", "Insurance - Brokers", "Independent Power Producers",
)


def industry_matches(industry: Optional[str], patterns: Sequence[str]) -> bool:
    """A pattern ending in `*` is a prefix; any other pattern must match exactly."""
    if not industry:
        return False
    return any(industry.startswith(p[:-1]) if p.endswith("*") else industry == p for p in patterns)


@dataclass(frozen=True)
class FactorSpec:
    family: str
    params_timing: str            # quarterly: frozen quarter-end package; weekly: re-estimated each board
    min_quarters: int             # quarters the formula needs; registered, not enforced here
    exempt_industries: Tuple[str, ...] = ()


FACTOR_SPECS: Mapping[str, FactorSpec] = {
    "revenue_accel": FactorSpec("accel", "quarterly", 6),
    "eps_accel": FactorSpec("accel", "quarterly", 14),
    "revenue_yoy": FactorSpec("growth", "quarterly", 5),
    "eps_sue": FactorSpec("growth", "quarterly", 13),
    "growth_4q": FactorSpec("growth", "quarterly", 8),
    "gm_yoy": FactorSpec("quality", "quarterly", 5, EXEMPT_INDUSTRIES),
    "gm_level": FactorSpec("quality", "quarterly", 1, EXEMPT_INDUSTRIES),
    "fcf_margin_yoy": FactorSpec("quality", "quarterly", 5, EXEMPT_INDUSTRIES),
    "surprise": FactorSpec("expectation", "weekly", 1),
    "revision": FactorSpec("expectation", "weekly", 0),
}


@dataclass(frozen=True)
class Scheme:
    scheme_id: str
    description: str
    weights: Tuple[Tuple[str, float], ...]
    transform: str = "winsor_z"
    gates: Tuple[str, ...] = GATES
    universe_exclude_industries: Tuple[str, ...] = ()
    pe_redflag_demotes: bool = False
    min_quarters: int = 6
    min_coverage: float = 0.5
    winsor_k: float = 3.0
    z_clip: float = 3.0
    composite_clip: float = 2.0
    grade_strict: float = 65.0
    grade_full: float = 50.0
    new_listing_days: int = 730
    new_listing_min_revenue_yoy: float = 75.0
    new_listing_min_gm: float = 40.0
    new_listing_min_score: float = 70.0
    slope_tiers: Tuple[Tuple[float, float], ...] = ((90, 10), (70, 6), (55, 2), (30, 0), (10, -5), (0, -10))
    group_min_members: int = 5
    pe_redflag_pct: float = 20.0
    changes: Tuple[Tuple[str, str], ...] = ()


_FIRST = (("2026-09-29", "首批方案；权重取自北极星第二层，先验，不拟合"),)
# North star F1: accel .35 (revenue .22, EPS .13); growth .30 (revenue YoY .10, SUE .15, 4Q .05);
# quality .25 (GM YoY .12, GM .03, FCF margin YoY .10); expectation .10 (surprise .05, revision .05)
_F1_WEIGHTS = (("revenue_accel", .22), ("eps_accel", .13), ("revenue_yoy", .10), ("eps_sue", .15),
               ("growth_4q", .05), ("gm_yoy", .12), ("gm_level", .03), ("fcf_margin_yoy", .10),
               ("surprise", .05), ("revision", .05))
# Original site scheme B without inventory (.13), rescaled by .87
_F0_WEIGHTS = tuple((f, w / .87) for f, w in (("revenue_accel", .40), ("revenue_yoy", .14), ("gm_yoy", .14),
                                              ("gm_level", .05), ("fcf_margin_yoy", .14)))

SCHEMES: Mapping[str, Scheme] = {
    "F1": Scheme("F1", "融合主方案", _F1_WEIGHTS, changes=_FIRST),
    "F1-rank": Scheme("F1-rank", "同 F1，缩尾 z 换成排名 z（D3）", _F1_WEIGHTS, transform="rank_z", changes=_FIRST),
    "F1-exfin": Scheme("F1-exfin", "剔除金融、REIT、公用事业的对照版", _F1_WEIGHTS,
                       universe_exclude_industries=EXFIN_EXCLUDED_INDUSTRIES, changes=_FIRST),
    "F0": Scheme("F0", "原框架基线：原站 B 方案去掉存货后按比例放大", _F0_WEIGHTS,
                 gates=("net_margin_down", "new_listing"), changes=_FIRST),
}


def scheme_hash(scheme: Scheme, specs: Mapping[str, FactorSpec] = FACTOR_SPECS) -> str:
    config = {k: v for k, v in asdict(scheme).items() if k not in ("description", "changes")}
    config["factor_specs"] = {f: asdict(specs[f]) for f, _ in scheme.weights}
    blob = json.dumps(config, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def validate_scheme(scheme: Scheme) -> None:
    factors = [f for f, _ in scheme.weights]
    if len(set(factors)) != len(factors) or any(f not in SCORING_FACTORS for f in factors):
        raise ValueError(f"{scheme.scheme_id}: unknown or duplicate factor in {factors}")
    if any(w <= 0 for _, w in scheme.weights) or abs(sum(w for _, w in scheme.weights) - 1.0) > 1e-9:
        raise ValueError(f"{scheme.scheme_id}: weights must be positive and sum to 1")
    if scheme.transform not in TRANSFORMS:
        raise ValueError(f"{scheme.scheme_id}: unknown transform {scheme.transform}")
    if any(g not in GATES for g in scheme.gates):
        raise ValueError(f"{scheme.scheme_id}: unknown gate in {scheme.gates}")
    expectation = sum(w for f, w in scheme.weights if FACTOR_SPECS[f].family == "expectation")
    if expectation > EXPECTATION_CAP + 1e-9:
        raise ValueError(f"{scheme.scheme_id}: expectation family {expectation:.2f} > {EXPECTATION_CAP}")


def get_scheme(scheme_id: str) -> Scheme:
    return SCHEMES[scheme_id]


def check_scheme_hash(scheme_id: str, expected: str) -> Scheme:
    scheme = get_scheme(scheme_id)
    if scheme_hash(scheme) != expected:
        raise ValueError(f"{scheme_id}: scheme_hash {expected} does not match the registry")
    return scheme
