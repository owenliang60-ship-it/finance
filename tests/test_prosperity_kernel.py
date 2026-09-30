import json
import math
import statistics
from dataclasses import replace

import pytest

from terminal.prosperity.kernel import (base_score, composite, estimate_params, grade, params_from_dict,
                                        params_to_dict, rank_z, rankable, score_board, slope_bonus, slope_bounds,
                                        standardize, usable_values, winsor_params, winsor_z)
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

def test_grade_thresholds_and_net_margin_and_ntm_gates():
    r = frow("A", full_values(1))
    assert [grade(s, r, F1)[0] for s in (65.0, 64.99, 50.0, 49.99)] == ["STRICT", "FULL", "FULL", "BELOW"]
    down = replace(r, aux={**r.aux, "net_margin_yoy": -0.5})
    assert grade(80.0, down, F1)[:2] == ("BELOW", ("net_margin_down",))
    assert grade(80.0, replace(r, aux={**r.aux, "net_margin_yoy": None}), F1)[0] == "STRICT"
    flat = replace(r, aux={**r.aux, "ntm_eps": 4.0})                        # NTM == TTM
    assert grade(80.0, flat, F1)[:2] == ("BELOW", ("ntm_not_above_ttm",))
    g, dem, badges = grade(80.0, replace(r, aux={**r.aux, "ntm_eps": None}), F1)
    assert (g, dem) == ("STRICT", ()) and "ntm_gate_unknown" in badges
    assert grade(80.0, flat, get_scheme("F0"))[0] == "STRICT"                # D-4 已确认


def test_new_listing_three_hurdles_replace_net_margin_guard():
    nl = frow("N", {**full_values(1), "revenue_yoy": 80.0, "gm_level": 45.0}, listing_days=100,
              aux={"net_margin_yoy": -5.0, "ntm_eps": 2.0, "ttm_eps": 1.0})
    assert grade(72.0, nl, F1)[0] == "STRICT"
    assert grade(69.0, nl, F1)[:2] == ("BELOW", ("new_listing_gate",))
    assert grade(72.0, replace(nl, values={**nl.values, "gm_level": 39.0}), F1)[0] == "BELOW"
    assert grade(72.0, replace(nl, values={**nl.values, "revenue_yoy": None}), F1)[0] == "BELOW"   # D-2 已确认


def test_new_listing_window_comes_from_the_scheme():
    nl = frow("N", {**full_values(1), "revenue_yoy": 80.0, "gm_level": 45.0}, listing_days=500,
              aux={"net_margin_yoy": -5.0, "ntm_eps": 2.0, "ttm_eps": 1.0})
    assert grade(72.0, nl, F1)[0] == "STRICT"                                      # 500 < 730: hurdles apply
    assert grade(72.0, nl, replace(F1, new_listing_days=365))[:2] == ("BELOW", ("net_margin_down",))
    assert grade(72.0, replace(nl, listing_days=729), F1)[0] == "STRICT"
    assert grade(72.0, replace(nl, listing_days=730), F1)[:2] == ("BELOW", ("net_margin_down",))
    assert grade(72.0, replace(nl, listing_days=None), F1)[:2] == ("BELOW", ("net_margin_down",))  # unknown → not new


def test_score_board_ranks_by_grade_then_score_and_marks_observe_rows():
    rows = [frow(f"S{i}", full_values(i)) for i in range(8)] + [frow("Y", full_values(3), disclosed=4)]
    board = score_board(rows, F1, frozen=None, as_of="2026-09-26", code_version="test")
    ranked = [r for r in board.rows if r.status == "ranked"]
    order = {"STRICT": 0, "FULL": 1, "BELOW": 2}
    assert ranked == sorted(ranked, key=lambda r: (order[r.grade], -r.score, r.symbol))
    assert [r.rank for r in ranked] == list(range(1, 9)) and ranked[0].symbol == "S7"
    y = next(r for r in board.rows if r.symbol == "Y")
    assert (y.status, y.observe_reason, y.rank, y.grade, y.score) == ("observe", "insufficient_quarters",
                                                                      None, None, None)
    assert board.params_bootstrap and all("params_bootstrap" in r.badges for r in ranked)
    assert board.counts == {"ranked": 8, "observe": 1, "excluded": 0}
    assert (board.scheme_hash, board.code_version) == (scheme_hash(F1), "test")
    assert all(0 <= v <= 100 for r in ranked for v in r.family_scores.values() if v is not None)


def test_within_sector_score_needs_five_ranked_peers():
    rows = ([frow(f"T{i}", full_values(i), sector="Technology") for i in range(5)] +
            [frow(f"E{i}", full_values(i), sector="Energy") for i in range(4)])
    board = score_board(rows, F1, frozen=None, as_of="2026-09-26", code_version="t")
    assert all(r.within is not None and 0 <= r.within <= 100 for r in board.rows if r.symbol.startswith("T"))
    assert all(r.within is None for r in board.rows if r.symbol.startswith("E"))


def test_pe_redflag_is_a_badge_unless_the_scheme_demotes():
    rows = [frow(f"P{i}", {**full_values(i), "revision": -1.0 if i == 0 else 1.0 + i},
                 aux={"ep_ntm": 0.01 * (i + 1)}) for i in range(5)]
    board = score_board(rows, F1, frozen=None, as_of="2026-09-26", code_version="t")
    p0 = next(r for r in board.rows if r.symbol == "P0")
    assert "pe_redflag" in p0.badges and "pe_redflag" not in p0.demotions
    assert all("pe_redflag" not in r.badges for r in board.rows if r.symbol != "P0")
    demoting = score_board(rows, replace(F1, pe_redflag_demotes=True), frozen=None, as_of="2026-09-26",
                           code_version="t")
    p0 = next(r for r in demoting.rows if r.symbol == "P0")
    assert p0.grade == "BELOW" and "pe_redflag" in p0.demotions


def test_scored_rows_carry_scheme_level_values_and_reasons():
    rows = [frow(f"S{i}", {**full_values(i), "revision": 1.0}) for i in range(6)]
    rows.append(frow("JPM", {**full_values(9), "revision": 1.0}, industry="Banks - Diversified"))
    board = score_board(rows, F1, frozen=None, as_of="2026-09-26", code_version="t")
    jpm = next(r for r in board.rows if r.symbol == "JPM")
    assert rows[-1].values["gm_level"] is not None                                 # raw value kept on FactorRow
    assert jpm.status == "ranked" and jpm.values["gm_level"] is None and jpm.missing["gm_level"] == "industry_exempt"
    assert all(r.values["revision"] == 1.0 and r.missing["revision"] == "degenerate_cross_section"
               and "revision" not in r.z for r in board.rows)
    s0 = next(r for r in board.rows if r.symbol == "S0")
    assert set(s0.missing) == {"revision"} and set(s0.values) == {f for f, _ in F1.weights}


def test_excluded_rows_do_not_enter_the_cross_section():
    rows = [frow(f"S{i}", full_values(i)) for i in range(6)] + [frow("JPM", full_values(9), industry="Banks - Diversified")]
    exfin = score_board(rows, get_scheme("F1-exfin"), frozen=None, as_of="2026-09-26", code_version="t")
    assert next(r for r in exfin.rows if r.symbol == "JPM").status == "excluded"
    base = score_board(rows[:6], get_scheme("F1-exfin"), frozen=None, as_of="2026-09-26", code_version="t")
    assert [r.score for r in exfin.rows if r.status == "ranked"] == [r.score for r in base.rows]
