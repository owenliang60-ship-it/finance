import pytest

from dataclasses import replace

from terminal.prosperity.street_eps import aligned_eps_window, announced_eps, pair_statement_dates
from tests.prosperity_fixtures import eq, er, gaap_rows, seq

SPLIT = lambda d, n, m=1.0: {"date": d, "numerator": n, "denominator": m}
# market.db street series; vendor re-adjusted only the newest quarters after each split
KLAC = seq([("2023-06-30", 5.40), ("2023-09-30", 5.74), ("2023-12-31", 6.16), ("2024-03-31", 5.26),
            ("2024-06-30", 6.60), ("2024-09-30", 0.733), ("2024-12-31", 0.82), ("2025-03-31", 0.84),
            ("2025-06-30", 0.94), ("2025-09-30", 0.88), ("2025-12-31", 0.89), ("2026-03-31", 0.94),
            ("2026-06-30", 1.05)])
ANET = seq([("2023-03-29", 1.43), ("2023-06-29", 1.58), ("2023-09-29", 1.83), ("2023-12-30", 2.08),
            ("2024-03-31", 1.99), ("2024-06-30", 2.10), ("2024-09-30", 0.60), ("2024-12-31", 0.65),
            ("2025-03-31", 0.65), ("2025-06-30", 0.73), ("2025-09-30", 0.75), ("2025-12-31", 0.82)])
ORLY = seq([("2023-03-29", 8.28), ("2023-06-29", 10.22), ("2023-09-29", 10.73), ("2023-12-30", 9.26),
            ("2024-03-31", 9.20), ("2024-06-30", 10.55), ("2024-09-30", 11.41), ("2024-12-31", 0.66),
            ("2025-03-31", 0.62), ("2025-06-30", 0.78), ("2025-09-30", 0.85), ("2025-12-31", 0.71)])
MCHP = seq([("2023-06-29", 1.64), ("2023-09-29", 1.62), ("2023-12-30", 1.08), ("2024-03-31", 0.57),
            ("2024-06-30", 0.53), ("2024-09-30", 0.46), ("2024-12-31", 0.20), ("2025-03-31", 0.11),
            ("2025-06-30", 0.27)])
SMOOTH = seq([("2023-03-31", 1.00), ("2023-06-30", 1.05), ("2023-09-30", 1.10), ("2023-12-31", 1.12),
              ("2024-03-31", 1.18), ("2024-06-30", 1.22), ("2024-09-30", 1.25), ("2024-12-31", 1.31)])


@pytest.mark.parametrize("rows,split,last_pre", [
    (KLAC, SPLIT("2026-06-12", 10.0), "2024-06-30"),
    (ANET, SPLIT("2024-12-04", 4.0), "2024-06-30"),
    (ORLY, SPLIT("2025-06-10", 15.0), "2024-09-30"),
])
def test_partial_split_adjustment_is_rescaled_in_memory(rows, split, last_pre):
    ratio = split["numerator"] / split["denominator"]
    got = announced_eps(rows, gaap_rows(rows, ratio, last_pre), [split], "2026-09-26")
    pre = [x for x in got if x.fiscal_date <= last_pre]
    assert pre and all("eps_split_rescaled" in x.labels for x in pre)
    assert not any("eps_split_rescaled" in x.labels for x in got if x.fiscal_date > last_pre)
    assert all(1 / 3 < b.eps_actual / a.eps_actual < 3 for a, b in zip(got, got[1:]))


def test_large_ratio_split_is_detected_and_rescaled():
    # BKNG-like 25:1; the real BKNG/CMG series are fully adjusted, so this partial case is synthetic
    rows = seq([("2024-03-31", 25.0), ("2024-06-30", 27.5), ("2024-09-30", 30.0), ("2024-12-31", 32.5),
                ("2025-03-31", 1.35), ("2025-06-30", 1.40), ("2025-09-30", 1.45), ("2025-12-31", 1.50)])
    got = announced_eps(rows, gaap_rows(rows, 25.0, "2024-12-31"), [SPLIT("2026-04-06", 25.0)], "2026-09-26")
    assert all("eps_split_rescaled" in x.labels for x in got if x.fiscal_date <= "2024-12-31")
    assert abs(got[3].eps_actual - 1.30) < 1e-9


def test_rescale_leaves_input_untouched_and_divides_by_ratio():
    got = announced_eps(KLAC, gaap_rows(KLAC, 10.0, "2024-06-30"), [SPLIT("2026-06-12", 10.0)], "2026-09-26")
    assert abs(next(x for x in got if x.fiscal_date == "2024-06-30").eps_actual - 0.66) < 1e-9
    assert KLAC[4]["eps_actual"] == 6.60


@pytest.mark.parametrize("rows,income,split,as_of", [
    (seq([("2023-04-30", 0.20), ("2023-07-30", 0.27), ("2023-10-29", 0.40), ("2024-01-28", 0.52),
          ("2024-04-28", 0.61), ("2024-07-28", 0.68)]), None, SPLIT("2024-06-10", 10.0), "2024-09-30"),  # NVDA: fully adjusted
    (MCHP, None, SPLIT("2021-10-13", 2.0), "2025-09-30"),                 # real decline, split predates series
    (SMOOTH, [{"date": r["fiscal_date"], "eps_diluted": r["eps_actual"] / (2.14 if r["fiscal_date"] <= "2023-12-31" else 1.34)}
              for r in SMOOTH], SPLIT("2023-06-15", 903.0, 500.0), "2025-06-30"),   # DELL-like: 903/500 adjustment factor dated inside the series; ignored since Codex P1
    (SMOOTH, [{"date": r["fiscal_date"], "eps_diluted": r["eps_actual"] / (2.39 if r["fiscal_date"] <= "2023-12-31" else 1.0)}
              for r in SMOOTH], SPLIT("2023-06-15", 239.0, 100.0), "2025-06-30"),   # DD-like 239/100: neither leg is 1, still ignored after widening
])
def test_real_moves_and_spin_off_factors_are_left_alone(rows, income, split, as_of):
    got = announced_eps(rows, income if income is not None else gaap_rows(rows), [split], as_of)
    assert [x.eps_actual for x in got] == [r["eps_actual"] for r in rows]
    assert not any(x.labels for x in got)


def test_gaap_only_break_leaves_street_untouched():
    income = gaap_rows(SMOOTH, 1.0, "2023-12-31", gaap_multiplier_before=4.0)   # GAAP left unadjusted, street fine
    got = announced_eps(SMOOTH, income, [SPLIT("2024-02-15", 4.0)], "2025-06-30")
    assert [x.eps_actual for x in got] == [r["eps_actual"] for r in SMOOTH]
    assert any("gaap_split_basis_break" in x.labels for x in got)
    assert not any(l.startswith("eps_split") for x in got for l in x.labels)


def test_conflicting_duplicates_become_missing():
    rows = [er("2023-12-31", "2024-02-19", 1.34), er("2023-12-31", "2024-03-18", 0.36),
            er("2023-12-31", "2024-04-17", 0.3651)]                   # BHP, market.db
    (x,) = announced_eps(rows, [], [], "2024-06-30")
    assert x.eps_actual is None and x.announce_date == "2024-02-19" and "eps_conflicting_quarter" in x.labels


def test_identical_duplicates_keep_earliest_announcement():
    rows = [er("2022-12-31", "2023-02-20", 2.55), er("2022-12-31", "2023-02-21", 2.55)]
    (x,) = announced_eps(rows, [], [], "2023-06-30")
    assert (x.eps_actual, x.announce_date) == (2.55, "2023-02-20")


def test_unmapped_and_future_rows_are_excluded():
    rows = [er(None, "2024-01-10", 1.0, method="none"), er("2024-03-31", "2024-04-20", 1.1, method="none"),
            er("2024-06-30", "2024-07-20", 1.2), er("2024-09-30", "2024-10-20", 1.3)]
    assert [x.fiscal_date for x in announced_eps(rows, [], [], "2024-09-30")] == ["2024-06-30"]


def test_aligned_window_ends_at_current_fiscal_and_ignores_later_announcements():
    got = announced_eps(KLAC, gaap_rows(KLAC, 10.0, "2024-06-30"), [SPLIT("2026-06-12", 10.0)], "2026-09-26")
    window, reason = aligned_eps_window(got, "2026-03-28")
    assert reason is None and window[-1].fiscal_date == "2026-03-31" and len(window) == 12
    assert aligned_eps_window(got, "2026-12-31") == ((), "eps_behind_current")


# market.db 2026-09-29 snapshot, street eps_actual vs income eps_diluted
REAL_FISCALS = ["2022-09-29", "2022-12-29", "2023-03-29", "2023-06-29", "2023-09-29", "2023-12-30", "2024-03-31",
                "2024-06-30", "2024-09-30", "2024-12-31", "2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31",
                "2026-03-31", "2026-06-30"]


def _real(street, gaap):
    return seq(list(zip(REAL_FISCALS, street))), [{"date": f, "eps_diluted": g} for f, g in zip(REAL_FISCALS, gaap)]


def test_one_break_reported_at_adjacent_boundaries_is_rescaled_once():
    # MNST: Codex flags both 2024-12-31 and 2025-03-31 for the same 2:1 break (GAAP Q4 dip)
    rows, income = _real([0.3, 0.29, 0.38, 0.39, 0.41, 0.38, 0.42, 0.41, 0.4, 0.19, 0.23, 0.26, 0.28, 0.25, 0.29, 0.3],
                         [0.15, 0.145, 0.19, 0.195, 0.215, 0.175, 0.21, 0.205, 0.19, 0.14, 0.225, 0.25, 0.265, 0.23,
                          0.29, 0.3])
    got = {x.fiscal_date: x for x in announced_eps(rows, income, [SPLIT("2023-03-28", 2.0), SPLIT("2026-08-11", 2.0)],
                                                   "2026-09-29")}
    assert abs(got["2022-09-29"].eps_actual - 0.15) < 1e-9 and abs(got["2024-09-30"].eps_actual - 0.2) < 1e-9
    assert got["2024-12-31"].eps_actual == 0.19 and got["2024-12-31"].labels == ()


def test_high_growth_does_not_hide_a_street_break():
    # APH: street halves at 2025-03-31 while GAAP is flat; 2025 growth offsets the jump in 3-quarter medians
    rows, income = _real([0.4, 0.39, 0.35, 0.36, 0.39, 0.41, 0.4, 0.44, 0.5, 0.55, 0.315, 0.405, 0.51, 0.97, 1.06, 0.68],
                         [0.2, 0.205, 0.18, 0.185, 0.21, 0.21, 0.22, 0.205, 0.24, 0.295, 0.29, 0.43, 0.485, 0.465,
                          0.36, 0.69])
    got = {x.fiscal_date: x for x in announced_eps(rows, income, [SPLIT("2024-06-12", 2.0), SPLIT("2026-09-03", 2.0)],
                                                   "2026-09-29")}
    assert abs(got["2024-12-31"].eps_actual - 0.275) < 1e-9 and "eps_split_rescaled" in got["2024-12-31"].labels
    assert got["2025-03-31"].eps_actual == 0.315
    assert not any("gaap_split_basis_break" in x.labels for x in got.values())


def test_rescale_and_blank_apply_to_vendor_estimates_too():
    rows = [dict(r, eps_estimated=r["eps_actual"] * 0.95) for r in KLAC]      # market.db: estimate on the actual's basis
    income = gaap_rows(KLAC, 10.0, "2024-06-30")
    got = {x.fiscal_date: x for x in announced_eps(rows, income, [SPLIT("2026-06-12", 10.0)], "2026-09-26")}
    assert abs(got["2024-06-30"].eps_estimated - 0.627) < 1e-9 and abs(got["2024-09-30"].eps_estimated - 0.69635) < 1e-9
    two_ratios = [SPLIT("2026-06-12", 10.0), SPLIT("2025-01-01", 9.0)]           # ambiguous ratio → unconfirmed
    blank = {x.fiscal_date: x for x in announced_eps(rows, income, two_ratios, "2026-09-26")}
    assert blank["2024-06-30"].eps_actual is None and blank["2024-06-30"].eps_estimated is None
    assert "eps_split_unconfirmed" in blank["2024-06-30"].labels


def test_replay_before_three_post_break_quarters_uses_the_full_stored_series():
    # Codex M4 review F1: KLAC replayed to 2024-12-31 mixed 0.733 (new unit) with three old-unit quarters,
    # while the price was already on the post-split basis. The split evidence is retrospective by nature.
    income, split = gaap_rows(KLAC, 10.0, "2024-06-30"), [SPLIT("2026-06-12", 10.0)]
    for as_of, last in (("2024-09-30", "2024-06-30"), ("2024-12-31", "2024-09-30"), ("2025-03-31", "2024-12-31")):
        got = announced_eps(KLAC, income, split, as_of)
        assert got[-1].fiscal_date == last                                 # still only what was announced by as_of
        assert all(1 / 3 < b.eps_actual / a.eps_actual < 3 for a, b in zip(got, got[1:]))
        assert all({"eps_split_rescaled", "eps_split_retrospective"} <= set(x.labels)
                   for x in got if x.fiscal_date <= "2024-06-30")
    ttm = sum(x.eps_actual for x in announced_eps(KLAC, income, split, "2024-12-31")[-4:])
    assert ttm == pytest.approx(0.616 + 0.526 + 0.660 + 0.733)
    late = announced_eps(KLAC, income, split, "2025-06-30")                # evidence fully visible by now
    assert all("eps_split_rescaled" in x.labels and "eps_split_retrospective" not in x.labels
               for x in late if x.fiscal_date <= "2024-06-30")


def test_unconfirmed_break_after_as_of_still_blanks_the_visible_quarters():
    income = gaap_rows(KLAC, 10.0, "2024-06-30")
    early = announced_eps(KLAC, income, [SPLIT("2026-06-12", 10.0), SPLIT("2025-01-01", 9.0)], "2024-09-30")
    assert early and all(x.eps_actual is None and "eps_split_unconfirmed" in x.labels for x in early)


def test_boundary_matching_tolerates_a_later_alias_of_the_same_quarter():
    # code review: the full-history grouping can name the boundary quarter by a later alias date
    rows = KLAC + [er("2024-10-02", "2025-09-01", 0.733)]         # same quarter and value, announced later
    got = {x.fiscal_date: x for x in announced_eps(rows, gaap_rows(KLAC, 10.0, "2024-06-30"),
                                                   [SPLIT("2026-06-12", 10.0)], "2024-12-31")}
    assert got["2024-09-30"].eps_actual == 0.733 and "eps_split_rescaled" not in got["2024-09-30"].labels
    assert abs(got["2024-06-30"].eps_actual - 0.66) < 1e-9


def test_break_seen_at_as_of_survives_later_rows_that_hide_it_in_full_history():
    # code review: a conflicting duplicate announced after as_of drops a pre-break quarter from the full audit
    rows = KLAC + [er("2024-03-31", "2025-08-15", 9.99)]
    got = announced_eps(rows, gaap_rows(KLAC, 10.0, "2024-06-30"), [SPLIT("2026-06-12", 10.0)], "2025-06-30")
    assert all(1 / 3 < b.eps_actual / a.eps_actual < 3 for a, b in zip(got, got[1:]))
    assert all("eps_split_rescaled" in x.labels for x in got if x.fiscal_date <= "2024-06-30")


# market.db 2026-09-29 snapshot: COST fmp_earnings fiscal_date / eps_actual and income_quarterly.date
COST_EPS = [("2022-08-22", 4.2), ("2022-11-22", 3.1), ("2023-02-22", 3.3), ("2023-05-22", 2.93),
            ("2023-08-22", 4.86), ("2023-11-23", 3.58), ("2024-02-15", 3.92), ("2024-05-10", 3.78),
            ("2024-08-10", 5.15), ("2024-11-24", 4.04), ("2025-02-16", 4.02), ("2025-05-11", 4.28),
            ("2025-08-31", 5.87), ("2025-11-23", 4.5), ("2026-02-15", 4.58), ("2026-05-10", 4.93),
            ("2026-08-10", 6.75)]
COST_STATEMENTS = ["2022-08-31", "2022-11-20", "2023-02-12", "2023-05-07", "2023-08-31", "2023-11-26",
                   "2024-02-18", "2024-05-12", "2024-09-01", "2024-11-24", "2025-02-16", "2025-05-11",
                   "2025-08-31", "2025-11-23", "2026-02-15", "2026-05-10", "2026-08-30"]


def cost_eps():
    return tuple(eq(f, "2026-09-24", e) for f, e in COST_EPS)


def test_eps_quarters_pair_with_the_statement_quarter_they_report():          # Boss 2026-09-30 ④
    paired = pair_statement_dates(cost_eps(), COST_STATEMENTS)
    assert [q.statement_fiscal for q in paired] == COST_STATEMENTS
    assert [q.fiscal_date for q in paired] == [f for f, _ in COST_EPS]          # FMP date kept for consensus


def test_pairing_is_one_to_one_within_half_a_quarter():
    eps = (eq("2025-12-01", "2026-01-20", 1.0), eq("2026-01-20", "2026-02-20", 1.1), eq("2026-05-19", "2026-06-20", 1.2))
    paired = pair_statement_dates(eps, ["2025-12-31", "2026-03-31", "2026-06-30"])
    # the first two both sit nearest 2025-12-31 → neither pairs; 2026-05-19 is 42 days from 2026-06-30 → pairs
    assert [q.statement_fiscal for q in paired] == [None, None, "2026-06-30"]
    near = pair_statement_dates((eq("2026-05-10", "2026-06-20", 1.0),), ["2026-06-30", "2026-03-31"])
    assert near[0].statement_fiscal == "2026-03-31"                             # 40 vs 51 days: nearest wins
    tie = pair_statement_dates((eq("2026-02-14", "2026-03-20", 1.0),), ["2025-12-31", "2026-03-31"])
    assert tie[0].statement_fiscal is None                                      # 45 days either way
    assert pair_statement_dates((eq("2026-05-17", "2026-06-20", 1.0),), ["2026-06-30"])[0].statement_fiscal \
        == "2026-06-30"                                                         # 44 days
    assert pair_statement_dates((eq("2026-05-10", "2026-06-20", 1.0),), ["2026-06-30"])[0].statement_fiscal \
        is None                                                                 # 51 days: too far


def test_window_aligns_on_the_paired_statement_date():
    assert aligned_eps_window(cost_eps()[:9], "2024-09-01") == ((), "eps_behind_current")   # 22 days off
    window, reason = aligned_eps_window(pair_statement_dates(cost_eps()[:9], COST_STATEMENTS), "2024-09-01")
    assert reason is None and window[-1].fiscal_date == "2024-08-10" and len(window) == 9
