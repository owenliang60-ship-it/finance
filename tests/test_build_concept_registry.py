"""Build/refresh script for company concept registry (Task 4)."""
import csv
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
CFG = PROJECT_ROOT / "config" / "concepts"
TAXONOMY_PATH = CFG / "taxonomy.json"
THEMES_PATH = CFG / "concept_themes.json"
OVERRIDES_PATH = CFG / "company_concept_overrides.json"
WATCHLIST_PATH = CFG / "concept_watchlist.json"


@pytest.fixture
def build_env(tmp_path):
    from src.data.market_store import MarketStore
    from terminal.company_concepts import ConceptRegistry

    store = MarketStore(tmp_path / "market.db")
    registry = ConceptRegistry(
        taxonomy_path=TAXONOMY_PATH,
        themes_path=THEMES_PATH,
        overrides_path=OVERRIDES_PATH,
        watchlist_path=WATCHLIST_PATH,
    )
    profiles = {
        "MU": {"symbol": "MU", "industry": "Semiconductors"},
        "NVDA": {"symbol": "NVDA", "industry": "Semiconductors"},
        "POET": {"symbol": "POET", "industry": "Semiconductors"},
        "ZZZA": {"symbol": "ZZZA", "industry": "Software—Application",
                 "companyName": "Hypothetical SaaS Co"},
        "ZZZB": {"symbol": "ZZZB", "companyName": "Mystery Holdings"},
    }
    return tmp_path, store, registry, profiles


def test_dry_run_does_not_write_db(build_env):
    from scripts.build_company_concept_registry import build_registry

    tmp_path, store, registry, profiles = build_env
    csv_path = tmp_path / "review.csv"
    result = build_registry(
        store=store, registry=registry,
        universe_symbols=["MU", "NVDA", "POET", "ZZZA", "ZZZB"],
        profiles=profiles,
        portfolio_holdings=["MU", "NVDA"],
        broad_top_symbols=["MU", "NVDA", "ZZZA"],
        review_csv_path=csv_path,
        save=False, force_save=False,
    )
    assert result.saved is False
    assert store.get_company_concept_coverage()["total"] == 0
    assert csv_path.exists()
    # dry-run must NOT touch concepts or concept_themes either.
    conn = store._get_conn()
    assert conn.execute("SELECT COUNT(*) FROM concepts").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM concept_themes").fetchone()[0] == 0


def test_dry_run_writes_review_csv_only_needs_review_rows(build_env):
    from scripts.build_company_concept_registry import build_registry

    tmp_path, store, registry, profiles = build_env
    csv_path = tmp_path / "review.csv"
    build_registry(
        store=store, registry=registry,
        universe_symbols=["MU", "NVDA", "ZZZB"],
        profiles=profiles,
        portfolio_holdings=[],
        broad_top_symbols=["MU", "NVDA"],
        review_csv_path=csv_path,
        save=False, force_save=False,
    )
    rows = list(csv.DictReader(csv_path.open()))
    assert {r["symbol"] for r in rows} == {"ZZZB"}


def test_save_writes_when_gate_passes(build_env):
    from scripts.build_company_concept_registry import build_registry

    tmp_path, store, registry, profiles = build_env
    # Universe = MU + NVDA; watchlist auto-adds POET/OKLO/DXYZ → 5 rows total.
    # All five hit manual override → needs_review=0 → priority_coverage 100%.
    result = build_registry(
        store=store, registry=registry,
        universe_symbols=["MU", "NVDA"],
        profiles=profiles,
        portfolio_holdings=["MU", "NVDA"],
        broad_top_symbols=["MU", "NVDA"],
        review_csv_path=tmp_path / "review.csv",
        save=True, force_save=False,
    )
    assert result.saved is True
    assert result.priority_coverage == 1.0
    cov = store.get_company_concept_coverage()
    assert cov["total"] == 5  # MU + NVDA + watchlist (POET, OKLO, DXYZ)
    assert cov["manual"] == 5


def test_save_fails_when_priority_not_fully_covered(build_env):
    from scripts.build_company_concept_registry import build_registry, BuildGateError

    tmp_path, store, registry, profiles = build_env
    with pytest.raises(BuildGateError):
        build_registry(
            store=store, registry=registry,
            universe_symbols=["MU", "ZZZB"],
            profiles=profiles,
            portfolio_holdings=[],
            broad_top_symbols=["ZZZB"],
            review_csv_path=tmp_path / "review.csv",
            save=True, force_save=False,
        )
    assert store.get_company_concept_coverage()["total"] == 0


def test_force_save_bypasses_gate(build_env):
    from scripts.build_company_concept_registry import build_registry

    tmp_path, store, registry, profiles = build_env
    # ZZZB hits fallback → priority_coverage<100% → gate fails → force_save bypasses.
    result = build_registry(
        store=store, registry=registry,
        universe_symbols=["MU", "ZZZB"],
        profiles=profiles,
        portfolio_holdings=[],
        broad_top_symbols=["ZZZB"],
        review_csv_path=tmp_path / "review.csv",
        save=True, force_save=True,
    )
    assert result.saved is True
    assert result.forced_save is True
    # MU + ZZZB + watchlist (POET, OKLO, DXYZ) = 5
    assert store.get_company_concept_coverage()["total"] == 5


def test_save_fails_when_tail_needs_review_too_high(build_env):
    from scripts.build_company_concept_registry import build_registry, BuildGateError

    tmp_path, store, registry, _ = build_env
    profiles = {
        f"Z{i}": {"symbol": f"Z{i}", "companyName": f"Mystery {i} Holdings"}
        for i in range(1, 6)
    }
    with pytest.raises(BuildGateError):
        build_registry(
            store=store, registry=registry,
            universe_symbols=list(profiles.keys()),
            profiles=profiles,
            portfolio_holdings=[],
            broad_top_symbols=[],
            review_csv_path=tmp_path / "review.csv",
            save=True, force_save=False,
        )


def test_build_persists_concepts_then_themes_then_companies(build_env):
    """First-run FK must not fail: concepts upserted before company tags."""
    from scripts.build_company_concept_registry import build_registry

    tmp_path, store, registry, profiles = build_env
    result = build_registry(
        store=store, registry=registry,
        universe_symbols=["MU"],
        profiles=profiles,
        portfolio_holdings=["MU"],
        broad_top_symbols=["MU"],
        review_csv_path=tmp_path / "review.csv",
        save=True, force_save=False,
    )
    assert result.saved is True
    conn = store._get_conn()
    assert conn.execute("SELECT COUNT(*) FROM concepts").fetchone()[0] >= 5
    assert conn.execute("SELECT COUNT(*) FROM concept_themes").fetchone()[0] >= 1
    mu = store.get_company_concepts(["MU"])["MU"]
    assert mu["primary_concept_id"] == "semiconductor"


def test_watchlist_included_even_if_absent_from_broad(build_env):
    from scripts.build_company_concept_registry import build_registry

    tmp_path, store, registry, profiles = build_env
    result = build_registry(
        store=store, registry=registry,
        universe_symbols=["MU"],   # 不含 POET
        profiles=profiles,         # 含 POET profile
        portfolio_holdings=[],
        broad_top_symbols=["MU"],
        review_csv_path=tmp_path / "review.csv",
        save=True, force_save=False,
    )
    fetched = store.get_company_concepts(["POET"])
    assert "POET" in fetched
    assert result.watchlist_added >= 1


def test_missing_profile_still_writes_manual_override(build_env):
    from scripts.build_company_concept_registry import build_registry

    tmp_path, store, registry, _ = build_env
    result = build_registry(
        store=store, registry=registry,
        universe_symbols=["MU"],
        profiles={},
        portfolio_holdings=["MU"],
        broad_top_symbols=["MU"],
        review_csv_path=tmp_path / "review.csv",
        save=True, force_save=False,
    )
    assert result.saved is True
    mu = store.get_company_concepts(["MU"])["MU"]
    assert mu["primary_concept_id"] == "semiconductor"
    assert mu["source"] == "manual"


def test_main_save_refuses_when_broad_top_empty(monkeypatch, tmp_path, capsys):
    """`--save` with empty broad_top must exit 2 — otherwise the gate denominator
    silently shrinks to portfolio+watchlist and any clean override passes."""
    from scripts import build_company_concept_registry as mod

    # Force broad_top empty regardless of dollar_volume DB state.
    monkeypatch.setattr(mod, "_read_broad_top", lambda *a, **kw: [])
    monkeypatch.setattr(mod, "_read_portfolio_holdings", lambda *a, **kw: ["MU"])
    monkeypatch.setattr(mod, "_load_profiles", lambda *a, **kw: {})
    monkeypatch.setattr(mod, "_load_universe", lambda *a, **kw: ["MU"])
    monkeypatch.setattr(sys.modules["scripts.build_company_concept_registry"],
                        "MarketStore",
                        lambda *a, **kw: __import__("src.data.market_store",
                                                   fromlist=["MarketStore"]).MarketStore(
                            tmp_path / "market.db"))

    monkeypatch.setattr(sys, "argv",
                        ["build_company_concept_registry.py", "--save",
                         "--review-csv", str(tmp_path / "review.csv")])
    rc = mod.main()
    assert rc == 2
    err = capsys.readouterr().err
    assert "GATE FAILED" in err and "broad_top is empty" in err


def test_main_save_with_force_save_bypasses_empty_broad_top(monkeypatch, tmp_path):
    """`--force-save` lets the operator override the broad_top fail-safe."""
    from scripts import build_company_concept_registry as mod

    monkeypatch.setattr(mod, "_read_broad_top", lambda *a, **kw: [])
    monkeypatch.setattr(mod, "_read_portfolio_holdings", lambda *a, **kw: ["MU"])
    monkeypatch.setattr(mod, "_load_profiles", lambda *a, **kw: {})
    monkeypatch.setattr(mod, "_load_universe", lambda *a, **kw: ["MU"])
    monkeypatch.setattr(mod, "MarketStore",
                        lambda *a, **kw: __import__("src.data.market_store",
                                                   fromlist=["MarketStore"]).MarketStore(
                            tmp_path / "market.db"))

    monkeypatch.setattr(sys, "argv",
                        ["build_company_concept_registry.py", "--save",
                         "--force-save",
                         "--review-csv", str(tmp_path / "review.csv")])
    rc = mod.main()
    assert rc == 0


def test_force_save_bypassing_empty_broad_top_marks_forced_in_result(build_env):
    """Empty broad_top + --force-save must leave forced_save=True audit trail
    even when the rest of the gate would have passed on its own."""
    from scripts.build_company_concept_registry import build_registry

    tmp_path, store, registry, profiles = build_env
    result = build_registry(
        store=store, registry=registry,
        universe_symbols=["MU"],   # MU is a manual override → clean
        profiles=profiles,
        portfolio_holdings=["MU"],
        broad_top_symbols=[],      # empty → must trip gate
        review_csv_path=tmp_path / "review.csv",
        save=True, force_save=True,
    )
    assert result.saved is True
    assert result.forced_save is True   # audit trail: bypass was used
    summary = result.as_summary()
    assert "(forced)" in summary


def test_save_fails_when_broad_top_empty_without_force_save(build_env):
    """Without --force-save, empty broad_top must trip gate (BuildGateError)."""
    from scripts.build_company_concept_registry import build_registry, BuildGateError

    tmp_path, store, registry, profiles = build_env
    with pytest.raises(BuildGateError, match="broad_top is empty"):
        build_registry(
            store=store, registry=registry,
            universe_symbols=["MU"],
            profiles=profiles,
            portfolio_holdings=["MU"],
            broad_top_symbols=[],
            review_csv_path=tmp_path / "review.csv",
            save=True, force_save=False,
        )


def test_priority_symbols_force_added_to_universe(build_env):
    """Portfolio + broad_top symbols absent from `universe_symbols` MUST be tagged
    anyway, so the gate denominator includes them instead of silently shrinking."""
    from scripts.build_company_concept_registry import build_registry, BuildGateError

    tmp_path, store, registry, profiles = build_env
    # Add fallback profiles for two off-universe priority names.
    profiles = {
        **profiles,
        "OFF1": {"symbol": "OFF1", "companyName": "Off Universe Holdings One"},
        "OFF2": {"symbol": "OFF2", "companyName": "Off Universe Holdings Two"},
    }

    # universe = MU only; OFF1 in portfolio, OFF2 in broad_top — both must
    # still be classified, hit fallback (needs_review=1), and fail the gate.
    with pytest.raises(BuildGateError):
        build_registry(
            store=store, registry=registry,
            universe_symbols=["MU"],
            profiles=profiles,
            portfolio_holdings=["OFF1"],
            broad_top_symbols=["OFF2"],
            review_csv_path=tmp_path / "review.csv",
            save=True, force_save=False,
        )

    # Now allow force_save and verify both off-universe priority names actually
    # got persisted (would have been silently dropped before the fix).
    result = build_registry(
        store=store, registry=registry,
        universe_symbols=["MU"],
        profiles=profiles,
        portfolio_holdings=["OFF1"],
        broad_top_symbols=["OFF2"],
        review_csv_path=tmp_path / "review.csv",
        save=True, force_save=True,
    )
    assert result.saved is True
    fetched = store.get_company_concepts(["OFF1", "OFF2"])
    assert "OFF1" in fetched
    assert "OFF2" in fetched
    # Both fall back, so they show up as needs_review and lower priority_coverage.
    assert result.priority_coverage < 1.0


def test_cli_reader_portfolio_uses_correct_company_store_api(monkeypatch):
    """`_read_portfolio_holdings` must call the public `get_all_open_holdings`."""
    from scripts import build_company_concept_registry as mod

    fake_called = {"hits": 0}

    class _FakeStore:
        def get_all_open_holdings(self):
            fake_called["hits"] += 1
            return [{"symbol": "MU"}, {"symbol": "nvda"}, {"symbol": ""}]

    import terminal.company_store as cs_mod
    monkeypatch.setattr(cs_mod, "get_store", lambda *a, **kw: _FakeStore())

    result = mod._read_portfolio_holdings()
    assert fake_called["hits"] == 1
    assert result == ["MU", "NVDA"]


def test_cli_reader_broad_top_uses_correct_get_rankings_signature(monkeypatch):
    """`_read_broad_top` must pass `date=, limit=` and not the bogus `top_n=`."""
    from scripts import build_company_concept_registry as mod
    from src.data import dollar_volume as dv_mod

    captured = {}

    def _fake_get_latest_date(db_path=None):
        return "2026-04-25"

    def _fake_get_rankings(date, limit=50, db_path=None):
        captured["date"] = date
        captured["limit"] = limit
        return [{"symbol": "AAPL"}, {"symbol": "MSFT"}, {"symbol": "nvda"}]

    monkeypatch.setattr(dv_mod, "get_latest_date", _fake_get_latest_date)
    monkeypatch.setattr(dv_mod, "get_rankings", _fake_get_rankings)

    result = mod._read_broad_top(100)
    assert captured == {"date": "2026-04-25", "limit": 100}
    assert result == ["AAPL", "MSFT", "NVDA"]


def test_cli_reader_broad_top_empty_when_no_rankings(monkeypatch):
    from scripts import build_company_concept_registry as mod
    from src.data import dollar_volume as dv_mod

    monkeypatch.setattr(dv_mod, "get_latest_date", lambda *a, **kw: None)
    monkeypatch.setattr(dv_mod, "get_rankings",
                        lambda *a, **kw: pytest.fail("should not be called"))

    assert mod._read_broad_top(100) == []


def test_rebuild_display_preserves_manual_overrides(build_env):
    """Manual override 自带 display_tags 时，rebuild 必须保留原串."""
    from scripts.build_company_concept_registry import (
        build_registry,
        rebuild_display_tags,
    )

    tmp_path, store, registry, profiles = build_env
    build_registry(
        store=store, registry=registry,
        universe_symbols=["MU", "ZZZA"],
        profiles=profiles,
        portfolio_holdings=["MU"],
        broad_top_symbols=["MU"],
        review_csv_path=tmp_path / "review.csv",
        save=True, force_save=True,
    )

    # Mutate label to detect rebuild
    conn = store._get_conn()
    conn.execute("UPDATE concepts SET label = 'XX存储' WHERE concept_id = 'memory'")
    conn.execute(
        "UPDATE concepts SET label = '企业软件V2' WHERE concept_id = 'enterprise_software'"
    )
    conn.commit()

    summary = rebuild_display_tags(store=store, registry=registry)
    assert summary["manual_display_tags_preserved"] >= 1

    mu = store.get_company_concepts(["MU"])["MU"]
    assert mu["display_tags"] == "半导体 / 存储 / HBM"  # 手写串保留

    zzza = store.get_company_concepts(["ZZZA"])["ZZZA"]
    # rule 行用新 label 重拼
    assert "企业软件V2" in zzza["display_tags"]
