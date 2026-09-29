from dataclasses import replace

import pytest

from terminal.prosperity.statements import build_quarters, visible_statements
from tests.prosperity_fixtures import hist


def _build(h, as_of, mode="replay", observed_at=None):
    return build_quarters(visible_statements(h, as_of, mode=mode, observed_at=observed_at), as_of)


def test_current_fiscal_is_newest_quarter_available_in_all_three_tables():
    h = hist()
    h.cashflow[-1]["accepted_date"] = "2026-03-05 16:00:00"   # 10-K cash flow later than income
    h.cashflow[-1]["filing_date"] = "2026-03-05"
    vis = visible_statements(h, "2026-03-01", mode="replay")
    qb = build_quarters(vis, "2026-03-01")
    assert vis.pit == "approximate" and qb.current_fiscal == "2025-09-30"
    assert [x.fiscal_date for x in qb.quarters] == ["2025-03-31", "2025-06-30", "2025-09-30"]
    assert qb.quarters[-1].period_days == 92


ASML_PLACEHOLDER = [("2024-03-31", "2024", "Q1", "2024-03-30 20:00:00"),
                    ("2024-06-30", "2024", "Q2", "2024-06-28 20:00:00")]   # market.db: accepted before quarter end


def test_placeholder_dates_are_unknown_in_replay():
    qb = _build(hist(ASML_PLACEHOLDER), "2024-07-01")
    assert qb.quarters == () and qb.current_fiscal is None and "no_current_fiscal" in qb.flags


def test_live_mode_proves_known_by_observation_date_only():
    qb = _build(hist(ASML_PLACEHOLDER), "2024-07-01", mode="live", observed_at="2024-07-01")
    assert qb.current_fiscal == "2024-06-30"
    assert (qb.quarters[-1].available_on, qb.quarters[-1].availability_basis) == ("2024-07-01", "observed_snapshot")
    with pytest.raises(ValueError):
        visible_statements(hist(ASML_PLACEHOLDER), "2024-06-30", mode="live", observed_at="2024-07-01")
    with pytest.raises(ValueError):
        visible_statements(hist(ASML_PLACEHOLDER), "2024-07-01", mode="live")


ASML_EARLY = [("2022-06-30", "2022", "Q2", "2022-07-02 06:00:00")]   # market.db: accepted 2 days after quarter end
ASML_EARNINGS = [{"fiscal_date": "2022-06-30", "announce_date": "2022-07-20", "eps_actual": 1.0,
                  "match_method": "statement_window"}]                # results released 07-20


def test_replay_statement_date_is_floored_at_same_quarter_earnings():
    h = hist(ASML_EARLY, earnings=ASML_EARNINGS)
    assert _build(h, "2022-07-10").current_fiscal is None                 # fails if earnings_rows is not passed
    q = _build(h, "2022-07-20")
    assert q.current_fiscal == "2022-06-30"
    assert (q.quarters[-1].available_on, q.quarters[-1].availability_basis) == ("2022-07-20", "earnings_floor")
    assert "statement_date_before_earnings" in q.quarters[-1].labels
    live = _build(h, "2022-07-10", mode="live", observed_at="2022-07-10")   # observed archive: floor not applied
    assert live.current_fiscal == "2022-06-30"


def test_replay_after_strict_boundary_reads_vintage_versions():
    h = hist()
    vint = {t: [dict(r, _observed_at="2026-09-28T13:04:27Z") for r in rows]
            for t, rows in (("income", h.income), ("balance", h.balance), ("cashflow", h.cashflow))}
    for r in vint["income"]:
        r.update(revenue=90.0, cost_of_revenue=36.0, gross_profit=54.0)
    h = replace(h, vintage=vint)
    vis = visible_statements(h, "2026-09-29", mode="replay")
    assert vis.pit == "strict" and vis.rows["income"][0]["revenue"] == 90.0
    late = dict(vint["income"][-1], _observed_at="2026-10-02T02:00:00Z", revenue=1.0)
    h2 = replace(h, vintage={**vint, "income": vint["income"] + [late]})
    assert visible_statements(h2, "2026-09-30", mode="replay").rows["income"][-1]["revenue"] == 90.0
    assert visible_statements(h, "2026-09-29", mode="live", observed_at="2026-09-29").pit == "live"


def test_hard_quality_issue_nulls_field_and_keeps_quarter():
    h = hist()
    h.income[1]["gross_profit"] = 90.0
    q2 = _build(h, "2026-03-31").quarters[1]
    assert q2.gross_profit is None and q2.revenue == 100.0
    assert "gross_profit:q8_gross_profit_identity" in q2.nulled


def test_semiannual_reporter_flag():
    rows = [("2024-06-30", "2024", "Q4", "2024-08-30 10:00:00"), ("2024-12-31", "2025", "Q2", "2025-02-18 10:00:00"),
            ("2025-06-30", "2025", "Q4", "2025-08-22 10:00:00"), ("2025-12-31", "2026", "Q2", "2026-02-17 10:00:00")]
    assert "semiannual_reporter" in _build(hist(rows), "2026-03-31").flags     # BHP-like


def test_alignment_conflict_fails_closed():
    h = hist()
    h.balance[0]["period"] = "Q2"
    qb = _build(h, "2026-03-31")
    assert qb.quarters == () and qb.current_fiscal is None and "statement_alignment_conflict" in qb.flags
