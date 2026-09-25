"""Real endpoint fields, not fabricated per-ticker fund CIKs (issue072)."""
import copy
import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

from src.data.fmp_forward_ingestion import (
    load_soxx_symbol_aliases, normalize_fund_disclosure_snapshot,
)
from src.data.market_store import MarketStore

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = json.loads((ROOT / "tests/fixtures/index_pe_source_identity_20260911.json").read_text())
CALENDAR = [(date(2020, 1, 1) + timedelta(days=i)).isoformat()
            for i in range(2600) if (date(2020, 1, 1) + timedelta(days=i)).weekday() < 5]


def normalize(labels, groups=None, aliases=None):
    first = EVIDENCE[labels[0]]
    rows, meta = normalize_fund_disclosure_snapshot(
        first["basket"], [copy.deepcopy(EVIDENCE[k]["raw"]) for k in labels],
        first["source_kind"], "2026-09-11T07:35:35Z", CALENDAR,
        {}, groups or {}, aliases or {}, expected_reconstitution_month=None)
    return [{**r, **{k: meta[k] for k in (
        "basket_symbol", "holding_date", "source_kind", "composition_effective_date",
        "composition_available_date")}} for r in rows], meta


def test_raw_issuer_fields_survive_adapter():
    rows, _ = normalize(["spy_AAPL"])
    row, raw = rows[0], EVIDENCE["spy_AAPL"]["raw"]
    assert row["cik"] == "0000884394"  # Filer, intentionally NOT Apple's CIK.
    assert row["issuer_lei"] == raw["lei"]
    assert row["asset_category"] == "EC"
    assert row["raw_payload_json"] == raw


def test_live_security_cusip_is_preserved_and_conflict_is_rejected():
    rows, _ = normalize(["live_aapl"])
    raw = EVIDENCE["live_aapl"]["raw"]
    assert rows[0]["cusip"] == raw["securityCusip"]
    assert rows[0]["issuer_lei"] is None
    conflicting = {**raw, "cusip": "000000001"}
    with pytest.raises(ValueError, match="CUSIP"):
        normalize_fund_disclosure_snapshot(
            "QQQ", [conflicting], "live", "2026-09-11T07:35:35Z",
            CALENDAR, {}, {}, {})


@pytest.mark.parametrize("label", ["qqq_nqm6", "qqq_nqu6"])
def test_named_ticker_future_never_enters_equity_universe(label):
    rows, _ = normalize([label])
    assert rows[0]["included"] == 0
    assert rows[0]["covered_by"] is None
    assert rows[0]["filter_reason"] == "futures"
    assert rows[0]["weight_pct"] == EVIDENCE[label]["raw"]["pctVal"]


def test_unknown_disclosure_asset_category_is_not_equity():
    raw = {**EVIDENCE["spy_AAPL"]["raw"], "assetCat": "NEW_CATEGORY"}
    rows, _ = normalize_fund_disclosure_snapshot(
        "SPY", [raw], "disclosure", "2026-09-11T07:35:35Z", CALENDAR, {}, {}, {})
    assert rows[0]["included"] == 0
    assert rows[0]["filter_reason"] == "unrecognized_asset_category"


def test_legacy_source_rows_are_preserved_with_unknown_new_columns(tmp_path):
    path = tmp_path / "market.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE fmp_fund_disclosure_holdings ("
                     "basket_symbol TEXT,holding_date TEXT,source_kind TEXT,raw_row_index INTEGER,"
                     "composition_effective_date TEXT,rebalance_close_date TEXT,cik TEXT)")
        conn.execute("INSERT INTO fmp_fund_disclosure_holdings VALUES "
                     "('SPY','2021-03-31','disclosure',0,'2021-03-22','2021-03-19','0000884394')")
    store = MarketStore(path)
    try:
        row = store._get_conn().execute("SELECT * FROM fmp_fund_disclosure_holdings").fetchone()
        assert row["cik"] == "0000884394"
        assert row["issuer_lei"] is None
        assert row["raw_payload_json"] is None
        store.upsert_daily_prices("AAPL", [{"date": "2026-09-10", "close": 100}])
    finally:
        store.close()


def test_source_identity_fields_round_trip(tmp_path):
    rows, meta = normalize(["spy_AAPL"])
    store = MarketStore(tmp_path / "market.db")
    try:
        store.replace_fund_disclosure_snapshot("SPY", meta["holding_date"], "disclosure", rows,
            **{k: meta[k] for k in ("rebalance_close_date", "composition_effective_date",
                                    "composition_available_date", "fetched_at")})
        row = store.get_fund_disclosure_snapshots("SPY")[0]
        assert row["issuer_lei"] == rows[0]["issuer_lei"]
        assert row["asset_category"] == "EC"
        assert json.loads(row["raw_payload_json"]) == EVIDENCE["spy_AAPL"]["raw"]
    finally:
        store.close()


@pytest.mark.parametrize("label", ["spy_TERN", "soxx_tern"])
def test_teradyne_alias_is_security_scoped_not_fund_scoped(label):
    aliases = load_soxx_symbol_aliases(ROOT / "config/soxx_symbol_aliases.json")
    rows, _ = normalize([label], aliases=aliases)
    assert rows[0]["alias_symbol"] == "TER"
    assert rows[0]["alias_mode"] == "authoritative"


def test_same_fund_distinct_issuers_are_not_duplicate_companies():
    from src.data.fund_issuer_identity import audit_snapshot_identities
    rows, _ = normalize(["spy_AAPL", "spy_GOOG"])
    result = audit_snapshot_identities(rows)
    assert result["errors"] == []
    assert len({r["issuer_lei"] for r in result["resolved"]}) == 2


@pytest.mark.parametrize("labels", [["spy_GOOG", "spy_GOOGL"], ["spy_DISCA", "spy_DISCK"]])
def test_real_same_issuer_share_classes_require_explicit_merge(labels):
    from src.data.fund_issuer_identity import audit_snapshot_identities
    rows, _ = normalize(labels)
    assert any("duplicate_issuer" in e for e in audit_snapshot_identities(rows)["errors"])


def test_configured_share_class_group_must_share_issuer_identity():
    from src.data.fund_issuer_identity import audit_snapshot_identities
    rows, _ = normalize(["spy_GOOG", "spy_GOOGL"], groups={"GOOGL": ["GOOG"]})
    assert audit_snapshot_identities(rows)["errors"] == []
    rows[0]["issuer_lei"] = EVIDENCE["spy_AAPL"]["raw"]["lei"]
    rows[0]["raw_payload_json"]["lei"] = rows[0]["issuer_lei"]
    assert any("share_class_issuer_conflict" in e for e in audit_snapshot_identities(rows)["errors"])


def test_missing_issuer_is_not_filled_from_filer_cik():
    from src.data.fund_issuer_identity import audit_snapshot_identities
    rows, _ = normalize(["spy_BKR"])
    assert any("issuer_identity_unresolved" in e for e in audit_snapshot_identities(rows)["errors"])


def test_bad_lei_checksum_is_not_an_identity():
    from src.data.fund_issuer_identity import valid_issuer_lei
    lei = EVIDENCE["spy_AAPL"]["raw"]["lei"]
    assert valid_issuer_lei(lei) == lei
    assert valid_issuer_lei(lei[:-1] + "0") is None
    assert valid_issuer_lei("N/A") is None
    assert valid_issuer_lei("00000000000000000001") is None


def test_raw_identity_cannot_be_attached_to_another_security():
    from src.data.fund_issuer_identity import resolve_issuer_identity
    rows, _ = normalize(["spy_AAPL"])
    row = rows[0]
    row["raw_symbol"] = "OTHER"
    assert resolve_issuer_identity(row)[0] is None


def test_issuer_lei_does_not_turn_a_derivative_into_equity():
    from src.data.fund_issuer_identity import resolve_issuer_identity
    rows, _ = normalize(["spy_AAPL"])
    rows[0]["asset_category"] = rows[0]["raw_payload_json"]["assetCat"] = "DE"
    assert resolve_issuer_identity(rows[0])[0] is None


def test_fox_and_news_market_caps_are_not_summed_without_class_share_evidence():
    from terminal.index_pe_weekly import default_share_class_config, resolve_company_market_cap
    config = default_share_class_config(ROOT / "config/baskets")
    for primary, secondary in (("FOXA", "FOX"), ("NWSA", "NWS")):
        assert config["conventions"][primary] is None
        result = resolve_company_market_cap(
            primary=primary, base_market_cap=1000.0, secondaries=[secondary],
            convention=config["conventions"][primary], valuation_date="2026-09-08",
            market_cap_by_symbol={secondary: [{"date": "2026-09-08", "market_cap": 900.0}]},
            sanity_by_symbol={secondary: [{"date": "2026-09-08", "status": "clean"}]})
        assert result["market_cap"] is None


def test_verifier_rebuilds_real_issuer_identity_from_specific_snapshot(tmp_path):
    from scripts.verify_index_pe_history import _company_identities, _company_identity_check
    rows, meta = normalize(["spy_AAPL", "spy_GOOG"])
    store = MarketStore(tmp_path / "market.db")
    try:
        store.replace_fund_disclosure_snapshot("SPY", meta["holding_date"], "disclosure", rows,
            **{k: meta[k] for k in ("rebalance_close_date", "composition_effective_date",
                                    "composition_available_date", "fetched_at")})
        product = {"valuation_date": "2021-06-01",
                   "composition_effective_date": meta["composition_effective_date"],
                   "members_json": {"holding_date": meta["holding_date"],
                     "weight_basis": "fixed_rebalance_weight_proxy", "members": [
                         {"symbol": r["symbol"], "market_cap": 100, "ttm_net_income_usd": 10}
                         for r in rows]}}
        result = _company_identity_check("SPY", [product], _company_identities(store._get_conn(), "SPY"))
        assert result["errors"] == []
        # A forged normalized field must not override the original evidence.
        store._get_conn().execute("UPDATE fmp_fund_disclosure_holdings SET issuer_lei=?",
                                  [rows[0]["issuer_lei"]])
        result = _company_identity_check("SPY", [product], _company_identities(store._get_conn(), "SPY"))
        assert result["errors"]
    finally:
        store.close()


def reviewed_override():
    raw = EVIDENCE["spy_AAPL"]["raw"]
    return {"cusip": raw["cusip"], "isin": raw["isin"], "issuer_lei": raw["lei"],
            "valid_from": "2021-03-01", "valid_to": "2021-03-31",
            "reviewed_at": "2026-09-11", "reason": "test reviewed issuer evidence",
            "source_url": "https://www.sec.gov/Archives/edgar/test",
            "source_sha256": EVIDENCE["spy_AAPL"]["source_sha256"]}


def test_reviewed_evidence_only_fills_matching_security_in_valid_range(tmp_path):
    from src.data.fund_issuer_identity import load_issuer_overrides, resolve_issuer_identity
    item = reviewed_override()
    (tmp_path / "issuer_identity_overrides.json").write_text(json.dumps([item]))
    overrides = load_issuer_overrides(tmp_path)
    rows, _ = normalize(["spy_AAPL"])
    row = rows[0]
    row["issuer_lei"] = row["raw_payload_json"]["lei"] = None
    assert resolve_issuer_identity(row, overrides) == ("lei:" + item["issuer_lei"], "reviewed_security")
    assert resolve_issuer_identity({**row, "holding_date": "2021-04-01"}, overrides)[0] is None
    wrong_security = copy.deepcopy(row)
    wrong_security["isin"] = wrong_security["raw_payload_json"]["isin"] = "US0000000010"
    assert resolve_issuer_identity(wrong_security, overrides)[0] is None


@pytest.mark.parametrize("field", ["source_url", "source_sha256", "valid_to", "reviewed_at", "cusip"])
def test_unreviewed_or_incomplete_override_is_rejected(tmp_path, field):
    from src.data.fund_issuer_identity import load_issuer_overrides
    item = reviewed_override()
    del item[field]
    (tmp_path / "issuer_identity_overrides.json").write_text(json.dumps([item]))
    with pytest.raises(ValueError):
        load_issuer_overrides(tmp_path)


def test_override_cannot_silently_replace_disclosed_or_conflicting_issuer():
    from src.data.fund_issuer_identity import resolve_issuer_identity
    rows, _ = normalize(["spy_AAPL"])
    original = reviewed_override()
    wrong = {**original, "issuer_lei": EVIDENCE["spy_GOOG"]["raw"]["lei"]}
    assert resolve_issuer_identity(rows[0], [wrong]) == (None, "issuer_evidence_conflict")
    rows[0]["issuer_lei"] = rows[0]["raw_payload_json"]["lei"] = None
    assert resolve_issuer_identity(rows[0], [original, wrong])[0] is None


def test_missing_identity_stops_company_calls_and_retains_source_diagnostics(tmp_path, monkeypatch):
    from tests.test_backfill_index_pe_history import _fixture_db, _args
    from scripts import backfill_index_pe_history as backfill
    store = _fixture_db(tmp_path)
    store._get_conn().execute("UPDATE fmp_fund_disclosure_holdings SET issuer_lei=NULL")
    store._get_conn().commit()
    def forbidden(*args, **kwargs):
        pytest.fail("per-company stage ran with unresolved issuer identity")
    monkeypatch.setattr(backfill, "_run_fundamentals", forbidden)
    try:
        with pytest.raises(ValueError, match="issuer identity gate") as error:
            backfill.backfill_basket(_args(tmp_path, ROOT / "config/baskets"), "SPY",
                                     store=store, conn=store._get_conn())
        assert error.value.backfill_report["source_identity"]["errors"]
    finally:
        store.close()


def test_future_source_with_same_effective_date_cannot_supply_missing_identity(tmp_path):
    from scripts.verify_index_pe_history import _company_identities, _company_identity_check
    rows, meta = normalize(["spy_AAPL"])
    store = MarketStore(tmp_path / "market.db")
    try:
        fields = {k: meta[k] for k in ("rebalance_close_date", "composition_effective_date",
                                       "composition_available_date", "fetched_at")}
        store.replace_fund_disclosure_snapshot("SPY", meta["holding_date"], "disclosure", rows, **fields)
        store.replace_fund_disclosure_snapshot("SPY", "2021-03-30", "disclosure", rows,
            **{**fields, "composition_available_date": "2021-07-01"})
        store._get_conn().execute("UPDATE fmp_fund_disclosure_holdings SET issuer_lei=NULL WHERE holding_date=?",
                                  [meta["holding_date"]])
        product = {"valuation_date": "2021-06-01", "members_json": {
            "holding_date": meta["holding_date"], "weight_basis": "fixed_rebalance_weight_proxy",
            "members": [{"symbol": "AAPL", "market_cap": 100, "ttm_net_income_usd": 10}]}}
        assert _company_identity_check("SPY", [product], _company_identities(store._get_conn(), "SPY"))["errors"]
    finally:
        store.close()


def normalize_as(basket, labels):
    source_kind = EVIDENCE[labels[0]]['source_kind']
    raw = [copy.deepcopy(EVIDENCE[label]['raw']) for label in labels]
    rows, meta = normalize_fund_disclosure_snapshot(
        basket, raw, source_kind, '2026-09-25T04:00:00Z', CALENDAR,
        {}, {}, {}, expected_reconstitution_month=None)
    return [{**row, **{key: meta[key] for key in (
        'basket_symbol', 'holding_date', 'source_kind',
        'composition_effective_date', 'composition_available_date')}} for row in rows]


@pytest.mark.parametrize('case', [
    'inherit', 'cross_basket', 'published_later', 'held_later', 'latest_missing',
    'latest_unresolved', 'override_with_unresolved', 'duplicate_key_conflict',
    'duplicate_isin_conflict', 'isin_conflict', 'override_conflict', 'lei_conflict',
    'new_entrant', 'missing_cusip', 'sentinel_cusip', 'correction', 'legacy',
])
def test_live_disclosure_security_identity(case):
    from src.data.fund_issuer_identity import audit_snapshot_identities
    disclosure = normalize_as('SPY', ['spy_AAPL'])[0]
    live = normalize_as('SPY', ['live_aapl'])[0]
    key = 'lei:' + disclosure['issuer_lei']
    other_lei = EVIDENCE['spy_GOOG']['raw']['lei']
    overrides = []
    sources = [disclosure]
    expected, reason = key, 'disclosure_security_issuer'
    if case == 'cross_basket':
        live['basket_symbol'] = 'QQQ'
    elif case == 'published_later':
        disclosure['composition_available_date'] = '2026-10-01'
    elif case == 'held_later':
        disclosure['holding_date'] = '2026-09-30'
    elif case.startswith('latest_') or case == 'override_with_unresolved':
        newer = copy.deepcopy(disclosure)
        newer.update(holding_date='2026-06-30', composition_available_date='2026-08-25')
        if case == 'latest_missing':
            newer['cusip'] = newer['raw_payload_json']['cusip'] = '000000001'
        else:
            newer['issuer_lei'] = newer['raw_payload_json']['lei'] = 'N/A'
        sources.append(newer)
    elif case.startswith('duplicate_'):
        duplicate = copy.deepcopy(disclosure)
        if case == 'duplicate_key_conflict':
            duplicate['issuer_lei'] = duplicate['raw_payload_json']['lei'] = other_lei
        else:
            duplicate['isin'] = duplicate['raw_payload_json']['isin'] = 'US0000000010'
        sources.extend([duplicate, copy.deepcopy(disclosure)])  # conflict must stay sticky
    elif case == 'isin_conflict':
        live['isin'] = live['raw_payload_json']['isin'] = 'US0000000010'
    elif case == 'lei_conflict':
        live['issuer_lei'] = live['raw_payload_json']['lei'] = other_lei
    elif case in ('new_entrant', 'missing_cusip', 'sentinel_cusip'):
        value = {'new_entrant': '000000001', 'missing_cusip': None,
                 'sentinel_cusip': '000000000'}[case]
        live['cusip'] = live['raw_payload_json']['securityCusip'] = value
    elif case == 'correction':
        disclosure['issuer_lei'] = disclosure['raw_payload_json']['lei'] = 'N/A'
        overrides = [reviewed_override()]
    if case in ('override_with_unresolved', 'override_conflict', 'duplicate_key_conflict'):
        overrides = [{**reviewed_override(), 'valid_from': '2026-09-18',
                      'valid_to': '2026-12-31',
                      'issuer_lei': other_lei if case == 'override_conflict' else key[4:]}]
    if case in ('cross_basket', 'published_later', 'held_later', 'latest_missing',
                'latest_unresolved', 'new_entrant', 'missing_cusip', 'sentinel_cusip', 'legacy'):
        expected, reason = None, 'issuer_identity_unresolved'
    elif case in ('duplicate_key_conflict', 'duplicate_isin_conflict', 'isin_conflict',
                  'override_conflict', 'lei_conflict'):
        expected, reason = None, 'issuer_evidence_conflict'
    elif case == 'override_with_unresolved':
        reason = 'reviewed_security'
    before = copy.deepcopy((sources, live))
    report = audit_snapshot_identities([live], overrides,
                                      source_rows=None if case == 'legacy' else sources)
    assert (sources, live) == before
    if expected is None:
        assert report['resolved'] == []
        assert any(reason in error for error in report['errors'])
    else:
        assert report['errors'] == []
        assert report['resolved'][0]['canonical_issuer_key'] == expected
        assert report['resolved'][0]['reason'] == reason
        if reason == 'disclosure_security_issuer':
            assert report['resolved'][0]['evidence_disclosure_date'] == disclosure['holding_date']


def test_backfill_keeps_disclosure_evidence_outside_valuation_window(tmp_path, monkeypatch):
    from scripts import backfill_index_pe_history as runner
    from scripts.backfill_soxx_historical_pe import BackfillState
    from tests.test_backfill_index_pe_history import _args
    disclosure = normalize_as('SPY', ['spy_AAPL'])[0]
    live = normalize_as('SPY', ['live_aapl'])[0]
    state = BackfillState(trading_dates=CALENDAR, snapshots=[disclosure, live])
    monkeypatch.setattr(runner, 'load_state', lambda *a: state)
    monkeypatch.setattr(runner, 'resolve_window', lambda *a: ('2026-09-25', '2026-09-26'))
    def stop_after_identity(*a):
        raise RuntimeError('identity passed; stop before company work')
    monkeypatch.setattr(runner, '_snapshot_member_universe', stop_after_identity)
    with pytest.raises(RuntimeError, match='identity passed') as error:
        runner.backfill_basket(_args(tmp_path, ROOT / 'config/baskets'), 'SPY', conn=object())
    report = error.value.backfill_report
    assert len(state.snapshots) == 1 and state.snapshots[0]['source_kind'] == 'live'
    assert report['source_identity']['errors'] == []
    assert report['source_identity']['resolved'][0]['evidence_disclosure_date'] == disclosure['holding_date']
