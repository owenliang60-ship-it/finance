"""Claude CLI prefill for L1/L2/L3 candidates when override + keyword rule miss.

Transport: subprocess to `claude -p <prompt> --output-format json`. Matches
the pattern in forge/runner.py — no anthropic SDK dependency added; the CLI
is already on PATH.

The builder orchestrator (scripts/build_company_concept_registry.py) calls
prefill_one() per symbol when registry.classify(...) returns source="unclassified".
The returned LLMResult carries one of:
    source="llm"           → l1/l2 validated against taxonomy
    source="llm_failed"    → CLI raised / parse failed
    source="llm_fallback"  → CLI returned but l1/l2 not in taxonomy or parent mismatch

The orchestrator writes the failure-mode rows into the review CSV with l1/l2
blank so Boss must fill them manually before Phase 5 save (fail-fast).
"""
from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


PROMPT_TEMPLATE = """\
You are a stock-tagger for {symbol}. Map this company to a 3-level taxonomy.

Company profile:
- name: {name}
- sector: {sector}
- industry: {industry}
- description: {description}

L1 candidates (pick exactly one concept_id):
{l1_list}

L2 candidates (must be parent={{l1_chosen}}):
{l2_list}

L3 themes (pick 0-4, only if they meaningfully impact valuation or momentum):
{l3_list}

Output JSON ONLY (no commentary, no markdown fence):
{{"l1":"...","l2":"...","l3_themes":[...],"business_role":"<one Chinese sentence>","confidence":0.0-1.0}}
"""


@dataclass
class LLMResult:
    l1: Optional[str]
    l2: Optional[str]
    l3_themes: list[str] = field(default_factory=list)
    business_role: str = ""
    confidence: float = 0.0
    source: str = "llm_failed"  # llm | llm_failed | llm_fallback
    evidence: str = ""
    needs_review: int = 1


def _run_claude_cli(prompt: str, timeout: int = 60) -> str:
    """Invoke `claude -p` synchronously and return stdout. Raises on non-zero exit."""
    completed = subprocess.run(
        ["claude", "-p", prompt, "--output-format", "json"],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        # `claude -p --output-format json` writes its real error (throttle /
        # cost limit / auth) to STDOUT, not stderr — issue 026 #1. Record both
        # so the failure log is actionable instead of an empty stderr blank.
        raise RuntimeError(
            f"claude CLI failed (rc={completed.returncode}): "
            f"stderr={completed.stderr[:300]} | stdout={completed.stdout[:300]}"
        )
    return completed.stdout


def _failed(symbol: str, evidence: str) -> LLMResult:
    logger.warning("LLM prefill failed for %s: %s", symbol, evidence)
    return LLMResult(
        l1=None, l2=None, l3_themes=[], business_role="",
        confidence=0.0, source="llm_failed",
        evidence=evidence, needs_review=1,
    )


def _fallback(symbol: str, business_role: str, evidence: str) -> LLMResult:
    return LLMResult(
        l1=None, l2=None, l3_themes=[], business_role=business_role,
        confidence=0.1, source="llm_fallback",
        evidence=evidence, needs_review=1,
    )


def prefill_one(symbol: str, profile: dict, taxonomy: dict) -> LLMResult:
    """Run a single claude-CLI prefill for one symbol; validate result.

    profile keys consumed (any missing = empty): companyName, sector,
    industry, description.

    taxonomy schema: same as concept_taxonomy_v2.json — uses concepts[].level
    + parent_id to derive valid L1/L2/L3 ids.
    """
    concepts = taxonomy.get("concepts", [])
    l1_concepts = [c for c in concepts if c.get("level") == 1]
    l2_concepts = [c for c in concepts if c.get("level") == 2]
    l3_concepts = [c for c in concepts if c.get("level") == 3]

    valid_l1 = {c["concept_id"] for c in l1_concepts}
    valid_l2_parent = {c["concept_id"]: c.get("parent_id") for c in l2_concepts}
    valid_l3 = {c["concept_id"] for c in l3_concepts}

    prompt = PROMPT_TEMPLATE.format(
        symbol=symbol,
        name=profile.get("companyName", "") or profile.get("company_name", ""),
        sector=profile.get("sector", ""),
        industry=profile.get("industry", ""),
        description=(profile.get("description", "") or "")[:500],
        l1_list="\n".join(
            f"- {c['concept_id']} ({c.get('label', '')})" for c in l1_concepts
        ),
        l2_list="\n".join(
            f"- {c['concept_id']} (parent={c.get('parent_id')}, {c.get('label', '')})"
            for c in l2_concepts
        ),
        l3_list="\n".join(
            f"- {c['concept_id']} ({c.get('label', '')})" for c in l3_concepts
        ),
    )

    try:
        stdout = _run_claude_cli(prompt)
    except Exception as exc:  # noqa: BLE001 — broad catch is intentional
        return _failed(symbol, f"cli: {exc}")

    # claude -p --output-format json wraps the model output under a `result`
    # field (matches forge/runner.py). Tolerate raw JSON too.
    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError as exc:
        return _failed(symbol, f"envelope JSON: {exc}")

    if isinstance(envelope, dict) and "result" in envelope:
        payload_text = envelope["result"]
        if isinstance(payload_text, str):
            try:
                candidate = json.loads(payload_text)
            except json.JSONDecodeError as exc:
                return _failed(symbol, f"payload JSON: {exc}")
        else:
            candidate = payload_text
    else:
        candidate = envelope

    if not isinstance(candidate, dict):
        return _failed(symbol, f"payload not dict: {type(candidate).__name__}")

    l1 = candidate.get("l1")
    l2 = candidate.get("l2")
    business_role = str(candidate.get("business_role", ""))

    if (
        l1 not in valid_l1
        or l2 not in valid_l2_parent
        or valid_l2_parent[l2] != l1
    ):
        return _fallback(
            symbol,
            business_role,
            "l1/l2 not in taxonomy or parent mismatch",
        )

    raw_l3 = candidate.get("l3_themes", []) or []
    if not isinstance(raw_l3, list):
        raw_l3 = []
    l3_themes = [t for t in raw_l3 if t in valid_l3]

    try:
        confidence = float(candidate.get("confidence", 0.5))
    except (TypeError, ValueError):
        confidence = 0.5

    return LLMResult(
        l1=l1,
        l2=l2,
        l3_themes=l3_themes,
        business_role=business_role,
        confidence=confidence,
        source="llm",
        evidence="claude prefill",
        needs_review=0,
    )
