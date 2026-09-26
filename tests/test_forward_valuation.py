"""PIT consensus valuation: frozen sources, fiscal windows and symmetric gates."""

import json
from datetime import date, timedelta

import pytest

from src.data.market_store import MarketStore
from terminal.forward_valuation import (
    FULL_BASKETS,
    compute_forward_member,
    compute_forward_basket,
    build_forward_valuations,
    verify_forward_valuations,
)

SNAP = "2026-09-05"
FUTURE = ["2026-09-30", "2026-12-31", "2027-03-31", "2027-06-30"]
ACTUAL = ["2025-12-31", "2026-03-31", "2026-06-30"]
SYMBOLS = ["AAPL", "AMZN", "GOOGL", "META", "MSFT", "NVDA", "TSLA"]


def estimates(income=25.0):
    return [
        {
            "snapshot_date": SNAP,
            "snapshot_kind": "weekly",
            "period_type": "Q",
            "fiscal_date": d,
            "net_income_avg": income,
            "eps_avg": 2.5,
            "num_analysts_eps": 5,
        }
        for d in FUTURE
    ]


def income_rows(currency="USD"):
    return [
        {
            "date": d,
            "period": f"Q{int(d[5:7]) // 3}",
            "accepted_date": (date.fromisoformat(d) + timedelta(days=20)).isoformat(),
            "reported_currency": currency,
            "net_income": 20.0,
            "weighted_average_shs_out_dil": 10.0,
        }
        for d in ACTUAL
    ]


def earnings():
    return [
        {
            "fiscal_date": d,
            "announce_date": row["accepted_date"],
            "eps_actual": 2.0,
            "match_method": "estimates_window",
        }
        for d, row in zip(ACTUAL, income_rows())
    ]


def member(**overrides):
    args = dict(
        symbol="AAPL",
        snapshot_date=SNAP,
        estimates=estimates(),
        earnings=earnings(),
        income=income_rows(),
        fx={},
        splits=[],
    )
    args.update(overrides)
    return compute_forward_member(**args)


def test_member_ntm_and_blend_have_distinct_consensus_windows():
    result = member()
    assert result["ntm_net_income_usd"] == 100
    assert result["blend_net_income_usd"] == 85
    assert [q["fiscal_date"] for q in result["ntm_quarters"]] == FUTURE
    assert [q["source"] for q in result["blend_quarters"]] == ["street_actual"] * 3 + [
        "consensus"
    ]
    assert result["earnings_basis"] == "analyst_consensus_not_verified_gaap"


@pytest.mark.parametrize(
    "bad", ["missing", "gap", "duplicate", "backfill", "other_snapshot"]
)
def test_ntm_does_not_fill_missing_or_ambiguous_quarters(bad):
    rows = estimates()
    if bad == "missing":
        rows[0]["net_income_avg"] = None
    elif bad == "gap":
        rows[1]["fiscal_date"] = "2027-09-30"
    elif bad == "duplicate":
        rows.append({**rows[-1], "fiscal_date": "2027-07-01"})
    elif bad == "backfill":
        for row in rows:
            row["snapshot_kind"] = "backfill"
    else:
        for row in rows:
            row["snapshot_date"] = "2026-09-12"
    assert member(estimates=rows)["ntm_net_income_usd"] is None


def test_future_announcement_is_not_a_blend_actual():
    rows = earnings()
    rows[-1]["announce_date"] = "2026-09-10"
    assert member(earnings=rows)["blend_net_income_usd"] is None


def test_blend_uses_street_eps_not_gaap_net_income():
    rows = income_rows()
    for row in rows:
        row["net_income"] = 999999
    assert member(income=rows)["blend_net_income_usd"] == 85


def test_blend_has_no_same_quarter_overlap_and_handles_reporting_lag():
    rows = estimates()
    rows.insert(0, {**rows[0], "fiscal_date": "2026-06-29", "net_income_avg": 9999})
    assert member(estimates=rows)["blend_net_income_usd"] == 85
    earlier = "2026-07-01"
    rows = [{**row, "snapshot_date": earlier} for row in rows]
    result = member(snapshot_date=earlier, estimates=rows)
    assert result["blend_net_income_usd"] is None  # only two actuals visible


def test_split_uncertainty_removes_blend_but_keeps_ntm():
    result = member(splits=[{"date": "2026-08-01", "numerator": 10, "denominator": 1}])
    assert result["blend_net_income_usd"] is None
    assert "split" in result["blend_exclusion_reason"]
    assert result["ntm_net_income_usd"] == 100


def test_currency_conversion_requires_visible_income_and_fresh_fx():
    rows = income_rows("EUR")
    assert member(income=rows)["ntm_net_income_usd"] is None
    result = member(
        income=rows,
        fx={
            "EUR": [
                {"date": "2026-09-04", "usd_per_unit": 1.1, "source_symbol": "EURUSD"}
            ]
        },
    )
    assert result["ntm_net_income_usd"] == pytest.approx(110)


def test_losses_remain_and_incomplete_members_leave_both_sides():
    members = [
        dict(member(), symbol="A", weight_pct=95, market_cap=1000),
        dict(member(estimates=estimates(-5)), symbol="B", weight_pct=5, market_cap=100),
    ]
    result = compute_forward_basket("SPY", SNAP, members)
    assert result["fwd_pe_ntm"] == pytest.approx(1100 / 80)
    members[1]["ntm_net_income_usd"] = None
    result = compute_forward_basket("SPY", SNAP, members)
    assert result["fwd_pe_ntm"] == 10
    assert result["n_covered_ntm"] == 1
    assert result["members_json"]["ntm_total_mcap"] == 1000


def test_missing_large_weight_is_not_invisible_to_mcap_gate():
    members = [
        dict(member(), symbol="A", weight_pct=40, market_cap=1000),
        dict(member(), symbol="B", weight_pct=60, market_cap=None),
    ]
    result = compute_forward_basket("SPY", SNAP, members)
    assert result["mcap_coverage_ntm"] == 1
    assert result["weight_coverage"] == 0.4
    assert result["fwd_pe_ntm"] is None
    assert result["members_json"]["status"] != "complete"


@pytest.fixture
def store(tmp_path):
    store = MarketStore(tmp_path / "market.db")
    store.upsert_fmp_forward_run(
        {
            "snapshot_date": SNAP,
            "run_kind": "weekly",
            "status": "complete",
            "target_universe": SYMBOLS,
            "started_at": SNAP,
            "summary_json": json.dumps(
                {
                    "run_state": {"quarter_empty": [], "earnings_failed": []},
                    "attempts": [],
                }
            ),
        }
    )
    for symbol in SYMBOLS:
        store.upsert_fmp_estimates(symbol, estimates())
        store.replace_fmp_earnings(symbol, earnings())
        c = store._get_conn()
        with c:
            for row in income_rows():
                c.execute(
                    "INSERT INTO income_quarterly "
                    "(symbol,date,period,accepted_date,reported_currency,net_income,weighted_average_shs_out_dil) "
                    "VALUES (?,?,?,?,?,?,?)",
                    [
                        symbol,
                        row["date"],
                        row["period"],
                        row["accepted_date"],
                        "USD",
                        20,
                        10,
                    ],
                )
            c.execute(
                "INSERT INTO historical_market_cap VALUES (?,?,?)",
                [symbol, "2026-09-04", 1000],
            )
            c.execute(
                "INSERT INTO daily_price (symbol,date,close) VALUES (?,?,?)",
                [symbol, "2026-09-04", 100],
            )
    for basket in FULL_BASKETS:
        if basket == "MAGS":
            continue
        store.replace_fmp_etf_holdings(
            basket,
            SNAP,
            [
                {
                    "raw_row_index": i,
                    "raw_asset": s,
                    "symbol": s,
                    "name": s,
                    "weight_pct": w,
                    "included": 1,
                    "covered_by": None,
                    "filter_reason": None,
                }
                for i, (s, w) in enumerate((("AAPL", 60), ("MSFT", 40)))
            ],
        )
    yield store
    store.close()


def test_six_baskets_use_exact_snapshot_and_sox_keeps_source_identity(store):
    rows = build_forward_valuations(store._get_conn(), SNAP)
    assert {r["basket"] for r in rows} == set(FULL_BASKETS)
    assert all(r["fwd_pe_ntm"] == 10 for r in rows)
    assert next(r for r in rows if r["basket"] == "MAGS")["n_members"] == 7
    assert verify_forward_valuations(store._get_conn(), SNAP, rows) == []


def test_failed_source_manifest_cannot_be_valued(store):
    with store._get_conn() as c:
        c.execute("UPDATE fmp_forward_runs SET status='failed'")
    with pytest.raises(ValueError, match="complete"):
        build_forward_valuations(store._get_conn(), SNAP)


def test_deleted_member_or_forged_value_fails_verification(store):
    rows = build_forward_valuations(store._get_conn(), SNAP)
    rows[0]["members_json"]["members"].pop()
    assert verify_forward_valuations(store._get_conn(), SNAP, rows)


def test_pit_commit_is_atomic_idempotent_and_frozen(store):
    rows = build_forward_valuations(store._get_conn(), SNAP)

    def certify(conn):
        assert conn.execute("PRAGMA query_only").fetchone()[0] == 1
        saved = [dict(r) for r in conn.execute("SELECT * FROM fmp_basket_valuation")]
        return not verify_forward_valuations(conn, SNAP, saved)

    with pytest.raises(ValueError, match="certification"):
        store.commit_fmp_basket_valuations(rows, lambda conn: False)
    assert (
        store._get_conn()
        .execute("SELECT COUNT(*) FROM fmp_basket_valuation")
        .fetchone()[0]
        == 0
    )
    assert store.commit_fmp_basket_valuations(rows, certify) == 6
    assert store.commit_fmp_basket_valuations(rows, certify) == 6
    rows[-1]["fwd_pe_ntm"] = 999
    with pytest.raises(ValueError, match="frozen"):
        store.commit_fmp_basket_valuations(rows, certify)
    assert store.get_fmp_basket_valuation("XLF", SNAP)[0]["fwd_pe_ntm"] == 10


def test_valuation_phase_cli_never_constructs_a_client(store, monkeypatch):
    from scripts.update_fmp_forward import main
    from importlib import import_module

    fmp_client = import_module("src.data.fmp_client")

    def forbid(*a, **kw):
        pytest.fail("valuation must not fetch fresh consensus")

    monkeypatch.setattr(fmp_client, "FMPClient", forbid)
    args = [
        "--mode",
        "weekly",
        "--phase",
        "valuation",
        "--snapshot-date",
        SNAP,
        "--data-root",
        str(store.db_path.parent),
    ]
    assert main(args + ["--dry-run"]) == 0
    assert (
        store._get_conn()
        .execute("SELECT COUNT(*) FROM fmp_basket_valuation")
        .fetchone()[0]
        == 0
    )
    assert main(args) == 0
    assert main(args) == 0
    run = store.get_fmp_forward_run(SNAP, "weekly")
    assert run["status"] == "complete"


def test_phase2_full_verifier_reads_nonempty_evidence(store):
    from scripts.update_fmp_forward import main
    from scripts.verify_fmp_forward import verify_run

    args = [
        "--mode",
        "weekly",
        "--phase",
        "valuation",
        "--snapshot-date",
        SNAP,
        "--data-root",
        str(store.db_path.parent),
    ]
    assert main(args) == 0
    rc, report = verify_run(store.db_path, store.db_path.parent, SNAP, stage="full")
    assert rc == 0, report["failures"]
    with store._get_conn() as conn:
        conn.execute(
            "UPDATE fmp_basket_valuation SET fwd_pe_ntm=999 WHERE basket='SPY'"
        )
    assert verify_run(store.db_path, store.db_path.parent, SNAP, stage="full")[0] == 1


def test_partial_valuation_never_commits_a_complete_snapshot(store):
    from scripts.update_fmp_forward import main

    with store._get_conn() as conn:
        conn.execute("DELETE FROM income_quarterly WHERE symbol='AAPL'")
    assert (
        main(
            [
                "--mode",
                "weekly",
                "--phase",
                "valuation",
                "--snapshot-date",
                SNAP,
                "--data-root",
                str(store.db_path.parent),
            ]
        )
        == 1
    )
    assert (
        store._get_conn()
        .execute("SELECT COUNT(*) FROM fmp_basket_valuation")
        .fetchone()[0]
        == 0
    )


@pytest.mark.parametrize(
    "extra", [["--resume"], ["--symbols", "AAPL"], ["--backfill-start", "2020-01-01"]]
)
def test_valuation_phase_rejects_ingestion_repair_flags(extra):
    from scripts.update_fmp_forward import parse_args

    with pytest.raises(SystemExit) as error:
        parse_args(["--mode", "weekly", "--phase", "valuation"] + extra)
    assert error.value.code == 2


def test_blend_gap_does_not_suppress_certified_ntm(store):
    from scripts.update_fmp_forward import main

    with store._get_conn() as conn:
        conn.execute("DELETE FROM fmp_earnings WHERE symbol='AAPL'")
    assert (
        main(
            [
                "--mode",
                "weekly",
                "--phase",
                "valuation",
                "--snapshot-date",
                SNAP,
                "--data-root",
                str(store.db_path.parent),
            ]
        )
        == 0
    )
    row = store.get_fmp_basket_valuation("SPY", SNAP)[0]
    assert row["fwd_pe_ntm"] == 10
    assert row["fwd_pe_blend"] is None
    assert json.loads(row["members_json"])["status"] == "partial"


def test_orphan_secondary_stays_in_weight_denominator_without_counting_income(store):
    with store._get_conn() as conn:
        conn.execute(
            "UPDATE fmp_etf_holdings_snapshot SET included=0, "
            "symbol=NULL,covered_by='AAPL',filter_reason='dual_class_secondary' "
            "WHERE basket='SPY' AND symbol='AAPL'"
        )
    row = next(
        r
        for r in build_forward_valuations(store._get_conn(), SNAP)
        if r["basket"] == "SPY"
    )
    assert row["fwd_pe_ntm"] is None
    assert row["weight_coverage"] == 0.4


def test_invalid_source_universe_is_not_laundered_by_complete_status(store):
    with store._get_conn() as conn:
        conn.execute("UPDATE fmp_forward_runs SET target_count=999")
    with pytest.raises(ValueError, match="universe"):
        build_forward_valuations(store._get_conn(), SNAP)


def test_two_share_classes_count_one_company_and_all_their_weight(store):
    store.replace_fmp_etf_holdings(
        "QQQ",
        SNAP,
        [
            {
                "raw_row_index": 0,
                "raw_asset": "GOOGL",
                "symbol": "GOOGL",
                "weight_pct": 60,
                "included": 1,
                "filter_reason": None,
                "covered_by": None,
            },
            {
                "raw_row_index": 1,
                "raw_asset": "GOOG",
                "symbol": None,
                "weight_pct": 40,
                "included": 0,
                "filter_reason": "dual_class_secondary",
                "covered_by": "GOOGL",
            },
        ],
    )
    row = next(
        r
        for r in build_forward_valuations(store._get_conn(), SNAP)
        if r["basket"] == "QQQ"
    )
    assert row["n_members"] == 1
    assert row["weight_coverage"] == 1
    assert row["total_mcap"] == 1000
    assert row["ntm_net_income"] == 100


def test_stale_market_cap_does_not_fall_back_to_current_profile(store):
    with store._get_conn() as conn:
        conn.execute(
            "UPDATE historical_market_cap SET date='2026-08-01' WHERE symbol='AAPL'"
        )
    row = next(
        r
        for r in build_forward_valuations(store._get_conn(), SNAP)
        if r["basket"] == "SPY"
    )
    assert row["fwd_pe_ntm"] is None
    assert row["weight_coverage"] == 0.4


def test_non_usd_street_eps_needs_explicit_adr_currency_basis():
    result = member(
        income=income_rows("EUR"),
        fx={
            "EUR": [
                {"date": "2026-09-04", "usd_per_unit": 1.1, "source_symbol": "EURUSD"}
            ]
        },
    )
    assert result["ntm_net_income_usd"] == pytest.approx(110)
    assert result["blend_net_income_usd"] is None
    assert "basis_unverified" in result["blend_exclusion_reason"]


def test_legacy_cash_and_negative_futures_are_excluded_without_source_rewrite(store):
    from src.data.fmp_forward_ingestion import normalize_holdings

    original = store.get_fmp_etf_holdings("QQQ", SNAP)
    names = [
        ("US DOLLAR", 0.1),
        ("USD Pending Dividends", 0.05),
        ("CME E-Mini NASDAQ 100 Index Future 09/18/2026", 0.11),
        ("CONTRA FUTURE FUTURE SEP 18 26 09/18/2026", -0.11),
    ]
    legacy = [
        {
            "raw_row_index": i + 2,
            "raw_asset": "",
            "name": name,
            "weight_pct": weight,
            "included": 0,
            "symbol": None,
            "covered_by": None,
            "filter_reason": "unrecognized_asset",
        }
        for i, (name, weight) in enumerate(names)
    ]
    store.replace_fmp_etf_holdings("QQQ", SNAP, original + legacy)
    row = next(
        r
        for r in build_forward_valuations(store._get_conn(), SNAP)
        if r["basket"] == "QQQ"
    )
    assert row["fwd_pe_ntm"] == 10
    assert row["weight_coverage"] == 1
    assert len(row["members_json"]["non_equity_exclusions"]) == 4
    assert all(
        r["filter_reason"] == "unrecognized_asset"
        for r in store.get_fmp_etf_holdings("QQQ", SNAP)[2:]
    )
    normalized = normalize_holdings(
        "QQQ",
        SNAP,
        [
            {"asset": "", "name": name, "weightPercentage": weight}
            for name, weight in names
        ],
        {},
        {},
    )
    assert [r["filter_reason"] for r in normalized] == [
        "cash_or_fund",
        "cash_or_fund",
        "futures",
        "futures",
    ]


def test_pit_valuation_excludes_reviewed_cvr_with_audited_source_join(store, monkeypatch):
    from pathlib import Path
    import terminal.forward_valuation as module
    import terminal.forward_source_verifier as source_verifier
    from src.data.security_source_corrections import load_security_source_corrections
    root = Path(__file__).resolve().parents[1]
    corrections = load_security_source_corrections(root/'config/baskets')[:1]
    corrections[0].update(valid_from=SNAP, valid_to=SNAP, required_snapshot_dates=[SNAP])
    monkeypatch.setattr(module, 'load_security_source_corrections', lambda _: corrections)
    monkeypatch.setattr(source_verifier, 'load_security_source_corrections', lambda _: corrections)
    source = json.loads((root/'tests/fixtures/index_pe_source_errors_20260926.json').read_text())[0]
    raw = json.loads(source['raw_payload_json'])
    raw['updatedAt'] = SNAP + ' 00:00:00'
    source.update(raw_payload_json=raw, holding_date=SNAP,
                  composition_available_date=SNAP, fetched_at=SNAP+'T12:00:00Z')
    store.replace_fund_disclosure_snapshot('SPY', SNAP, 'live', [source],
        rebalance_close_date='2025-12-19', composition_effective_date='2025-12-22',
        composition_available_date=SNAP, fetched_at=source['fetched_at'])
    original = store.get_fmp_etf_holdings('SPY', SNAP)
    cvr = dict(raw_row_index=99, raw_asset=raw['asset'], name=raw['name'],
               weight_pct=raw['weightPercentage'], market_value=raw['marketValue'],
               updated_at=raw['updatedAt'], included=1, symbol=raw['asset'],
               covered_by=None, filter_reason=None)
    store.replace_fmp_etf_holdings('SPY', SNAP, original+[cvr])
    rows = build_forward_valuations(store._get_conn(), SNAP)
    spy = next(r for r in rows if r['basket'] == 'SPY')
    assert spy['n_members'] == len(original)
    assert spy['members_json']['non_equity_exclusions'][0]['correction_id'] == corrections[0]['id']
    assert not any(m['symbol'] == raw['asset'] for m in spy['members_json']['members'])
    assert store.get_fmp_etf_holdings('SPY', SNAP)[-1]['included'] == 1
    assert verify_forward_valuations(store._get_conn(), SNAP, rows) == []


def test_physical_reviewed_cvr_marker_cannot_exclude_an_ordinary_equity(store):
    with store._get_conn() as conn:
        conn.execute("UPDATE fmp_etf_holdings_snapshot SET filter_reason='reviewed_cvr' "
                     "WHERE basket='SPY' AND symbol='MSFT'")
    with pytest.raises(ValueError, match='physical.*correction'):
        build_forward_valuations(store._get_conn(), SNAP)


def test_verified_xlf_financial_future_keeps_raw_negative_weight(store):
    from src.data.fmp_forward_ingestion import normalize_holdings
    old = store.get_fmp_etf_holdings('XLF', SNAP)
    future = dict(raw_row_index=99, raw_asset='IXAZ6', symbol='IXAZ6',
                  name='XAF FINANCIAL     DEC26', weight_pct=-0.0065394,
                  included=1, filter_reason=None, covered_by=None)
    store.replace_fmp_etf_holdings('XLF', SNAP, old+[future])
    rows = build_forward_valuations(store._get_conn(), SNAP)
    row = next(r for r in rows if r['basket']=='XLF')
    assert row['n_members'] == len(old)
    excluded = row['members_json']['non_equity_exclusions']
    assert excluded[0]['valuation_filter_reason']=='futures'
    assert excluded[0]['weight_pct']==future['weight_pct']
    assert store.get_fmp_etf_holdings('XLF', SNAP)[-1]['included']==1
    assert verify_forward_valuations(store._get_conn(), SNAP, rows)==[]
    normalized = normalize_holdings('XLF',SNAP,[{
        'asset':'IXAZ6','name':future['name'],'weightPercentage':future['weight_pct']}],{}, {})
    assert normalized[0]['filter_reason']=='futures' and normalized[0]['included']==0
    assert normalized[0]['weight_pct']==future['weight_pct']


@pytest.mark.parametrize('asset,name', [
    ('MSFT','XAF FINANCIAL DEC26'), ('IXAZ6','MICROSOFT CORP'),
    ('IXAZ7','XAF FINANCIAL DEC26'), ('IXAZ6','XAF FINANCIAL DEC27'),
])
def test_unreviewed_ticker_name_pair_keeps_negative_equity_gate(store,asset,name):
    original = store.get_fmp_etf_holdings('XLF',SNAP)
    store.replace_fmp_etf_holdings('XLF',SNAP,original+[{
        'raw_row_index':99,'raw_asset':asset,'symbol':asset,'name':name,
        'weight_pct':-0.01,'included':1,'filter_reason':None,'covered_by':None}])
    with pytest.raises(ValueError,match='invalid equity weight'):
        build_forward_valuations(store._get_conn(),SNAP)
