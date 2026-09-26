"""Three-index weekly PE backfill: routing, gates, provenance and atomicity.

Covers the orchestration contract of `scripts/backfill_index_pe_history.py`
and the weekly point kernel in `terminal/index_pe_weekly.py`:

* CLI routing over SPY/QQQ/SOXX at weekly frequency across a five-year window;
* the run manifest landing before the first per-symbol remote call;
* weekly sampling (last publishable trading day of each ISO week, no
  forward-fill across a gap);
* the R1 caliber -- the published TTM number is the aggregate `Sigma mcap /
  Sigma NI` with the 90% gate applied to that aggregate, never the holding
  weighted proxy;
* share-class market caps merged upstream of the engines, per the convention
  declared in `config/baskets/share_class_groups.json`;
* fail-closed behaviour: >20% member failure, contaminated split windows,
  dry-run write freedom, per-basket transaction isolation.

Network is never touched: every client here is a `Mock`, and no test opens the
live `data/market.db`.
"""
import json
import shutil
import sqlite3
from argparse import Namespace
from datetime import date
from pathlib import Path
from unittest.mock import Mock

import pytest

import scripts.backfill_index_pe_history as backfill
import scripts.verify_index_pe_history as verifier
from scripts.backfill_index_pe_history import (
    backfill_basket,
    parse_args,
    resolve_window,
)
from src.data.fmp_forward_ingestion import infer_basket_rebalance_close
from src.data.market_store import MarketStore
from tests.index_pe_identity_helpers import seed_source_identity, synthetic_lei
from terminal.index_pe_weekly import (
    WEEKLY_METHODOLOGY_VERSION,
    compute_weekly_point,
    group_trading_dates_by_week,
    resolve_company_market_cap,
    select_weekly_rows,
)


# ---------------------------------------------------------------------------
# fixtures: one tiny two-member basket with a full quarterly history
# ---------------------------------------------------------------------------

TRADING_DATES = [
    # 2026-01-05..09 is one ISO week, 2026-01-12..16 the next.
    "2025-12-29", "2025-12-30", "2025-12-31",
    "2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08",
    "2026-01-09", "2026-01-12", "2026-01-13", "2026-01-14", "2026-01-15",
    "2026-01-16",
]


@pytest.mark.parametrize("disclosed_at,live_effective,excluded", [
    ("2026-08-28", "2026-06-22", True),
    ("2026-09-12", "2026-06-22", False),
    ("2026-08-28", "2026-09-21", False),
])
def test_only_always_dominated_live_snapshot_is_outside_source_scope(
    disclosed_at, live_effective, excluded,
):
    disclosure = {"holding_date": "2026-06-30", "source_kind": "disclosure",
                  "composition_effective_date": "2026-06-22",
                  "composition_available_date": disclosed_at}
    live = {"holding_date": "2026-09-11", "source_kind": "live",
            "composition_effective_date": live_effective,
            "composition_available_date": "2026-09-11"}
    rows = [disclosure, live]
    original = json.dumps(rows, sort_keys=True)
    result = backfill._window_snapshots(rows, ("2021-09-12", "2026-09-26"))
    assert (live not in result) is excluded
    assert json.dumps(rows, sort_keys=True) == original

COMPOSITION = {
    "holding_date": "2025-12-31",
    "anchor_trading_date": "2025-12-31",
    "composition_effective_date": "2025-12-22",
    "composition_available_date": "2026-01-02",
    "weight_basis": "fixed_rebalance_weight_proxy",
    "data_quality_tier": "historical_disclosure_fixed_proxy",
    "snapshot_warnings": [],
    "source_kind": "disclosure",
}


def _holding(symbol, weight, index, covered_by=None):
    return {
        "basket_symbol": "SPY", "holding_date": "2025-12-31",
        "source_kind": "disclosure", "raw_row_index": index,
        "rebalance_close_date": "2025-12-19",
        "composition_effective_date": "2025-12-22",
        "composition_available_date": "2026-01-02",
        "raw_symbol": symbol,
        "symbol": None if covered_by else symbol,
        "name": symbol, "weight_pct": weight, "market_value": weight * 10,
        "included": 0 if covered_by else 1,
        "filter_reason": "dual_class_secondary" if covered_by else None,
        "covered_by": covered_by,
        "snapshot_warnings_json": "[]",
        "alias_symbol": None, "alias_mode": None, "alias_reason": None,
    }


def _quarters(symbol, dates, income, currency="USD"):
    return [
        {"symbol": symbol, "date": day, "period": f"Q{index % 4 + 1}",
         "accepted_date": f"{day[:8]}{int(day[8:]):02d} 16:00:00"
         if False else _accepted_for(day),
         "reported_currency": currency, "net_income": income}
        for index, day in enumerate(dates)
    ]


def _accepted_for(fiscal_date):
    """A filing lands ~20 days after the fiscal period end."""
    from datetime import date, timedelta
    return (date.fromisoformat(fiscal_date)
            + timedelta(days=20)).isoformat() + " 16:00:00"


TTM_FISCAL = ["2024-12-31", "2025-03-31", "2025-06-30", "2025-09-30"]
NTM_FISCAL = ["2026-03-31", "2026-06-30", "2026-09-30", "2026-12-31"]


def _sources(members=("AAA", "BBB"), income=25.0, market_cap=1000.0):
    income_by_symbol = {
        symbol: _quarters(symbol, TTM_FISCAL + NTM_FISCAL, income)
        for symbol in members
    }
    market_cap_by_symbol = {
        symbol: [{"symbol": symbol, "date": day, "market_cap": market_cap}
                 for day in TRADING_DATES]
        for symbol in members
    }
    sanity_by_symbol = {
        symbol: [{"symbol": symbol, "date": day, "status": "clean",
                  "candidate": False}
                 for day in TRADING_DATES]
        for symbol in members
    }
    return {
        "income_by_symbol": income_by_symbol,
        "market_cap_by_symbol": market_cap_by_symbol,
        "sanity_by_symbol": sanity_by_symbol,
        "estimates_by_symbol": {},
        "fx_by_currency": {},
    }


def _point(valuation_date="2026-01-09", holding_rows=None, sources=None,
           **overrides):
    holding_rows = holding_rows or [
        _holding("AAA", 60.0, 0), _holding("BBB", 40.0, 1)]
    payload = dict(sources or _sources())
    payload.update(overrides)
    return compute_weekly_point(
        basket_symbol="SPY", valuation_date=valuation_date,
        holding_rows=holding_rows, composition=COMPOSITION,
        trading_dates=TRADING_DATES, **payload)


# ---------------------------------------------------------------------------
# RED 1: CLI routing
# ---------------------------------------------------------------------------

def test_cli_routes_three_baskets_at_weekly_frequency_over_five_years(tmp_path):
    args = parse_args([
        "--baskets", "SPY,QQQ,SOXX", "--frequency", "weekly", "--years", "5",
        "--dry-run", "--as-of", "2026-07-30", "--db", str(tmp_path / "x.db"),
    ])
    assert args.baskets == ["SPY", "QQQ", "SOXX"]
    assert args.frequency == "weekly"
    assert args.dry_run is True
    assert args.allow_network is False
    assert resolve_window(args) == ("2021-07-30", "2026-07-30")


def test_cli_rejects_an_unconfigured_basket(tmp_path):
    with pytest.raises(SystemExit):
        parse_args(["--baskets", "SPY,IWM", "--dry-run",
                    "--db", str(tmp_path / "x.db")])


def test_cli_rejects_a_non_weekly_frequency(tmp_path):
    with pytest.raises(SystemExit):
        parse_args(["--baskets", "SPY", "--frequency", "daily", "--dry-run",
                    "--db", str(tmp_path / "x.db")])


# ---------------------------------------------------------------------------
# RED 3/4: weekly sampling
# ---------------------------------------------------------------------------

def test_trading_dates_group_into_iso_weeks_inside_the_window():
    weeks = group_trading_dates_by_week(
        TRADING_DATES, "2026-01-02", "2026-01-16")
    assert weeks[0] == ["2026-01-02"]
    assert weeks[1] == ["2026-01-05", "2026-01-06", "2026-01-07",
                        "2026-01-08", "2026-01-09"]
    assert weeks[-1][-1] == "2026-01-16"
    assert all(day >= "2026-01-02" for week in weeks for day in week)


def test_weekly_selection_takes_the_last_publishable_day_of_the_week():
    published = {"2026-01-05", "2026-01-06", "2026-01-07"}

    def compute(day):
        return {"valuation_date": day,
                "ttm_pe_gaap": 20.0 if day in published else None,
                "hindsight_ntm_pe_gaap": None}

    rows = select_weekly_rows(
        [["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08",
          "2026-01-09"]], compute)
    assert [row["valuation_date"] for row in rows] == ["2026-01-07"]


def test_weekly_selection_stops_early_once_a_day_publishes_both_lines():
    seen = []

    def compute(day):
        seen.append(day)
        return {"valuation_date": day, "ttm_pe_gaap": 20.0,
                "hindsight_ntm_pe_gaap": 18.0}

    rows = select_weekly_rows(
        [["2026-01-05", "2026-01-06", "2026-01-09"]], compute)
    assert [row["valuation_date"] for row in rows] == ["2026-01-09"]
    assert seen == ["2026-01-09"], "must not evaluate the rest of the week"


def test_a_week_with_no_publishable_day_keeps_a_null_row_not_a_carry_forward():
    def compute(day):
        return {"valuation_date": day, "ttm_pe_gaap": None,
                "hindsight_ntm_pe_gaap": None}

    rows = select_weekly_rows(
        [["2026-01-05", "2026-01-06"], ["2026-01-12", "2026-01-13"]], compute)
    assert [row["valuation_date"] for row in rows] == [
        "2026-01-06", "2026-01-13"]
    assert all(row["ttm_pe_gaap"] is None for row in rows)


def test_a_week_with_no_composition_at_all_produces_no_row():
    def compute(day):
        return None

    assert select_weekly_rows([["2026-01-05", "2026-01-06"]], compute) == []


# ---------------------------------------------------------------------------
# RED 12: the R1 caliber
# ---------------------------------------------------------------------------

def test_weekly_ttm_is_the_aggregate_caliber_not_the_holding_weighted_proxy():
    sources = _sources()
    # AAA is 10x the market cap of BBB but carries the smaller disclosure
    # weight, so a holding-weighted proxy and the aggregate cannot agree.
    sources["market_cap_by_symbol"]["AAA"] = [
        {"symbol": "AAA", "date": day, "market_cap": 10000.0}
        for day in TRADING_DATES]
    row = _point(sources=sources)
    assert row["ttm_pe_gaap"] == pytest.approx(11000.0 / 200.0)
    assert "rebalance_weighted_ttm_pe_gaap_proxy" not in row
    assert "uncapped_mcap_basket_pe_gaap" not in row
    assert "weighted_earnings_yield" not in row


def test_ttm_gate_applies_to_the_aggregate_metric_itself():
    sources = _sources()
    # BBB holds 10.01% of the observable market cap and has no usable income,
    # so aggregate mcap coverage is 89.99% -- just under the gate. Its
    # disclosure weight is small enough that the weight gate still passes, so
    # the mcap gate is the only thing under test.
    sources["market_cap_by_symbol"]["AAA"] = [
        {"symbol": "AAA", "date": day, "market_cap": 8999.0}
        for day in TRADING_DATES]
    sources["market_cap_by_symbol"]["BBB"] = [
        {"symbol": "BBB", "date": day, "market_cap": 1001.0}
        for day in TRADING_DATES]
    sources["income_by_symbol"]["BBB"] = []
    row = _point(holding_rows=[_holding("AAA", 95.0, 0), _holding("BBB", 5.0, 1)],
                 sources=sources)
    assert row["mcap_coverage_ttm"] == pytest.approx(0.8999)
    assert row["ttm_pe_gaap"] is None
    assert any("mcap_coverage_ttm_below_gate" in warning
               for warning in row["warnings_json"])


def test_ttm_publishes_at_exactly_ninety_percent_coverage():
    sources = _sources()
    sources["market_cap_by_symbol"]["AAA"] = [
        {"symbol": "AAA", "date": day, "market_cap": 9000.0}
        for day in TRADING_DATES]
    sources["market_cap_by_symbol"]["BBB"] = [
        {"symbol": "BBB", "date": day, "market_cap": 1000.0}
        for day in TRADING_DATES]
    sources["income_by_symbol"]["BBB"] = []
    row = _point(holding_rows=[_holding("AAA", 95.0, 0), _holding("BBB", 5.0, 1)],
                 sources=sources)
    assert row["mcap_coverage_ttm"] == pytest.approx(0.90)
    assert row["ttm_pe_gaap"] == pytest.approx(9000.0 / 100.0)


def test_quarantining_a_heavy_member_blocks_publication_via_the_weight_gate():
    """The mcap gate cannot see a member that has no market cap at all.

    AAA is 60% of the disclosed weight. Quarantine its market cap and the
    remaining 40% of the basket divides cleanly -- 100% mcap coverage of a
    minority of the index. Disclosure weight is the measure that exposes it.
    """
    sources = _sources()
    sources["sanity_by_symbol"]["AAA"] = [
        {"symbol": "AAA", "date": day, "status": "invalid_mcap",
         "candidate": True}
        for day in TRADING_DATES]
    row = _point(sources=sources)
    assert row["mcap_coverage_ttm"] == pytest.approx(1.0)
    assert row["ttm_pe_gaap"] is None
    assert row["hindsight_ntm_pe_gaap"] is None
    assert row["quality_tier"] == "unpublishable"
    assert any("weight_coverage_ttm_below_gate" in warning
               for warning in row["warnings_json"])
    assert any("weight_coverage_hindsight_below_gate" in warning
               for warning in row["warnings_json"])


def test_non_positive_aggregate_earnings_are_not_published():
    sources = _sources()
    sources["income_by_symbol"]["AAA"] = _quarters(
        "AAA", TTM_FISCAL + NTM_FISCAL, -30.0)
    row = _point(sources=sources)
    assert row["ttm_pe_gaap"] is None
    assert any("ttm_net_income_not_positive" in warning
               for warning in row["warnings_json"])


def test_weekly_row_carries_both_weight_coverages_for_the_verifier():
    row = _point()
    members = json.loads(json.dumps(row["members_json"]))
    assert members["weight_coverage_ttm"] == pytest.approx(1.0)
    assert members["weight_coverage_hindsight"] == pytest.approx(1.0)
    # The store's column filter drops these top-level keys, so the consensus
    # vintage has to survive inside members_json.
    assert "consensus_snapshot_date" in members
    assert members["is_ex_post"] == 1


# ---------------------------------------------------------------------------
# share classes merged upstream of the engines
# ---------------------------------------------------------------------------

def test_identical_market_cap_share_classes_are_counted_once():
    """GOOGL/GOOG both carry the full-company figure; summing doubles it."""
    holdings = [_holding("GOOGL", 60.0, 0), _holding("GOOG", 0.0, 1,
                                                     covered_by="GOOGL"),
                _holding("BBB", 40.0, 2)]
    sources = _sources(members=("GOOGL", "GOOG", "BBB"))
    row = _point(holding_rows=holdings, sources=sources)
    assert row["ttm_total_mcap"] == pytest.approx(2000.0)
    assert row["n_members"] == 2
    assert row["ttm_pe_gaap"] == pytest.approx(2000.0 / 200.0)


def test_split_market_cap_share_classes_are_summed_into_one_company():
    """Synthetic split convention; not a claim about vendor FOXA/FOX data."""
    holdings = [_holding("FOXA", 60.0, 0), _holding("FOX", 0.0, 1,
                                                    covered_by="FOXA"),
                _holding("BBB", 40.0, 2)]
    sources = _sources(members=("FOXA", "FOX", "BBB"))
    sources["market_cap_by_symbol"]["FOX"] = [
        {"symbol": "FOX", "date": day, "market_cap": 400.0}
        for day in TRADING_DATES]
    row = _point(holding_rows=holdings, sources=sources,
                 share_class_conventions={"FOXA": "split_across_classes"})
    assert row["ttm_total_mcap"] == pytest.approx(1000.0 + 400.0 + 1000.0)
    assert row["n_members"] == 2


def test_split_convention_fails_closed_when_a_class_market_cap_is_missing():
    holdings = [_holding("FOXA", 60.0, 0), _holding("FOX", 0.0, 1,
                                                    covered_by="FOXA"),
                _holding("BBB", 40.0, 2)]
    sources = _sources(members=("FOXA", "BBB"))
    row = _point(holding_rows=holdings, sources=sources,
                 share_class_conventions={"FOXA": "split_across_classes"})
    assert row["ttm_total_mcap"] == pytest.approx(1000.0)
    assert any("share_class_market_cap_incomplete" in warning
               for warning in row["warnings_json"])


def test_undeclared_share_class_convention_excludes_the_company():
    resolved = resolve_company_market_cap(
        primary="AAA", base_market_cap=1000.0, secondaries=["AAA.B"],
        convention=None, valuation_date="2026-01-09",
        market_cap_by_symbol={"AAA.B": [
            {"symbol": "AAA.B", "date": "2026-01-09", "market_cap": 500.0}]},
        sanity_by_symbol={})
    assert resolved["market_cap"] is None
    assert resolved["exclusion_reason"] == "share_class_convention_undeclared"


def test_full_company_convention_conflict_excludes_the_company():
    """Boss review P1-5: a contradicted convention must not publish anyway.

    The config says both classes quote the whole company, and the data says
    they do not. One of the two is wrong and neither is safe: taking the
    primary halves the company if the classes really are split. Warning while
    still publishing puts a number nobody can defend on the chart.
    """
    resolved = resolve_company_market_cap(
        primary="AAA", base_market_cap=1000.0, secondaries=["AAA.B"],
        convention="full_company_per_class", valuation_date="2026-01-09",
        market_cap_by_symbol={"AAA.B": [
            {"symbol": "AAA.B", "date": "2026-01-09", "market_cap": 400.0}]},
        sanity_by_symbol={"AAA.B": [
            {"symbol": "AAA.B", "date": "2026-01-09", "status": "clean"}]})
    assert resolved["market_cap"] is None
    assert resolved["exclusion_reason"] == "share_class_convention_conflict"
    assert any("share_class_convention_mismatch" in warning
               for warning in resolved["warnings"])


def test_split_convention_conflict_excludes_the_company():
    """The mirror image: config says split, the two classes quote the same."""
    resolved = resolve_company_market_cap(
        primary="FOXA", base_market_cap=1000.0, secondaries=["FOX"],
        convention="split_across_classes", valuation_date="2026-01-09",
        market_cap_by_symbol={"FOX": [
            {"symbol": "FOX", "date": "2026-01-09", "market_cap": 1000.0}]},
        sanity_by_symbol={"FOX": [
            {"symbol": "FOX", "date": "2026-01-09", "status": "clean"}]})
    assert resolved["market_cap"] is None
    assert resolved["exclusion_reason"] == "share_class_convention_conflict"


def test_a_conflicted_share_class_company_leaves_the_metric_entirely():
    holdings = [_holding("FOXA", 60.0, 0), _holding("FOX", 0.0, 1,
                                                    covered_by="FOXA"),
                _holding("BBB", 40.0, 2)]
    sources = _sources(members=("FOXA", "FOX", "BBB"))
    row = _point(holding_rows=holdings, sources=sources,
                 share_class_conventions={"FOXA": "split_across_classes"})
    assert row["ttm_total_mcap"] == pytest.approx(1000.0)
    assert row["hindsight_total_mcap"] == pytest.approx(1000.0)
    assert any("share_class_convention_mismatch" in warning
               for warning in row["warnings_json"])


# ---------------------------------------------------------------------------
# RED 13: one aggregation kernel for producer and verifier
# ---------------------------------------------------------------------------

def test_producer_and_verifier_aggregate_through_the_same_function():
    from terminal.basket_pe_aggregate import compute_aggregate_basket_pe
    import terminal.index_pe_weekly as weekly

    assert weekly.compute_aggregate_basket_pe is compute_aggregate_basket_pe
    assert verifier.compute_aggregate_basket_pe is compute_aggregate_basket_pe
    assert (weekly.MINIMUM_MCAP_COVERAGE
            is verifier.MINIMUM_MCAP_COVERAGE)


# ---------------------------------------------------------------------------
# orchestration: manifest ordering, fuses, dry-run and transactions
# ---------------------------------------------------------------------------

REPO_CONFIG_DIR = Path(__file__).parent.parent / "config" / "baskets"


@pytest.fixture
def config_dir(tmp_path):
    """A copy of the repo basket config with this fixture's member bounds.

    The snapshot-plausibility bounds are per-basket production numbers (SPY
    480-520 holdings); a two-member fixture has to declare its own rather than
    weaken the real ones.
    """
    target = tmp_path / "baskets"
    shutil.copytree(REPO_CONFIG_DIR, target)
    # This synthetic two-member universe has no production security anchors.
    (target / 'security_source_corrections.json').write_text('[]')
    shutil.copy(REPO_CONFIG_DIR.parent / "soxx_symbol_aliases.json", target)
    payload = json.loads((target / "index_pe_baskets.json").read_text())
    for entry in payload.values():
        entry["snapshot_quality"] = {
            "minimum_members": 1, "maximum_members": 5,
            "minimum_weight": 99.0, "maximum_weight": 101.0,
        }
    (target / "index_pe_baskets.json").write_text(json.dumps(payload))
    return target


def _insert_live_snapshot(store, basket):
    """A current live-tail snapshot, so the source stage has nothing to fetch."""
    close = infer_basket_rebalance_close(date.today().isoformat())
    conn = store._get_conn()
    with conn:
        for index, (symbol, weight) in enumerate((("AAA", 60.0), ("BBB", 40.0))):
            conn.execute(
                "INSERT OR REPLACE INTO fmp_fund_disclosure_holdings "
                "(basket_symbol, holding_date, source_kind, raw_row_index, "
                "rebalance_close_date, composition_effective_date, "
                "composition_available_date, raw_symbol, symbol, name, "
                "weight_pct, market_value, included, filter_reason, "
                "covered_by, snapshot_warnings_json, fetched_at, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [basket, date.today().isoformat(), "live", index, close,
                 "2026-07-01", "2026-07-01", symbol, symbol, symbol,
                 weight, weight * 10, 1, None, None, "[]",
                 "2026-07-30T00:00:00Z", "2026-07-30T00:00:00Z"])


def _fixture_db(tmp_path, baskets=("SPY",)):
    store = MarketStore(tmp_path / "market.db")
    conn = store._get_conn()
    with conn:
        for basket in baskets:
            conn.executemany(
                "INSERT OR REPLACE INTO daily_price "
                "(symbol, date, open, high, low, close, volume) "
                "VALUES (?,?,?,?,?,?,?)",
                [(basket, day, 1.0, 1.0, 1.0, 1.0, 1) for day in TRADING_DATES])
        for symbol in ("AAA", "BBB"):
            conn.executemany(
                "INSERT OR REPLACE INTO daily_price "
                "(symbol, date, open, high, low, close, volume) "
                "VALUES (?,?,?,?,?,?,?)",
                [(symbol, day, 10.0, 10.0, 10.0, 10.0, 1)
                 for day in TRADING_DATES])
            conn.executemany(
                "INSERT OR REPLACE INTO historical_market_cap "
                "(symbol, date, market_cap) VALUES (?,?,?)",
                [(symbol, day, 1000.0) for day in TRADING_DATES])
            for row in _quarters(symbol, TTM_FISCAL + NTM_FISCAL, 25.0):
                conn.execute(
                    "INSERT OR REPLACE INTO income_quarterly "
                    "(symbol, date, period, accepted_date, reported_currency, "
                    "net_income) VALUES (?,?,?,?,?,?)",
                    [row["symbol"], row["date"], row["period"],
                     row["accepted_date"], row["reported_currency"],
                     row["net_income"]])
        for basket in baskets:
            for index, (symbol, weight) in enumerate(
                    (("AAA", 60.0), ("BBB", 40.0))):
                conn.execute(
                    "INSERT OR REPLACE INTO fmp_fund_disclosure_holdings "
                    "(basket_symbol, holding_date, source_kind, raw_row_index, "
                    "rebalance_close_date, composition_effective_date, "
                    "composition_available_date, raw_symbol, symbol, name, "
                    "weight_pct, market_value, included, filter_reason, "
                    "covered_by, snapshot_warnings_json, fetched_at, "
                    "created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    [basket, "2025-12-31", "disclosure", index, "2025-12-19",
                     "2025-12-22", "2026-01-02", symbol, symbol, symbol,
                     weight, weight * 10, 1, None, None, "[]",
                     "2026-01-20T00:00:00Z", "2026-01-20T00:00:00Z"])
        seed_source_identity(conn)
    return store


def _args(tmp_path, config_dir, **overrides):
    values = {
        "baskets": ["SPY"], "frequency": "weekly", "years": 5,
        "as_of": "2026-01-16", "dry_run": True, "allow_network": False,
        "db": tmp_path / "market.db", "refresh_live": False,
        "run_id": "run-test", "config_dir": config_dir,
    }
    values.update(overrides)
    return Namespace(**values)


def test_manifest_lands_before_the_first_per_symbol_remote_call(
        tmp_path, config_dir):
    store = _fixture_db(tmp_path)
    _insert_live_snapshot(store, "SPY")
    calls = []
    client = Mock()
    # The disclosure the fixture already holds, so the source stage fetches
    # nothing per symbol and the ordering under test is the real one.
    client.get_fund_disclosure_dates.side_effect = (
        lambda *a, **k: calls.append("disclosure_dates") or [
            {"date": "2025-12-31", "year": 2025, "quarter": 4}])
    client.get_income_statement.side_effect = (
        lambda symbol, **k: calls.append("income") or _quarters(
            symbol, TTM_FISCAL + NTM_FISCAL, 25.0))
    client.get_historical_market_cap.side_effect = (
        lambda symbol, **k: calls.append("mcap") or [
            {"symbol": symbol, "date": day, "market_cap": 1000.0}
            for day in TRADING_DATES])
    client.get_stock_splits.side_effect = lambda *a, **k: calls.append("splits") or []
    recording = Mock(wraps=store)
    recording.append_basket_pe_run_events.side_effect = (
        lambda rows: calls.append("manifest") or store.append_basket_pe_run_events(rows))
    args = _args(tmp_path, config_dir, dry_run=False, allow_network=True)
    backfill_basket(args, "SPY", client=client, store=recording,
                    conn=store._get_conn())
    assert "manifest" in calls, "no manifest event was ever written"
    per_symbol = [index for index, name in enumerate(calls)
                  if name in {"income", "mcap", "splits"}]
    assert per_symbol, "expected the per-symbol stages to reach the client"
    assert calls.index("manifest") < per_symbol[0]
    assert calls.index("disclosure_dates") < calls.index("manifest"), (
        "the manifest freezes the universe the disclosure fetch just defined")
    store.close()


def test_dry_run_writes_no_valuation_rows(tmp_path, config_dir):
    store = _fixture_db(tmp_path)
    args = _args(tmp_path, config_dir)
    result = backfill_basket(args, "SPY", client=None, store=None,
                             conn=store._get_conn())
    assert result["weekly_rows"] > 0
    assert store.get_basket_weekly_pe_history("SPY") == []
    assert store.get_basket_pe_run_events(basket="SPY") == []
    assert result["planned_network_calls"] == [] or all(
        "symbol" in call or "stage" in call
        for call in result["planned_network_calls"])
    store.close()


def test_write_mode_persists_one_row_per_week_and_a_manifest(tmp_path, config_dir):
    store = _fixture_db(tmp_path)
    args = _args(tmp_path, config_dir, dry_run=False)
    result = backfill_basket(args, "SPY", client=None, store=store,
                             conn=store._get_conn())
    rows = store.get_basket_weekly_pe_history("SPY")
    assert len(rows) == result["weekly_rows"]
    assert rows[-1]["valuation_date"] == "2026-01-16"
    assert rows[0]["methodology_version"] == WEEKLY_METHODOLOGY_VERSION
    events = store.get_basket_pe_run_events(basket="SPY")
    assert [row["event_kind"] for row in events][0] == "run_started"
    assert events[-1]["event_kind"] == "run_completed"
    assert events[0]["expected_from_date"] == "2021-01-16"
    assert events[0]["expected_to_date"] == "2026-01-16"
    store.close()


def test_all_baskets_producer_to_verifier_with_same_window_rerun(
        tmp_path, config_dir):
    """Persist real producer evidence, then independently certify each basket.

    The same-window retry is in scope; sliding/pruning remains C1.
    """
    baskets = ("SPY", "QQQ", "SOXX")
    store = _fixture_db(tmp_path, baskets=baskets)
    with store._get_conn() as conn:
        conn.execute("UPDATE fmp_fund_disclosure_holdings SET cik = "
                     "CASE symbol WHEN 'AAA' THEN '0000000001' "
                     "ELSE '0000000002' END")
    try:
        for run_id in ("first", "retry"):
            args = _args(tmp_path, config_dir, dry_run=False, run_id=run_id)
            for basket in baskets:
                backfill_basket(args, basket, client=None, store=store,
                                conn=store._get_conn())
            readonly = verifier.connect_readonly(store.db_path)
            try:
                result = verifier.verify_database(
                    readonly, baskets, args.as_of, args.years, 50, config_dir)
            finally:
                readonly.close()
            assert result["passed"], result["checks"]
            for basket in baskets:
                summary = result["baskets"][basket]
                assert summary["rows"] == 3  # Jan 2, Jan 9, Jan 16
                assert summary["raw_source_reconciliation"][
                    "reconciled_hindsight_incomes"] == 6
    finally:
        store.close()


def test_five_year_window_slides_a_week_without_orphaned_ownership(
        tmp_path, config_dir):
    from datetime import timedelta
    store = _fixture_db(tmp_path)
    days = []
    day = date(2020, 12, 31)
    while day <= date(2026, 1, 23):
        if day.weekday() < 5:
            days.append(day.isoformat())
        day += timedelta(days=1)
    quarters = [f"{year}-{ending}" for year in range(2020, 2028)
                for ending in ("03-31", "06-30", "09-30", "12-31")]
    with store._get_conn() as conn:
        conn.execute("UPDATE fmp_fund_disclosure_holdings SET "
                     "holding_date='2020-12-31', composition_effective_date='2021-01-04', "
                     "composition_available_date='2021-01-05'")
        for symbol in ("SPY", "AAA", "BBB"):
            conn.executemany("INSERT OR REPLACE INTO daily_price "
                             "(symbol,date,close) VALUES (?,?,?)",
                             [(symbol, d, 10.0) for d in days])
        for symbol in ("AAA", "BBB"):
            conn.executemany("INSERT OR REPLACE INTO historical_market_cap "
                             "(symbol,date,market_cap) VALUES (?,?,?)",
                             [(symbol, d, 1000.0) for d in days])
            conn.execute("DELETE FROM income_quarterly WHERE symbol=?", [symbol])
            conn.executemany("INSERT INTO income_quarterly "
                             "(symbol,date,period,accepted_date,reported_currency,net_income) "
                             "VALUES (?,?,?,?,?,?)",
                             [(symbol, d, f"Q{int(d[5:7]) // 3}",
                               _accepted_for(d), "USD", 25.0) for d in quarters])
    try:
        first = backfill_basket(
            _args(tmp_path, config_dir, dry_run=False, run_id="week-1"),
            "SPY", store=store, conn=store._get_conn())
        assert first["verification"]["passed"]
        assert store.get_basket_weekly_pe_history("SPY")[0]["valuation_date"] == "2021-01-22"
        second = backfill_basket(
            _args(tmp_path, config_dir, dry_run=False, run_id="week-2",
                  as_of="2026-01-23"),
            "SPY", store=store, conn=store._get_conn())
        rows = store.get_basket_weekly_pe_history("SPY")
        assert second["verification"]["passed"]
        assert rows[0]["valuation_date"] == "2021-01-29"
        assert rows[-1]["valuation_date"] == "2026-01-23"
        assert {r["run_id"] for r in rows} == {"week-2"}
        readonly = verifier.connect_readonly(store.db_path)
        try:
            result = verifier.verify_database(
                readonly, ["SPY"], "2026-01-23", 5, 50, config_dir)
            assert result["passed"], result["checks"]
        finally:
            readonly.close()
    finally:
        store.close()


def test_failed_candidate_keeps_old_product_and_next_run_recovers(
        tmp_path, config_dir, monkeypatch):
    store = _fixture_db(tmp_path)
    try:
        backfill_basket(_args(tmp_path, config_dir, dry_run=False, run_id="good"),
                        "SPY", store=store, conn=store._get_conn())
        before = store.get_basket_weekly_pe_history("SPY")
        with monkeypatch.context() as patch:
            patch.setattr(verifier, "verify_database", lambda *a, **kw: {
                "passed": False, "checks": [{"passed": False, "name": "injected"}]})
            with pytest.raises(ValueError, match="certification") as error:
                backfill_basket(
                    _args(tmp_path, config_dir, dry_run=False, run_id="bad"),
                    "SPY", store=store, conn=store._get_conn())
        assert error.value.backfill_report["verification"]["passed"] is False
        assert store.get_basket_weekly_pe_history("SPY") == before
        failed_events = store.get_basket_pe_run_events(basket="SPY", run_id="bad")
        assert [r["event_kind"] for r in failed_events] == ["run_started", "run_failed"]
        assert json.loads(failed_events[-1]["payload_json"])["rows_written"] is False
        retry = backfill_basket(
            _args(tmp_path, config_dir, dry_run=False, run_id="retry"),
            "SPY", store=store, conn=store._get_conn())
        assert retry["verification"]["passed"]
        assert {r["run_id"] for r in store.get_basket_weekly_pe_history("SPY")} == {"retry"}
    finally:
        store.close()


def test_http_budget_cli_is_explicit_and_positive():
    args=backfill.parse_args(['--max-api-requests','3000'])
    assert args.max_api_requests==3000
    with pytest.raises(SystemExit) as error:
        backfill.parse_args(['--max-api-requests','0'])
    assert error.value.code==2


def test_legacy_pe_schema_fails_preflight_before_network(tmp_path, config_dir):
    store = _fixture_db(tmp_path)
    try:
        with store._get_conn() as conn:
            conn.execute("ALTER TABLE basket_weekly_pe_history RENAME COLUMN run_id TO legacy_run_id")
        client = Mock()
        with pytest.raises(RuntimeError, match="run_id") as error:
            backfill_basket(
                _args(tmp_path, config_dir, dry_run=False, allow_network=True),
                "SPY", client=client, store=store, conn=store._get_conn())
        assert client.mock_calls == []
        assert error.value.backfill_report["status"] == "failed"
        assert [e["event_kind"] for e in error.value.backfill_report["manifest"]] == [
            "run_started", "run_failed"]
    finally:
        store.close()


@pytest.mark.parametrize("phase", ["preflight_start", "preflight_terminal", "started", "commit"])
def test_failure_manifest_error_preserves_original_exception_and_report(
        tmp_path, config_dir, monkeypatch, caplog, phase):
    store = _fixture_db(tmp_path)
    primary = ValueError("primary pipeline failure")
    secondary = OSError("failure manifest unavailable")
    original_append = store.append_basket_pe_run_events
    attempts = []

    def fail_primary(*args, **kwargs):
        raise primary

    def append(events):
        attempts.append(events[0]["event_kind"])
        if phase == "preflight_start":
            raise secondary
        if phase == "started":
            raise primary if len(attempts) == 1 else secondary
        if events[0]["event_kind"] == "run_failed":
            raise secondary
        return original_append(events)

    monkeypatch.setattr(store, "append_basket_pe_run_events", append)
    if phase.startswith("preflight"):
        monkeypatch.setattr(backfill, "load_state", fail_primary)
    elif phase == "commit":
        monkeypatch.setattr(store, "commit_basket_weekly_pe_window", fail_primary)
    try:
        with pytest.raises(ValueError) as error:
            backfill_basket(_args(tmp_path, config_dir, dry_run=False), "SPY",
                            store=store, conn=store._get_conn())
        assert error.value is primary
        report = error.value.backfill_report
        assert report["error"] == "ValueError: primary pipeline failure"
        assert report["status"] == "failed"
        assert report["from_date"] == "2021-01-16"
        assert report["manifest_persist_error"] == "OSError: failure manifest unavailable"
        assert "failure manifest unavailable" in caplog.text
        if phase == "commit":
            assert report["stages"]["fundamentals"]
        expected_events = [] if phase in ("preflight_start", "started") else ["run_started"]
        assert [e["event_kind"] for e in report["manifest"]] == expected_events
    finally:
        store.close()


def test_run_report_retains_diagnostics_when_failure_manifest_cannot_be_written(
        tmp_path, config_dir, monkeypatch):
    store = _fixture_db(tmp_path)

    def fail_load(*args, **kwargs):
        raise ValueError("primary source read failed")

    def fail_append(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(backfill, "load_state", fail_load)
    monkeypatch.setattr(store, "append_basket_pe_run_events", fail_append)
    try:
        report = backfill.run_backfill(
            _args(tmp_path, config_dir, baskets=["SPY"], dry_run=False),
            store=store, conn=store._get_conn())
        assert report["failed_baskets"] == ["SPY"]
        failed = report["baskets"]["SPY"]
        assert failed["error"] == "ValueError: primary source read failed"
        assert failed["manifest_persist_error"] == "OperationalError: database is locked"
        assert failed["from_date"] == "2021-01-16"
        assert "stages" in failed
        assert failed["manifest"] == []
        json.dumps(report)  # Same serialization as the CLI's failure output.
    finally:
        store.close()


def test_window_universe_keeps_public_predecessor_and_discards_unused_archive():
    rows=[
        {'holding_date':'2020-12-31','source_kind':'disclosure','composition_effective_date':'2020-12-21','composition_available_date':'2021-02-01'},
        {'holding_date':'2025-09-30','source_kind':'disclosure','composition_effective_date':'2025-09-22','composition_available_date':'2025-11-01'},
        {'holding_date':'2025-12-31','source_kind':'disclosure','composition_effective_date':'2025-12-22','composition_available_date':'2026-02-01'},
        {'holding_date':'2026-01-12','source_kind':'live','composition_effective_date':'2026-01-12','composition_available_date':'2026-01-13'},
    ]
    got=backfill._window_snapshots(rows,('2026-01-01','2026-01-16'))
    assert [r['holding_date'] for r in got]==['2025-09-30','2026-01-12']


def test_member_failure_above_twenty_percent_publishes_nothing(tmp_path, config_dir):
    store = _fixture_db(tmp_path)
    _insert_live_snapshot(store, "SPY")
    client = Mock()
    client.get_fund_disclosure_dates.return_value = [
        {"date": "2025-12-31", "year": 2025, "quarter": 4}]
    client.get_income_statement.return_value = []
    client.get_historical_market_cap.return_value = []
    client.get_stock_splits.return_value = []
    conn = store._get_conn()
    # Both members lose their income history, so the fundamentals stage cannot
    # complete for 100% of the universe.
    with conn:
        conn.execute("DELETE FROM income_quarterly")
    args = _args(tmp_path, config_dir, dry_run=False, allow_network=True)
    with pytest.raises(RuntimeError, match="fuse"):
        backfill_basket(args, "SPY", client=client, store=store, conn=conn)
    assert store.get_basket_weekly_pe_history("SPY") == []
    store.close()


def test_one_basket_failing_leaves_the_others_committed(tmp_path, config_dir):
    store = _fixture_db(tmp_path, baskets=("SPY", "QQQ"))
    conn = store._get_conn()
    with conn:
        # QQQ keeps a calendar but loses its composition entirely.
        conn.execute(
            "DELETE FROM fmp_fund_disclosure_holdings WHERE basket_symbol='QQQ'")
    args = _args(tmp_path, config_dir, baskets=["SPY", "QQQ"], dry_run=False)
    report = backfill.run_backfill(args, client=None, store=store, conn=conn)
    assert report["baskets"]["SPY"]["status"] == "complete"
    assert report["baskets"]["QQQ"]["status"] == "failed"
    assert store.get_basket_weekly_pe_history("SPY")
    assert store.get_basket_weekly_pe_history("QQQ") == []
    failed = [row for row in store.get_basket_pe_run_events(basket="QQQ")
              if row["event_kind"] == "run_failed"]
    assert failed, "a failed basket still owes the manifest an explanation"
    # A failed basket keeps its diagnostics: which stages ran, what the
    # manifest recorded, which window it was working on. Replacing that with a
    # four-key stub leaves an operator nothing to debug from.
    diagnostics = report["baskets"]["QQQ"]
    assert "ValueError" in diagnostics["error"]
    assert [event["event_kind"] for event in diagnostics["manifest"]] == [
        "run_started", "run_failed"]
    assert diagnostics["manifest"][0]["payload_json"]["preflight_failed"] is True
    assert diagnostics["from_date"] == "2021-01-16"
    assert diagnostics["to_date"] == "2026-01-16"
    assert "stages" in diagnostics
    store.close()

    # Restoring the source must permit a new valid run. A failure-only event
    # would otherwise remain run_never_started and poison all future retries.
    restored = _fixture_db(tmp_path, baskets=("SPY", "QQQ"))
    try:
        retry = backfill_basket(
            _args(tmp_path, config_dir, dry_run=False, run_id="restored"),
            "QQQ", store=restored, conn=restored._get_conn())
        assert retry["verification"]["passed"]
    finally:
        restored.close()


def test_rerun_rescans_jump_sanity_even_with_complete_market_cap_rows(tmp_path, config_dir):
    """issue035: a complete market-cap range is not evidence of a clean one."""
    store = _fixture_db(tmp_path)
    conn = store._get_conn()
    with conn:
        # A KLAC-shaped 10:1 cliff inside an otherwise complete AAA history.
        for day in ("2026-01-12", "2026-01-13", "2026-01-14", "2026-01-15",
                    "2026-01-16"):
            conn.execute(
                "UPDATE historical_market_cap SET market_cap = 100.0 "
                "WHERE symbol = 'AAA' AND date = ?", [day])
    args = _args(tmp_path, config_dir)
    result = backfill_basket(args, "SPY", client=None, store=None, conn=conn)
    flagged = result["stages"]["mcap_sanity"]["AAA"]
    assert flagged["flagged"] > 0
    assert flagged["quarantined"], "the polluted window must be quarantined"
    contaminated = [row for row in result["rows"]
                    if row["valuation_date"] >= "2026-01-12"]
    assert contaminated, "the contaminated week still owes a row"
    assert all(row["ttm_pe_gaap"] is None for row in contaminated), (
        "a quarantined market cap must not reach a published PE")
    assert all(row["hindsight_ntm_pe_gaap"] is None for row in contaminated)
    # An earlier, clean week is unaffected: the quarantine is not global.
    clean = [row for row in result["rows"] if row["valuation_date"] < "2026-01-12"]
    assert clean and all(row["ttm_pe_gaap"] is not None for row in clean)
    store.close()


def test_contaminated_window_is_replanned_for_refresh_in_dry_run(tmp_path, config_dir):
    store = _fixture_db(tmp_path)
    conn = store._get_conn()
    with conn:
        for day in ("2026-01-13", "2026-01-14", "2026-01-15", "2026-01-16"):
            conn.execute(
                "UPDATE historical_market_cap SET market_cap = 100.0 "
                "WHERE symbol = 'AAA' AND date = ?", [day])
    args = _args(tmp_path, config_dir)
    result = backfill_basket(args, "SPY", client=None, store=None, conn=conn)
    planned = [call for call in result["planned_network_calls"]
               if call.get("stage") == "mcap_sanity" and call.get("symbol") == "AAA"]
    assert planned, "a quarantined window must be planned for a forced refresh"
    store.close()


def test_manifest_records_forced_refresh_row_hashes(tmp_path, config_dir):
    store = _fixture_db(tmp_path)
    conn = store._get_conn()
    with conn:
        for day in ("2026-01-13", "2026-01-14", "2026-01-15", "2026-01-16"):
            conn.execute(
                "UPDATE historical_market_cap SET market_cap = 100.0 "
                "WHERE symbol = 'AAA' AND date = ?", [day])
    _insert_live_snapshot(store, "SPY")
    client = Mock()
    client.get_fund_disclosure_dates.return_value = [
        {"date": "2025-12-31", "year": 2025, "quarter": 4}]
    client.get_income_statement.side_effect = (
        lambda symbol, **k: _quarters(symbol, TTM_FISCAL + NTM_FISCAL, 25.0))
    client.get_historical_market_cap.side_effect = (
        lambda symbol, from_date=None, to_date=None: [
            {"symbol": symbol, "date": day, "market_cap": 1000.0}
            for day in TRADING_DATES
            if (from_date or "") <= day <= (to_date or "9999")])
    client.get_stock_splits.return_value = []
    args = _args(tmp_path, config_dir, dry_run=False, allow_network=True)
    backfill_basket(args, "SPY", client=client, store=store, conn=conn)
    refreshes = [row for row in store.get_basket_pe_run_events(basket="SPY")
                 if row["event_kind"] == "forced_refresh"]
    assert refreshes
    payload = json.loads(refreshes[0]["payload_json"])
    assert payload["symbol"] == "AAA"
    assert len(payload["pre_row_hash"]) == 64
    assert payload["pre_row_hash"] != payload["post_row_hash"]
    store.close()


# ---------------------------------------------------------------------------
# Fix round 1 / Important 2: one config root for both merge layers
# ---------------------------------------------------------------------------

def _disclosure_row(symbol, weight, holding_date="2026-01-05"):
    return {
        "date": holding_date, "acceptedDate": "2026-01-06 16:00:00",
        "symbol": symbol, "title": symbol, "name": symbol,
        "pctVal": weight, "valUsd": weight * 100.0,
        "cik": "0000884394", "assetCat": "EC",
        "lei": synthetic_lei(symbol.split(".")[0]),
    }


def test_both_merge_layers_read_share_classes_from_one_config_root(
        tmp_path, config_dir):
    """Upstream weight merging and the product market-cap merge must agree.

    The disclosure normalizer decides which ticker is a secondary class (whose
    weight folds into the primary); the weekly layer decides how that company's
    market cap is composed. Reading those from two different config roots is
    exactly the double-count / halving the convention mechanism exists to stop.
    """
    payload = json.loads((config_dir / "share_class_groups.json").read_text())
    payload["AAA"] = {"secondaries": ["AAA.B"],
                      "market_cap_convention": "split_across_classes"}
    (config_dir / "share_class_groups.json").write_text(json.dumps(payload))

    store = _fixture_db(tmp_path)
    conn = store._get_conn()
    with conn:
        conn.execute("DELETE FROM fmp_fund_disclosure_holdings")
        conn.executemany(
            "INSERT OR REPLACE INTO daily_price (symbol, date, close) "
            "VALUES (?,?,?)", [("AAA.B", day, 10.0) for day in TRADING_DATES])
        conn.executemany(
            "INSERT OR REPLACE INTO historical_market_cap "
            "(symbol, date, market_cap) VALUES (?,?,?)",
            [("AAA.B", day, 400.0) for day in TRADING_DATES])
    _insert_live_snapshot(store, "SPY")

    client = Mock()
    client.get_fund_disclosure_dates.return_value = [
        {"date": "2019-09-30", "year": 2019, "quarter": 3},
        {"date": "2026-01-05", "year": 2026, "quarter": 1}]
    client.get_fund_disclosure.return_value = [
        _disclosure_row("AAA", 60.0), _disclosure_row("AAA.B", 0.0),
        _disclosure_row("BBB", 40.0)]
    client.get_income_statement.side_effect = (
        lambda symbol, **k: _quarters(symbol, TTM_FISCAL + NTM_FISCAL, 25.0))
    client.get_historical_market_cap.side_effect = (
        lambda symbol, from_date=None, to_date=None: [
            {"symbol": symbol, "date": day, "market_cap": 1000.0}
            for day in TRADING_DATES
            if (from_date or "") <= day <= (to_date or "9999")])
    client.get_stock_splits.return_value = []
    args = _args(tmp_path, config_dir, dry_run=False, allow_network=True)
    result = backfill_basket(args, "SPY", client=client, store=store, conn=conn)

    # Upstream: AAA.B was recognised as a class of AAA, not an unmapped
    # foreign listing, so its weight folded into AAA.
    stored = [dict(row) for row in conn.execute(
        "SELECT raw_symbol, symbol, included, filter_reason, covered_by "
        "FROM fmp_fund_disclosure_holdings WHERE basket_symbol = 'SPY' "
        "AND source_kind = 'disclosure' AND raw_symbol = 'AAA.B'").fetchall()]
    assert stored and stored[0]["covered_by"] == "AAA"
    assert client.get_fund_disclosure.call_count == 1  # catalog predating calendar is not fetched
    assert stored[0]["filter_reason"] == "dual_class_secondary"

    # Product layer: the same group, with the convention this config declares.
    members = {member["symbol"]: member
               for member in result["rows"][-1]["members_json"]["members"]}
    assert members["AAA"]["share_class"] == {
        "convention": "split_across_classes", "components": ["AAA.B"]}
    assert members["AAA"]["market_cap"] == pytest.approx(1400.0)
    assert "AAA.B" in result["share_class_secondary_symbols"]
    store.close()


# ---------------------------------------------------------------------------
# Boss review P1-1: a composition may not be used before it was disclosed
# ---------------------------------------------------------------------------

def _composition(effective, available, holding_date, index=0):
    return {
        "holding_date": holding_date,
        "anchor_trading_date": holding_date,
        "composition_effective_date": effective,
        "composition_available_date": available,
        "weight_basis": "fixed_rebalance_weight_proxy",
        "data_quality_tier": "historical_disclosure_fixed_proxy",
        "snapshot_warnings": [],
        "source_kind": "disclosure",
    }


def test_a_composition_is_not_used_before_its_disclosure_date(
        tmp_path, config_dir):
    """Boss repro: a composition disclosed 2026-02-15 valuing 2025-12-26.

    Effective-date-only selection reaches back to a rebalance whose membership
    and weights nobody could know yet. The weights are only *knowable* from the
    disclosure, so a valuation date before it must keep using the last
    composition that was already public.
    """
    store = _fixture_db(tmp_path)
    conn = store._get_conn()
    with conn:
        conn.execute("DELETE FROM fmp_fund_disclosure_holdings")
        rows = []
        # Old composition: in force and public early in the window.
        for index, (symbol, weight) in enumerate((("AAA", 60.0), ("BBB", 40.0))):
            rows.append(("SPY", "2025-12-30", "disclosure", index, "2025-12-19",
                         "2025-12-22", "2026-01-02", symbol, symbol, symbol,
                         weight, weight * 10, 1, None, None, "[]",
                         "2026-01-02T00:00:00Z", "2026-01-02T00:00:00Z"))
        # New composition: in force inside the window, disclosed two months
        # later -- the shape of Boss's repro.
        for index, (symbol, weight) in enumerate((("AAA", 10.0), ("BBB", 90.0))):
            rows.append(("SPY", "2026-01-13", "disclosure", index + 2,
                         "2026-01-09", "2026-01-12", "2026-02-15", symbol,
                         symbol, symbol, weight, weight * 10, 1, None, None,
                         "[]", "2026-02-15T00:00:00Z", "2026-02-15T00:00:00Z"))
        conn.executemany(
            "INSERT OR REPLACE INTO fmp_fund_disclosure_holdings "
            "(basket_symbol, holding_date, source_kind, raw_row_index, "
            "rebalance_close_date, composition_effective_date, "
            "composition_available_date, raw_symbol, symbol, name, "
            "weight_pct, market_value, included, filter_reason, covered_by, "
            "snapshot_warnings_json, fetched_at, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        seed_source_identity(conn)
    args = _args(tmp_path, config_dir)
    result = backfill_basket(args, "SPY", client=None, store=None, conn=conn)
    for row in result["rows"]:
        assert row["composition_available_date"] <= row["valuation_date"], (
            f"{row['valuation_date']} used a composition disclosed "
            f"{row['composition_available_date']}")
        # Every weekly point is point-in-time by construction, so the daily
        # engine's ex-post composition flag can never be set on one.
        assert row["members_json"]["weight_coverage_ttm"] is not None
    used = {row["valuation_date"]: row["composition_effective_date"]
            for row in result["rows"]}
    assert set(used.values()) == {"2025-12-22"}, used
    # The later composition is in force from 2026-01-12 but only public from
    # 2026-02-15, so no row in this window may have reached for it.
    assert "2026-01-12" not in set(used.values())
    assert min(used) == "2026-01-02", "no row before the first disclosure"
    store.close()


def test_a_week_before_any_disclosure_produces_no_row(tmp_path, config_dir):
    store = _fixture_db(tmp_path)
    conn = store._get_conn()
    with conn:
        conn.execute(
            "UPDATE fmp_fund_disclosure_holdings "
            "SET composition_available_date = '2026-01-13'")
    args = _args(tmp_path, config_dir)
    result = backfill_basket(args, "SPY", client=None, store=None, conn=conn)
    assert [row["valuation_date"] for row in result["rows"]] == ["2026-01-16"]
    store.close()


# ---------------------------------------------------------------------------
# M0 (plan 2026-09-26): scheduled backup label + every backup failure → rc 2
# ---------------------------------------------------------------------------

import scripts.backfill_soxx_historical_pe as soxx_backfill  # noqa: E402
import scripts.build_company_concept_registry as concept_build  # noqa: E402


def _m0_plain_db(path):
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE t (k TEXT)")
    conn.execute("INSERT INTO t VALUES ('x')")
    conn.commit()
    conn.close()
    return path


def _m0_stub_after_backup(monkeypatch, stores):
    """Record MarketStore construction and stop right after it, before any network."""
    def store(path):
        stores.append(Path(path))
        raise RuntimeError("stop after store")
    monkeypatch.setattr(soxx_backfill, "MarketStore", store)


@pytest.mark.parametrize("flags,label,keep", [
    (["--scheduled"], "auto-index-pe-weekly", 1),
    ([], "pre-soxx-historical-pe", None),
])
def test_cli_scheduled_flag_selects_backup_policy(tmp_path, monkeypatch, flags, label, keep):
    db = _m0_plain_db(tmp_path / "market.db")
    calls, stores = [], []
    monkeypatch.setattr(soxx_backfill, "_backup_sqlite",
                        lambda path, lbl, keep=None: calls.append((lbl, keep)) or None)
    _m0_stub_after_backup(monkeypatch, stores)
    assert backfill.main(["--db", str(db), *flags]) == 2
    assert calls == [(label, keep)]
    assert stores == [db]


def _m0_no_space(monkeypatch):
    import collections
    usage = collections.namedtuple("usage", "total used free")
    monkeypatch.setattr(concept_build.shutil, "disk_usage", lambda _p: usage(10, 10, 0))


def _m0_enospc_midway(monkeypatch):
    real_connect = sqlite3.connect

    class _Boom:
        def __init__(self, real):
            self._real = real
        def backup(self, *_a, **_k):
            raise OSError(28, "No space left on device")
        def __getattr__(self, name):
            return getattr(self._real, name)

    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: _Boom(real_connect(*a, **k)))


def _m0_publish_conflict(monkeypatch):
    monkeypatch.setattr(concept_build, "_backup_timestamp", lambda: "20260104000000")
    def link(_src, _dst):
        raise FileExistsError(17, "File exists")
    monkeypatch.setattr(concept_build.os, "link", link)


@pytest.mark.parametrize("inject", [_m0_no_space, _m0_enospc_midway, _m0_publish_conflict])
def test_every_real_backup_failure_returns_2_before_market_store(
        tmp_path, monkeypatch, capsys, inject):
    db = _m0_plain_db(tmp_path / "market.db")
    stores = []
    _m0_stub_after_backup(monkeypatch, stores)
    inject(monkeypatch)
    assert backfill.main(["--db", str(db), "--scheduled"]) == 2
    assert stores == []                               # MarketStore never constructed
    assert "backup" in capsys.readouterr().err
    assert [p.name for p in tmp_path.iterdir() if ".partial" in p.name] == []
