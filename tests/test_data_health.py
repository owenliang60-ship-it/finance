"""Tests for data_health.py — 全链数据健康检查。"""
import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def mock_healthy_data(tmp_path, monkeypatch):
    """创建一个健康的 data/ 目录，所有检查都应 PASS。"""
    fundamental_dir = tmp_path / "fundamental"
    pool_dir = tmp_path / "pool"

    fundamental_dir.mkdir()
    pool_dir.mkdir()

    # 100 只股票
    symbols = [f"SYM{i:03d}" for i in range(100)]

    # universe.json
    universe = [{"symbol": s} for s in symbols]
    (pool_dir / "universe.json").write_text(json.dumps(universe))

    today = datetime.now().strftime("%Y-%m-%d")

    # 基本面 JSON
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    profiles = {"_meta": {"updated_at": now_str}}
    for s in symbols:
        profiles[s] = {"name": f"Company {s}"}
    (fundamental_dir / "profiles.json").write_text(json.dumps(profiles))

    # company.db
    db_path = tmp_path / "company.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE companies (symbol TEXT PRIMARY KEY)")
    conn.commit()
    conn.close()

    # market.db — 8 个数据表 + 价格/基本面/IV 数据
    market_db_path = tmp_path / "market.db"
    mconn = sqlite3.connect(str(market_db_path))
    mconn.execute("CREATE TABLE daily_price (symbol TEXT, date TEXT, close REAL, PRIMARY KEY(symbol, date))")
    mconn.execute("CREATE TABLE income_quarterly (symbol TEXT, date TEXT, revenue REAL, PRIMARY KEY(symbol, date))")
    mconn.execute("CREATE TABLE balance_sheet_quarterly (symbol TEXT, date TEXT, total_assets REAL, PRIMARY KEY(symbol, date))")
    mconn.execute("CREATE TABLE cash_flow_quarterly (symbol TEXT, date TEXT, operating_cash_flow REAL, PRIMARY KEY(symbol, date))")
    mconn.execute("CREATE TABLE metrics_quarterly (symbol TEXT, date TEXT, pe_ratio REAL, PRIMARY KEY(symbol, date))")
    mconn.execute("CREATE TABLE ratios_annual (symbol TEXT, date TEXT, roe REAL, PRIMARY KEY(symbol, date))")
    mconn.execute("CREATE TABLE iv_daily (symbol TEXT, date TEXT, iv_30d REAL, PRIMARY KEY(symbol, date))")
    mconn.execute("CREATE TABLE options_snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, snapshot_date TEXT)")
    # 插入价格数据 (所有 symbols, 今天)
    for s in symbols:
        mconn.execute("INSERT INTO daily_price VALUES (?, ?, ?)", (s, today, 103.0))
        mconn.execute("INSERT INTO income_quarterly VALUES (?, ?, ?)", (s, today, 1000.0))
        mconn.execute("INSERT INTO balance_sheet_quarterly VALUES (?, ?, ?)", (s, today, 5000.0))
        mconn.execute("INSERT INTO cash_flow_quarterly VALUES (?, ?, ?)", (s, today, 200.0))
    # 插入 IV 数据 (60% 覆盖 → 超过 50% 阈值 → PASS)
    for s in symbols[:60]:
        mconn.execute("INSERT INTO iv_daily VALUES (?, ?, ?)", (s, today, 0.35))
    mconn.commit()
    mconn.close()

    # Monkeypatch
    import src.data.data_health as health
    monkeypatch.setattr(health, "DATA_DIR", tmp_path)
    monkeypatch.setattr(health, "FUNDAMENTAL_DIR", fundamental_dir)
    monkeypatch.setattr(health, "POOL_DIR", pool_dir)
    monkeypatch.setattr(health, "UNIVERSE_FILE", pool_dir / "universe.json")
    monkeypatch.setattr(health, "COMPANY_DB", db_path)
    monkeypatch.setattr(health, "MARKET_DB", market_db_path)
    # _check_extended_coverage's denominator is current_base_universe(), not the pool
    # symbols above — the same 100 symbols are fully covered across the 3 quarterly
    # tables, so this check also PASSes in the "all-healthy" scenario.
    monkeypatch.setattr(health, "current_base_universe", lambda: list(symbols))

    # 主题篮子：最小主池表 + 3 篮子迷你字典 + 100 只健康分类行（_check_theme_baskets PASS）
    _seed_pool_tables(market_db_path, symbols)
    _seed_theme_store(market_db_path, symbols)

    return tmp_path


def _seed_pool_tables(db_path, symbols, *, extra=()):
    """extended_membership + security_master (subset of market_store._SCHEMA columns).

    `extra` = (symbol, effective_to, eligible) rows on top of the active eligible pool.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE extended_membership (symbol TEXT NOT NULL, effective_from TEXT NOT NULL, "
                     "effective_to TEXT, reason TEXT NOT NULL DEFAULT 'screener', "
                     "PRIMARY KEY (symbol, effective_from))")
        conn.execute("CREATE TABLE security_master (symbol TEXT PRIMARY KEY, cik TEXT, company_name TEXT, "
                     "eligible INTEGER NOT NULL, reason TEXT NOT NULL, updated_at TEXT NOT NULL)")
        rows = [(s, None, 1) for s in symbols] + list(extra)
        for s, effective_to, eligible in rows:
            conn.execute("INSERT INTO extended_membership (symbol, effective_from, effective_to) "
                         "VALUES (?, '2026-01-01', ?)", (s, effective_to))
            conn.execute("INSERT INTO security_master VALUES (?, NULL, ?, ?, 'test', '2026-01-01')",
                         (s, f"Company {s}", eligible))
        conn.commit()
    finally:
        conn.close()


def _mini_baskets(min_members=5):
    def basket(basket_id, tier, parent=None, pi=None, minimum=0):
        return {"basket_id": basket_id, "label_zh": basket_id, "tier": tier,
                "parent_l1_id": parent, "default_pi_bucket": pi, "sort_order": 0,
                "min_active_members": minimum, "narrative_start_date": None,
                "inclusion_rule": "in", "exclusion_rule": "out", "is_active": 1}
    return {"schema_version": 1, "baskets": [
        basket("pi_test", "pi_bucket"),
        basket("l1_test", "l1", minimum=min_members),
        basket("l2_test", "l2", parent="l1_test", pi="pi_test", minimum=min_members),
    ]}


def _seed_theme_store(db_path, symbols, *, now=None, min_members=5):
    from src.data import theme_basket_store as tbs
    rows = [{"symbol": s, "asset_kind": "us_equity", "in_us_basket_calc": 1, "pi_bucket": "pi_test",
             "agreement_status": "dual_agreed", "l2_id": "l2_test"} for s in symbols]
    conn = tbs.open_writer(db_path)
    try:
        result = tbs.apply_envelope(conn, baskets_doc=_mini_baskets(min_members),
                                    overrides_doc={"schema_version": 1, "locked_stocks": []},
                                    rows=rows, payload_id=tbs.compute_payload_id(rows), now=now)
    finally:
        conn.close()
    assert result.inserted == len(symbols) and not result.quarantined


@pytest.fixture
def mock_unhealthy_data(tmp_path, monkeypatch):
    """创建一个有问题的 data/ 目录，应触发 WARN/FAIL。"""
    fundamental_dir = tmp_path / "fundamental"
    pool_dir = tmp_path / "pool"

    fundamental_dir.mkdir()
    pool_dir.mkdir()

    # 只有 50 只股票 (偏少)
    symbols = [f"SYM{i:03d}" for i in range(50)]
    universe = [{"symbol": s} for s in symbols]
    (pool_dir / "universe.json").write_text(json.dumps(universe))
    # 设置 mtime 为 25 天前
    import os
    old_time = (datetime.now() - timedelta(days=25)).timestamp()
    os.utime(pool_dir / "universe.json", (old_time, old_time))

    # 基本面: 40天前更新
    old_str = (datetime.now() - timedelta(days=40)).strftime("%Y-%m-%d %H:%M:%S")
    profiles = {"_meta": {"updated_at": old_str}}
    for s in symbols[:35]:
        profiles[s] = {"name": f"Company {s}"}
    (fundamental_dir / "profiles.json").write_text(json.dumps(profiles))

    # company.db 不存在 → FAIL
    # market.db 不存在 → FAIL

    import src.data.data_health as health
    monkeypatch.setattr(health, "DATA_DIR", tmp_path)
    monkeypatch.setattr(health, "FUNDAMENTAL_DIR", fundamental_dir)
    monkeypatch.setattr(health, "POOL_DIR", pool_dir)
    monkeypatch.setattr(health, "UNIVERSE_FILE", pool_dir / "universe.json")
    monkeypatch.setattr(health, "COMPANY_DB", tmp_path / "company.db")
    monkeypatch.setattr(health, "MARKET_DB", tmp_path / "market.db")

    # Simulates a deployment before bootstrap has run — must WARN, not raise.
    def _raise_bootstrap():
        raise RuntimeError("extended_membership empty — run bootstrap first")
    monkeypatch.setattr(health, "current_base_universe", _raise_bootstrap)

    return tmp_path


class TestHealthCheck:
    def test_healthy_data_passes(self, mock_healthy_data):
        from src.data.data_health import health_check
        report = health_check()

        assert report.level == "PASS"
        assert all(c.status == "PASS" for c in report.checks)

    def test_unhealthy_data_fails(self, mock_unhealthy_data):
        from src.data.data_health import health_check
        report = health_check()

        assert report.level == "FAIL"
        # 应该有多个 WARN 和 FAIL
        statuses = [c.status for c in report.checks]
        assert "FAIL" in statuses

    def test_summary_format(self, mock_healthy_data):
        from src.data.data_health import health_check
        report = health_check()
        summary = report.summary()

        assert "PASS" in summary
        assert "池完整性" in summary
        assert "基本面覆盖率" in summary


class TestCheckPoolIntegrity:
    def test_normal_pool(self, mock_healthy_data):
        from src.data.data_health import _check_pool_integrity
        result = _check_pool_integrity()
        assert result.status == "PASS"
        assert "100" in result.detail

    def test_empty_pool(self, tmp_path, monkeypatch):
        import src.data.data_health as health
        pool_dir = tmp_path / "pool"
        pool_dir.mkdir()
        (pool_dir / "universe.json").write_text("[]")
        monkeypatch.setattr(health, "UNIVERSE_FILE", pool_dir / "universe.json")

        from src.data.data_health import _check_pool_integrity
        result = _check_pool_integrity()
        assert result.status == "FAIL"

    def test_below_pool_size_range_fails(self, tmp_path, monkeypatch):
        """Below settings.POOL_SIZE_RANGE[0] (70) → FAIL, not the old 90-150 WARN band."""
        import src.data.data_health as health
        pool_dir = tmp_path / "pool"
        pool_dir.mkdir()
        universe = [{"symbol": f"S{i}"} for i in range(50)]
        (pool_dir / "universe.json").write_text(json.dumps(universe))
        monkeypatch.setattr(health, "UNIVERSE_FILE", pool_dir / "universe.json")

        from src.data.data_health import _check_pool_integrity
        result = _check_pool_integrity()
        assert result.status == "FAIL"

    def test_209_pool_passes(self, tmp_path, monkeypatch):
        """R9/R14: today's real Core pool is 209 — must PASS under the widened
        Boss-decided POOL_SIZE_RANGE=(70, 260), not FAIL the old >200 bound."""
        import src.data.data_health as health
        pool_dir = tmp_path / "pool"
        pool_dir.mkdir()
        universe = [{"symbol": f"S{i}"} for i in range(209)]
        (pool_dir / "universe.json").write_text(json.dumps(universe))
        monkeypatch.setattr(health, "UNIVERSE_FILE", pool_dir / "universe.json")

        from src.data.data_health import _check_pool_integrity
        result = _check_pool_integrity()
        assert result.status == "PASS"
        assert "209" in result.detail

    def test_above_pool_size_range_fails(self, tmp_path, monkeypatch):
        """Above settings.POOL_SIZE_RANGE[1] (260) still FAILs."""
        import src.data.data_health as health
        pool_dir = tmp_path / "pool"
        pool_dir.mkdir()
        universe = [{"symbol": f"S{i}"} for i in range(261)]
        (pool_dir / "universe.json").write_text(json.dumps(universe))
        monkeypatch.setattr(health, "UNIVERSE_FILE", pool_dir / "universe.json")

        from src.data.data_health import _check_pool_integrity
        result = _check_pool_integrity()
        assert result.status == "FAIL"



class TestCheckCompanyDb:
    def test_valid_db(self, mock_healthy_data):
        from src.data.data_health import _check_company_db
        result = _check_company_db()
        assert result.status == "PASS"

    def test_missing_db(self, tmp_path, monkeypatch):
        import src.data.data_health as health
        monkeypatch.setattr(health, "COMPANY_DB", tmp_path / "nonexistent.db")

        from src.data.data_health import _check_company_db
        result = _check_company_db()
        assert result.status == "FAIL"

    def test_corrupted_db(self, tmp_path, monkeypatch):
        import src.data.data_health as health
        bad_db = tmp_path / "bad.db"
        bad_db.write_text("not a sqlite database")
        monkeypatch.setattr(health, "COMPANY_DB", bad_db)

        from src.data.data_health import _check_company_db
        result = _check_company_db()
        # sqlite3 可能不会立即报错，但查表会失败
        assert result.status in ("PASS", "FAIL")


class TestHealthReport:
    def test_escalation(self):
        from src.data.data_health import HealthReport, CheckResult

        report = HealthReport()
        assert report.level == "PASS"

        report.add(CheckResult("test1", "PASS", "ok"))
        assert report.level == "PASS"

        report.add(CheckResult("test2", "WARN", "warning"))
        assert report.level == "WARN"

        report.add(CheckResult("test3", "FAIL", "failure"))
        assert report.level == "FAIL"

    def test_summary_includes_all_checks(self):
        from src.data.data_health import HealthReport, CheckResult

        report = HealthReport()
        report.add(CheckResult("check_a", "PASS", "good"))
        report.add(CheckResult("check_b", "WARN", "needs attention"))

        summary = report.summary()
        assert "check_a" in summary
        assert "check_b" in summary


# ============ market.db 健康检查测试 ============


@pytest.fixture
def mock_market_db(tmp_path, monkeypatch):
    """创建一个包含完整 market.db 的测试环境，支持自定义数据量。"""
    symbols = [f"SYM{i:03d}" for i in range(100)]
    today = datetime.now().strftime("%Y-%m-%d")

    db_path = tmp_path / "market.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE daily_price (symbol TEXT, date TEXT, close REAL, PRIMARY KEY(symbol, date))")
    conn.execute("CREATE TABLE income_quarterly (symbol TEXT, date TEXT, revenue REAL, PRIMARY KEY(symbol, date))")
    conn.execute("CREATE TABLE balance_sheet_quarterly (symbol TEXT, date TEXT, total_assets REAL, PRIMARY KEY(symbol, date))")
    conn.execute("CREATE TABLE cash_flow_quarterly (symbol TEXT, date TEXT, operating_cash_flow REAL, PRIMARY KEY(symbol, date))")
    conn.execute("CREATE TABLE metrics_quarterly (symbol TEXT, date TEXT, pe_ratio REAL, PRIMARY KEY(symbol, date))")
    conn.execute("CREATE TABLE ratios_annual (symbol TEXT, date TEXT, roe REAL, PRIMARY KEY(symbol, date))")
    conn.execute("CREATE TABLE iv_daily (symbol TEXT, date TEXT, iv_30d REAL, PRIMARY KEY(symbol, date))")
    conn.execute("CREATE TABLE options_snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, snapshot_date TEXT)")
    # 全部 symbols 有价格和基本面
    for s in symbols:
        conn.execute("INSERT INTO daily_price VALUES (?, ?, ?)", (s, today, 103.0))
        conn.execute("INSERT INTO income_quarterly VALUES (?, ?, ?)", (s, today, 1000.0))
    # 60% IV 覆盖
    for s in symbols[:60]:
        conn.execute("INSERT INTO iv_daily VALUES (?, ?, ?)", (s, today, 0.35))
    conn.commit()
    conn.close()

    # 也要有 universe.json 以便加载 symbols
    pool_dir = tmp_path / "pool"
    pool_dir.mkdir()
    universe = [{"symbol": s} for s in symbols]
    (pool_dir / "universe.json").write_text(json.dumps(universe))

    import src.data.data_health as health
    monkeypatch.setattr(health, "MARKET_DB", db_path)
    monkeypatch.setattr(health, "UNIVERSE_FILE", pool_dir / "universe.json")

    return {"db_path": db_path, "symbols": symbols, "today": today, "tmp_path": tmp_path}


class TestCheckMarketDb:
    def test_all_tables_present(self, mock_market_db):
        from src.data.data_health import _check_market_db
        result = _check_market_db()
        assert result.status == "PASS"
        assert "正常" in result.detail

    def test_missing_db(self, tmp_path, monkeypatch):
        import src.data.data_health as health
        monkeypatch.setattr(health, "MARKET_DB", tmp_path / "nonexistent.db")

        from src.data.data_health import _check_market_db
        result = _check_market_db()
        assert result.status == "FAIL"
        assert "不存在" in result.detail

    def test_missing_tables(self, tmp_path, monkeypatch):
        import src.data.data_health as health
        db_path = tmp_path / "market.db"
        conn = sqlite3.connect(str(db_path))
        # 只建 3 个表
        conn.execute("CREATE TABLE daily_price (symbol TEXT, date TEXT, PRIMARY KEY(symbol, date))")
        conn.execute("CREATE TABLE income_quarterly (symbol TEXT, date TEXT, PRIMARY KEY(symbol, date))")
        conn.execute("CREATE TABLE iv_daily (symbol TEXT, date TEXT, PRIMARY KEY(symbol, date))")
        conn.commit()
        conn.close()
        monkeypatch.setattr(health, "MARKET_DB", db_path)

        from src.data.data_health import _check_market_db
        result = _check_market_db()
        assert result.status == "FAIL"
        assert "缺少" in result.detail

    def test_corrupted_db(self, tmp_path, monkeypatch):
        import src.data.data_health as health
        bad_db = tmp_path / "bad_market.db"
        bad_db.write_text("not a sqlite database")
        monkeypatch.setattr(health, "MARKET_DB", bad_db)

        from src.data.data_health import _check_market_db
        result = _check_market_db()
        assert result.status in ("PASS", "FAIL")


class TestCheckMdbPriceCoverage:
    def test_full_coverage(self, mock_market_db):
        from src.data.data_health import _check_mdb_price_coverage, _load_universe_symbols
        symbols = _load_universe_symbols()
        result = _check_mdb_price_coverage(symbols)
        assert result.status == "PASS"
        assert "100/100" in result.detail

    def test_low_coverage(self, tmp_path, monkeypatch):
        import src.data.data_health as health
        symbols = [f"SYM{i:03d}" for i in range(100)]

        db_path = tmp_path / "market.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE daily_price (symbol TEXT, date TEXT, PRIMARY KEY(symbol, date))")
        # 只有 50% 覆盖
        for s in symbols[:50]:
            conn.execute("INSERT INTO daily_price VALUES (?, ?)", (s, "2026-03-01"))
        conn.commit()
        conn.close()
        monkeypatch.setattr(health, "MARKET_DB", db_path)

        from src.data.data_health import _check_mdb_price_coverage
        result = _check_mdb_price_coverage(symbols)
        assert result.status == "FAIL"

    def test_no_symbols(self, mock_market_db):
        from src.data.data_health import _check_mdb_price_coverage
        result = _check_mdb_price_coverage([])
        assert result.status == "FAIL"


class TestCheckMdbPriceFreshness:
    def test_fresh_data(self, mock_market_db):
        from src.data.data_health import _check_mdb_price_freshness
        result = _check_mdb_price_freshness()
        assert result.status == "PASS"

    def test_stale_data(self, tmp_path, monkeypatch):
        import src.data.data_health as health
        db_path = tmp_path / "market.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE daily_price (symbol TEXT, date TEXT, PRIMARY KEY(symbol, date))")
        old_date = (datetime.now() - timedelta(days=20)).strftime("%Y-%m-%d")
        conn.execute("INSERT INTO daily_price VALUES (?, ?)", ("TEST", old_date))
        conn.commit()
        conn.close()
        monkeypatch.setattr(health, "MARKET_DB", db_path)

        from src.data.data_health import _check_mdb_price_freshness
        result = _check_mdb_price_freshness()
        assert result.status == "FAIL"

    def test_empty_table(self, tmp_path, monkeypatch):
        import src.data.data_health as health
        db_path = tmp_path / "market.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE daily_price (symbol TEXT, date TEXT, PRIMARY KEY(symbol, date))")
        conn.commit()
        conn.close()
        monkeypatch.setattr(health, "MARKET_DB", db_path)

        from src.data.data_health import _check_mdb_price_freshness
        result = _check_mdb_price_freshness()
        assert result.status == "FAIL"
        assert "无数据" in result.detail


class TestCheckMdbFundamentalCoverage:
    def test_full_coverage(self, mock_market_db):
        from src.data.data_health import _check_mdb_fundamental_coverage, _load_universe_symbols
        symbols = _load_universe_symbols()
        result = _check_mdb_fundamental_coverage(symbols)
        assert result.status == "PASS"

    def test_partial_coverage(self, tmp_path, monkeypatch):
        import src.data.data_health as health
        symbols = [f"SYM{i:03d}" for i in range(100)]

        db_path = tmp_path / "market.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE income_quarterly (symbol TEXT, date TEXT, PRIMARY KEY(symbol, date))")
        # 85% 覆盖 → WARN
        for s in symbols[:85]:
            conn.execute("INSERT INTO income_quarterly VALUES (?, ?)", (s, "2026-03-01"))
        conn.commit()
        conn.close()
        monkeypatch.setattr(health, "MARKET_DB", db_path)

        from src.data.data_health import _check_mdb_fundamental_coverage
        result = _check_mdb_fundamental_coverage(symbols)
        assert result.status == "WARN"


class TestCheckMdbIvCoverage:
    def test_good_coverage(self, mock_market_db):
        from src.data.data_health import _check_mdb_iv_coverage, _load_universe_symbols
        symbols = _load_universe_symbols()
        result = _check_mdb_iv_coverage(symbols)
        # 60% >= 50% → PASS
        assert result.status == "PASS"

    def test_low_coverage(self, tmp_path, monkeypatch):
        import src.data.data_health as health
        symbols = [f"SYM{i:03d}" for i in range(100)]

        db_path = tmp_path / "market.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE iv_daily (symbol TEXT, date TEXT, PRIMARY KEY(symbol, date))")
        # 只有 20% → FAIL (<30%)
        for s in symbols[:20]:
            conn.execute("INSERT INTO iv_daily VALUES (?, ?)", (s, "2026-03-01"))
        conn.commit()
        conn.close()
        monkeypatch.setattr(health, "MARKET_DB", db_path)

        from src.data.data_health import _check_mdb_iv_coverage
        result = _check_mdb_iv_coverage(symbols)
        assert result.status == "FAIL"

    def test_warn_coverage(self, tmp_path, monkeypatch):
        import src.data.data_health as health
        symbols = [f"SYM{i:03d}" for i in range(100)]

        db_path = tmp_path / "market.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE iv_daily (symbol TEXT, date TEXT, PRIMARY KEY(symbol, date))")
        # 40% → WARN (>=30%, <50%)
        for s in symbols[:40]:
            conn.execute("INSERT INTO iv_daily VALUES (?, ?)", (s, "2026-03-01"))
        conn.commit()
        conn.close()
        monkeypatch.setattr(health, "MARKET_DB", db_path)

        from src.data.data_health import _check_mdb_iv_coverage
        result = _check_mdb_iv_coverage(symbols)
        assert result.status == "WARN"


class TestCheckMdbIvFreshness:
    def test_fresh_data(self, mock_market_db):
        from src.data.data_health import _check_mdb_iv_freshness
        result = _check_mdb_iv_freshness()
        assert result.status == "PASS"

    def test_stale_data(self, tmp_path, monkeypatch):
        import src.data.data_health as health
        db_path = tmp_path / "market.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE iv_daily (symbol TEXT, date TEXT, PRIMARY KEY(symbol, date))")
        old_date = (datetime.now() - timedelta(days=20)).strftime("%Y-%m-%d")
        conn.execute("INSERT INTO iv_daily VALUES (?, ?)", ("TEST", old_date))
        conn.commit()
        conn.close()
        monkeypatch.setattr(health, "MARKET_DB", db_path)

        from src.data.data_health import _check_mdb_iv_freshness
        result = _check_mdb_iv_freshness()
        assert result.status == "FAIL"

    def test_missing_db(self, tmp_path, monkeypatch):
        import src.data.data_health as health
        monkeypatch.setattr(health, "MARKET_DB", tmp_path / "nonexistent.db")

        from src.data.data_health import _check_mdb_iv_freshness
        result = _check_mdb_iv_freshness()
        assert result.status == "FAIL"
        assert "不存在" in result.detail


# ============ Extended Primary Universe coverage 健康检查 (T14) ============


class TestCheckExtendedCoverage:
    """`_check_extended_coverage`: denominator is `current_base_universe()`
    (active Extended membership ∩ SM eligible, R3-P1-1) — a different, larger
    set than the Core `universe.json` pool. `settings.EXTENDED_COVERAGE_ENFORCE`
    gates whether under-threshold coverage is WARN (backfill in progress, never
    fails the build) or FAIL (Stop F, backfill declared complete)."""

    @staticmethod
    def _make_market_db(tmp_path, covered_symbols, today):
        db_path = tmp_path / "market.db"
        conn = sqlite3.connect(str(db_path))
        for table in ("income_quarterly", "balance_sheet_quarterly", "cash_flow_quarterly"):
            conn.execute(f"CREATE TABLE {table} (symbol TEXT, date TEXT, PRIMARY KEY(symbol, date))")
        for s in covered_symbols:
            for table in ("income_quarterly", "balance_sheet_quarterly", "cash_flow_quarterly"):
                conn.execute(f"INSERT INTO {table} VALUES (?, ?)", (s, today))
        conn.commit()
        conn.close()
        return db_path

    def test_full_coverage_passes(self, tmp_path, monkeypatch):
        import src.data.data_health as health
        symbols = [f"SYM{i:04d}" for i in range(1000)]
        today = datetime.now().strftime("%Y-%m-%d")
        db_path = self._make_market_db(tmp_path, symbols, today)

        monkeypatch.setattr(health, "MARKET_DB", db_path)
        monkeypatch.setattr(health, "current_base_universe", lambda: list(symbols))

        from src.data.data_health import _check_extended_coverage
        result = _check_extended_coverage()
        assert result.status == "PASS"
        assert "1000/1000" in result.detail

    def test_low_coverage_enforce_false_warns(self, tmp_path, monkeypatch):
        """eligible=1000, covered=202 (~20%), enforce=False → WARN, never FAIL —
        prevents a false gate before the production backfill completes."""
        import src.data.data_health as health
        symbols = [f"SYM{i:04d}" for i in range(1000)]
        today = datetime.now().strftime("%Y-%m-%d")
        db_path = self._make_market_db(tmp_path, symbols[:202], today)

        monkeypatch.setattr(health, "MARKET_DB", db_path)
        monkeypatch.setattr(health, "current_base_universe", lambda: list(symbols))
        monkeypatch.setattr(health, "EXTENDED_COVERAGE_ENFORCE", False)

        from src.data.data_health import _check_extended_coverage
        result = _check_extended_coverage()
        assert result.status == "WARN"
        assert "202/1000" in result.detail

    def test_low_coverage_enforce_true_fails(self, tmp_path, monkeypatch):
        """Same 202/1000 coverage, but enforce=True (post Stop F) → FAIL."""
        import src.data.data_health as health
        symbols = [f"SYM{i:04d}" for i in range(1000)]
        today = datetime.now().strftime("%Y-%m-%d")
        db_path = self._make_market_db(tmp_path, symbols[:202], today)

        monkeypatch.setattr(health, "MARKET_DB", db_path)
        monkeypatch.setattr(health, "current_base_universe", lambda: list(symbols))
        monkeypatch.setattr(health, "EXTENDED_COVERAGE_ENFORCE", True)

        from src.data.data_health import _check_extended_coverage
        result = _check_extended_coverage()
        assert result.status == "FAIL"
        assert "202/1000" in result.detail

    def test_sm_empty_warns_with_bootstrap_hint(self, tmp_path, monkeypatch):
        """SM/membership empty → current_base_universe() raises RuntimeError;
        the check must convert that to WARN, never let it propagate — health
        check has to run on a fresh deployment before bootstrap."""
        import src.data.data_health as health

        def _raise_bootstrap():
            raise RuntimeError("security_master empty — run bootstrap first")
        monkeypatch.setattr(health, "current_base_universe", _raise_bootstrap)

        from src.data.data_health import _check_extended_coverage
        result = _check_extended_coverage()
        assert result.status == "WARN"
        assert "bootstrap" in result.detail

    def test_included_in_health_check_report(self, mock_healthy_data):
        """Wired into the aggregate `health_check()` report, not just standalone."""
        from src.data.data_health import health_check
        report = health_check()
        names = [c.name for c in report.checks]
        assert "扩展覆盖率" in names


# ============ 主题篮子只读健康检查 ============


def _open_paths():
    """Absolute paths this process currently holds open."""
    import os
    import subprocess
    fd_dir = Path("/proc/self/fd")
    if fd_dir.is_dir():
        return {os.path.realpath(fd_dir / name) for name in os.listdir(fd_dir)}
    out = subprocess.run(["lsof", "-Fn", "-p", str(os.getpid())],
                         capture_output=True, text=True, check=True).stdout
    return {line[1:] for line in out.splitlines() if line.startswith("n")}


class TestCheckThemeBaskets:
    SYMBOLS = [f"SYM{i:03d}" for i in range(100)]

    @pytest.fixture
    def theme_db(self, tmp_path, monkeypatch):
        """Healthy theme store + pool tables; returns the db path (MARKET_DB patched)."""
        import src.data.data_health as health
        db = tmp_path / "market.db"
        _seed_pool_tables(db, self.SYMBOLS)
        _seed_theme_store(db, self.SYMBOLS)
        monkeypatch.setattr(health, "MARKET_DB", db)
        return db

    @staticmethod
    def _run():
        from src.data.data_health import _check_theme_baskets
        result = _check_theme_baskets()
        assert result.name == "主题篮子"
        assert result.status in ("PASS", "WARN")
        return result

    def test_healthy_passes(self, theme_db):
        result = self._run()
        assert result.status == "PASS", result.detail
        assert "100" in result.detail

    def test_missing_db_warns(self, tmp_path, monkeypatch):
        import src.data.data_health as health
        monkeypatch.setattr(health, "MARKET_DB", tmp_path / "market.db")
        result = self._run()
        assert result.status == "WARN"
        assert not (tmp_path / "market.db").exists()

    def test_missing_theme_tables_warns(self, tmp_path, monkeypatch):
        import src.data.data_health as health
        db = tmp_path / "market.db"
        _seed_pool_tables(db, self.SYMBOLS)
        monkeypatch.setattr(health, "MARKET_DB", db)
        result = self._run()
        assert result.status == "WARN"
        assert "theme_baskets" in result.detail

    def test_empty_theme_tables_warn(self, tmp_path, monkeypatch):
        import src.data.data_health as health
        from src.data import theme_basket_store as tbs
        db = tmp_path / "market.db"
        _seed_pool_tables(db, self.SYMBOLS)
        tbs.open_writer(db).close()
        monkeypatch.setattr(health, "MARKET_DB", db)
        result = self._run()
        assert result.status == "WARN"
        assert "空" in result.detail

    def test_no_heartbeat_warns(self, theme_db):
        conn = sqlite3.connect(str(theme_db))
        conn.execute("DELETE FROM theme_sync_commits")
        conn.commit()
        conn.close()
        result = self._run()
        assert result.status == "WARN"
        assert "心跳" in result.detail

    @pytest.mark.parametrize("age_days,status", [(13, "PASS"), (15, "WARN")])
    def test_heartbeat_staleness(self, tmp_path, monkeypatch, age_days, status):
        from datetime import timezone
        import src.data.data_health as health
        assert health.THEME_SYNC_STALE_DAYS == 14
        db = tmp_path / "market.db"
        _seed_pool_tables(db, self.SYMBOLS)
        _seed_theme_store(db, self.SYMBOLS, now=datetime.now(timezone.utc) - timedelta(days=age_days))
        monkeypatch.setattr(health, "MARKET_DB", db)
        result = self._run()
        assert result.status == status, result.detail
        assert f"{age_days} 天前" in result.detail

    @pytest.mark.parametrize("bad_value", ["yesterday", b"2026-11-27T00:00:00Z"])
    def test_unparseable_heartbeat_warns(self, theme_db, bad_value):
        conn = sqlite3.connect(str(theme_db))
        conn.execute("UPDATE theme_sync_commits SET applied_at = ?", (bad_value,))
        conn.commit()
        conn.close()
        assert self._run().status == "WARN"

    def test_missing_pool_tables_warns(self, tmp_path, monkeypatch):
        import src.data.data_health as health
        db = tmp_path / "market.db"
        _seed_theme_store(db, self.SYMBOLS)
        monkeypatch.setattr(health, "MARKET_DB", db)
        result = self._run()
        assert result.status == "WARN"
        assert "主池表未就绪" in result.detail

    def test_pool_gap_warns_with_count_and_first_ten(self, tmp_path, monkeypatch):
        import src.data.data_health as health
        db = tmp_path / "market.db"
        gap = [f"GAP{i:02d}" for i in range(12)]
        extra = [(s, None, 1) for s in gap] + [("LEFT", "2026-06-01", 1), ("INELIGIBLE", None, 0)]
        _seed_pool_tables(db, self.SYMBOLS, extra=extra)
        _seed_theme_store(db, self.SYMBOLS)
        monkeypatch.setattr(health, "MARKET_DB", db)
        result = self._run()
        assert result.status == "WARN"
        assert "12" in result.detail
        assert all(s in result.detail for s in gap[:10])
        assert "GAP10" not in result.detail and "GAP11" not in result.detail
        assert "LEFT" not in result.detail and "INELIGIBLE" not in result.detail

    def test_under_min_active_members_warns(self, tmp_path, monkeypatch):
        import src.data.data_health as health
        db = tmp_path / "market.db"
        few = self.SYMBOLS[:3]
        _seed_pool_tables(db, few)
        _seed_theme_store(db, few, min_members=5)
        monkeypatch.setattr(health, "MARKET_DB", db)
        result = self._run()
        assert result.status == "WARN"
        assert "l1_test" in result.detail and "l2_test" in result.detail
        assert "pi_test" not in result.detail

    def test_corrupted_db_warns(self, tmp_path, monkeypatch):
        import src.data.data_health as health
        db = tmp_path / "market.db"
        db.write_bytes(b"this is not a sqlite database" * 100)
        monkeypatch.setattr(health, "MARKET_DB", db)
        result = self._run()
        assert result.status == "WARN"

    def test_os_error_warns(self, theme_db, monkeypatch):
        import src.data.data_health as health

        def boom(*args, **kwargs):
            raise OSError("disk gone")
        monkeypatch.setattr(health.sqlite3, "connect", boom)
        result = self._run()
        assert result.status == "WARN"
        assert "disk gone" in result.detail

    def test_does_not_touch_market_store_or_base_universe(self, theme_db, monkeypatch):
        import ast
        import src.data.data_health as health
        import src.data.market_store as market_store

        def forbidden(*args, **kwargs):
            raise AssertionError("theme check must not use get_store/current_base_universe")
        monkeypatch.setattr(market_store, "get_store", forbidden)
        monkeypatch.setattr(health, "current_base_universe", forbidden)
        assert self._run().status == "PASS"

        tree = ast.parse(Path(health.__file__).read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                imported |= {node.module or ""} | {f"{node.module}.{a.name}" for a in node.names}
        assert not any("theme_basket_store" in name for name in imported)

    def test_read_only_and_leaves_no_handles(self, theme_db):
        import hashlib
        import os
        conn = sqlite3.connect(str(theme_db))
        assert conn.execute("PRAGMA journal_mode = WAL").fetchone()[0] == "wal"
        conn.close()
        wal = Path(str(theme_db) + "-wal")
        digest = hashlib.sha256(theme_db.read_bytes()).hexdigest()
        mtime = theme_db.stat().st_mtime_ns
        assert self._run().status == "PASS"
        assert hashlib.sha256(theme_db.read_bytes()).hexdigest() == digest
        assert theme_db.stat().st_mtime_ns == mtime
        assert not wal.exists() or wal.stat().st_size == 0
        held = {p for p in _open_paths() if os.path.realpath(p).startswith(os.path.realpath(theme_db))}
        assert held == set()

    def test_included_in_health_check_report(self, mock_healthy_data):
        from src.data.data_health import health_check
        report = health_check()
        names = [c.name for c in report.checks]
        assert names.index("主题篮子") == names.index("扩展覆盖率") + 1

    def test_unhealthy_report_only_warns_for_theme_check(self, mock_unhealthy_data):
        from src.data.data_health import health_check
        report = health_check()
        theme = [c for c in report.checks if c.name == "主题篮子"]
        assert len(theme) == 1 and theme[0].status == "WARN"
