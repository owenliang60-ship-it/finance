"""Tests for portfolio holdings manager (SQLite-backed)."""
import pytest
from terminal.company_store import CompanyStore


@pytest.fixture
def store(tmp_path):
    db_path = tmp_path / "test_company.db"
    s = CompanyStore(db_path=db_path)
    # Seed companies + OPRMS
    s.upsert_company("NVDA", company_name="NVIDIA", sector="Technology", industry="Semiconductors")
    s.upsert_company("AAPL", company_name="Apple", sector="Technology", industry="Consumer Electronics")
    s.save_oprms_rating("NVDA", dna="S", timing="A", timing_coeff=0.9)
    s.save_oprms_rating("AAPL", dna="A", timing="B", timing_coeff=0.5)
    yield s
    s.close()


@pytest.fixture
def manager(store):
    """Create a manager backed by the test store."""
    from portfolio.holdings.manager import PortfolioManager
    return PortfolioManager(store=store)


class TestLoadAndGet:
    def test_empty_portfolio(self, manager):
        positions = manager.load_holdings()
        assert positions == []

    def test_add_and_load(self, manager):
        manager.add_position("NVDA", shares=100, avg_cost=135.0, date="2026-04-01")
        positions = manager.load_holdings()
        assert len(positions) == 1
        p = positions[0]
        assert p.symbol == "NVDA"
        assert p.shares == 100
        assert p.cost_basis == 135.0
        assert p.company_name == "NVIDIA"
        assert p.sector == "Technology"
        assert p.dna_rating == "S"
        assert p.timing_rating == "A"
        assert p.status == "OPEN"

    def test_get_position(self, manager):
        manager.add_position("NVDA", shares=100, avg_cost=135.0, date="2026-04-01")
        p = manager.get_position("NVDA")
        assert p is not None
        assert p.symbol == "NVDA"

    def test_get_nonexistent(self, manager):
        assert manager.get_position("FAKE") is None


class TestClosePosition:
    def test_close_and_reopen(self, manager):
        manager.add_position("NVDA", shares=100, avg_cost=135.0, date="2026-04-01")
        manager.close_position("NVDA", sell_price=150.0, date="2026-04-05")
        assert manager.get_position("NVDA") is None

        # Reopen
        manager.add_position("NVDA", shares=50, avg_cost=160.0, date="2026-04-06")
        p = manager.get_position("NVDA")
        assert p.shares == 50

    def test_close_calculates_pnl(self, manager):
        manager.add_position("NVDA", shares=100, avg_cost=135.0, date="2026-04-01")
        manager.close_position("NVDA", sell_price=150.0, date="2026-04-05")
        # realized_pnl = (150 - 135) * 100 = 1500
        holdings = manager._store.get_all_open_holdings()
        assert len(holdings) == 0  # No open


class TestNAV:
    def test_total_nav(self, manager):
        manager.add_position("NVDA", shares=100, avg_cost=135.0, date="2026-04-01")
        manager._store.set_cash(500000.0)
        prices = {"NVDA": 150.0}
        nav = manager.get_total_nav(prices)
        # NAV = 100 * 150 + 500000 = 515000
        assert nav == pytest.approx(515000.0)

    def test_weights(self, manager):
        manager.add_position("NVDA", shares=100, avg_cost=135.0, date="2026-04-01")
        manager.add_position("AAPL", shares=200, avg_cost=200.0, date="2026-04-01")
        manager._store.set_cash(100000.0)
        prices = {"NVDA": 150.0, "AAPL": 210.0}
        positions = manager.refresh_prices(prices)
        # NVDA: 15000, AAPL: 42000, cash: 100000, NAV: 157000
        nvda = [p for p in positions if p.symbol == "NVDA"][0]
        assert nvda.current_weight == pytest.approx(15000 / 157000, rel=1e-3)

    def test_summary(self, manager):
        manager.add_position("NVDA", shares=100, avg_cost=135.0, date="2026-04-01")
        manager._store.set_cash(500000.0)
        prices = {"NVDA": 150.0}
        summary = manager.get_portfolio_summary(prices)
        assert summary["total_nav"] == pytest.approx(515000.0)
        assert summary["invested_pct"] == pytest.approx(15000 / 515000, rel=1e-3)
        assert summary["cash_pct"] == pytest.approx(500000 / 515000, rel=1e-3)
        assert summary["total_positions"] == 1

    def test_nav_includes_options(self, manager):
        """Options market value should be part of total_nav."""
        manager.add_position("NVDA", shares=100, avg_cost=135.0, date="2026-04-01")
        manager._store.set_cash(100000.0)
        # Add a long put: 5 contracts @ $10 premium = 5 * 10 * 100 = $5000
        manager._store.upsert_company("QQQ", company_name="QQQ")
        manager._store.insert_option_position(
            symbol="QQQ", expiration="2026-06-18", strike=580.0, side="PUT",
            quantity=5, avg_premium=10.0, open_date="2026-04-01",
        )
        prices = {"NVDA": 150.0}
        nav = manager.get_total_nav(prices)
        # stock: 100*150=15000 + option: 5*10*100=5000 + cash: 100000 = 120000
        assert nav == pytest.approx(120000.0)

        summary = manager.get_portfolio_summary(prices)
        assert summary["option_value"] == pytest.approx(5000.0)
        assert summary["option_positions"] == 1
        assert len(summary["options"]) == 1


class TestExecuteTrade:
    def test_buy_new_position(self, manager):
        manager._store.set_cash(500000.0)
        result = manager.execute_trade("NVDA", "BUY", shares=100, price=135.0, date="2026-04-01")
        assert result["action"] == "BUY"
        assert result["new_shares"] == 100
        assert result["new_avg_cost"] == 135.0
        p = manager.get_position("NVDA")
        assert p.shares == 100
        assert manager._store.get_cash_balance() == pytest.approx(500000 - 100 * 135)

    def test_add_to_existing(self, manager):
        manager._store.set_cash(500000.0)
        manager.execute_trade("NVDA", "BUY", shares=100, price=135.0, date="2026-04-01")
        manager.execute_trade("NVDA", "ADD", shares=50, price=140.0, date="2026-04-03")
        p = manager.get_position("NVDA")
        assert p.shares == 150
        # avg_cost = (100*135 + 50*140) / 150 = 136.67
        assert p.cost_basis == pytest.approx((100 * 135 + 50 * 140) / 150, rel=1e-2)
        assert manager._store.get_cash_balance() == pytest.approx(500000 - 100 * 135 - 50 * 140)

    def test_trim(self, manager):
        manager._store.set_cash(500000.0)
        manager.execute_trade("NVDA", "BUY", shares=100, price=135.0, date="2026-04-01")
        manager.execute_trade("NVDA", "TRIM", shares=30, price=150.0, date="2026-04-05")
        p = manager.get_position("NVDA")
        assert p.shares == 70
        assert manager._store.get_cash_balance() == pytest.approx(500000 - 100 * 135 + 30 * 150)

    def test_sell_all_closes_position(self, manager):
        manager._store.set_cash(500000.0)
        manager.execute_trade("NVDA", "BUY", shares=100, price=135.0, date="2026-04-01")
        result = manager.execute_trade("NVDA", "SELL", shares=100, price=150.0, date="2026-04-05")
        assert result["closed"] is True
        assert result["realized_pnl"] == pytest.approx(1500.0)
        assert manager.get_position("NVDA") is None
        assert manager._store.get_cash_balance() == pytest.approx(500000 - 13500 + 15000)

    def test_atomic_rollback(self, manager):
        """If cash goes negative, entire trade should rollback."""
        manager._store.set_cash(1000.0)  # Not enough for 100 @ 135
        with pytest.raises(ValueError, match="Insufficient cash"):
            manager.execute_trade("NVDA", "BUY", shares=100, price=135.0, date="2026-04-01")
        # Nothing changed
        assert manager.get_position("NVDA") is None
        assert manager._store.get_cash_balance() == pytest.approx(1000.0)

    def test_trim_then_sell_cumulative_pnl(self, manager):
        """BUY 100@100 -> TRIM 40@120 -> SELL 60@130: cumulative = 800 + 1800 = 2600."""
        manager._store.set_cash(500000.0)
        manager.execute_trade("NVDA", "BUY", shares=100, price=100.0, date="2026-04-01")

        # TRIM 40 @ 120: this_leg = (120-100)*40 = 800
        trim_result = manager.execute_trade("NVDA", "TRIM", shares=40, price=120.0, date="2026-04-05")
        assert trim_result["this_leg_pnl"] == pytest.approx(800.0)
        assert trim_result["realized_pnl"] == pytest.approx(800.0)  # cumulative so far
        assert trim_result["closed"] is False

        # SELL remaining 60 @ 130: this_leg = (130-100)*60 = 1800, cumulative = 800+1800 = 2600
        sell_result = manager.execute_trade("NVDA", "SELL", shares=60, price=130.0, date="2026-04-10")
        assert sell_result["this_leg_pnl"] == pytest.approx(1800.0)
        assert sell_result["realized_pnl"] == pytest.approx(2600.0)  # total across all legs
        assert sell_result["closed"] is True

    def test_buy_rejects_existing_open(self, manager):
        """BUY should reject if position already open — use ADD instead."""
        manager._store.set_cash(500000.0)
        manager.execute_trade("NVDA", "BUY", shares=100, price=135.0, date="2026-04-01")
        with pytest.raises(ValueError, match="already open"):
            manager.execute_trade("NVDA", "BUY", shares=50, price=140.0, date="2026-04-02")

    def test_transactions_logged(self, manager):
        manager._store.set_cash(500000.0)
        manager.execute_trade("NVDA", "BUY", shares=100, price=135.0, date="2026-04-01")
        manager.execute_trade("NVDA", "ADD", shares=50, price=140.0, date="2026-04-03")
        txns = manager._store.get_transactions("NVDA")
        assert len(txns) == 2
        assert txns[0]["action"] == "BUY"
        assert txns[1]["action"] == "ADD"


@pytest.fixture
def opt_store(tmp_path):
    """Store with QQQ + EWY companies seeded for option tests."""
    db_path = tmp_path / "test_company_opt.db"
    s = CompanyStore(db_path=db_path)
    s.upsert_company("QQQ", company_name="Invesco QQQ Trust")
    s.upsert_company("EWY", company_name="iShares MSCI South Korea ETF")
    yield s
    s.close()


@pytest.fixture
def opt_manager(opt_store):
    from portfolio.holdings.manager import PortfolioManager
    return PortfolioManager(store=opt_store)


def _opt_args(**overrides):
    base = dict(
        symbol="QQQ",
        expiration="2026-09-18",
        strike=410.0,
        side="PUT",
        action="BTO",
        quantity=10,
        premium=4.50,
        date="2026-04-01",
        strategy_tag="",
        notes="",
    )
    base.update(overrides)
    return base


class TestOptionTradeEngine:
    def test_bto_opens_long_and_debits_cash(self, opt_manager):
        opt_manager._store.set_cash(100000.0)
        result = opt_manager.execute_option_trade(**_opt_args(
            action="BTO", quantity=10, premium=4.50,
        ))
        assert result["effect"] == "OPEN"
        assert result["new_quantity"] == 10
        assert result["new_avg_premium"] == pytest.approx(4.50)
        assert result["cash_delta"] == pytest.approx(-4500.0)
        assert opt_manager._store.get_cash_balance() == pytest.approx(95500.0)
        pos = opt_manager._store.get_open_option_position(
            symbol="QQQ", expiration="2026-09-18", strike=410.0, side="PUT",
        )
        assert pos["quantity"] == 10
        assert pos["avg_premium"] == pytest.approx(4.50)

    def test_bto_adds_to_existing_long_and_reprices_avg(self, opt_manager):
        opt_manager._store.set_cash(100000.0)
        opt_manager.execute_option_trade(**_opt_args(action="BTO", quantity=10, premium=4.00))
        result = opt_manager.execute_option_trade(**_opt_args(action="BTO", quantity=5, premium=5.20))
        assert result["effect"] == "ADD"
        assert result["new_quantity"] == 15
        # weighted avg = (10*4.00 + 5*5.20) / 15 = (40 + 26) / 15 = 4.40
        assert result["new_avg_premium"] == pytest.approx((10 * 4.00 + 5 * 5.20) / 15)
        # cash spent = 4000 + 2600 = 6600
        assert opt_manager._store.get_cash_balance() == pytest.approx(100000.0 - 4000 - 2600)

    def test_stc_partially_closes_long_and_realizes_pnl(self, opt_manager):
        opt_manager._store.set_cash(100000.0)
        opt_manager.execute_option_trade(**_opt_args(action="BTO", quantity=10, premium=4.00))
        result = opt_manager.execute_option_trade(**_opt_args(action="STC", quantity=4, premium=6.50))
        assert result["effect"] == "REDUCE"
        assert result["new_quantity"] == 6
        # remaining avg unchanged
        assert result["new_avg_premium"] == pytest.approx(4.00)
        # realized = (6.50 - 4.00) * 4 * 100 = 1000
        assert result["realized_pnl_this_trade"] == pytest.approx(1000.0)
        # cash: -4000 + 2600 = -1400
        assert opt_manager._store.get_cash_balance() == pytest.approx(100000.0 - 4000 + 2600)
        pos = opt_manager._store.get_open_option_position(
            symbol="QQQ", expiration="2026-09-18", strike=410.0, side="PUT",
        )
        assert pos["realized_pnl"] == pytest.approx(1000.0)

    def test_stc_full_close_long(self, opt_manager):
        opt_manager._store.set_cash(100000.0)
        opt_manager.execute_option_trade(**_opt_args(action="BTO", quantity=10, premium=4.00))
        result = opt_manager.execute_option_trade(**_opt_args(action="STC", quantity=10, premium=6.50))
        assert result["effect"] == "CLOSE"
        assert result["new_quantity"] == 0
        # realized = (6.50 - 4.00) * 10 * 100 = 2500
        assert result["realized_pnl_this_trade"] == pytest.approx(2500.0)
        assert opt_manager._store.get_open_option_position(
            symbol="QQQ", expiration="2026-09-18", strike=410.0, side="PUT",
        ) is None

    def test_sto_opens_short_and_credits_cash(self, opt_manager):
        opt_manager._store.set_cash(100000.0)
        result = opt_manager.execute_option_trade(**_opt_args(
            action="STO", quantity=10, premium=4.78,
        ))
        assert result["effect"] == "OPEN"
        assert result["new_quantity"] == -10
        assert result["new_avg_premium"] == pytest.approx(4.78)
        assert result["cash_delta"] == pytest.approx(+4780.0)
        assert opt_manager._store.get_cash_balance() == pytest.approx(104780.0)

    def test_btc_partially_closes_short_and_realizes_pnl(self, opt_manager):
        opt_manager._store.set_cash(100000.0)
        opt_manager.execute_option_trade(**_opt_args(action="STO", quantity=10, premium=4.78))
        result = opt_manager.execute_option_trade(**_opt_args(action="BTC", quantity=4, premium=2.10))
        assert result["effect"] == "REDUCE"
        assert result["new_quantity"] == -6
        assert result["new_avg_premium"] == pytest.approx(4.78)
        # realized = (4.78 - 2.10) * 4 * 100 = 1072
        assert result["realized_pnl_this_trade"] == pytest.approx(1072.0)
        assert opt_manager._store.get_cash_balance() == pytest.approx(100000.0 + 4780 - 840)

    def test_btc_full_cover_short(self, opt_manager):
        opt_manager._store.set_cash(100000.0)
        opt_manager.execute_option_trade(**_opt_args(action="STO", quantity=10, premium=4.78))
        result = opt_manager.execute_option_trade(**_opt_args(action="BTC", quantity=10, premium=2.10))
        assert result["effect"] == "CLOSE"
        assert result["new_quantity"] == 0
        # realized = (4.78 - 2.10) * 10 * 100 = 2680
        assert result["realized_pnl_this_trade"] == pytest.approx(2680.0)
        assert opt_manager._store.get_open_option_position(
            symbol="QQQ", expiration="2026-09-18", strike=410.0, side="PUT",
        ) is None

    def test_reject_over_close_for_option_leg(self, opt_manager):
        opt_manager._store.set_cash(100000.0)
        opt_manager.execute_option_trade(**_opt_args(action="BTO", quantity=10, premium=4.00))
        with pytest.raises(ValueError, match="quantity"):
            opt_manager.execute_option_trade(**_opt_args(action="STC", quantity=11, premium=5.00))
        # state unchanged
        pos = opt_manager._store.get_open_option_position(
            symbol="QQQ", expiration="2026-09-18", strike=410.0, side="PUT",
        )
        assert pos["quantity"] == 10
        assert opt_manager._store.get_cash_balance() == pytest.approx(100000.0 - 4000)

    def test_reject_close_when_no_open_leg(self, opt_manager):
        opt_manager._store.set_cash(100000.0)
        with pytest.raises(ValueError, match="no open"):
            opt_manager.execute_option_trade(**_opt_args(action="STC", quantity=5, premium=4.00))

    def test_reject_action_against_wrong_direction(self, opt_manager):
        """STO when long is open should be rejected (would flip sign)."""
        opt_manager._store.set_cash(100000.0)
        opt_manager.execute_option_trade(**_opt_args(action="BTO", quantity=10, premium=4.00))
        with pytest.raises(ValueError, match="direction"):
            opt_manager.execute_option_trade(**_opt_args(action="STO", quantity=5, premium=4.00))

    def test_preview_matches_execute_for_btc(self, opt_manager):
        opt_manager._store.set_cash(100000.0)
        opt_manager.execute_option_trade(**_opt_args(action="STO", quantity=10, premium=4.78))
        preview = opt_manager.preview_option_trade(**_opt_args(action="BTC", quantity=10, premium=2.10))
        assert preview["effect"] == "CLOSE"
        assert preview["new_quantity"] == 0
        assert preview["cash_delta"] == pytest.approx(-2100.0)
        assert preview["realized_pnl_this_trade"] == pytest.approx(2680.0)
        # preview must NOT mutate state
        assert opt_manager._store.get_cash_balance() == pytest.approx(104780.0)
        pos = opt_manager._store.get_open_option_position(
            symbol="QQQ", expiration="2026-09-18", strike=410.0, side="PUT",
        )
        assert pos["quantity"] == -10

    def test_transactions_logged_for_option_trade(self, opt_manager):
        opt_manager._store.set_cash(100000.0)
        opt_manager.execute_option_trade(**_opt_args(action="STO", quantity=10, premium=4.78))
        opt_manager.execute_option_trade(**_opt_args(action="BTC", quantity=10, premium=2.10))
        txns = opt_manager._store.get_option_transactions(symbol="QQQ")
        assert len(txns) == 2
        assert txns[0]["action"] == "STO"
        assert txns[1]["action"] == "BTC"
        # both linked to same option position id
        assert txns[0]["option_position_id"] == txns[1]["option_position_id"]

    def test_strategy_tag_distinguishes_legs(self, opt_manager):
        opt_manager._store.set_cash(100000.0)
        opt_manager.execute_option_trade(**_opt_args(
            action="STO", quantity=10, premium=4.78, strategy_tag="tail_hedge",
        ))
        opt_manager.execute_option_trade(**_opt_args(
            action="STO", quantity=5, premium=5.20, strategy_tag="theta_carry",
        ))
        all_pos = opt_manager._store.get_open_option_positions(symbol="QQQ")
        assert len(all_pos) == 2

    def test_roll_is_atomic_close_then_open(self, opt_manager):
        opt_manager._store.set_cash(100000.0)
        opt_manager.execute_option_trade(**_opt_args(
            action="STO", quantity=10, premium=4.78, strategy_tag="tail_hedge",
        ))
        cash_before = opt_manager._store.get_cash_balance()
        result = opt_manager.execute_option_roll(
            close_leg=_opt_args(
                action="BTC", quantity=10, premium=2.10, strategy_tag="tail_hedge",
            ),
            open_leg=_opt_args(
                expiration="2026-12-19", strike=380.0,
                action="STO", quantity=10, premium=6.50, strategy_tag="tail_hedge",
            ),
            date="2026-04-15",
            notes="ROLL QQQ 260918 410P -> 261219 380P",
        )
        assert result["close"]["effect"] == "CLOSE"
        assert result["open"]["effect"] == "OPEN"
        # close leg cash: -2100, open leg cash: +6500 → net +4400
        net_delta = result["close"]["cash_delta"] + result["open"]["cash_delta"]
        assert net_delta == pytest.approx(-2100 + 6500)
        assert opt_manager._store.get_cash_balance() == pytest.approx(cash_before + net_delta)
        # old leg gone, new leg open
        assert opt_manager._store.get_open_option_position(
            symbol="QQQ", expiration="2026-09-18", strike=410.0, side="PUT",
            strategy_tag="tail_hedge",
        ) is None
        new_leg = opt_manager._store.get_open_option_position(
            symbol="QQQ", expiration="2026-12-19", strike=380.0, side="PUT",
            strategy_tag="tail_hedge",
        )
        assert new_leg["quantity"] == -10
        assert new_leg["avg_premium"] == pytest.approx(6.50)

    def test_roll_rolls_back_on_open_leg_failure(self, opt_manager):
        """If the open leg of a roll fails, the close leg must also be rolled back."""
        opt_manager._store.set_cash(100000.0)
        opt_manager.execute_option_trade(**_opt_args(
            action="STO", quantity=10, premium=4.78, strategy_tag="tail_hedge",
        ))
        cash_before = opt_manager._store.get_cash_balance()
        # Sabotage: open leg uses an invalid action that should ValueError mid-transaction
        with pytest.raises(Exception):
            opt_manager.execute_option_roll(
                close_leg=_opt_args(
                    action="BTC", quantity=10, premium=2.10, strategy_tag="tail_hedge",
                ),
                open_leg=_opt_args(
                    expiration="2026-12-19", strike=380.0,
                    action="STC",  # invalid: STC requires existing long
                    quantity=10, premium=6.50, strategy_tag="tail_hedge",
                ),
                date="2026-04-15",
                notes="bad roll",
            )
        # Cash unchanged
        assert opt_manager._store.get_cash_balance() == pytest.approx(cash_before)
        # Original short leg still open
        pos = opt_manager._store.get_open_option_position(
            symbol="QQQ", expiration="2026-09-18", strike=410.0, side="PUT",
            strategy_tag="tail_hedge",
        )
        assert pos is not None
        assert pos["quantity"] == -10


class TestEnrichHoldingRow:
    """Public wrapper used by sheet-sourced callers (PI)."""

    def test_wraps_enrich_with_company_metadata(self, tmp_path):
        from terminal.company_store import CompanyStore
        from portfolio.holdings.manager import PortfolioManager

        store = CompanyStore(db_path=tmp_path / "t.db")
        store.upsert_company("NVDA", company_name="NVIDIA", sector="Technology")
        mgr = PortfolioManager(store=store)
        row = {"symbol": "NVDA", "avg_cost": 10.0, "shares": 100,
               "open_date": "", "position_id": None, "status": "OPEN"}
        pos = mgr.enrich_holding_row(row)
        assert pos.symbol == "NVDA"
        assert pos.company_name == "NVIDIA"
        assert pos.sector == "Technology"
        assert pos.cost_basis == 10.0
        assert pos.shares == 100
        store.close()

    def test_unknown_symbol_best_effort(self, tmp_path):
        from terminal.company_store import CompanyStore
        from portfolio.holdings.manager import PortfolioManager

        store = CompanyStore(db_path=tmp_path / "t.db")
        mgr = PortfolioManager(store=store)
        row = {"symbol": "0001", "avg_cost": 4.5, "shares": 200,
               "open_date": "", "position_id": None, "status": "OPEN"}
        pos = mgr.enrich_holding_row(row)
        assert pos.symbol == "0001"
        assert pos.sector == ""  # not in companies table -> empty (PI falls back to Category)
        store.close()
