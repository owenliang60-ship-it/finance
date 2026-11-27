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
