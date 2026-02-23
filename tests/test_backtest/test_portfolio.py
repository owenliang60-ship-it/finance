"""
PortfolioState 单元测试
"""

import pytest
from backtest.portfolio import PortfolioState


class TestPortfolioState:
    """组合状态跟踪测试"""

    def test_initial_state(self):
        p = PortfolioState(1_000_000, cost_rate=0.0005)
        assert p.cash == 1_000_000
        assert p.initial_capital == 1_000_000
        assert len(p.holdings) == 0
        assert p.total_trades == 0

    def test_buy_basic(self):
        p = PortfolioState(100_000, cost_rate=0.0)
        shares = p.buy("AAPL", 10_000, 150.0, "2024-01-01")
        assert shares == pytest.approx(10_000 / 150.0)
        assert p.cash == pytest.approx(90_000)
        assert "AAPL" in p.holdings

    def test_buy_with_cost(self):
        p = PortfolioState(100_000, cost_rate=0.001)
        shares = p.buy("AAPL", 10_000, 100.0, "2024-01-01")
        # cost = 10_000 * 0.001 = 10
        # net_amount = 9_990
        # shares = 9_990 / 100 = 99.9
        assert shares == pytest.approx(99.9)
        assert p.cash == pytest.approx(90_000)
        assert p.total_costs == pytest.approx(10.0)

    def test_sell_basic(self):
        p = PortfolioState(100_000, cost_rate=0.0)
        p.buy("AAPL", 10_000, 100.0, "2024-01-01")
        net = p.sell("AAPL", 50.0, 120.0, "2024-02-01")
        assert net == pytest.approx(50 * 120.0)
        assert p.holdings["AAPL"] == pytest.approx(50.0)

    def test_sell_with_cost(self):
        p = PortfolioState(100_000, cost_rate=0.001)
        p.buy("AAPL", 10_000, 100.0, "2024-01-01")
        shares_held = p.holdings["AAPL"]
        net = p.sell("AAPL", shares_held, 120.0, "2024-02-01")
        gross = shares_held * 120.0
        expected_cost = gross * 0.001
        assert net == pytest.approx(gross - expected_cost)

    def test_sell_all(self):
        p = PortfolioState(100_000, cost_rate=0.0)
        p.buy("AAPL", 10_000, 100.0, "2024-01-01")
        p.sell_all("AAPL", 100.0, "2024-02-01")
        assert "AAPL" not in p.holdings

    def test_sell_more_than_held(self):
        p = PortfolioState(100_000, cost_rate=0.0)
        p.buy("AAPL", 5_000, 100.0, "2024-01-01")  # 50 shares
        net = p.sell("AAPL", 200.0, 100.0, "2024-02-01")
        # should only sell 50
        assert net == pytest.approx(5_000)
        assert "AAPL" not in p.holdings

    def test_sell_nonexistent(self):
        p = PortfolioState(100_000, cost_rate=0.0)
        net = p.sell("AAPL", 100, 150.0, "2024-01-01")
        assert net == 0.0

    def test_compute_nav(self):
        p = PortfolioState(100_000, cost_rate=0.0)
        p.buy("AAPL", 50_000, 100.0, "2024-01-01")
        p.buy("GOOG", 30_000, 200.0, "2024-01-01")
        nav = p.compute_nav({"AAPL": 110.0, "GOOG": 210.0})
        # cash = 20_000, AAPL: 500*110=55_000, GOOG: 150*210=31_500
        assert nav == pytest.approx(20_000 + 55_000 + 31_500)

    def test_take_snapshot(self):
        p = PortfolioState(100_000, cost_rate=0.0)
        p.buy("AAPL", 50_000, 100.0, "2024-01-01")
        snap = p.take_snapshot("2024-01-01", {"AAPL": 100.0})
        assert snap.date == "2024-01-01"
        assert snap.nav == pytest.approx(100_000)
        assert snap.n_holdings == 1

    def test_nav_series(self):
        p = PortfolioState(100_000, cost_rate=0.0)
        p.take_snapshot("2024-01-01", {})
        p.take_snapshot("2024-01-02", {})
        series = p.nav_series()
        assert len(series) == 2
        assert series[0] == ("2024-01-01", 100_000)

    def test_fractional_shares(self):
        p = PortfolioState(100_000, cost_rate=0.0)
        shares = p.buy("AAPL", 10_000, 333.33, "2024-01-01")
        assert shares == pytest.approx(10_000 / 333.33)

    def test_buy_zero_price(self):
        p = PortfolioState(100_000, cost_rate=0.0)
        shares = p.buy("AAPL", 10_000, 0, "2024-01-01")
        assert shares == 0.0

    def test_multiple_buys_same_symbol(self):
        p = PortfolioState(100_000, cost_rate=0.0)
        p.buy("AAPL", 5_000, 100.0, "2024-01-01")
        p.buy("AAPL", 5_000, 110.0, "2024-01-02")
        assert p.holdings["AAPL"] == pytest.approx(50 + 5_000 / 110.0)
