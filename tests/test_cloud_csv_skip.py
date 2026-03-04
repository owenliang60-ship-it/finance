"""IS_CLOUD 条件下 CSV 副写跳过测试"""
import pandas as pd
import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture
def sample_df():
    return pd.DataFrame({
        "date": pd.to_datetime(["2026-03-03", "2026-03-02"]),
        "open": [100.0, 99.0],
        "high": [101.0, 100.0],
        "low": [99.0, 98.0],
        "close": [100.5, 99.5],
        "volume": [1000, 900],
    })


@patch("src.data.market_store.get_store")
def test_cloud_skips_csv_write(mock_get_store, sample_df, tmp_path):
    """IS_CLOUD=True 时不写 CSV"""
    mock_store = MagicMock()
    mock_get_store.return_value = mock_store

    with patch("src.data.price_fetcher.IS_CLOUD", True), \
         patch("src.data.price_fetcher.PRICE_DIR", tmp_path):
        from src.data.price_fetcher import save_price_cache
        save_price_cache("AAPL", sample_df)

    # market.db 仍然写入
    mock_store.upsert_daily_prices_df.assert_called_once()
    # CSV 不应存在
    assert not (tmp_path / "AAPL.csv").exists()


@patch("src.data.market_store.get_store")
def test_local_writes_csv(mock_get_store, sample_df, tmp_path):
    """IS_CLOUD=False 时正常写 CSV"""
    mock_store = MagicMock()
    mock_get_store.return_value = mock_store

    with patch("src.data.price_fetcher.IS_CLOUD", False), \
         patch("src.data.price_fetcher.PRICE_DIR", tmp_path):
        from src.data.price_fetcher import save_price_cache
        save_price_cache("AAPL", sample_df)

    # market.db 写入
    mock_store.upsert_daily_prices_df.assert_called_once()
    # CSV 应存在
    assert (tmp_path / "AAPL.csv").exists()
