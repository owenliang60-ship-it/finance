import csv
from pathlib import Path
from scripts.build_company_concept_registry import (
    _read_csv_symbols, _db_tag_symbols, _normalize_review_csv, _append_csv_atomic,
    REVIEW_CSV_FIELDS,
)


def _write(p: Path, header: list[str], rows: list[list[str]]):
    with p.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh); w.writerow(header)
        for r in rows: w.writerow(r)


def test_normalize_dedups_duplicate_business_role(tmp_path):
    # legacy 17-col header: business_role at pos 2 AND pos 11
    header = ["review_reason", "symbol", "business_role", "company_name", "fmp_sector",
              "fmp_industry", "market_cap_b", "mcap_tier", "description", "l1", "l2",
              "l3_themes", "business_role", "prefill_source", "confidence", "needs_review", "boss_notes"]
    row = ["ok", "AAA", "", "Co", "Tech", "Semis", "12.0", "small", "desc", "信息技术",
           "半导体", "存储", "代工", "rule", "0.70", "0", ""]
    src = tmp_path / "legacy.csv"; _write(src, header, [row])
    dst = tmp_path / "canon.csv"
    assert _normalize_review_csv(src, dst) == 1
    out = list(csv.DictReader(dst.open(encoding="utf-8")))
    assert list(out[0].keys()) == REVIEW_CSV_FIELDS          # 16 unique fields
    assert out[0]["business_role"] == "代工"                  # coalesced non-empty (pos 11)


def test_append_csv_atomic_normalizes(tmp_path):
    canon = tmp_path / "canon.csv"
    _write(canon, REVIEW_CSV_FIELDS, [["ok", "AAA"] + [""] * 14])
    _append_csv_atomic(canon, [{"symbol": "BBB", "l1": "x"}])
    assert _read_csv_symbols(canon) == {"AAA", "BBB"}


# ---- Task 2: weekly_sync drift detection + classify split ----

import scripts.build_company_concept_registry as b


def _fake_registry(): return object()


def _run_ws(tmp_path, monkeypatch, *, base_syms, uni_syms, classify_fn, sent):
    canon = tmp_path / "canon.csv"
    canon.write_text("symbol\n" + "\n".join(base_syms) + "\n", encoding="utf-8")
    uni = tmp_path / "uni.json"
    import json as _j
    uni.write_text(_j.dumps({"symbols": uni_syms}), encoding="utf-8")
    return b.weekly_sync(
        registry=_fake_registry(), taxonomy={"concepts": []},
        canonical_csv=canon, extended_universe_path=uni,
        profiles_path=tmp_path / "prof.json", market_db_path=tmp_path / "m.db",
        queue_dir=tmp_path, run_date="2026-06-01",
        classify_fn=classify_fn, refresh_fn=lambda syms, profiles_path: 0,
        store_factory=None,                                    # skip persist (Task 2 scope)
        telegram_fn=lambda text, channel: sent.append((text, channel)))


def test_weekly_sync_splits_and_always_notifies(tmp_path, monkeypatch):
    monkeypatch.setattr(b, "_load_profiles",
        lambda p: {"RULEX": {"sector": "Tech", "industry": "Semis"},
                   "LLMY": {"sector": "X", "industry": "Y"}})

    def fake_classify(reg, profile, tax):
        sym = profile["symbol"]
        if sym == "RULEX":
            return {"symbol": sym, "source": "rule", "l1": "l1_tech", "l2": "l2_semis", "l3_themes": []}
        return {"symbol": sym, "source": "llm", "l1": "l1_x", "l2": "l2_y", "l3_themes": [], "needs_review": 1}

    sent = []
    res = _run_ws(tmp_path, monkeypatch, base_syms=["AAA"],
                  uni_syms=["AAA", "RULEX", "LLMY"], classify_fn=fake_classify, sent=sent)
    assert res.auto_saved == ["RULEX"] and res.queued == ["LLMY"] and res.error is None
    assert sent and sent[0][1] == "group"                      # D2: telegram fired


def test_weekly_sync_anchor_without_profile_auto_saves(tmp_path, monkeypatch):
    """P1.5: anchor matched by SYMBOL even with empty profile → deterministic."""
    monkeypatch.setattr(b, "_load_profiles", lambda p: {})     # no profile at all
    fake = lambda reg, prof, tax: {"symbol": prof["symbol"], "source": "manual",
                                   "l1": "l1_a", "l2": "l2_b", "l3_themes": []}
    sent = []
    res = _run_ws(tmp_path, monkeypatch, base_syms=["AAA"], uni_syms=["AAA", "ANCHORX"],
                  classify_fn=fake, sent=sent)
    assert res.auto_saved == ["ANCHORX"] and res.failed == []


def test_weekly_sync_classify_error_becomes_failed_artifact(tmp_path, monkeypatch):
    """P1.4: classify raising → failed bucket + a review row carried for the queue CSV."""
    monkeypatch.setattr(b, "_load_profiles", lambda p: {"BADX": {"sector": "Z", "industry": "Q"}})
    def boom(reg, prof, tax): raise RuntimeError("LLM down")
    sent = []
    res = _run_ws(tmp_path, monkeypatch, base_syms=["AAA"], uni_syms=["AAA", "BADX"],
                  classify_fn=boom, sent=sent)
    assert res.failed == ["BADX"] and res.error is None         # caught, not fatal
    assert any(r["symbol"] == "BADX" for r in res._failed_rows)
    assert sent and sent[0][1] == "group"


def test_weekly_sync_no_drift_still_notifies(tmp_path, monkeypatch):
    monkeypatch.setattr(b, "_load_profiles", lambda p: {})
    sent = []
    res = _run_ws(tmp_path, monkeypatch, base_syms=["AAA"], uni_syms=["AAA"],
                  classify_fn=lambda *a: {}, sent=sent)
    assert res.drift_in == [] and res.auto_saved == []
    assert sent and "无新增" in sent[0][0] and sent[0][1] == "group"   # D2: even no-drift pushes


# ---- Task 3: incremental save + CSV⇔DB lockstep + queue ----


def test_weekly_sync_persists_deterministic_incrementally(tmp_path, monkeypatch):
    """End-to-end with a real temp MarketStore seeded via rebuild_concept_tree."""
    import json
    from src.data.market_store import MarketStore
    import scripts.build_company_concept_registry as b

    # real taxonomy + registry
    cfg = Path("config/concepts")
    taxonomy = json.loads((cfg / "concept_taxonomy_v2.json").read_text(encoding="utf-8"))
    registry = b.ConceptRegistry(taxonomy_path=cfg / "concept_taxonomy_v2.json",
                                 watchlist_path=cfg / "concept_watchlist.json")

    db = tmp_path / "market.db"
    store = MarketStore(db)
    store.rebuild_concept_tree(registry.concepts)           # populate concepts tree
    # seed one existing tag so base lockstep holds
    seed_l1 = taxonomy["concepts"][0]["concept_id"]
    store.upsert_company_concepts([{
        "symbol": "AAA", "primary_concept_id": seed_l1, "theme_ids": [],
        "display_tags": "", "business_role": "", "confidence": 1.0,
        "source": "manual", "evidence": "seed", "needs_review": 0}])

    canon = tmp_path / "canon.csv"
    # canonical CSV must match DB (lockstep): one row AAA
    _write(canon, b.REVIEW_CSV_FIELDS, [["ok", "AAA"] + [""] * 14])

    # pick a real (sector,industry) from industry_map that yields a rule hit
    imap = taxonomy["industry_map"]; key = next(iter(imap)); sector, industry = key.split("|", 1)
    uni = tmp_path / "uni.json"; uni.write_text(json.dumps({"symbols": ["AAA", "RULEX"]}), encoding="utf-8")

    monkeypatch.setattr(b, "_load_profiles",
        lambda p: {"RULEX": {"symbol": "RULEX", "sector": sector, "industry": industry,
                             "companyName": "Rule Co", "description": "x"}})

    sent = []
    res = b.weekly_sync(
        registry=registry, taxonomy=taxonomy, canonical_csv=canon,
        extended_universe_path=uni, profiles_path=tmp_path / "prof.json",
        market_db_path=db, queue_dir=tmp_path, run_date="2026-06-01",
        refresh_fn=lambda syms, profiles_path: 0,
        store_factory=lambda: store,
        telegram_fn=lambda text, channel: sent.append((text, channel)))

    assert res.error is None
    assert "RULEX" in res.auto_saved
    assert _db_tag_symbols(store) == {"AAA", "RULEX"}        # incremental, AAA preserved
    assert _read_csv_symbols(canon) == {"AAA", "RULEX"}      # CSV ⇔ DB lockstep
    assert sent and sent[0][1] == "group"                    # telegram group summary


def test_weekly_sync_preflight_fails_closed(tmp_path, monkeypatch):
    """CSV ⇔ DB mismatch at preflight → fail-closed, no mutation."""
    import json
    from src.data.market_store import MarketStore
    import scripts.build_company_concept_registry as b
    cfg = Path("config/concepts")
    taxonomy = json.loads((cfg / "concept_taxonomy_v2.json").read_text(encoding="utf-8"))
    registry = b.ConceptRegistry(taxonomy_path=cfg / "concept_taxonomy_v2.json",
                                 watchlist_path=cfg / "concept_watchlist.json")
    db = tmp_path / "m.db"; store = MarketStore(db); store.rebuild_concept_tree(registry.concepts)
    # DB empty but canonical CSV has a row → lockstep broken
    canon = tmp_path / "canon.csv"; _write(canon, b.REVIEW_CSV_FIELDS, [["ok", "ZZZ"] + [""] * 14])
    uni = tmp_path / "uni.json"; uni.write_text(json.dumps({"symbols": ["ZZZ", "NEW"]}), encoding="utf-8")
    monkeypatch.setattr(b, "_load_profiles",
        lambda p: {"NEW": {"symbol": "NEW", "sector": "Tech", "industry": "Semis"}})
    res = b.weekly_sync(
        registry=registry, taxonomy=taxonomy, canonical_csv=canon,
        extended_universe_path=uni, profiles_path=tmp_path / "p.json",
        market_db_path=db, queue_dir=tmp_path, run_date="2026-06-01",
        refresh_fn=lambda syms, profiles_path: 0, store_factory=lambda: store,
        classify_fn=lambda r, prof, tax: {"symbol": prof["symbol"], "source": "rule",
                                          "l1": registry.concepts[0]["concept_id"], "l2": None, "l3_themes": []})
    assert res.error and "preflight" in res.error
    assert _db_tag_symbols(store) == set()                   # no mutation


def test_weekly_sync_no_drift_still_checks_lockstep(tmp_path):
    """P1.A: a CSV⇔DB divergence is caught even on a week with ZERO drift-in."""
    import json
    from src.data.market_store import MarketStore
    import scripts.build_company_concept_registry as b
    cfg = Path("config/concepts")
    taxonomy = json.loads((cfg / "concept_taxonomy_v2.json").read_text(encoding="utf-8"))
    registry = b.ConceptRegistry(taxonomy_path=cfg / "concept_taxonomy_v2.json",
                                 watchlist_path=cfg / "concept_watchlist.json")
    db = tmp_path / "m.db"; store = MarketStore(db); store.rebuild_concept_tree(registry.concepts)
    seed_l1 = taxonomy["concepts"][0]["concept_id"]
    for sym in ("AAA", "DBONLY"):                            # DB has 2 tags
        store.upsert_company_concepts([{"symbol": sym, "primary_concept_id": seed_l1,
            "theme_ids": [], "display_tags": "", "business_role": "", "confidence": 1.0,
            "source": "manual", "evidence": "seed", "needs_review": 0}])
    canon = tmp_path / "canon.csv"; _write(canon, b.REVIEW_CSV_FIELDS, [["ok", "AAA"] + [""] * 14])  # CSV has 1
    uni = tmp_path / "uni.json"; uni.write_text(json.dumps({"symbols": ["AAA"]}), encoding="utf-8")   # no drift
    sent = []
    res = b.weekly_sync(
        registry=registry, taxonomy=taxonomy, canonical_csv=canon,
        extended_universe_path=uni, profiles_path=tmp_path / "p.json",
        market_db_path=db, queue_dir=tmp_path, run_date="2026-06-01",
        refresh_fn=lambda syms, profiles_path: 0, store_factory=lambda: store,
        telegram_fn=lambda text, channel: sent.append((text, channel)))
    assert res.drift_in == []                                # nothing drifted in
    assert res.error and "preflight" in res.error            # divergence still caught
    assert sent and "preflight" in sent[0][0]                # alerted, not silent "no change"


def test_weekly_sync_csv_commit_failure_restores_db(tmp_path, monkeypatch):
    """P1.B: DB upsert commits but the atomic CSV swap fails → DB restored, lockstep held."""
    import json
    from src.data.market_store import MarketStore
    import scripts.build_company_concept_registry as b
    cfg = Path("config/concepts")
    taxonomy = json.loads((cfg / "concept_taxonomy_v2.json").read_text(encoding="utf-8"))
    registry = b.ConceptRegistry(taxonomy_path=cfg / "concept_taxonomy_v2.json",
                                 watchlist_path=cfg / "concept_watchlist.json")
    db = tmp_path / "m.db"; store = MarketStore(db); store.rebuild_concept_tree(registry.concepts)
    seed_l1 = taxonomy["concepts"][0]["concept_id"]
    store.upsert_company_concepts([{"symbol": "AAA", "primary_concept_id": seed_l1,
        "theme_ids": [], "display_tags": "", "business_role": "", "confidence": 1.0,
        "source": "manual", "evidence": "seed", "needs_review": 0}])
    canon = tmp_path / "canon.csv"; _write(canon, b.REVIEW_CSV_FIELDS, [["ok", "AAA"] + [""] * 14])
    imap = taxonomy["industry_map"]; key = next(iter(imap)); sector, industry = key.split("|", 1)
    uni = tmp_path / "uni.json"; uni.write_text(json.dumps({"symbols": ["AAA", "RULEX"]}), encoding="utf-8")
    monkeypatch.setattr(b, "_load_profiles",
        lambda p: {"RULEX": {"symbol": "RULEX", "sector": sector, "industry": industry,
                             "companyName": "Rule Co", "description": "x"}})
    # fail the atomic CSV swap that happens AFTER the DB upsert
    real_replace = b.os.replace
    def boom(src, dst):
        if str(dst).endswith("canon.csv"):
            raise OSError("swap failed")
        return real_replace(src, dst)
    monkeypatch.setattr(b.os, "replace", boom)
    sent = []
    res = b.weekly_sync(
        registry=registry, taxonomy=taxonomy, canonical_csv=canon,
        extended_universe_path=uni, profiles_path=tmp_path / "prof.json",
        market_db_path=db, queue_dir=tmp_path, run_date="2026-06-01",
        refresh_fn=lambda syms, profiles_path: 0, store_factory=lambda: store,
        telegram_fn=lambda text, channel: sent.append((text, channel)))
    assert res.error and "restored from backup" in res.error
    assert b._db_tag_symbols(store) == {"AAA"}               # RULEX rolled back
    assert b._read_csv_symbols(canon) == {"AAA"}             # CSV never advanced → lockstep held
    assert sent and sent[0][1] == "group"


def test_cli_weekly_sync_wires_and_exits(monkeypatch, tmp_path):
    import scripts.build_company_concept_registry as b
    called = {}
    def fake_ws(**kw):
        called.update(kw); return b.WeeklySyncResult(drift_in=[], auto_saved=[])
    monkeypatch.setattr(b, "weekly_sync", fake_ws)
    monkeypatch.setattr(b, "send_message", lambda *a, **k: True)
    rc = b.main(["--weekly-sync", "--data-root", str(tmp_path)])
    assert rc == 0
    assert called["telegram_fn"] is b.send_message
    assert called["store_factory"] is not None


def test_bootstrap_normalizes_and_writes_manifest(tmp_path):
    import scripts.build_company_concept_registry as b
    src = tmp_path / "legacy.csv"
    _write(src, ["review_reason", "symbol", "business_role"] + b.REVIEW_CSV_FIELDS[2:],
           [["ok", "AAA", "dup"] + [""] * 14])
    out = tmp_path / "canon.csv"
    rc = b.main(["--bootstrap-canonical", str(src), "--canonical-csv", str(out)])
    assert rc == 0
    assert b._read_csv_symbols(out) == {"AAA"}
    assert b._load_review_manifest(out) == {"AAA"}
