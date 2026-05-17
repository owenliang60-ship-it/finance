"""ConceptRegistry v2 runtime tests (anchor + industry_map rule + alias +
unclassified). The v1 substring keyword_rules were replaced 2026-05-16 by an
exact FMP (sector|industry) → (l1,l2) lookup — see issue 025 and
docs/plans/2026-05-16-concept-rule-classifier-rebuild.md."""
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
CONFIG_DIR = PROJECT_ROOT / "config" / "concepts"
TAXONOMY_V2_PATH = CONFIG_DIR / "concept_taxonomy_v2.json"
OVERRIDES_PATH = CONFIG_DIR / "company_concept_overrides.json"
WATCHLIST_PATH = CONFIG_DIR / "concept_watchlist.json"


@pytest.fixture(scope="module")
def registry():
    from terminal.company_concepts import ConceptRegistry
    return ConceptRegistry(
        taxonomy_path=TAXONOMY_V2_PATH,
        overrides_path=OVERRIDES_PATH,
        watchlist_path=WATCHLIST_PATH,
    )


# ---- anchor branch ----


def test_v2_anchor_override_takes_priority(registry):
    """Anchor 主链路覆盖 industry 信号：AMZN 走 AI算力与云不走零售。"""
    result = registry.classify({"symbol": "AMZN", "industry": "Internet Retail"})
    assert result["l1"] == "ai_compute_cloud"
    assert result["l2"] == "hyperscaler"
    assert "ai_compute" in result["l3_themes"]
    assert result["source"] == "manual"


def test_v2_anchor_multi_theme_msft(registry):
    """MSFT 锚定 3 个 L3 主题（ai_compute / ai_application_layer / edge_ai）。"""
    result = registry.classify({"symbol": "MSFT", "industry": "Software"})
    assert result["l1"] == "ai_compute_cloud"
    assert result["l2"] == "hyperscaler"
    assert set(result["l3_themes"]) >= {"ai_compute", "ai_application_layer", "edge_ai"}
    assert result["source"] == "manual"


# ---- industry_map exact-lookup rule branch ----


def test_v2_classify_rule_hit_via_industry_map(registry):
    """A (sector, industry) pair in industry_map → deterministic source=rule
    with confidence 0.7, empty L3 list and no business_role."""
    result = registry.classify({
        "symbol": "RTX",
        "sector": "Industrials",
        "industry": "Aerospace & Defense",
    })
    assert result["l1"] == "industrial_aerospace"
    assert result["l2"] == "aerospace_defense"
    assert result["l3_themes"] == []
    assert result["business_role"] == ""
    assert result["source"] == "rule"
    assert result["confidence"] == 0.7
    assert result["needs_review"] == 0


def test_v2_classify_rule_hit_regulated_electric(registry):
    result = registry.classify({
        "symbol": "DUK",
        "sector": "Utilities",
        "industry": "Regulated Electric",
    })
    assert (result["l1"], result["l2"]) == ("realestate_utility", "power_utility")
    assert result["source"] == "rule"


def test_v2_classify_telecom_operator_l2(registry):
    """telecom_operator 是 2026-05-16 重建新增的 L2，挂在 internet_software。"""
    result = registry.classify({
        "symbol": "VZ",
        "sector": "Communication Services",
        "industry": "Telecommunications Services",
    })
    assert result["l1"] == "internet_software"
    assert result["l2"] == "telecom_operator"
    assert result["source"] == "rule"


# ---- unclassified branch (→ LLM prefill in the builder) ----


def test_v2_classify_ambiguous_industry_is_unclassified(registry):
    """Semiconductors 故意不入 industry_map —— industry 信号定不了 7 个半导体
    L2 子桶中的哪一个 → unclassified，交 builder 的 LLM prefill。"""
    result = registry.classify({
        "symbol": "NVDA",
        "sector": "Technology",
        "industry": "Semiconductors",
    })
    assert result["source"] == "unclassified"
    assert result["l1"] is None
    assert result["l2"] is None
    assert result["l3_themes"] == []
    assert result["needs_review"] == 1
    assert result["confidence"] == 0.0


def test_v2_classify_returns_unclassified_when_override_and_rule_both_miss(registry):
    """v2 不再有 legacy bucket fallback。registry.classify 内不调用 LLM
    (LLM 是 builder 编排层职责)。缺 sector → 无法构造 industry_map key。"""
    result = registry.classify({
        "symbol": "OBSCURE",
        "industry": "Completely Unknown Sector",
        "description": "Mystery company",
    })
    assert result["source"] == "unclassified"
    assert result["l1"] is None
    assert result["l2"] is None
    assert result["l3_themes"] == []
    assert result["needs_review"] == 1
    assert result["confidence"] == 0.0


# ---- issue 025 regressions: description text must never drive classify ----


def test_v2_classify_no_false_hit_on_description(registry):
    """issue 025: 财团/宽泛业务公司的 description 偶然撞上无关行业关键词。
    A+ 只看 (sector, industry)，从不读 description —— BRK 类公司不再误分。"""
    result = registry.classify({
        "symbol": "TESTCO",
        "sector": "Financial Services",
        "industry": "Insurance - Diversified",
        "description": "holding company with restaurants, data center, mining "
                       "and consumer electronics subsidiaries",
    })
    assert result["l1"] == "finance"
    assert result["l2"] == "insurance"
    assert result["source"] == "rule"


def test_v2_classify_electric_utility_not_clean_energy(registry):
    """issue 025: ~20 家受监管电力公用被误分到「新能源」。精确映射把
    Regulated Electric 一律送 power_utility，绝不命中 clean_energy。"""
    result = registry.classify({
        "symbol": "SO",
        "sector": "Utilities",
        "industry": "Regulated Electric",
        "description": "electric utility investing in solar and renewable energy",
    })
    assert result["l2"] == "power_utility"
    assert result["l2"] != "clean_energy"


# ---- L3 alias resolver ----


def test_v2_resolve_l3_alias_maps_chinese_label_to_id(registry):
    assert registry.resolve_l3_alias("AI算力") == "ai_compute"
    assert registry.resolve_l3_alias("液冷") == "datacenter_cooling"
    assert registry.resolve_l3_alias("foo_unknown") is None


def test_v2_resolve_l3_alias_accepts_concept_id_directly(registry):
    """直接传 concept_id 也能正常返回（idempotent)。"""
    assert registry.resolve_l3_alias("ai_compute") == "ai_compute"
    assert registry.resolve_l3_alias("storage") == "storage"


def test_v2_resolve_l3_alias_ignores_l1_l2_ids(registry):
    """L1/L2 不在 L3 alias pool — 不应被识别为 L3。"""
    assert registry.resolve_l3_alias("semiconductor") is None  # L1
    assert registry.resolve_l3_alias("gpu_accelerator") is None  # L2


# ---- display_tags & priority_list ----


def test_v2_display_tags_two_segments_when_no_l3(registry):
    """rule 命中不带 L3 → display_tags 只有 2 段（不带尾巴）。"""
    result = registry.classify({
        "symbol": "DUK",
        "sector": "Utilities",
        "industry": "Regulated Electric",
    })
    assert result["display_tags"] == "地产与公用 / 电力与公用"
    assert not result["display_tags"].endswith("/")


def test_v2_display_tags_three_segments_with_l3_first(registry):
    """有 L3 时拼三段，取 theme_ids 首位。"""
    result = registry.classify({"symbol": "AMZN"})
    parts = result["display_tags"].split(" / ")
    assert parts[0] == "AI算力与云"
    assert parts[1] == "超大规模云"
    assert parts[2] == "AI算力"  # ai_compute label


def test_v2_priority_list_matches_plan_definition(registry):
    """priority_list = portfolio ∪ watchlist ∪ broad_top；anchor 不自动 force-add。"""
    priority = registry.priority_list(
        broad_top_symbols=["AAPL", "MSFT"],
        portfolio_holdings=["NVDA"],
    )
    assert {"AAPL", "MSFT", "NVDA"}.issubset(priority)
    # POET in watchlist
    assert "POET" in priority


def test_v2_classify_accepts_string_symbol(registry):
    """classify('AMZN') 简写形式应等价于 classify({'symbol': 'AMZN'})。"""
    by_str = registry.classify("AMZN")
    by_dict = registry.classify({"symbol": "AMZN"})
    assert by_str == by_dict


# ---- industry_map property ----


def test_v2_industry_map_property_exposes_82_pairs(registry):
    im = registry.industry_map
    assert len(im) == 82
    assert im["Utilities|Regulated Electric"] == {
        "l1": "realestate_utility", "l2": "power_utility",
    }
    # property returns a copy — mutating it must not corrupt the registry
    im.clear()
    assert len(registry.industry_map) == 82
