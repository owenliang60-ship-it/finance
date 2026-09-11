from datetime import date, timedelta
import json

import pytest

from src.data import dollar_volume as dv
from scripts import collect_dollar_volume as collector


def rankings(n=200):
    return [dict(rank=i, symbol=f"S{i}", price=10., volume=10000-i,
                 dollar_volume=10.*(10000-i), market_cap=20e9)
            for i in range(1, n+1)]


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "dv.db"
    dv.init_db(path)
    return path


def seed(db, day, n=200, scanned=1000):
    rows = rankings(n)
    reference = dv.latest_quality_reference(day, db)
    evidence = dv.make_quality_evidence(rows, scanned, [r["symbol"] for r in rows], reference)
    dv.store_daily_rankings(day, rows, db_path=db,
                           stats=dict(total_scanned=scanned, stored=n, status="ok"), evidence=evidence)


def wire(monkeypatch, db, snapshots):
    monkeypatch.setattr(collector, "FMPClient", lambda: object())
    monkeypatch.setattr(collector.time, "sleep", lambda _: None)
    calls = []
    def fetch(*a, **k):
        calls.append(1)
        return snapshots[min(len(calls)-1, len(snapshots)-1)], 3
    monkeypatch.setattr(collector, "fetch_all_stocks", fetch)
    return calls


def stocks(n=1000, valid=1000):
    return [dict(symbol=f"S{i+1}", price=10., volume=(10000-i if i<valid else 0))
            for i in range(n)]


def test_partial_retry_fails_without_writing(db, monkeypatch):
    calls = wire(monkeypatch, db, [stocks(574, 95)])
    result = collector.collect_daily("2026-09-04", db_path=db)
    assert result["status"] == "unavailable" and not result["rankings"]
    assert len(calls) == 2
    assert not dv.is_collected("2026-09-04", db_path=db)
    assert dv.get_collection_log(db_path=db)[0]["status"] == "unavailable"


def test_retry_recovers_and_stores_full_snapshot(db, monkeypatch):
    calls = wire(monkeypatch, db, [stocks(574, 95), stocks()])
    result = collector.collect_daily("2026-09-04", db_path=db)
    assert result["status"] == "ok" and len(result["rankings"]) == 50
    assert len(calls) == 2
    assert len(dv.get_rankings("2026-09-04", 200, db_path=db)) == 200
    assert result["history_warning"] and result["new_faces"] == []


def test_bad_cache_never_skips_as_success(db, monkeypatch):
    seed(db, "2026-09-04", n=95, scanned=574)
    before_log = dv.get_collection_log(db_path=db)
    calls = wire(monkeypatch, db, [stocks()])
    result = collector.collect_daily("2026-09-04", db_path=db)
    assert result["status"] == "unavailable" and result["rankings"] == []
    assert not calls  # cached historical data requires explicit repair
    assert len(dv.get_rankings("2026-09-04", 200, db_path=db)) == 95
    assert dv.get_collection_log(db_path=db) == before_log


def test_failed_force_preserves_good_cache_and_log(db, monkeypatch):
    seed(db, "2026-09-04")
    before = dv.get_collection_log(db_path=db)
    wire(monkeypatch, db, [stocks(574, 95)])
    result = collector.collect_daily("2026-09-04", force=True, db_path=db)
    assert result["status"] == "unavailable"
    assert dv.get_collection_log(db_path=db) == before
    assert len(dv.get_rankings("2026-09-04", 200, db_path=db)) == 200


def test_baseline_rejects_even_when_200_rows_available(db, monkeypatch):
    for d in range(1, 6):
        seed(db, f"2026-09-0{d}", scanned=1400)
    wire(monkeypatch, db, [stocks(1000)])
    assert collector.collect_daily("2026-09-07", db_path=db)["status"] == "unavailable"


def test_missing_leaders_rejected_even_with_normal_count(db, monkeypatch):
    seed(db, "2026-09-03")
    sample = stocks()
    for row in sample[:5]:
        row["volume"] = 0
    wire(monkeypatch, db, [sample])
    assert collector.collect_daily("2026-09-04", db_path=db)["status"] == "unavailable"


def test_transport_failure_gets_bounded_retry_and_no_secret_leak(db, monkeypatch):
    wire(monkeypatch, db, [stocks()])
    calls = []
    def fail(*a, **kw):
        calls.append(1)
        raise RuntimeError("https://provider/endpoint?apikey=DO_NOT_PRINT")
    monkeypatch.setattr(collector, "fetch_all_stocks", fail)
    result = collector.collect_daily("2026-09-04", db_path=db)
    assert len(calls) == 2 and result["status"] == "unavailable"
    assert "DO_NOT_PRINT" not in json.dumps(result)


@pytest.mark.parametrize("mutation", ["duplicate", "nan", "negative", "rank_gap", "unsorted"])
def test_snapshot_validation_rejects_corruption(mutation):
    rows = rankings()
    if mutation == "duplicate": rows[-1]["symbol"] = "S1"
    if mutation == "nan": rows[-1]["dollar_volume"] = float("nan")
    if mutation == "negative": rows[-1]["price"] = -1
    if mutation == "rank_gap": rows[-1]["rank"] = 201
    if mutation == "unsorted": rows[-1]["dollar_volume"] = 1e12
    assert dv.rankings_integrity_error(rows)


def test_bad_previous_day_disables_comparison_not_new(db):
    seed(db, "2026-09-01")
    seed(db, "2026-09-02", n=95, scanned=574)
    assert dv.get_previous_day_ranks("2026-09-03", db_path=db) is None
    rows = rankings(2)
    dv.annotate_rank_changes(rows, None)
    assert [r["rank_change_label"] for r in rows] == ["—", "—"]


def test_new_faces_requires_whole_30_day_window(db):
    for offset in range(31):
        day = (date(2026, 7, 1)+timedelta(days=offset)).isoformat()
        seed(db, day)
    rows = rankings()
    rows[0]["symbol"] = "NEWCOMER"
    evidence = dv.make_quality_evidence(rows, 1000, [r["symbol"] for r in rows],
                                        dv.latest_quality_reference("2026-07-31", db))
    dv.store_daily_rankings("2026-07-31", rows, db_path=db, evidence=evidence)
    # One bad day cannot be silently skipped in the lookback.
    seed(db, "2026-07-10", n=95)
    assert dv.detect_new_faces("2026-07-31", db_path=db) == []
    assert dv.get_history_warning("2026-07-31", db_path=db)
    seed(db, "2026-07-10")
    assert dv.get_history_warning("2026-07-31", db_path=db) == ""
    assert [r["symbol"] for r in dv.detect_new_faces("2026-07-31", db_path=db)] == ["NEWCOMER"]


def test_rejected_date_without_rows_breaks_history(db):
    seed(db, "2026-09-01")
    dv.log_collection("2026-09-02", dict(status="unavailable", stored=0), db_path=db)
    assert dv.get_previous_day_ranks("2026-09-03", db_path=db) is None


def test_good_cache_does_not_fetch(db, monkeypatch):
    seed(db, "2026-09-04")
    calls = wire(monkeypatch, db, [stocks(574, 95)])
    result = collector.collect_daily("2026-09-04", db_path=db)
    assert result["status"] == "skipped" and len(result["rankings"]) == 50
    assert not calls


def test_missing_provenance_is_not_a_good_cache(db, monkeypatch):
    dv.store_daily_rankings("2026-09-04", rankings(), db_path=db)
    wire(monkeypatch, db, [stocks()])
    assert collector.collect_daily("2026-09-04", db_path=db)["status"] == "unavailable"


def test_no_dividend_or_average_price_fallback():
    assert collector.compute_rankings([
        dict(symbol="BAD", lastAnnualDividend=5, priceAvg50=100, volume=1e8),
        dict(symbol="NAN", price=float("nan"), volume=1e8),
    ]) == []


@pytest.mark.parametrize("with_warning", [True, False])
def test_unavailable_is_visible_in_all_renderers(with_warning):
    from scripts import morning_report as mr
    bad = dict(status="unavailable", warning="数据不可用：完整性检查未通过",
               rankings=rankings(1), new_faces=rankings(1))
    if not with_warning:
        bad.pop("warning")
    market = dict(as_of="2026-09-04", pmarp={"hits": []}, volume_anomaly={"hits": []})
    text = mr.format_section_d(bad)
    html = mr.build_html_payload(market, bad, "2026-09-04")
    visual = mr.build_morning_visual_sections(market_signals=market, dv_result=bad)
    for output in (text, json.dumps(html, ensure_ascii=False), json.dumps(visual, ensure_ascii=False)):
        assert "数据不可用" in output
        assert "S1" not in output


def test_complete_cache_with_low_scan_count_is_rejected(db, monkeypatch):
    seed(db, "2026-09-04", scanned=574)
    wire(monkeypatch, db, [stocks()])
    assert collector.collect_daily("2026-09-04", db_path=db)["status"] == "unavailable"


def test_baseline_ignores_future_and_incomplete_samples(db):
    seed(db, "2026-09-01", scanned=1000)
    seed(db, "2026-09-02", n=95, scanned=10000)
    seed(db, "2026-09-10", scanned=10000)
    assert dv.collection_minimum("2026-09-04", db) == 800


def test_leader_guard_survives_failed_previous_day(db, monkeypatch):
    seed(db, "2026-09-01")
    seed(db, "2026-09-02", n=95, scanned=574)
    wire(monkeypatch, db, [stocks()[5:]])
    result = collector.collect_daily("2026-09-03", db_path=db)
    assert result["status"] == "unavailable"
    assert not dv.is_collected("2026-09-03", db_path=db)
    assert dv.latest_quality_reference("2026-09-03", db)["date"] == "2026-09-01"
    assert dv.get_previous_day_ranks("2026-09-03", db_path=db) is None


def test_old_cache_without_full_evidence_cannot_bypass_leader_guard(db, monkeypatch):
    seed(db, "2026-09-01")
    rows = collector.compute_rankings(stocks()[5:])
    dv.store_daily_rankings("2026-09-02", rows, db_path=db,
                           stats=dict(total_scanned=995, stored=200, status="ok"))
    wire(monkeypatch, db, [stocks()[5:]])
    assert collector.collect_daily("2026-09-02", db_path=db)["status"] == "unavailable"
    assert collector.collect_daily("2026-09-02", force=True, db_path=db)["status"] == "unavailable"


def test_leaders_outside_top200_still_have_valid_cached_evidence(db, monkeypatch):
    seed(db, "2026-09-01")
    sample = stocks()
    for r in sample[:5]:
        r["volume"] = 1
    calls = wire(monkeypatch, db, [sample])
    result = collector.collect_daily("2026-09-02", db_path=db)
    assert result["status"] == "ok"
    assert "S1" not in {r["symbol"] for r in result["rankings"]}
    assert collector.collect_daily("2026-09-02", db_path=db)["status"] == "skipped"
    assert len(calls) == 1


@pytest.mark.parametrize("damage", ["rows", "symbols", "reference", "malformed"])
def test_cache_evidence_corruption_fails_closed(db, monkeypatch, damage):
    seed(db, "2026-09-01")
    wire(monkeypatch, db, [stocks()])
    assert collector.collect_daily("2026-09-02", db_path=db)["status"] == "ok"
    conn = dv.get_connection(db)
    proof = json.loads(conn.execute("SELECT evidence_json FROM quality_evidence WHERE date='2026-09-02'").fetchone()[0])
    if damage == "rows":
        conn.execute("UPDATE daily_rankings SET price=price+1 WHERE date='2026-09-02' AND rank=1")
    else:
        if damage == "symbols": proof["valid_symbols"] = proof["valid_symbols"][20:]
        if damage == "reference": proof["reference"] = None
        conn.execute("UPDATE quality_evidence SET evidence_json=? WHERE date='2026-09-02'",
                     ("{" if damage == "malformed" else json.dumps(proof),))
    conn.commit(); conn.close()
    assert collector.collect_daily("2026-09-02", db_path=db)["status"] == "unavailable"


def test_atomic_save_rolls_back_rows_log_and_evidence(db, monkeypatch):
    seed(db, "2026-09-01")
    seed(db, "2026-09-02")
    before_rows = dv.get_rankings("2026-09-02", 200, db)
    before_logs = dv.get_collection_log(db_path=db)
    def fail(*a, **kw):
        raise RuntimeError("injected log failure")
    monkeypatch.setattr(dv, "_write_collection_log", fail)
    sample = stocks()
    sample[0]["price"] = 20
    wire(monkeypatch, db, [sample])
    with pytest.raises(RuntimeError, match="injected"):
        collector.collect_daily("2026-09-02", force=True, db_path=db)
    assert dv.get_rankings("2026-09-02", 200, db) == before_rows
    assert dv.get_collection_log(db_path=db) == before_logs
    assert dv.snapshot_integrity_error("2026-09-02", db) == ""


def test_legacy_rewrite_invalidates_proof_without_creating_transition_anchor(db):
    seed(db, "2026-09-01")
    seed(db, "2026-09-02")
    dv.store_daily_rankings("2026-09-02", rankings(), db_path=db)
    assert dv.snapshot_integrity_error("2026-09-02", db)
    assert dv.latest_quality_reference("2026-09-03", db)["date"] == "2026-09-01"


def test_legacy_snapshot_only_bootstraps_anchor_not_published_cache(db, monkeypatch):
    dv.store_daily_rankings("2026-09-01", rankings(), db_path=db,
                           stats=dict(total_scanned=1000, stored=200, status="ok"))
    assert dv.snapshot_integrity_error("2026-09-01", db)
    ref = dv.latest_quality_reference("2026-09-02", db)
    assert ref["mode"] == "legacy_transition"
    wire(monkeypatch, db, [stocks()])
    assert collector.collect_daily("2026-09-02", db_path=db)["status"] == "ok"
    assert dv.latest_quality_reference("2026-09-03", db)["mode"] == "verified"
