import statistics
from dataclasses import replace

import pytest

from terminal.prosperity.factors import AUX_KEYS, compute_factor_row, eps_factors, expectation_factors, statement_factors
from terminal.prosperity.schemes import SCORING_FACTORS
from tests.prosperity_fixtures import cons, eps_q, pkt, qends, qin

D8 = qends(8)


def _qs(revs, **kw):
    return [qin(f, rev=r, **kw) for f, r in zip(D8[-len(revs):], revs)]


def test_revenue_yoy_accel_and_margins_use_date_paired_base():
    qs = _qs([100, 100, 100, 100, 100, 110, 130, 140])
    qs[-1] = qin(D8[-1], rev=140, gm=0.6, nm=0.08, fcfm=0.25)
    out = statement_factors(qs)
    v = out.values
    assert v["fiscal_date"] == D8[-1]
    assert v["revenue_yoy"] == pytest.approx(40.0)        # 140 vs 100 four quarters earlier, %
    assert v["revenue_accel"] == pytest.approx(10.0)      # 40% − 30% (130 vs 100), pp
    assert v["gm_level"] == pytest.approx(60.0)
    assert v["gm_yoy"] == pytest.approx(10.0)
    assert v["fcf_margin_yoy"] == pytest.approx(5.0)
    assert v["net_margin_yoy"] == pytest.approx(-2.0)


def test_day_count_adjustment_removes_53_week_effect():
    qs = _qs([91.0] * 8)
    qs[-1] = qin(D8[-1], rev=98.0, days=98)
    assert statement_factors(qs).values["revenue_yoy"] == pytest.approx(0.0)
    assert statement_factors(qs, day_adjust=False).values["revenue_yoy"] == pytest.approx(98 / 91 * 100 - 100)


def test_missing_year_ago_quarter_is_a_gap_not_a_positional_neighbour():
    qs = _qs([100, 100, 100, 100, 120, 130, 140, 150])
    del qs[3]                                              # the current quarter's year-ago row is absent
    out = statement_factors(qs)
    assert out.values["revenue_yoy"] is None and out.missing["revenue_yoy"] == "no_yoy_base"
    assert statement_factors(qs, pairing="position").values["revenue_yoy"] is not None   # original site's [i−4]


def test_period_days_must_be_comparable_and_half_years_pair_unadjusted():
    qs = _qs([100.0] * 8)
    qs[-1] = qin(D8[-1], rev=200.0, days=182)              # a skipped quarter makes this row look six months long
    out = statement_factors(qs)
    assert out.values["revenue_yoy"] is None and out.missing["revenue_yoy"] == "period_days_out_of_range"
    semi = [qin(f, rev=r, days=182) for f, r in
            zip(["2024-06-30", "2024-12-31", "2025-06-30", "2025-12-31"], [100, 100, 120, 110])]
    out = statement_factors(semi)
    assert out.values["revenue_yoy"] == pytest.approx(10.0) and "semiannual_period" in out.labels
    assert out.missing["revenue_accel"] == "no_prior_quarter"


def test_fcf_patches_follow_original_site():
    qs = _qs([100.0] * 8)
    qs[-1] = qin(D8[-1], fcfm=2.5)                         # FCF margin 250% > 200
    assert statement_factors(qs).missing["fcf_margin_yoy"] == "fcf_outlier"
    qs[-1] = qin(D8[-1], fcfm=0.0)
    assert statement_factors(qs).missing["fcf_margin_yoy"] == "fcf_zero_placeholder"


def test_gross_margin_slope_needs_eight_contiguous_quarters():
    qs = [qin(f, gm=0.40 + 0.01 * i) for i, f in enumerate(D8)]
    assert statement_factors(qs).values["gm_slope"] == pytest.approx(1.0)       # pp per quarter
    assert statement_factors(qs[1:]).missing["gm_slope"] == "slope_needs_8_quarters"


def test_revenue_ttm_leg_uses_two_contiguous_four_quarter_windows():
    out = statement_factors(_qs([100] * 4 + [120] * 4))
    assert out.values["revenue_ttm_leg"] == pytest.approx(1.2 ** 0.25 - 1)
    assert statement_factors(_qs([100] * 7)).missing["revenue_ttm_leg"] == "ttm_needs_8_quarters"

D = [0.1, 0.2, 0.1, 0.2, 0.1, 0.2, 0.1, 0.2, 0.3]


def _eps(diffs, last="2026-06-30"):
    dates = qends(4 + len(diffs), last)
    vals = [1.0] * 4
    for d in diffs:
        vals.append(vals[-4] + d)
    return [eps_q(f, e) for f, e in zip(dates, vals)]


def test_sue_and_delta_sue_follow_north_star_formula():
    out = eps_factors(_eps(D))
    sue0 = 0.3 / statistics.stdev(D[:8])
    sue1 = 0.2 / statistics.stdev(D[:7])
    assert out.values["eps_sue"] == pytest.approx(sue0)
    assert out.values["eps_accel"] == pytest.approx(sue0 - sue1)


def test_conflict_inside_window_blocks_the_whole_factor_not_just_one_quarter():
    eps = _eps(D)
    eps[4] = replace(eps[4], eps_actual=None, labels=("eps_conflicting_quarter",))
    out = eps_factors(eps)                  # dropping the quarter would still leave 7 σ observations
    assert out.values["eps_sue"] is None and out.missing["eps_sue"] == "eps_conflicting_quarter"
    assert out.values["eps_accel"] is None


def test_conflict_outside_sue_window_only_blocks_delta_sue():
    eps = _eps([0.1] * 3 + D)               # 16 quarters
    eps[2] = replace(eps[2], eps_actual=None, labels=("eps_conflicting_quarter",))
    out = eps_factors(eps)
    assert out.values["eps_sue"] is not None
    assert out.values["eps_accel"] is None and out.missing["eps_accel"] == "eps_conflicting_quarter"


def test_delta_sue_needs_the_adjacent_prior_quarter():
    eps = _eps([0.1] * 3 + D)               # 16 quarters
    del eps[-2]                             # previous quarter missing: eps[-2] is now ~182 days back
    out = eps_factors(eps)                  # both SUEs would still be computable by position
    assert out.values["eps_sue"] is not None
    assert out.values["eps_accel"] is None and out.missing["eps_accel"] == "no_prior_quarter"


def test_rescaled_split_quarters_are_usable_but_unconfirmed_ones_block():
    eps = [replace(q, labels=("eps_split_rescaled",)) if i < 5 else q for i, q in enumerate(_eps(D))]
    assert eps_factors(eps).values["eps_sue"] is not None
    eps[1] = replace(eps[1], eps_actual=None, labels=("eps_split_unconfirmed",))
    assert eps_factors(eps).missing["eps_sue"] == "eps_split_unconfirmed"


def test_retrospective_split_quarters_are_usable_and_labelled():          # D-9, Boss 2026-09-30
    eps = [replace(q, labels=("eps_split_rescaled", "eps_split_retrospective")) if i < 5 else q
           for i, q in enumerate(_eps(D))]
    eps[6] = replace(eps[6], labels=("gaap_split_basis_break",))
    out = eps_factors(eps)
    assert out.values["eps_sue"] is not None and out.values["eps_accel"] is not None
    assert "eps_split_retrospective" in out.labels
    assert "eps_split_retrospective" not in eps_factors(_eps(D)).labels


def test_empty_eps_window_is_behind_current():
    out = eps_factors(())
    assert out.values["eps_sue"] is None and out.missing["eps_sue"] == "eps_behind_current"


def test_eps_ttm_leg_turnaround_and_loss():
    dates = qends(8)
    grow = [eps_q(f, e) for f, e in zip(dates, [1, 1, 1, 1, 1.5, 1.5, 1.5, 1.5])]
    assert eps_factors(grow).values["eps_ttm_leg"] == pytest.approx(1.5 ** 0.25 - 1)
    turn = eps_factors([eps_q(f, e) for f, e in zip(dates, [-1, -1, 0.5, 0.2, 0.5, 0.5, 0.5, 0.5])])
    assert turn.missing["eps_ttm_leg"] == "eps_ttm_turnaround" and "eps_ttm_turnaround" in turn.labels
    loss = eps_factors([eps_q(f, e) for f, e in zip(dates, [1, 1, 1, 1, -1, -1, 0.5, 0.5])])
    assert loss.missing["eps_ttm_leg"] == "eps_ttm_nonpositive"


def test_single_quarter_eps_turnaround_is_a_display_label():
    dates = qends(5)
    out = eps_factors([eps_q(f, e) for f, e in zip(dates, [-0.2, 0.1, 0.1, 0.1, 0.3])])
    assert out.values["eps_yoy_pct"] is None and "eps_turnaround" in out.labels


def test_surprise_revision_and_pe_inputs_are_price_scaled():
    p = pkt([qin(f) for f in qends(4)], [eps_q(f, 1.2) for f in qends(4)],
            cons(pre=1.0, price_pre=50.0, ntm=6.0, ttm=4.8, delta=-0.5), price=80.0)
    v = expectation_factors(p).values
    assert v["surprise"] == pytest.approx(0.2 / 50.0 * 100)
    assert v["revision"] == pytest.approx(-0.5 / 80.0 * 100)
    assert (v["ntm_eps"], v["ttm_eps"]) == (6.0, 4.8)
    assert v["ep_ntm"] == pytest.approx(6.0 / 80.0) and v["pe_ntm"] == pytest.approx(80.0 / 6.0)
    assert v["pe_ttm"] == pytest.approx(80.0 / 4.8) and v["ntm_growth"] == pytest.approx(6.0 / 4.8 - 1)


def test_expectation_missing_reasons_come_from_m4():
    qs, eps = [qin(f) for f in qends(4)], [eps_q(f, 1.2) for f in qends(4)]
    blocked = expectation_factors(pkt(qs, eps, cons(missing={"pre_announce": "unit_unverified",
                                                             "ntm": "unit_unverified",
                                                             "revision": "unit_unverified"})))
    assert blocked.missing["surprise"] == "unit_unverified" and blocked.missing["revision"] == "unit_unverified"
    assert blocked.values["ntm_eps"] is None and blocked.values["pe_ntm"] is None
    no_price = expectation_factors(pkt(qs, eps, cons(pre=1.0, price_pre=None, delta=0.1), price=None))
    assert no_price.missing["surprise"] == "price_missing" and no_price.missing["revision"] == "price_missing"

def test_factor_row_splits_scoring_values_from_aux():
    dates = qends(8)
    qs = [qin(f, rev=r) for f, r in zip(dates, [100] * 4 + [120] * 4)]
    eps = [eps_q(f, e) for f, e in zip(dates, [1.0] * 4 + [1.5] * 4)]
    row = compute_factor_row(pkt(qs, eps, cons(pre=1.4, price_pre=50.0, ntm=7.0, ttm=6.0, delta=0.1)))
    assert tuple(row.values) == SCORING_FACTORS and set(row.aux) == set(AUX_KEYS)
    assert row.values["growth_4q"] == pytest.approx(((1.2 ** 0.25 - 1) + (1.5 ** 0.25 - 1)) / 2 * 100)
    assert row.values["eps_sue"] is None and row.missing["eps_sue"] == "few_sigma_obs"     # 8 quarters only
    assert row.values["surprise"] == pytest.approx((1.5 - 1.4) / 50.0 * 100)
    assert (row.aux["ntm_eps"], row.aux["ttm_eps"]) == (7.0, 6.0)
    assert row.disclosed_quarters == 8 and row.listing_days > 730 and row.current_fiscal == dates[-1]
    assert set(row.missing) == {f for f, v in row.values.items() if v is None}


def test_growth_4q_missing_when_eps_ttm_turns_positive():
    dates = qends(8)
    qs = [qin(f, rev=r) for f, r in zip(dates, [100] * 4 + [120] * 4)]
    eps = [eps_q(f, e) for f, e in zip(dates, [-1.0, -1.0, 0.5, 0.2] + [0.5] * 4)]
    row = compute_factor_row(pkt(qs, eps))
    assert row.values["growth_4q"] is None and row.missing["growth_4q"] == "eps_ttm_turnaround"
    assert "eps_ttm_turnaround" in row.labels


def test_listing_days_is_raw_age_and_the_threshold_lives_in_the_scheme():
    qs = [qin(f) for f in qends(6)]
    assert compute_factor_row(pkt(qs, listing_date="2025-03-28")).listing_days == 547      # as_of 2026-09-26
    assert compute_factor_row(pkt(qs, listing_date="2024-09-25")).listing_days == 731
    unknown = compute_factor_row(pkt(qs, listing_date=None))
    assert unknown.listing_days is None and "listing_date_unknown" in unknown.labels


def test_packet_without_current_fiscal_is_not_blamed_on_eps():
    row = compute_factor_row(pkt([]))
    assert row.missing["eps_sue"] == "no_current_fiscal" and row.missing["growth_4q"] == "no_statements"
    assert eps_factors(()).missing["eps_sue"] == "eps_behind_current"
