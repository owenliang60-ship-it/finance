import json
import math
import statistics
from dataclasses import replace

import pytest

from terminal.prosperity.kernel import (base_score, composite, estimate_params, params_from_dict, params_to_dict,
                                        rank_z, rankable, slope_bonus, slope_bounds, standardize, usable_values,
                                        winsor_params, winsor_z)
from terminal.prosperity.schemes import get_scheme, scheme_hash
from tests.prosperity_fixtures import frow, full_values

F1 = get_scheme("F1")


def test_winsor_is_two_pass_population_sigma_and_clips_z():
    vals = [1.0] * 19 + [100.0]
    p = winsor_params(vals, k=3.0)
    mu0, s0 = statistics.fmean(vals), statistics.pstdev(vals)
    assert (p.lo, p.hi) == pytest.approx((mu0 - 3 * s0, mu0 + 3 * s0))
    clipped = [min(max(v, p.lo), p.hi) for v in vals]
    assert (p.mu, p.sigma) == pytest.approx((statistics.fmean(clipped), statistics.pstdev(clipped)))
    assert winsor_z(100.0, p) == 3.0 and winsor_z(1e9, p) == 3.0
    assert winsor_z(1.0, p) == pytest.approx((1.0 - p.mu) / p.sigma)


def test_rank_z_is_standardized_midrank_against_reference():
    ref = (1.0, 2.0, 3.0, 4.0)
    assert rank_z(1.0, ref) == pytest.approx((0.5 / 4 - 0.5) * math.sqrt(12))
    assert rank_z(2.5, ref) == pytest.approx(0.0)
    assert rank_z(9.0, ref) == pytest.approx(0.5 * math.sqrt(12))


def test_slope_bounds_are_inclusive_quantiles_and_bonus_is_tiered():
    b = slope_bounds([float(v) for v in range(1, 11)], F1.slope_tiers)
    assert b == pytest.approx((9.1, 7.3, 5.95, 3.7, 1.9))
    expect = {10: 10, 9: 6, 8: 6, 7: 2, 6: 2, 5: 0, 4: 0, 3: -5, 2: -5, 1: -10}
    assert {v: slope_bonus(float(v), b, F1.slope_tiers) for v in expect} == expect
    assert slope_bonus(None, b, F1.slope_tiers) == 0 and slope_bonus(5.0, None, F1.slope_tiers) == 0


def test_exempt_industries_lose_margin_factors_only():
    vals, missing = usable_values(frow("JPM", full_values(1), industry="Banks - Diversified"), F1)
    assert all(vals[f] is None and missing[f] == "industry_exempt"
               for f in ("gm_yoy", "gm_level", "fcf_margin_yoy"))
    assert vals["revenue_yoy"] is not None and vals["eps_sue"] is not None


def test_rankable_gate_universe_identity_quarters_coverage():
    ok = frow("A", full_values(1), disclosed=6)
    assert rankable(ok, F1) == (True, None)
    assert rankable(replace(ok, disclosed_quarters=5), F1) == (False, "insufficient_quarters")
    thin = frow("B", {"revenue_accel": 1.0, "eps_accel": 1.0, "revenue_yoy": 1.0})      # .22 + .13 + .10 = .45
    assert rankable(thin, F1) == (False, "low_coverage")
    assert rankable(replace(ok, packet_flags=("identity_unverified",)), F1) == (False, "identity_unverified")
    bank = frow("C", {**full_values(1), "surprise": None, "revision": None}, industry="Banks - Regional")
    assert rankable(bank, F1) == (True, None)             # .25 exempt + .10 expectation missing → coverage .65
    assert rankable(frow("D", full_values(1), industry="Banks - Regional"),
                    get_scheme("F1-exfin")) == (False, "universe_excluded")


def test_composite_reweights_present_factors_and_maps_to_0_100():
    comp, used = composite({"revenue_accel": 2.0, "revenue_yoy": 1.0}, F1)
    assert comp == pytest.approx((2.0 * .22 + 1.0 * .10) / .32)
    assert used == pytest.approx({"revenue_accel": .22 / .32, "revenue_yoy": .10 / .32})
    assert base_score(comp, F1) == pytest.approx((comp + 2) / 4 * 100)
    assert base_score(5.0, F1) == 100.0 and base_score(-5.0, F1) == 0.0
    assert composite({}, F1)[0] is None


def _week(as_of, n=12, offset=0, prefix="S"):
    return [frow(f"{prefix}{i}", full_values(offset + i), as_of=as_of) for i in range(n)]


def test_frozen_quarterly_params_make_financial_z_independent_of_peers():
    frozen = estimate_params(_week("2026-06-27"), F1, as_of="2026-06-27", code_version="t")
    week = _week("2026-07-04")
    a, _, boot = standardize(week, F1, frozen=frozen, as_of="2026-07-04", code_version="t")
    peers = week[:6] + _week("2026-07-04", n=6, offset=100, prefix="T")
    b, _, _ = standardize(peers, F1, frozen=frozen, as_of="2026-07-04", code_version="t")
    assert boot is False
    assert a["S0"]["revenue_yoy"] == b["S0"]["revenue_yoy"]       # quarterly factor: frozen package
    assert a["S0"]["surprise"] != b["S0"]["surprise"]             # expectation factor: re-estimated weekly


def test_rows_must_be_dated_on_the_params_or_board_date():
    late = _week("2026-09-26")
    with pytest.raises(ValueError, match="row_as_of_mismatch"):   # 9/26 inputs cannot make 6/27 params
        estimate_params(late, F1, as_of="2026-06-27", code_version="t")
    with pytest.raises(ValueError, match="row_as_of_mismatch"):
        standardize(late, F1, frozen=None, as_of="2026-07-04", code_version="t")
    mixed = _week("2026-07-04")[:11] + late[11:]
    with pytest.raises(ValueError, match="row_as_of_mismatch"):   # one stray row is enough
        standardize(mixed, F1, frozen=None, as_of="2026-07-04", code_version="t")


def test_bootstrap_and_future_foreign_or_stale_code_params_fail_closed():
    rows = _week("2026-09-26")
    _, used, boot = standardize(rows, F1, frozen=None, as_of="2026-09-26", code_version="t")
    assert boot is True and (used.scheme_hash, used.code_version) == (scheme_hash(F1), "t")
    cases = ((estimate_params(_week("2026-10-03"), F1, as_of="2026-10-03", code_version="t"), "params_from_future"),
             (estimate_params(rows, get_scheme("F0"), as_of="2026-09-26", code_version="t"), "params_scheme_mismatch"),
             (estimate_params(rows, F1, as_of="2026-09-26", code_version="old"), "params_code_mismatch"))
    for frozen, err in cases:
        with pytest.raises(ValueError, match=err):
            standardize(rows, F1, frozen=frozen, as_of="2026-09-26", code_version="t")


def test_degenerate_cross_section_factor_is_dropped_not_zeroed():
    rows = [frow(f"S{i}", {**full_values(i), "revision": 1.0}) for i in range(12)]
    z, _, _ = standardize(rows, F1, frozen=None, as_of="2026-09-26", code_version="t")
    assert all("revision" not in z[s] for s in z)


def test_rank_scheme_uses_reference_distribution():
    rows = [frow(f"S{i}", full_values(i)) for i in range(4)]
    z, used, _ = standardize(rows, get_scheme("F1-rank"), frozen=None, as_of="2026-09-26", code_version="t")
    assert z["S0"]["revenue_yoy"] == pytest.approx((0.5 / 4 - 0.5) * math.sqrt(12))
    assert used.reference["revenue_yoy"] == tuple(sorted(r.values["revenue_yoy"] for r in rows))


def test_params_round_trip_and_version_is_a_content_hash():
    p = estimate_params(_week("2026-09-26"), F1, as_of="2026-09-26", code_version="t")
    assert params_from_dict(json.loads(json.dumps(params_to_dict(p)))) == p
    assert len(p.params_version) == 16
    assert estimate_params(_week("2026-09-26"), F1, as_of="2026-09-26", code_version="u").params_version != p.params_version
