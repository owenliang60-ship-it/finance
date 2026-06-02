"""Tests for report concept classifier config and presentation labels."""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import REPORT_CONCEPTS_PATH
from terminal.concept_classifier import ConceptClassifier


def classifier() -> ConceptClassifier:
    return ConceptClassifier(REPORT_CONCEPTS_PATH)


def test_symbol_override_and_business_role():
    clf = classifier()
    item = {
        "symbol": "NVDA",
        "companyName": "NVIDIA Corporation",
        "industry": "Semiconductors",
    }

    assert clf.classify(item) == "AI算力/云"
    assert clf.business_role(item) == "GPU/AI加速器"


def test_etf_override():
    clf = classifier()

    assert clf.classify({"symbol": "SOXX", "companyName": "iShares Semiconductor ETF"}) == "ETF/宏观工具"
    assert clf.business_role({"symbol": "SOXX"}) == "ETF/指数工具"


def test_keyword_fallback_for_poet_like_optical_name():
    clf = classifier()
    item = {
        "symbol": "POET",
        "companyName": "POET Technologies optical interposer platform",
        "sector": "Technology",
        "industry": "Optical Networking",
    }

    assert clf.classify(item) == "通信/网络设备"
    assert clf.business_role(item) == "光通信/光器件"


def test_unknown_defaults_to_other_and_pending_role():
    clf = classifier()
    item = {"symbol": "ZZZZ", "companyName": "Unmapped Holding Company"}

    assert clf.classify(item) == "其他"
    assert clf.business_role(item) == "待补业务标签"


def test_business_role_respects_existing_concept_bucket():
    clf = classifier()
    item = {"symbol": "ZZZZ", "companyName": "Unknown", "concept_bucket": "软件/SaaS"}

    assert clf.business_role(item) == "企业软件/SaaS"


def test_group_items_preserves_bucket_order():
    clf = classifier()
    grouped = clf.group_items([
        {"symbol": "TSLA", "concept_bucket": "自动驾驶/机器人"},
        {"symbol": "NVDA"},
        {"symbol": "MU"},
    ])

    assert list(grouped.keys()) == ["AI算力/云", "半导体链", "自动驾驶/机器人"]


# ---------- Registry-aware classifier (v2: Task 9) ----------
#
# v2 contract:
#   - display_tags() returns the DB canonical 3-segment Chinese string,
#     written by build pipeline Phase 6 (Task 8) into company_concept_tags.
#   - concept_tags() splits that string back into a list.
#   - _grouping_bucket() maps registry primary_concept_id → legacy section
#     bucket via _CONCEPT_TO_LEGACY_BUCKET (11 L1 → 14 legacy buckets so
#     morning report section headers stay stable).
#   - Unregistered symbols fall through to legacy ConceptClassifier.classify().

import pytest  # noqa: E402


def _seed_v2_registry(tmp_path, rows: dict[str, dict]):
    """Bootstrap a MarketStore with v2 concept rows + canonical display_tags.

    `rows` shape: {symbol: {"primary": "semiconductor",
                            "secondary": "gpu_accelerator",
                            "display": "半导体 / 计算芯片/GPU加速器 / AI算力",
                            "business_role": "...", "theme_ids": [...]}}
    """
    from src.data.market_store import MarketStore
    store = MarketStore(tmp_path / "market.db")
    # Seed the v2 concepts referenced by `rows` plus a couple of generic L1s.
    seeded_concepts = [
        {"concept_id": "semiconductor", "label": "半导体", "level": 1, "parent_id": None},
        {"concept_id": "consumer_retail", "label": "消费与零售", "level": 1, "parent_id": None},
        {"concept_id": "internet_software", "label": "互联网与软件", "level": 1, "parent_id": None},
        {"concept_id": "gpu_accelerator", "label": "计算芯片/GPU加速器", "level": 2, "parent_id": "semiconductor"},
        {"concept_id": "consumer_staples", "label": "必需消费品", "level": 2, "parent_id": "consumer_retail"},
        {"concept_id": "ai_compute", "label": "AI算力", "level": 3, "concept_type": "theme"},
    ]
    store.upsert_concepts(seeded_concepts)
    upserts = []
    for sym, r in rows.items():
        upserts.append({
            "symbol": sym,
            "primary_concept_id": r["primary"],
            "secondary_concept_id": r.get("secondary"),
            "tertiary_concept_id": None,
            "theme_ids": r.get("theme_ids", []),
            "display_tags": r["display"],
            "business_role": r.get("business_role", ""),
            "confidence": 0.95,
            "source": "manual",
            "evidence": "",
            "needs_review": 0,
        })
    store.upsert_company_concepts(upserts)
    return store


def test_classifier_display_tags_returns_db_canonical(tmp_path):
    store = _seed_v2_registry(tmp_path, {
        "NVDA": {"primary": "semiconductor", "secondary": "gpu_accelerator",
                 "theme_ids": ["ai_compute"],
                 "display": "半导体 / 计算芯片/GPU加速器 / AI算力"},
        "KO": {"primary": "consumer_retail", "secondary": "consumer_staples",
               "display": "消费与零售 / 必需消费品"},
    })
    clf = ConceptClassifier(REPORT_CONCEPTS_PATH, market_store=store)
    assert clf.display_tags({"symbol": "NVDA"}) == "半导体 / 计算芯片/GPU加速器 / AI算力"
    assert clf.display_tags({"symbol": "KO"}) == "消费与零售 / 必需消费品"
    # Unregistered symbol falls back to legacy single-bucket
    assert clf.display_tags({"symbol": "SPY", "industry": "ETF"}) == "ETF/宏观工具"


def test_classifier_concept_tags_returns_split_list(tmp_path):
    store = _seed_v2_registry(tmp_path, {
        "NVDA": {"primary": "semiconductor", "secondary": "gpu_accelerator",
                 "theme_ids": ["ai_compute"],
                 "display": "半导体 / 计算芯片/GPU加速器 / AI算力"},
    })
    clf = ConceptClassifier(REPORT_CONCEPTS_PATH, market_store=store)
    tags = clf.concept_tags({"symbol": "NVDA"})
    assert tags == ["半导体", "计算芯片/GPU加速器", "AI算力"]


def test_grouping_bucket_maps_v2_l1_to_legacy_bucket(tmp_path):
    """For section grouping, new 11 L1 → legacy 14 bucket via _CONCEPT_TO_LEGACY_BUCKET."""
    store = _seed_v2_registry(tmp_path, {
        "NVDA": {"primary": "semiconductor", "secondary": "gpu_accelerator",
                 "display": "半导体 / 计算芯片/GPU加速器"},
    })
    clf = ConceptClassifier(REPORT_CONCEPTS_PATH, market_store=store)
    bucket = clf._grouping_bucket({"symbol": "NVDA"})
    assert bucket == "半导体链"   # legacy section header continuity


def test_classifier_unregistered_symbol_falls_back_to_legacy(tmp_path):
    """Symbol absent from registry → legacy ConceptClassifier.classify() path."""
    store = _seed_v2_registry(tmp_path, {})
    clf = ConceptClassifier(REPORT_CONCEPTS_PATH, market_store=store)
    # NVDA isn't in our minimal seed; falls back to legacy keyword path.
    display = clf.display_tags({"symbol": "NVDA",
                                "companyName": "NVIDIA Corporation",
                                "industry": "Semiconductors"})
    assert display == "AI算力/云"


def test_classifier_no_store_uses_legacy_only():
    """When market_store=None, classifier never queries DB."""
    clf = ConceptClassifier(REPORT_CONCEPTS_PATH, market_store=None)
    display = clf.display_tags({"symbol": "NVDA",
                                "companyName": "NVIDIA Corporation",
                                "industry": "Semiconductors"})
    assert display == "AI算力/云"


def test_db_unavailable_does_not_crash(tmp_path):
    """Broken DB file → classifier silently falls back to legacy."""
    from src.data.market_store import MarketStore

    store = MarketStore(tmp_path / "broken.db")
    store.close()
    (tmp_path / "broken.db").write_bytes(b"not a sqlite db")

    clf = ConceptClassifier(REPORT_CONCEPTS_PATH, market_store=store)
    display = clf.display_tags({"symbol": "NVDA",
                                "companyName": "NVIDIA Corporation",
                                "industry": "Semiconductors"})
    assert display == "AI算力/云"


def test_classifier_v2_internet_software_maps_to_internet_ads_bucket(tmp_path):
    """internet_software L1 → '互联网/广告' legacy section."""
    store = _seed_v2_registry(tmp_path, {
        "GOOG": {"primary": "internet_software", "secondary": None,
                 "display": "互联网与软件"},
    })
    clf = ConceptClassifier(REPORT_CONCEPTS_PATH, market_store=store)
    bucket = clf._grouping_bucket({"symbol": "GOOG"})
    assert bucket == "互联网/广告"


def test_l2_bucket_order_uses_all_61_taxonomy_l2(tmp_path):
    """l2_bucket_order must list ALL 61 L2 from the taxonomy SSOT (DB concepts
    table when present), not just the L2 currently used by tagged companies —
    else a future stock in the 61st bucket is hidden by group_items()."""
    from src.data.market_store import MarketStore
    import json
    from config.settings import CONCEPT_TAXONOMY_PATH

    taxonomy = json.loads(CONCEPT_TAXONOMY_PATH.read_text(encoding="utf-8"))
    l2_concepts = [c for c in taxonomy["concepts"] if c.get("level") == 2]
    assert len(l2_concepts) == 61  # SSOT truth (verified 2026-06-02)

    store = MarketStore(tmp_path / "market.db")
    # ⚠️ concepts.parent_id is a self-FK (`parent_id TEXT REFERENCES
    # concepts(concept_id)`) and MarketStore opens `PRAGMA foreign_keys=ON`
    # (market_store.py:563). The 61 L2 reference 11 distinct L1 parents, so
    # inserting L2 rows before their L1 parents raises
    # `IntegrityError: FOREIGN KEY constraint failed` (reproduced 2026-06-02).
    # rebuild_concept_tree (market_store.py:1568) is the FK-safe loader: it
    # seeds the whole tree in ONE transaction ordered L1→L2→L3 under
    # `defer_foreign_keys=ON`. Pass the full taxonomy (L1+L2+L3), not just L2.
    store.rebuild_concept_tree(taxonomy["concepts"])
    clf = ConceptClassifier(REPORT_CONCEPTS_PATH, market_store=store)
    order = clf.l2_bucket_order
    assert len(order) == 61
    # taxonomy order is preserved (gpu_accelerator label appears before memory_chip)
    assert order.index("计算芯片/GPU加速器") < order.index("存储芯片")
    # all labels are L2 labels, none from the legacy 15-bucket set
    assert "半导体链" not in order


def test_l2_bucket_order_falls_back_to_taxonomy_json_without_store():
    """No market_store → l2_bucket_order reads the taxonomy JSON SSOT, still 61."""
    clf = ConceptClassifier(REPORT_CONCEPTS_PATH, market_store=None)
    order = clf.l2_bucket_order
    assert len(order) == 61
    assert "计算芯片/GPU加速器" in order
    assert "存储芯片" in order
