from src.data.fundamental_value_checks import FLOW_FIELDS, apply_hard_issues, check_quarter_values


def q(date, **kw):
    row = {"date": date, "reported_currency": "USD", "revenue": 100.0, "cost_of_revenue": 40.0,
           "gross_profit": 60.0, "net_income": 10.0, "operating_cash_flow": 20.0,
           "capital_expenditure": -5.0, "free_cash_flow": 15.0,
           "goodwill_and_intangible_assets": 50.0, "total_assets": 500.0}
    row.update(kw)
    return row


def codes(issues):
    return sorted((i.code, i.fiscal_date) for i in issues)


def test_clean_series_has_no_hard_issues():
    rows = [q("2025-03-31"), q("2025-06-30"), q("2025-09-30")]
    assert [i for i in check_quarter_values(rows) if i.severity == "hard"] == []


def test_q8_identity_break_nulls_gross_profit_only():
    rows = [q("2025-03-31"), q("2025-06-30", gross_profit=70.0)]
    issues = check_quarter_values(rows)
    assert ("q8_gross_profit_identity", "2025-06-30") in codes(issues)
    fixed = apply_hard_issues(rows, issues)
    assert fixed[1]["gross_profit"] is None and fixed[1]["revenue"] == 100.0
    assert "gross_profit:q8_gross_profit_identity" in fixed[1]["_nulled"]
    assert rows[1]["gross_profit"] == 70.0


def test_q8_tolerates_one_percent_and_zero_cor_financials():
    rows = [q("2025-03-31", gross_profit=60.9),
            q("2025-06-30", cost_of_revenue=0.0, gross_profit=100.0)]
    assert not [i for i in check_quarter_values(rows) if i.code == "q8_gross_profit_identity"]


def test_q9_sign_rules():
    rows = [q("2025-03-31", revenue=-5.0, gross_profit=-45.0),
            q("2025-06-30", cost_of_revenue=-10.0, gross_profit=110.0)]
    got = codes(check_quarter_values(rows))
    assert ("q9_negative_revenue", "2025-03-31") in got
    assert ("q9_negative_cost_of_revenue", "2025-06-30") in got
    assert ("q9_gross_profit_exceeds_revenue", "2025-06-30") in got


def test_q10_unit_segment_nulls_only_the_off_scale_run():
    # YPF (market.db): 2025Q1–Q2 revenue reported ×1/1000; re-entry ratio 1428 includes real growth
    revs = [("2024-09-30", 5.06e12), ("2024-12-31", 4.84e12), ("2025-03-31", 4.6e9),
            ("2025-06-30", 4.641e9), ("2025-09-30", 6.63e12)]
    rows = [q(d, revenue=r, cost_of_revenue=r * 0.6, gross_profit=r * 0.4) for d, r in revs]
    issues = [i for i in check_quarter_values(rows) if i.code == "q10_unit_scale"]
    assert sorted(i.fiscal_date for i in issues) == ["2025-03-31", "2025-06-30"]
    fixed = apply_hard_issues(rows, issues)
    assert all(fixed[k][f] is None for k in (2, 3) for f in FLOW_FIELDS)
    assert fixed[4]["revenue"] == 6.63e12 and fixed[0]["revenue"] == 5.06e12


def test_q13_currency_change_nulls_older_quarters_in_other_currency():
    rows = [q("2025-03-31", reported_currency="EUR"), q("2025-06-30", reported_currency="EUR"),
            q("2025-09-30", reported_currency="USD"), q("2025-12-31", reported_currency="USD")]
    issues = [i for i in check_quarter_values(rows) if i.code == "q13_currency_change"]
    assert sorted(i.fiscal_date for i in issues) == ["2025-03-31", "2025-06-30"]
    assert all(i.severity == "hard" and i.null_fields == FLOW_FIELDS for i in issues)
