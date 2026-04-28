"""Concept registry: config validation (Task 2) + ConceptRegistry runtime (Task 3)."""
import json
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
CONFIG_DIR = PROJECT_ROOT / "config" / "concepts"
TAXONOMY_PATH = CONFIG_DIR / "taxonomy.json"
THEMES_PATH = CONFIG_DIR / "concept_themes.json"
OVERRIDES_PATH = CONFIG_DIR / "company_concept_overrides.json"
WATCHLIST_PATH = CONFIG_DIR / "concept_watchlist.json"


# ---------- Task 2: config schema validation ----------

@pytest.fixture(scope="module")
def taxonomy() -> dict:
    return json.loads(TAXONOMY_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def themes() -> dict:
    return json.loads(THEMES_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def overrides() -> dict:
    return json.loads(OVERRIDES_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def watchlist() -> dict:
    return json.loads(WATCHLIST_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def concept_ids(taxonomy: dict) -> set[str]:
    return {c["concept_id"] for c in taxonomy["concepts"]}


@pytest.fixture(scope="module")
def theme_ids(themes: dict) -> set[str]:
    return {t["theme_id"] for t in themes["themes"]}


def test_taxonomy_has_required_keys(taxonomy):
    assert "concepts" in taxonomy
    assert isinstance(taxonomy["concepts"], list)
    assert len(taxonomy["concepts"]) > 0


def test_taxonomy_concept_levels_valid(taxonomy):
    for c in taxonomy["concepts"]:
        assert c["level"] in (1, 2, 3), f"Invalid level for {c['concept_id']}: {c['level']}"


def test_taxonomy_parent_links_resolve(concept_ids, taxonomy):
    """每个 parent_id 必须指向已定义的 concept_id."""
    for c in taxonomy["concepts"]:
        if c.get("parent_id") is not None:
            assert c["parent_id"] in concept_ids, (
                f"{c['concept_id']} parent_id={c['parent_id']} not in taxonomy"
            )


def test_taxonomy_level_consistency(taxonomy):
    """level=1 必须 parent_id=None；level=2/3 必须有 parent_id."""
    by_id = {c["concept_id"]: c for c in taxonomy["concepts"]}
    for c in taxonomy["concepts"]:
        if c["level"] == 1:
            assert c.get("parent_id") is None, f"level=1 {c['concept_id']} should have no parent"
        else:
            assert c.get("parent_id") is not None, f"level={c['level']} {c['concept_id']} needs parent"
            parent = by_id[c["parent_id"]]
            assert parent["level"] == c["level"] - 1, (
                f"{c['concept_id']} (level {c['level']}) parent {c['parent_id']} "
                f"is level {parent['level']}, expected {c['level'] - 1}"
            )


def test_taxonomy_includes_other_bucket(concept_ids):
    """`other` 是 fallback 必备桶."""
    assert "other" in concept_ids


def test_themes_parent_concept_resolves(concept_ids, themes):
    for t in themes["themes"]:
        if t.get("parent_concept_id") is not None:
            assert t["parent_concept_id"] in concept_ids


def test_themes_lifecycle_state_valid(themes):
    valid = {"watch", "active", "fading", "retired"}
    for t in themes["themes"]:
        assert t["lifecycle_state"] in valid, (
            f"{t['theme_id']} bad lifecycle_state={t['lifecycle_state']}"
        )


def test_overrides_concept_ids_resolve(concept_ids, overrides):
    for sym, cfg in overrides["symbols"].items():
        assert cfg["primary_concept_id"] in concept_ids, f"{sym} primary not in taxonomy"
        for key in ("secondary_concept_id", "tertiary_concept_id"):
            cid = cfg.get(key)
            if cid is not None:
                assert cid in concept_ids, f"{sym} {key}={cid} not in taxonomy"


def test_overrides_theme_ids_resolve(theme_ids, overrides):
    for sym, cfg in overrides["symbols"].items():
        for tid in cfg.get("theme_ids", []):
            assert tid in theme_ids, f"{sym} theme_ids contains unknown {tid}"


def test_overrides_primary_not_empty(overrides):
    for sym, cfg in overrides["symbols"].items():
        assert cfg["primary_concept_id"], f"{sym} has empty primary_concept_id"


def test_overrides_confidence_in_range(overrides):
    for sym, cfg in overrides["symbols"].items():
        c = cfg.get("confidence", 0)
        assert 0.0 <= c <= 1.0, f"{sym} confidence={c} out of [0,1]"


def test_overrides_display_tags_match_concept_ids(overrides, taxonomy):
    """display_tags 长度应与非空 concept_id 数量匹配，避免错位."""
    by_id = {c["concept_id"]: c["label"] for c in taxonomy["concepts"]}
    for sym, cfg in overrides["symbols"].items():
        tags = cfg.get("display_tags", [])
        if not tags:
            continue
        # 第一个标签应该匹配 primary concept 的 label（或 override 的自定义 label）
        primary_label = by_id[cfg["primary_concept_id"]]
        assert tags[0] == primary_label, (
            f"{sym} display_tags[0]={tags[0]!r} != primary label {primary_label!r}"
        )


def test_overrides_seed_includes_acceptance_symbols(overrides):
    """Acceptance criteria: MU/POET/NVDA/COHR/VRT/TSLA/SOXX 必须有 stable display tags."""
    required = {"MU", "POET", "NVDA", "COHR", "VRT", "TSLA", "SOXX"}
    seeded = set(overrides["symbols"].keys())
    missing = required - seeded
    assert not missing, f"Missing acceptance seed symbols: {missing}"


def test_watchlist_schema(watchlist):
    assert "symbols" in watchlist
    assert isinstance(watchlist["symbols"], list)
    for sym in watchlist["symbols"]:
        assert isinstance(sym, str) and sym == sym.upper()


def test_watchlist_includes_off_broad_names(watchlist):
    """POET 必须在 watchlist（小盘，不进 broad universe）."""
    assert "POET" in watchlist["symbols"]


# ---------- Task 3: ConceptRegistry runtime ----------

@pytest.fixture(scope="module")
def registry():
    from terminal.company_concepts import ConceptRegistry
    return ConceptRegistry(
        taxonomy_path=TAXONOMY_PATH,
        themes_path=THEMES_PATH,
        overrides_path=OVERRIDES_PATH,
        watchlist_path=WATCHLIST_PATH,
    )


def test_registry_manual_override_mu(registry):
    result = registry.classify({"symbol": "MU", "industry": "Semiconductors"})
    assert result["primary_concept_id"] == "semiconductor"
    assert result["secondary_concept_id"] == "memory"
    assert result["tertiary_concept_id"] == "memory_chips"
    assert result["theme_ids"] == ["hbm"]
    assert result["display_tags"] == "半导体 / 存储 / HBM"
    assert result["business_role"] == "DRAM/HBM存储"
    assert result["source"] == "manual"
    assert result["confidence"] == 0.98
    assert result["needs_review"] == 0


def test_registry_manual_override_poet_beats_profile(registry):
    """POET profile 即便说 Semiconductors，manual override 仍走 InP光接口."""
    result = registry.classify({
        "symbol": "POET",
        "industry": "Semiconductors",
        "companyName": "POET Technologies",
    })
    assert result["primary_concept_id"] == "network_equipment"
    assert result["secondary_concept_id"] == "optical_communications"
    assert result["tertiary_concept_id"] == "inp_optical_interface"
    assert result["display_tags"] == "通信/网络设备 / 光通信 / InP光接口"
    assert result["source"] == "manual"


def test_registry_rule_software_saas(registry):
    """未在 override 中的软件公司走 keyword rule."""
    result = registry.classify({
        "symbol": "ZZZZ",
        "industry": "Software—Application",
        "companyName": "Hypothetical Enterprise SaaS Co",
    })
    assert result["primary_concept_id"] == "software_saas"
    assert result["source"] == "rule"
    assert result["needs_review"] == 0
    # display_tags 至少含一层
    assert "软件/SaaS" in result["display_tags"]


def test_registry_thin_profile_falls_back_to_other(registry):
    """没有任何关键词的 profile 走 fallback，标记 needs_review."""
    result = registry.classify({"symbol": "XYZUNKNOWN", "companyName": "Mystery Holdings Co"})
    assert result["primary_concept_id"] == "other"
    assert result["secondary_concept_id"] is None
    assert result["tertiary_concept_id"] is None
    assert result["source"] == "fallback"
    assert result["needs_review"] == 1
    assert result["display_tags"] == "其他"


def test_registry_two_level_display_omits_empty_tertiary(registry):
    """无意义第三层时 display_tags 只拼接两层，不带空段或斜杠尾巴."""
    result = registry.classify({"symbol": "TSM", "industry": "Semiconductors"})
    assert result["primary_concept_id"] == "semiconductor"
    assert result["secondary_concept_id"] == "foundry"
    assert result["tertiary_concept_id"] is None
    assert result["display_tags"] == "半导体 / 晶圆代工"
    assert not result["display_tags"].endswith("/")
    assert " /  " not in result["display_tags"]


def test_registry_manual_display_tags_preserved_verbatim(registry):
    """Override 自带 display_tags 时，registry 必须保留原串而不重新拼接."""
    # NVDA override 写的是 ["AI算力/云", "GPU/AI加速器"]
    result = registry.classify({"symbol": "NVDA"})
    assert result["display_tags"] == "AI算力/云 / GPU/AI加速器"
    assert result["source"] == "manual"


def test_registry_priority_list_matches_plan_definition(registry):
    """priority_list = portfolio ∪ watchlist ∪ broad_top. Override seed is NOT auto-included."""
    priority = registry.priority_list(
        broad_top_symbols=["AAPL", "MSFT"],
        portfolio_holdings=["NVDA"],
    )
    # Watchlist (POET) and broad_top + portfolio members must be in.
    assert "POET" in priority
    assert "AAPL" in priority
    assert "MSFT" in priority
    assert "NVDA" in priority
    # Override-only seed (MU is in overrides but not portfolio/watchlist/broad_top here)
    # must NOT be auto-pulled in by priority — overrides aren't a force-add.
    assert "MU" not in priority


def test_registry_classify_accepts_string_symbol(registry):
    """支持 classify('MU') 简写形式，跟 classify({'symbol': 'MU'}) 等价."""
    by_str = registry.classify("MU")
    by_dict = registry.classify({"symbol": "MU"})
    assert by_str == by_dict
