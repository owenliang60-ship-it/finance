from src.data.dollar_volume import rank_change_label


def test_rank_change_label():
    assert rank_change_label(3, 8) == "↑5"
    assert rank_change_label(10, 4) == "↓6"
    assert rank_change_label(5, 5) == "="
    assert rank_change_label(7, None) == "NEW"
