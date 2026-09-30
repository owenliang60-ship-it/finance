"""M6 scoring kernel: factor rows + scheme (+ optional frozen parameter package) → board.

Steps follow docs/plans/2026-09-29-prosperity-m5-m6-scheme-and-kernel.md "打分流程":
universe and exemptions → rankable gate → winsorized z (two-pass, population σ) or
standardized mid-rank z → coverage-reweighted composite → 0–100 + gross-margin-slope
bonus → grade gates → PE guard and within-sector scores. Quarterly factors use the
frozen quarter-end package; expectation factors are re-estimated every board; without a
package everything bootstraps. Every entry point rejects rows dated off the board
(or package) date. Pure functions: no reads, no writes, no pandas.
"""
from __future__ import annotations

import bisect
import hashlib
import json
import math
import statistics
from dataclasses import asdict, dataclass
from typing import Dict, Mapping, Optional, Sequence, Tuple

from terminal.prosperity.factors import FactorRow
from terminal.prosperity.schemes import EXEMPT_INDUSTRIES, FACTOR_SPECS, Scheme, industry_matches, scheme_hash

COVERAGE_TOLERANCE = 1e-9


@dataclass(frozen=True)
class WinsorParams:
    lo: float
    hi: float
    mu: float
    sigma: float


@dataclass(frozen=True)
class ParamsPackage:
    scheme_id: str
    scheme_hash: str
    code_version: str                        # engine code that produced it (D-8: checked in full)
    as_of: str
    winsor: Mapping[str, WinsorParams]       # winsor_z schemes
    reference: Mapping[str, Tuple[float, ...]]   # rank_z schemes: sorted rankable values
    slope_bounds: Optional[Tuple[float, ...]]
    n_rankable: int
    params_version: str                      # content hash of every other field


def _factors(scheme: Scheme) -> Tuple[str, ...]:
    return tuple(f for f, _ in scheme.weights)


def _check_rows(rows: Sequence[FactorRow], as_of: str) -> None:
    stray = sorted({r.as_of for r in rows if r.as_of != as_of})
    if stray:
        raise ValueError(f"row_as_of_mismatch: rows dated {stray[:3]} on a {as_of} board")


def _package(scheme_id: str, s_hash: str, code_version: str, as_of: str, winsor: Mapping[str, WinsorParams],
             reference: Mapping[str, Tuple[float, ...]], bounds: Optional[Tuple[float, ...]],
             n_rankable: int) -> ParamsPackage:
    body = {"scheme_id": scheme_id, "scheme_hash": s_hash, "code_version": code_version,
            "as_of": as_of, "winsor": {f: asdict(p) for f, p in winsor.items()},
            "reference": {f: list(v) for f, v in reference.items()},
            "slope_bounds": None if bounds is None else list(bounds), "n_rankable": n_rankable}
    version = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]
    return ParamsPackage(scheme_id, s_hash, code_version, as_of, dict(winsor), dict(reference), bounds,
                         n_rankable, version)


def params_to_dict(p: ParamsPackage) -> dict:
    d = asdict(p)
    d["reference"] = {f: list(v) for f, v in p.reference.items()}
    d["slope_bounds"] = None if p.slope_bounds is None else list(p.slope_bounds)
    return d


def params_from_dict(d: Mapping) -> ParamsPackage:
    """Rebuild a stored package; its content must still hash to the stored params_version."""
    p = _package(d["scheme_id"], d["scheme_hash"], d["code_version"], d["as_of"],
                 {f: WinsorParams(**w) for f, w in d["winsor"].items()},
                 {f: tuple(v) for f, v in d["reference"].items()},
                 None if d["slope_bounds"] is None else tuple(d["slope_bounds"]), d["n_rankable"])
    if p.params_version != d["params_version"]:
        raise ValueError(f"params_version_mismatch: stored {d['params_version']}, content {p.params_version}")
    return p


def winsor_params(values: Sequence[float], k: float) -> WinsorParams:
    mu0, s0 = statistics.fmean(values), statistics.pstdev(values)
    lo, hi = mu0 - k * s0, mu0 + k * s0
    clipped = [min(max(v, lo), hi) for v in values]
    return WinsorParams(lo, hi, statistics.fmean(clipped), statistics.pstdev(clipped))


def winsor_z(value: float, p: WinsorParams, clip: float = 3.0) -> float:
    z = (min(max(value, p.lo), p.hi) - p.mu) / p.sigma
    return max(-clip, min(clip, z))


def rank_z(value: float, reference: Sequence[float]) -> float:
    """Standardized mid-rank (QMJ, D-6): p = (below + ½·equal) / n, z = (p − ½)·√12."""
    below = bisect.bisect_left(reference, value)
    equal = bisect.bisect_right(reference, value) - below
    return ((below + 0.5 * equal) / len(reference) - 0.5) * math.sqrt(12)


def slope_bounds(slopes: Sequence[float], tiers) -> Optional[Tuple[float, ...]]:
    if len(slopes) < 2:
        return None
    cuts = statistics.quantiles(slopes, n=100, method="inclusive")
    return tuple(cuts[int(pct) - 1] for pct, _ in tiers[:-1])


def slope_bonus(slope: Optional[float], bounds, tiers) -> float:
    if slope is None or bounds is None:
        return 0
    for (_, bonus), bound in zip(tiers[:-1], bounds):
        if slope >= bound:
            return bonus
    return tiers[-1][1]


def usable_values(row: FactorRow, scheme: Scheme) -> Tuple[Dict[str, Optional[float]], Dict[str, str]]:
    """The scheme's factors after industry exemptions; reasons for every blank."""
    values, missing = {}, {}
    for f in _factors(scheme):
        if industry_matches(row.industry, FACTOR_SPECS[f].exempt_industries):
            values[f], missing[f] = None, "industry_exempt"
        else:
            values[f] = row.values.get(f)
            if values[f] is None:
                missing[f] = row.missing.get(f, "missing")
    return values, missing


def _coverage(values: Mapping[str, Optional[float]], scheme: Scheme) -> float:
    return sum(w for f, w in scheme.weights if values.get(f) is not None)


def rankable(row: FactorRow, scheme: Scheme) -> Tuple[bool, Optional[str]]:
    if industry_matches(row.industry, scheme.universe_exclude_industries):
        return False, "universe_excluded"
    if "identity_unverified" in row.packet_flags:          # D-7 A: observe, never rank
        return False, "identity_unverified"
    if row.disclosed_quarters < scheme.min_quarters:
        return False, "insufficient_quarters"
    if _coverage(usable_values(row, scheme)[0], scheme) + COVERAGE_TOLERANCE < scheme.min_coverage:
        return False, "low_coverage"
    return True, None


def _slope(row: FactorRow) -> Optional[float]:
    return None if industry_matches(row.industry, EXEMPT_INDUSTRIES) else row.aux.get("gm_slope")


def estimate_params(rows: Sequence[FactorRow], scheme: Scheme, *, as_of: str, code_version: str) -> ParamsPackage:
    """Parameters for every scheme factor and the slope tiers, from rankable rows dated `as_of` only."""
    _check_rows(rows, as_of)
    ranked = [r for r in rows if rankable(r, scheme)[0]]
    usable = [usable_values(r, scheme)[0] for r in ranked]
    winsor, reference = {}, {}
    for f in _factors(scheme):
        vals = [u[f] for u in usable if u[f] is not None]
        if len(vals) < 2 or statistics.pstdev(vals) == 0:
            continue                                        # degenerate_cross_section
        if scheme.transform == "rank_z":
            reference[f] = tuple(sorted(vals))
        else:
            p = winsor_params(vals, scheme.winsor_k)
            if p.sigma > 0:
                winsor[f] = p
    slopes = [s for s in (_slope(r) for r in ranked) if s is not None]
    return _package(scheme.scheme_id, scheme_hash(scheme), code_version, as_of, winsor, reference,
                    slope_bounds(slopes, scheme.slope_tiers), len(ranked))


def standardize(rows: Sequence[FactorRow], scheme: Scheme, *, frozen: Optional[ParamsPackage], as_of: str,
                code_version: str) -> Tuple[Dict[str, Dict[str, float]], ParamsPackage, bool]:
    """Per-factor z for rankable rows, the package actually used, and whether it bootstrapped."""
    _check_rows(rows, as_of)
    if frozen is not None:
        if frozen.as_of > as_of:
            raise ValueError(f"params_from_future: package {frozen.as_of} on a {as_of} board")
        if frozen.scheme_hash != scheme_hash(scheme):
            raise ValueError(f"params_scheme_mismatch: {frozen.scheme_id} {frozen.scheme_hash}")
        if frozen.code_version != code_version:
            raise ValueError(f"params_code_mismatch: package {frozen.code_version}, code {code_version}")
    current = estimate_params(rows, scheme, as_of=as_of, code_version=code_version)
    used = current
    if frozen is not None:
        weekly = {f for f in _factors(scheme) if FACTOR_SPECS[f].params_timing == "weekly"}

        def pick(frz, cur):
            """Quarterly factors from the frozen package, weekly ones from this board."""
            out = {}
            for f in _factors(scheme):
                source = cur if f in weekly else frz
                if f in source:
                    out[f] = source[f]
            return out

        used = _package(scheme.scheme_id, scheme_hash(scheme), code_version, as_of,
                        pick(frozen.winsor, current.winsor), pick(frozen.reference, current.reference),
                        frozen.slope_bounds, current.n_rankable)
    z: Dict[str, Dict[str, float]] = {}
    for row in rows:
        if not rankable(row, scheme)[0]:
            continue
        values = usable_values(row, scheme)[0]
        zs = {}
        for f, v in values.items():
            if v is None:
                continue
            if scheme.transform == "rank_z" and f in used.reference:
                zs[f] = rank_z(v, used.reference[f])
            elif scheme.transform == "winsor_z" and f in used.winsor:
                zs[f] = winsor_z(v, used.winsor[f], scheme.z_clip)
        z[row.symbol] = zs
    return z, used, frozen is None


def composite(z: Mapping[str, float], scheme: Scheme) -> Tuple[Optional[float], Dict[str, float]]:
    """Σ z·w ÷ Σ w over the factors that have a z; the second item is the renormalized weights."""
    weights = {f: w for f, w in scheme.weights if f in z}
    total = sum(weights.values())
    if not weights:
        return None, {}
    return sum(z[f] * w for f, w in weights.items()) / total, {f: w / total for f, w in weights.items()}


def base_score(comp: float, scheme: Scheme) -> float:
    c = scheme.composite_clip
    return (max(-c, min(c, comp)) + c) / (2 * c) * 100


GRADE_ORDER = {"STRICT": 0, "FULL": 1, "BELOW": 2}


@dataclass(frozen=True)
class ScoredRow:
    symbol: str
    status: str                                # ranked / observe / excluded
    observe_reason: Optional[str]
    values: Mapping[str, Optional[float]]      # the scheme's factors after exemptions
    missing: Mapping[str, str]                 # scheme factors without a z → why
    z: Mapping[str, float]
    weights: Mapping[str, float]               # renormalized over factors with a z
    contributions: Mapping[str, float]
    coverage: float
    composite: Optional[float]
    base: Optional[float]
    trend_adj: Optional[float]
    score: Optional[float]
    grade: Optional[str]
    demotions: Tuple[str, ...]
    within: Optional[float]
    family_scores: Mapping[str, Optional[float]]
    rank: Optional[int]
    badges: Tuple[str, ...]


@dataclass(frozen=True)
class BoardResult:
    scheme_id: str
    scheme_hash: str
    code_version: str
    as_of: str
    params: ParamsPackage                      # what scored this board: frozen quarterly + this board's weekly
    params_bootstrap: bool
    rows: Tuple[ScoredRow, ...]
    counts: Mapping[str, int]
    frozen_as_of: Optional[str] = None         # the frozen package behind `params`, when one was used
    frozen_params_version: Optional[str] = None


def is_new_listing(row: FactorRow, scheme: Scheme) -> bool:
    """Listed fewer than the scheme's days ago; an unknown listing date is not new (D-2)."""
    return row.listing_days is not None and row.listing_days < scheme.new_listing_days


def grade(score: float, row: FactorRow, scheme: Scheme) -> Tuple[str, Tuple[str, ...], Tuple[str, ...]]:
    """(grade, demotions, badges). Every failed gate is listed, not just the first."""
    g = "STRICT" if score >= scheme.grade_strict else "FULL" if score >= scheme.grade_full else "BELOW"
    demotions, badges = [], []
    new = is_new_listing(row, scheme)
    for gate in scheme.gates:
        if gate == "net_margin_down":
            nm = row.aux.get("net_margin_yoy")
            if not new and nm is not None and nm < 0:
                demotions.append("net_margin_down")
        elif gate == "new_listing" and new:
            rev, gm = row.values.get("revenue_yoy"), row.values.get("gm_level")   # raw, before exemptions
            if not (rev is not None and rev >= scheme.new_listing_min_revenue_yoy and gm is not None
                    and gm >= scheme.new_listing_min_gm and score >= scheme.new_listing_min_score):
                demotions.append("new_listing_gate")
        elif gate == "ntm_not_above_ttm":
            ntm, ttm = row.aux.get("ntm_eps"), row.aux.get("ttm_eps")
            if ntm is None or ttm is None:
                badges.append("ntm_gate_unknown")          # unverifiable: no penalty
            elif ntm <= ttm:
                demotions.append("ntm_not_above_ttm")
    return ("BELOW" if demotions else g), tuple(demotions), tuple(badges)


def _family_scores(z: Mapping[str, float], scheme: Scheme) -> Dict[str, Optional[float]]:
    out = {}
    for family in dict.fromkeys(FACTOR_SPECS[f].family for f in _factors(scheme)):
        members = {f: w for f, w in scheme.weights if FACTOR_SPECS[f].family == family and f in z}
        out[family] = (base_score(sum(z[f] * w for f, w in members.items()) / sum(members.values()), scheme)
                       if members else None)
    return out


def _pct(values: Sequence[float], pct: float) -> float:
    return statistics.quantiles(values, n=100, method="inclusive")[int(pct) - 1]


def score_board(rows: Sequence[FactorRow], scheme: Scheme, *, frozen: Optional[ParamsPackage], as_of: str,
                code_version: str) -> BoardResult:
    _check_rows(rows, as_of)
    z, used, boot = standardize(rows, scheme, frozen=frozen, as_of=as_of, code_version=code_version)
    by_symbol = {r.symbol: r for r in rows}
    out: Dict[str, dict] = {}
    for row in rows:
        ok, reason = rankable(row, scheme)
        values, missing = usable_values(row, scheme)
        rec = dict(symbol=row.symbol, status="ranked", observe_reason=None, values=values, missing=dict(missing),
                   z={}, weights={}, contributions={}, coverage=_coverage(values, scheme), composite=None,
                   base=None, trend_adj=None, score=None, grade=None, demotions=(), within=None,
                   family_scores={}, rank=None, badges=["industry_unknown"] if not row.industry else [])
        out[row.symbol] = rec
        if not ok:
            rec.update(status="excluded" if reason == "universe_excluded" else "observe", observe_reason=reason)
            continue
        zs = z[row.symbol]
        for f, v in values.items():
            if v is not None and f not in zs:
                rec["missing"][f] = "degenerate_cross_section"
        comp, weights = composite(zs, scheme)
        if comp is None:
            rec.update(status="observe", observe_reason="no_scorable_factor")
            continue
        slope = _slope(row)
        bonus = slope_bonus(slope, used.slope_bounds, scheme.slope_tiers)
        base = base_score(comp, scheme)
        g, demotions, badges = grade(base + bonus, row, scheme)
        rec.update(z=zs, weights=weights, contributions={f: zs[f] * w for f, w in weights.items()},
                   composite=comp, base=base, trend_adj=bonus, score=base + bonus, grade=g, demotions=demotions,
                   family_scores=_family_scores(zs, scheme))
        if boot:
            rec["badges"].append("params_bootstrap")
        if slope is None or used.slope_bounds is None:
            rec["badges"].append("gm_slope_missing")
        rec["badges"].extend(badges)

    ranked = [s for s, r in out.items() if r["status"] == "ranked"]

    def negative(s: str) -> bool:
        raw = by_symbol[s].values
        return any(raw.get(f) is not None and raw[f] < 0 for f in ("revision", "surprise"))

    def redflag(s: str) -> None:
        out[s]["badges"].append("pe_redflag")
        if scheme.pe_redflag_demotes:
            out[s].update(grade="BELOW", demotions=out[s]["demotions"] + ("pe_redflag",))

    # Expected losses (NTM E/P ≤ 0) stay out of the valuation percentile: badge, and flagged on their own
    # with a negative revision or surprise, whatever the sector size (Boss 2026-09-30 ②)
    for s in ranked:
        v = by_symbol[s].aux.get("ep_ntm")
        if v is not None and v <= 0:
            out[s]["badges"].append("ntm_loss")
            if negative(s):
                redflag(s)
    sectors: Dict[str, list] = {}
    for s in ranked:
        if by_symbol[s].sector:
            sectors.setdefault(by_symbol[s].sector, []).append(s)
    for members in sectors.values():
        if len(members) < scheme.group_min_members:
            continue
        # PE guard (D-5): positive NTM E/P in the sector's lowest pct (richest valuation) with a negative raw
        # revision or surprise
        ep = {s: v for s in members if (v := by_symbol[s].aux.get("ep_ntm")) is not None and v > 0}
        if len(ep) >= scheme.group_min_members:
            cut = _pct(list(ep.values()), scheme.pe_redflag_pct)
            for s, v in ep.items():
                if v <= cut and negative(s):
                    redflag(s)
        # Within-sector base score on this board's sector cross-section; reference only
        zs, _, _ = standardize([by_symbol[s] for s in members], scheme, frozen=None, as_of=as_of,
                               code_version=code_version)
        for s in members:
            comp = composite(zs[s], scheme)[0]
            out[s]["within"] = None if comp is None else base_score(comp, scheme)

    ranked.sort(key=lambda s: (GRADE_ORDER[out[s]["grade"]], -out[s]["score"], s))
    for i, s in enumerate(ranked, 1):
        out[s]["rank"] = i
    rest = lambda status: sorted(s for s, r in out.items() if r["status"] == status)
    order = ranked + rest("observe") + rest("excluded")
    scored = tuple(ScoredRow(**{**out[s], "badges": tuple(dict.fromkeys(out[s]["badges"]))}) for s in order)
    counts = {k: sum(r.status == k for r in scored) for k in ("ranked", "observe", "excluded")}
    return BoardResult(scheme.scheme_id, scheme_hash(scheme), code_version, as_of, used, boot, scored, counts,
                       frozen.as_of if frozen else None, frozen.params_version if frozen else None)
