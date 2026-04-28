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
        {"concept_id": "network_equipment", "label": "通信/网络设备", "level": 1, "parent_id": None},
        {"concept_id": "optical_communications", "label": "光通信", "level": 2, "parent_id": "network_equipment"},
    ])
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
