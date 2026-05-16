"""Real CLI-semantics integration test for the v2 concept builder.

Complements tests/test_concept_v2_integration.py which short-circuits the
extend-pool coverage check via ``extend_pool={"OBSCURE"}``. This test exercises
the actual CLI loop:

    dry-run → review CSV (full universe) → read-reviewed-csv --save

Three behaviors locked in:

1. Phase 4 emits one CSV row per universe symbol with ``review_reason``
   ∈ {ok, soft_low_confidence, hard_needs_review}, hard rows surfacing first.
2. ``--dry-run`` leaves both ``concepts`` and ``company_concept_tags`` empty.
3. The reviewed-CSV ``--save`` path rebuilds the taxonomy BEFORE upserting tags,
   and the upsert covers the full universe (not just the review rows). Without
   the rebuild step, FK-NOT-NULL on ``primary_concept_id`` blows up the upsert.
"""
from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).parent.parent
TAXONOMY_V2 = PROJECT_ROOT / "config" / "concepts" / "concept_taxonomy_v2.json"


def _profiles() -> dict[str, dict]:
    return {
        "NVDA": {
            "symbol": "NVDA", "companyName": "NVIDIA Corporation",
            "sector": "Technology", "industry": "Semiconductors",
            "description": "GPU and AI accelerator",
        },
        "AMZN": {
            "symbol": "AMZN", "companyName": "Amazon.com Inc.",
            "sector": "Consumer Cyclical", "industry": "Internet Retail",
            "description": "AWS cloud + e-commerce platform",
        },
        "OBSCURE": {
            "symbol": "OBSCURE", "companyName": "Obscure Holdings",
            "sector": "Unknown", "industry": "Mystery",
            "description": "Unknown business model",
        },
    }


def _llm_failed_result():
    from terminal.llm_concept_prefill import LLMResult
    return LLMResult(
        l1=None, l2=None, l3_themes=[],
        business_role="", confidence=0.0,
        source="llm_failed", evidence="timeout (mocked)", needs_review=1,
    )


def _llm_success_nvda():
    """Mock prefill_one success for NVDA — Semiconductors is ambiguous (not in
    industry_map) so the registry returns unclassified → builder calls the LLM."""
    from terminal.llm_concept_prefill import LLMResult
    return LLMResult(
        l1="semiconductor", l2="gpu_accelerator", l3_themes=[],
        business_role="GPU/AI加速器", confidence=0.85,
        source="llm", evidence="llm prefill (mocked)", needs_review=0,
    )


def test_cli_full_loop_dry_run_then_reviewed_save(tmp_path):
    """End-to-end CLI semantics on a fresh DB.

    Universe = {NVDA (llm success), AMZN (anchor), OBSCURE (llm_failed)}. We run
    build_registry in dry-run mode, then simulate Boss editing OBSCURE in the
    CSV, then run the CLI's reviewed-csv save path against an empty DB. The DB
    must end up with all three symbols tagged, with concepts rebuilt fresh.
    """
    from src.data.market_store import MarketStore
    from terminal.company_concepts import ConceptRegistry
    from scripts.build_company_concept_registry import (
        REVIEW_CSV_FIELDS, apply_reviewed_csv, build_registry,
    )

    store = MarketStore(tmp_path / "market.db")
    registry = ConceptRegistry(taxonomy_path=TAXONOMY_V2, watchlist_path=None)
    csv_path = tmp_path / "review.csv"
    universe = ["NVDA", "AMZN", "OBSCURE"]

    # ---- Phase 1+3+4: dry-run. NVDA's Semiconductors industry is ambiguous
    # → unclassified → LLM (mocked success); OBSCURE → LLM (mocked failure).
    def _mock_prefill(*, symbol, profile, taxonomy):
        return _llm_success_nvda() if symbol == "NVDA" else _llm_failed_result()

    with patch(
        "scripts.build_company_concept_registry.prefill_one",
        side_effect=_mock_prefill,
    ):
        result = build_registry(
            store=store, registry=registry,
            universe_symbols=universe,
            profiles=_profiles(),
            portfolio_holdings=["NVDA", "AMZN"],
            broad_top_symbols=universe,
            review_csv_path=csv_path,
            save=False, force_save=False,
        )
    assert result.saved is False

    # ---- Assertion #1: dry-run leaves DB empty ----
    conn = sqlite3.connect(str(tmp_path / "market.db"))
    conn.row_factory = sqlite3.Row
    assert conn.execute("SELECT COUNT(*) FROM concepts").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM company_concept_tags").fetchone()[0] == 0

    # ---- Assertion #2: CSV is the full universe, not just review queue ----
    rows = list(csv.DictReader(csv_path.open()))
    assert {r["symbol"] for r in rows} == set(universe), (
        "Phase 4 must emit one row per universe symbol, not just review queue"
    )
    # All four review_reason buckets are allowed; OBSCURE is hard, the others
    # are auto-classified (rule/anchor with high confidence) → ok.
    by_symbol = {r["symbol"]: r for r in rows}
    assert by_symbol["OBSCURE"]["review_reason"] == "hard_needs_review"
    assert by_symbol["NVDA"]["review_reason"] in ("ok", "soft_low_confidence")
    assert by_symbol["AMZN"]["review_reason"] in ("ok", "soft_low_confidence")

    # ---- Assertion #3: hard rows come first in the CSV (Boss sees them top) ----
    assert rows[0]["review_reason"] == "hard_needs_review"

    # ---- Boss "edits" OBSCURE in the CSV, leaving the others as auto-classified ----
    edited = dict(by_symbol["OBSCURE"])
    edited["l1"] = "工业与航天"
    edited["l2"] = "工程与建筑"
    edited["l3_themes"] = ""
    edited["business_role"] = "Mystery industrials"
    edited["needs_review"] = "0"
    edited["prefill_source"] = "manual"
    edited["confidence"] = "0.85"
    edited["review_reason"] = "ok"
    by_symbol["OBSCURE"] = edited

    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=REVIEW_CSV_FIELDS)
        w.writeheader()
        for sym in universe:  # write in stable order
            w.writerow({k: by_symbol[sym].get(k, "") for k in REVIEW_CSV_FIELDS})

    # ---- Phase 5+6 via the CLI helper, hitting a still-empty DB ----
    saved = apply_reviewed_csv(
        store=store, registry=registry,
        csv_path=csv_path,
        extend_pool=set(universe),
    )
    assert saved == 3, "All 3 universe rows must persist, not just review rows"

    # ---- Assertion #4: concepts tree was rebuilt by the save path ----
    level_counts = dict(conn.execute(
        "SELECT level, COUNT(*) FROM concepts GROUP BY level"
    ).fetchall())
    assert level_counts == {1: 11, 2: 61, 3: 42}

    # ---- Assertion #5: all three symbols are tagged with display labels ----
    tags = {row["symbol"]: dict(row) for row in conn.execute(
        "SELECT symbol, primary_concept_id, secondary_concept_id, display_tags, "
        "source FROM company_concept_tags"
    ).fetchall()}
    conn.close()
    assert set(tags.keys()) == set(universe)
    assert tags["NVDA"]["primary_concept_id"] == "semiconductor"
    assert tags["AMZN"]["primary_concept_id"] == "ai_compute_cloud"
    assert tags["OBSCURE"]["primary_concept_id"] == "industrial_aerospace"
    # display_tags reconstructed from the rebuilt concepts.label
    assert tags["OBSCURE"]["display_tags"] == "工业与航天 / 工程与建筑"


def test_read_reviewed_csv_accepts_l3_concept_id(tmp_path):
    """L3 column accepts either Chinese label OR concept_id (parity with
    ConceptRegistry.resolve_l3_alias). Boss copying ``ai_compute`` from the
    id column of taxonomy_reference.csv must not be flagged as an error."""
    from scripts.build_company_concept_registry import (
        REVIEW_CSV_FIELDS, read_reviewed_csv,
    )

    taxonomy = json.loads(TAXONOMY_V2.read_text(encoding="utf-8"))

    csv_path = tmp_path / "review.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=REVIEW_CSV_FIELDS)
        w.writeheader()
        w.writerow({
            "review_reason": "ok",
            "symbol": "TEST",
            "company_name": "Test",
            "fmp_sector": "",
            "fmp_industry": "",
            "market_cap_b": "",
            "mcap_tier": "",
            "description": "",
            "l1": "AI算力与云",
            "l2": "超大规模云",
            "l3_themes": "ai_compute",   # concept_id, not label
            "business_role": "",
            "prefill_source": "manual",
            "confidence": "0.9",
            "needs_review": "0",
            "boss_notes": "",
        })

    parsed = read_reviewed_csv(
        csv_path, extend_pool={"TEST"}, taxonomy=taxonomy,
    )
    assert len(parsed) == 1
    assert parsed[0]["theme_ids"] == ["ai_compute"]


def test_refresh_profiles_merges_existing_and_writes_meta(tmp_path):
    """refresh_profiles must load any existing profiles.json, preserve symbols
    whose FMP fetch failed this run, and write _meta.updated_at so that
    data_health._check_fundamental_freshness doesn't go WARN.
    """
    from scripts.build_company_concept_registry import refresh_profiles

    profiles_path = tmp_path / "profiles.json"
    profiles_path.write_text(json.dumps({
        "AAPL": {"symbol": "AAPL", "companyName": "Apple Inc.", "stale": True},
        "MSFT": {"symbol": "MSFT", "companyName": "Microsoft", "stale": True},
        "_meta": {"updated_at": "2020-01-01 00:00:00", "count": 2},
    }), encoding="utf-8")

    def fake_fetch(symbol: str) -> dict:
        if symbol == "MSFT":
            raise RuntimeError("simulated FMP failure")
        return {"symbol": symbol, "companyName": f"{symbol} (fresh)"}

    with patch(
        "scripts.build_company_concept_registry._fetch_fmp_profile",
        side_effect=fake_fetch,
    ):
        count = refresh_profiles(["AAPL", "MSFT"], profiles_path=profiles_path)

    out = json.loads(profiles_path.read_text(encoding="utf-8"))
    assert count == 1, "only AAPL fetched successfully"
    assert out["AAPL"]["companyName"] == "AAPL (fresh)"
    assert "MSFT" in out, "MSFT fetch failed but its previous profile must survive"
    assert out["MSFT"].get("stale") is True
    assert "_meta" in out and "updated_at" in out["_meta"]
    assert out["_meta"]["updated_at"] != "2020-01-01 00:00:00", (
        "_meta.updated_at must advance even when some symbols fail"
    )


def test_cli_overrides_data_paths(tmp_path):
    """CLI must accept overrides for the data files so the script can be run
    from a worktree against the main workspace's data. The legacy module-level
    constants are not enough — passing the path through must produce identical
    behavior.
    """
    from scripts.build_company_concept_registry import _load_universe

    # Boss's --extended-universe-path argument resolves via _load_universe
    # against an arbitrary path, not the hard-coded EXTENDED_UNIVERSE_PATH.
    fake_path = tmp_path / "alt_universe.json"
    fake_path.write_text(json.dumps(["FOO", "BAR"]), encoding="utf-8")
    assert _load_universe(fake_path) == ["FOO", "BAR"]


def test_refresh_profiles_fails_fast_when_universe_empty(tmp_path, capsys):
    """--refresh-profiles against an empty extended universe must NOT silently
    overwrite profiles.json with an empty cache. It must exit non-zero.
    """
    from scripts.build_company_concept_registry import main

    universe_path = tmp_path / "extended_universe.json"
    universe_path.write_text(json.dumps([]), encoding="utf-8")
    profiles_path = tmp_path / "profiles.json"
    profiles_path.write_text(json.dumps({
        "AAPL": {"symbol": "AAPL"}, "_meta": {"updated_at": "2020-01-01"}
    }), encoding="utf-8")

    rc = main([
        "--refresh-profiles",
        "--extended-universe-path", str(universe_path),
        "--profiles-path", str(profiles_path),
    ])
    assert rc != 0, "empty universe must be a fatal error, not a no-op"
    # Existing cache must be untouched
    after = json.loads(profiles_path.read_text(encoding="utf-8"))
    assert "AAPL" in after, "existing profiles.json must not be clobbered"


def test_refresh_profiles_fails_fast_when_api_key_missing(tmp_path, monkeypatch):
    """No FMP_API_KEY → exit non-zero before hitting the FMP client (which
    would otherwise silently 401 on every request).
    """
    from scripts.build_company_concept_registry import main

    monkeypatch.setenv("FMP_API_KEY", "")
    universe_path = tmp_path / "extended_universe.json"
    universe_path.write_text(json.dumps(["AAPL"]), encoding="utf-8")
    profiles_path = tmp_path / "profiles.json"

    rc = main([
        "--refresh-profiles",
        "--extended-universe-path", str(universe_path),
        "--profiles-path", str(profiles_path),
    ])
    assert rc != 0, "missing FMP_API_KEY must be a fatal error"
    assert not profiles_path.exists(), "no fetch attempted → no file write"


def test_cli_data_root_routes_all_paths(tmp_path, monkeypatch):
    """--data-root sets the root for profiles + universe + dbs in one shot,
    so worktree runs can target the main workspace's data with one flag.
    """
    from scripts.build_company_concept_registry import main

    monkeypatch.setenv("FMP_API_KEY", "fake-key")
    data_root = tmp_path / "main_data"
    (data_root / "pool").mkdir(parents=True)
    (data_root / "fundamental").mkdir(parents=True)
    (data_root / "pool" / "extended_universe.json").write_text(
        json.dumps(["AAPL"]), encoding="utf-8"
    )

    with patch(
        "scripts.build_company_concept_registry._fetch_fmp_profile",
        return_value={"symbol": "AAPL", "companyName": "Apple"},
    ):
        rc = main(["--refresh-profiles", "--data-root", str(data_root)])

    assert rc == 0
    profiles_path = data_root / "fundamental" / "profiles.json"
    assert profiles_path.exists()
    out = json.loads(profiles_path.read_text(encoding="utf-8"))
    assert "AAPL" in out
    assert "_meta" in out


# ---- Regression tests: code review follow-ups (2026-05-14) ----


def test_apply_reviewed_csv_backs_up_before_rebuild(tmp_path):
    """apply_reviewed_csv must back up market.db BEFORE rebuild_concept_tree
    wipes company_concept_tags. Otherwise a save failure leaves zero recovery
    path — the production DB is already cleared and the backup mirrors the
    cleared state.

    Regression test: seed old tags, mock the upsert step to raise, and verify
    the pre-rebuild backup still contains the original tag rows.
    """
    from scripts.build_company_concept_registry import (
        REVIEW_CSV_FIELDS, apply_reviewed_csv,
    )
    from src.data.market_store import MarketStore
    from terminal.company_concepts import ConceptRegistry

    store = MarketStore(tmp_path / "market.db")
    registry = ConceptRegistry(taxonomy_path=TAXONOMY_V2, watchlist_path=None)
    # Seed the live taxonomy + a pre-existing tag row that represents the
    # production state we must be able to roll back to.
    store.rebuild_concept_tree(registry.concepts)
    store.upsert_company_concepts([{
        "symbol": "LEGACY",
        "primary_concept_id": "semiconductor",
        "secondary_concept_id": "gpu_accelerator",
        "theme_ids": [],
        "display_tags": "半导体 / 计算芯片/GPU加速器",
        "business_role": "old",
        "confidence": 0.9,
        "source": "manual",
        "needs_review": 0,
    }])

    # Build a valid reviewed CSV so read_reviewed_csv() passes — failure must
    # come from the upsert, not validation, to exercise the rollback path.
    csv_path = tmp_path / "review.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=REVIEW_CSV_FIELDS)
        w.writeheader()
        w.writerow({
            "review_reason": "ok",
            "symbol": "NEW",
            "company_name": "New",
            "fmp_sector": "", "fmp_industry": "",
            "market_cap_b": "", "mcap_tier": "", "description": "",
            "l1": "AI算力与云", "l2": "超大规模云",
            "l3_themes": "",
            "business_role": "",
            "prefill_source": "manual", "confidence": "0.9",
            "needs_review": "0", "boss_notes": "",
        })

    def _boom(self, rows):  # method signature: self + rows
        raise RuntimeError("simulated upsert failure")

    with patch.object(MarketStore, "upsert_company_concepts", _boom):
        try:
            apply_reviewed_csv(
                store=store, registry=registry,
                csv_path=csv_path, extend_pool={"NEW"},
            )
        except RuntimeError:
            pass
        else:
            raise AssertionError("expected the patched upsert to raise")

    # Production DB is now post-rebuild (tags cleared) — but the backup taken
    # before rebuild must still hold the original LEGACY row.
    backups = sorted(tmp_path.glob("market.db.backup-*-pre-rebuild"))
    assert len(backups) == 1, f"expected one pre-rebuild backup, found {backups}"

    bconn = sqlite3.connect(str(backups[0]))
    bconn.row_factory = sqlite3.Row
    legacy = bconn.execute(
        "SELECT symbol, primary_concept_id, business_role FROM company_concept_tags"
    ).fetchall()
    bconn.close()
    assert [dict(r) for r in legacy] == [{
        "symbol": "LEGACY",
        "primary_concept_id": "semiconductor",
        "business_role": "old",
    }], "pre-rebuild backup must capture the LEGACY row before rebuild wiped it"


def test_refresh_profiles_does_not_create_market_db(tmp_path, monkeypatch):
    """--refresh-profiles is FMP-only and must not touch market.db. Regression
    against the bug where MarketStore() was instantiated unconditionally at the
    top of main(), creating an empty market.db even on FMP-only commands.
    """
    from scripts.build_company_concept_registry import main

    monkeypatch.setenv("FMP_API_KEY", "fake-key")
    data_root = tmp_path / "data"
    (data_root / "pool").mkdir(parents=True)
    (data_root / "fundamental").mkdir(parents=True)
    (data_root / "pool" / "extended_universe.json").write_text(
        json.dumps(["AAPL"]), encoding="utf-8"
    )

    with patch(
        "scripts.build_company_concept_registry._fetch_fmp_profile",
        return_value={"symbol": "AAPL", "companyName": "Apple"},
    ):
        rc = main(["--refresh-profiles", "--data-root", str(data_root)])

    assert rc == 0
    assert not (data_root / "market.db").exists(), (
        "--refresh-profiles must not touch market.db"
    )


def test_validate_only_does_not_create_market_db(tmp_path):
    """--read-reviewed-csv --validate-only is read-only; market.db should not
    materialize as a side effect.
    """
    from scripts.build_company_concept_registry import (
        REVIEW_CSV_FIELDS, main,
    )

    data_root = tmp_path / "data"
    (data_root / "pool").mkdir(parents=True)
    (data_root / "pool" / "extended_universe.json").write_text(
        json.dumps(["TEST"]), encoding="utf-8"
    )

    csv_path = tmp_path / "review.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=REVIEW_CSV_FIELDS)
        w.writeheader()
        w.writerow({
            "review_reason": "ok", "symbol": "TEST",
            "company_name": "T",
            "fmp_sector": "", "fmp_industry": "",
            "market_cap_b": "", "mcap_tier": "", "description": "",
            "l1": "AI算力与云", "l2": "超大规模云", "l3_themes": "",
            "business_role": "",
            "prefill_source": "manual", "confidence": "0.9",
            "needs_review": "0", "boss_notes": "",
        })

    rc = main([
        "--read-reviewed-csv", str(csv_path),
        "--validate-only",
        "--data-root", str(data_root),
    ])
    assert rc == 0
    assert not (data_root / "market.db").exists(), (
        "--validate-only must not touch market.db"
    )


def test_company_db_override_routes_portfolio(tmp_path):
    """--company-db must drive _read_portfolio_holdings, not the
    terminal.company_store singleton (whose db_path is fixed on first call).
    """
    from scripts.build_company_concept_registry import _read_portfolio_holdings

    # Empty/missing company.db → empty portfolio, no exception.
    assert _read_portfolio_holdings(tmp_path / "missing.db") == []

    # Build a minimal company.db with one OPEN holding and ensure we pick it
    # up — proves we read FROM the supplied path, not the singleton default.
    import sqlite3
    db_path = tmp_path / "company.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE holdings (
            position_id INTEGER PRIMARY KEY,
            symbol TEXT NOT NULL,
            status TEXT NOT NULL
        );
        INSERT INTO holdings (symbol, status) VALUES ('PORTFOLIO_SYM', 'OPEN');
        INSERT INTO holdings (symbol, status) VALUES ('CLOSED_SYM', 'CLOSED');
    """)
    conn.commit()
    conn.close()

    assert _read_portfolio_holdings(db_path) == ["PORTFOLIO_SYM"]


def test_dollar_volume_db_override_routes_broad_top(tmp_path):
    """--dollar-volume-db (and the --data-root default that derives it) must
    drive _read_broad_top, otherwise worktree runs read the module-default DB
    and undercount priority coverage.
    """
    from scripts.build_company_concept_registry import _read_broad_top

    # Non-existent → empty, no DB file created.
    missing = tmp_path / "missing_dv.db"
    assert _read_broad_top(10, missing) == []
    assert not missing.exists()

    # Real DB with two ranked rows for a known date.
    from src.data.dollar_volume import init_db, store_daily_rankings
    db_path = tmp_path / "dollar_volume.db"
    init_db(db_path)
    store_daily_rankings(
        date="2026-05-14",
        rankings=[
            {"rank": 1, "symbol": "AAA", "dollar_volume": 1e9},
            {"rank": 2, "symbol": "BBB", "dollar_volume": 5e8},
        ],
        db_path=db_path,
    )
    assert _read_broad_top(10, db_path) == ["AAA", "BBB"]


def test_review_manifest_blocks_dropped_watchlist_row(tmp_path):
    """The full review manifest (universe ∪ watchlist ∪ portfolio ∪ broad_top)
    must be enforced at save time. Otherwise a hand edit that deletes a row
    whose symbol isn't in ``--extended-universe`` (e.g., a watchlist-only or
    portfolio-only ticker) would slip through validation and silently
    disappear during rebuild_concept_tree.
    """
    from scripts.build_company_concept_registry import (
        CSVValidationError, REVIEW_CSV_FIELDS,
        apply_reviewed_csv, build_registry,
    )
    from src.data.market_store import MarketStore
    from terminal.company_concepts import ConceptRegistry

    # POET is in the watchlist but NOT in the extended pool. Without the
    # manifest defense, dropping POET from the CSV would pass coverage.
    watchlist_path = tmp_path / "watchlist.json"
    watchlist_path.write_text(json.dumps({"symbols": ["POET"]}), encoding="utf-8")

    store = MarketStore(tmp_path / "market.db")
    registry = ConceptRegistry(
        taxonomy_path=TAXONOMY_V2, watchlist_path=watchlist_path,
    )
    csv_path = tmp_path / "review.csv"

    profiles = dict(_profiles())
    profiles["POET"] = {
        "symbol": "POET", "companyName": "POET Technologies",
        "sector": "Technology", "industry": "Semiconductors",
        "description": "Optical interposer for photonic compute.",
    }

    # NVDA + POET both carry the ambiguous Semiconductors industry → both fall
    # through to the LLM. Mock prefill_one so the test never spawns `claude`;
    # the LLM result is irrelevant here — we only assert POET reaches the
    # manifest + CSV and that dropping it fails save-time validation.
    with patch(
        "scripts.build_company_concept_registry.prefill_one",
        return_value=_llm_failed_result(),
    ):
        build_registry(
            store=store, registry=registry,
            universe_symbols=["NVDA", "AMZN"],         # extended pool proxy
            profiles=profiles,
            portfolio_holdings=["NVDA", "AMZN"],
            broad_top_symbols=["NVDA", "AMZN"],
            review_csv_path=csv_path,
            save=False, force_save=False,
        )

    # Manifest sidecar must record POET so the save path can catch its drop.
    manifest = json.loads(
        (tmp_path / "review_manifest.json").read_text(encoding="utf-8")
    )
    assert "POET" in manifest["symbols"], (
        "build_registry must persist watchlist symbols in the manifest"
    )

    rows = list(csv.DictReader(csv_path.open()))
    by_sym = {r["symbol"]: r for r in rows}
    assert "POET" in by_sym, "watchlist row must appear in the dry-run CSV"

    # Boss "accidentally" deletes the POET row before saving.
    surviving = [r for sym, r in by_sym.items() if sym != "POET"]
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=REVIEW_CSV_FIELDS)
        w.writeheader()
        for r in surviving:
            w.writerow({k: r.get(k, "") for k in REVIEW_CSV_FIELDS})

    # The caller's extend_pool is just the extended-universe proxy
    # ({"NVDA", "AMZN"}); POET is only in the manifest. The save must still
    # reject because the manifest is unioned into the effective pool.
    try:
        apply_reviewed_csv(
            store=store, registry=registry,
            csv_path=csv_path,
            extend_pool={"NVDA", "AMZN"},
        )
    except CSVValidationError as exc:
        assert "POET" in str(exc), f"error must call out POET, got: {exc}"
    else:
        raise AssertionError(
            "dropping a watchlist-only row must fail save-time validation"
        )

    # And the DB must NOT be mutated when validation fails — pre-rebuild
    # backup should not exist either (validation runs before backup).
    backups = list(tmp_path.glob("market.db.backup-*"))
    assert backups == [], (
        "failed validation must short-circuit before backup/rebuild"
    )


def test_data_root_loads_env_for_fmp_key(tmp_path, monkeypatch):
    """When --data-root is provided and data_root.parent/.env exists, the
    FMP_API_KEY must be picked up so --refresh-profiles doesn't fail-fast
    just because the worktree shell didn't source the main workspace's .env.
    """
    from scripts.build_company_concept_registry import main

    monkeypatch.delenv("FMP_API_KEY", raising=False)

    workspace = tmp_path / "main_workspace"
    data_root = workspace / "data"
    (data_root / "pool").mkdir(parents=True)
    (data_root / "fundamental").mkdir(parents=True)
    (data_root / "pool" / "extended_universe.json").write_text(
        json.dumps(["AAPL"]), encoding="utf-8"
    )
    # Place the .env next to the data dir (matches the main workspace layout).
    (workspace / ".env").write_text(
        'FMP_API_KEY="env-loaded-key"\n# a comment\nFRED_API_KEY=fred\n',
        encoding="utf-8",
    )

    with patch(
        "scripts.build_company_concept_registry._fetch_fmp_profile",
        return_value={"symbol": "AAPL", "companyName": "Apple"},
    ):
        rc = main(["--refresh-profiles", "--data-root", str(data_root)])

    assert rc == 0, "FMP_API_KEY from data_root.parent/.env should make the run succeed"

    import os
    assert os.environ.get("FMP_API_KEY") == "env-loaded-key"
    # Existing env vars must not be clobbered by .env loading.
    monkeypatch.setenv("FRED_API_KEY", "explicit-wins")
    from scripts.build_company_concept_registry import _maybe_load_env_file
    _maybe_load_env_file(data_root)
    assert os.environ.get("FRED_API_KEY") == "explicit-wins"
