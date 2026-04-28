"""Concept registry: resolve symbol → canonical concept ids + display tags.

Order of resolution per Phase 1 plan:
    manual override → keyword rule → legacy bucket → fallback("其他", needs_review=1)

This module loads taxonomy + themes + overrides + watchlist from JSON. The
runtime classifier (terminal.concept_classifier) prefers DB-materialized rows
when available and falls through to this in-memory registry / legacy JSON
otherwise.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# Legacy report_concepts.json bucket → canonical concept_id (fallback path 3).
_LEGACY_BUCKET_TO_CONCEPT: dict[str, str] = {
    "AI算力/云": "ai_compute_cloud",
    "半导体链": "semiconductor",
    "数据中心电力": "data_center_power",
    "通信/网络设备": "network_equipment",
    "互联网/广告": "internet_ads",
    "软件/SaaS": "software_saas",
    "自动驾驶/机器人": "evs_robotics",
    "金融/加密": "finance_crypto",
    "医药/生命科学": "pharma_life_sci",
    "工业/航天/国防": "industrial_aerospace",
    "消费/电商": "consumer_ecommerce",
    "能源/材料": "energy_materials",
    "地产/基础设施": "realestate_infra",
    "ETF/宏观工具": "etf_macro",
    "其他": "other",
}


# Keyword rules: (keywords[lowercase], primary, secondary, tertiary, business_role).
# Order matters — most specific first. Profile text is lowercased before matching.
_KEYWORD_RULES: list[tuple[list[str], str, str | None, str | None, str]] = [
    (["semiconductor equipment", "lithography", "wafer fab equipment"],
     "semiconductor", "semiconductor_equipment", None, "半导体设备"),
    (["foundry", "wafer foundry"],
     "semiconductor", "foundry", None, "晶圆代工"),
    (["dram", "nand", "memory chip"],
     "semiconductor", "memory", "memory_chips", "存储芯片"),
    (["analog chip", "analog semiconductor", "power management ic"],
     "semiconductor", "analog_chips", None, "模拟芯片"),
    (["semiconductor", "chip designer", "ic design", "fabless"],
     "semiconductor", None, None, "半导体"),

    (["gpu", "ai accelerator", "graphics processor"],
     "ai_compute_cloud", "gpu_accelerator", None, "GPU/AI加速器"),
    (["ai server", "compute platform"],
     "ai_compute_cloud", "ai_server", None, "AI服务器"),
    (["cloud computing", "cloud infrastructure", "hyperscaler", "iaas"],
     "ai_compute_cloud", "cloud_infra", None, "云基础设施"),

    (["fiber optic", "optical network", "optical communication", "photonic"],
     "network_equipment", "optical_communications", None, "光通信"),
    (["network equipment", "telecom equipment", "communications equipment"],
     "network_equipment", None, None, "通信/网络设备"),

    (["data center power", "rack power", "ups system", "thermal management",
      "liquid cooling"],
     "data_center_power", "power_thermal", None, "数据中心电源/散热"),

    (["enterprise software", "saas", "software—application",
      "software-application", "application software"],
     "software_saas", "enterprise_software", None, "企业软件/SaaS"),
    (["software", "platform-as-a-service"],
     "software_saas", None, None, "软件"),

    (["internet content", "social media", "advertising", "search engine",
      "digital advertising"],
     "internet_ads", None, None, "互联网/广告"),

    (["electric vehicle", "ev maker", "auto manufactur"],
     "evs_robotics", "electric_vehicles", None, "电动车"),
    (["robotic", "humanoid"],
     "evs_robotics", "robotics", None, "机器人"),

    (["bank", "insurance", "broker", "asset management", "fintech",
      "crypto", "exchange operator"],
     "finance_crypto", None, None, "金融/加密"),

    (["pharmaceutical", "biotech", "drug manufactur", "medical device",
      "life sciences"],
     "pharma_life_sci", None, None, "医药/生命科学"),

    (["aerospace", "defense", "industrial machinery", "diversified industrial"],
     "industrial_aerospace", None, None, "工业/航天/国防"),

    (["e-commerce", "ecommerce", "retail apparel", "department store",
      "consumer electronics retail"],
     "consumer_ecommerce", None, None, "消费/电商"),

    (["nuclear", "uranium", "smr "],
     "energy_materials", "nuclear_power", None, "核电"),
    (["oil & gas", "renewable energy", "solar", "specialty chemicals"],
     "energy_materials", None, None, "能源/材料"),

    (["reit", "real estate", "property management"],
     "realestate_infra", None, None, "地产/基础设施"),

    (["exchange traded fund", "etf", "index fund"],
     "etf_macro", "index_etf", None, "指数ETF"),
]


class ConceptRegistry:
    """Resolve symbols to concept tags via override → rule → legacy → fallback."""

    def __init__(
        self,
        taxonomy_path: Path,
        themes_path: Path,
        overrides_path: Path,
        watchlist_path: Path | None = None,
        legacy_classifier: Any = None,
    ) -> None:
        self._taxonomy = json.loads(Path(taxonomy_path).read_text(encoding="utf-8"))
        self._themes = json.loads(Path(themes_path).read_text(encoding="utf-8"))
        self._overrides = json.loads(Path(overrides_path).read_text(encoding="utf-8"))
        self._watchlist_data: dict[str, Any] = (
            json.loads(Path(watchlist_path).read_text(encoding="utf-8"))
            if watchlist_path
            else {"symbols": []}
        )
        self._legacy_classifier = legacy_classifier

        self._concepts_by_id: dict[str, dict] = {
            c["concept_id"]: c for c in self._taxonomy.get("concepts", [])
        }
        self._theme_by_id: dict[str, dict] = {
            t["theme_id"]: t for t in self._themes.get("themes", [])
        }
        self._symbol_overrides: dict[str, dict] = self._overrides.get("symbols", {})
        self._watchlist_symbols: set[str] = {
            s.upper() for s in self._watchlist_data.get("symbols", [])
        }

    # ---- public API ----

    @property
    def concepts(self) -> list[dict]:
        return list(self._taxonomy.get("concepts", []))

    @property
    def themes(self) -> list[dict]:
        return list(self._themes.get("themes", []))

    @property
    def watchlist_symbols(self) -> set[str]:
        return set(self._watchlist_symbols)

    @property
    def override_symbols(self) -> set[str]:
        return set(self._symbol_overrides.keys())

    def classify(self, item: dict | str) -> dict[str, Any]:
        """Resolve a profile/symbol to a concept tag dict. Always returns full result."""
        symbol = self._symbol(item)

        # 1. Manual override
        if symbol in self._symbol_overrides:
            return self._from_override(symbol)

        # 2. Keyword rules over profile text
        text = self._text(item)
        if text.strip():
            for keywords, primary, secondary, tertiary, role in _KEYWORD_RULES:
                hit = next((kw for kw in keywords if kw in text), None)
                if hit is not None:
                    return self._build_result(
                        symbol=symbol,
                        primary_concept_id=primary,
                        secondary_concept_id=secondary,
                        tertiary_concept_id=tertiary,
                        theme_ids=[],
                        business_role=role,
                        confidence=0.6,
                        source="rule",
                        evidence=f"keyword: {hit}",
                        needs_review=0,
                        manual_display_tags=None,
                    )

        # 3. Legacy classifier fallback
        if self._legacy_classifier is not None:
            try:
                bucket = self._legacy_classifier.classify(item)
            except Exception:  # noqa: BLE001 - legacy classifier is best-effort
                bucket = None
            default = getattr(self._legacy_classifier, "default_bucket", "其他")
            if bucket and bucket != default:
                primary = _LEGACY_BUCKET_TO_CONCEPT.get(bucket, "other")
                role = ""
                try:
                    role = self._legacy_classifier.business_role(item)
                except Exception:  # noqa: BLE001
                    role = ""
                return self._build_result(
                    symbol=symbol,
                    primary_concept_id=primary,
                    secondary_concept_id=None,
                    tertiary_concept_id=None,
                    theme_ids=[],
                    business_role=role or "待补业务标签",
                    confidence=0.4,
                    source="legacy",
                    evidence=f"legacy bucket: {bucket}",
                    needs_review=0,
                    manual_display_tags=None,
                )

        # 4. Default fallback
        return self._build_result(
            symbol=symbol,
            primary_concept_id="other",
            secondary_concept_id=None,
            tertiary_concept_id=None,
            theme_ids=[],
            business_role="待补业务标签",
            confidence=0.1,
            source="fallback",
            evidence="no profile keywords matched",
            needs_review=1,
            manual_display_tags=None,
        )

    def priority_list(
        self,
        broad_top_symbols: list[str] | set[str],
        portfolio_holdings: list[str] | set[str] | None = None,
    ) -> set[str]:
        """priority_list = portfolio holdings ∪ watchlist ∪ broad_top_100_by_30d_ADV.

        This matches the gate definition in the plan. Override seed is NOT in
        priority — overrides serve to give high-confidence tags to whichever
        symbols are already in scope; they don't force-add symbols to priority.
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
            "companyName",
            "shortName",
            "longName",
            "company_name",
            "description",
            "sector",
            "industry",
        )
        return " ".join(str(item.get(k) or "") for k in keys).lower()

    def _from_override(self, symbol: str) -> dict[str, Any]:
        cfg = self._symbol_overrides[symbol]
        manual_tags = cfg.get("display_tags")
        if isinstance(manual_tags, list):
            manual_display = " / ".join(t for t in manual_tags if t)
        elif isinstance(manual_tags, str):
            manual_display = manual_tags
        else:
            manual_display = None
        return self._build_result(
            symbol=symbol,
            primary_concept_id=cfg["primary_concept_id"],
            secondary_concept_id=cfg.get("secondary_concept_id"),
            tertiary_concept_id=cfg.get("tertiary_concept_id"),
            theme_ids=list(cfg.get("theme_ids", [])),
            business_role=cfg.get("business_role", ""),
            confidence=float(cfg.get("confidence", 1.0)),
            source="manual",
            evidence=cfg.get("evidence", ""),
            needs_review=0,
            manual_display_tags=manual_display,
        )

    def _build_result(
        self,
        *,
        symbol: str,
        primary_concept_id: str,
        secondary_concept_id: str | None,
        tertiary_concept_id: str | None,
        theme_ids: list[str],
        business_role: str,
        confidence: float,
        source: str,
        evidence: str,
        needs_review: int,
        manual_display_tags: str | None,
    ) -> dict[str, Any]:
        if manual_display_tags:
            display_tags = manual_display_tags
        else:
            display_tags = self._auto_display_tags(
                primary_concept_id,
                secondary_concept_id,
                tertiary_concept_id,
                theme_ids,
            )
        return {
            "symbol": symbol,
            "primary_concept_id": primary_concept_id,
            "secondary_concept_id": secondary_concept_id,
            "tertiary_concept_id": tertiary_concept_id,
            "theme_ids": list(theme_ids),
            "display_tags": display_tags,
            "business_role": business_role,
            "confidence": confidence,
            "source": source,
            "evidence": evidence,
            "needs_review": needs_review,
        }

    def _auto_display_tags(
        self,
        primary: str,
        secondary: str | None,
        tertiary: str | None,
        theme_ids: list[str],
    ) -> str:
        """Compose display string from labels; omit None levels and append theme labels."""
        labels: list[str] = []
        for cid in (primary, secondary, tertiary):
            if cid is None:
                continue
            concept = self._concepts_by_id.get(cid)
            if concept:
                labels.append(concept["label"])
        for tid in theme_ids:
            theme = self._theme_by_id.get(tid)
            if theme:
                labels.append(theme["label"])
        return " / ".join(labels)
