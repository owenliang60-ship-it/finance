"""ConceptRegistry v2 runtime tests (anchor + rule + alias + unclassified)."""
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


# ---- core v2 classify chain ----


def test_v2_classify_returns_l3_list_and_business_role(registry):
    result = registry.classify({
        "symbol": "NVDA",
        "industry": "Semiconductors",
        "description": "GPU and AI accelerator",
    })
    assert result["l1"] == "semiconductor"
    assert result["l2"] == "gpu_accelerator"
    assert isinstance(result["l3_themes"], list)
    assert result["source"] == "rule"
    assert result["confidence"] >= 0.6


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


def test_v2_classify_returns_unclassified_when_override_and_rule_both_miss(registry):
    """v2 不再有 legacy bucket fallback。registry.classify 内不调用 LLM
    (LLM 是 builder 编排层职责，Task 4b)。"""
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


# ---- L3 alias resolver ----


def test_v2_resolve_l3_alias_maps_chinese_label_to_id(registry):
    assert registry.resolve_l3_alias("AI算力") == "ai_compute"
    assert registry.resolve_l3_alias("液冷") == "datacenter_cooling"
    assert registry.resolve_l3_alias("foo_unknown") is None


def test_v2_resolve_l3_alias_accepts_concept_id_directly(registry):
    """直接传 concept_id 也能正常返回（idempotent)。"""
    assert registry.resolve_l3_alias("ai_compute") == "ai_compute"
    assert registry.resolve_l3_alias("hbm") == "hbm"


def test_v2_resolve_l3_alias_ignores_l1_l2_ids(registry):
    """L1/L2 不在 L3 alias pool — 不应被识别为 L3。"""
    assert registry.resolve_l3_alias("semiconductor") is None  # L1
    assert registry.resolve_l3_alias("gpu_accelerator") is None  # L2


# ---- keyword rule coverage ----


def test_v2_rule_asml_semi_equipment(registry):
    result = registry.classify({
        "symbol": "ASML",
        "industry": "Semiconductor Equipment & Materials",
        "description": "lithography systems",
    })
    assert result["l1"] == "semiconductor"
    assert result["l2"] == "semi_equipment"
    assert result["source"] == "rule"


def test_v2_rule_tsm_foundry(registry):
    result = registry.classify({
        "symbol": "TSM",
        "industry": "Semiconductors",
        "description": "wafer foundry",
    })
    assert result["l1"] == "semiconductor"
    assert result["l2"] == "foundry"


def test_v2_rule_mu_memory(registry):
    """MU 不在 anchor，走 keyword rule (dram/nand) → memory_chip L2。"""
    result = registry.classify({
        "symbol": "MU",
        "industry": "Semiconductors",
        "description": "DRAM and NAND memory chip manufacturer",
    })
    assert result["l1"] == "semiconductor"
    assert result["l2"] == "memory_chip"


# ---- display_tags & priority_list ----


def test_v2_display_tags_two_segments_when_no_l3(registry):
    """无 L3 时 display_tags 只有 2 段（不带尾巴）。"""
    result = registry.classify({
        "symbol": "TSM",
        "industry": "Semiconductors",
        "description": "wafer foundry contract manufacturer",
    })
    assert result["display_tags"] == "半导体 / 晶圆代工"
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
