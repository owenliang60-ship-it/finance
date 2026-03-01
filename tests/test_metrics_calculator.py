"""Tests for MetricsCalculator (src/data/metrics_calculator.py)."""
import pytest
from pathlib import Path

from src.data.market_store import MarketStore
from src.data.metrics_calculator import (
    compute_metrics,
    compute_all_metrics,
    _safe_div,
    _avg,
    _yoy_growth,
    _cagr,
    _delta,
    _find_yoy_match,
    _sum_last_n,
)


@pytest.fixture
def store(tmp_path):
    """Create a fresh MarketStore with sample data."""
    db_path = tmp_path / "test_metrics.db"
    s = MarketStore(db_path=db_path)
    yield s
    s.close()


def _seed_full_data(store):
    """Seed 8 quarters of income, BS, CF for AAPL for testing."""
    # 8 quarters of income (Q4 2024 back to Q1 2023)
    income_rows = [
        {"date": "2024-09-28", "fiscalYear": "2024", "period": "Q4",
         "revenue": 94930e6, "costOfRevenue": 52254e6, "grossProfit": 42676e6,
         "operatingIncome": 30561e6, "netIncome": 14736e6, "ebitda": 33585e6,
         "eps": 0.97, "epsDiluted": 0.97,
         "incomeBeforeTax": 18610e6, "incomeTaxExpense": 3874e6},
        {"date": "2024-06-29", "fiscalYear": "2024", "period": "Q3",
         "revenue": 85778e6, "costOfRevenue": 46099e6, "grossProfit": 39679e6,
         "operatingIncome": 26688e6, "netIncome": 21448e6, "ebitda": 29754e6,
         "eps": 1.40, "epsDiluted": 1.40,
         "incomeBeforeTax": 26750e6, "incomeTaxExpense": 5302e6},
        {"date": "2024-03-30", "fiscalYear": "2024", "period": "Q2",
         "revenue": 90753e6, "costOfRevenue": 49141e6, "grossProfit": 41612e6,
         "operatingIncome": 28105e6, "netIncome": 23636e6, "ebitda": 31500e6,
         "eps": 1.53, "epsDiluted": 1.53,
         "incomeBeforeTax": 29800e6, "incomeTaxExpense": 6164e6},
        {"date": "2023-12-30", "fiscalYear": "2024", "period": "Q1",
         "revenue": 119575e6, "costOfRevenue": 64720e6, "grossProfit": 54855e6,
         "operatingIncome": 40373e6, "netIncome": 33916e6, "ebitda": 44000e6,
         "eps": 2.18, "epsDiluted": 2.18,
         "incomeBeforeTax": 42970e6, "incomeTaxExpense": 9054e6},
        # Prior year for YoY
        {"date": "2023-09-30", "fiscalYear": "2023", "period": "Q4",
         "revenue": 89498e6, "costOfRevenue": 49141e6, "grossProfit": 40357e6,
         "operatingIncome": 26969e6, "netIncome": 22956e6, "ebitda": 30000e6,
         "eps": 1.46, "epsDiluted": 1.46,
         "incomeBeforeTax": 28750e6, "incomeTaxExpense": 5794e6},
        {"date": "2023-07-01", "fiscalYear": "2023", "period": "Q3",
         "revenue": 81797e6, "costOfRevenue": 45384e6, "grossProfit": 36413e6,
         "operatingIncome": 23206e6, "netIncome": 19881e6, "ebitda": 26000e6,
         "eps": 1.26, "epsDiluted": 1.26,
         "incomeBeforeTax": 24850e6, "incomeTaxExpense": 4969e6},
        {"date": "2023-04-01", "fiscalYear": "2023", "period": "Q2",
         "revenue": 94836e6, "costOfRevenue": 52860e6, "grossProfit": 41976e6,
         "operatingIncome": 28316e6, "netIncome": 24160e6, "ebitda": 31800e6,
         "eps": 1.52, "epsDiluted": 1.52,
         "incomeBeforeTax": 30300e6, "incomeTaxExpense": 6140e6},
        {"date": "2022-12-31", "fiscalYear": "2023", "period": "Q1",
         "revenue": 117154e6, "costOfRevenue": 66822e6, "grossProfit": 50332e6,
         "operatingIncome": 36016e6, "netIncome": 29998e6, "ebitda": 39500e6,
         "eps": 1.88, "epsDiluted": 1.88,
         "incomeBeforeTax": 37900e6, "incomeTaxExpense": 7902e6},
    ]
    store.upsert_income("AAPL", income_rows)

    # Balance sheet rows (matching dates)
    bs_rows = [
        {"date": "2024-09-28", "fiscalYear": "2024", "period": "Q4",
         "totalAssets": 364980e6, "totalCurrentAssets": 152987e6,
         "totalCurrentLiabilities": 176392e6, "totalStockholdersEquity": 56950e6,
         "totalDebt": 97300e6, "inventory": 6331e6, "netReceivables": 66243e6,
         "cashAndCashEquivalents": 29943e6},
        {"date": "2024-06-29", "fiscalYear": "2024", "period": "Q3",
         "totalAssets": 331500e6, "totalCurrentAssets": 135000e6,
         "totalCurrentLiabilities": 145000e6, "totalStockholdersEquity": 66708e6,
         "totalDebt": 101000e6, "inventory": 5500e6, "netReceivables": 50000e6,
         "cashAndCashEquivalents": 25000e6},
        {"date": "2024-03-30", "fiscalYear": "2024", "period": "Q2",
         "totalAssets": 337000e6, "totalCurrentAssets": 140000e6,
         "totalCurrentLiabilities": 150000e6, "totalStockholdersEquity": 74100e6,
         "totalDebt": 105000e6, "inventory": 6000e6, "netReceivables": 55000e6,
         "cashAndCashEquivalents": 32000e6},
        {"date": "2023-12-30", "fiscalYear": "2024", "period": "Q1",
         "totalAssets": 353500e6, "totalCurrentAssets": 143000e6,
         "totalCurrentLiabilities": 133000e6, "totalStockholdersEquity": 74236e6,
         "totalDebt": 108000e6, "inventory": 6200e6, "netReceivables": 60000e6,
         "cashAndCashEquivalents": 40718e6},
        {"date": "2023-09-30", "fiscalYear": "2023", "period": "Q4",
         "totalAssets": 352583e6, "totalCurrentAssets": 143566e6,
         "totalCurrentLiabilities": 145308e6, "totalStockholdersEquity": 62146e6,
         "totalDebt": 111000e6, "inventory": 6331e6, "netReceivables": 60985e6,
         "cashAndCashEquivalents": 29965e6},
    ]
    store.upsert_balance_sheet("AAPL", bs_rows)

    # Cash flow rows
    cf_rows = [
        {"date": "2024-09-28", "fiscalYear": "2024", "period": "Q4",
         "operatingCashFlow": 26800e6, "capitalExpenditure": -2900e6,
         "freeCashFlow": 23900e6, "netIncome": 14736e6},
        {"date": "2024-06-29", "fiscalYear": "2024", "period": "Q3",
         "operatingCashFlow": 28900e6, "capitalExpenditure": -2600e6,
         "freeCashFlow": 26300e6, "netIncome": 21448e6},
        {"date": "2024-03-30", "fiscalYear": "2024", "period": "Q2",
         "operatingCashFlow": 22700e6, "capitalExpenditure": -1900e6,
         "freeCashFlow": 20800e6, "netIncome": 23636e6},
        {"date": "2023-12-30", "fiscalYear": "2024", "period": "Q1",
         "operatingCashFlow": 39900e6, "capitalExpenditure": -3100e6,
         "freeCashFlow": 36800e6, "netIncome": 33916e6},
    ]
    store.upsert_cash_flow("AAPL", cf_rows)


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_safe_div_normal(self):
        assert _safe_div(10, 5) == 2.0

    def test_safe_div_zero(self):
        assert _safe_div(10, 0) is None

    def test_safe_div_none(self):
        assert _safe_div(None, 5) is None
        assert _safe_div(10, None) is None

    def test_avg_normal(self):
        assert _avg(10, 20) == 15.0

    def test_avg_none(self):
        assert _avg(None, 20) is None

    def test_yoy_growth_normal(self):
        assert _yoy_growth(110, 100) == pytest.approx(0.10)

    def test_yoy_growth_decline(self):
        assert _yoy_growth(90, 100) == pytest.approx(-0.10)

    def test_yoy_growth_zero_prior(self):
        assert _yoy_growth(100, 0) is None

    def test_yoy_growth_negative_prior(self):
        """Growth from negative to positive uses abs(prior)."""
        result = _yoy_growth(50, -100)
        assert result == pytest.approx(1.5)  # (50 - (-100)) / |-100| = 1.5

    def test_sum_last_n(self):
        rows = [{"v": 10}, {"v": 20}, {"v": 30}]
        assert _sum_last_n(rows, "v", 3) == 60.0

    def test_sum_last_n_insufficient(self):
        rows = [{"v": 10}]
        assert _sum_last_n(rows, "v", 4) is None

    def test_find_yoy_match(self):
        rows = [
            {"period": "Q4", "fiscal_year": "2024"},
            {"period": "Q3", "fiscal_year": "2024"},
            {"period": "Q4", "fiscal_year": "2023"},
        ]
        match = _find_yoy_match(rows, "Q4", "2024")
        assert match is not None
        assert match["fiscal_year"] == "2023"

    def test_find_yoy_match_no_match(self):
        rows = [{"period": "Q4", "fiscal_year": "2024"}]
        assert _find_yoy_match(rows, "Q4", "2024") is None

    def test_delta_normal(self):
        assert _delta(0.30, 0.25) == pytest.approx(0.05)

    def test_delta_negative(self):
        assert _delta(0.20, 0.25) == pytest.approx(-0.05)

    def test_delta_none(self):
        assert _delta(None, 0.25) is None
        assert _delta(0.30, None) is None

    def test_cagr_normal(self):
        # 100 → 110 → 121 → 133.1 over 3 periods = 10% per period
        assert _cagr(133.1, 100, 3) == pytest.approx(0.10, abs=0.001)

    def test_cagr_decline(self):
        # 80 from base 100 over 3 periods
        assert _cagr(80, 100, 3) == pytest.approx(-0.0717, abs=0.001)

    def test_cagr_negative_base(self):
        assert _cagr(100, -50, 3) is None

    def test_cagr_zero_base(self):
        assert _cagr(100, 0, 3) is None

    def test_cagr_none(self):
        assert _cagr(None, 100, 3) is None
        assert _cagr(100, None, 3) is None


# ---------------------------------------------------------------------------
# Margin metrics
# ---------------------------------------------------------------------------

class TestMargins:
    def test_gross_margin(self, store):
        _seed_full_data(store)
        compute_metrics("AAPL", store)

        metrics = store.get_metrics("AAPL", limit=1)
        assert len(metrics) == 1
        m = metrics[0]
        # 42676e6 / 94930e6 ≈ 0.4495
        assert m["gross_margin"] == pytest.approx(0.4495, abs=0.001)

    def test_net_margin(self, store):
        _seed_full_data(store)
        compute_metrics("AAPL", store)

        m = store.get_metrics("AAPL", limit=1)[0]
        # 14736e6 / 94930e6 ≈ 0.1552
        assert m["net_margin"] == pytest.approx(0.1552, abs=0.001)

    def test_operating_margin(self, store):
        _seed_full_data(store)
        compute_metrics("AAPL", store)

        m = store.get_metrics("AAPL", limit=1)[0]
        # 30561e6 / 94930e6 ≈ 0.3219
        assert m["operating_margin"] == pytest.approx(0.3219, abs=0.001)


# ---------------------------------------------------------------------------
# Returns
# ---------------------------------------------------------------------------

class TestReturns:
    def test_roe_ttm(self, store):
        _seed_full_data(store)
        compute_metrics("AAPL", store)

        m = store.get_metrics("AAPL", limit=1)[0]
        # ROE = TTM NI / avg(current_equity, period_start_equity)
        # TTM NI = 14736 + 21448 + 23636 + 33916 = 93736e6
        # current equity (Q4'24) = 56950e6
        # period-start equity (Q4'23 BS, one quarter before TTM window) = 62146e6
        # avg equity = (56950 + 62146) / 2 = 59548e6
        # ROE ≈ 1.574
        assert m["roe"] is not None
        assert m["roe"] == pytest.approx(1.574, abs=0.01)

    def test_roa(self, store):
        _seed_full_data(store)
        compute_metrics("AAPL", store)

        m = store.get_metrics("AAPL", limit=1)[0]
        assert m["roa"] is not None
        assert m["roa"] > 0

    def test_roic(self, store):
        _seed_full_data(store)
        compute_metrics("AAPL", store)

        m = store.get_metrics("AAPL", limit=1)[0]
        assert m["roic"] is not None


# ---------------------------------------------------------------------------
# Leverage
# ---------------------------------------------------------------------------

class TestLeverage:
    def test_debt_to_equity(self, store):
        _seed_full_data(store)
        compute_metrics("AAPL", store)

        m = store.get_metrics("AAPL", limit=1)[0]
        # 97300 / 56950 ≈ 1.708
        assert m["debt_to_equity"] == pytest.approx(1.708, abs=0.01)

    def test_current_ratio(self, store):
        _seed_full_data(store)
        compute_metrics("AAPL", store)

        m = store.get_metrics("AAPL", limit=1)[0]
        # 152987 / 176392 ≈ 0.867
        assert m["current_ratio"] == pytest.approx(0.867, abs=0.01)

    def test_quick_ratio(self, store):
        _seed_full_data(store)
        compute_metrics("AAPL", store)

        m = store.get_metrics("AAPL", limit=1)[0]
        # (152987 - 6331) / 176392 ≈ 0.831
        assert m["quick_ratio"] == pytest.approx(0.831, abs=0.01)


# ---------------------------------------------------------------------------
# YoY Growth
# ---------------------------------------------------------------------------

class TestGrowth:
    def test_revenue_yoy(self, store):
        _seed_full_data(store)
        compute_metrics("AAPL", store)

        m = store.get_metrics("AAPL", limit=1)[0]
        # Q4 2024: 94930 vs Q4 2023: 89498 → ~6.1%
        assert m["revenue_growth_yoy"] == pytest.approx(0.0607, abs=0.005)

    def test_eps_yoy(self, store):
        _seed_full_data(store)
        compute_metrics("AAPL", store)

        m = store.get_metrics("AAPL", limit=1)[0]
        # Q4 2024: 0.97 vs Q4 2023: 1.46 → -33.6%
        assert m["eps_growth_yoy"] is not None
        assert m["eps_growth_yoy"] < 0  # Decline

    def test_no_yoy_for_earliest_quarters(self, store):
        """Quarters without prior-year match should have NULL growth."""
        # Only seed 2 quarters, same fiscal year
        store.upsert_income("TEST", [
            {"date": "2024-09-28", "fiscalYear": "2024", "period": "Q4",
             "revenue": 100e6, "netIncome": 10e6},
            {"date": "2024-06-29", "fiscalYear": "2024", "period": "Q3",
             "revenue": 90e6, "netIncome": 9e6},
        ])
        compute_metrics("TEST", store)
        metrics = store.get_metrics("TEST")
        # Both should have NULL YoY (no fiscal_year-1 data)
        for m in metrics:
            assert m["revenue_growth_yoy"] is None


# ---------------------------------------------------------------------------
# QoQ Growth & Delta
# ---------------------------------------------------------------------------

class TestQoQ:
    def test_revenue_growth_qoq(self, store):
        _seed_full_data(store)
        compute_metrics("AAPL", store)

        # Most recent quarter (Q4'24): revenue 94930 vs Q3'24: 85778
        m = store.get_metrics("AAPL", limit=1)[0]
        expected = (94930e6 - 85778e6) / abs(85778e6)
        assert m["revenue_growth_qoq"] == pytest.approx(expected, abs=0.001)

    def test_net_income_growth_qoq(self, store):
        _seed_full_data(store)
        compute_metrics("AAPL", store)

        m = store.get_metrics("AAPL", limit=1)[0]
        # Q4'24 NI=14736 vs Q3'24 NI=21448 → decline
        expected = (14736e6 - 21448e6) / abs(21448e6)
        assert m["net_income_growth_qoq"] == pytest.approx(expected, abs=0.001)

    def test_eps_growth_qoq(self, store):
        _seed_full_data(store)
        compute_metrics("AAPL", store)

        m = store.get_metrics("AAPL", limit=1)[0]
        # Q4'24 eps=0.97 vs Q3'24 eps=1.40
        expected = (0.97 - 1.40) / abs(1.40)
        assert m["eps_growth_qoq"] == pytest.approx(expected, abs=0.001)

    def test_net_margin_delta_qoq(self, store):
        _seed_full_data(store)
        compute_metrics("AAPL", store)

        metrics = store.get_metrics("AAPL", limit=2)
        m_latest = metrics[0]  # Q4'24
        m_prev = metrics[1]    # Q3'24
        # Delta should equal difference in net_margin
        expected = m_latest["net_margin"] - m_prev["net_margin"]
        assert m_latest["net_margin_delta_qoq"] == pytest.approx(expected, abs=1e-6)

    def test_gross_margin_delta_qoq(self, store):
        _seed_full_data(store)
        compute_metrics("AAPL", store)

        metrics = store.get_metrics("AAPL", limit=2)
        m_latest = metrics[0]
        m_prev = metrics[1]
        expected = m_latest["gross_margin"] - m_prev["gross_margin"]
        assert m_latest["gross_margin_delta_qoq"] == pytest.approx(expected, abs=1e-6)

    def test_oldest_quarter_qoq_is_none(self, store):
        """The oldest quarter should have NULL QoQ fields."""
        _seed_full_data(store)
        compute_metrics("AAPL", store)

        # Get all metrics, oldest is last
        metrics = store.get_metrics("AAPL", limit=20)
        oldest = metrics[-1]
        assert oldest["revenue_growth_qoq"] is None
        assert oldest["net_income_growth_qoq"] is None
        assert oldest["eps_growth_qoq"] is None
        assert oldest["operating_income_growth_qoq"] is None
        assert oldest["gross_margin_delta_qoq"] is None
        assert oldest["operating_margin_delta_qoq"] is None
        assert oldest["net_margin_delta_qoq"] is None
        assert oldest["ebitda_margin_delta_qoq"] is None
        assert oldest["roe_delta_qoq"] is None
        assert oldest["roic_delta_qoq"] is None

    def test_single_quarter_qoq_is_none(self, store):
        """Symbol with only 1 quarter should have NULL QoQ."""
        store.upsert_income("SOLO", [
            {"date": "2024-09-28", "fiscalYear": "2024", "period": "Q4",
             "revenue": 100e6, "netIncome": 10e6},
        ])
        compute_metrics("SOLO", store)
        m = store.get_metrics("SOLO", limit=1)[0]
        assert m["revenue_growth_qoq"] is None
        assert m["net_margin_delta_qoq"] is None
        assert m["revenue_cagr_4q"] is None
        assert m["net_margin_change_4q"] is None


# ---------------------------------------------------------------------------
# CAGR & 4Q Change
# ---------------------------------------------------------------------------

class TestCAGR:
    def test_revenue_cagr_4q(self, store):
        """CAGR over 4 quarters (Q4'24 vs Q1'24, 3 periods)."""
        _seed_full_data(store)
        compute_metrics("AAPL", store)

        m = store.get_metrics("AAPL", limit=1)[0]
        # Q4'24 revenue=94930, Q1'24 revenue=119575, 3 periods
        expected = (94930e6 / 119575e6) ** (1.0 / 3) - 1
        assert m["revenue_cagr_4q"] == pytest.approx(expected, abs=0.001)

    def test_net_income_cagr_4q(self, store):
        _seed_full_data(store)
        compute_metrics("AAPL", store)

        m = store.get_metrics("AAPL", limit=1)[0]
        # Q4'24 NI=14736, Q1'24 NI=33916, 3 periods
        expected = (14736e6 / 33916e6) ** (1.0 / 3) - 1
        assert m["net_income_cagr_4q"] == pytest.approx(expected, abs=0.001)

    def test_eps_cagr_4q(self, store):
        _seed_full_data(store)
        compute_metrics("AAPL", store)

        m = store.get_metrics("AAPL", limit=1)[0]
        # Q4'24 eps=0.97, Q1'24 eps=2.18
        expected = (0.97 / 2.18) ** (1.0 / 3) - 1
        assert m["eps_cagr_4q"] == pytest.approx(expected, abs=0.001)

    def test_net_margin_change_4q(self, store):
        _seed_full_data(store)
        compute_metrics("AAPL", store)

        metrics = store.get_metrics("AAPL", limit=4)
        m_latest = metrics[0]   # Q4'24
        m_base = metrics[3]     # Q1'24
        expected = m_latest["net_margin"] - m_base["net_margin"]
        assert m_latest["net_margin_change_4q"] == pytest.approx(expected, abs=1e-6)

    def test_insufficient_quarters_cagr_is_none(self, store):
        """Less than 4 quarters should produce NULL CAGR."""
        store.upsert_income("SHORT", [
            {"date": "2024-09-28", "fiscalYear": "2024", "period": "Q4",
             "revenue": 100e6, "netIncome": 10e6},
            {"date": "2024-06-29", "fiscalYear": "2024", "period": "Q3",
             "revenue": 90e6, "netIncome": 9e6},
            {"date": "2024-03-30", "fiscalYear": "2024", "period": "Q2",
             "revenue": 80e6, "netIncome": 8e6},
        ])
        compute_metrics("SHORT", store)
        m = store.get_metrics("SHORT", limit=1)[0]
        assert m["revenue_cagr_4q"] is None
        assert m["net_margin_change_4q"] is None

    def test_negative_base_cagr_is_none(self, store):
        """Negative base income should produce NULL CAGR."""
        store.upsert_income("NEG", [
            {"date": "2024-09-28", "fiscalYear": "2024", "period": "Q4",
             "revenue": 100e6, "netIncome": 10e6},
            {"date": "2024-06-29", "fiscalYear": "2024", "period": "Q3",
             "revenue": 90e6, "netIncome": 9e6},
            {"date": "2024-03-30", "fiscalYear": "2024", "period": "Q2",
             "revenue": 80e6, "netIncome": 8e6},
            {"date": "2023-12-30", "fiscalYear": "2024", "period": "Q1",
             "revenue": 70e6, "netIncome": -5e6},
        ])
        compute_metrics("NEG", store)
        m = store.get_metrics("NEG", limit=1)[0]
        assert m["revenue_cagr_4q"] is not None  # revenue base positive
        assert m["net_income_cagr_4q"] is None    # NI base negative


# ---------------------------------------------------------------------------
# Cash Flow
# ---------------------------------------------------------------------------

class TestCashFlowMetrics:
    def test_fcf_margin(self, store):
        _seed_full_data(store)
        compute_metrics("AAPL", store)

        m = store.get_metrics("AAPL", limit=1)[0]
        # 23900 / 94930 ≈ 0.2518
        assert m["fcf_margin"] == pytest.approx(0.2518, abs=0.005)

    def test_fcf_to_net_income(self, store):
        _seed_full_data(store)
        compute_metrics("AAPL", store)

        m = store.get_metrics("AAPL", limit=1)[0]
        # 23900 / 14736 ≈ 1.622
        assert m["fcf_to_net_income"] == pytest.approx(1.622, abs=0.01)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_no_income_data(self, store):
        """Symbol with no data should return 0."""
        result = compute_metrics("EMPTY", store)
        assert result == 0

    def test_zero_revenue(self, store):
        """Zero revenue should produce NULL margins."""
        store.upsert_income("ZERO", [
            {"date": "2024-09-28", "fiscalYear": "2024", "period": "Q4",
             "revenue": 0, "grossProfit": 0, "netIncome": -100e6,
             "operatingIncome": -50e6},
        ])
        compute_metrics("ZERO", store)
        m = store.get_metrics("ZERO", limit=1)[0]
        assert m["gross_margin"] is None
        assert m["net_margin"] is None

    def test_missing_balance_sheet(self, store):
        """Metrics should handle missing BS gracefully."""
        store.upsert_income("NOBALANCE", [
            {"date": "2024-09-28", "fiscalYear": "2024", "period": "Q4",
             "revenue": 100e6, "netIncome": 10e6},
        ])
        compute_metrics("NOBALANCE", store)
        m = store.get_metrics("NOBALANCE", limit=1)[0]
        # Margins should work, leverage should be NULL
        assert m["net_margin"] == pytest.approx(0.10)
        assert m["debt_to_equity"] is None
        assert m["current_ratio"] is None


# ---------------------------------------------------------------------------
# compute_all_metrics
# ---------------------------------------------------------------------------

class TestComputeAll:
    def test_compute_all(self, store):
        _seed_full_data(store)
        # Add a second symbol
        store.upsert_income("NVDA", [
            {"date": "2024-10-27", "fiscalYear": "2025", "period": "Q3",
             "revenue": 35082e6, "grossProfit": 26156e6, "netIncome": 19309e6,
             "operatingIncome": 21869e6, "ebitda": 23000e6},
        ])

        results = compute_all_metrics(store=store)
        assert "AAPL" in results
        assert "NVDA" in results
        assert results["AAPL"] > 0
        assert results["NVDA"] > 0

    def test_compute_all_with_symbol_filter(self, store):
        _seed_full_data(store)
        store.upsert_income("NVDA", [
            {"date": "2024-10-27", "fiscalYear": "2025", "period": "Q3",
             "revenue": 35082e6, "netIncome": 19309e6},
        ])

        results = compute_all_metrics(symbols=["AAPL"], store=store)
        assert "AAPL" in results
        assert "NVDA" not in results
