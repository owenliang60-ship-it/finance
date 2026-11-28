"""Tests for M6 short-connection read-only layer (terminal/theme_baskets.py) and data_health alignment."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from src.data import data_health, theme_basket_store as store
from terminal import theme_baskets as tb

REPO_ROOT = Path(__file__).resolve().parents[1]
BASKETS_PATH = REPO_ROOT / "config" / "theme_baskets" / "baskets.json"
OVERRIDES_PATH = REPO_ROOT / "config" / "theme_baskets" / "overrides.json"


def _seed_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "market.db"
    baskets_doc = json.loads(BASKETS_PATH.read_text(encoding="utf-8"))
    overrides_doc = json.loads(OVERRIDES_PATH.read_text(encoding="utf-8"))
    l2_pi = {b["basket_id"]: b["default_pi_bucket"] for b in baskets_doc["baskets"] if b["tier"] == "l2"}
    ads_pi = l2_pi["digital_ads_social"]
    kind_pi = baskets_doc["asset_kind_default_pi_bucket"]

    with closing(store.open_writer(db_path)) as conn:
        conn.execute("CREATE TABLE extended_membership (symbol TEXT, effective_from TEXT, effective_to TEXT)")
        conn.execute("CREATE TABLE security_master (symbol TEXT PRIMARY KEY, cik TEXT, company_name TEXT, is_etf INTEGER, is_fund INTEGER, share_class_of TEXT, eligible INTEGER, reason TEXT)")
        conn.execute("CREATE TABLE historical_market_cap (symbol TEXT, date TEXT, market_cap REAL)")

        # NVDA: active in extended_membership
        conn.execute("INSERT INTO extended_membership VALUES ('NVDA', '2025-01-01', NULL)")
        conn.execute("INSERT INTO security_master VALUES ('NVDA', '0001045810', 'NVIDIA', 0, 0, NULL, 1, 'ok')")
        # PINS: dropped from extended_membership, rescued by 10-day >= $10B historical_market_cap
        conn.execute("INSERT INTO extended_membership VALUES ('PINS', '2025-01-01', '2026-11-20')")
        conn.execute("INSERT INTO security_master VALUES ('PINS', '0001506293', 'Pinterest', 0, 0, NULL, 1, 'ok')")
        conn.execute("INSERT INTO historical_market_cap VALUES ('PINS', '2026-11-26', 22000000000)")
        # SMALL: dropped and market cap < $10B -> NOT rescued
        conn.execute("INSERT INTO extended_membership VALUES ('SMALL', '2025-01-01', '2026-11-20')")
        conn.execute("INSERT INTO security_master VALUES ('SMALL', '0000000001', 'SmallCo', 0, 0, NULL, 1, 'ok')")
        conn.execute("INSERT INTO historical_market_cap VALUES ('SMALL', '2026-11-26', 5000000000)")
        # GOOG: share_class_of GOOGL (not active in extended_membership so gap check stays clean)
        conn.execute("INSERT INTO security_master VALUES ('GOOG', '0001652044', 'Alphabet C', 0, 0, 'GOOGL', 1, 'ok')")
        conn.execute("INSERT INTO security_master VALUES ('GOOGL', '0001652044', 'Alphabet A', 0, 0, NULL, 1, 'ok')")

        rows = [
            {
                "symbol": "PINS", "cik": "0001506293", "asset_kind": "us_equity",
                "in_us_basket_calc": 1, "l2_id": "digital_ads_social",
                "pi_bucket": ads_pi, "business_note": "视觉社交广告平台",
                "cross_themes": [], "agreement_status": "dual_agreed",
            },
            {
                "symbol": "SMALL", "cik": "0000000001", "asset_kind": "us_equity",
                "in_us_basket_calc": 1, "l2_id": "digital_ads_social",
                "pi_bucket": ads_pi, "business_note": "小市值广告",
                "cross_themes": [], "agreement_status": "dual_agreed",
            },
            {
                "symbol": "HK:0700", "cik": None, "asset_kind": "non_us_equity",
                "in_us_basket_calc": 0, "l2_id": "digital_ads_social",
                "pi_bucket": ads_pi, "business_note": "腾讯控股",
                "cross_themes": [], "agreement_status": "dual_agreed",
            },
            {
                "symbol": "CRYPTO:BTC", "cik": None, "asset_kind": "crypto",
                "in_us_basket_calc": 0, "l2_id": None,
                "pi_bucket": kind_pi["crypto"], "business_note": "比特币现货",
                "cross_themes": [], "agreement_status": "dual_agreed",
            },
            {
                "symbol": "BTC", "cik": None, "asset_kind": "etf",
                "in_us_basket_calc": 0, "l2_id": None,
                "pi_bucket": kind_pi["etf"], "business_note": "Grayscale BTC Mini Trust",
                "cross_themes": [], "agreement_status": "dual_agreed",
            },
        ]
        res = store.apply_envelope(
            conn,
            baskets_doc=baskets_doc,
            overrides_doc=overrides_doc,
            rows=rows,
            payload_id=store.compute_payload_id(rows),
        )
        assert not res.quarantined, res.quarantined
    return db_path


def test_parse_instrument_symbol_market_and_wrapper(tmp_path):
    db_path = _seed_db(tmp_path)
    # Crypto vs US ETF collision prevention
    assert tb.parse_instrument_symbol("BTC", market="CRYPTO", db_path=db_path) == tb.InstrumentRef(
        "CRYPTO:BTC", "equity", "BTC", "CRYPTO"
    )
    assert tb.parse_instrument_symbol("BTC", market="US", db_path=db_path) == tb.InstrumentRef(
        "BTC", "equity", "BTC", "US"
    )
    # HK zero-padding + prefix forms
    assert tb.parse_instrument_symbol("700", market="HK", db_path=db_path).lookup_key == "HK:0700"
    assert tb.parse_instrument_symbol("HKG:0700", market="US", db_path=db_path).lookup_key == "HK:0700"
    assert tb.parse_instrument_symbol("01810", market="HK", db_path=db_path).lookup_key == "HK:1810"
    # KR / CN
    assert tb.parse_instrument_symbol("000660", market="KR", db_path=db_path).lookup_key == "KR:000660"
    assert tb.parse_instrument_symbol("603259", market="CN", db_path=db_path).lookup_key == "CN:603259"
    # Option wrapper preservation
    ref = tb.parse_instrument_symbol("TQQQ PUT", market="US", db_path=db_path)
    assert ref.lookup_key == "TQQQ" and ref.wrapper_kind == "put"
    ref2 = tb.parse_instrument_symbol("NVDA LEAPS", market="US", db_path=db_path)
    assert ref2.lookup_key == "NVDA" and ref2.wrapper_kind == "leaps"
    # Share class resolution via security_master
    assert tb.parse_instrument_symbol("GOOG", market="US", db_path=db_path).lookup_key == "GOOGL"


def test_get_stock_themes_distinguishes_crypto_and_us_etf(tmp_path):
    db_path = _seed_db(tmp_path)
    crypto = tb.get_stock_themes("BTC", market="CRYPTO", db_path=db_path)
    etf = tb.get_stock_themes("BTC", market="US", db_path=db_path)
    hk = tb.get_stock_themes("0700", market="HK", db_path=db_path)
    nvda = tb.get_stock_themes("NVDA", market="US", db_path=db_path)
    assert crypto["symbol"] == "CRYPTO:BTC" and crypto["asset_kind"] == "crypto"
    assert etf["symbol"] == "BTC" and etf["asset_kind"] == "etf"
    assert crypto["pi_bucket"] != etf["pi_bucket"]
    assert hk["symbol"] == "HK:0700" and hk["asset_kind"] == "non_us_equity"
    assert nvda["symbol"] == "NVDA" and nvda["l2_id"] is not None


def test_get_basket_members_active_rescues_10d_10b_and_excludes_small(tmp_path):
    db_path = _seed_db(tmp_path)
    res = tb.get_basket_members("digital_ads_social", scope="active", db_path=db_path)
    assert res["pit_basis"] == "live"
    assert "PINS" in res["members"]
    assert "SMALL" not in res["members"]
    assert "HK:0700" not in res["members"]  # non_us_equity excluded from US basket calc


def test_get_basket_members_as_of_backfill_and_exact(tmp_path):
    db_path = _seed_db(tmp_path)
    # Before min(effective_from) -> backfill_approximate + narrative_start_date
    early = tb.get_basket_members("digital_ads_social", scope="as_of", as_of="2025-06-01", db_path=db_path)
    assert early["pit_basis"] == "backfill_approximate"
    assert "PINS" in early["members"]
    assert "narrative_start_date" in early
    # On/after effective_from -> history_exact (PINS dropped on 2026-11-20, so absent on 2026-12-01)
    later = tb.get_basket_members("digital_ads_social", scope="as_of", as_of="2026-12-01", db_path=db_path)
    assert later["pit_basis"] == "history_exact"
    assert "PINS" not in later["members"]


def test_adapter_pi_and_l2_classification_and_unknown_fallback(tmp_path, caplog):
    db_path = _seed_db(tmp_path)
    adapter = tb.ThemeBasketAdapter(db_path=db_path)
    assert adapter.is_available
    assert len(adapter.pi_bucket_order) == 15
    assert "其他" in adapter.pi_bucket_order
    assert adapter.classify_pi({"symbol": "BTC", "market": "CRYPTO"}) != adapter.classify_pi({"symbol": "BTC", "market": "US"})
    assert adapter.classify_pi({"symbol": "0700", "market": "HK"}) == adapter.classify_pi("HK:0700")
    assert adapter.classify_pi({"symbol": "UNKNOWN_XYZ", "market": "US"}) == "其他"
    assert any("UNKNOWN_XYZ" in rec.message for rec in caplog.records)


def test_data_health_uses_10d_10b_active_rule(tmp_path):
    db_path = _seed_db(tmp_path)
    with closing(sqlite3.connect(str(db_path))) as conn:
        # Lower min_active_members to 1 on digital_ads_social, 0 on others, so only PINS rescue decides PASS vs WARN
        conn.execute("UPDATE theme_baskets SET min_active_members = 0")
        conn.execute("UPDATE theme_baskets SET min_active_members = 1 WHERE basket_id = 'digital_ads_social'")
        conn.commit()
    with closing(sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        res = data_health._theme_baskets_result(conn)
    assert res.status == "PASS", res.detail


def test_code_version_unchanged():
    from terminal.prosperity.version import code_version

    assert code_version() == "4c9da30bd5001102"
