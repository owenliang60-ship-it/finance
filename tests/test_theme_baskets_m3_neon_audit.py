"""Neon Step-5 independent audit tests for M3 (scripts/theme_basket_sync.py).

Targets three real-world defects caught during Step-5 real-data smoke & code audit:
1. Sub-$10B US stocks in watchlist/Sheet (e.g. GRAL, IREN) have neither security_master
   nor company_profile rows in market.db, and must be classified as ('us_equity', 1)
   so Step B labels them — while placeholder 'TEST' stays ('other', 0) and common US
   ETFs not in security_master/report_concepts (e.g. TQQQ from 'TQQQ PUT', IBIT, GLD)
   become ('etf', 0).
2. Step C commit_outbox & format_weekly_summary must preserve D4 pivot_notes / pivot
   symbols and surface unclassified_symbols when Step B submits a strict subset of
   missing_symbols (and GateError on out-of-scope pivot_notes).
3. scan_sheet_tickers_tolerant must accept comma-formatted string shares ('1,250') and
   reject non-finite float('inf') / float('nan') shares.
"""
import io
import json
from pathlib import Path

import openpyxl
import pytest

from scripts import theme_basket_sync as sync


def _make_workbook_bytes(rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Summary_OSV"
    ws.append(["Stock Ticker", "Market", "Shares", "Last Price"])
    for row in rows:
        ws.append(list(row))
    buf = io.BytesIO()
    wb.save(buf)
    wb.close()
    return buf.getvalue()


def test_unprofiled_real_us_watchlist_stock_is_us_equity_not_other():
    """GRAL ($4.9B NASDAQ watchlist stock) has sm_row=None and profile_meta=None."""
    assert sync.classify_asset_kind(
        "GRAL", sm_row=None, profile_meta=None, extra_etfs=frozenset()
    ) == ("us_equity", 1)
    assert sync.classify_asset_kind(
        "TEST", sm_row=None, profile_meta=None, extra_etfs=frozenset()
    ) == ("other", 0)
    assert sync.classify_asset_kind(
        "TQQQ", sm_row=None, profile_meta=None, extra_etfs=frozenset()
    ) == ("etf", 0)
    assert sync.classify_asset_kind(
        "IBIT", sm_row=None, profile_meta=None, extra_etfs=frozenset()
    ) == ("etf", 0)


def test_scan_sheet_handles_comma_shares_and_rejects_nonfinite():
    xlsx = _make_workbook_bytes([
        ("NASDAQ:NVDA", "US", "1,250", "#N/A"),
        ("TQQQ PUT", "US", "10", "#N/A"),
        ("BADINF", "US", float("inf"), 10.0),
        ("BADNAN", "US", float("nan"), 10.0),
        ("CLOSED", "US", "0", 10.0),
    ])
    got = sync.scan_sheet_tickers_tolerant(xlsx)
    assert got == [("NASDAQ:NVDA", "US"), ("TQQQ PUT", "US")]


def test_commit_outbox_surfaces_pivot_notes_and_unclassified_in_telegram(tmp_path):
    """D4 pivot advice + partial missing_symbols subset must reach cloud Telegram summary."""
    repo_root = Path(__file__).resolve().parents[1]
    baskets_path = repo_root / "config" / "theme_baskets" / "baskets.json"
    overrides_path = repo_root / "config" / "theme_baskets" / "overrides.json"
    exchange_dir = tmp_path / "exchange"
    dirs = sync.ensure_exchange_dirs(exchange_dir)

    today = "2026-11-27"
    delta = {
        "schema_version": 1,
        "generated_date": today,
        "generated_at": "2026-11-27T01:50:00Z",
        "market_db_status": "OK",
        "sheet_status": "OK",
        "warnings": [],
        "counts": {
            "candidates": 3,
            "existing": 1,
            "missing_symbols": 2,
            "auto_rows": 0,
            "pivot_candidates": 1,
        },
        "missing_symbols": [
            {
                "symbol": "GRAL",
                "cik": None,
                "asset_kind": "us_equity",
                "in_us_basket_calc": 1,
                "profile_sector": None,
                "profile_industry": None,
                "company_name_snapshot": None,
                "sources": ["overlay"],
            },
            {
                "symbol": "SKIPME",
                "cik": None,
                "asset_kind": "us_equity",
                "in_us_basket_calc": 1,
                "profile_sector": None,
                "profile_industry": None,
                "company_name_snapshot": None,
                "sources": ["overlay"],
            },
        ],
        "auto_rows": [],
        "pivot_candidates": [
            {
                "symbol": "PIVOT1",
                "old_profile_sector": "Consumer Cyclical",
                "old_profile_industry": "Retail",
                "new_profile_sector": "Technology",
                "new_profile_industry": "Cloud Computing",
            }
        ],
    }
    delta["delta_sha256"] = sync.compute_delta_sha256(delta)
    sync.atomic_write_json(dirs["inbox"] / "delta.json", delta)

    # First verify out-of-scope pivot_notes symbol is rejected by hard gate
    bad_payload = {
        "schema_version": 1,
        "generated_date": today,
        "delta_sha256": delta["delta_sha256"],
        "classifications": [],
        "pivot_notes": [{"symbol": "NOT_IN_PIVOTS", "note": "spoofed"}],
    }
    sync.atomic_write_json(dirs["outbox"] / "payload.json", bad_payload)
    rc_bad = sync.commit_outbox(
        exchange_dir=exchange_dir,
        baskets_path=baskets_path,
        overrides_path=overrides_path,
        market_db_path=tmp_path / "dummy_local.db",
        today=today,
        ssh_runner=lambda *a, **k: pytest.fail("gate should block before SSH"),
        skip_pull=True,
    )
    assert rc_bad == sync.EXIT_FAIL

    # Now write valid payload classifying GRAL, omitting SKIPME, and advising on PIVOT1
    good_payload = {
        "schema_version": 1,
        "generated_date": today,
        "delta_sha256": delta["delta_sha256"],
        "classifications": [
            {
                "symbol": "GRAL",
                "l2_id": "diagnostics_genomics_tools",
                "cross_themes": [],
                "pi_bucket": "pi_healthcare",
                "business_note": "Liquid biopsy cancer screening",
                "agreement_status": "single_pass_weekly",
            }
        ],
        "pivot_notes": [
            {"symbol": "PIVOT1", "note": "建议复核是否转入云基础设施赛道"}
        ],
    }
    sync.atomic_write_json(dirs["outbox"] / "payload.json", good_payload)

    cloud_db = tmp_path / "cloud_market.db"
    lock_path = tmp_path / "writer.lock"
    captured_messages = []

    def fake_ssh(remote_args, stdin_bytes=None):
        assert remote_args == ["--apply-cloud-stdin"]
        env = json.loads(stdin_bytes.decode("utf-8"))
        rc, rep = sync.apply_cloud_envelope(
            env,
            db_path=cloud_db,
            lock_path=lock_path,
            allow_noncloud=True,
            send_telegram_fn=lambda msg, channel="private": captured_messages.append((channel, msg)),
        )
        return rc, json.dumps(rep).encode("utf-8"), b""

    rc_ok = sync.commit_outbox(
        exchange_dir=exchange_dir,
        baskets_path=baskets_path,
        overrides_path=overrides_path,
        market_db_path=tmp_path / "dummy_local.db",
        today=today,
        ssh_runner=fake_ssh,
        send_telegram=True,
        skip_pull=True,
    )
    assert rc_ok == sync.EXIT_OK
    assert len(captured_messages) == 1
    channel, text = captured_messages[0]
    assert channel == "private"
    assert "SKIPME" in text
    assert "PIVOT1" in text
    assert "建议复核是否转入云基础设施赛道" in text
