"""LLM prefill 单测 — mock claude CLI，不实际调用。"""
import json
from unittest.mock import patch

import pytest

from terminal.llm_concept_prefill import LLMResult, prefill_one


_TAXONOMY = {
    "concepts": [
        {"concept_id": "semiconductor", "label": "半导体", "level": 1, "parent_id": None},
        {"concept_id": "gpu_accelerator", "label": "GPU加速器", "level": 2,
         "parent_id": "semiconductor"},
        {"concept_id": "ai_compute", "label": "AI算力", "level": 3,
         "parent_id": None, "concept_type": "theme"},
    ]
}


def _cli_envelope(payload: dict) -> str:
    """Mock claude -p --output-format json envelope shape used by forge/runner."""
    return json.dumps({"result": json.dumps(payload)})


def test_prefill_parses_json_response():
    fake_payload = {
        "l1": "semiconductor",
        "l2": "gpu_accelerator",
        "l3_themes": ["ai_compute"],
        "business_role": "GPU",
        "confidence": 0.85,
    }
    with patch(
        "terminal.llm_concept_prefill._run_claude_cli",
        return_value=_cli_envelope(fake_payload),
    ):
        result = prefill_one(
            symbol="NVDA",
            profile={"description": "GPU", "industry": "Semi"},
            taxonomy=_TAXONOMY,
        )
    assert result.source == "llm"
    assert result.l1 == "semiconductor"
    assert result.l2 == "gpu_accelerator"
    assert result.l3_themes == ["ai_compute"]
    assert result.confidence == 0.85
    assert result.needs_review == 0


def test_prefill_cli_failure_returns_failed_marker():
    with patch(
        "terminal.llm_concept_prefill._run_claude_cli",
        side_effect=RuntimeError("timeout"),
    ):
        result = prefill_one(
            symbol="FOO", profile={}, taxonomy={"concepts": []},
        )
    assert result.source == "llm_failed"
    assert result.l1 is None
    assert result.l2 is None
    assert result.confidence == 0.0
    assert result.needs_review == 1


def test_prefill_unparseable_label_returns_fallback():
    fake_payload = {
        "l1": "not_in_taxonomy",
        "l2": "also_invalid",
        "l3_themes": [],
        "business_role": "foo",
        "confidence": 0.5,
    }
    with patch(
        "terminal.llm_concept_prefill._run_claude_cli",
        return_value=_cli_envelope(fake_payload),
    ):
        result = prefill_one(
            symbol="BAR",
            profile={},
            taxonomy={"concepts": [{"concept_id": "semiconductor", "level": 1,
                                    "parent_id": None}]},
        )
    assert result.source == "llm_fallback"
    assert result.confidence == 0.1
    assert result.needs_review == 1


def test_prefill_parent_mismatch_returns_fallback():
    """LLM 返回的 l2 parent 与所选 l1 不匹配 → fallback。"""
    fake_payload = {
        "l1": "semiconductor",
        "l2": "hyperscaler",  # hyperscaler 的 parent 是 ai_compute_cloud，不是 semiconductor
        "l3_themes": [],
        "business_role": "wrong parent",
        "confidence": 0.8,
    }
    taxonomy = {
        "concepts": [
            {"concept_id": "semiconductor", "level": 1, "parent_id": None,
             "label": "半导体"},
            {"concept_id": "ai_compute_cloud", "level": 1, "parent_id": None,
             "label": "AI算力与云"},
            {"concept_id": "hyperscaler", "level": 2, "parent_id": "ai_compute_cloud",
             "label": "超大规模云"},
        ]
    }
    with patch(
        "terminal.llm_concept_prefill._run_claude_cli",
        return_value=_cli_envelope(fake_payload),
    ):
        result = prefill_one(symbol="BAZ", profile={}, taxonomy=taxonomy)
    assert result.source == "llm_fallback"
    assert result.needs_review == 1


def test_prefill_drops_invalid_l3_themes_but_keeps_valid_ones():
    """LLM 返回的 l3_themes 含 1 个非法 + 1 个合法 → 只保留合法。"""
    fake_payload = {
        "l1": "semiconductor",
        "l2": "gpu_accelerator",
        "l3_themes": ["ai_compute", "fake_theme"],
        "business_role": "GPU",
        "confidence": 0.7,
    }
    with patch(
        "terminal.llm_concept_prefill._run_claude_cli",
        return_value=_cli_envelope(fake_payload),
    ):
        result = prefill_one(symbol="NVDA", profile={}, taxonomy=_TAXONOMY)
    assert result.source == "llm"
    assert result.l3_themes == ["ai_compute"]


def test_prefill_returns_dataclass_instance():
    """API 约定：返回 LLMResult dataclass，便于 builder 编排层 destructure。"""
    with patch(
        "terminal.llm_concept_prefill._run_claude_cli",
        side_effect=RuntimeError("net error"),
    ):
        result = prefill_one(symbol="X", profile={}, taxonomy=_TAXONOMY)
    assert isinstance(result, LLMResult)


def test_run_claude_cli_failure_surfaces_stdout(monkeypatch):
    """issue 026 #1: `claude -p --output-format json` writes its real error
    (throttle / cost limit / auth) to STDOUT, not stderr. A non-zero exit must
    surface stdout in the RuntimeError so the failure log is actionable."""
    import subprocess
    import terminal.llm_concept_prefill as mod

    fake = subprocess.CompletedProcess(
        args=["claude"], returncode=1,
        stdout='{"error":"Credit balance is too low"}',
        stderr="",
    )
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **kw: fake)
    with pytest.raises(RuntimeError) as exc:
        mod._run_claude_cli("prompt")
    msg = str(exc.value)
    assert "Credit balance is too low" in msg   # stdout content reached the log
    assert "rc=1" in msg
