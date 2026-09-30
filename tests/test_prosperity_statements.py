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


def _vintage(h, stamp):
    return {t: [dict(r, _observed_at=stamp) for r in rows]
            for t, rows in (("income", h.income), ("balance", h.balance), ("cashflow", h.cashflow))}


def test_strict_bound_compares_instants_not_strings():
    # Codex M4 review F3: '…T00:00:00+00:00' sorts before '…T00:00:00Z', so a next-midnight version leaked in
    h = replace(hist(), vintage=_vintage(hist(), "2026-09-30T00:00:00+00:00"))
    assert _build(h, "2026-09-29").quarters == ()
    assert len(_build(h, "2026-09-30").quarters) == 4


def test_newest_strict_version_is_chosen_by_instant_across_formats():
    vint = _vintage(hist(), "2026-09-29T10:00:00Z")
    newer = dict(vint["income"][-1], _observed_at="2026-09-29T10:00:00.500000+00:00", revenue=77.0)
    h = replace(hist(), vintage={**vint, "income": vint["income"] + [newer]})
    assert visible_statements(h, "2026-09-29", mode="replay").rows["income"][-1]["revenue"] == 77.0


def test_quarters_carry_the_observation_date_that_proves_them():
    h = replace(hist(), vintage=_vintage(hist(), "2026-09-28T13:04:27+00:00"))
    assert {q.observed_on for q in _build(h, "2026-09-29").quarters} == {"2026-09-28"}
    assert {q.observed_on for q in _build(h, "2026-09-29", "live", "2026-09-29").quarters} == {"2026-09-29"}
    assert {q.observed_on for q in _build(hist(), "2026-03-31").quarters} == {None}      # approximate replay


def test_strict_replay_without_vintage_is_missing_not_current_tables():
    # Codex M4 review F5: an empty vintage silently fell back to today's restated tables
    vis = visible_statements(hist(), "2026-09-29", mode="replay")
    assert vis.pit == "strict" and not any(vis.rows.values())
    qb = _build(hist(), "2026-09-29")
    assert qb.quarters == () and "strict_vintage_missing" in qb.flags


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


def test_alignment_conflict_drops_only_the_conflicting_quarters():
    # Codex M4 review F2: one bad quarter used to empty the whole symbol
    h = hist()
    h.balance[0]["period"] = "Q2"          # 2025-03-31 now disagrees with income, and Q2 has two balance rows
    qb = _build(h, "2026-03-31")
    assert [q.fiscal_date for q in qb.quarters] == ["2025-09-30", "2025-12-31"]
    assert qb.current_fiscal == "2025-12-31" and "statement_alignment_conflict" in qb.flags
    assert qb.dropped == ("2025-03-31", "2025-06-30")


def test_duplicate_fiscal_quarter_in_income_alone_is_not_a_second_quarter():
    h = hist()
    alias = dict(h.income[2], date="2025-10-27")    # balance / cash flow keep one Q3 row within the 120-day match
    qb = _build(replace(h, income=h.income + [alias]), "2026-03-31")
    assert [q.fiscal_date for q in qb.quarters] == ["2025-03-31", "2025-06-30", "2025-12-31"]
    assert "statement_alignment_conflict" in qb.flags and qb.dropped == ("2025-09-30", "2025-10-27")


def test_malformed_counterpart_identity_drops_only_its_quarter():
    # code review: one cash flow row with an invalid fiscal year made every per-quarter alignment raise
    h = hist()
    h.cashflow[0]["fiscal_year"] = "FY2025"
    qb = _build(h, "2026-03-31")
    assert [q.fiscal_date for q in qb.quarters] == ["2025-06-30", "2025-09-30", "2025-12-31"]
    assert qb.dropped == ("2025-03-31",) and "statement_alignment_conflict" in qb.flags


def _quarters(n):
    ends, filed = ["03-31", "06-30", "09-30", "12-31"], ["05", "08", "11", "02"]
    return [(f"{2022 + i // 4}-{ends[i % 4]}", str(2022 + i // 4), f"Q{i % 4 + 1}",
             f"{2022 + i // 4 + (i % 4 == 3)}-{filed[i % 4]}-01 16:00:00") for i in range(n)]


def test_conflicts_older_than_the_packet_window_are_not_reported():
    # code review: a 2022 conflict flagged a symbol whose 16 packet quarters are all clean
    h = hist(_quarters(18))                       # 2022Q1 … 2026Q2
    h.balance[0]["period"] = "Q2"                 # conflicts at 2022-03-31 and 2022-06-30 only
    qb = _build(h, "2026-09-26")
    assert len(qb.quarters) == 16 and qb.quarters[0].fiscal_date == "2022-09-30"
    assert qb.dropped == () and "statement_alignment_conflict" not in qb.flags


Q3_ALIAS = "2025-10-27"      # AEM-like: FY Q3 first stored under a wrong date, later repaired to the quarter end


def _aliased(repair_at, removed_at=None):
    """Vintage where Q3 was observed under Q3_ALIAS, then under 2025-09-30 at `repair_at`."""
    vint = _vintage(hist(), "2026-09-04T15:03:34Z")
    for rows in vint.values():
        rows.append(dict(rows[2], _observed_at=repair_at))
        rows[2] = dict(rows[2], date=Q3_ALIAS)
    removed = {} if removed_at is None else {t: [(Q3_ALIAS, removed_at)] for t in vint}
    return replace(hist(), vintage=vint, removed=removed)


def test_strict_replay_drops_dates_removed_by_a_recorded_repair():
    # Codex M4 review F2 (AEM/BIP/MDLN/P/SNA): the append-only vintage kept both the wrong and the repaired date
    qb = _build(_aliased("2026-09-29T03:11:58.849639Z", "2026-09-29T03:11:58.851349Z"), "2026-09-29")
    assert [q.fiscal_date for q in qb.quarters] == ["2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31"]
    assert "statement_alignment_conflict" not in qb.flags


def test_repair_takes_effect_only_from_when_it_was_recorded():
    h = _aliased("2026-10-05T01:00:00Z", "2026-10-05T01:00:00.100000Z")
    assert _build(h, "2026-09-29").quarters[2].fiscal_date == Q3_ALIAS      # the wrong date was the record then
    assert _build(h, "2026-10-05").quarters[2].fiscal_date == "2025-09-30"


def test_unrepaired_duplicate_in_strict_replay_drops_only_that_quarter():
    qb = _build(_aliased("2026-09-29T03:11:58.849639Z"), "2026-09-29")
    assert [q.fiscal_date for q in qb.quarters] == ["2025-03-31", "2025-06-30", "2025-12-31"]
    assert "statement_alignment_conflict" in qb.flags


def test_quarter_dated_by_its_results_release_carries_a_label():
    # FMP placeholder dates (both on the fiscal day); the release dates the quarter (Boss 2026-09-30)
    from datetime import date, timedelta
    from terminal.prosperity.statements import VisibleStatements, build_quarters
    fiscals = ["2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31"]
    rows = {t: [{"date": f, "fiscal_year": f[:4], "period": f"Q{i + 1}", "reported_currency": "USD",
                 "filing_date": f, "accepted_date": f + " 00:00:00", "revenue": 100.0, "gross_profit": 60.0,
                 "operating_cash_flow": 20.0, "capital_expenditure": -5.0, "free_cash_flow": 15.0,
                 "total_assets": 500.0}
                for i, f in enumerate(fiscals)] for t in ("income", "balance", "cashflow")}
    earnings = tuple({"fiscal_date": f, "eps_actual": 1.0,
                      "announce_date": (date.fromisoformat(f) + timedelta(days=30)).isoformat()} for f in fiscals)
    qb = build_quarters(VisibleStatements(rows, "approximate", None, earnings), "2026-03-31")
    assert [q.fiscal_date for q in qb.quarters] == fiscals
    assert all("statement_date_from_earnings" in q.labels for q in qb.quarters)
    assert qb.quarters[-1].availability_basis == "earnings_announcement"
