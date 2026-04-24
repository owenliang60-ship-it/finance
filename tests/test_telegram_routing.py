"""Behavior tests for Telegram routing in Finance scripts."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import scripts.broad_market_scan as broad_market_scan
import scripts.morning_report as morning_report
import scripts.portfolio_intelligence as portfolio_intelligence
import scripts.rs_universe_scan as rs_universe_scan


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeSqliteConn:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.row_factory = None

    def execute(self, *_args, **_kwargs):
        return _FakeCursor(self.rows)

    def close(self):
        return None


class TestMorningReportRouting:
    @patch("scripts.morning_report.send_message")
    def test_send_group_report_splits_to_group(self, mock_send):
        msg = "A" * 2200 + "*D. Dollar Volume*" + "B" * 2200

        morning_report._send_group_report(msg)

        assert mock_send.call_count == 2
        assert all(call.kwargs["channel"] == "group" for call in mock_send.call_args_list)

    @patch("scripts.morning_report.send_message")
    @patch("scripts.morning_report.format_morning_report", return_value="group report")
    @patch("scripts.morning_report.run_dollar_volume", return_value={"rankings": [], "new_faces": []})
    @patch("scripts.morning_report.run_momentum_scan", return_value={"dv_acceleration": pd.DataFrame(columns=["signal"]), "rvol_sustained": []})
    @patch("scripts.morning_report.get_indicator_summary", return_value={"top_pmarp": [], "low_pmarp": [], "pmarp_crossovers": {}, "total": 1, "with_signals": 0, "errors": 0})
    @patch("scripts.morning_report.run_all_indicators", return_value={})
    @patch("scripts.morning_report.get_symbols", return_value=["AAPL"])
    def test_main_routes_normal_report_to_group(self, _symbols, _indicators, _summary, _momentum, _dv, _format, mock_send, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "argv", ["morning_report.py", "--no-social"])
        monkeypatch.setattr(morning_report, "SCANS_DIR", tmp_path)

        morning_report.main()

        mock_send.assert_called_once_with("group report", channel="group")

    @patch("scripts.morning_report.send_message")
    @patch("scripts.morning_report.run_all_indicators", side_effect=RuntimeError("boom"))
    @patch("scripts.morning_report.get_symbols", return_value=["AAPL"])
    def test_main_routes_errors_to_group(self, _symbols, _indicators, mock_send, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["morning_report.py", "--no-social"])

        morning_report.main()

        mock_send.assert_called_once()
        assert mock_send.call_args.kwargs["channel"] == "group"
        assert "晨报异常" in mock_send.call_args.args[0]


class TestBroadMarketRouting:
    @patch("scripts.broad_market_scan.send_message")
    def test_send_group_report_splits_to_group(self, mock_send):
        report = "A" * 2200 + "🟡 今日新触发" + "B" * 2200

        broad_market_scan._send_group_report(report)

        assert mock_send.call_count == 2
        assert all(call.kwargs["channel"] == "group" for call in mock_send.call_args_list)

    @patch("scripts.broad_market_scan.send_message")
    @patch("scripts.broad_market_scan.format_broad_scan_report", return_value="broad report")
    @patch("scripts.broad_market_scan.apply_tracker_stats", side_effect=lambda candidates, _tracker: candidates)
    @patch("scripts.broad_market_scan.update_streak_tracker", return_value={"_meta": {"last_scan_date": "2026-04-08"}})
    @patch("scripts.broad_market_scan._write_json")
    @patch("scripts.broad_market_scan._read_json", return_value={})
    @patch("scripts.broad_market_scan.scan_candidates", return_value={"scan_date": "2026-04-08", "all_triggered": [], "outside_candidates": [], "symbols_scanned": 10, "triggered_total": 0, "outside_total": 0})
    @patch("scripts.broad_market_scan.download_price_frames", return_value={f"S{i}": pd.DataFrame() for i in range(10)})
    @patch("scripts.broad_market_scan.load_pool_symbols", return_value=set())
    @patch("scripts.broad_market_scan.fetch_universe_metadata", return_value={"stocks": {f"S{i}": {"marketCap": i} for i in range(10)}})
    @patch("src.data.market_store.get_store")
    def test_main_routes_normal_report_to_group(self, mock_get_store, _universe, _pool, _frames, _scan, _read, _write, _tracker, _stats, _format, mock_send, monkeypatch):
        mock_get_store.return_value = MagicMock()
        monkeypatch.setattr(sys, "argv", ["broad_market_scan.py"])

        broad_market_scan.main()

        mock_send.assert_called_once_with("broad report", channel="group")

    @patch("scripts.broad_market_scan.send_message")
    @patch("scripts.broad_market_scan.fetch_universe_metadata", return_value={"stocks": {}})
    def test_main_routes_errors_to_group(self, _universe, mock_send, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["broad_market_scan.py"])

        with pytest.raises(RuntimeError):
            broad_market_scan.main()

        mock_send.assert_called_once()
        assert mock_send.call_args.kwargs["channel"] == "group"
        assert "Broad Market RVOL Scan 异常" in mock_send.call_args.args[0]


class TestRsUniverseRouting:
    @patch("scripts.rs_universe_scan.send_message")
    def test_send_group_report_splits_to_group(self, mock_send):
        msg = "A" * 2200 + "*Method C" + "B" * 2200

        rs_universe_scan._send_group_report(msg)

        assert mock_send.call_count == 2
        assert all(call.kwargs["channel"] == "group" for call in mock_send.call_args_list)

    @patch("scripts.rs_universe_scan.send_message")
    @patch("scripts.rs_universe_scan.format_rs_report", return_value="rs report")
    @patch("scripts.rs_universe_scan.format_console_report", return_value="console report")
    @patch("scripts.rs_universe_scan.compute_rs_rating_c", return_value=pd.DataFrame([{"symbol": "MSFT", "rs_rank": 95, "clenow_63d": 1.0, "clenow_21d": 1.0, "clenow_10d": 1.0}]))
    @patch("scripts.rs_universe_scan.compute_rs_rating_b", return_value=pd.DataFrame([{"symbol": "AAPL", "rs_rank": 90, "z_3m": 1.0, "z_1m": 1.0, "z_1w": 1.0}]))
    @patch("scripts.rs_universe_scan.load_price_data", return_value={f"S{i}": pd.DataFrame({"date": ["2026-04-08"], "close": [1.0]}) for i in range(10)})
    @patch("scripts.rs_universe_scan.fetch_universe", return_value=[f"S{i}" for i in range(10)])
    @patch("scripts.rs_universe_scan.FMPClient")
    def test_main_routes_normal_report_to_group(self, _client, _fetch, _load, _rs_b, _rs_c, _console, _report, mock_send, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "argv", ["rs_universe_scan.py"])
        monkeypatch.setattr(rs_universe_scan, "SCANS_DIR", tmp_path)

        rs_universe_scan.main()

        mock_send.assert_called_once_with("rs report", channel="group")

    @patch("scripts.rs_universe_scan.send_message")
    @patch("scripts.rs_universe_scan.fetch_universe", side_effect=RuntimeError("boom"))
    @patch("scripts.rs_universe_scan.FMPClient")
    def test_main_routes_errors_to_group(self, _client, _fetch, mock_send, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["rs_universe_scan.py"])

        rs_universe_scan.main()

        mock_send.assert_called_once()
        assert mock_send.call_args.kwargs["channel"] == "group"
        assert "RS Universe Scan 异常" in mock_send.call_args.args[0]


class TestPortfolioRouting:
    @patch("scripts.portfolio_intelligence.send_message")
    def test_send_private_report_routes_private_channel(self, mock_send):
        portfolio_intelligence._send_private_report("private report", dry_run=False)

        mock_send.assert_called_once_with("private report", channel="private")

    @patch("scripts.portfolio_intelligence.send_message")
    def test_send_private_report_skips_when_dry_run(self, mock_send):
        portfolio_intelligence._send_private_report("private report", dry_run=True)

        mock_send.assert_not_called()

    @patch("scripts.portfolio_intelligence._send_private_report", side_effect=lambda message, dry_run=False: message)
    @patch("portfolio.holdings.manager.PortfolioManager")
    @patch("scripts.portfolio_intelligence.get_store")
    def test_run_intelligence_empty_holdings_uses_private_delivery(self, mock_get_store, mock_manager_cls, mock_deliver):
        mock_store = MagicMock()
        mock_store.get_open_option_positions.return_value = []
        mock_store.get_cash_balance.return_value = 0
        mock_get_store.return_value = mock_store

        mock_manager = MagicMock()
        mock_manager.load_holdings.return_value = []
        mock_manager_cls.return_value = mock_manager

        result = portfolio_intelligence.run_intelligence(dry_run=False)

        assert result == "📊 Portfolio Intelligence: 无持仓"
        mock_deliver.assert_called_once_with("📊 Portfolio Intelligence: 无持仓", dry_run=False)

    @patch("scripts.portfolio_intelligence._send_private_report", side_effect=lambda message, dry_run=False: message)
    @patch("portfolio.holdings.live_quote_provider.fetch_stock_live_quotes")
    @patch("sqlite3.connect", return_value=_FakeSqliteConn())
    @patch("portfolio.holdings.manager.PortfolioManager")
    @patch("scripts.portfolio_intelligence.get_store")
    def test_run_intelligence_normal_uses_private_delivery(self, mock_get_store, mock_manager_cls, _sqlite, mock_fetch_stock, mock_deliver):
        mock_store = MagicMock()
        mock_store.get_open_option_positions.return_value = []
        mock_store.get_cash_balance.return_value = 100
        mock_store.get_kill_conditions.return_value = []
        mock_store.get_oprms_history.return_value = []
        # get_positions_as_of() walks _get_conn().execute().fetchone(); make it
        # return (None,) so the helper short-circuits to None without comparing
        # MagicMocks under max().
        mock_store._get_conn.return_value.execute.return_value.fetchone.return_value = (None,)
        mock_get_store.return_value = mock_store

        position = SimpleNamespace(
            symbol="AAPL",
            cost_basis=100.0,
            dna_rating="A",
            current_weight=0.5,
            sector="Technology",
            unrealized_pnl=25.0,
        )

        mock_manager = MagicMock()
        mock_manager.load_holdings.return_value = [position]
        mock_manager.get_total_nav.return_value = 1000.0
        mock_manager.refresh_prices.return_value = [position]
        mock_manager_cls.return_value = mock_manager
        mock_fetch_stock.return_value = SimpleNamespace(
            prices={"AAPL": 125.0},
            failed=[],
            quote_meta={"AAPL": {"price_field": "mid"}},
            request_count=1,
            credit_header_available=False,
            credits_used=None,
            credits_remaining=None,
        )

        result = portfolio_intelligence.run_intelligence(dry_run=False, allow_local=True)

        assert "组合概览" in result
        assert "NAV 快照 ET" in result
        assert "credit header unavailable" in result
        mock_deliver.assert_called_once()
        assert mock_deliver.call_args.kwargs["dry_run"] is False
