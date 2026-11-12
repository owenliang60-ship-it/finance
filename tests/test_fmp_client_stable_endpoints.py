"""FMP /stable endpoint names for earnings calendar, insider trades and news."""
from unittest.mock import MagicMock


def test_fmp_client_uses_stable_endpoint_names():
    """The legacy names return 404 on the /stable base (verified live 2026-11-12)."""
    from src.data.fmp_client import FMPClient
    client = FMPClient.__new__(FMPClient)
    client._request = MagicMock(return_value=[])
    client.get_earnings_calendar("2026-11-12", "2026-11-26")
    client.get_insider_trades("NVDA", limit=2)
    client.get_stock_news("NVDA", limit=2)
    calls = [(c.args[0], c.args[1]) for c in client._request.call_args_list]
    assert calls[0][0] == "earnings-calendar"
    assert calls[1] == ("insider-trading/search", {"symbol": "NVDA", "limit": 2})
    assert calls[2] == ("news/stock", {"limit": 2, "symbols": "NVDA"})
