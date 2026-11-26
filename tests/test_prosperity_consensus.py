from tests.prosperity_fixtures import replay_cutoff
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
    got = pre_announcement_consensus(q, [], [("2026-04-28", 50.0), ("2026-04-29", 55.0)], cutoff=replay_cutoff(q.announce_date))
    assert (got.value, got.source, got.price_pre_announce) == (1.00, "vendor_estimate", 50.0)


def test_pre_announcement_uses_last_snapshot_strictly_before_announce():
    q = EpsQuarter("2026-06-30", "2026-08-01", 1.30, 1.00, ())
    rows = [est("2026-07-25", "2026-06-28", 1.20), est("2026-08-01", "2026-06-28", 1.25),
            est("2026-07-18", "2026-06-28", 1.15)]
    got = pre_announcement_consensus(q, rows, [("2026-07-31", 80.0)], cutoff=replay_cutoff(q.announce_date))
    assert (got.value, got.source, got.snapshot_date) == (1.20, "local_snapshot", "2026-07-25")


def test_pre_announcement_after_snapshots_start_never_falls_back_to_vendor():
    q = EpsQuarter("2026-06-30", "2026-08-01", 1.30, 1.00, ())
    got = pre_announcement_consensus(q, [], [("2026-07-31", 80.0)], cutoff=replay_cutoff(q.announce_date))
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
    got = ntm_eps(NVDA_EST, NVDA_EPS, "2026-08-15", cutoff=replay_cutoff("2026-08-15"))
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
    got = ntm_eps(rows, eps, "2026-09-26", cutoff=replay_cutoff("2026-09-26"))
    assert got.basis == "ntm_fy_blend" and abs(got.value - 5.0) < 1e-9   # 2/4 × FY1 + 2/4 × FY2


def test_ntm_missing_when_snapshot_stale_or_window_broken():
    assert ntm_eps(NVDA_EST, NVDA_EPS, "2026-09-01", cutoff=replay_cutoff("2026-09-01")).missing_reason == "snapshot_stale"
    broken = [r for r in NVDA_EST if r["fiscal_date"] != "2026-10-26"]
    assert ntm_eps(broken, NVDA_EPS, "2026-08-15", cutoff=replay_cutoff("2026-08-15")).missing_reason == "ntm_window_incomplete"


def test_ntm_null_on_week_over_week_jump():
    prev = [est("2026-08-08", r["fiscal_date"], r["eps_avg"] / 3, r["num_analysts_eps"]) for r in NVDA_EST]
    got = ntm_eps(prev + NVDA_EST, NVDA_EPS, "2026-08-15", cutoff=replay_cutoff("2026-08-15"))
    assert got.value is None and got.missing_reason == "e2_consensus_jump"


def test_revision_uses_fixed_quarter_set_from_base_snapshot():
    base = [est("2026-08-29", f, e) for f, e in [("2026-09-30", 1.0), ("2026-12-31", 1.1),
                                                 ("2027-03-31", 1.2), ("2027-06-30", 1.3)]]
    cur = [est("2026-09-26", f, e) for f, e in [("2026-09-30", 1.1), ("2026-12-31", 1.2),
                                                ("2027-03-31", 1.3), ("2027-06-30", 1.4),
                                                ("2027-09-30", 9.9)]]
    got = revision_inputs(base + cur, [eq("2026-06-30", "2026-07-30", 1.0)], "2026-09-26", cutoff=replay_cutoff("2026-09-26"))
    assert got.window_weeks == 4
    assert got.quarters == ("2026-09-30", "2026-12-31", "2027-03-31", "2027-06-30")
    assert abs(got.delta_eps - 0.4) < 1e-9


def test_revision_allows_gradual_rise_but_nulls_a_single_week_jump():
    eps = [eq("2026-06-30", "2026-07-30", 1.0)]
    quarters = ("2026-09-30", "2026-12-31")
    gradual = [("2026-08-29", 1.0), ("2026-09-05", 1.4), ("2026-09-12", 1.9),
               ("2026-09-19", 2.3), ("2026-09-26", 2.5)]
    got = revision_inputs([est(s, f, v) for s, v in gradual for f in quarters], eps, "2026-09-26", cutoff=replay_cutoff("2026-09-26"))
    assert got.missing_reason is None and abs(got.delta_eps - 3.0) < 1e-9
    jump = [("2026-08-29", 1.0), ("2026-09-05", 1.0), ("2026-09-12", 2.5),
            ("2026-09-19", 2.5), ("2026-09-26", 2.5)]
    got = revision_inputs([est(s, f, v) for s, v in jump for f in quarters], eps, "2026-09-26", cutoff=replay_cutoff("2026-09-26"))
    assert got.delta_eps is None and got.missing_reason == "e2_consensus_jump"


def test_revision_window_switches_to_13_weeks():
    got = revision_inputs([est("2026-10-17", "2026-12-31", 1.0)], [], "2026-10-17", cutoff=replay_cutoff("2026-10-17"))
    assert got.window_weeks == 13 and got.missing_reason == "no_base_snapshot"


def test_build_consensus_gates_per_share_inputs_on_unit():
    kw = dict(estimates=NVDA_EST, announced=NVDA_EPS, current_eps=NVDA_EPS[-1],
              closes=[("2026-05-19", 130.0)], as_of="2026-08-15")
    blocked = build_consensus(unit_ok=False, **kw, cutoff=replay_cutoff(kw["as_of"]), forward_runs_available=True)
    assert blocked.ntm.value is None and blocked.ttm_eps is None and blocked.unit_factor is None
    assert blocked.missing_reasons["ntm"] == "unit_unverified"
    ok = build_consensus(unit_ok=True, **kw, cutoff=replay_cutoff(kw["as_of"]), forward_runs_available=True)
    assert abs(ok.ntm.value - 10.01455) < 1e-9 and ok.unit_factor == 1.0


def test_fy_blend_rejects_a_fiscal_year_beyond_the_next_twelve_months():
    eps = [eq("2025-09-30", "2025-10-30", 1.0), eq("2025-12-31", "2026-01-30", 1.0),
           eq("2026-03-31", "2026-04-30", 1.0), eq("2026-06-30", "2026-07-30", 1.0)]
    rows = [est("2026-09-26", "2026-09-30", 1.1, 8), est("2026-09-26", "2026-12-31", 1.2, 5),
            est("2026-09-26", "2027-03-31", 1.3, 2), est("2026-09-26", "2027-06-30", 1.4, 1),
            est("2026-09-26", "2027-12-31", 5.6, 6, period="FY"),                # in-progress FY2026 missing
            est("2026-09-26", "2028-12-31", 7.0, 4, period="FY")]
    got = ntm_eps(rows, eps, "2026-09-26", cutoff=replay_cutoff("2026-09-26"))
    assert got.value is None and got.missing_reason == "ntm_thin_coverage"


def test_revision_jump_across_a_snapshot_missing_the_quarter_is_caught():
    eps = [eq("2026-06-30", "2026-07-30", 1.0)]
    rows = [est("2026-08-29", "2026-09-30", 1.0), est("2026-08-29", "2026-12-31", 1.0),
            est("2026-09-12", "2026-12-31", 1.0),                                # 09-30 absent this week
            est("2026-09-26", "2026-09-30", 3.0), est("2026-09-26", "2026-12-31", 1.0)]
    got = revision_inputs(rows, eps, "2026-09-26", cutoff=replay_cutoff("2026-09-26"))
    assert got.delta_eps is None and got.missing_reason == "e2_consensus_jump"


def test_ntm_jump_check_uses_the_last_snapshot_that_had_each_quarter():
    two_weeks_ago = [est("2026-08-01", r["fiscal_date"], r["eps_avg"] / 3, r["num_analysts_eps"]) for r in NVDA_EST]
    last_week = [est("2026-08-08", "2027-07-26", 3.1, 11)]                      # lacks the NTM quarters
    got = ntm_eps(two_weeks_ago + last_week + NVDA_EST, NVDA_EPS, "2026-08-15", cutoff=replay_cutoff("2026-08-15"))
    assert got.value is None and got.missing_reason == "e2_consensus_jump"


# P0-1: market.db SNDK weekly snapshots (Q rows, 2026-10 … 2027-10 quarters). FMP moved the quarter ends
# from the -03 to the -02 dates on 10-31; the 11-21 snapshot is still on the pre-split basis.
SNDK_WEEKS = [
    ("2026-07-13", 43.428, 51.337, 49.934, 50.56, 54.538), ("2026-07-18", 43.839, 51.911, 50.424, 51.529, 54.538),
    ("2026-07-25", 43.839, 51.911, 50.424, 51.529, 54.538), ("2026-08-01", 43.936, 51.966, 49.69, 51.194, 55.203),
    ("2026-08-08", 45.71, 52.368, 53.025, 56.805, 59.517), ("2026-08-15", 45.71, 52.368, 53.025, 56.805, 59.517),
    ("2026-08-22", 46.031, 52.195, 53.721, 58.18, 61.574), ("2026-08-29", 46.031, 52.195, 53.721, 58.18, 61.574),
    ("2026-09-05", 46.031, 52.195, 53.721, 58.18, 61.574), ("2026-09-12", 46.031, 52.195, 53.721, 58.18, 61.361),
    ("2026-09-19", 46.121, 52.405, 54.01, 58.531, 61.852), ("2026-09-26", 46.108, 52.43, 53.974, 58.509, 61.706),
    ("2026-10-03", 46.108, 52.43, 53.974, 58.51, 61.706), ("2026-10-10", 46.174, 52.544, 54.05, 58.673, 61.706),
    ("2026-10-17", 46.174, 52.544, 54.05, 58.673, 61.706), ("2026-10-24", 46.205, 52.772, 54.55, 59.147, 61.706),
    ("2026-10-31", 46.67, 53.763, 54.674, 59.33, 62.427), ("2026-11-07", 46.67, 56.97, 57.438, 63.614, 68.945),
    ("2026-11-14", 46.67, 56.97, 57.438, 63.614, 69.279), ("2026-11-21", 46.67, 56.97, 57.438, 63.614, 69.279)]
SNDK_SPLIT = [{"date": "2026-11-20", "numerator": 8.0, "denominator": 1.0}]


def sndk_estimates(weeks=SNDK_WEEKS):
    out = []
    for snap, *values in weeks:
        day = "02" if snap >= "2026-10-31" else "03"
        fiscals = [f"{y}-{m}-{day}" for y, m in (("2026", "10"), ("2027", "01"), ("2027", "04"), ("2027", "07"),
                                                  ("2027", "10"))]
        out += [est(snap, f, v) for f, v in zip(fiscals, values)]
    return out


SNDK_Q = EpsQuarter("2026-10-02", "2026-10-29", 6.19, 5.83, ())      # fmp_earnings, already on the 8:1 basis
SNDK_ANNOUNCED = [eq("2026-07-03", "2026-08-13", 4.91), SNDK_Q]


def _sndk_consensus(estimates, as_of="2026-11-21", **kw):
    return build_consensus(estimates=estimates, announced=SNDK_ANNOUNCED, current_eps=SNDK_Q,
                           closes=[("2026-10-28", 1404.77 / 8), ("2026-11-20", 79.24)], as_of=as_of, unit_ok=True,
                           cutoff=replay_cutoff(as_of), forward_runs_available=True, **kw)


def test_every_pre_split_snapshot_is_put_on_the_latest_basis():
    c = _sndk_consensus(sndk_estimates(), splits=SNDK_SPLIT, last_close_date="2026-11-24")
    assert abs(c.pre_announce.value - 46.205 / 8) < 1e-9 and c.pre_announce.source == "local_snapshot"
    assert abs(c.ntm.value - (56.97 + 57.438 + 63.614 + 69.279) / 8) < 1e-9 and c.ntm.snapshot_date == "2026-11-21"
    assert abs(c.revision.delta_eps - (247.301 - 225.67) / 8) < 1e-9 and c.revision.base_snapshot == "2026-08-22"
    surprise = (6.19 - c.pre_announce.value) / c.pre_announce.price_pre_announce * 100
    assert abs(surprise - 0.236) < 0.005
    assert abs(c.revision.delta_eps / 79.24 * 100 - 3.41) < 0.01 and abs(79.24 / c.ntm.value - 2.56) < 0.01
    # without split metadata the old behaviour stays: the 8:1 unit is still mixed in
    raw = _sndk_consensus(sndk_estimates())
    assert abs(raw.ntm.value - 247.301) < 1e-9


def test_post_split_snapshot_pairs_quarters_across_fiscal_date_drift():
    # ref 10-24 names 2027-01-03 …, the 11-21 snapshot names 2027-01-02 …: ±ESTIMATE_MATCH_DAYS pairs them
    weeks = [w for w in SNDK_WEEKS if not "2026-10-31" <= w[0] <= "2026-11-14"]
    c = _sndk_consensus(sndk_estimates(weeks), splits=SNDK_SPLIT, last_close_date="2026-11-24")
    assert c.ntm.snapshot_date == "2026-11-21" and abs(c.ntm.value - 247.301 / 8) < 1e-9


def _mnst(snap, a, b, c):
    return [est(snap, "2026-09-30", a), est(snap, "2026-12-31", b), est(snap, "2027-03-31", c)]


def test_on_latest_basis_divides_only_snapshots_still_on_the_old_unit():
    from terminal.prosperity.consensus import on_latest_basis
    rows = (_mnst("2026-08-08", 0.80, 0.85, 0.90) + _mnst("2026-08-15", 0.80, 0.86, 0.90)
            + _mnst("2026-08-22", 0.40, 0.43, 0.45) + _mnst("2026-08-29", 0.41, 0.43, 0.46))
    out, divisor, dropped = on_latest_basis(rows, (("2026-08-11", 2.0),))
    assert divisor == {"2026-08-08": 2.0, "2026-08-15": 2.0, "2026-08-22": 1.0, "2026-08-29": 1.0}
    assert dropped == () and [r["eps_avg"] for r in out[:3]] == [0.40, 0.425, 0.45]
    assert [r["eps_avg"] for r in out[6:9]] == [0.40, 0.43, 0.45] and rows[0]["eps_avg"] == 0.80
    # a snapshot past the verify window that the reference still proves unswitched keeps being divided
    late = _mnst("2026-08-08", 0.80, 0.85, 0.90) + _mnst("2026-09-19", 0.81, 0.86, 0.91)
    _, divisor, _ = on_latest_basis(late, (("2026-08-11", 2.0),))
    assert divisor["2026-09-19"] == 2.0
    # neither 1 nor the split ratio: unknown unit, dropped
    odd = _mnst("2026-08-08", 0.80, 0.85, 0.90) + _mnst("2026-08-15", 0.53, 0.57, 0.60)
    out, divisor, dropped = on_latest_basis(odd, (("2026-08-11", 2.0),))
    assert dropped == ("2026-08-15",) and {r["snapshot_date"] for r in out} == {"2026-08-08"}


def test_on_latest_basis_without_a_reference_drops_the_verify_window():
    from terminal.prosperity.consensus import on_latest_basis
    rows = _mnst("2026-07-18", 0.4, 0.43, 0.45) + _mnst("2026-08-08", 0.4, 0.43, 0.45) + _mnst("2026-08-22", 0.4, 0.43, 0.45)
    out, divisor, dropped = on_latest_basis(rows, (("2026-07-15", 2.0),))
    assert dropped == ("2026-07-18", "2026-08-08") and divisor == {"2026-08-22": 1.0}
    assert {r["snapshot_date"] for r in out} == {"2026-08-22"}
    # no overlapping quarter to compare: dropped inside the window, accepted after it
    ref = [est("2026-08-08", "2026-06-30", 0.8)]
    out, divisor, dropped = on_latest_basis(ref + _mnst("2026-08-15", 0.4, 0.43, 0.45) + _mnst("2026-09-12", 0.4, 0.43, 0.45),
                                            (("2026-08-11", 2.0),))
    assert dropped == ("2026-08-15",) and divisor == {"2026-08-08": 2.0, "2026-09-12": 1.0}
    assert on_latest_basis(rows, ()) == ([dict(r) for r in rows], {}, ())


def test_split_events_ignore_future_and_spin_off_records():
    from terminal.prosperity.consensus import split_events
    splits = [{"date": "2026-12-07", "numerator": 3.0, "denominator": 1.0},        # SITM, after the last close
              {"date": "2025-11-03", "numerator": 239.0, "denominator": 100.0},    # DD spin-off factor
              {"date": "2026-11-20", "numerator": 8.0, "denominator": 1.0},
              {"date": "2026-06-24", "numerator": 1.0, "denominator": 3.0}]
    assert split_events(splits, "2026-11-24") == (("2026-06-24", 1 / 3), ("2026-11-20", 8.0))
    assert split_events(splits, None) == ()
    c = _sndk_consensus(sndk_estimates(), splits=[{"date": "2026-12-07", "numerator": 8.0, "denominator": 1.0}],
                        last_close_date="2026-11-24")
    assert abs(c.ntm.value - 247.301) < 1e-9


def test_pre_announcement_blanks_a_value_on_the_other_side_of_a_split():
    events = (("2026-06-01", 2.0),)
    closes = [("2026-04-28", 50.0)]
    restated = EpsQuarter("2026-03-31", "2026-04-29", 0.5, 1.0, ())     # actual restated, vendor estimate not
    got = pre_announcement_consensus(restated, [], closes, cutoff=replay_cutoff("2026-04-29"), events=events)
    assert (got.value, got.source, got.missing_reason) == (None, "vendor_estimate", "split_basis_unconfirmed")
    # review fix 3: actual / estimate at +ratio is a real beat unless street_eps divided both by the ratio
    beat = EpsQuarter("2026-03-31", "2026-04-29", 2.0, 1.0, ())
    got = pre_announcement_consensus(beat, [], closes, cutoff=replay_cutoff("2026-04-29"), events=events)
    assert (got.value, got.missing_reason) == (1.0, None)
    # local snapshots are already on the latest basis: only an unrestated (too large) actual is a mismatch
    q = EpsQuarter("2026-06-30", "2026-08-01", 2.4, None, ())
    rows = [est("2026-07-25", "2026-06-28", 1.20)]
    late = (("2026-08-10", 2.0),)
    got = pre_announcement_consensus(q, rows, [("2026-07-31", 80.0)], cutoff=replay_cutoff("2026-08-01"), events=late)
    assert got.missing_reason == "split_basis_unconfirmed"
    small = EpsQuarter("2026-06-30", "2026-08-01", 0.6, None, ())
    got = pre_announcement_consensus(small, rows, [("2026-07-31", 80.0)], cutoff=replay_cutoff("2026-08-01"), events=late)
    assert (got.value, got.missing_reason) == (1.20, None)
    # snapshot taken after the split: nothing to guard
    got = pre_announcement_consensus(q, rows, [("2026-07-31", 80.0)], cutoff=replay_cutoff("2026-08-01"),
                                     events=(("2026-07-20", 2.0),))
    assert (got.value, got.missing_reason) == (1.20, None)


def test_normal_inputs_are_unchanged_by_split_metadata():
    # NVDA-type: split long before every snapshot and quarter
    q = EpsQuarter("2026-07-31", "2026-08-27", 1.05, 1.0, ())
    rows = [est("2026-08-22", "2026-07-27", 1.01), est("2026-08-22", "2026-10-26", 1.2),
            est("2026-08-22", "2027-01-25", 1.3), est("2026-08-22", "2027-04-26", 1.4),
            est("2026-08-22", "2027-07-26", 1.5)]
    kw = dict(estimates=rows, announced=[q], current_eps=q, closes=[("2026-08-26", 180.0), ("2026-08-28", 181.0)],
              as_of="2026-08-29", unit_ok=True, cutoff=replay_cutoff("2026-08-29"), forward_runs_available=True)
    base = build_consensus(**kw)
    with_splits = build_consensus(**kw, splits=[{"date": "2024-06-10", "numerator": 10.0, "denominator": 1.0}],
                                  last_close_date="2026-08-28")
    assert with_splits == base and base.pre_announce.value == 1.01



def test_non_finite_consensus_inputs_become_missing_values():
    # review fix 2: NaN/Inf estimates, vendor estimates, actuals and prices never reach the archive
    import math
    nan, inf = float("nan"), float("inf")
    q = EpsQuarter("2026-03-31", "2026-04-29", 1.10, inf, ())
    got = pre_announcement_consensus(q, [], [("2026-04-27", 50.0), ("2026-04-28", inf)], cutoff=replay_cutoff(q.announce_date))
    assert (got.value, got.missing_reason, got.price_pre_announce) == (None, "no_vendor_estimate", 50.0)
    late = EpsQuarter("2026-06-30", "2026-08-01", 1.30, None, ())
    got = pre_announcement_consensus(late, [est("2026-07-25", "2026-06-28", nan)], [("2026-07-31", 80.0)],
                                     cutoff=replay_cutoff(late.announce_date))
    assert got.value is None and got.missing_reason == "no_pre_announce_value"
    assert ttm_eps([eq("2025-09-30", "2025-10-30", 1.0), eq("2025-12-31", "2026-01-30", 1.0),
                    eq("2026-03-31", "2026-04-30", inf), eq("2026-06-30", "2026-07-30", 1.0)]) == (None, ())
    anchor = eq("2026-06-30", "2026-07-30", 1.0)
    rows = [est("2026-08-01", f, v) for f, v in (("2026-09-30", 1.0), ("2026-12-31", nan), ("2027-03-31", 1.2),
                                                   ("2027-06-30", 1.3))]
    ntm = ntm_eps(rows, [anchor], "2026-08-01", cutoff=replay_cutoff("2026-08-01"))
    assert ntm.value is None or math.isfinite(ntm.value)



def _vendor(q, events):
    return pre_announcement_consensus(q, [], [("2024-07-29", 1.0)], cutoff=replay_cutoff(q.announce_date), events=events)


def test_vendor_estimate_guard_is_one_sided_and_skips_small_adjustments():
    # review fix 3, market.db: unadjusted vendor estimates against post-split actuals sit at 1 / ratio
    fast = EpsQuarter("2024-03-31", "2024-04-11", 0.26, 0.53, ())               # FAST, 2025-05-22 2:1
    assert _vendor(fast, (("2025-05-22", 2.0),)).missing_reason == "split_basis_unconfirmed"
    amcr = EpsQuarter("2024-12-31", "2025-02-04", 0.8, 0.16, ())                 # AMCR, 2026-01-15 1:5
    assert _vendor(amcr, (("2026-01-15", 0.2),)).missing_reason == "split_basis_unconfirmed"
    mstr = EpsQuarter("2024-06-30", "2024-07-30", -0.76, -0.09267, ())           # MSTR miss, GAAP -0.57
    got = _vendor(mstr, (("2024-08-08", 10.0),))
    assert (got.value, got.missing_reason) == (-0.09267, None)
    vmrk = EpsQuarter("2025-09-30", "2025-10-29", 0.76, 0.4276, ())              # VMRK beat before 2026-08-17 2:1
    assert _vendor(vmrk, (("2026-08-17", 2.0),)).missing_reason is None
    bbd = EpsQuarter("2021-09-29", "2021-10-28", 0.11, 0.12, ())                 # BBD 1.1x stock dividend
    assert _vendor(bbd, (("2022-04-19", 1.1),)).missing_reason is None
    qgen = EpsQuarter("2025-09-30", "2025-11-04", 0.64, 0.61, ())                # QGEN 0.95 adjustment
    assert _vendor(qgen, (("2026-01-08", 0.95),)).missing_reason is None


def test_a_double_divided_vendor_estimate_is_still_caught_after_a_rescale():
    # APH 2024-12-31: street_eps divided actual and the already-adjusted estimate by 2
    aph = EpsQuarter("2024-12-31", "2025-01-22", 0.275, 0.12545, ("eps_split_rescaled",))
    assert _vendor(aph, (("2026-09-03", 2.0),)).missing_reason == "split_basis_unconfirmed"
    plain = EpsQuarter("2024-12-31", "2025-01-22", 0.275, 0.12545, ())
    assert _vendor(plain, (("2026-09-03", 2.0),)).missing_reason is None


def test_a_partially_switched_snapshot_is_dropped():
    # review fix 5: the median passed 2 switched quarters + 1 old-unit quarter as "switched"
    from terminal.prosperity.consensus import on_latest_basis
    ref = _mnst("2026-08-08", 0.80, 0.86, 0.90)
    mixed = _mnst("2026-08-15", 0.40, 0.43, 0.90)
    switched = _mnst("2026-08-22", 0.40, 0.43, 0.45)
    stale = _mnst("2026-08-29", 0.81, 0.86, 0.91)
    out, divisor, dropped = on_latest_basis(ref + mixed + switched, (("2026-08-11", 2.0),))
    assert dropped == ("2026-08-15",) and divisor == {"2026-08-08": 2.0, "2026-08-22": 1.0}
    out, divisor, dropped = on_latest_basis(ref + stale, (("2026-08-11", 2.0),))
    assert dropped == () and divisor == {"2026-08-08": 2.0, "2026-08-29": 2.0}
