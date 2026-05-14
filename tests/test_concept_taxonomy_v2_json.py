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


def test_taxonomy_has_11_l1_60_l2_42_l3():
    data = _load()
    levels = {1: [], 2: [], 3: []}
    for c in data["concepts"]:
        levels[c["level"]].append(c)
    assert len(levels[1]) == 11
    assert len(levels[2]) == 60
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
