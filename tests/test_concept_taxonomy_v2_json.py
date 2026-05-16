"""SSOT 完整性测试 — 不依赖 DB/registry，只验 JSON 结构。"""
import json
from pathlib import Path

TAXONOMY_PATH = (
    Path(__file__).resolve().parent.parent
    / "config"
    / "concepts"
    / "concept_taxonomy_v2.json"
)


def _load():
    return json.loads(TAXONOMY_PATH.read_text(encoding="utf-8"))


def test_taxonomy_has_11_l1_61_l2_42_l3():
    data = _load()
    levels = {1: [], 2: [], 3: []}
    for c in data["concepts"]:
        levels[c["level"]].append(c)
    assert len(levels[1]) == 11
    # 61 L2: telecom_operator added by the 2026-05-16 rebuild (plan §3.3).
    assert len(levels[2]) == 61
    assert len(levels[3]) == 42


def test_l2_parent_strictly_under_l1():
    data = _load()
    l1_ids = {c["concept_id"] for c in data["concepts"] if c["level"] == 1}
    for c in data["concepts"]:
        if c["level"] == 2:
            assert c.get("parent_id") in l1_ids, f"{c['concept_id']} parent missing"


def test_l3_independent_axis_no_parent():
    data = _load()
    for c in data["concepts"]:
        if c["level"] == 3:
            assert c.get("parent_id") in (None, ""), f"{c['concept_id']} should have no parent"
            assert c.get("concept_type") == "theme"


def test_concept_ids_globally_unique():
    data = _load()
    ids = [c["concept_id"] for c in data["concepts"]]
    assert len(ids) == len(set(ids)), f"duplicate concept_id: {ids}"


def test_l3_aliases_map_back_to_id():
    data = _load()
    alias_to_id: dict[str, str] = {}
    for c in data["concepts"]:
        if c["level"] == 3:
            for alias in c.get("aliases", []):
                assert (
                    alias not in alias_to_id or alias_to_id[alias] == c["concept_id"]
                ), f"alias '{alias}' collides between {alias_to_id.get(alias)} and {c['concept_id']}"
                alias_to_id[alias] = c["concept_id"]
            assert c["label"] in alias_to_id, f"L3 {c['concept_id']} label not in aliases"


def test_anchor_l3_ids_all_in_pool():
    data = _load()
    l3_ids = {c["concept_id"] for c in data["concepts"] if c["level"] == 3}
    for anchor in data.get("multi_segment_anchors", []):
        for tid in anchor.get("theme_ids", []):
            assert tid in l3_ids, f"anchor {anchor['symbol']} references unknown L3 {tid}"


def test_industry_map_present_and_well_formed():
    """v1 keyword_rules were replaced by industry_map (issue 025 / plan
    2026-05-16). Keys are FMP "sector|industry" strings; values are
    {l1, l2} pointing at valid L1/L2 concept_ids."""
    data = _load()
    assert "keyword_rules" not in data, "v1 keyword_rules must be gone"
    industry_map = data["industry_map"]
    assert len(industry_map) == 82
    l1_ids = {c["concept_id"] for c in data["concepts"] if c["level"] == 1}
    l2_ids = {c["concept_id"] for c in data["concepts"] if c["level"] == 2}
    for key, val in industry_map.items():
        assert key.count("|") == 1, f"key '{key}' is not sector|industry"
        sector, industry = key.split("|")
        assert sector.strip() and industry.strip(), f"empty half in key '{key}'"
        assert val["l1"] in l1_ids, f"{key} → unknown l1 {val['l1']}"
        assert val["l2"] in l2_ids, f"{key} → unknown l2 {val['l2']}"


def test_industry_map_l2_parent_consistent_with_l1():
    """Each industry_map (l1, l2) pair must respect the taxonomy tree:
    the mapped L2's parent_id is the mapped L1."""
    data = _load()
    l2_parent = {
        c["concept_id"]: c.get("parent_id")
        for c in data["concepts"] if c["level"] == 2
    }
    for key, val in data["industry_map"].items():
        assert l2_parent[val["l2"]] == val["l1"], (
            f"{key}: l2 {val['l2']} parent {l2_parent[val['l2']]} != l1 {val['l1']}"
        )
