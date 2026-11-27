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
    ("TEST", None, None, ("other", 0)),
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
