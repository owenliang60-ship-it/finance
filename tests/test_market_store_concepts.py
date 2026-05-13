"""Concept registry tables in market.db: schema + upsert/get coverage."""
import pytest

from src.data.market_store import MarketStore


@pytest.fixture
def store(tmp_path):
    return MarketStore(tmp_path / "test.db")


def _seed_taxonomy(store: MarketStore) -> None:
    store.upsert_concepts([
        {"concept_id": "semiconductor", "label": "半导体", "level": 1, "parent_id": None},
        {"concept_id": "memory", "label": "存储", "level": 2, "parent_id": "semiconductor"},
        {"concept_id": "memory_chips", "label": "存储芯片", "level": 3, "parent_id": "memory"},
        # v2: themes live in concepts table as level=3 (theme_ids must reference level=3).
        {"concept_id": "hbm", "label": "HBM", "level": 3,
         "concept_type": "theme"},
        {"concept_id": "network_equipment", "label": "通信/网络设备", "level": 1, "parent_id": None},
        {"concept_id": "optical_communications", "label": "光通信", "level": 2, "parent_id": "network_equipment"},
    ])
    # Legacy concept_themes table snapshot still seeded so the old
    # test_upsert_concept_themes / rebuild tests retain meaningful fixtures.
    store.upsert_concept_themes([
        {"theme_id": "hbm", "label": "HBM", "parent_concept_id": "memory",
         "lifecycle_state": "active"},
    ])


def test_concept_tables_exist_after_init(store):
    """Fresh MarketStore auto-creates concepts, concept_themes, company_concept_tags."""
    conn = store._get_conn()
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name IN ('concepts','concept_themes','company_concept_tags')"
    ).fetchall()
    assert {r[0] for r in rows} == {"concepts", "concept_themes", "company_concept_tags"}


def test_symbol_concept_edges_table_reserved_for_phase2(store):
    """Phase 2 N:M graph table must be created by Phase 1 init so future
    rollout doesn't require a schema migration. It stays empty in Phase 1."""
    conn = store._get_conn()
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name='symbol_concept_edges'"
    ).fetchone()
    assert row is not None, "symbol_concept_edges should be pre-created"
    # Empty in Phase 1.
    assert conn.execute(
        "SELECT COUNT(*) FROM symbol_concept_edges"
    ).fetchone()[0] == 0


def test_symbol_concept_edges_fk_concept_required(store):
    """Inserting an edge with an unknown concept_id must fail FK validation,
    proving the edges table is wired to the same concepts taxonomy that
    company_concept_tags uses."""
    import sqlite3
    _seed_taxonomy(store)
    conn = store._get_conn()
    # Valid FK target → succeeds.
    with conn:
        conn.execute(
            """INSERT INTO symbol_concept_edges
               (symbol, concept_id, weight, edge_type, confidence, source,
                evidence, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("AAPL", "semiconductor", 1.0, "business_exposure",
             0.9, "manual", "", "2026-04-29T00:00:00Z"),
        )
    # Bogus FK target → IntegrityError (matches company_concept_tags behavior).
    with pytest.raises(sqlite3.IntegrityError):
        with conn:
            conn.execute(
                """INSERT INTO symbol_concept_edges
                   (symbol, concept_id, weight, edge_type, confidence, source,
                    evidence, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                ("AAPL", "nonexistent_concept", 1.0, "business_exposure",
                 0.9, "manual", "", "2026-04-29T00:00:00Z"),
            )


def test_upsert_concepts_preserves_parent_links(store):
    rows = [
        {"concept_id": "semiconductor", "label": "半导体", "level": 1, "parent_id": None},
        {"concept_id": "memory", "label": "存储", "level": 2, "parent_id": "semiconductor"},
        {"concept_id": "memory_chips", "label": "存储芯片", "level": 3, "parent_id": "memory"},
    ]
    assert store.upsert_concepts(rows) == 3

    conn = store._get_conn()
    fetched = conn.execute("SELECT concept_id, parent_id, level FROM concepts").fetchall()
    parent_map = {r[0]: r[1] for r in fetched}
    level_map = {r[0]: r[2] for r in fetched}
    assert parent_map["semiconductor"] is None
    assert parent_map["memory"] == "semiconductor"
    assert parent_map["memory_chips"] == "memory"
    assert level_map["memory_chips"] == 3


def test_upsert_concepts_idempotent_updates_label(store):
    store.upsert_concepts([
        {"concept_id": "semiconductor", "label": "半导体", "level": 1, "parent_id": None},
    ])
    store.upsert_concepts([
        {"concept_id": "semiconductor", "label": "Semis", "level": 1, "parent_id": None},
    ])
    conn = store._get_conn()
    label = conn.execute(
        "SELECT label FROM concepts WHERE concept_id = ?", ("semiconductor",)
    ).fetchone()[0]
    assert label == "Semis"


def test_upsert_concept_themes(store):
    store.upsert_concepts([
        {"concept_id": "memory", "label": "存储", "level": 2, "parent_id": None},
    ])
    assert store.upsert_concept_themes([
        {"theme_id": "hbm", "label": "HBM", "parent_concept_id": "memory",
         "lifecycle_state": "active"},
    ]) == 1

    conn = store._get_conn()
    row = conn.execute(
        "SELECT label, lifecycle_state, parent_concept_id "
        "FROM concept_themes WHERE theme_id = ?", ("hbm",),
    ).fetchone()
    assert row[0] == "HBM"
    assert row[1] == "active"
    assert row[2] == "memory"


def test_upsert_company_concepts_with_theme_ids(store):
    _seed_taxonomy(store)
    rows = [{
        "symbol": "MU",
        "primary_concept_id": "semiconductor",
        "secondary_concept_id": "memory",
        "tertiary_concept_id": "memory_chips",
        "theme_ids": ["hbm"],
        "display_tags": "半导体 / 存储 / HBM",
        "business_role": "DRAM/HBM存储",
        "confidence": 0.98,
        "source": "manual",
        "evidence": "Manual override: DRAM/NAND/HBM supplier",
        "needs_review": 0,
    }]
    assert store.upsert_company_concepts(rows) == 1

    fetched = store.get_company_concepts(["MU"])
    mu = fetched["MU"]
    assert mu["primary_concept_id"] == "semiconductor"
    assert mu["secondary_concept_id"] == "memory"
    assert mu["tertiary_concept_id"] == "memory_chips"
    assert mu["theme_ids"] == ["hbm"]
    assert mu["display_tags"] == "半导体 / 存储 / HBM"
    assert mu["confidence"] == 0.98
    assert mu["source"] == "manual"
    assert mu["needs_review"] == 0


def test_upsert_company_concepts_allows_null_secondary_tertiary(store):
    """tertiary 可空：无真实语义时不强填，schema 必须接受 None."""
    _seed_taxonomy(store)
    store.upsert_company_concepts([{
        "symbol": "TWOLEVEL",
        "primary_concept_id": "semiconductor",
        "secondary_concept_id": None,
        "tertiary_concept_id": None,
        "theme_ids": [],
        "display_tags": "半导体",
        "business_role": "通用半导体",
        "confidence": 0.7,
        "source": "rule",
        "evidence": "",
        "needs_review": 0,
    }])
    row = store.get_company_concepts(["TWOLEVEL"])["TWOLEVEL"]
    assert row["secondary_concept_id"] is None
    assert row["tertiary_concept_id"] is None
    assert row["theme_ids"] == []


def test_upsert_company_concepts_update_replaces_row(store):
    _seed_taxonomy(store)
    base = {
        "symbol": "MU",
        "primary_concept_id": "semiconductor",
        "secondary_concept_id": None,
        "tertiary_concept_id": None,
        "theme_ids": [],
        "display_tags": "半导体",
        "business_role": "DRAM",
        "confidence": 0.5,
        "source": "rule",
        "evidence": "",
        "needs_review": 1,
    }
    store.upsert_company_concepts([base])

    upgraded = dict(base)
    upgraded.update({
        "secondary_concept_id": "memory",
        "tertiary_concept_id": "memory_chips",
        "theme_ids": ["hbm"],
        "display_tags": "半导体 / 存储 / HBM",
        "business_role": "DRAM/HBM存储",
        "confidence": 0.98,
        "source": "manual",
        "evidence": "fixed",
        "needs_review": 0,
    })
    store.upsert_company_concepts([upgraded])

    mu = store.get_company_concepts(["MU"])["MU"]
    assert mu["confidence"] == 0.98
    assert mu["source"] == "manual"
    assert mu["business_role"] == "DRAM/HBM存储"
    assert mu["needs_review"] == 0
    assert mu["theme_ids"] == ["hbm"]


def test_get_company_concepts_skips_missing_symbols(store):
    _seed_taxonomy(store)
    store.upsert_company_concepts([
        {"symbol": "MU", "primary_concept_id": "semiconductor",
         "secondary_concept_id": None, "tertiary_concept_id": None,
         "theme_ids": [], "display_tags": "半导体", "business_role": "DRAM",
         "confidence": 0.98, "source": "manual", "evidence": "", "needs_review": 0},
    ])
    fetched = store.get_company_concepts(["MU", "ZZZZ"])
    assert set(fetched.keys()) == {"MU"}


def test_get_company_concept_coverage(store):
    _seed_taxonomy(store)
    store.upsert_company_concepts([
        {"symbol": "A", "primary_concept_id": "semiconductor",
         "secondary_concept_id": None, "tertiary_concept_id": None,
         "theme_ids": [], "display_tags": "半导体", "business_role": "X",
         "confidence": 0.98, "source": "manual", "evidence": "", "needs_review": 0},
        {"symbol": "B", "primary_concept_id": "semiconductor",
         "secondary_concept_id": None, "tertiary_concept_id": None,
         "theme_ids": [], "display_tags": "半导体", "business_role": "X",
         "confidence": 0.6, "source": "rule", "evidence": "", "needs_review": 0},
        {"symbol": "C", "primary_concept_id": "semiconductor",
         "secondary_concept_id": None, "tertiary_concept_id": None,
         "theme_ids": [], "display_tags": "其他", "business_role": "",
         "confidence": 0.2, "source": "fallback", "evidence": "", "needs_review": 1},
    ])
    coverage = store.get_company_concept_coverage()
    assert coverage["total"] == 3
    assert coverage["manual"] == 1
    assert coverage["rule"] == 1
    assert coverage["fallback"] == 1
    assert coverage["needs_review"] == 1


def test_foreign_key_concept_must_exist(store):
    """Refusing a non-existent concept_id ensures Phase 1 build order is enforced."""
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        store.upsert_company_concepts([{
            "symbol": "X",
            "primary_concept_id": "nonexistent",
            "secondary_concept_id": None,
            "tertiary_concept_id": None,
            "theme_ids": [],
            "display_tags": "",
            "business_role": "",
            "confidence": 0.5,
            "source": "rule",
            "evidence": "",
            "needs_review": 0,
        }])


# ---- v2 rebuild_concept_tree (atomic migration, FK-safe) ----


def test_rebuild_concept_tree_atomic_with_fk_themes_present(tmp_path):
    """FK 风险用例：concept_themes 5 条历史快照引用旧 concepts。
    rebuild_concept_tree 必须先 NULL parent_concept_id 再 DELETE concepts，否则 FK 失败。
    """
    db = tmp_path / "market.db"
    store = MarketStore(db_path=db)
    # 种子：旧 concepts + 旧 concept_themes 引用 (FK 必然存在)
    store.upsert_concepts([{"concept_id": "old_semi", "label": "旧半导体", "level": 1}])
    store.upsert_concept_themes([{"theme_id": "hbm", "label": "HBM",
                                  "parent_concept_id": "old_semi"}])

    new_concepts = [
        {"concept_id": "semiconductor", "label": "半导体", "level": 1},
        {"concept_id": "gpu_accelerator", "label": "GPU加速器", "level": 2,
         "parent_id": "semiconductor"},
        {"concept_id": "ai_compute", "label": "AI算力", "level": 3,
         "concept_type": "theme"},
    ]
    inserted = store.rebuild_concept_tree(new_concepts)
    assert inserted == 3
    # 旧 concepts 必须清空
    conn = store._get_conn()
    rows = conn.execute(
        "SELECT concept_id FROM concepts ORDER BY concept_id"
    ).fetchall()
    assert [r[0] for r in rows] == ["ai_compute", "gpu_accelerator", "semiconductor"]
    # 旧 concept_themes 行保留但 parent_concept_id 已切到 NULL
    themes = conn.execute(
        "SELECT theme_id, parent_concept_id FROM concept_themes"
    ).fetchall()
    assert len(themes) == 1
    assert themes[0]["theme_id"] == "hbm"
    assert themes[0]["parent_concept_id"] is None


def test_rebuild_concept_tree_clears_company_concept_tags(tmp_path):
    """concepts 重建必须先清 company_concept_tags（FK references concepts）。"""
    db = tmp_path / "market.db"
    store = MarketStore(db_path=db)
    store.upsert_concepts([{"concept_id": "old", "label": "old", "level": 1}])
    store.upsert_company_concepts([{
        "symbol": "FOO",
        "primary_concept_id": "old",
        "secondary_concept_id": None,
        "tertiary_concept_id": None,
        "theme_ids": [],
        "display_tags": "old",
        "business_role": "",
        "confidence": 0.5,
        "source": "rule",
        "evidence": "",
        "needs_review": 0,
    }])

    store.rebuild_concept_tree([
        {"concept_id": "new", "label": "new", "level": 1},
    ])

    conn = store._get_conn()
    cct_rows = conn.execute("SELECT symbol FROM company_concept_tags").fetchall()
    assert cct_rows == []  # 清空 — Boss 审改后重新 upsert


def test_rebuild_concept_tree_rollback_on_error(tmp_path):
    """事务内任一 INSERT 失败必须整体回滚。"""
    db = tmp_path / "market.db"
    store = MarketStore(db_path=db)
    store.upsert_concepts([{"concept_id": "keep", "label": "保留", "level": 1}])

    bad_rows = [
        {"concept_id": "new1", "label": "new1", "level": 1},
        {"concept_id": "new2", "level": 2},  # missing label → KeyError / INSERT raises
    ]
    with pytest.raises(Exception):
        store.rebuild_concept_tree(bad_rows)

    # 旧数据必须仍在
    conn = store._get_conn()
    rows = conn.execute("SELECT concept_id FROM concepts").fetchall()
    assert [r[0] for r in rows] == ["keep"]


# ---- Task 8: theme_ids level=3 guard ----


def test_upsert_rejects_theme_ids_pointing_to_non_level3(tmp_path):
    """theme_ids 元素必须指向 concepts.level=3，指向 L1/L2 应被拒。"""
    store = MarketStore(tmp_path / "market.db")
    _seed_taxonomy(store)
    bad = [{
        "symbol": "X",
        "primary_concept_id": "semiconductor",
        "secondary_concept_id": "memory",
        "tertiary_concept_id": None,
        "theme_ids": ["semiconductor"],   # L1, not L3
        "display_tags": "半导体 / 存储",
        "business_role": "",
        "confidence": 0.5,
        "source": "manual",
        "evidence": "",
        "needs_review": 0,
    }]
    with pytest.raises(ValueError, match="theme_ids must reference level=3"):
        store.upsert_company_concepts(bad)


def test_upsert_rejects_theme_ids_pointing_to_unknown_concept(tmp_path):
    """theme_id 不存在于 concepts 表 → 拒绝（防御 FK 漏检）。"""
    store = MarketStore(tmp_path / "market.db")
    _seed_taxonomy(store)
    bad = [{
        "symbol": "X",
        "primary_concept_id": "semiconductor",
        "secondary_concept_id": "memory",
        "tertiary_concept_id": None,
        "theme_ids": ["does_not_exist"],
        "display_tags": "x",
        "business_role": "",
        "confidence": 0.5,
        "source": "manual",
        "evidence": "",
        "needs_review": 0,
    }]
    with pytest.raises(ValueError, match="theme_ids must reference level=3"):
        store.upsert_company_concepts(bad)
