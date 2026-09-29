from terminal.prosperity.consensus import pre_announcement_consensus, unit_verified
from terminal.prosperity.types import EpsQuarter
from tests.prosperity_fixtures import est


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
