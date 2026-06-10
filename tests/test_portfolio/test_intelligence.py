"""Tests for Portfolio Intelligence engine."""
import pytest
import pandas as pd
import numpy as np
from datetime import datetime


def _make_price_df(n=200, base=100.0):
    """Generate synthetic daily price DataFrame."""
    np.random.seed(42)
    close = base * np.cumprod(1 + np.random.normal(0.001, 0.02, n))
    volume = np.random.randint(1_000_000, 10_000_000, n).astype(float)
    return pd.DataFrame({
        "close": close, "volume": volume,
        "open": close * 0.99, "high": close * 1.01, "low": close * 0.98,
    })


class TestEMA120Signal:
    def test_below_ema120_triggers(self):
        from scripts.portfolio_intelligence import check_ema120
        df = _make_price_df(200)
        # Force last price below EMA120
        df.iloc[-1, df.columns.get_loc("close")] = df["close"].ewm(span=120).mean().iloc[-1] * 0.95
        result = check_ema120(df)
        assert result is not None
        assert result["signal"] == "below_ema120"

    def test_above_ema120_no_signal(self):
        from scripts.portfolio_intelligence import check_ema120
        df = _make_price_df(200)
        df.iloc[-1, df.columns.get_loc("close")] = df["close"].ewm(span=120).mean().iloc[-1] * 1.10
        result = check_ema120(df)
        assert result is None

    def test_insufficient_data(self):
        from scripts.portfolio_intelligence import check_ema120
        df = _make_price_df(50)
        assert check_ema120(df) is None


class TestCostAlerts:
    def test_loss_exceeds_threshold(self):
        from scripts.portfolio_intelligence import check_cost_alert
        # DNA=A → threshold -20%
        result = check_cost_alert(
            symbol="MSFT", avg_cost=200.0, current_price=155.0, dna="A"
        )
        assert result is not None
        assert "浮亏" in result["message"]

    def test_no_alert_within_threshold(self):
        from scripts.portfolio_intelligence import check_cost_alert
        result = check_cost_alert(
            symbol="MSFT", avg_cost=200.0, current_price=190.0, dna="A"
        )
        assert result is None

    def test_s_tier_wider_threshold(self):
        from scripts.portfolio_intelligence import check_cost_alert
        # DNA=S → threshold -30%, so -25% should NOT trigger
        result = check_cost_alert(
            symbol="NVDA", avg_cost=100.0, current_price=75.0, dna="S"
        )
        assert result is None

    def test_zero_cost_no_crash(self):
        from scripts.portfolio_intelligence import check_cost_alert
        result = check_cost_alert("TEST", avg_cost=0.0, current_price=10.0, dna="B")
        assert result is None


class TestSectorConcentration:
    def test_warns_above_40pct(self):
        from scripts.portfolio_intelligence import calc_sector_concentration
        positions = [
            {"sector": "Technology", "weight": 0.35},
            {"sector": "Technology", "weight": 0.25},
            {"sector": "Healthcare", "weight": 0.20},
            {"sector": "Financial", "weight": 0.20},
        ]
        result = calc_sector_concentration(positions)
        assert result["Technology"] == pytest.approx(0.60, rel=1e-2)
        assert len(result["_warnings"]) == 1
        assert "Technology" in result["_warnings"][0]

    def test_no_warning_when_balanced(self):
        from scripts.portfolio_intelligence import calc_sector_concentration
        positions = [
            {"sector": "A", "weight": 0.25},
            {"sector": "B", "weight": 0.25},
            {"sector": "C", "weight": 0.25},
            {"sector": "D", "weight": 0.25},
        ]
        result = calc_sector_concentration(positions)
        assert result["_warnings"] == []


class TestTimingChange:
    def test_detect_change(self):
        from scripts.portfolio_intelligence import detect_timing_change
        ratings = [
            {"dna": "S", "timing": "A", "created_at": "2026-04-01"},
            {"dna": "S", "timing": "B", "created_at": "2026-03-15"},
        ]
        result = detect_timing_change(ratings)
        assert result is not None
        assert result["old_timing"] == "B"
        assert result["new_timing"] == "A"

    def test_no_change(self):
        from scripts.portfolio_intelligence import detect_timing_change
        ratings = [
            {"dna": "S", "timing": "A", "created_at": "2026-04-01"},
            {"dna": "S", "timing": "A", "created_at": "2026-03-15"},
        ]
        assert detect_timing_change(ratings) is None

    def test_single_rating(self):
        from scripts.portfolio_intelligence import detect_timing_change
        assert detect_timing_change([{"dna": "S", "timing": "A"}]) is None


class TestFormatReport:
    def test_three_blocks(self):
        from scripts.portfolio_intelligence import format_report
        signals = ["NVDA | PMARP 99.1% ⬆️ 超涨预警"]
        summary = {
            "total_nav": 2_000_000, "invested_pct": 0.62, "cash_pct": 0.38,
            "qqq_beta": 1.15, "total_pnl": 50000, "total_pnl_pct": 0.04,
            "sectors": {"Technology": 0.55}, "sector_warnings": ["Technology 55%"],
            "total_positions": 14, "dna_distribution": "S×3 A×5 B×6",
        }
        kc = {"NVDA": {"dna": "S", "conditions": ["估值严重脱离基本面"]}}
        report = format_report(signals, summary, kc)
        assert "行动信号" in report
        assert "组合概览" in report
        assert "退出条件审视" in report
        assert "$2,000,000" in report

    def test_total_capital_position_details_and_concentration(self):
        from scripts.portfolio_intelligence import format_report
        summary = {
            "total_nav": 2_000_000,
            "total_capital": 5_000_000,
            "tracked_nav_total_pct": 0.40,
            "invested_value": 1_250_000,
            "invested_pct": 0.625,
            "invested_total_pct": 0.25,
            "cash": 750_000,
            "cash_pct": 0.375,
            "qqq_beta": None,
            "total_pnl": 0,
            "total_pnl_pct": 0,
            "sectors": {},
            "sector_warnings": [],
            "concentration": {
                "top_position": {
                    "symbol": "NVDA", "market_value": 300_000,
                    "tracked_pct": 0.15, "total_pct": 0.06,
                },
                "top5": {"symbols": ["NVDA", "MSFT"], "tracked_pct": 0.25, "total_pct": 0.10},
                "largest_sector": {
                    "sector": "Technology", "value": 800_000,
                    "tracked_pct": 0.40, "total_pct": 0.16,
                },
                "flags": ["Technology ≥40% NAV"],
            },
            "position_details": [
                {
                    "symbol": "NVDA", "company_name": "NVIDIA Corporation",
                    "sector": "Technology", "industry": "Semiconductors",
                    "market_value": 300_000, "tracked_pct": 0.15,
                    "total_pct": 0.06,
                },
            ],
            "option_details": [
                {
                    "contract": "QQQ 2026-06-18 580P x10",
                    "strategy_tag": "tail_hedge",
                    "market_value": 23_000,
                    "tracked_pct": 0.0115,
                    "total_pct": 0.0046,
                },
            ],
            "total_positions": 1,
            "dna_distribution": "S×1",
        }

        report = format_report([], summary, {})

        assert "追踪NAV" in report
        assert "of $5.00M" in report  # dynamic capital via _format_usd_compact
        assert "持仓明细" in report
        assert "NVDA NVIDIA" in report
        assert "Semiconductors" in report
        assert "QQQ 2026-06-18 580P x10" in report
        assert "最大单票" in report
        assert "Top5" in report
        assert "Technology ≥40% NAV" in report

    def test_snapshot_line_appears_first(self):
        from scripts.portfolio_intelligence import format_report
        summary = {
            "total_nav": 1_000_000, "invested_pct": 0.50, "cash_pct": 0.50,
            "qqq_beta": None, "total_pnl": 0, "total_pnl_pct": 0,
            "sectors": {}, "sector_warnings": [],
            "total_positions": 5, "dna_distribution": "A×5",
        }
        report = format_report(
            [],
            summary,
            {},
            snapshot_line="📍 NAV 快照 ET 2026-04-22 10:05 | live 1/1 | signals as of 2026-04-21",
        )
        assert report.splitlines()[0].startswith("📍 NAV 快照 ET")

    def test_no_signals_no_block1(self):
        from scripts.portfolio_intelligence import format_report
        summary = {
            "total_nav": 1_000_000, "invested_pct": 0.50, "cash_pct": 0.50,
            "qqq_beta": None, "total_pnl": 0, "total_pnl_pct": 0,
            "sectors": {}, "sector_warnings": [],
            "total_positions": 5, "dna_distribution": "A×5",
        }
        report = format_report([], summary, {})
        assert "行动信号" not in report
        assert "组合概览" in report

    def test_snapshot_line_includes_positions_as_of(self):
        from scripts.portfolio_intelligence import format_report
        summary = {
            "total_nav": 1_000_000, "invested_pct": 0.50, "cash_pct": 0.50,
            "qqq_beta": None, "total_pnl": 0, "total_pnl_pct": 0,
            "sectors": {}, "sector_warnings": [],
            "total_positions": 5, "dna_distribution": "A×5",
        }
        report = format_report(
            [],
            summary,
            {},
            snapshot_line=(
                "📍 NAV 快照 ET 2026-04-24 10:05 | positions as of 2026-04-23 "
                "| live 11/11 | signals as of 2026-04-24"
            ),
        )
        assert "positions as of 2026-04-23" in report.splitlines()[0]

    def test_credit_header_unavailable_does_not_claim_delay(self):
        from scripts.portfolio_intelligence import format_report
        summary = {
            "total_nav": 1_000_000, "invested_pct": 0.50, "cash_pct": 0.50,
            "qqq_beta": None, "total_pnl": 0, "total_pnl_pct": 0,
            "sectors": {}, "sector_warnings": [],
            "total_positions": 5, "dna_distribution": "A×5",
        }
        report = format_report(
            [],
            summary,
            {},
            snapshot_line="📍 NAV 快照 ET 2026-04-22 10:05 | credit header unavailable",
        )
        assert "credit header unavailable" in report
        assert "delay ~" not in report

    def test_require_cloud_env_rejects_non_cloud_without_override(self, monkeypatch):
        from scripts.portfolio_intelligence import require_cloud_env

        monkeypatch.delenv("FINANCE_ENV", raising=False)

        with pytest.raises(RuntimeError, match="FINANCE_ENV=cloud"):
            require_cloud_env()

    def test_require_cloud_env_allows_explicit_override(self, monkeypatch, caplog):
        from scripts.portfolio_intelligence import require_cloud_env

        monkeypatch.setenv("FINANCE_ENV", "local")

        require_cloud_env(allow_local=True)

        assert "proceeding because local override was requested" in caplog.text


class TestPortfolioVisualReport:
    def _summary(self):
        return {
            "total_nav": 2_000_000,
            "total_capital": 5_000_000,
            "tracked_nav_total_pct": 0.40,
            "invested_value": 1_250_000,
            "invested_pct": 0.625,
            "invested_total_pct": 0.25,
            "cash": 750_000,
            "cash_pct": 0.375,
            "cash_total_pct": 0.15,
            "qqq_beta": 1.23,
            "total_pnl": 50_000,
            "total_pnl_pct": 0.04,
            "sectors": {},
            "sector_warnings": [],
            "concentration": {
                "top_position": {
                    "symbol": "NVDA",
                    "market_value": 300_000,
                    "tracked_pct": 0.15,
                    "total_pct": 0.06,
                },
                "top5": {"symbols": ["NVDA", "MSFT"], "tracked_pct": 0.25, "total_pct": 0.10},
                "largest_sector": {
                    "sector": "Technology",
                    "value": 800_000,
                    "tracked_pct": 0.40,
                    "total_pct": 0.16,
                },
                "flags": ["Technology ≥40% NAV"],
            },
            "position_details": [
                {
                    "symbol": "NVDA",
                    "company_name": "NVIDIA Corporation",
                    "sector": "Technology",
                    "industry": "Semiconductors",
                    "market_value": 300_000,
                    "tracked_pct": 0.15,
                    "total_pct": 0.06,
                    "pnl": 45_000,
                },
                {
                    "symbol": "TSLA",
                    "company_name": "Tesla, Inc.",
                    "sector": "Consumer Cyclical",
                    "industry": "Auto Manufacturers",
                    "market_value": 220_000,
                    "tracked_pct": 0.11,
                    "total_pct": 0.044,
                    "pnl": -5_000,
                },
            ],
            "option_details": [
                {
                    "symbol": "QQQ",
                    "contract": "QQQ 2026-06-18 580P x10",
                    "strategy_tag": "tail_hedge",
                    "market_value": 23_000,
                    "tracked_pct": 0.0115,
                    "total_pct": 0.0046,
                },
            ],
            "total_positions": 2,
            "dna_distribution": "S×1 A×1",
        }

    def test_visual_sections_use_ticker_and_morning_concepts(self):
        from scripts.portfolio_intelligence import build_portfolio_visual_sections

        sections = build_portfolio_visual_sections(
            action_signals=["NVDA | PMARP 99.1%"],
            summary=self._summary(),
            kill_conditions={},
            snapshot_line="NAV 快照 ET 2026-04-28",
        )

        payload = repr(sections)
        assert "NVIDIA Corporation" not in payload
        assert "Tesla, Inc." not in payload
        assert "Technology" not in payload
        assert "NVDA" in payload
        assert "TSLA" in payload
        assert "AI算力/云" in payload
        assert "自动驾驶/机器人" in payload

    def test_render_portfolio_images(self, tmp_path):
        from scripts.portfolio_intelligence import render_portfolio_report_images

        paths = render_portfolio_report_images(
            action_signals=[],
            summary=self._summary(),
            kill_conditions={"NVDA": {"dna": "S", "conditions": ["估值严重脱离基本面"]}},
            snapshot_line="NAV 快照 ET 2026-04-28",
            output_dir=tmp_path,
        )

        assert [path.name for path in paths] == [
            "01_overview.png",
            "02_stock_positions.png",
            "03_option_positions.png",
            "04_signals_exits.png",
        ]
        assert all(path.exists() and path.stat().st_size > 0 for path in paths)

    def test_render_portfolio_pdf(self, tmp_path):
        from scripts.portfolio_intelligence import (
            render_portfolio_report_images,
            render_portfolio_report_pdf,
        )

        paths = render_portfolio_report_images(
            action_signals=[],
            summary=self._summary(),
            kill_conditions={"NVDA": {"dna": "S", "conditions": ["估值严重脱离基本面"]}},
            snapshot_line="NAV 快照 ET 2026-04-28",
            output_dir=tmp_path,
        )

        pdf_path = render_portfolio_report_pdf(paths)

        assert pdf_path is not None
        assert pdf_path.name == "portfolio_intelligence.pdf"
        assert pdf_path.exists()
        assert pdf_path.read_bytes().startswith(b"%PDF")


class TestPositionsAsOf:
    def _store(self, tmp_path):
        from terminal.company_store import CompanyStore
        s = CompanyStore(db_path=tmp_path / "pi_test.db")
        s.upsert_company("NVDA", company_name="NVIDIA")
        s.upsert_company("QQQ", company_name="Invesco QQQ Trust")
        return s

    def test_returns_none_when_empty(self, tmp_path):
        from scripts.portfolio_intelligence import get_positions_as_of
        store = self._store(tmp_path)
        result = get_positions_as_of(store)
        assert result == {"latest": None, "oldest_open_option": None}
        store.close()

    def test_returns_iso_date_after_writes(self, tmp_path):
        from scripts.portfolio_intelligence import get_positions_as_of
        from portfolio.holdings.manager import PortfolioManager
        store = self._store(tmp_path)
        store.set_cash(100000.0)
        mgr = PortfolioManager(store=store)
        mgr.execute_trade("NVDA", "BUY", shares=10, price=130.0, date="2026-04-23")
        result = get_positions_as_of(store)
        latest = result["latest"]
        assert isinstance(latest, str)
        # YYYY-MM-DD format
        assert len(latest) == 10
        assert latest[4] == "-" and latest[7] == "-"
        # No open option legs → oldest_open_option is None
        assert result["oldest_open_option"] is None
        store.close()

    def test_picks_max_across_sources(self, tmp_path, monkeypatch):
        from scripts.portfolio_intelligence import get_positions_as_of
        store = self._store(tmp_path)
        # Hand-craft three rows with different timestamps
        conn = store._get_conn()
        conn.execute(
            "INSERT INTO holdings (symbol, shares, avg_cost, open_date, status, last_updated) "
            "VALUES (?, ?, ?, ?, 'OPEN', ?)",
            ("NVDA", 10, 130.0, "2026-04-20", "2026-04-20T10:00:00"),
        )
        conn.execute(
            "INSERT INTO option_positions (symbol, expiration, strike, side, quantity, "
            "avg_premium, open_date, status, strategy_tag, notes, last_updated) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'OPEN', '', '', ?)",
            ("QQQ", "2026-09-18", 410.0, "PUT", -10, 4.78,
             "2026-04-22", "2026-04-22T15:30:00"),
        )
        conn.execute(
            "INSERT INTO portfolio_cash (action, amount, balance_after, notes, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("DEPOSIT", 1000.0, 1000.0, "seed", "2026-04-23T08:00:00"),
        )
        result = get_positions_as_of(store)
        assert result["latest"] == "2026-04-23"
        assert result["oldest_open_option"] == "2026-04-22"
        store.close()

    def test_stale_option_leg_surfaced_when_cash_newer(self, tmp_path):
        """Old OPEN option leg + new cash write → oldest_open_option exposes staleness."""
        from scripts.portfolio_intelligence import get_positions_as_of
        store = self._store(tmp_path)
        conn = store._get_conn()
        # Stale OPEN leg — 3 months old
        conn.execute(
            "INSERT INTO option_positions (symbol, expiration, strike, side, quantity, "
            "avg_premium, open_date, status, strategy_tag, notes, last_updated) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'OPEN', '', '', ?)",
            ("NVDA", "2026-05-16", 140.0, "PUT", -10, 3.20,
             "2026-01-24", "2026-01-24T09:30:00"),
        )
        # Fresh cash write — yesterday
        conn.execute(
            "INSERT INTO portfolio_cash (action, amount, balance_after, notes, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("DEPOSIT", 5000.0, 5000.0, "salary", "2026-04-23T08:00:00"),
        )
        result = get_positions_as_of(store)
        # latest reflects newest write (cash), oldest_open_option reflects stale leg
        assert result["latest"] == "2026-04-23"
        assert result["oldest_open_option"] == "2026-01-24"
        store.close()


class TestHKTickerMapping:
    def test_to_yfinance_format(self):
        from scripts.portfolio_intelligence import to_yfinance_ticker
        assert to_yfinance_ticker("07709") == "7709.HK"
        assert to_yfinance_ticker("01810") == "1810.HK"
        assert to_yfinance_ticker("09992") == "9992.HK"

    def test_4digit_preserves_leading_zero(self):
        from scripts.portfolio_intelligence import to_yfinance_ticker
        assert to_yfinance_ticker("0700") == "0700.HK"
        assert to_yfinance_ticker("0005") == "0005.HK"

    def test_us_ticker_unchanged(self):
        from scripts.portfolio_intelligence import to_yfinance_ticker
        assert to_yfinance_ticker("NVDA") is None
        assert to_yfinance_ticker("AAPL") is None

    def test_is_hk_ticker(self):
        from scripts.portfolio_intelligence import is_hk_ticker
        assert is_hk_ticker("07709") is True
        assert is_hk_ticker("01810") is True
        assert is_hk_ticker("NVDA") is False


class TestFetchHKPrices:
    def test_returns_dict_with_prices(self, monkeypatch):
        """Mock yfinance to verify fetch_hk_prices returns correct structure."""
        from scripts.portfolio_intelligence import fetch_hk_prices
        import scripts.portfolio_intelligence as mod

        fake_data = pd.DataFrame({
            "Close": [30.0, 31.0, 32.0],
            "Volume": [1e6, 2e6, 3e6],
            "Open": [29.5, 30.5, 31.5],
            "High": [30.5, 31.5, 32.5],
            "Low": [29.0, 30.0, 31.0],
        }, index=pd.date_range("2026-04-01", periods=3))

        class FakeTicker:
            def __init__(self, symbol):
                self.symbol = symbol
            @property
            def history(self_inner):
                return lambda period, **kw: fake_data

        # Patch yfinance.Ticker
        monkeypatch.setattr(mod, "_yf_download_hk", lambda sym, period="200d": fake_data)
        result = fetch_hk_prices(["07709"])
        assert "07709" in result
        assert result["07709"] == pytest.approx(32.0 / 7.8366, rel=1e-2)


class TestQQQBetaDateAlignment:
    """Fix P2: beta must align on dates, not positional index."""

    def test_different_length_series_align(self):
        from scripts.portfolio_intelligence import calc_qqq_beta
        dates_long = pd.bdate_range(end="2026-04-01", periods=200)
        dates_short = pd.bdate_range(end="2026-04-01", periods=100)
        np.random.seed(99)
        qqq_df = pd.DataFrame({"date": dates_long, "close": 500 * np.cumprod(1 + np.random.normal(0.001, 0.01, 200))})
        sym_df = pd.DataFrame({"date": dates_short, "close": 100 * np.cumprod(1 + np.random.normal(0.001, 0.02, 100))})
        beta = calc_qqq_beta(["TEST"], {"TEST": sym_df}, qqq_df, {"TEST": 1.0}, lookback=60)
        # Should NOT be None or 0 — the 100-bar series overlaps with QQQ's last 100 bars
        assert beta is not None
        assert beta != 0.0

    def test_no_overlap_returns_none(self):
        from scripts.portfolio_intelligence import calc_qqq_beta
        dates_a = pd.bdate_range(start="2025-01-01", periods=60)
        dates_b = pd.bdate_range(start="2026-01-01", periods=60)
        np.random.seed(42)
        qqq_df = pd.DataFrame({"date": dates_a, "close": 500 * np.cumprod(1 + np.random.normal(0, 0.01, 60))})
        sym_df = pd.DataFrame({"date": dates_b, "close": 100 * np.cumprod(1 + np.random.normal(0, 0.02, 60))})
        beta = calc_qqq_beta(["TEST"], {"TEST": sym_df}, qqq_df, {"TEST": 1.0})
        # No overlapping dates → beta should be 0 (no contribution)
        assert beta == 0.0

    def test_timestamp_vs_string_dates_align(self):
        """P2 fix: HK history has Timestamp dates, SQLite has string dates."""
        from scripts.portfolio_intelligence import calc_qqq_beta
        dates = pd.bdate_range(end="2026-04-01", periods=100)
        np.random.seed(77)
        # QQQ from SQLite: string dates
        qqq_df = pd.DataFrame({"date": dates.strftime("%Y-%m-%d"), "close": 500 * np.cumprod(1 + np.random.normal(0.001, 0.01, 100))})
        # HK from yfinance: Timestamp dates
        sym_df = pd.DataFrame({"date": dates, "close": 100 * np.cumprod(1 + np.random.normal(0.001, 0.02, 100))})
        beta = calc_qqq_beta(["HK"], {"HK": sym_df}, qqq_df, {"HK": 1.0}, lookback=60)
        assert beta is not None
        assert beta != 0.0


class TestPositionFromSheet:
    def test_field_mapping_and_sector_fallback(self, tmp_path):
        from scripts.portfolio_intelligence import _position_from_sheet
        from portfolio.holdings.manager import PortfolioManager
        from portfolio.holdings.sheet_book import SheetHolding
        from terminal.company_store import CompanyStore

        store = CompanyStore(db_path=tmp_path / "t.db")
        mgr = PortfolioManager(store=store)
        h = SheetHolding(
            symbol="BSKT", raw_ticker="BSKT", market="US", shares=50,
            cost_per_share=15.0, sheet_price=20.0, market_value=1000.0,
            category="Fundamental", is_leaps=False)
        pos = _position_from_sheet(mgr, h)
        assert pos.symbol == "BSKT"
        assert pos.cost_basis == 15.0
        assert pos.shares == 50
        assert pos.sector == "Fundamental"  # companies 表查不到 -> fallback Category
        assert pos.status == "OPEN"
        store.close()

    def test_known_symbol_keeps_db_sector(self, tmp_path):
        from scripts.portfolio_intelligence import _position_from_sheet
        from portfolio.holdings.manager import PortfolioManager
        from portfolio.holdings.sheet_book import SheetHolding
        from terminal.company_store import CompanyStore

        store = CompanyStore(db_path=tmp_path / "t.db")
        store.upsert_company("AAA", company_name="Aaa Inc", sector="Technology")
        mgr = PortfolioManager(store=store)
        h = SheetHolding(
            symbol="AAA", raw_ticker="AAA", market="US", shares=100,
            cost_per_share=8.0, sheet_price=10.0, market_value=1000.0,
            category="Sentiment", is_leaps=False)
        pos = _position_from_sheet(mgr, h)
        assert pos.sector == "Technology"  # DB sector 优先于 Category
        assert pos.company_name == "Aaa Inc"
        store.close()


class TestSheetFailurePath:
    def test_sheet_failure_sends_alert_and_raises(self, monkeypatch):
        import scripts.portfolio_intelligence as pi
        from portfolio.holdings.sheet_book import SheetBookError

        sent = []

        def fake_load():
            raise SheetBookError("sheet tab missing: Summary_OSV")

        monkeypatch.setattr(pi, "load_sheet_book", fake_load)
        monkeypatch.setattr(
            pi, "_send_private_report",
            lambda msg, dry_run=False: (sent.append(msg), msg)[1])
        with pytest.raises(SheetBookError):
            pi.run_intelligence(dry_run=True)
        assert len(sent) == 1
        assert "失败" in sent[0]
        assert "Summary_OSV" in sent[0]


class TestFormatReportDynamicCapital:
    def test_no_hardcoded_5m(self):
        from scripts.portfolio_intelligence import format_report
        summary = {
            "total_nav": 3400000, "total_capital": 6681327,
            "tracked_nav_total_pct": 0.509, "invested_value": 2650000,
            "invested_pct": 0.78, "invested_total_pct": 0.397,
            "cash": 737500, "cash_pct": 0.22, "cash_total_pct": 0.11,
            "total_pnl": 200000, "total_pnl_pct": 0.08,
            "total_positions": 14, "position_details": [],
            "option_details": [], "sector_warnings": [],
            "concentration": {}, "dna_distribution": "",
        }
        report = format_report([], summary, {})
        assert "of $5M" not in report
        assert "$6.68M" in report
