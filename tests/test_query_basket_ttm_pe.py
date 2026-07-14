"""Read-only basket valuation query contracts."""
import csv
import json
import sqlite3

import pytest

import scripts.query_basket_ttm_pe as query


SCHEMA = """
CREATE TABLE basket_ttm_valuation (
 basket_symbol TEXT, valuation_date TEXT, holding_date TEXT,
 composition_effective_date TEXT, composition_available_date TEXT,
 is_ex_post_composition INTEGER, weight_basis TEXT, data_quality_tier TEXT,
 is_observed_weight_date INTEGER, eligible_weight REAL, covered_weight REAL,
 rebalance_weighted_ttm_pe_gaap_proxy REAL,
 weighted_earnings_yield REAL, uncapped_mcap_basket_pe_gaap REAL,
 covered_market_cap REAL, ttm_net_income_usd REAL,
 member_count INTEGER, covered_count INTEGER, weight_coverage REAL,
 mcap_weight_coverage REAL, income_weight_coverage REAL,
 fx_weight_coverage REAL, members_json TEXT, warnings_json TEXT,
 mcap_sanity_json TEXT, methodology_version TEXT, created_at TEXT,
 PRIMARY KEY (basket_symbol, valuation_date)
);
"""


def _database(tmp_path):
    path = tmp_path / "market.db"
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    rows = [
        ("2026-01-02", 10.0, 12.0, 1, "[]"),
        ("2026-01-05", 20.0, 22.0, 0, "[\"notice\"]"),
        ("2026-01-06", 20.0, 24.0, 0, "[]"),
        ("2026-01-07", None, None, 0, "[]"),
    ]
    for valuation_date, primary, secondary, anchor, warnings in rows:
        conn.execute(
            "INSERT INTO basket_ttm_valuation VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ["SOXX", valuation_date, "2025-12-31", "2026-01-02",
             "2026-02-01", 1, "fixed_rebalance_weight_proxy",
             "historical_disclosure_fixed_proxy", anchor, 100.0, 95.0,
             primary, None if primary is None else 1.0 / primary, secondary,
             1000.0, 50.0, 30, 28, 0.95, 0.96, 0.97, 0.98,
             "[]", warnings, "[]", "1.0", "2026-07-14T00:00:00Z"])
    conn.commit()
    conn.execute(
        "UPDATE basket_ttm_valuation SET mcap_sanity_json = ? "
        "WHERE valuation_date = '2026-01-05'",
        [json.dumps([{
            "symbol": "KLAC", "date": "2026-01-05",
            "status": "invalid_mcap", "quarantined": True,
            "forced_refresh_attempted": True,
        }])])
    conn.commit()
    conn.close()
    return path


def test_percentile_counts_ties_at_or_below():
    assert query.percentile_at_or_below([10.0, 20.0, 20.0], 20.0) == 100.0
    assert query.percentile_at_or_below([10.0, 20.0, 20.0], 10.0) == pytest.approx(100 / 3)


def test_summary_excludes_null_pe_and_separates_anchors(tmp_path):
    db = _database(tmp_path)
    conn = query.connect_readonly(db)
    rows = query.load_rows(conn, "SOXX")
    result = query.build_result(rows, "SOXX")
    conn.close()
    assert result["primary"]["count"] == 3
    assert result["primary"]["current"] is None
    assert result["primary"]["current_date"] == "2026-01-07"
    assert result["primary"]["percentile"] is None
    assert result["primary"]["last_publishable"] == 20.0
    assert result["primary"]["last_publishable_date"] == "2026-01-06"
    assert len(result["observed_weight_anchors"]) == 1
    assert result["quality"]["gap_dates"] == ["2026-01-07"]
    assert result["quality"]["warnings"] == ["notice"]
    assert result["quality"]["quarantine_gaps"][0]["symbol"] == "KLAC"
    assert "restatements" in result["methodology_caveats"][1]


def test_connection_uses_read_only_uri(monkeypatch, tmp_path):
    seen = {}
    real_connect = sqlite3.connect

    def capture(database, *args, **kwargs):
        seen.update(database=database, kwargs=kwargs)
        return real_connect(database, *args, **kwargs)

    db = _database(tmp_path)
    monkeypatch.setattr(query.sqlite3, "connect", capture)
    conn = query.connect_readonly(db)
    conn.close()
    assert seen["database"].endswith("?mode=ro")
    assert seen["kwargs"]["uri"] is True


def test_empty_range_returns_exit_two(tmp_path, capsys):
    db = _database(tmp_path)
    rc = query.main([
        "--db", str(db), "--from-date", "2030-01-01",
    ])
    assert rc == 2
    assert "no basket valuation rows" in capsys.readouterr().err


def test_csv_and_markdown_are_deterministic(tmp_path):
    db = _database(tmp_path)
    csv_path = tmp_path / "out.csv"
    md_path = tmp_path / "out.md"
    args = ["--db", str(db), "--csv", str(csv_path),
            "--markdown", str(md_path)]
    assert query.main(args) == 0
    first_csv = csv_path.read_bytes()
    first_md = md_path.read_bytes()
    assert query.main(args) == 0
    assert csv_path.read_bytes() == first_csv
    assert md_path.read_bytes() == first_md
    with csv_path.open(newline="", encoding="utf-8") as handle:
        assert [row["valuation_date"] for row in csv.DictReader(handle)] == [
            "2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07"]
    assert "not official SOXX PE" in md_path.read_text(encoding="utf-8")
    assert "Quarantine gaps: 1" in md_path.read_text(encoding="utf-8")
    assert "Last publishable primary: 20.00 on 2026-01-06" in md_path.read_text(
        encoding="utf-8")


def test_invalid_json_fails_closed(tmp_path):
    db = _database(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE basket_ttm_valuation SET members_json = 'bad' "
                 "WHERE valuation_date = '2026-01-02'")
    conn.commit()
    conn.close()
    ro = query.connect_readonly(db)
    with pytest.raises(ValueError, match="invalid members_json"):
        query.build_result(query.load_rows(ro, "SOXX"), "SOXX")
    ro.close()
