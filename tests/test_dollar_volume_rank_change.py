from src.data.dollar_volume import rank_change_label


def test_rank_change_label():
    assert rank_change_label(3, 8) == "↑5"
    assert rank_change_label(10, 4) == "↓6"
    assert rank_change_label(5, 5) == "="
    assert rank_change_label(7, None) == "NEW"


def test_get_previous_day_ranks(tmp_path):
    from src.data import dollar_volume as dv
    db = tmp_path / "dv.db"
    dv.init_db(db)   # P1-3 修：store_daily_rankings 首句即 DELETE，不建表；建表在 init_db:32
    dv.store_daily_rankings("2026-06-02",
        [{"symbol": "AAPL", "rank": 1, "dollar_volume": 9e9, "price": 1.0},
         {"symbol": "NVDA", "rank": 2, "dollar_volume": 8e9, "price": 1.0}], db_path=db)
    dv.store_daily_rankings("2026-06-03",
        [{"symbol": "NVDA", "rank": 1, "dollar_volume": 9e9, "price": 1.0}], db_path=db)
    assert dv.get_previous_day_ranks("2026-06-03", db_path=db) == {"AAPL": 1, "NVDA": 2}


def test_annotate_rank_changes_mutates():
    from src.data.dollar_volume import annotate_rank_changes
    rankings = [{"symbol": "NVDA", "rank": 1}, {"symbol": "TSLA", "rank": 2}]
    annotate_rank_changes(rankings, {"NVDA": 2})  # TSLA 昨日无
    assert rankings[0]["rank_change_label"] == "↑1"
    assert rankings[1]["rank_change_label"] == "NEW"
