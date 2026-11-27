"""Tests for scripts/theme_basket_sync.py — 股票主题分类 M3 仓外三明治同步 CLI。

所有数据库一律在 tmp_path 自建；交换目录一律用 tmp_path，绝不碰真实
data/market.db、data/company.db 或 ~/.local/share/finance-theme-sync。
"""
import io
import json
import os
import sqlite3
import stat
import time
from contextlib import closing
from pathlib import Path

import pytest

from scripts import theme_basket_sync as sync

REPO = Path(__file__).resolve().parents[1]
BASKETS = REPO / "config" / "theme_baskets" / "baskets.json"
OVERRIDES = REPO / "config" / "theme_baskets" / "overrides.json"
ALIASES = REPO / "config" / "symbol_aliases.json"
REPORT_CONCEPTS = REPO / "config" / "concepts" / "report_concepts.json"
TODAY = "2026-11-29"
FAKE_SHEET_ID = "FAKE-SHEET-ID-1234567890abcdef"

SUPPLEMENTAL = json.loads(OVERRIDES.read_text(encoding="utf-8"))["supplemental_aliases"]
REAL_ALIASES = json.loads(ALIASES.read_text(encoding="utf-8"))["aliases"]

_MARKET_DDL = """
CREATE TABLE security_master (
    symbol TEXT PRIMARY KEY, cik TEXT, company_name TEXT, exchange TEXT,
    is_etf INTEGER NOT NULL DEFAULT 0, is_fund INTEGER NOT NULL DEFAULT 0,
    is_adr INTEGER NOT NULL DEFAULT 0, share_class_of TEXT,
    eligible INTEGER NOT NULL, reason TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE extended_membership (
    symbol TEXT NOT NULL, effective_from TEXT NOT NULL, effective_to TEXT,
    reason TEXT NOT NULL DEFAULT 'screener',
    PRIMARY KEY (symbol, effective_from)
);
CREATE TABLE company_profile (
    symbol TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at TEXT NOT NULL
);
"""


# ---------------------------------------------------------------- fixtures / builders

def _sm(symbol, cik=None, name=None, *, is_etf=0, is_fund=0, share_class_of=None,
        eligible=1, reason="ok"):
    return (symbol, cik, name or f"{symbol} Inc.", "NASDAQ", is_etf, is_fund, 0,
            share_class_of, eligible, reason, "2026-11-21T01:00:00Z")


def _profile(symbol, *, sector="Technology", industry="Semiconductors", cik=None,
             is_etf=False, is_fund=False, name=None):
    return {
        "symbol": symbol, "companyName": name or f"{symbol} Inc.", "cik": cik,
        "sector": sector, "industry": industry, "isEtf": is_etf, "isFund": is_fund,
        "description": f"SECRET-LONG-DESCRIPTION-{symbol}", "marketCap": 123456789,
    }


def make_market_db(path, *, sm=(), extended=(), profiles=()):
    with closing(sqlite3.connect(str(path))) as c:
        c.executescript(_MARKET_DDL)
        c.executemany("INSERT INTO security_master VALUES (?,?,?,?,?,?,?,?,?,?,?)", list(sm))
        c.executemany("INSERT INTO extended_membership (symbol, effective_from, effective_to) "
                      "VALUES (?,?,?)", list(extended))
        c.executemany("INSERT INTO company_profile VALUES (?,?,?)",
                      [(p["symbol"], json.dumps(p), "2026-11-21") for p in profiles])
        c.commit()
    return path


def make_company_db(path, watchlist=()):
    with closing(sqlite3.connect(str(path))) as c:
        c.execute("CREATE TABLE watchlist (symbol TEXT PRIMARY KEY, source TEXT DEFAULT '', "
                  "added_at TEXT NOT NULL)")
        c.executemany("INSERT INTO watchlist VALUES (?, 'analysis', '2026-08-21')",
                      [(s,) for s in watchlist])
        c.commit()
    return path


def make_xlsx(rows, *, tab="Summary_OSV", preamble=2):
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = tab
    for _ in range(preamble):
        ws.append(["Portfolio", None, None])
    ws.append(["Market", "Stock Ticker", "Last Price", "Shares", "Cost (Per Share)",
               "Mkt Value", "Category"])
    for market, ticker, shares, price in rows:
        ws.append([market, ticker, price, shares, 10.0, 999999.0, "Core"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


SHEET_ROWS = [
    ("US", "NASDAQ:NVDA", 100, "#N/A"),
    ("HK", "HKG:0700", 200, 400.0),
    ("CRYPTO", "BTC", 1.5, "#N/A"),
    ("US", "MU CALL", 3, 12.0),
    ("US", "BATS:DRAM", 50, 20.0),
    ("US", "SOX PUT", 2, 5.0),
    ("KR", "KRX:000660", 10, 150.0),
    ("US", "FITB-PA", 40, 25.0),
    ("US", "SOXX", 30, 220.0),
    ("US", "CLOSEDCO", 0, 1.0),
    ("US", "BADSHARES", "#N/A", 1.0),
    ("US", None, 5, 1.0),
]


@pytest.fixture
def world(tmp_path):
    """A small universe touching every asset_kind branch."""
    market = make_market_db(
        tmp_path / "market.db",
        sm=[
            _sm("NVDA", "0001045810"), _sm("GOOG", "0001652044"),
            _sm("GOOGL", "0001652044", share_class_of="GOOG", eligible=0,
                reason="secondary_share_class"),
            _sm("MU", "0000723125"), _sm("ABBNY", "0001091587"),
            _sm("NEWCO", "0009990001"), _sm("NEWCB", "0009990001", eligible=0),
            _sm("SOXX", "0001100663", is_etf=1, eligible=0, reason="etf"),
            _sm("BTC", "0002000001", is_etf=1, eligible=0, reason="etf"),
            _sm("FITB-PA", None, "Fifth Third Bancorp", eligible=0, reason="missing_profile"),
        ],
        extended=[
            ("NVDA", "2026-08-21", None), ("GOOG", "2026-08-21", None),
            ("GOOGL", "2026-08-21", "2026-09-01"), ("ABBNY", "2026-08-21", None),
            ("NEWCO", "2026-08-21", None), ("NEWCB", "2026-08-21", None),
            ("BTC", "2026-08-21", "2026-10-01"),
        ],
        profiles=[
            _profile("NVDA", cik="0001045810"), _profile("GOOG", cik="0001652044"),
            _profile("MU", cik="0000723125"), _profile("ABBNY", sector="Industrials",
                                                       industry="Electrical Equipment"),
            _profile("NEWCO"), _profile("NEWCB"),
            _profile("SOXX", is_etf=True), _profile("DXYZ", is_fund=True, sector="Financial Services"),
        ],
    )
    company = make_company_db(tmp_path / "company.db",
                              ["TEST", "1548.HK", "603259.SS", "DXYZ", "ABBNY"])
    return {
        "market_db_path": market, "company_db_path": company,
        "exchange_dir": tmp_path / "xchg", "baskets_path": BASKETS,
        "overrides_path": OVERRIDES, "aliases_path": ALIASES,
        "report_concepts_path": REPORT_CONCEPTS, "today": TODAY,
        "auto_pull_stale": False,
        "sheet_xlsx_fetcher": lambda: make_xlsx(SHEET_ROWS),
    }


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _by_symbol(items):
    return {item["symbol"]: item for item in items}


# ---------------------------------------------------------------- Task 1: normalize_instrument

def _norm(raw, market="US", *, as_of="2026-11-29", aliases=REAL_ALIASES,
          supplemental=SUPPLEMENTAL, share_class=None, known=frozenset()):
    return sync.normalize_instrument(
        raw, market, as_of_date=as_of, aliases=aliases, supplemental_aliases=supplemental,
        share_class_map=share_class or {}, known_us_symbols=known)


@pytest.mark.parametrize("raw, market, canonical, wrapper", [
    ("NASDAQ:NVDA", "US", "NVDA", "equity"),
    ("  nasdaq:nvda ", "US", "NVDA", "equity"),
    ("BATS:DRAM", "US", "DRAM", "equity"),
    ("HKG:0700", "HK", "HK:0700", "equity"),
    ("1548.HK", "US", "HK:1548", "equity"),
    ("00700.HK", "HK", "HK:0700", "equity"),
    ("700", "HK", "HK:0700", "equity"),
    ("09992", "HK", "HK:9992", "equity"),
    ("01810", "HK", "HK:1810", "equity"),
    ("HK:12345", "US", "HK:12345", "equity"),
    ("KRX:000660", "KR", "KR:000660", "equity"),
    ("KOSDAQ:083450", "KR", "KR:083450", "equity"),
    ("660", "KR", "KR:000660", "equity"),
    ("005930.KS", "US", "KR:005930", "equity"),
    ("603259.SS", "US", "CN:603259", "equity"),
    ("SZSE:300750", "US", "CN:300750", "equity"),
    ("BTC", "CRYPTO", "CRYPTO:BTC", "equity"),
    ("CRYPTO:ETH", "US", "CRYPTO:ETH", "equity"),
    ("BTC", "US", "BTC", "equity"),
    ("MU CALL", "US", "MU", "call"),
    ("SOX PUT", "US", "SOX", "put"),
    ("NVDA LEAPS", "US", "NVDA", "leaps"),
    ("NYSEARCA:SOXX", "US", "SOXX", "equity"),
    ("DXYZ", "US", "DXYZ", "equity"),
    ("OTC:ABBNY", "US", "ABBNY", "equity"),
    ("FITB-PA", "US", "FITB-PA", "equity"),
    ("FITB-PM", "US", "FITB-PM", "equity"),
    ("TEST", "US", "TEST", "equity"),
])
def test_normalize_instrument_markets_and_wrappers(raw, market, canonical, wrapper):
    got = _norm(raw, market)
    assert got is not None
    assert (got.canonical_symbol, got.wrapper_kind) == (canonical, wrapper)
    assert got.raw_symbol == raw
    prefix = canonical.split(":", 1)[0] if ":" in canonical else "US"
    assert got.market == prefix


def test_normalize_folds_share_class_and_supplemental_aliases():
    assert _norm("GOOGL").canonical_symbol == "GOOG"
    assert _norm("GOOGL", supplemental=[], share_class={"GOOGL": "GOOG"}).canonical_symbol == "GOOG"
    assert _norm("NASDAQ:GOOGL LEAPS").canonical_symbol == "GOOG"
    assert _norm("BRK.A").canonical_symbol == "BRK-B"


def test_normalize_dot_to_dash_only_for_known_us_symbols():
    assert _norm("BRK.B", supplemental=[], known=frozenset({"BRK-B"})).canonical_symbol == "BRK-B"
    assert _norm("ABC.D", supplemental=[], known=frozenset()).canonical_symbol == "ABC.D"


def test_normalize_supplemental_alias_respects_effective_from():
    assert _norm("PSTG", as_of="2026-06-18").canonical_symbol == "PSTG"
    assert _norm("PSTG", as_of="2026-06-19").canonical_symbol == "P"


def test_normalize_resolve_alias_none_drops_symbol_and_hops_are_bounded():
    merger = [{"alias": "OLD", "canonical": "NEW", "kind": "merger", "status": "verified",
               "reviewed_at": "2026-01-01", "evidence": "x", "effective_date": "2026-12-01"}]
    assert _norm("NEW", aliases=merger, supplemental=[]) is None
    assert _norm("OLD", aliases=merger, supplemental=[]).canonical_symbol == "OLD"
    loop = [{"alias_symbol": "AAA", "canonical_symbol": "BBB", "effective_from": None},
            {"alias_symbol": "BBB", "canonical_symbol": "AAA", "effective_from": None}]
    assert _norm("AAA", supplemental=loop).canonical_symbol in {"AAA", "BBB"}
    assert _norm("   ") is None


# ---------------------------------------------------------------- Task 1: classify_asset_kind

def _sm_row(reason="ok", *, is_etf=0, is_fund=0, company_name="X Inc."):
    return {"reason": reason, "is_etf": is_etf, "is_fund": is_fund, "company_name": company_name}


@pytest.mark.parametrize("symbol, sm_row, profile, expected", [
    ("HK:0700", None, None, ("non_us_equity", 0)),
    ("KR:000660", None, None, ("non_us_equity", 0)),
    ("CN:603259", None, None, ("non_us_equity", 0)),
    ("CRYPTO:BTC", None, None, ("crypto", 0)),
    ("DRAM", None, None, ("custom_basket", 0)),
    ("SOX", None, None, ("custom_basket", 0)),
    ("SOXX", _sm_row("etf", is_etf=1), None, ("etf", 0)),
    ("BTC", _sm_row("etf", is_etf=1), None, ("etf", 0)),
    ("QQQ", None, None, ("etf", 0)),
    ("SPYX", None, {"isEtf": True}, ("etf", 0)),
    ("DXYZ", None, {"isEtf": False, "isFund": True}, ("fund_cef", 0)),
    ("PSLV", _sm_row("fund", is_fund=1), None, ("fund_cef", 0)),
    ("ALL-PB", _sm_row("non_common_instrument"), {"isEtf": False}, ("other", 0)),
    ("FITB-PA", _sm_row("missing_profile", company_name="Fifth Third Bancorp"), None, ("other", 0)),
    ("FITB-PM", None, {"isEtf": False}, ("other", 0)),
    ("XYZ-WS", _sm_row(), {"isEtf": False}, ("other", 0)),
    ("TEST", None, None, ("other", 0)),             # PLACEHOLDER_SYMBOLS
    ("NEWUS", None, None, ("us_equity", 1)),        # unprofiled sub-$10B US stock (e.g. GRAL)
    ("TQQQ", None, None, ("etf", 0)),               # COMMON_US_ETFS
    ("IBIT", None, None, ("etf", 0)),
    ("GRAL", _sm_row("missing_profile", company_name=None), None, ("other", 0)),
    ("RML", _sm_row("missing_profile", company_name="Resolution ADR"), None, ("us_equity", 1)),
    ("ABBNY", _sm_row(), None, ("us_equity", 1)),
    ("BRK-B", _sm_row(), {"isEtf": False, "isFund": False}, ("us_equity", 1)),
    ("NVDA", _sm_row(), {"isEtf": False, "isFund": False}, ("us_equity", 1)),
])
def test_classify_asset_kind(symbol, sm_row, profile, expected):
    got = sync.classify_asset_kind(symbol, sm_row=sm_row, profile_meta=profile,
                                   extra_etfs=frozenset({"QQQ"}))
    assert got == expected


# ---------------------------------------------------------------- Task 1: tolerant sheet scan

def test_scan_sheet_tickers_tolerant_reads_only_ticker_market_shares():
    got = sync.scan_sheet_tickers_tolerant(make_xlsx(SHEET_ROWS))
    assert ("NASDAQ:NVDA", "US") in got
    assert ("HKG:0700", "HK") in got
    assert ("BTC", "CRYPTO") in got
    assert ("MU CALL", "US") in got
    tickers = [t for t, _ in got]
    assert "CLOSEDCO" not in tickers
    assert "BADSHARES" not in tickers
    assert len(got) == 9


def test_scan_sheet_tickers_tolerant_rejects_missing_tab_or_header():
    with pytest.raises(ValueError):
        sync.scan_sheet_tickers_tolerant(make_xlsx(SHEET_ROWS, tab="Other"))
    with pytest.raises(ValueError):
        sync.scan_sheet_tickers_tolerant(make_xlsx(SHEET_ROWS, preamble=20))
    with pytest.raises(ValueError):
        sync.scan_sheet_tickers_tolerant(b"<html>login</html>")


# ---------------------------------------------------------------- Task 1: detect_delta

def test_detect_delta_partitions_world(world):
    delta = sync.detect_delta(**world)
    missing = _by_symbol(delta["missing_symbols"])
    auto = _by_symbol(delta["auto_rows"])

    assert set(missing) == {"ABBNY", "NEWCO", "HK:0700", "HK:1548", "CN:603259", "KR:000660"}
    assert missing["ABBNY"]["sources"] == ["extended", "overlay"]
    assert missing["HK:0700"]["sources"] == ["overlay"]
    assert missing["NEWCO"]["sources"] == ["extended"]
    assert missing["ABBNY"]["asset_kind"] == "us_equity"
    assert missing["ABBNY"]["in_us_basket_calc"] == 1
    assert missing["ABBNY"]["profile_sector"] == "Industrials"
    assert missing["HK:0700"]["asset_kind"] == "non_us_equity"
    assert missing["HK:0700"]["in_us_basket_calc"] == 0
    assert missing["HK:0700"]["cik"] is None

    assert auto["SOXX"]["asset_kind"] == "etf"
    assert auto["BTC"]["asset_kind"] == "etf"
    assert auto["CRYPTO:BTC"]["asset_kind"] == "crypto"
    assert auto["DRAM"]["asset_kind"] == "custom_basket"
    assert auto["SOX"]["asset_kind"] == "custom_basket"
    assert auto["DXYZ"]["asset_kind"] == "fund_cef"
    assert auto["FITB-PA"]["asset_kind"] == "other"
    assert auto["TEST"]["asset_kind"] == "other"
    assert "GOOGL" not in auto and "GOOGL" not in missing
    for sym in ("SOXX", "BTC", "CRYPTO:BTC", "DRAM", "SOX", "DXYZ", "FITB-PA", "TEST"):
        row = auto[sym]
        assert row["in_us_basket_calc"] == 0
        assert row["l2_id"] is None
        assert row["cross_themes"] == []
    assert auto["SOXX"]["pi_bucket"] == "pi_etf_macro"
    assert auto["CRYPTO:BTC"]["pi_bucket"] == "pi_finance_crypto"
    assert auto["TEST"]["pi_bucket"] == "pi_other"


def test_detect_delta_locked_giants_become_auto_rows(world):
    delta = sync.detect_delta(**world)
    auto = _by_symbol(delta["auto_rows"])
    overrides = _by_symbol(_read(OVERRIDES)["locked_stocks"])
    baskets = {b["basket_id"]: b for b in _read(BASKETS)["baskets"]}
    for sym in ("NVDA", "GOOG", "MU"):
        row = auto[sym]
        assert row["asset_kind"] == "us_equity"
        assert row["in_us_basket_calc"] == 1
        assert row["l2_id"] == overrides[sym]["l2_id"]
        assert row["cross_themes"] == overrides[sym]["cross_themes"]
        assert row["pi_bucket"] == baskets[overrides[sym]["l2_id"]]["default_pi_bucket"]


def test_detect_delta_same_cik_secondary_is_superseded(world):
    delta = sync.detect_delta(**world)
    auto = _by_symbol(delta["auto_rows"])
    row = auto["NEWCB"]
    assert row["is_superseded"] == 1
    assert row["superseded_by"] == "NEWCO"
    assert row["in_us_basket_calc"] == 0
    assert row["asset_kind"] == "us_equity"
    assert "NEWCB" not in _by_symbol(delta["missing_symbols"])


def test_detect_delta_rows_are_store_ready(world):
    """auto_rows go straight into apply_envelope: no unknown keys, all valid in :memory:."""
    from src.data import theme_basket_store as store

    delta = sync.detect_delta(**world)
    rows = delta["auto_rows"]
    with closing(store.open_writer(":memory:")) as conn:
        res = store.apply_envelope(conn, baskets_doc=_read(BASKETS), overrides_doc=_read(OVERRIDES),
                                   rows=rows, payload_id=store.compute_payload_id(rows))
    assert res.quarantined == []


def test_detect_delta_writes_files_without_description_or_secrets(world, monkeypatch):
    monkeypatch.setenv("PORTFOLIO_SHEET_ID", FAKE_SHEET_ID)
    delta = sync.detect_delta(**world)
    xchg = world["exchange_dir"]
    text = (xchg / "inbox" / "delta.json").read_text(encoding="utf-8")
    assert "description" not in text
    assert "SECRET-LONG-DESCRIPTION" not in text
    assert FAKE_SHEET_ID not in text
    assert "docs.google.com" not in text
    assert "999999" not in text and "123456789" not in text
    assert _read(xchg / "inbox" / "delta.json") == delta
    assert delta["generated_date"] == TODAY
    assert delta["sheet_status"] == "OK"
    assert delta["delta_sha256"] == sync.compute_delta_sha256(delta)


def test_detect_delta_sheet_failure_degrades_to_unknown_without_leaking(world, capsys):
    def boom():
        raise RuntimeError(f"download failed for https://docs.google.com/spreadsheets/d/{FAKE_SHEET_ID}")

    world["sheet_xlsx_fetcher"] = boom
    delta = sync.detect_delta(**world)
    out = capsys.readouterr()
    text = (world["exchange_dir"] / "inbox" / "delta.json").read_text(encoding="utf-8")
    assert delta["sheet_status"] == "UNKNOWN"
    assert any("sheet" in w for w in delta["warnings"])
    for blob in (text, out.out, out.err, json.dumps(delta)):
        assert FAKE_SHEET_ID not in blob
        assert "docs.google.com" not in blob
    assert "HK:0700" not in _by_symbol(delta["missing_symbols"])


def test_detect_delta_skip_sheet(world):
    world["sheet_xlsx_fetcher"] = lambda: pytest.fail("sheet must not be fetched")
    delta = sync.detect_delta(skip_sheet=True, **world)
    assert delta["sheet_status"] == "SKIPPED"


def test_detect_delta_zero_pending_writes_empty_outbox_payload(tmp_path):
    market = make_market_db(
        tmp_path / "market.db",
        sm=[_sm("NVDA", "0001045810"), _sm("SOXX", "0001100663", is_etf=1, eligible=0, reason="etf")],
        extended=[("NVDA", "2026-08-21", None), ("SOXX", "2026-08-21", None)],
        profiles=[_profile("NVDA")])
    company = make_company_db(tmp_path / "company.db", [])
    xchg = tmp_path / "xchg"
    delta = sync.detect_delta(
        market_db_path=market, company_db_path=company, exchange_dir=xchg,
        baskets_path=BASKETS, overrides_path=OVERRIDES, aliases_path=ALIASES,
        today=TODAY, auto_pull_stale=False, skip_sheet=True)
    assert delta["missing_symbols"] == [] and delta["pivot_candidates"] == []
    payload = _read(xchg / "outbox" / "payload.json")
    assert payload["classifications"] == []
    assert payload["delta_sha256"] == delta["delta_sha256"]
    assert stat.S_IMODE((xchg / "outbox" / "payload.json").stat().st_mode) == 0o600


def test_detect_delta_clears_stale_outbox_and_sets_permissions(world):
    outbox = world["exchange_dir"] / "outbox"
    outbox.mkdir(parents=True)
    (outbox / "payload.json").write_text("{}", encoding="utf-8")
    (outbox / "junk.tmp").write_text("x", encoding="utf-8")
    delta = sync.detect_delta(**world)
    assert delta["missing_symbols"]
    assert list(outbox.iterdir()) == []
    xchg = world["exchange_dir"]
    for sub in ("inbox", "outbox", "pending", "db_locked_backup"):
        assert stat.S_IMODE((xchg / sub).stat().st_mode) == 0o700
    assert stat.S_IMODE((xchg / "inbox" / "delta.json").stat().st_mode) == 0o600


def test_detect_delta_stale_db_pulls_once_then_warns(world):
    old = time.time() - 40 * 3600
    os.utime(world["market_db_path"], (old, old))
    calls = []
    world.update(auto_pull_stale=True, pull_runner=lambda: calls.append(1) or 1)
    delta = sync.detect_delta(**world)
    assert calls == [1]
    assert delta["market_db_status"] == "WARN"
    assert any("stale" in w for w in delta["warnings"])
    assert delta["missing_symbols"]


def test_detect_delta_stale_db_recovered_by_pull(world):
    old = time.time() - 40 * 3600
    os.utime(world["market_db_path"], (old, old))

    def pull():
        os.utime(world["market_db_path"], None)
        return 0

    world.update(auto_pull_stale=True, pull_runner=pull)
    assert sync.detect_delta(**world)["market_db_status"] == "OK"


def test_detect_delta_existing_rows_skip_and_pivot(world):
    """Symbols already in stock_themes are skipped; a changed sector is a pivot candidate."""
    from src.data import theme_basket_store as store

    rows = [{"symbol": "ABBNY", "cik": "0001091587", "asset_kind": "us_equity",
             "in_us_basket_calc": 1, "l2_id": _first_l2(), "pi_bucket": "pi_other",
             "agreement_status": "single_pass_weekly", "profile_sector": "Technology",
             "profile_industry": "Electrical Equipment"},
            {"symbol": "TEST", "asset_kind": "other", "in_us_basket_calc": 0,
             "pi_bucket": "pi_other", "agreement_status": "single_pass_weekly"}]
    baskets = _read(BASKETS)
    with closing(store.open_writer(world["market_db_path"])) as conn:
        store.apply_envelope(conn, baskets_doc=baskets, overrides_doc=_read(OVERRIDES),
                             rows=rows, payload_id=store.compute_payload_id(rows))
    delta = sync.detect_delta(**world)
    assert "ABBNY" not in _by_symbol(delta["missing_symbols"])
    assert "TEST" not in _by_symbol(delta["auto_rows"])
    assert "NVDA" not in _by_symbol(delta["auto_rows"])  # override already in DB
    pivots = _by_symbol(delta["pivot_candidates"])
    assert set(pivots) == {"ABBNY"}
    assert pivots["ABBNY"]["old_profile_sector"] == "Technology"
    assert pivots["ABBNY"]["new_profile_sector"] == "Industrials"
    assert "description" not in json.dumps(delta)


def test_detect_delta_cik_collision_with_db_row_supersedes_new_symbol(world):
    from src.data import theme_basket_store as store

    rows = [{"symbol": "NEWCB", "cik": "0009990001", "asset_kind": "us_equity",
             "in_us_basket_calc": 1, "l2_id": _first_l2(), "pi_bucket": "pi_other",
             "agreement_status": "single_pass_weekly"}]
    with closing(store.open_writer(world["market_db_path"])) as conn:
        store.apply_envelope(conn, baskets_doc=_read(BASKETS), overrides_doc=_read(OVERRIDES),
                             rows=rows, payload_id=store.compute_payload_id(rows))
    delta = sync.detect_delta(**world)
    auto = _by_symbol(delta["auto_rows"])
    assert auto["NEWCO"]["is_superseded"] == 1
    assert auto["NEWCO"]["superseded_by"] == "NEWCB"
    assert "NEWCO" not in _by_symbol(delta["missing_symbols"])


def test_detect_delta_leaves_no_wal_and_no_writes(world):
    before = world["market_db_path"].read_bytes()
    sync.detect_delta(**world)
    assert world["market_db_path"].read_bytes() == before
    wal = Path(str(world["market_db_path"]) + "-wal")
    assert not wal.exists() or wal.stat().st_size == 0


def _first_l2():
    return sorted(b["basket_id"] for b in _read(BASKETS)["baskets"] if b["tier"] == "l2")[0]


# ================================================================ Task 2: cloud apply + private locks

import fcntl  # noqa: E402
import gzip  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
from contextlib import redirect_stdout  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402

import config.settings as settings  # noqa: E402
from src.data import theme_basket_store as store  # noqa: E402

T1 = datetime(2026, 11, 29, 2, 0, 0, tzinfo=timezone.utc)
T2 = datetime(2026, 11, 29, 2, 0, 1, tzinfo=timezone.utc)
T3 = datetime(2026, 11, 29, 2, 0, 2, tzinfo=timezone.utc)
BACKUP_LABEL = "auto-theme-sync"


def make_wal_db(path, n=50):
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(str(path))) as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("CREATE TABLE IF NOT EXISTS filler (id INTEGER PRIMARY KEY, v TEXT)")
        c.executemany("INSERT INTO filler (v) VALUES (?)", [("x" * 100,)] * n)
        c.commit()
    return path


def _backups(db_path):
    return sorted(p.name for p in db_path.parent.iterdir()
                  if p.name.startswith(db_path.name + ".backup-"))


def _row(symbol, **kw):
    row = {"symbol": symbol, "asset_kind": "us_equity", "in_us_basket_calc": 1, "l2_id": _first_l2(),
           "pi_bucket": "pi_other", "agreement_status": "single_pass_weekly", "cross_themes": []}
    row.update(kw)
    return row


def envelope(rows=(), *, overrides=None, send_telegram=True):
    rows = [dict(r) for r in rows]
    return sync.build_envelope(rows=rows, payload_id=store.compute_payload_id(rows),
                               baskets_doc=_read(BASKETS),
                               overrides_doc=overrides if overrides is not None else _read(OVERRIDES),
                               send_telegram=send_telegram)


class FakeTelegram:
    def __init__(self, lock_path=None, fail=False):
        self.calls, self.lock_path, self.fail = [], lock_path, fail
        self.lock_was_free = None

    def __call__(self, text, channel="private"):
        if self.lock_path is not None:
            with open(self.lock_path, "a") as fh:
                try:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    self.lock_was_free = True
                except BlockingIOError:
                    self.lock_was_free = False
        self.calls.append((text, channel))
        if self.fail:
            raise ConnectionError("telegram down")


@pytest.fixture
def cloud(tmp_path, monkeypatch):
    monkeypatch.setenv("FINANCE_ENV", "cloud")
    db = make_wal_db(tmp_path / "cloud" / "market.db")
    return {"db_path": db, "lock_path": tmp_path / "locks" / "resource-market_db_writer.lock"}


def _apply(cloud, env, **kw):
    kw.setdefault("send_telegram_fn", FakeTelegram())
    return sync.apply_cloud_envelope(env, db_path=cloud["db_path"], lock_path=cloud["lock_path"], **kw)


def _stock(db_path, symbol):
    with closing(sqlite3.connect(str(db_path))) as c:
        c.row_factory = sqlite3.Row
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name = 'stock_themes'").fetchone():
            return None
        row = c.execute("SELECT * FROM stock_themes WHERE symbol = ?", (symbol,)).fetchone()
        return dict(row) if row else None


def _count_rows(db_path, table):
    with closing(sqlite3.connect(str(db_path))) as c:
        return c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


# ---------------------------------------------------------------- backup_sqlite_wal_safe

def test_backup_keep2_gzips_previous_and_prunes_third(tmp_path):
    db = make_wal_db(tmp_path / "market.db")
    first = sync.backup_sqlite_wal_safe(db, keep=2, now=T1)
    second = sync.backup_sqlite_wal_safe(db, keep=2, now=T2)
    third = sync.backup_sqlite_wal_safe(db, keep=2, now=T3)
    assert first.name == "market.db.backup-20261129020000-auto-theme-sync"
    assert _backups(db) == [second.name + ".gz", third.name]
    assert not first.exists()
    with closing(sqlite3.connect(str(third))) as c:
        assert c.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert c.execute("SELECT COUNT(*) FROM filler").fetchone()[0] == 50
    with gzip.open(str(second) + ".gz", "rb") as fh:
        assert fh.read(16) == b"SQLite format 3\x00"
    assert not [p for p in tmp_path.iterdir() if "partial" in p.name]


def test_backup_refuses_when_disk_is_short(tmp_path, monkeypatch):
    db = make_wal_db(tmp_path / "market.db")

    class Usage:
        free = 10

    monkeypatch.setattr(sync.shutil, "disk_usage", lambda p: Usage)
    with pytest.raises(RuntimeError, match="insufficient disk"):
        sync.backup_sqlite_wal_safe(db, keep=2, now=T1)
    assert _backups(db) == []


def test_backup_missing_db_returns_none(tmp_path):
    assert sync.backup_sqlite_wal_safe(tmp_path / "absent.db", now=T1) is None


# ---------------------------------------------------------------- apply_cloud_envelope

def test_apply_first_commit_backs_up_commits_and_notifies_after_unlock(cloud):
    tg = FakeTelegram(lock_path=cloud["lock_path"])
    rc, res = _apply(cloud, envelope([_row("PRIV1"), _row("PRIV2")]), send_telegram_fn=tg, now=T1)
    assert rc == 0 and res["ok"] is True
    assert res["replayed"] is False
    assert res["inserted"] >= 2
    assert res["backup"] and Path(res["backup"]).exists()
    assert _stock(cloud["db_path"], "PRIV1")["source"] == "agy_llm"
    assert _stock(cloud["db_path"], "NVDA")["source"] == "override"
    assert len(tg.calls) == 1 and tg.calls[0][1] == "private"
    assert tg.lock_was_free is True
    assert res["telegram"] == "sent"


def test_apply_replay_is_idempotent_and_heartbeat_skips_backup(cloud):
    env = envelope([_row("PRIV1")])
    rc1, res1 = _apply(cloud, env, now=T1)
    history = _count_rows(cloud["db_path"], "stock_theme_history")
    rc2, res2 = _apply(cloud, env, now=T2)
    assert (rc1, rc2) == (0, 0)
    assert res2["replayed"] is True and res2["inserted"] == 0
    assert res2["backup"] is None
    assert _count_rows(cloud["db_path"], "stock_theme_history") == history
    assert _count_rows(cloud["db_path"], "theme_sync_commits") == 1

    rc3, res3 = _apply(cloud, envelope([]), now=T2)
    assert rc3 == 0 and res3["backup"] is None          # 0 rows, dictionary unchanged

    overrides = _read(OVERRIDES)
    overrides["locked_stocks"] = overrides["locked_stocks"][:-1]
    rc4, res4 = _apply(cloud, envelope([], overrides=overrides), now=T3)
    assert rc4 == 0 and res4["backup"] is not None      # dictionary changed


def test_apply_lock_busy_returns_75_without_db_writes(cloud):
    cloud["lock_path"].parent.mkdir(parents=True)
    holder = subprocess.Popen(
        [sys.executable, "-c",
         "import fcntl, sys, time\n"
         "fh = open(sys.argv[1], 'a')\n"
         "fcntl.flock(fh.fileno(), fcntl.LOCK_EX)\n"
         "print('locked', flush=True)\n"
         "time.sleep(30)\n", str(cloud["lock_path"])],
        stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "locked"
        before = cloud["db_path"].read_bytes()
        tg = FakeTelegram()
        start = time.monotonic()
        rc, res = _apply(cloud, envelope([_row("PRIV1")]), lock_timeout_sec=0.5, send_telegram_fn=tg)
        assert rc == 75
        assert res == {"ok": False, "error": "lock_busy"} or (res["ok"] is False and res["error"] == "lock_busy")
        assert time.monotonic() - start < 10
        assert cloud["db_path"].read_bytes() == before
        with closing(sqlite3.connect(str(cloud["db_path"]))) as c:
            assert c.execute("SELECT 1 FROM sqlite_master WHERE name = 'stock_themes'").fetchone() is None
        assert tg.calls == []
    finally:
        holder.kill()
        holder.wait()


def test_apply_telegram_failure_keeps_exit_zero(cloud):
    tg = FakeTelegram(fail=True)
    rc, res = _apply(cloud, envelope([_row("PRIV1")]), send_telegram_fn=tg, now=T1)
    assert rc == 0 and res["ok"] is True
    assert res["telegram"] == "failed"
    assert "ConnectionError" in res["telegram_error"]
    assert _stock(cloud["db_path"], "PRIV1") is not None


@pytest.mark.parametrize("flags", [{"force_no_telegram": True}, {"send_telegram": False}])
def test_apply_telegram_can_be_disabled(cloud, flags):
    tg = FakeTelegram()
    env = envelope([], send_telegram=flags.get("send_telegram", True))
    rc, res = _apply(cloud, env, send_telegram_fn=tg,
                     force_no_telegram=flags.get("force_no_telegram", False))
    assert rc == 0 and tg.calls == [] and res["telegram"] == "skipped"


def test_apply_refuses_outside_cloud(tmp_path, monkeypatch):
    monkeypatch.delenv("FINANCE_ENV", raising=False)
    db = tmp_path / "market.db"
    rc, res = sync.apply_cloud_envelope(envelope([]), db_path=db, lock_path=tmp_path / "l.lock",
                                        send_telegram_fn=FakeTelegram())
    assert rc == 1 and res["ok"] is False and "FINANCE_ENV" in res["error"]
    assert not db.exists()


def test_apply_allow_noncloud_never_reaches_production(tmp_path, monkeypatch):
    prod = make_wal_db(tmp_path / "prod" / "market.db")
    monkeypatch.setattr(settings, "MARKET_DB_PATH", prod)
    monkeypatch.delenv("FINANCE_ENV", raising=False)
    before = prod.read_bytes()
    rc, res = sync.apply_cloud_envelope(envelope([_row("PRIV1")]), db_path=prod,
                                        lock_path=tmp_path / "l.lock", allow_noncloud=True,
                                        send_telegram_fn=FakeTelegram())
    assert rc == 1 and res["ok"] is False
    assert prod.read_bytes() == before
    assert _backups(prod) == []


def test_apply_allow_noncloud_on_test_db(tmp_path, monkeypatch):
    monkeypatch.delenv("FINANCE_ENV", raising=False)
    db = tmp_path / "t" / "market.db"
    db.parent.mkdir()
    rc, res = sync.apply_cloud_envelope(envelope([_row("PRIV1")]), db_path=db,
                                        lock_path=tmp_path / "l.lock", allow_noncloud=True,
                                        send_telegram_fn=FakeTelegram())
    assert rc == 0 and res["ok"] is True


@pytest.mark.parametrize("mutate", [
    lambda e: e.update(payload_id="0" * 64),
    lambda e: e.update(schema_version=2),
    lambda e: e.update(rows="nope"),
    lambda e: e.pop("baskets_doc"),
])
def test_apply_rejects_bad_envelope_without_writes(cloud, mutate):
    env = envelope([_row("PRIV1")])
    mutate(env)
    before = cloud["db_path"].read_bytes()
    rc, res = _apply(cloud, env)
    assert rc == 1 and res["ok"] is False
    assert cloud["db_path"].read_bytes() == before
    assert _backups(cloud["db_path"]) == []


def test_apply_broken_dictionary_rolls_back(cloud):
    env = envelope([_row("PRIV1")])
    env["overrides_doc"]["locked_stocks"][0]["l2_id"] = "no_such_l2"
    rc, res = _apply(cloud, env, now=T1)
    assert rc == 1 and res["ok"] is False
    with closing(sqlite3.connect(str(cloud["db_path"]))) as c:
        assert c.execute("SELECT COUNT(*) FROM theme_sync_commits").fetchone()[0] == 0


def _run_cli(args, stdin_bytes, env_extra):
    env = dict(os.environ)
    env.update(env_extra)
    return subprocess.run([sys.executable, str(REPO / "scripts" / "theme_basket_sync.py"), *args],
                          input=stdin_bytes, capture_output=True, cwd=str(REPO), env=env, timeout=120)


def test_apply_cli_stdout_is_pure_json(cloud):
    args = ["--apply-cloud-stdin", "--db", str(cloud["db_path"]), "--lock-path", str(cloud["lock_path"]),
            "--no-telegram"]
    body = json.dumps(envelope([_row("PRIV1", business_note="中文备注")]), ensure_ascii=False).encode("utf-8")
    proc = _run_cli(args, body, {"FINANCE_ENV": "cloud"})
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    lines = proc.stdout.decode("ascii").splitlines()
    assert len(lines) == 1
    res = json.loads(lines[0])
    assert res["ok"] is True and res["inserted"] >= 1
    assert _stock(cloud["db_path"], "PRIV1")["business_note"] == "中文备注"

    bad = _run_cli(args, b"\xff\xfe not utf8", {"FINANCE_ENV": "cloud"})
    assert bad.returncode == 1
    assert json.loads(bad.stdout.decode("ascii"))["ok"] is False

    nonjson = _run_cli(args, b"{truncated", {"FINANCE_ENV": "cloud"})
    assert nonjson.returncode == 1
    assert json.loads(nonjson.stdout.decode("ascii"))["ok"] is False


# ---------------------------------------------------------------- private locks

def _cloud_cli_runner(cloud, calls):
    """Fake SSH: run the remote CLI in-process against the tmp cloud db."""
    def runner(remote_args, stdin_bytes=None):
        calls.append(list(remote_args))
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = sync.main(list(remote_args) + ["--db", str(cloud["db_path"]),
                                                "--lock-path", str(cloud["lock_path"])])
        return rc, buf.getvalue().encode("ascii"), b""
    return runner


def _cross_of(db_path, symbol):
    with closing(sqlite3.connect(str(db_path))) as c:
        return sorted(c.execute("SELECT theme_id, role, source FROM stock_cross_themes WHERE symbol = ?",
                                (symbol,)).fetchall())


def _first_cross():
    return sorted(b["basket_id"] for b in _read(BASKETS)["baskets"] if b["tier"] == "cross")[0]


def test_private_lock_refuses_public_override(cloud):
    _apply(cloud, envelope([_row("PRIV1")]), now=T1)
    rc, res = sync.cloud_private_lock(db_path=cloud["db_path"], lock_path=cloud["lock_path"],
                                      action="lock", symbol="NVDA", l2_id=_first_l2())
    assert rc == 1 and res["ok"] is False and "override" in res["error"]
    assert _stock(cloud["db_path"], "NVDA")["source"] == "override"


def test_private_lock_then_unlock_via_local_cli(cloud, tmp_path):
    _apply(cloud, envelope([_row("PRIV1")]), now=T1)
    xchg = tmp_path / "xchg"
    calls, pulls = [], []
    runner = _cloud_cli_runner(cloud, calls)
    cross = _first_cross()
    rc = sync.run_private_lock(action="lock", symbol="priv1", l2_id=_first_l2(),
                               cross=[f"{cross}:platform_giant"], note="私密小票", exchange_dir=xchg,
                               market_db_path=tmp_path / "local-market.db",
                               ssh_runner=runner, pull_runner=lambda: pulls.append(1) or 0)
    assert rc == 0
    assert calls[0][:3] == ["--cloud-local", "--lock-private", "PRIV1"]
    row = _stock(cloud["db_path"], "PRIV1")
    assert (row["source"], row["agreement_status"], row["business_note"]) == ("db_locked", "locked", "私密小票")
    assert _cross_of(cloud["db_path"], "PRIV1") == [(cross, "platform_giant", "db_locked")]
    backup = xchg / "db_locked_backup" / "db_locked_latest.json"
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    saved = _read(backup)
    assert [r["symbol"] for r in saved["db_locked_rows"]] == ["PRIV1"]
    assert saved["db_locked_rows"][0]["cross_themes"] == [{"role": "platform_giant", "theme_id": cross}]
    assert pulls == [1]

    rc = sync.run_private_lock(action="unlock", symbol="PRIV1", exchange_dir=xchg,
                               market_db_path=tmp_path / "local-market.db",
                               ssh_runner=runner, pull_runner=lambda: pulls.append(1) or 0,
                               skip_pull=True)
    assert rc == 0
    assert calls[1] == ["--cloud-local", "--unlock-private", "PRIV1"]
    row = _stock(cloud["db_path"], "PRIV1")
    assert (row["source"], row["agreement_status"]) == ("agy_llm", "needs_spot_check")
    assert _read(backup)["db_locked_rows"] == []
    assert pulls == [1]


def test_private_lock_cloud_failure_keeps_local_backup(cloud, tmp_path):
    xchg = tmp_path / "xchg"
    runner = _cloud_cli_runner(cloud, [])
    rc = sync.run_private_lock(action="unlock", symbol="NOPE", exchange_dir=xchg,
                               market_db_path=tmp_path / "local-market.db", ssh_runner=runner,
                               pull_runner=lambda: pytest.fail("no pull on failure"))
    assert rc == 1
    assert not (xchg / "db_locked_backup" / "db_locked_latest.json").exists()


def test_private_lock_transport_failure(tmp_path):
    def runner(remote_args, stdin_bytes=None):
        raise ConnectionError("ssh: connect to host aliyun port 22: Connection refused")

    rc = sync.run_private_lock(action="unlock", symbol="PRIV1", exchange_dir=tmp_path / "x",
                               market_db_path=tmp_path / "m.db", ssh_runner=runner,
                               pull_runner=lambda: pytest.fail("no pull"))
    assert rc == 1


# ================================================================ Task 3: Step C / sync-config / replay

class FakeCloud:
    """Fake SSH runner: `--apply-cloud-stdin` against a tmp cloud db, optional scripted failures.

    Each call runs one second later: backup names have 1 s resolution, and a real 1.5 GB
    backup alone takes longer than that, so two calls never share a second in production.
    """

    def __init__(self, cloud, script=()):
        self.cloud, self.script, self.calls = cloud, list(script), []

    def __call__(self, remote_args, stdin_bytes=None):
        env = json.loads(stdin_bytes.decode("utf-8"))
        self.calls.append((list(remote_args), env))
        mode = self.script.pop(0) if self.script else "ok"
        now = T1 + timedelta(seconds=len(self.calls))
        if mode == "busy":
            return 75, b'{"ok": false, "error": "lock_busy"}\n', b""
        if mode == "down":
            raise subprocess.TimeoutExpired(["ssh"], 1)
        if mode == "commit_then_drop":
            sync.apply_cloud_envelope(env, db_path=self.cloud["db_path"], lock_path=self.cloud["lock_path"],
                                      send_telegram_fn=FakeTelegram(), now=now)
            return 255, b"", b"Connection reset by peer"
        rc, res = sync.apply_cloud_envelope(env, db_path=self.cloud["db_path"],
                                            lock_path=self.cloud["lock_path"],
                                            send_telegram_fn=FakeTelegram(), now=now)
        return rc, (json.dumps(res) + "\n").encode("ascii"), b""


def _step_a(tmp_path, *, missing=("ABBNY", "NEWCO")):
    """Minimal delta via the real Step A: two missing US names + deterministic auto rows."""
    sm = [_sm("NVDA", "0001045810"), _sm("SOXX", "0001100663", is_etf=1, eligible=0, reason="etf")]
    sm += [_sm(s, f"00099900{i:02d}") for i, s in enumerate(missing)]
    market = make_market_db(
        tmp_path / "local-market.db", sm=sm,
        extended=[(r[0], "2026-08-21", None) for r in sm],
        profiles=[_profile(r[0]) for r in sm])
    company = make_company_db(tmp_path / "company.db", ["TEST"])
    xchg = tmp_path / "xchg"
    delta = sync.detect_delta(market_db_path=market, company_db_path=company, exchange_dir=xchg,
                              baskets_path=BASKETS, overrides_path=OVERRIDES, aliases_path=ALIASES,
                              today=TODAY, auto_pull_stale=False, skip_sheet=True)
    return xchg, market, delta


def _write_payload(xchg, delta, classifications, **extra):
    payload = {"delta_sha256": delta["delta_sha256"], "classifications": classifications}
    payload.update(extra)
    (xchg / "outbox" / "payload.json").write_text(json.dumps(payload), encoding="utf-8")


def _cls(symbol, l2_id=None, **kw):
    item = {"symbol": symbol, "l2_id": l2_id or _first_l2(), "cross_themes": [],
            "business_note": f"{symbol} note", "agreement_status": "single_pass_weekly"}
    item.update(kw)
    return item


def _commit(xchg, market, runner, pulls=None, **kw):
    kw.setdefault("today", TODAY)
    return sync.commit_outbox(exchange_dir=xchg, baskets_path=BASKETS, overrides_path=OVERRIDES,
                              market_db_path=market, ssh_runner=runner,
                              pull_runner=(lambda: pulls.append(1) or 0) if pulls is not None
                              else (lambda: pytest.fail("unexpected pull")), **kw)


def _pending(xchg):
    return sorted((xchg / "pending").glob("*.json"))


def test_commit_outbox_happy_path_pushes_and_pulls(cloud, tmp_path):
    xchg, market, delta = _step_a(tmp_path)
    _write_payload(xchg, delta, [_cls("ABBNY"), _cls("NEWCO")])
    fake, pulls = FakeCloud(cloud), []
    assert _commit(xchg, market, fake, pulls) == 0
    assert pulls == [1]
    assert not (xchg / "outbox" / "payload.json").exists()
    assert _pending(xchg) == []
    assert fake.calls[0][0] == ["--apply-cloud-stdin"]
    env = fake.calls[0][1]
    assert env["baskets_doc"] == _read(BASKETS) and env["overrides_doc"] == _read(OVERRIDES)
    symbols = {r["symbol"] for r in env["rows"]}
    assert {"ABBNY", "NEWCO", "SOXX", "TEST", "NVDA"} <= symbols
    assert _stock(cloud["db_path"], "ABBNY")["business_note"] == "ABBNY note"
    assert _stock(cloud["db_path"], "SOXX")["asset_kind"] == "etf"


@pytest.mark.parametrize("tamper", ["sha", "stale", "extra_symbol", "duplicate", "auto_symbol", "no_delta"])
def test_commit_outbox_hard_gates_reject(cloud, tmp_path, tamper):
    xchg, market, delta = _step_a(tmp_path)
    classifications = [_cls("ABBNY")]
    today = TODAY
    if tamper == "sha":
        _write_payload(xchg, delta, classifications)
        payload = _read(xchg / "outbox" / "payload.json")
        payload["delta_sha256"] = "f" * 64
        (xchg / "outbox" / "payload.json").write_text(json.dumps(payload), encoding="utf-8")
    else:
        if tamper == "stale":
            today = "2026-11-30"
        elif tamper == "extra_symbol":
            classifications.append(_cls("HALLUCINATED"))
        elif tamper == "duplicate":
            classifications.append(_cls("ABBNY"))
        elif tamper == "auto_symbol":
            classifications.append(_cls("SOXX"))
        _write_payload(xchg, delta, classifications)
        if tamper == "no_delta":
            (xchg / "inbox" / "delta.json").unlink()
    fake = FakeCloud(cloud)
    assert _commit(xchg, market, fake, today=today) == 1
    assert fake.calls == []
    assert (xchg / "outbox" / "payload.json").exists()
    assert _pending(xchg) == []


def test_commit_outbox_rejects_edited_delta(cloud, tmp_path):
    xchg, market, delta = _step_a(tmp_path)
    tampered = _read(xchg / "inbox" / "delta.json")
    tampered["missing_symbols"].append(dict(tampered["missing_symbols"][0], symbol="EVIL"))
    (xchg / "inbox" / "delta.json").write_text(json.dumps(tampered), encoding="utf-8")
    _write_payload(xchg, delta, [_cls("EVIL")])
    fake = FakeCloud(cloud)
    assert _commit(xchg, market, fake) == 1
    assert fake.calls == []


def test_commit_outbox_restores_deterministic_fields(cloud, tmp_path):
    xchg, market, delta = _step_a(tmp_path)
    evil = _cls("ABBNY", asset_kind="etf", in_us_basket_calc=0, cik="0000000666",
                profile_sector="Evil", company_name_snapshot="Evil Corp", source="override",
                is_superseded=1, superseded_by="NVDA")
    _write_payload(xchg, delta, [evil, _cls("NEWCO", agreement_status=None, pi_bucket=None)])
    fake = FakeCloud(cloud)
    assert _commit(xchg, market, fake, skip_pull=True) == 0
    truth = _by_symbol(delta["missing_symbols"])["ABBNY"]
    row = _stock(cloud["db_path"], "ABBNY")
    for key in ("cik", "asset_kind", "in_us_basket_calc", "profile_sector", "company_name_snapshot"):
        assert row[key] == truth[key]
    assert (row["source"], row["is_superseded"], row["superseded_by"]) == ("agy_llm", 0, None)
    newco = _stock(cloud["db_path"], "NEWCO")
    baskets = {b["basket_id"]: b for b in _read(BASKETS)["baskets"]}
    assert newco["agreement_status"] == "single_pass_weekly"
    assert newco["pi_bucket"] == baskets[_first_l2()]["default_pi_bucket"]


def test_commit_outbox_non_us_rows_drop_cross_themes(cloud, tmp_path):
    xchg, market, delta = _step_a(tmp_path)
    world_delta = _read(xchg / "inbox" / "delta.json")
    world_delta["missing_symbols"].append({
        "symbol": "HK:0700", "cik": None, "asset_kind": "non_us_equity", "in_us_basket_calc": 0,
        "profile_sector": None, "profile_industry": None, "company_name_snapshot": None,
        "sources": ["overlay"]})
    world_delta["delta_sha256"] = sync.compute_delta_sha256(world_delta)
    (xchg / "inbox" / "delta.json").write_text(json.dumps(world_delta), encoding="utf-8")
    cross = _first_cross()
    _write_payload(xchg, world_delta, [_cls("HK:0700", cross_themes=[{"theme_id": cross, "role": "core"}])])
    assert _commit(xchg, market, FakeCloud(cloud), skip_pull=True) == 0
    assert _stock(cloud["db_path"], "HK:0700")["asset_kind"] == "non_us_equity"
    assert _cross_of(cloud["db_path"], "HK:0700") == []


def test_commit_outbox_quarantines_bad_rows_locally(cloud, tmp_path):
    xchg, market, delta = _step_a(tmp_path)
    _write_payload(xchg, delta, [_cls("ABBNY", l2_id="no_such_l2"), _cls("NEWCO")])
    fake = FakeCloud(cloud)
    assert _commit(xchg, market, fake, skip_pull=True) == 0
    env = fake.calls[0][1]
    assert "ABBNY" not in {r["symbol"] for r in env["rows"]}
    assert "ABBNY" in env["local_summary"]["local_quarantined"]
    assert _stock(cloud["db_path"], "ABBNY") is None
    assert _stock(cloud["db_path"], "NEWCO") is not None


def test_commit_outbox_rejects_locked_agreement_status(cloud, tmp_path):
    xchg, market, delta = _step_a(tmp_path)
    _write_payload(xchg, delta, [_cls("ABBNY", agreement_status="locked"), _cls("NEWCO")])
    fake = FakeCloud(cloud)
    assert _commit(xchg, market, fake, skip_pull=True) == 0
    assert "ABBNY" in fake.calls[0][1]["local_summary"]["local_quarantined"]
    assert _stock(cloud["db_path"], "ABBNY") is None


def test_commit_outbox_broken_dictionary_refuses_before_cloud(cloud, tmp_path):
    xchg, market, delta = _step_a(tmp_path)
    _write_payload(xchg, delta, [_cls("ABBNY")])
    broken = _read(OVERRIDES)
    broken["locked_stocks"][0]["l2_id"] = "no_such_l2"
    bad_overrides = tmp_path / "overrides.json"
    bad_overrides.write_text(json.dumps(broken), encoding="utf-8")
    fake = FakeCloud(cloud)
    rc = sync.commit_outbox(exchange_dir=xchg, baskets_path=BASKETS, overrides_path=bad_overrides,
                            market_db_path=market, today=TODAY, ssh_runner=fake,
                            pull_runner=lambda: pytest.fail("no pull"))
    assert rc == 1 and fake.calls == []
    assert (xchg / "outbox" / "payload.json").exists()


def test_lock_busy_goes_to_pending_then_replay_is_idempotent(cloud, tmp_path):
    xchg, market, delta = _step_a(tmp_path)
    _write_payload(xchg, delta, [_cls("ABBNY"), _cls("NEWCO")])
    fake = FakeCloud(cloud, script=["busy"])
    assert _commit(xchg, market, fake) == 75
    assert not (xchg / "outbox" / "payload.json").exists()
    files = _pending(xchg)
    assert len(files) == 1
    sealed = _read(files[0])
    assert files[0].name == sealed["payload_id"] + ".json"
    assert stat.S_IMODE(files[0].stat().st_mode) == 0o600
    assert _stock(cloud["db_path"], "ABBNY") is None

    pulls = []
    rc = sync.replay_pending(exchange_dir=xchg, baskets_path=BASKETS, overrides_path=OVERRIDES,
                             market_db_path=market, ssh_runner=fake,
                             pull_runner=lambda: pulls.append(1) or 0)
    assert rc == 0 and pulls == [1]
    assert _pending(xchg) == []
    assert fake.calls[-1][1]["payload_id"] == sealed["payload_id"]
    assert fake.calls[-1][1]["rows"] == sealed["rows"]
    assert _stock(cloud["db_path"], "ABBNY") is not None

    history = _count_rows(cloud["db_path"], "stock_theme_history")
    rc, res = sync.apply_cloud_envelope(fake.calls[-1][1], db_path=cloud["db_path"],
                                        lock_path=cloud["lock_path"], send_telegram_fn=FakeTelegram())
    assert rc == 0 and res["replayed"] is True
    assert _count_rows(cloud["db_path"], "stock_theme_history") == history


def test_commit_then_dropped_reply_replays_as_recorded(cloud, tmp_path):
    xchg, market, delta = _step_a(tmp_path)
    _write_payload(xchg, delta, [_cls("ABBNY"), _cls("NEWCO")])
    fake = FakeCloud(cloud, script=["commit_then_drop"])
    assert _commit(xchg, market, fake) == 1
    assert len(_pending(xchg)) == 1
    commits = _count_rows(cloud["db_path"], "theme_sync_commits")
    stocks = _count_rows(cloud["db_path"], "stock_themes")

    rc = sync.replay_pending(exchange_dir=xchg, baskets_path=BASKETS, overrides_path=OVERRIDES,
                             market_db_path=market, ssh_runner=fake, pull_runner=lambda: 0)
    assert rc == 0 and _pending(xchg) == []
    assert _count_rows(cloud["db_path"], "theme_sync_commits") == commits
    assert _count_rows(cloud["db_path"], "stock_themes") == stocks


def test_transport_failure_goes_to_pending_and_next_commit_replays_first(cloud, tmp_path):
    xchg, market, delta = _step_a(tmp_path)
    _write_payload(xchg, delta, [_cls("ABBNY")])
    fake = FakeCloud(cloud, script=["down"])
    assert _commit(xchg, market, fake) == 1
    assert not (xchg / "outbox" / "payload.json").exists()
    first = _read(_pending(xchg)[0])

    _write_payload(xchg, delta, [_cls("NEWCO")])
    pulls = []
    assert _commit(xchg, market, fake, pulls) == 0
    assert _pending(xchg) == []
    assert [c[1]["payload_id"] for c in fake.calls[1:]][0] == first["payload_id"]
    assert len(fake.calls) == 3
    assert pulls == [1]
    assert _stock(cloud["db_path"], "ABBNY") is not None
    assert _stock(cloud["db_path"], "NEWCO") is not None


def test_replay_pending_keeps_files_on_failure_and_ignores_corrupt(cloud, tmp_path):
    xchg, market, delta = _step_a(tmp_path)
    _write_payload(xchg, delta, [_cls("ABBNY")])
    assert _commit(xchg, market, FakeCloud(cloud, script=["busy"])) == 75
    (xchg / "pending" / "garbage.json").write_text("{not json", encoding="utf-8")
    rc = sync.replay_pending(exchange_dir=xchg, baskets_path=BASKETS, overrides_path=OVERRIDES,
                             market_db_path=market, ssh_runner=FakeCloud(cloud, script=["busy"]),
                             pull_runner=lambda: pytest.fail("no pull"))
    assert rc == 75
    assert len(_pending(xchg)) == 2
    rc = sync.replay_pending(exchange_dir=xchg, baskets_path=BASKETS, overrides_path=OVERRIDES,
                             market_db_path=market, ssh_runner=FakeCloud(cloud), pull_runner=lambda: 0)
    assert rc == 0
    assert [p.name for p in _pending(xchg)] == ["garbage.json"]


def test_replay_pending_empty_is_noop(tmp_path):
    rc = sync.replay_pending(exchange_dir=tmp_path / "x", baskets_path=BASKETS, overrides_path=OVERRIDES,
                             market_db_path=tmp_path / "m.db",
                             ssh_runner=lambda *a: pytest.fail("no cloud call"),
                             pull_runner=lambda: pytest.fail("no pull"))
    assert rc == 0


def test_sync_config_pushes_99_baskets_and_49_locks(cloud, tmp_path):
    fake, pulls = FakeCloud(cloud), []
    rc = sync.sync_config(baskets_path=BASKETS, overrides_path=OVERRIDES,
                          market_db_path=tmp_path / "m.db", ssh_runner=fake,
                          pull_runner=lambda: pulls.append(1) or 0)
    assert rc == 0 and pulls == [1]
    env = fake.calls[0][1]
    assert env["rows"] == [] and env["payload_id"] == store.compute_payload_id([])
    with closing(sqlite3.connect(str(cloud["db_path"]))) as c:
        assert c.execute("SELECT COUNT(*) FROM theme_baskets").fetchone()[0] == 99
        assert c.execute("SELECT COUNT(*) FROM stock_themes WHERE source = 'override'").fetchone()[0] == 49


def test_sync_config_lock_busy_returns_75(cloud, tmp_path):
    rc = sync.sync_config(baskets_path=BASKETS, overrides_path=OVERRIDES,
                          market_db_path=tmp_path / "m.db", ssh_runner=FakeCloud(cloud, script=["busy"]),
                          pull_runner=lambda: pytest.fail("no pull"))
    assert rc == 75


def test_zero_row_heartbeat_end_to_end(cloud, tmp_path):
    xchg, market, delta = _step_a(tmp_path, missing=())
    assert (xchg / "outbox" / "payload.json").exists()
    fake = FakeCloud(cloud)
    assert _commit(xchg, market, fake, skip_pull=True) == 0
    env = fake.calls[0][1]
    assert {r["symbol"] for r in env["rows"]} == {r["symbol"] for r in delta["auto_rows"]}


def test_code_version_unchanged():
    from terminal.prosperity.version import code_version

    assert code_version() == "4c9da30bd5001102"
