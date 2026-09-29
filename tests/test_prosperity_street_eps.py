import pytest

from terminal.prosperity.street_eps import aligned_eps_window, announced_eps
from tests.prosperity_fixtures import er, gaap_rows, seq

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
