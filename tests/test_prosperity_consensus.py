from terminal.prosperity.consensus import (build_consensus, ntm_eps, pre_announcement_consensus, revision_inputs,
                                           ttm_eps, unit_verified)
from terminal.prosperity.types import EpsQuarter
from tests.prosperity_fixtures import eq, est


def test_unit_verified_only_for_usd_non_adr():
    assert unit_verified("USD", False) is True
    assert unit_verified("USD", True) is False      # ADR ratio unknown (e.g. BHP)
    assert unit_verified("EUR", False) is False
    assert unit_verified(None, False) is False


def test_pre_announcement_uses_vendor_estimate_before_first_snapshot():
    q = EpsQuarter("2026-03-31", "2026-04-29", 1.10, 1.00, ())
    got = pre_announcement_consensus(q, [], [("2026-04-28", 50.0), ("2026-04-29", 55.0)])
    assert (got.value, got.source, got.price_pre_announce) == (1.00, "vendor_estimate", 50.0)


def test_pre_announcement_uses_last_snapshot_strictly_before_announce():
    q = EpsQuarter("2026-06-30", "2026-08-01", 1.30, 1.00, ())
    rows = [est("2026-07-25", "2026-06-28", 1.20), est("2026-08-01", "2026-06-28", 1.25),
            est("2026-07-18", "2026-06-28", 1.15)]
    got = pre_announcement_consensus(q, rows, [("2026-07-31", 80.0)])
    assert (got.value, got.source, got.snapshot_date) == (1.20, "local_snapshot", "2026-07-25")


def test_pre_announcement_after_snapshots_start_never_falls_back_to_vendor():
    q = EpsQuarter("2026-06-30", "2026-08-01", 1.30, 1.00, ())
    got = pre_announcement_consensus(q, [], [("2026-07-31", 80.0)])
    assert got.value is None and got.missing_reason == "no_pre_announce_snapshot"


# NVDA estimates from market.db weekly snapshot 2026-08-15; EPS actual values illustrative except 1.87
NVDA_EPS = [eq("2025-07-27", "2025-08-27", 1.05), eq("2025-10-26", "2025-11-19", 1.30),
            eq("2026-01-25", "2026-02-25", 1.62), eq("2026-04-26", "2026-05-20", 1.87)]
NVDA_EST = [est("2026-08-15", f, e, n) for f, e, n in [
    ("2026-04-26", 1.75203, 26), ("2026-07-26", 2.0835, 26), ("2026-10-26", 2.35755, 25),
    ("2027-01-26", 2.67497, 11), ("2027-04-26", 2.89853, 11), ("2027-07-26", 3.10377, 11)]]


def test_ttm_requires_four_contiguous_announced_quarters():
    assert ttm_eps(NVDA_EPS) == (1.05 + 1.30 + 1.62 + 1.87,
                                 ("2025-07-27", "2025-10-26", "2026-01-25", "2026-04-26"))
    assert ttm_eps(NVDA_EPS[:1] + NVDA_EPS[2:])[0] is None


def test_ntm_includes_quarter_ended_but_not_yet_announced():
    # as_of sits between NVDA's 2026-07-26 quarter end and its 2026-08-26 announcement
    got = ntm_eps(NVDA_EST, NVDA_EPS, "2026-08-15")
    assert got.basis == "quarter_sum"
    assert [f for f, _, _ in got.quarters] == ["2026-07-26", "2026-10-26", "2027-01-26", "2027-04-26"]
    assert abs(got.value - 10.01455) < 1e-9


def test_ntm_falls_back_to_fy_blend_when_far_quarters_thin():
    eps = [eq("2025-09-30", "2025-10-30", 1.0), eq("2025-12-31", "2026-01-30", 1.0),
           eq("2026-03-31", "2026-04-30", 1.0), eq("2026-06-30", "2026-07-30", 1.0)]
    rows = [est("2026-09-26", "2026-09-30", 1.1, 8), est("2026-09-26", "2026-12-31", 1.2, 5),
            est("2026-09-26", "2027-03-31", 1.3, 2), est("2026-09-26", "2027-06-30", 1.4, 1),
            est("2026-09-26", "2026-12-31", 4.4, 9, period="FY"),
            est("2026-09-26", "2027-12-31", 5.6, 6, period="FY")]
    got = ntm_eps(rows, eps, "2026-09-26")
    assert got.basis == "ntm_fy_blend" and abs(got.value - 5.0) < 1e-9   # 2/4 × FY1 + 2/4 × FY2


def test_ntm_missing_when_snapshot_stale_or_window_broken():
    assert ntm_eps(NVDA_EST, NVDA_EPS, "2026-09-01").missing_reason == "snapshot_stale"
    broken = [r for r in NVDA_EST if r["fiscal_date"] != "2026-10-26"]
    assert ntm_eps(broken, NVDA_EPS, "2026-08-15").missing_reason == "ntm_window_incomplete"


def test_ntm_null_on_week_over_week_jump():
    prev = [est("2026-08-08", r["fiscal_date"], r["eps_avg"] / 3, r["num_analysts_eps"]) for r in NVDA_EST]
    got = ntm_eps(prev + NVDA_EST, NVDA_EPS, "2026-08-15")
    assert got.value is None and got.missing_reason == "e2_consensus_jump"


def test_revision_uses_fixed_quarter_set_from_base_snapshot():
    base = [est("2026-08-29", f, e) for f, e in [("2026-09-30", 1.0), ("2026-12-31", 1.1),
                                                 ("2027-03-31", 1.2), ("2027-06-30", 1.3)]]
    cur = [est("2026-09-26", f, e) for f, e in [("2026-09-30", 1.1), ("2026-12-31", 1.2),
                                                ("2027-03-31", 1.3), ("2027-06-30", 1.4),
                                                ("2027-09-30", 9.9)]]
    got = revision_inputs(base + cur, [eq("2026-06-30", "2026-07-30", 1.0)], "2026-09-26")
    assert got.window_weeks == 4
    assert got.quarters == ("2026-09-30", "2026-12-31", "2027-03-31", "2027-06-30")
    assert abs(got.delta_eps - 0.4) < 1e-9


def test_revision_allows_gradual_rise_but_nulls_a_single_week_jump():
    eps = [eq("2026-06-30", "2026-07-30", 1.0)]
    quarters = ("2026-09-30", "2026-12-31")
    gradual = [("2026-08-29", 1.0), ("2026-09-05", 1.4), ("2026-09-12", 1.9),
               ("2026-09-19", 2.3), ("2026-09-26", 2.5)]
    got = revision_inputs([est(s, f, v) for s, v in gradual for f in quarters], eps, "2026-09-26")
    assert got.missing_reason is None and abs(got.delta_eps - 3.0) < 1e-9
    jump = [("2026-08-29", 1.0), ("2026-09-05", 1.0), ("2026-09-12", 2.5),
            ("2026-09-19", 2.5), ("2026-09-26", 2.5)]
    got = revision_inputs([est(s, f, v) for s, v in jump for f in quarters], eps, "2026-09-26")
    assert got.delta_eps is None and got.missing_reason == "e2_consensus_jump"


def test_revision_window_switches_to_13_weeks():
    got = revision_inputs([est("2026-10-17", "2026-12-31", 1.0)], [], "2026-10-17")
    assert got.window_weeks == 13 and got.missing_reason == "no_base_snapshot"


def test_build_consensus_gates_per_share_inputs_on_unit():
    kw = dict(estimates=NVDA_EST, announced=NVDA_EPS, current_eps=NVDA_EPS[-1],
              closes=[("2026-05-19", 130.0)], as_of="2026-08-15")
    blocked = build_consensus(unit_ok=False, **kw)
    assert blocked.ntm.value is None and blocked.ttm_eps is None and blocked.unit_factor is None
    assert blocked.missing_reasons["ntm"] == "unit_unverified"
    ok = build_consensus(unit_ok=True, **kw)
    assert abs(ok.ntm.value - 10.01455) < 1e-9 and ok.unit_factor == 1.0
