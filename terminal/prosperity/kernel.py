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


def _package(scheme: Scheme, code_version: str, as_of: str, winsor: Mapping[str, WinsorParams],
             reference: Mapping[str, Tuple[float, ...]], bounds: Optional[Tuple[float, ...]],
             n_rankable: int) -> ParamsPackage:
    body = {"scheme_id": scheme.scheme_id, "scheme_hash": scheme_hash(scheme), "code_version": code_version,
            "as_of": as_of, "winsor": {f: asdict(p) for f, p in winsor.items()},
            "reference": {f: list(v) for f, v in reference.items()},
            "slope_bounds": None if bounds is None else list(bounds), "n_rankable": n_rankable}
    version = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]
    return ParamsPackage(scheme.scheme_id, body["scheme_hash"], code_version, as_of, dict(winsor),
                         dict(reference), bounds, n_rankable, version)


def params_to_dict(p: ParamsPackage) -> dict:
    d = asdict(p)
    d["reference"] = {f: list(v) for f, v in p.reference.items()}
    d["slope_bounds"] = None if p.slope_bounds is None else list(p.slope_bounds)
    return d


def params_from_dict(d: Mapping) -> ParamsPackage:
    return ParamsPackage(
        d["scheme_id"], d["scheme_hash"], d["code_version"], d["as_of"],
        {f: WinsorParams(**w) for f, w in d["winsor"].items()},
        {f: tuple(v) for f, v in d["reference"].items()},
        None if d["slope_bounds"] is None else tuple(d["slope_bounds"]), d["n_rankable"], d["params_version"])


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
    return _package(scheme, code_version, as_of, winsor, reference, slope_bounds(slopes, scheme.slope_tiers),
                    len(ranked))


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

        used = _package(scheme, code_version, as_of, pick(frozen.winsor, current.winsor),
                        pick(frozen.reference, current.reference), frozen.slope_bounds, current.n_rankable)
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
