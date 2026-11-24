"""Skill SOP template docs/skills/valuation/SKILL.md (Task 8). Frontmatter parsed by regex, no PyYAML."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DOC = ROOT / "docs" / "skills" / "valuation" / "SKILL.md"


def _parts():
    text = DOC.read_text(encoding="utf-8")
    m = re.match(r"---\n(.*?)\n---\n(.*)", text, re.S)
    assert m, "frontmatter block missing"
    return text, m.group(1), m.group(2)


def test_frontmatter():
    text, front, _ = _parts()
    assert text.startswith("---\n")
    assert re.search(r"^name:\s*valuation\s*$", front, re.M)
    desc = re.search(r'^description:\s*"(.*)"\s*$', front, re.M).group(1)
    assert len(desc) <= 1024 and "Use when" in desc
    assert re.search(r"^invocation:\s*user\s*$", front, re.M)
    assert re.search(r"^\s+- name:\s*symbol\s*$", front, re.M) and re.search(r"^\s+required:\s*true\s*$", front, re.M)


def test_body_mentions_required_terms():
    _, _, body = _parts()
    for term in ("scripts/valuation.py", "sync_to_cloud.sh --pull", "mode=ro", "fx_daily", "[[acks]]", "g4_reextract"):
        assert term in body, term


def test_exit_code_lines():
    _, _, body = _parts()
    codes = set(re.findall(r"^\s*-?\s*\**(0|10|20|1)\b", body, re.M))
    assert codes == {"0", "10", "20", "1"}


def test_seven_fixed_steps():
    _, _, body = _parts()
    steps = re.findall(r"^(\d)\. ", body, re.M)
    assert steps[:7] == [str(i) for i in range(1, 8)]


def test_repo_paths_exist():
    _, _, body = _parts()
    paths = set(re.findall(r"`((?:scripts|terminal|tests|docs)/[\w./-]+)`", body))
    assert {"scripts/valuation.py", "terminal/valuation/ledger.py"} <= paths
    for p in paths:
        assert (ROOT / p).exists(), p


def test_no_placeholders():
    text, _, _ = _parts()
    assert "TBD" not in text and "TODO" not in text
