"""Tests for src/data/theme_basket_store.py — 股票主题分类 M2 五表存储引擎。

所有写入一律落在 ``:memory:`` 或 ``tmp_path``；涉及真实生产路径的用例先把
``sqlite3.connect`` 换成哨兵，守卫失效也只会抛 AssertionError，不会打开生产库。
"""
import sqlite3
import subprocess
from contextlib import closing
from pathlib import Path

import pytest

import config.settings as settings
from src.data import theme_basket_store as store

TS = "2026-11-27T00:00:00Z"

TABLES = {
    "theme_baskets", "stock_themes", "stock_cross_themes",
    "stock_theme_history", "theme_sync_commits",
}

TRIGGERS = {
    "trg_theme_baskets_no_delete",
    "trg_theme_baskets_identity",
    "trg_theme_baskets_refs_ins",
    "trg_theme_baskets_refs_upd",
    "trg_theme_baskets_deactivate_guard",
    "trg_stock_themes_validate_ins",
    "trg_stock_themes_validate_upd",
    "trg_stock_themes_exit_basket",
    "trg_stock_cross_themes_validate_ins",
    "trg_stock_cross_themes_validate_upd",
    "trg_stock_theme_history_no_update",
    "trg_stock_theme_history_no_delete",
}

EXPECTED_COLUMNS = {
    "theme_baskets": [
        "basket_id", "label_zh", "tier", "parent_l1_id", "default_pi_bucket",
        "sort_order", "min_active_members", "narrative_start_date",
        "inclusion_rule", "exclusion_rule", "is_active", "updated_at",
    ],
    "stock_themes": [
        "symbol", "cik", "asset_kind", "in_us_basket_calc", "is_superseded",
        "superseded_by", "l1_id", "l2_id", "pi_bucket", "business_note",
        "profile_sector", "profile_industry", "company_name_snapshot", "source",
        "agreement_status", "assigned_at", "updated_at",
    ],
    "stock_cross_themes": ["symbol", "theme_id", "role", "source", "assigned_at"],
    "stock_theme_history": [
        "id", "symbol", "asset_kind", "in_us_basket_calc", "is_superseded",
        "l1_id", "l2_id", "cross_themes_json", "pi_bucket", "source",
        "effective_from", "change_reason", "changed_at",
    ],
    "theme_sync_commits": [
        "payload_id", "taxonomy_sha256", "applied_at", "inserted_count",
        "updated_count", "quarantined_count",
    ],
}


# ---------------------------------------------------------------- helpers

@pytest.fixture
def conn():
    c = store.open_writer(":memory:")
    yield c
    c.close()


def _basket(conn, basket_id, tier, *, label=None, parent=None, pi=None,
            narrative=None, min_members=0, is_active=1):
    conn.execute(
        "INSERT INTO theme_baskets (basket_id, label_zh, tier, parent_l1_id, "
        "default_pi_bucket, min_active_members, narrative_start_date, "
        "inclusion_rule, exclusion_rule, is_active, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (basket_id, label or basket_id, tier, parent, pi, min_members, narrative,
         "in", "out", is_active, TS),
    )


@pytest.fixture
def seeded(conn):
    _basket(conn, "pi_a", "pi_bucket")
    _basket(conn, "pi_b", "pi_bucket")
    _basket(conn, "l1_a", "l1")
    _basket(conn, "l1_b", "l1")
    _basket(conn, "l2_a", "l2", parent="l1_a", pi="pi_a")
    _basket(conn, "l2_b", "l2", parent="l1_b", pi="pi_b")
    _basket(conn, "x_a", "cross", narrative="2023-01-01")
    return conn


STOCK_DEFAULTS = dict(
    cik=None, asset_kind="us_equity", in_us_basket_calc=1, is_superseded=0,
    superseded_by=None, l1_id="l1_a", l2_id="l2_a", pi_bucket="pi_a",
    source="agy_llm", agreement_status="dual_agreed",
)


def _stock(conn, symbol="SYM", **kw):
    r = {**STOCK_DEFAULTS, **kw}
    conn.execute(
        "INSERT INTO stock_themes (symbol, cik, asset_kind, in_us_basket_calc, "
        "is_superseded, superseded_by, l1_id, l2_id, pi_bucket, source, "
        "agreement_status, assigned_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (symbol, r["cik"], r["asset_kind"], r["in_us_basket_calc"], r["is_superseded"],
         r["superseded_by"], r["l1_id"], r["l2_id"], r["pi_bucket"], r["source"],
         r["agreement_status"], TS, TS),
    )


def _cross(conn, symbol, theme_id="x_a", role="core", source="agy_llm"):
    conn.execute(
        "INSERT INTO stock_cross_themes (symbol, theme_id, role, source, assigned_at) "
        "VALUES (?,?,?,?,?)",
        (symbol, theme_id, role, source, TS),
    )


def _history(conn, symbol="SYM", row_id=None):
    conn.execute(
        "INSERT INTO stock_theme_history (id, symbol, asset_kind, in_us_basket_calc, "
        "is_superseded, l1_id, l2_id, cross_themes_json, pi_bucket, source, "
        "effective_from, change_reason, changed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (row_id, symbol, "us_equity", 1, 0, "l1_a", "l2_a", "[]", "pi_a", "agy_llm",
         TS[:10], "new_classification", TS),
    )


def _count(conn, table, where="1=1", params=()):
    return conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", params).fetchone()[0]


# ---------------------------------------------------------------- schema

class TestSchema:
    def test_open_writer_builds_schema_and_pragmas(self, tmp_path):
        db = tmp_path / "t.db"
        with closing(store.open_writer(db)) as c:
            kinds = c.execute("SELECT name, type FROM sqlite_master").fetchall()
            assert TABLES <= {n for n, t in kinds if t == "table"}
            assert {n for n, t in kinds if t == "trigger"} == TRIGGERS
            assert {"ux_stock_themes_active_cik", "ix_stock_theme_history_symbol_eff"} <= {
                n for n, t in kinds if t == "index"}
            assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
            assert c.execute("PRAGMA recursive_triggers").fetchone()[0] == 1
            assert c.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
            assert c.isolation_level is None
        # Re-opening an existing store is a no-op schema-wise.
        with closing(store.open_writer(db)) as c:
            assert _count(c, "sqlite_master", "type = 'trigger'") == 12

    def test_column_contract(self, conn):
        for table, cols in EXPECTED_COLUMNS.items():
            assert [r[1] for r in conn.execute(f"PRAGMA table_info({table})")] == cols

    def test_primary_keys_reject_null(self, conn):
        with pytest.raises(sqlite3.IntegrityError):
            _basket(conn, None, "pi_bucket", label="x")
        _basket(conn, "pi_a", "pi_bucket")
        with pytest.raises(sqlite3.IntegrityError):
            _stock(conn, None, in_us_basket_calc=0, l1_id=None, l2_id=None)

    def test_schema_failure_closes_connection(self, monkeypatch):
        opened = []
        real_connect = sqlite3.connect

        def spy(*args, **kwargs):
            c = real_connect(*args, **kwargs)
            opened.append(c)
            return c

        monkeypatch.setattr(store.sqlite3, "connect", spy)
        monkeypatch.setattr(store, "_SCHEMA", "CREATE TABLE broken (")
        with pytest.raises(sqlite3.Error):
            store.open_writer(":memory:")
        assert len(opened) == 1
        with pytest.raises(sqlite3.ProgrammingError):
            opened[0].execute("SELECT 1")


# ---------------------------------------------------------------- theme_baskets

class TestThemeBaskets:
    @pytest.mark.parametrize("kwargs", [
        dict(basket_id="bad", tier="l3"),
        dict(basket_id="bad", tier="l2"),
        dict(basket_id="bad", tier="l2", parent="l1_a"),
        dict(basket_id="bad", tier="l2", pi="pi_a"),
        dict(basket_id="bad", tier="l1", parent="l1_a"),
        dict(basket_id="bad", tier="l1", pi="pi_a"),
        dict(basket_id="bad", tier="cross"),
        dict(basket_id="bad", tier="l1", narrative="2023-01-01"),
        dict(basket_id="bad", tier="l1", min_members=-1),
        dict(basket_id="bad", tier="l1", is_active=2),
        dict(basket_id="bad", tier="l1", label="l1_a"),
        dict(basket_id="l1_a", tier="l1", label="dup"),
    ])
    def test_check_constraints(self, seeded, kwargs):
        with pytest.raises(sqlite3.IntegrityError):
            _basket(seeded, **kwargs)

    def test_no_delete(self, seeded):
        with pytest.raises(sqlite3.IntegrityError, match="delete"):
            seeded.execute("DELETE FROM theme_baskets WHERE basket_id = 'x_a'")
        assert _count(seeded, "theme_baskets") == 7

    def test_identity_is_frozen(self, seeded):
        with pytest.raises(sqlite3.IntegrityError):
            seeded.execute("UPDATE theme_baskets SET tier = 'l1' WHERE basket_id = 'pi_b'")
        with pytest.raises(sqlite3.IntegrityError):
            seeded.execute("UPDATE theme_baskets SET basket_id = 'zz' WHERE basket_id = 'pi_b'")
        seeded.execute("UPDATE theme_baskets SET label_zh = 'renamed' WHERE basket_id = 'pi_b'")

    def test_insert_or_replace_is_blocked(self, seeded):
        with pytest.raises(sqlite3.IntegrityError):
            seeded.execute(
                "INSERT OR REPLACE INTO theme_baskets (basket_id, label_zh, tier, "
                "inclusion_rule, exclusion_rule, updated_at) "
                "VALUES ('pi_a', 'pi_a', 'pi_bucket', 'i', 'e', ?)", (TS,))
        assert _count(seeded, "theme_baskets") == 7

    @pytest.mark.parametrize("parent,pi", [
        ("pi_a", "pi_a"),      # parent is not an L1
        ("l1_a", "l1_b"),      # default bucket is not a pi_bucket
    ])
    def test_refs_insert_wrong_tier(self, seeded, parent, pi):
        with pytest.raises(sqlite3.IntegrityError):
            _basket(seeded, "l2_new", "l2", parent=parent, pi=pi)

    def test_refs_require_active_targets_for_active_rows(self, seeded):
        _basket(seeded, "l1_off", "l1", is_active=0)
        _basket(seeded, "pi_off", "pi_bucket", is_active=0)
        with pytest.raises(sqlite3.IntegrityError):
            _basket(seeded, "l2_new", "l2", parent="l1_off", pi="pi_a")
        with pytest.raises(sqlite3.IntegrityError):
            _basket(seeded, "l2_new", "l2", parent="l1_a", pi="pi_off")
        # An inactive L2 may hang under an inactive L1 (retired branch).
        _basket(seeded, "l2_off", "l2", parent="l1_off", pi="pi_off", is_active=0)
        seeded.execute("UPDATE theme_baskets SET inclusion_rule = 'edited' WHERE basket_id = 'l2_off'")
        # ...but cannot be reactivated while its parent stays inactive.
        with pytest.raises(sqlite3.IntegrityError):
            seeded.execute("UPDATE theme_baskets SET is_active = 1 WHERE basket_id = 'l2_off'")

    def test_refs_update(self, seeded):
        with pytest.raises(sqlite3.IntegrityError):
            seeded.execute("UPDATE theme_baskets SET parent_l1_id = 'pi_a' WHERE basket_id = 'l2_a'")
        with pytest.raises(sqlite3.IntegrityError):
            seeded.execute("UPDATE theme_baskets SET default_pi_bucket = 'l1_a' WHERE basket_id = 'l2_a'")
        seeded.execute("UPDATE theme_baskets SET parent_l1_id = 'l1_b' WHERE basket_id = 'l2_a'")

    def test_deactivate_guard_blocks_referenced_baskets(self, seeded):
        _stock(seeded, "AAA")
        _cross(seeded, "AAA")
        for basket_id in ("l1_a", "pi_a", "l2_a", "x_a", "l1_b", "pi_b"):
            with pytest.raises(sqlite3.IntegrityError):
                seeded.execute("UPDATE theme_baskets SET is_active = 0 WHERE basket_id = ?", (basket_id,))

    def test_deactivate_guard_allows_unreferenced_and_superseded(self, seeded):
        _stock(seeded, "OLD", in_us_basket_calc=0, is_superseded=1, superseded_by="NEW",
               l1_id="l1_b", l2_id="l2_b", pi_bucket="pi_b")
        for basket_id in ("l2_b", "l1_b", "pi_b"):
            seeded.execute("UPDATE theme_baskets SET is_active = 0 WHERE basket_id = ?", (basket_id,))
        assert _count(seeded, "theme_baskets", "is_active = 0") == 3


# ---------------------------------------------------------------- stock_themes

class TestStockThemes:
    @pytest.mark.parametrize("kwargs", [
        dict(symbol=""),
        dict(symbol=" AAA"),
        dict(asset_kind="bond"),
        dict(in_us_basket_calc=2),
        dict(asset_kind="etf"),
        dict(asset_kind="etf", in_us_basket_calc=0),
        dict(symbol="BTC", asset_kind="crypto", in_us_basket_calc=0, l1_id=None, l2_id=None, pi_bucket="pi_b"),
        dict(symbol=":BTC", asset_kind="crypto", in_us_basket_calc=0, l1_id=None, l2_id=None, pi_bucket="pi_b"),
        dict(symbol="0700", asset_kind="non_us_equity", in_us_basket_calc=0),
        dict(l1_id=None),
        dict(in_us_basket_calc=0, l1_id=None),
        dict(in_us_basket_calc=0, l2_id=None),
        dict(is_superseded=1, in_us_basket_calc=0),
        dict(in_us_basket_calc=0, superseded_by="NEW"),
        dict(in_us_basket_calc=0, is_superseded=1, superseded_by="SYM"),
        dict(is_superseded=1, superseded_by="NEW"),
        dict(source="manual"),
        dict(agreement_status="maybe"),
        dict(agreement_status="locked"),
        dict(source="override", agreement_status="dual_agreed"),
        dict(source="db_locked", agreement_status="needs_spot_check"),
    ])
    def test_check_constraints(self, seeded, kwargs):
        with pytest.raises(sqlite3.IntegrityError):
            _stock(seeded, **kwargs)

    def test_valid_shapes(self, seeded):
        _stock(seeded, "CRYPTO:BTC", asset_kind="crypto", in_us_basket_calc=0,
               l1_id=None, l2_id=None, pi_bucket="pi_b")
        _stock(seeded, "HK:0700", asset_kind="non_us_equity", in_us_basket_calc=0)
        _stock(seeded, "SPY", asset_kind="etf", in_us_basket_calc=0, l1_id=None, l2_id=None)
        _stock(seeded, "OUT", in_us_basket_calc=0, l1_id=None, l2_id=None)
        _stock(seeded, "LOCK", source="override", agreement_status="locked")
        _stock(seeded, "PRIV", source="db_locked", agreement_status="locked")
        assert _count(seeded, "stock_themes") == 6

    @pytest.mark.parametrize("kwargs", [
        dict(pi_bucket="l1_a"),
        dict(l2_id="l2_b"),
        dict(l1_id="l1_a", l2_id="l1_a"),
        dict(l1_id="nope", l2_id="nope"),
    ])
    def test_validate_insert_refs(self, seeded, kwargs):
        with pytest.raises(sqlite3.IntegrityError):
            _stock(seeded, **kwargs)

    def test_inactive_baskets_only_for_superseded_rows(self, seeded):
        _basket(seeded, "pi_off", "pi_bucket", is_active=0)
        _basket(seeded, "l1_c", "l1")
        _basket(seeded, "l2_c", "l2", parent="l1_c", pi="pi_a")
        seeded.execute("UPDATE theme_baskets SET is_active = 0 WHERE basket_id = 'l2_c'")
        with pytest.raises(sqlite3.IntegrityError):
            _stock(seeded, "A1", pi_bucket="pi_off")
        with pytest.raises(sqlite3.IntegrityError):
            _stock(seeded, "A2", in_us_basket_calc=0, l1_id="l1_c", l2_id="l2_c")
        _stock(seeded, "A3", in_us_basket_calc=0, is_superseded=1, superseded_by="X",
               pi_bucket="pi_off", l1_id="l1_c", l2_id="l2_c")

    def test_validate_update_refs(self, seeded):
        _stock(seeded, "AAA")
        with pytest.raises(sqlite3.IntegrityError):
            seeded.execute("UPDATE stock_themes SET l2_id = 'l2_b' WHERE symbol = 'AAA'")
        with pytest.raises(sqlite3.IntegrityError):
            seeded.execute("UPDATE stock_themes SET pi_bucket = 'x_a' WHERE symbol = 'AAA'")
        seeded.execute("UPDATE stock_themes SET l1_id = 'l1_b', l2_id = 'l2_b' WHERE symbol = 'AAA'")

    def test_active_cik_partial_unique(self, seeded):
        _stock(seeded, "GOOG", cik="0001652044")
        with pytest.raises(sqlite3.IntegrityError):
            _stock(seeded, "GOOGL", cik="0001652044")
        _stock(seeded, "GOOGL", cik="0001652044", in_us_basket_calc=0)
        _stock(seeded, "GOOGX", cik="0001652044", in_us_basket_calc=0, is_superseded=1, superseded_by="GOOG")
        _stock(seeded, "E1", cik="")
        _stock(seeded, "E2", cik="")
        _stock(seeded, "N1")
        _stock(seeded, "N2")

    def test_exit_basket_clears_cross_themes(self, seeded):
        _stock(seeded, "AAA")
        _stock(seeded, "BBB")
        _cross(seeded, "AAA")
        _cross(seeded, "BBB")
        seeded.execute("UPDATE stock_themes SET in_us_basket_calc = 0 WHERE symbol = 'AAA'")
        seeded.execute(
            "UPDATE stock_themes SET in_us_basket_calc = 0, is_superseded = 1, "
            "superseded_by = 'CCC' WHERE symbol = 'BBB'")
        assert _count(seeded, "stock_cross_themes") == 0


# ---------------------------------------------------------------- stock_cross_themes

class TestStockCrossThemes:
    def test_validate_insert(self, seeded):
        _stock(seeded, "AAA")
        _stock(seeded, "OUT", in_us_basket_calc=0)
        _basket(seeded, "x_off", "cross", narrative="2024-01-01", is_active=0)
        for symbol, theme in (("OUT", "x_a"), ("NONE", "x_a"), ("AAA", "l2_a"), ("AAA", "x_off")):
            with pytest.raises(sqlite3.IntegrityError):
                _cross(seeded, symbol, theme)
        with pytest.raises(sqlite3.IntegrityError):
            _cross(seeded, "AAA", role="adopter")
        _cross(seeded, "AAA")
        with pytest.raises(sqlite3.IntegrityError):
            _cross(seeded, "AAA")  # duplicate (symbol, theme_id)

    def test_validate_update(self, seeded):
        _stock(seeded, "AAA")
        _cross(seeded, "AAA")
        with pytest.raises(sqlite3.IntegrityError):
            seeded.execute("UPDATE stock_cross_themes SET theme_id = 'l2_a' WHERE symbol = 'AAA'")
        seeded.execute("UPDATE stock_cross_themes SET role = 'platform_giant' WHERE symbol = 'AAA'")


# ---------------------------------------------------------------- history

class TestHistoryAppendOnly:
    def test_no_update_no_delete_no_replace(self, conn):
        _history(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE stock_theme_history SET source = 'override'")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM stock_theme_history")
        row_id = conn.execute("SELECT id FROM stock_theme_history").fetchone()[0]
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT OR REPLACE INTO stock_theme_history (id, symbol, asset_kind, "
                "in_us_basket_calc, is_superseded, cross_themes_json, pi_bucket, source, "
                "effective_from, change_reason, changed_at) "
                "VALUES (?, 'SYM', 'us_equity', 0, 0, '[]', 'pi_a', 'agy_llm', 'x', 'y', 'z')",
                (row_id,))
        assert _count(conn, "stock_theme_history") == 1
        assert conn.execute("SELECT source FROM stock_theme_history").fetchone()[0] == "agy_llm"


class TestThemeSyncCommits:
    @pytest.mark.parametrize("payload,taxonomy,counts", [
        ("a" * 63, "b" * 64, (0, 0, 0)),
        ("a" * 64, "b" * 65, (0, 0, 0)),
        ("a" * 64, "b" * 64, (-1, 0, 0)),
        ("a" * 64, "b" * 64, (0, -1, 0)),
        ("a" * 64, "b" * 64, (0, 0, -1)),
    ])
    def test_checks(self, conn, payload, taxonomy, counts):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO theme_sync_commits VALUES (?,?,?,?,?,?)",
                         (payload, taxonomy, TS, *counts))


# ---------------------------------------------------------------- write guard

def _forbid_connect(monkeypatch):
    def sentinel(*args, **kwargs):
        raise AssertionError("sqlite3.connect must not run for a refused path")
    monkeypatch.setattr(store.sqlite3, "connect", sentinel)


@pytest.fixture
def fake_prod(tmp_path, monkeypatch):
    prod = tmp_path / "prod" / "market.db"
    prod.parent.mkdir()
    prod.write_bytes(b"")
    monkeypatch.setattr(settings, "MARKET_DB_PATH", prod)
    monkeypatch.delenv("FINANCE_ENV", raising=False)
    return prod


class TestWriteGuard:
    def test_refuses_production_path_before_connect(self, fake_prod, monkeypatch):
        _forbid_connect(monkeypatch)
        with pytest.raises(store.ProductionWriteRefused):
            store.open_writer(fake_prod)
        with pytest.raises(store.ProductionWriteRefused):
            store.open_writer(str(fake_prod))
        assert issubclass(store.ProductionWriteRefused, PermissionError)

    def test_refuses_symlink_to_production(self, fake_prod, tmp_path, monkeypatch):
        link = tmp_path / "link.db"
        link.symlink_to(fake_prod)
        _forbid_connect(monkeypatch)
        with pytest.raises(store.ProductionWriteRefused):
            store.open_writer(link)

    @pytest.mark.parametrize("cloud_authoritative,env", [
        (True, None), (False, "cloud"), (True, "local"),
    ])
    def test_needs_both_cloud_conditions(self, fake_prod, monkeypatch, cloud_authoritative, env):
        if env is not None:
            monkeypatch.setenv("FINANCE_ENV", env)
        _forbid_connect(monkeypatch)
        with pytest.raises(store.ProductionWriteRefused):
            store.open_writer(fake_prod, cloud_authoritative=cloud_authoritative)

    def test_cloud_authoritative_writer_allowed(self, fake_prod, monkeypatch):
        monkeypatch.setenv("FINANCE_ENV", "cloud")
        with closing(store.open_writer(fake_prod, cloud_authoritative=True)) as c:
            assert _count(c, "sqlite_master", "type = 'table' AND name = 'theme_baskets'") == 1

    def test_memory_and_scratch_paths_allowed(self, fake_prod, tmp_path):
        with closing(store.open_writer(":memory:")) as c:
            assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        with closing(store.open_writer(tmp_path / "scratch.db")):
            pass
        assert fake_prod.stat().st_size == 0

    def test_refuses_main_repo_db_from_linked_worktree(self, tmp_path, monkeypatch):
        main = tmp_path / "main"
        gitdir = main / ".git" / "worktrees" / "wt"
        gitdir.mkdir(parents=True)
        (gitdir / "commondir").write_text("../..\n")
        (main / "data").mkdir()
        main_db = main / "data" / "market.db"
        main_db.write_bytes(b"")
        wt = tmp_path / "wt"
        wt.mkdir()
        (wt / ".git").write_text(f"gitdir: {gitdir}\n")
        monkeypatch.setattr(store, "_REPO_ROOT", wt)
        # The worktree's own data/market.db is NOT a symlink here, so only the
        # commondir resolution can find the main checkout's production db.
        monkeypatch.setattr(settings, "MARKET_DB_PATH", wt / "data" / "market.db")
        monkeypatch.delenv("FINANCE_ENV", raising=False)
        _forbid_connect(monkeypatch)
        with pytest.raises(store.ProductionWriteRefused):
            store.open_writer(main_db)

    def test_unparseable_worktree_git_file_fails_closed(self, tmp_path, monkeypatch):
        wt = tmp_path / "wt"
        wt.mkdir()
        (wt / ".git").write_text("not a gitdir pointer\n")
        monkeypatch.setattr(store, "_REPO_ROOT", wt)
        monkeypatch.setattr(settings, "MARKET_DB_PATH", wt / "data" / "market.db")
        _forbid_connect(monkeypatch)
        with pytest.raises(store.ProductionWriteRefused):
            store.open_writer(tmp_path / "scratch.db")

    def test_real_local_production_db_is_refused(self, monkeypatch):
        """真实生产路径（worktree 软链 + git common dir 推出的主仓路径）都在连接前被拒。"""
        monkeypatch.delenv("FINANCE_ENV", raising=False)
        _forbid_connect(monkeypatch)
        common = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=Path(__file__).resolve().parent, capture_output=True, text=True, check=True,
        ).stdout.strip()
        main_db = Path(common).parent / "data" / "market.db"
        for path in (Path(settings.MARKET_DB_PATH), main_db):
            with pytest.raises(store.ProductionWriteRefused):
                store.open_writer(path)


# ================================================================ Task 2: envelope

import copy
import json
import re
from datetime import datetime, timedelta, timezone

REPO = Path(__file__).resolve().parents[1]
NOW1 = datetime(2026, 11, 29, 2, 0, 0, tzinfo=timezone.utc)
NOW2 = NOW1 + timedelta(days=7)
NOW3 = NOW2 + timedelta(days=7)


def _real_docs():
    baskets = json.loads((REPO / "config/theme_baskets/baskets.json").read_text(encoding="utf-8"))
    overrides = json.loads((REPO / "config/theme_baskets/overrides.json").read_text(encoding="utf-8"))
    return baskets, overrides


def _b(basket_id, tier, parent=None, pi=None, narrative=None, is_active=1, label=None):
    return {
        "basket_id": basket_id, "label_zh": label or f"label {basket_id}", "tier": tier,
        "parent_l1_id": parent, "default_pi_bucket": pi, "sort_order": 0,
        "min_active_members": 0, "narrative_start_date": narrative,
        "inclusion_rule": "in", "exclusion_rule": "out", "positive_examples": [],
        "is_active": is_active,
    }


def _mini_docs(locked=()):
    baskets = {"schema_version": 1, "baskets": [
        _b("pi_a", "pi_bucket"), _b("pi_b", "pi_bucket"),
        _b("l1_a", "l1"), _b("l1_b", "l1"),
        _b("l2_a", "l2", parent="l1_a", pi="pi_a"),
        _b("l2_b", "l2", parent="l1_b", pi="pi_b"),
        _b("x_a", "cross", narrative="2023-01-01"),
        _b("x_b", "cross", narrative="2024-01-01"),
    ]}
    overrides = {"schema_version": 1, "locked_stocks": [dict(x) for x in locked]}
    return baskets, overrides


def _lock(symbol, l2_id="l2_a", cross=(), rationale=None):
    return {"symbol": symbol, "l2_id": l2_id,
            "cross_themes": [{"theme_id": t, "role": r} for t, r in cross],
            "rationale": rationale if rationale is not None else f"why {symbol}"}


def _row(symbol, **kw):
    r = {"symbol": symbol, "asset_kind": "us_equity", "in_us_basket_calc": 1,
         "pi_bucket": "pi_a", "agreement_status": "dual_agreed", "l2_id": "l2_a"}
    r.update(kw)
    return r


def _apply(conn, baskets, overrides, rows=(), now=NOW1):
    rows = list(rows)
    return store.apply_envelope(conn, baskets_doc=baskets, overrides_doc=overrides, rows=rows,
                                payload_id=store.compute_payload_id(rows), now=now)


def _stock_row(conn, symbol):
    conn.row_factory = sqlite3.Row
    try:
        r = conn.execute("SELECT * FROM stock_themes WHERE symbol = ?", (symbol,)).fetchone()
        return dict(r) if r else None
    finally:
        conn.row_factory = None


def _cross_rows(conn, symbol):
    return conn.execute(
        "SELECT theme_id, role, source FROM stock_cross_themes WHERE symbol = ? ORDER BY theme_id",
        (symbol,)).fetchall()


def _hist(conn, symbol=None):
    sql = "SELECT symbol, change_reason, l1_id, l2_id, cross_themes_json, source, effective_from, changed_at FROM stock_theme_history"
    if symbol is None:
        return conn.execute(sql + " ORDER BY id").fetchall()
    return conn.execute(sql + " WHERE symbol = ? ORDER BY id", (symbol,)).fetchall()


def _db_state(conn):
    return {t: conn.execute(f"SELECT * FROM {t}").fetchall() for t in sorted(TABLES)}


class TestHashes:
    def test_payload_id_is_order_independent_and_deterministic(self):
        a = [_row("BBB"), _row("AAA")]
        b = [_row("AAA"), _row("BBB")]
        assert store.compute_payload_id(a) == store.compute_payload_id(b)
        assert re.fullmatch(r"[0-9a-f]{64}", store.compute_payload_id(a))
        assert store.compute_payload_id([]) == store.compute_payload_id(())
        assert store.compute_payload_id([]) != store.compute_payload_id(a)
        assert store.compute_payload_id([_row("AAA", business_note="x")]) != \
            store.compute_payload_id([_row("AAA", business_note="y")])

    def test_taxonomy_sha256(self):
        baskets, overrides = _mini_docs()
        h1 = store.compute_taxonomy_sha256(baskets, overrides)
        assert re.fullmatch(r"[0-9a-f]{64}", h1)
        assert h1 == store.compute_taxonomy_sha256(copy.deepcopy(baskets), copy.deepcopy(overrides))
        baskets["baskets"][0]["label_zh"] = "changed"
        assert store.compute_taxonomy_sha256(baskets, overrides) != h1


class TestApplyRealConfig:
    def test_real_m1_config_loads_and_is_idempotent(self):
        baskets, overrides = _real_docs()
        with closing(store.open_writer(":memory:")) as c:
            r1 = _apply(c, baskets, overrides, now=NOW1)
            assert _count(c, "theme_baskets") == 99
            assert _count(c, "stock_themes") == 49
            assert _count(c, "stock_themes", "source = 'override' AND agreement_status = 'locked'") == 49
            assert _count(c, "stock_theme_history") == 49
            assert _count(c, "stock_theme_history", "change_reason = 'override_lock'") == 49
            assert _count(c, "theme_sync_commits") == 1
            assert (r1.replayed, r1.inserted, r1.updated, r1.quarantined, r1.history_appended) == \
                (False, 49, 0, [], 49)
            assert r1.taxonomy_sha256 == store.compute_taxonomy_sha256(baskets, overrides)
            cross_total = sum(len(s["cross_themes"]) for s in overrides["locked_stocks"])
            assert _count(c, "stock_cross_themes", "source = 'override'") == cross_total

            msft = _stock_row(c, "MSFT")
            assert (msft["l1_id"], msft["l2_id"], msft["pi_bucket"]) == \
                ("l1_ai_cloud_hw", "hyperscale_ai_cloud", "pi_ai_cloud")
            assert msft["business_note"] == "Azure云与AI资本开支为核心定价锚"
            assert msft["cik"] is None  # no security_master in this db
            assert _hist(c, "MSFT")[0][4] == (
                '[{"role":"platform_giant","theme_id":"cross_ai_compute_infra"},'
                '{"role":"platform_giant","theme_id":"cross_ai_software_agents"}]')
            assert _hist(c, "AAPL")[0][4] == "[]"
            assert _hist(c, "AAPL")[0][6:] == ("2026-11-29", "2026-11-29T02:00:00Z")
            baskets_before = c.execute("SELECT * FROM theme_baskets").fetchall()

            r2 = _apply(c, baskets, overrides, now=NOW2)
            assert (r2.replayed, r2.inserted, r2.updated, r2.history_appended) == (True, 0, 0, 0)
            assert _count(c, "stock_theme_history") == 49
            assert _count(c, "theme_sync_commits") == 1
            assert c.execute("SELECT applied_at FROM theme_sync_commits").fetchone()[0] == \
                "2026-12-06T02:00:00Z"
            # Unchanged dictionary rows are not rewritten (updated_at stays put).
            assert c.execute("SELECT * FROM theme_baskets").fetchall() == baskets_before


class TestApplyEnvelope:
    def test_preflight_errors_write_nothing(self, tmp_path):
        baskets, overrides = _mini_docs()
        rows = [_row("AAA")]
        db = tmp_path / "t.db"
        with closing(store.open_writer(db)) as c:
            empty = _db_state(c)
            pid = store.compute_payload_id(rows)
            cases = [
                dict(baskets_doc=baskets, overrides_doc=overrides, rows=rows, payload_id="0" * 64, now=NOW1),
                dict(baskets_doc={**baskets, "schema_version": 2}, overrides_doc=overrides, rows=rows, payload_id=pid, now=NOW1),
                dict(baskets_doc=baskets, overrides_doc={**overrides, "schema_version": "1"}, rows=rows, payload_id=pid, now=NOW1),
                dict(baskets_doc=baskets, overrides_doc=overrides, rows=rows, payload_id=pid, now=datetime(2026, 11, 29)),
            ]
            for kwargs in cases:
                with pytest.raises(ValueError):
                    store.apply_envelope(c, **kwargs)
                assert not c.in_transaction
            assert _db_state(c) == empty

    def test_requires_store_pragmas(self):
        baskets, overrides = _mini_docs()
        with closing(sqlite3.connect(":memory:", isolation_level=None)) as raw:
            with pytest.raises(sqlite3.OperationalError, match="foreign_keys"):
                _apply(raw, baskets, overrides)

    def test_does_not_hijack_caller_transaction(self, conn):
        baskets, overrides = _mini_docs()
        _apply(conn, baskets, overrides)
        conn.execute("BEGIN")
        conn.execute("UPDATE theme_baskets SET sort_order = 5 WHERE basket_id = 'pi_a'")
        with pytest.raises(sqlite3.OperationalError):
            _apply(conn, baskets, overrides, now=NOW2)
        assert conn.in_transaction
        conn.execute("ROLLBACK")

    def test_now_defaults_to_utc(self, conn):
        baskets, overrides = _mini_docs()
        rows = []
        r = store.apply_envelope(conn, baskets_doc=baskets, overrides_doc=overrides, rows=rows,
                                 payload_id=store.compute_payload_id(rows))
        applied = conn.execute("SELECT applied_at FROM theme_sync_commits").fetchone()[0]
        assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", applied)
        assert r.replayed is False
        # Non-UTC aware datetimes are normalised to UTC.
        sgt = timezone(timedelta(hours=8))
        _apply(conn, baskets, overrides, now=datetime(2026, 12, 1, 9, 30, tzinfo=sgt))
        assert conn.execute("SELECT applied_at FROM theme_sync_commits").fetchone()[0] == \
            "2026-12-01T01:30:00Z"

    def test_three_rows_first_apply_then_replay(self, conn):
        baskets, overrides = _mini_docs()
        rows = [
            _row("AAA", cross_themes=[{"theme_id": "x_b", "role": "core"},
                                      {"theme_id": "x_a", "role": "platform_giant"}],
                 business_note="note A", cik="0000000001"),
            _row("BBB", l2_id="l2_b", pi_bucket="pi_b", agreement_status="single_pass_weekly"),
            _row("SPY", asset_kind="etf", in_us_basket_calc=0, l2_id=None, pi_bucket="pi_b"),
        ]
        r1 = _apply(conn, baskets, overrides, rows, now=NOW1)
        assert (r1.replayed, r1.inserted, r1.updated, r1.quarantined, r1.history_appended) == \
            (False, 3, 0, [], 3)
        aaa = _stock_row(conn, "AAA")
        assert (aaa["l1_id"], aaa["source"], aaa["agreement_status"], aaa["business_note"], aaa["cik"]) == \
            ("l1_a", "agy_llm", "dual_agreed", "note A", "0000000001")
        assert _stock_row(conn, "BBB")["l1_id"] == "l1_b"
        assert _stock_row(conn, "SPY")["l1_id"] is None
        assert _cross_rows(conn, "AAA") == [("x_a", "platform_giant", "agy_llm"), ("x_b", "core", "agy_llm")]
        assert _hist(conn, "AAA")[0][1] == "new_classification"
        assert _hist(conn, "AAA")[0][4] == \
            '[{"role":"platform_giant","theme_id":"x_a"},{"role":"core","theme_id":"x_b"}]'
        commit = conn.execute("SELECT * FROM theme_sync_commits").fetchone()
        assert commit[0] == r1.payload_id
        assert commit[2:] == ("2026-11-29T02:00:00Z", 3, 0, 0)

        # Same payload again (network retry), with an edited stock row in between:
        # the row segment is skipped, so the manual edit survives and nothing is re-inserted.
        conn.execute("UPDATE stock_themes SET business_note = 'edited' WHERE symbol = 'AAA'")
        r2 = _apply(conn, baskets, overrides, list(reversed(rows)), now=NOW2)
        assert r2.payload_id == r1.payload_id
        assert (r2.replayed, r2.inserted, r2.updated, r2.quarantined, r2.history_appended) == \
            (True, 0, 0, [], 0)
        assert _stock_row(conn, "AAA")["business_note"] == "edited"
        assert _count(conn, "stock_theme_history") == 3
        commit = conn.execute("SELECT * FROM theme_sync_commits").fetchone()
        assert commit[2:] == ("2026-12-06T02:00:00Z", 3, 0, 0)

    def test_bad_rows_are_quarantined_without_blocking_good_rows(self, conn):
        baskets, overrides = _mini_docs()
        _apply(conn, baskets, overrides, [_row("OLD", business_note="keep me")], now=NOW1)
        rows = [
            _row("GOOD1"),
            _row("BADL2", l2_id="nope"),
            _row("BADKEY", l1_id="l1_a"),
            {"symbol": "MISSING", "asset_kind": "us_equity"},
            _row("BADTYPE", in_us_basket_calc=True),
            _row("BADSTATUS", agreement_status="locked"),
            _row("BADCROSS", cross_themes=[{"theme_id": "l2_a", "role": "core"}]),
            _row("BADCROSSKEY", cross_themes=[{"theme_id": "x_a"}]),
            _row("OLD", l2_id="l2_b", pi_bucket="pi_b", business_note="overwrite attempt"),
            _row("GOOD2", l2_id="l2_b", pi_bucket="pi_b", cross_themes=[{"theme_id": "x_a", "role": "core"}]),
            _row("GOOD2"),
            _row("HK0700", asset_kind="non_us_equity", in_us_basket_calc=0),
        ]
        r = _apply(conn, baskets, overrides, rows, now=NOW2)
        assert r.inserted == 2
        bad = dict(r.quarantined)
        assert set(bad) == {"BADL2", "BADKEY", "MISSING", "BADTYPE", "BADSTATUS", "BADCROSS",
                            "BADCROSSKEY", "OLD", "GOOD2", "HK0700"}
        assert len(r.quarantined) == 10
        assert "exists" in bad["OLD"]
        assert "l1_id" in bad["BADKEY"]
        assert set(s for (s,) in conn.execute("SELECT symbol FROM stock_themes")) == {"OLD", "GOOD1", "GOOD2"}
        old = _stock_row(conn, "OLD")
        assert (old["l2_id"], old["business_note"]) == ("l2_a", "keep me")
        assert _cross_rows(conn, "GOOD2") == [("x_a", "core", "agy_llm")]
        assert _cross_rows(conn, "BADCROSS") == []
        assert _count(conn, "stock_theme_history", "symbol = 'OLD'") == 1
        assert conn.execute("SELECT inserted_count, quarantined_count FROM theme_sync_commits "
                            "WHERE payload_id = ?", (r.payload_id,)).fetchone() == (2, 10)
        assert not conn.in_transaction

    def test_new_row_plus_override_in_same_batch(self, conn):
        baskets, overrides = _mini_docs([_lock("NEW", l2_id="l2_b", cross=[("x_a", "core")])])
        rows = [_row("NEW", pi_bucket="pi_a", cross_themes=[{"theme_id": "x_b", "role": "core"}]),
                _row("NEW2", business_note=None)]
        overrides["locked_stocks"].append(_lock("NEW2", rationale="rationale 2"))
        r = _apply(conn, baskets, overrides, rows, now=NOW1)
        assert (r.inserted, r.quarantined, r.history_appended) == (2, [], 2)
        new = _stock_row(conn, "NEW")
        assert (new["l1_id"], new["l2_id"], new["pi_bucket"], new["source"], new["agreement_status"]) == \
            ("l1_b", "l2_b", "pi_a", "override", "locked")
        assert new["business_note"] == "why NEW"
        assert _cross_rows(conn, "NEW") == [("x_a", "core", "override")]
        assert _hist(conn, "NEW") == [("NEW", "override_lock", "l1_b", "l2_b",
                                       '[{"role":"core","theme_id":"x_a"}]', "override",
                                       "2026-11-29", "2026-11-29T02:00:00Z")]
        assert _stock_row(conn, "NEW2")["business_note"] == "rationale 2"

    def test_override_locks_existing_row_and_keeps_pi_bucket_and_note(self, conn):
        baskets, overrides = _mini_docs()
        _apply(conn, baskets, overrides, [_row("AAA", business_note="llm note",
                                                cross_themes=[{"theme_id": "x_b", "role": "core"}])])
        _, overrides = _mini_docs([_lock("AAA", l2_id="l2_b", cross=[("x_a", "platform_giant")])])
        r = _apply(conn, baskets, overrides, now=NOW2)
        assert (r.inserted, r.updated, r.history_appended) == (0, 1, 1)
        aaa = _stock_row(conn, "AAA")
        assert (aaa["l1_id"], aaa["l2_id"], aaa["pi_bucket"], aaa["business_note"], aaa["source"]) == \
            ("l1_b", "l2_b", "pi_a", "llm note", "override")
        assert _cross_rows(conn, "AAA") == [("x_a", "platform_giant", "override")]
        assert [h[1] for h in _hist(conn, "AAA")] == ["new_classification", "override_lock"]
        # Rerun: nothing changes, no extra history.
        r = _apply(conn, baskets, overrides, now=NOW3)
        assert (r.updated, r.history_appended) == (0, 0)

    def test_override_backfills_identity_from_security_master(self, conn):
        conn.execute("CREATE TABLE security_master (symbol TEXT PRIMARY KEY, cik TEXT, company_name TEXT, "
                     "eligible INTEGER NOT NULL)")
        conn.execute("INSERT INTO security_master VALUES ('AAA', '0000320193', 'Apple Inc.', 1)")
        baskets, overrides = _mini_docs([_lock("AAA"), _lock("ZZZ")])
        _apply(conn, baskets, overrides)
        aaa = _stock_row(conn, "AAA")
        assert (aaa["cik"], aaa["company_name_snapshot"]) == ("0000320193", "Apple Inc.")
        zzz = _stock_row(conn, "ZZZ")
        assert (zzz["cik"], zzz["company_name_snapshot"]) == (None, None)

    def test_removed_override_is_downgraded(self, conn):
        baskets, overrides = _mini_docs([_lock("AAA", cross=[("x_a", "core")]), _lock("BBB")])
        _apply(conn, baskets, overrides, now=NOW1)
        _, overrides = _mini_docs([_lock("BBB")])
        r = _apply(conn, baskets, overrides, now=NOW2)
        assert (r.updated, r.history_appended) == (1, 1)
        aaa = _stock_row(conn, "AAA")
        assert (aaa["source"], aaa["agreement_status"], aaa["l2_id"]) == ("agy_llm", "needs_spot_check", "l2_a")
        assert _cross_rows(conn, "AAA") == [("x_a", "core", "agy_llm")]
        assert _hist(conn, "AAA")[-1][1] == "override_removed"
        assert _stock_row(conn, "BBB")["source"] == "override"

    def test_reparent_cascades_l1(self, conn):
        baskets, overrides = _mini_docs([_lock("LCK")])
        _apply(conn, baskets, overrides, [_row("AAA"), _row("BBB", l2_id="l2_b", pi_bucket="pi_b")], now=NOW1)
        moved = copy.deepcopy(baskets)
        for b in moved["baskets"]:
            if b["basket_id"] == "l2_a":
                b["parent_l1_id"] = "l1_b"
        r = _apply(conn, moved, overrides, now=NOW2)
        assert (r.updated, r.history_appended) == (2, 2)
        for sym in ("AAA", "LCK"):
            assert _stock_row(conn, sym)["l1_id"] == "l1_b"
            assert _hist(conn, sym)[-1][1:3] == ("taxonomy_reparent", "l1_b")
        assert _count(conn, "stock_theme_history", "symbol = 'BBB'") == 1
        assert conn.execute("SELECT parent_l1_id, updated_at FROM theme_baskets WHERE basket_id = 'l2_a'"
                            ).fetchone() == ("l1_b", "2026-12-06T02:00:00Z")

    def test_dictionary_updates_and_deactivation_order(self, conn):
        baskets, overrides = _mini_docs()
        _apply(conn, baskets, overrides, now=NOW1)
        retired = copy.deepcopy(baskets)
        retired["baskets"].append(_b("l1_c", "l1"))
        retired["baskets"].append(_b("l2_c", "l2", parent="l1_c", pi="pi_b"))
        _apply(conn, retired, overrides, now=NOW2)
        # Retire a whole branch in one envelope: cross, L2, its L1 and an unused pi_bucket.
        for b in retired["baskets"]:
            if b["basket_id"] in ("x_b", "l2_c", "l1_c", "l2_b", "l1_b", "pi_b"):
                b["is_active"] = 0
            if b["basket_id"] == "pi_a":
                b["label_zh"] = "PI A renamed"
        _apply(conn, retired, overrides, now=NOW3)
        active = dict(conn.execute("SELECT basket_id, is_active FROM theme_baskets"))
        assert [k for k, v in sorted(active.items()) if v == 0] == ["l1_b", "l1_c", "l2_b", "l2_c", "pi_b", "x_b"]
        assert conn.execute("SELECT label_zh FROM theme_baskets WHERE basket_id = 'pi_a'").fetchone()[0] == \
            "PI A renamed"
        # Re-running with the branch still retired (metadata edit on a retired L2) works...
        for b in retired["baskets"]:
            if b["basket_id"] == "l2_c":
                b["inclusion_rule"] = "edited while retired"
        _apply(conn, retired, overrides, now=NOW3 + timedelta(days=1))
        # ...and reactivating the branch flips it back on in tier order.
        for b in retired["baskets"]:
            b["is_active"] = 1
        _apply(conn, retired, overrides, now=NOW3 + timedelta(days=2))
        assert _count(conn, "theme_baskets", "is_active = 0") == 0

    @pytest.mark.parametrize("mutate", [
        "override_unknown_l2", "override_unknown_cross", "override_duplicate", "tier_change",
        "deactivate_referenced", "duplicate_basket", "missing_rule", "bad_is_active", "bad_tier",
    ])
    def test_illegal_dictionary_or_override_rolls_back_everything(self, conn, mutate):
        baskets, overrides = _mini_docs([_lock("AAA")])
        _apply(conn, baskets, overrides, [_row("OLD")], now=NOW1)
        before = _db_state(conn)

        baskets = copy.deepcopy(baskets)
        overrides["locked_stocks"].append(_lock("BBB"))         # a legal change that must be undone
        baskets["baskets"].append(_b("l1_new", "l1"))            # likewise
        if mutate == "override_unknown_l2":
            overrides["locked_stocks"].append(_lock("CCC", l2_id="nope"))
        elif mutate == "override_unknown_cross":
            overrides["locked_stocks"].append(_lock("CCC", cross=[("nope", "core")]))
        elif mutate == "override_duplicate":
            overrides["locked_stocks"].append(_lock("AAA", l2_id="l2_b"))
        elif mutate == "tier_change":
            baskets["baskets"][0]["tier"] = "l1"
        elif mutate == "deactivate_referenced":
            baskets["baskets"][2]["is_active"] = 0               # l1_a, referenced by l2_a and stocks
        elif mutate == "duplicate_basket":
            baskets["baskets"].append(_b("pi_a", "pi_bucket", label="dup"))
        elif mutate == "missing_rule":
            del baskets["baskets"][1]["inclusion_rule"]
        elif mutate == "bad_is_active":
            baskets["baskets"][1]["is_active"] = 2
        elif mutate == "bad_tier":
            baskets["baskets"].append(_b("zz", "l3"))
        rows = [_row("NEWROW")]
        with pytest.raises((ValueError, KeyError, sqlite3.IntegrityError)):
            _apply(conn, baskets, overrides, rows, now=NOW2)
        assert not conn.in_transaction
        assert _db_state(conn) == before


class TestPrivateLocks:
    def test_lock_new_and_existing_and_unlock(self, conn):
        baskets, overrides = _mini_docs([_lock("PUB")])
        _apply(conn, baskets, overrides, [_row("AAA", business_note="llm")], now=NOW1)

        store.lock_private_symbol(conn, "PRIV", l2_id="l2_b", cross_themes=[{"theme_id": "x_a", "role": "core"}],
                                  business_note="private note", now=NOW2)
        priv = _stock_row(conn, "PRIV")
        assert (priv["source"], priv["agreement_status"], priv["l1_id"], priv["pi_bucket"],
                priv["business_note"], priv["asset_kind"], priv["in_us_basket_calc"]) == \
            ("db_locked", "locked", "l1_b", "pi_b", "private note", "us_equity", 1)
        assert _cross_rows(conn, "PRIV") == [("x_a", "core", "db_locked")]
        assert _hist(conn, "PRIV")[-1][1] == "db_lock"

        store.lock_private_symbol(conn, "AAA", l2_id="l2_b", now=NOW2)
        aaa = _stock_row(conn, "AAA")
        assert (aaa["source"], aaa["l2_id"], aaa["pi_bucket"], aaa["business_note"]) == \
            ("db_locked", "l2_b", "pi_a", "llm")
        assert [h[1] for h in _hist(conn, "AAA")] == ["new_classification", "db_lock"]

        # Same lock again is a no-op for history.
        store.lock_private_symbol(conn, "AAA", l2_id="l2_b", now=NOW3)
        assert _count(conn, "stock_theme_history", "symbol = 'AAA'") == 2
        # An explicit business_note on a private lock replaces the old note (no history: not a snapshot field).
        store.lock_private_symbol(conn, "AAA", l2_id="l2_b", business_note="manual", now=NOW3)
        assert _stock_row(conn, "AAA")["business_note"] == "manual"
        assert _count(conn, "stock_theme_history", "symbol = 'AAA'") == 2

        # A later envelope keeps the private lock (override segment does not touch it).
        _apply(conn, baskets, overrides, now=NOW3)
        assert _stock_row(conn, "PRIV")["source"] == "db_locked"

        store.unlock_private_symbol(conn, "PRIV", now=NOW3)
        priv = _stock_row(conn, "PRIV")
        assert (priv["source"], priv["agreement_status"]) == ("agy_llm", "needs_spot_check")
        assert _cross_rows(conn, "PRIV") == [("x_a", "core", "agy_llm")]
        assert _hist(conn, "PRIV")[-1][1] == "db_unlock"
        assert not conn.in_transaction

    def test_lock_refusals(self, conn):
        baskets, overrides = _mini_docs([_lock("PUB")])
        _apply(conn, baskets, overrides, [_row("AAA")], now=NOW1)
        before = _db_state(conn)
        with pytest.raises(ValueError, match="override"):
            store.lock_private_symbol(conn, "PUB", l2_id="l2_b", now=NOW2)
        with pytest.raises(ValueError):
            store.lock_private_symbol(conn, "NEW", l2_id="nope", now=NOW2)
        with pytest.raises(sqlite3.IntegrityError):
            store.lock_private_symbol(conn, "NEW", l2_id="l2_a",
                                      cross_themes=[{"theme_id": "l2_b", "role": "core"}], now=NOW2)
        for sym in ("PUB", "AAA", "NOPE"):
            with pytest.raises(ValueError):
                store.unlock_private_symbol(conn, sym, now=NOW2)
        with pytest.raises(ValueError):
            store.lock_private_symbol(conn, "NEW", l2_id="l2_a", now=datetime(2026, 12, 1))
        assert not conn.in_transaction
        assert _db_state(conn) == before

    def test_requires_store_pragmas(self):
        with closing(sqlite3.connect(":memory:", isolation_level=None)) as raw:
            with pytest.raises(sqlite3.OperationalError):
                store.lock_private_symbol(raw, "AAA", l2_id="l2_a")
            with pytest.raises(sqlite3.OperationalError):
                store.unlock_private_symbol(raw, "AAA")


class TestStaticCompatibility:
    FORBIDDEN = (
        "OR REPLACE", "->>", "IS DISTINCT FROM", "unixepoch", "subsec", "concat(", "format(",
        "string_agg", "RIGHT JOIN", "FULL JOIN", "FULL OUTER JOIN", "autocommit",
        "sqlite_errorcode", "datetime.UTC",
    )

    def test_source_has_no_cloud_incompatible_syntax(self):
        import ast
        src = (REPO / "src/data/theme_basket_store.py").read_text(encoding="utf-8")
        upper = src.upper()
        for token in self.FORBIDDEN:
            assert token.upper() not in upper, token
        imported = set()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Import):
                imported |= {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                imported |= {f"{node.module}.{a.name}" for a in node.names} | {node.module or ""}
        assert not any("market_store" in name for name in imported), imported
        aggregate_order_by = (r"\b(group_concat|json_group_array|json_group_object|string_agg|"
                              r"count|sum|total|avg|min|max)\s*\([^()]*ORDER BY")
        assert re.search(aggregate_order_by, src, re.IGNORECASE) is None
        assert re.search(aggregate_order_by, "SELECT group_concat(x ORDER BY x) FROM t", re.IGNORECASE)
        raises = re.findall(r"RAISE\s*\(", src)
        static = re.findall(r"RAISE\(ABORT, '[^']*'\)", src)
        assert raises and len(raises) == len(static)
        future = [line for line in src.splitlines() if line.startswith("from __future__")]
        assert future in ([], ["from __future__ import annotations"])

    def test_module_is_outside_prosperity_hash(self):
        from terminal.prosperity.version import CODE_VERSION_SOURCES, code_version
        rel = "src/data/theme_basket_store.py"
        assert not any(rel == s or rel.startswith(s + "/") for s in CODE_VERSION_SOURCES)
        assert code_version() == "4c9da30bd5001102"
