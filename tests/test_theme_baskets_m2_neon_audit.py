"""Independent Step-5 invariant audit tests authored by Neon (separate from builder tests)."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import sqlite3
from pathlib import Path

import pytest

import src.data.data_health as health
from src.data import theme_basket_store as tbs


def _audit_baskets(min_l2: int = 4) -> dict:
    return {
        "schema_version": 1,
        "updated_at": "2026-11-27",
        "baskets": [
            {
                "basket_id": "pi_semi",
                "label_zh": "半导体链",
                "tier": "pi_bucket",
                "parent_l1_id": None,
                "default_pi_bucket": None,
                "sort_order": 10,
                "min_active_members": 0,
                "narrative_start_date": None,
                "inclusion_rule": "pi",
                "exclusion_rule": "none",
                "is_active": 1,
            },
            {
                "basket_id": "pi_other",
                "label_zh": "其他",
                "tier": "pi_bucket",
                "parent_l1_id": None,
                "default_pi_bucket": None,
                "sort_order": 20,
                "min_active_members": 0,
                "narrative_start_date": None,
                "inclusion_rule": "other",
                "exclusion_rule": "none",
                "is_active": 1,
            },
            {
                "basket_id": "l1_semi",
                "label_zh": "半导体与存储",
                "tier": "l1",
                "parent_l1_id": None,
                "default_pi_bucket": None,
                "sort_order": 10,
                "min_active_members": min_l2,
                "narrative_start_date": None,
                "inclusion_rule": "semi",
                "exclusion_rule": "none",
                "is_active": 1,
            },
            {
                "basket_id": "l2_mem",
                "label_zh": "存储芯片",
                "tier": "l2",
                "parent_l1_id": "l1_semi",
                "default_pi_bucket": "pi_semi",
                "sort_order": 10,
                "min_active_members": min_l2,
                "narrative_start_date": None,
                "inclusion_rule": "mem",
                "exclusion_rule": "none",
                "is_active": 1,
            },
            {
                "basket_id": "cross_ai",
                "label_zh": "AI算力全链",
                "tier": "cross",
                "parent_l1_id": None,
                "default_pi_bucket": None,
                "sort_order": 10,
                "min_active_members": 1,
                "narrative_start_date": "2023-01-01",
                "inclusion_rule": "ai",
                "exclusion_rule": "adopters",
                "is_active": 1,
            },
        ],
    }


def test_health_check_counts_only_active_extended_members_not_exited_stocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """North Star R4 & §3.6: stocks that dropped out of extended_membership keep
    their stock_themes rows (in_us_basket_calc=1), so _check_theme_baskets must
    intersect active extended_membership ∩ security_master(eligible=1) when
    checking min_active_members."""
    db_path = tmp_path / "market.db"
    syms = ["MU", "WDC", "STX", "SNDK", "NTAP"]

    with closing(sqlite3.connect(str(db_path))) as raw:
        raw.execute(
            "CREATE TABLE extended_membership (symbol TEXT NOT NULL, effective_from TEXT NOT NULL, "
            "effective_to TEXT, reason TEXT NOT NULL DEFAULT 'screener', PRIMARY KEY (symbol, effective_from))"
        )
        raw.execute(
            "CREATE TABLE security_master (symbol TEXT PRIMARY KEY, cik TEXT, company_name TEXT, "
            "eligible INTEGER NOT NULL, reason TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        # Only MU and WDC remain active in extended_membership; STX/SNDK/NTAP exited!
        for idx, sym in enumerate(syms):
            eff_to = None if idx < 2 else "2026-11-15"
            raw.execute(
                "INSERT INTO extended_membership (symbol, effective_from, effective_to) VALUES (?, '2026-08-21', ?)",
                (sym, eff_to),
            )
            raw.execute(
                "INSERT INTO security_master VALUES (?, ?, ?, 1, 'ok', '2026-11-21T00:00:00Z')",
                (sym, f"000000000{idx+1}", sym),
            )
        raw.commit()

    rows = [
        {
            "symbol": sym,
            "asset_kind": "us_equity",
            "in_us_basket_calc": 1,
            "pi_bucket": "pi_semi",
            "agreement_status": "dual_agreed",
            "l2_id": "l2_mem",
            "cross_themes": [{"theme_id": "cross_ai", "role": "core"}],
        }
        for sym in syms
    ]
    with closing(tbs.open_writer(db_path)) as conn:
        tbs.apply_envelope(
            conn,
            baskets_doc=_audit_baskets(min_l2=4),
            overrides_doc={"schema_version": 1, "locked_stocks": []},
            rows=rows,
            payload_id=tbs.compute_payload_id(rows),
            now=datetime.now(timezone.utc),
        )

    monkeypatch.setattr(health, "MARKET_DB", db_path)
    res = health._check_theme_baskets()
    assert res.status == "WARN"
    assert "l2_mem 2/4" in res.detail


def test_lock_promotes_out_of_basket_us_equity_and_rejects_non_us_or_superseded() -> None:
    """Locking an existing non-superseded us_equity with in_us_basket_calc=0 must set
    in_us_basket_calc=1 so cross_themes succeed; locking asset_kind!='us_equity' or
    is_superseded=1 must fail closed with ValueError."""
    now = datetime(2026, 11, 27, 10, 0, tzinfo=timezone.utc)
    rows = [
        {
            "symbol": "SMLL",
            "asset_kind": "us_equity",
            "in_us_basket_calc": 0,
            "pi_bucket": "pi_other",
            "agreement_status": "single_pass_weekly",
        },
        {
            "symbol": "HK:0700",
            "asset_kind": "non_us_equity",
            "in_us_basket_calc": 0,
            "pi_bucket": "pi_other",
            "agreement_status": "single_pass_weekly",
        },
        {
            "symbol": "PSTG",
            "asset_kind": "us_equity",
            "in_us_basket_calc": 0,
            "is_superseded": 1,
            "superseded_by": "P",
            "pi_bucket": "pi_semi",
            "agreement_status": "dual_agreed",
        },
    ]
    with closing(tbs.open_writer(":memory:")) as conn:
        tbs.apply_envelope(
            conn,
            baskets_doc=_audit_baskets(min_l2=1),
            overrides_doc={"schema_version": 1, "locked_stocks": []},
            rows=rows,
            payload_id=tbs.compute_payload_id(rows),
            now=now,
        )

        # Promoting SMLL via lock_private_symbol with a cross_theme must succeed
        tbs.lock_private_symbol(
            conn,
            "SMLL",
            l2_id="l2_mem",
            cross_themes=[{"theme_id": "cross_ai", "role": "core"}],
            business_note="private conviction",
            now=now,
        )
        st = conn.execute(
            "SELECT in_us_basket_calc, l1_id, l2_id, source FROM stock_themes WHERE symbol = 'SMLL'"
        ).fetchone()
        assert st == (1, "l1_semi", "l2_mem", "db_locked")
        ct = conn.execute(
            "SELECT theme_id, role, source FROM stock_cross_themes WHERE symbol = 'SMLL'"
        ).fetchall()
        assert ct == [("cross_ai", "core", "db_locked")]

        # Non-US equity and superseded US equity must be rejected cleanly
        with pytest.raises(ValueError):
            tbs.lock_private_symbol(conn, "HK:0700", l2_id="l2_mem", now=now)
        with pytest.raises(ValueError):
            tbs.lock_private_symbol(conn, "PSTG", l2_id="l2_mem", now=now)
