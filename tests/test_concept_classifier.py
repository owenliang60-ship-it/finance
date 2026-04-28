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


# ---------- Registry-aware classifier (Task 5) ----------

import pytest  # noqa: E402

CFG = PROJECT_ROOT / "config" / "concepts"
TAXONOMY_PATH = CFG / "taxonomy.json"
THEMES_PATH = CFG / "concept_themes.json"
OVERRIDES_PATH = CFG / "company_concept_overrides.json"
WATCHLIST_PATH = CFG / "concept_watchlist.json"


@pytest.fixture
def store_with_registry(tmp_path):
    """A MarketStore prepopulated with MU + POET + TWOLEVEL via the build pipeline."""
    from src.data.market_store import MarketStore
    from terminal.company_concepts import ConceptRegistry
    from scripts.build_company_concept_registry import build_registry

    store = MarketStore(tmp_path / "market.db")
    registry = ConceptRegistry(
        taxonomy_path=TAXONOMY_PATH,
        themes_path=THEMES_PATH,
        overrides_path=OVERRIDES_PATH,
        watchlist_path=WATCHLIST_PATH,
    )
    profiles = {
        "MU": {"symbol": "MU", "industry": "Semiconductors"},
        "POET": {"symbol": "POET", "industry": "Semiconductors"},
        # A two-level rule-based row to test no-tertiary display
        "TSM": {"symbol": "TSM", "industry": "Foundry"},
    }
    build_registry(
        store=store, registry=registry,
        universe_symbols=["MU", "POET", "TSM"],
        profiles=profiles,
        portfolio_holdings=["MU", "POET", "TSM"],
        broad_top_symbols=["MU", "POET", "TSM"],
        review_csv_path=tmp_path / "review.csv",
        save=True, force_save=True,
    )
    return store


def test_display_tags_registry_hit_mu(store_with_registry):
    clf = ConceptClassifier(REPORT_CONCEPTS_PATH, market_store=store_with_registry)
    assert clf.display_tags({"symbol": "MU"}) == "半导体 / 存储 / HBM"
    assert clf.concept_tags({"symbol": "MU"}) == ["半导体", "存储", "HBM"]


def test_display_tags_registry_hit_poet(store_with_registry):
    clf = ConceptClassifier(REPORT_CONCEPTS_PATH, market_store=store_with_registry)
    assert clf.display_tags({"symbol": "POET"}) == "通信/网络设备 / 光通信 / InP光接口"


def test_display_tags_registry_two_level(store_with_registry):
    """TSM override has two levels (半导体/晶圆代工); display must not have a trailing slash."""
    clf = ConceptClassifier(REPORT_CONCEPTS_PATH, market_store=store_with_registry)
    display = clf.display_tags({"symbol": "TSM"})
    assert display == "半导体 / 晶圆代工"
    assert not display.endswith("/")
    assert " /  " not in display


def test_display_tags_registry_miss_falls_back_to_legacy(store_with_registry):
    """NVDA not in registry → falls back to legacy JSON bucket."""
    clf = ConceptClassifier(REPORT_CONCEPTS_PATH, market_store=store_with_registry)
    display = clf.display_tags({"symbol": "NVDA",
                                "companyName": "NVIDIA Corporation",
                                "industry": "Semiconductors"})
    assert display == "AI算力/云"   # legacy single bucket


def test_display_tags_no_store_uses_legacy_only():
    """When market_store=None, classifier never queries DB."""
    clf = ConceptClassifier(REPORT_CONCEPTS_PATH, market_store=None)
    display = clf.display_tags({"symbol": "NVDA",
                                "companyName": "NVIDIA Corporation",
                                "industry": "Semiconductors"})
    assert display == "AI算力/云"


def test_business_role_uses_registry_when_available(store_with_registry):
    """Registry row's business_role wins over legacy keyword rules."""
    clf = ConceptClassifier(REPORT_CONCEPTS_PATH, market_store=store_with_registry)
    # MU's manual override role differs from a generic FMP-derived label.
    assert clf.business_role({"symbol": "MU", "industry": "Semiconductors"}) == "DRAM/HBM存储"
    # POET registry role is "光通信/光器件(InP optical interposer)" — registry wins
    # even when the profile says Semiconductors (which would mislead legacy rules).
    poet_role = clf.business_role({"symbol": "POET", "industry": "Semiconductors"})
    assert "光通信" in poet_role


def test_group_items_uses_registry_primary_over_legacy_bucket(store_with_registry):
    """A POET-style registry override must drive the section bucket, even when
    the item's pre-computed `concept_bucket` (from legacy classify) is wrong."""
    clf = ConceptClassifier(REPORT_CONCEPTS_PATH, market_store=store_with_registry)
    # Simulate a stale classify result on the item (legacy assigned 半导体链
    # because POET's profile literally says Semiconductors).
    items = [
        {"symbol": "POET", "concept_bucket": "半导体链",
         "industry": "Semiconductors"},
        {"symbol": "MU", "concept_bucket": "半导体链"},
    ]
    grouped = clf.group_items(items)
    # POET should land under registry primary (network_equipment → 通信/网络设备)
    poet_buckets = [b for b, rows in grouped.items()
                    if any(r["symbol"] == "POET" for r in rows)]
    assert poet_buckets == ["通信/网络设备"]
    # MU stays under 半导体链 (registry primary semiconductor → legacy alias).
    mu_buckets = [b for b, rows in grouped.items()
                  if any(r["symbol"] == "MU" for r in rows)]
    assert mu_buckets == ["半导体链"]


def test_business_role_falls_back_to_legacy_when_unregistered(store_with_registry):
    """Unregistered symbols still get legacy role mapping."""
    clf = ConceptClassifier(REPORT_CONCEPTS_PATH, market_store=store_with_registry)
    role = clf.business_role({"symbol": "NVDA",
                              "companyName": "NVIDIA Corporation",
                              "industry": "Semiconductors"})
    assert role == "GPU/AI加速器"


def test_db_unavailable_does_not_crash(tmp_path):
    """Broken DB file → classifier silently falls back to legacy."""
    from src.data.market_store import MarketStore

    # Create a store, then corrupt the DB file before classifying
    store = MarketStore(tmp_path / "broken.db")
    # Close + truncate to force a query failure during classify().
    store.close()
    (tmp_path / "broken.db").write_bytes(b"not a sqlite db")

    clf = ConceptClassifier(REPORT_CONCEPTS_PATH, market_store=store)
    # Should not raise; falls back to legacy.
    display = clf.display_tags({"symbol": "NVDA",
                                "companyName": "NVIDIA Corporation",
                                "industry": "Semiconductors"})
    assert display == "AI算力/云"
