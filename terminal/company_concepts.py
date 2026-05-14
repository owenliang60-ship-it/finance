"""Concept registry v2: resolve symbol → (l1, l2, l3_themes, business_role).

Resolution order (registry-internal only — LLM prefill lives in the builder
orchestration layer, see Task 4 / 4b):
    1. multi_segment_anchor → source="manual"
    2. keyword rule         → source="rule"
    3. otherwise            → source="unclassified" (l1=l2=None, needs_review=1)

Legacy bucket fallback is removed in v2; "unclassified" rows hand off to the
LLM prefill orchestrator in the builder.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


class ConceptRegistry:
    """v2 registry — loads single SSOT JSON (concept_taxonomy_v2.json)."""

    def __init__(
        self,
        taxonomy_path: Path,
        watchlist_path: Path | None = None,
        # Accepted for backward compatibility with v1 call sites; ignored in v2.
        # v2 reads overrides from taxonomy_path's `multi_segment_anchors`; the
        # legacy company_concept_overrides.json is not consumed.
        overrides_path: Path | None = None,
        themes_path: Path | None = None,
        legacy_classifier: Any = None,
    ) -> None:
        data = json.loads(Path(taxonomy_path).read_text(encoding="utf-8"))
        if "keyword_rules" not in data or "multi_segment_anchors" not in data:
            raise ValueError(
                f"taxonomy_path={taxonomy_path} is not a v2 SSOT JSON "
                "(missing keyword_rules / multi_segment_anchors)"
            )
        self._taxonomy = data
        self._watchlist_data: dict[str, Any] = (
            json.loads(Path(watchlist_path).read_text(encoding="utf-8"))
            if watchlist_path
            else {"symbols": []}
        )

        concepts = data.get("concepts", [])
        self._concepts_by_id: dict[str, dict] = {
            c["concept_id"]: c for c in concepts
        }
        self._l3_ids: set[str] = {
            c["concept_id"] for c in concepts if c.get("level") == 3
        }
        self._alias_to_l3: dict[str, str] = {}
        for c in concepts:
            if c.get("level") != 3:
                continue
            cid = c["concept_id"]
            self._alias_to_l3[c["label"]] = cid
            for alias in c.get("aliases", []):
                self._alias_to_l3[alias] = cid

        self._anchors_by_symbol: dict[str, dict] = {
            a["symbol"].upper(): a
            for a in data.get("multi_segment_anchors", [])
        }
        self._keyword_rules: list[dict] = list(data.get("keyword_rules", []))

        self._watchlist_symbols: set[str] = {
            s.upper() for s in self._watchlist_data.get("symbols", [])
        }

    # ---- public API ----

    @property
    def concepts(self) -> list[dict]:
        return list(self._taxonomy.get("concepts", []))

    @property
    def keyword_rules(self) -> list[dict]:
        return list(self._keyword_rules)

    @property
    def anchors(self) -> list[dict]:
        return list(self._taxonomy.get("multi_segment_anchors", []))

    @property
    def watchlist_symbols(self) -> set[str]:
        return set(self._watchlist_symbols)

    @property
    def override_symbols(self) -> set[str]:
        return set(self._anchors_by_symbol.keys())

    def resolve_l3_alias(self, label_or_id: str) -> Optional[str]:
        """Resolve a Chinese label / English alias / concept_id back to a
        canonical L3 concept_id. Returns None when no L3 matches.

        Important: only resolves to L3 ids; L1/L2 ids passed in return None
        (a CSV writer hand-coding "semiconductor" as a theme is a bug).
        """
        if label_or_id in self._l3_ids:
            return label_or_id
        return self._alias_to_l3.get(label_or_id)

    def classify(self, item: dict | str) -> dict[str, Any]:
        """Classify a profile into (l1, l2, l3_themes, business_role) tuple.

        Returns dict with keys:
            symbol, l1, l2, l3_themes, display_tags, business_role,
            confidence, source, evidence, needs_review
        """
        symbol = self._symbol(item)

        if symbol in self._anchors_by_symbol:
            return self._from_anchor(symbol)

        text = self._text(item)
        if text.strip():
            for rule in self._keyword_rules:
                hit = next((kw for kw in rule["keywords"] if kw in text), None)
                if hit is not None:
                    return self._build_result(
                        symbol=symbol,
                        l1=rule["l1"],
                        l2=rule["l2"],
                        l3_themes=[],
                        business_role=rule.get("business_role", ""),
                        confidence=float(rule.get("confidence", 0.6)),
                        source="rule",
                        evidence=f"keyword: {hit}",
                        needs_review=0,
                    )

        return self._build_result(
            symbol=symbol,
            l1=None,
            l2=None,
            l3_themes=[],
            business_role="",
            confidence=0.0,
            source="unclassified",
            evidence="override + rule both missed",
            needs_review=1,
        )

    def priority_list(
        self,
        broad_top_symbols: list[str] | set[str],
        portfolio_holdings: list[str] | set[str] | None = None,
    ) -> set[str]:
        """priority_list = portfolio ∪ watchlist ∪ broad_top.

        Anchor seed is NOT in priority — anchors give high-confidence tags
        to symbols already in scope; they don't force-add to priority.
        """
        result: set[str] = {s.upper() for s in broad_top_symbols}
        if portfolio_holdings:
            result.update(s.upper() for s in portfolio_holdings)
        result.update(self._watchlist_symbols)
        return result

    # ---- internal ----

    @staticmethod
    def _symbol(item: dict | str) -> str:
        if isinstance(item, str):
            return item.upper()
        return str(item.get("symbol") or "").upper()

    @staticmethod
    def _text(item: dict | str) -> str:
        if isinstance(item, str):
            return ""
        keys = (
            "companyName", "shortName", "longName", "company_name",
            "description", "sector", "industry",
        )
        return " ".join(str(item.get(k) or "") for k in keys).lower()

    def _from_anchor(self, symbol: str) -> dict[str, Any]:
        anchor = self._anchors_by_symbol[symbol]
        return self._build_result(
            symbol=symbol,
            l1=anchor["l1"],
            l2=anchor["l2"],
            l3_themes=list(anchor.get("theme_ids", [])),
            business_role=anchor.get("business_role", ""),
            confidence=0.99,
            source="manual",
            evidence="multi_segment_anchor",
            needs_review=0,
        )

    def _build_result(
        self,
        *,
        symbol: str,
        l1: Optional[str],
        l2: Optional[str],
        l3_themes: list[str],
        business_role: str,
        confidence: float,
        source: str,
        evidence: str,
        needs_review: int,
    ) -> dict[str, Any]:
        display_tags = self._auto_display_tags(l1, l2, l3_themes)
        return {
            "symbol": symbol,
            "l1": l1,
            "l2": l2,
            "l3_themes": list(l3_themes),
            "display_tags": display_tags,
            "business_role": business_role,
            "confidence": confidence,
            "source": source,
            "evidence": evidence,
            "needs_review": needs_review,
        }

    def _auto_display_tags(
        self,
        l1: Optional[str],
        l2: Optional[str],
        l3_themes: list[str],
    ) -> str:
        parts: list[str] = []
        for cid in (l1, l2):
            if cid is None:
                continue
            c = self._concepts_by_id.get(cid)
            if c:
                parts.append(c["label"])
        if l3_themes:
            first = l3_themes[0]
            c = self._concepts_by_id.get(first)
            if c:
                parts.append(c["label"])
        return " / ".join(parts)
