"""Read-only data loading, per-line ranks and truthful chart geometry."""

import json
import sqlite3
from pathlib import Path
from PIL import Image

from terminal.index_valuation_chart import (
    load_chart_data,
    render_chart,
    line_segments,
    summarize,
)
from terminal.index_pe_weekly import RESULT_HASH_FIELDS, weekly_result_hash


def test_missing_database_is_not_created_and_renders_unavailable(tmp_path):
    path = tmp_path / "missing.db"
    data = load_chart_data(path, "2026-09-11")
    assert not path.exists()
    assert [p["basket"] for p in data["panels"]] == ["SPY", "QQQ", "SOXX"]
    image = render_chart(data, tmp_path / "empty.png")
    with Image.open(image) as im:
        assert im.size == (1800, 1510) and im.mode == "RGB"
        assert "共识" in im.info["Description"]


def test_null_and_absent_weeks_are_not_connected():
    rows = [
        {"valuation_date": d, "v": v}
        for d, v in [
            ("2026-01-02", 10),
            ("2026-01-09", None),
            ("2026-01-16", 12),
            ("2026-02-06", 13),
        ]
    ]
    assert [len(points) for _, points in line_segments(rows, "v")] == [1, 1, 1]


def test_consensus_tail_is_dashed_with_boundary_connection():
    rows = [
        {"valuation_date": "2026-01-02", "v": 10, "quality_tier": "actual_only"},
        {
            "valuation_date": "2026-01-09",
            "v": 12,
            "quality_tier": "latest_consensus_tail",
        },
    ]
    lines = line_segments(rows, "v", style_key="quality_tier")
    assert [s for s, p in lines] == ["solid", "dashed"]
    assert len(lines[-1][1]) == 2


def test_ttm_ranking_is_independent_of_hindsight_tail_tier():
    rows = [
        {
            "valuation_date": "2026-01-02",
            "ttm_pe_gaap": 30,
            "hindsight_ntm_pe_gaap": 10,
            "quality_tier": "actual_only",
        },
        {
            "valuation_date": "2026-01-09",
            "ttm_pe_gaap": 20,
            "hindsight_ntm_pe_gaap": 99,
            "quality_tier": "latest_consensus_tail",
        },
    ]
    result = summarize(rows, [], "2026-02-01")
    assert result["ttm_percentile"] == 50
    assert result["hindsight_percentile"] is None
    assert result["stale"]


def _seed(path):
    conn = sqlite3.connect(path)
    string_fields = {
        "valuation_date",
        "composition_effective_date",
        "composition_available_date",
        "quality_tier",
        "methodology_version",
    }
    cols = ", ".join(
        f"{k} " + ("TEXT" if k in string_fields else "REAL") for k in RESULT_HASH_FIELDS
    )
    conn.execute(
        f"CREATE TABLE basket_weekly_pe_history (basket TEXT,run_id TEXT,{cols})"
    )
    conn.execute(
        "CREATE TABLE basket_pe_backfill_runs (basket TEXT,run_id TEXT,event_kind TEXT,payload_json TEXT)"
    )
    rows = []
    for day, value in [("2021-08-01", 20), ("2026-09-04", 30)]:
        row = {k: None for k in RESULT_HASH_FIELDS}
        row.update(
            valuation_date=day,
            ttm_pe_gaap=value,
            hindsight_ntm_pe_gaap=value - 5,
            quality_tier="actual_only",
            mcap_coverage_ttm=1,
            mcap_coverage_hindsight=1,
        )
        rows.append(row)
        conn.execute(
            f"INSERT INTO basket_weekly_pe_history VALUES ({','.join('?' for _ in range(len(RESULT_HASH_FIELDS) + 2))})",
            ["SPY", "run", *[row[k] for k in RESULT_HASH_FIELDS]],
        )
    conn.execute(
        "INSERT INTO basket_pe_backfill_runs VALUES (?,?,?,?)",
        ["SPY", "run", "run_started", "{}"],
    )
    conn.execute(
        "INSERT INTO basket_pe_backfill_runs VALUES (?,?,?,?)",
        [
            "SPY",
            "run",
            "run_completed",
            json.dumps({"weekly_rows": 2, "result_hash": weekly_result_hash(rows)}),
        ],
    )
    conn.commit()
    conn.close()


def test_reader_verifies_full_owned_hash_before_five_year_filter(tmp_path):
    path = tmp_path / "space ? #.db"
    _seed(path)
    before = path.read_bytes()
    data = load_chart_data(path, "2026-09-11")
    assert [r["valuation_date"] for r in data["panels"][0]["history"]] == ["2026-09-04"]
    assert path.read_bytes() == before
    assert not data["panels"][2]["history"]
    image = render_chart(data, tmp_path / "actual.png")
    again = render_chart(data, tmp_path / "again.png")
    assert image.read_bytes() == again.read_bytes()
    assert image.stat().st_size < 1_500_000


def test_tampered_history_is_not_plotted(tmp_path):
    path = tmp_path / "market.db"
    _seed(path)
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE basket_weekly_pe_history SET ttm_pe_gaap=999")
    data = load_chart_data(path, "2026-09-11")
    assert not data["panels"][0]["history"]
    assert "认证" in " ".join(data["panels"][0]["warnings"])


def test_dash_phase_survives_short_weekly_edges():
    from terminal.index_valuation_chart import dashed_strokes
    import math

    strokes = list(dashed_strokes([(0, 0), (5, 0), (10, 0), (15, 0), (20, 0)]))
    assert sum(math.dist(a, b) for a, b in strokes) == 14


def test_sox_pit_is_displayed_as_soxx_and_not_backcast(tmp_path):
    path = tmp_path / "market.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE fmp_forward_runs (snapshot_date TEXT,run_kind TEXT,status TEXT)"
        )
        conn.execute(
            "CREATE TABLE fmp_basket_valuation (basket TEXT,snapshot_date TEXT,fwd_pe_ntm REAL,ntm_net_income REAL,mcap_coverage_ntm REAL,weight_coverage REAL,members_json TEXT)"
        )
        for snap in ("2026-07-12", "2026-09-04", "2026-09-18"):
            conn.execute(
                "INSERT INTO fmp_forward_runs VALUES (?,?,?)",
                [snap, "weekly", "complete"],
            )
            conn.execute(
                "INSERT INTO fmp_basket_valuation VALUES (?,?,?,?,?,?,?)",
                [
                    "SOX",
                    snap,
                    20,
                    100,
                    1,
                    1,
                    json.dumps(
                        {
                            "status": "partial",
                            "earnings_basis": "analyst_consensus_not_verified_gaap",
                            "ntm_total_mcap": 2000,
                        }
                    ),
                ],
            )
    panel = load_chart_data(path, "2026-09-11")["panels"][2]
    assert panel["basket"] == "SOXX"
    assert [r["snapshot_date"] for r in panel["pit"]] == ["2026-09-04"]
