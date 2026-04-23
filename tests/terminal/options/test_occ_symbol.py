import pytest

from terminal.options.occ_symbol import build_occ_symbol


def test_standard_call():
    assert build_occ_symbol("AAPL", "2026-03-21", 200.0, "CALL") == "AAPL260321C00200000"


def test_standard_put():
    assert build_occ_symbol("AAPL", "2026-03-21", 200.0, "PUT") == "AAPL260321P00200000"


def test_lowercase_side():
    assert build_occ_symbol("AAPL", "2026-03-21", 200.0, "call") == "AAPL260321C00200000"


def test_fractional_strike():
    assert build_occ_symbol("NVDA", "2026-06-20", 152.5, "CALL") == "NVDA260620C00152500"


def test_small_strike():
    assert build_occ_symbol("SOFI", "2026-12-19", 7.5, "CALL") == "SOFI261219C00007500"


def test_long_ticker():
    assert build_occ_symbol("GOOGL", "2026-03-21", 180.0, "CALL") == "GOOGL260321C00180000"


def test_invalid_side():
    with pytest.raises(ValueError, match="side must be"):
        build_occ_symbol("AAPL", "2026-03-21", 200.0, "X")


def test_invalid_date():
    with pytest.raises(ValueError):
        build_occ_symbol("AAPL", "not-a-date", 200.0, "CALL")
