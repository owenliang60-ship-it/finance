import pytest

from terminal.prosperity.factors import statement_factors
from tests.prosperity_fixtures import qends, qin

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
