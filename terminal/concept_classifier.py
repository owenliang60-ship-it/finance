"""Concept buckets and business-role labels for report display.

This module is intentionally separate from ``terminal/themes.py``. It is not a
research thesis engine; it is a lightweight presentation classifier shared by
the morning report and Portfolio Intelligence.

Phase 1 layered resolution: when a ``MarketStore`` is provided, the classifier
prefers DB-materialized concept tags from ``market.db.company_concept_tags``;
symbols absent from the registry fall through to the legacy JSON buckets. DB
errors are swallowed and treated as registry misses so morning report cron
never crashes on a broken concept DB.
"""
from __future__ import annotations

import json
import logging
from collections import OrderedDict
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


# v2 11 L1 → legacy bucket label, for grouping continuity.
# Morning report section headers and bucket_order keep their legacy names so
# downstream consumers (Boss's eyes, dashboards) don't see new strings on the
# same business slice. crypto is folded into finance section; ETF stays
# managed by the legacy path (ETF symbols don't carry v2 L1 tags).
_CONCEPT_TO_LEGACY_BUCKET: dict[str, str] = {
    "ai_compute_cloud": "AI算力/云",
    "semiconductor": "半导体链",
    "internet_software": "互联网/广告",         # software/SaaS also lands here
    "autonomy_robotics": "自动驾驶/机器人",
    "pharma_life_sci": "医药/生命科学",
    "finance": "金融/加密",
    "crypto": "金融/加密",                       # crypto folds into finance section
    "consumer_retail": "消费/电商",
    "energy_materials": "能源/材料",
    "industrial_aerospace": "工业/航天/国防",
    "realestate_utility": "地产/基础设施",
    # ETF / unmapped fall through to legacy classify() bucket.
}


class ConceptClassifier:
    """Classify tickers into coarse report buckets and business-role labels."""

    def __init__(self, json_path: Path, market_store: Any = None):
        self._json_path = Path(json_path)
        self._config = self._load_config()
        self.bucket_order: list[str] = list(self._config.get("bucket_order", []))
        self.default_bucket: str = self._config.get("default_bucket") or "其他"
        self._etf_symbols = set(self._config.get("etf_symbols", []))
        self._symbol_bucket_overrides = self._config.get("symbol_bucket_overrides", {})
        self._theme_bucket_hints = self._config.get("theme_bucket_hints", {})
        self._concept_keyword_rules = self._config.get("concept_keyword_rules", [])
        self._business_role_overrides = self._config.get("business_role_overrides", {})
        self._business_role_keyword_rules = self._config.get("business_role_keyword_rules", [])
        self._bucket_role_fallbacks = self._config.get("bucket_role_fallbacks", {})

        if self.default_bucket not in self.bucket_order:
            self.bucket_order.append(self.default_bucket)

        self._market_store = market_store
        self._registry_cache: Optional[dict[str, dict]] = None
        self._l2_concepts_cache: Optional[list[dict]] = None
        self._l2_label_to_id: Optional[dict[str, str]] = None
        self._l2_id_to_label: Optional[dict[str, str]] = None

    def _load_config(self) -> dict[str, Any]:
        if not self._json_path.exists():
            logger.warning("Report concepts JSON missing at %s", self._json_path)
            return {}
        try:
            return json.loads(self._json_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            logger.error("Report concepts JSON parse error: %s", exc)
            return {}

    @staticmethod
    def _symbol(item: dict | str) -> str:
        if isinstance(item, str):
            return item.upper()
        return str(item.get("symbol") or "").upper()

    @staticmethod
    def _text(item: dict | str) -> str:
        if isinstance(item, str):
            return item.lower()
        keys = [
            "companyName",
            "shortName",
            "longName",
            "company_name",
            "description",
            "sector",
            "industry",
        ]
        return " ".join(str(item.get(key) or "") for key in keys).lower()

    def _theme_bucket_for_symbol(self, symbol: str) -> str | None:
        try:
            from config.settings import THEME_KEYWORDS_SEED
        except Exception:
            return None

        for theme, bucket in self._theme_bucket_hints.items():
            tickers = THEME_KEYWORDS_SEED.get(theme, {}).get("tickers", [])
            if symbol in {ticker.upper() for ticker in tickers}:
                return bucket
        return None

    def classify(self, item: dict | str) -> str:
        """Return a single coarse concept bucket for display grouping."""
        symbol = self._symbol(item)
        if "-P" in symbol:
            return "金融/加密"
        if symbol in self._etf_symbols:
            return "ETF/宏观工具"
        if symbol in self._symbol_bucket_overrides:
            return self._symbol_bucket_overrides[symbol]

        theme_bucket = self._theme_bucket_for_symbol(symbol)
        if theme_bucket:
            return theme_bucket

        text = self._text(item)
        for rule in self._concept_keyword_rules:
            if any(keyword in text for keyword in rule.get("keywords", [])):
                return rule.get("bucket") or self.default_bucket
        return self.default_bucket

    def business_role(self, item: dict | str) -> str:
        """Return a compact business-role label.

        Registry rows authored via manual override / build pipeline are
        preferred — otherwise the row's three-tier display would disagree
        with a legacy-derived role on the same item.
        """
        symbol = self._symbol(item)
        row = self._registry_row(symbol)
        if row and row.get("business_role"):
            return str(row["business_role"])

        if symbol in self._business_role_overrides:
            return self._business_role_overrides[symbol]

        text = self._text(item)
        for rule in self._business_role_keyword_rules:
            if any(keyword in text for keyword in rule.get("keywords", [])):
                return rule.get("label") or "待补业务标签"

        bucket = item.get("concept_bucket") if isinstance(item, dict) else None
        bucket = bucket or self.classify(item)
        return self._bucket_role_fallbacks.get(bucket, "待补业务标签")

    # ---- Registry-aware tags (Phase 1) ----

    def _load_registry(self) -> dict[str, dict]:
        """Lazy-load company_concept_tags rows once per classifier instance."""
        if self._registry_cache is not None:
            return self._registry_cache
        cache: dict[str, dict] = {}
        if self._market_store is not None:
            try:
                conn = self._market_store._get_conn()
                rows = conn.execute(
                    "SELECT symbol, display_tags, business_role, "
                    "primary_concept_id, secondary_concept_id, tertiary_concept_id, "
                    "needs_review, source FROM company_concept_tags"
                ).fetchall()
                for row in rows:
                    cache[row["symbol"]] = dict(row)
            except Exception as exc:
                logger.warning("Concept registry unavailable, falling back to legacy JSON: %s", exc)
                cache = {}
        self._registry_cache = cache
        return cache

    def _load_l2_concepts(self) -> list[dict]:
        """Load ordered level=2 concept rows. Prefer market.db `concepts`
        (rowid preserves taxonomy insert order); fall back to the taxonomy
        JSON SSOT. Both yield ALL 61 L2 — never just the used subset."""
        if self._l2_concepts_cache is not None:
            return self._l2_concepts_cache
        rows: list[dict] = []
        if self._market_store is not None:
            try:
                conn = self._market_store._get_conn()
                db_rows = conn.execute(
                    "SELECT concept_id, label FROM concepts "
                    "WHERE level = 2 ORDER BY rowid"
                ).fetchall()
                rows = [{"concept_id": r["concept_id"], "label": r["label"]}
                        for r in db_rows]
            except Exception as exc:  # noqa: BLE001
                logger.warning("L2 concepts unavailable from DB, "
                               "falling back to taxonomy JSON: %s", exc)
                rows = []
        if not rows:
            rows = self._load_l2_from_taxonomy()
        self._l2_concepts_cache = rows
        return rows

    def _load_l2_from_taxonomy(self) -> list[dict]:
        try:
            from config.settings import CONCEPT_TAXONOMY_PATH
            data = json.loads(
                Path(CONCEPT_TAXONOMY_PATH).read_text(encoding="utf-8")
            )
            return [
                {"concept_id": c["concept_id"], "label": c["label"]}
                for c in data.get("concepts", [])
                if c.get("level") == 2
            ]
        except Exception as exc:  # noqa: BLE001
            logger.warning("Taxonomy JSON L2 load failed: %s", exc)
            return []

    @property
    def l2_bucket_order(self) -> list[str]:
        """Ordered L2 labels (all 61 from SSOT). Empty when DB+JSON both fail
        (caller then keeps legacy bucket_order)."""
        return [c["label"] for c in self._load_l2_concepts()]

    def _registry_row(self, symbol: str) -> dict | None:
        if not symbol:
            return None
        return self._load_registry().get(symbol.upper())

    def display_tags(self, item: dict | str) -> str:
        """Return three-layer display string ('半导体 / 存储 / HBM') from registry,
        falling back to the legacy single-bucket label when the symbol is unregistered.
        """
        symbol = self._symbol(item)
        row = self._registry_row(symbol)
        if row and row.get("display_tags"):
            return row["display_tags"]
        return self.classify(item) or ""

    def concept_tags(self, item: dict | str) -> list[str]:
        """List form of display_tags. Falls back to ``[bucket]`` when not registered."""
        symbol = self._symbol(item)
        row = self._registry_row(symbol)
        if row and row.get("display_tags"):
            return [t.strip() for t in str(row["display_tags"]).split(" / ") if t.strip()]
        bucket = self.classify(item)
        return [bucket] if bucket else []

    def _grouping_bucket(self, item: dict | str) -> str:
        """Resolve the section bucket for grouping, preferring registry primary."""
        symbol = self._symbol(item)
        row = self._registry_row(symbol)
        if row:
            primary = row.get("primary_concept_id")
            if primary:
                bucket = _CONCEPT_TO_LEGACY_BUCKET.get(primary)
                if bucket:
                    return bucket
        if isinstance(item, dict):
            return item.get("concept_bucket") or self.classify(item)
        return self.classify(item)

    def group_items(self, items: list[dict]) -> OrderedDict[str, list[dict]]:
        """Group items by bucket in configured display order; registry wins over
        any pre-computed legacy `concept_bucket` field on the item."""
        grouped: dict[str, list[dict]] = {bucket: [] for bucket in self.bucket_order}
        for item in items:
            bucket = self._grouping_bucket(item)
            grouped.setdefault(bucket, []).append(item)
        return OrderedDict(
            (bucket, grouped[bucket])
            for bucket in self.bucket_order
            if grouped.get(bucket)
        )


_REPORT_CONCEPT_CLASSIFIER: ConceptClassifier | None = None


def get_report_concept_classifier() -> ConceptClassifier:
    """Return the process-wide report concept classifier wired to market.db."""
    global _REPORT_CONCEPT_CLASSIFIER
    if _REPORT_CONCEPT_CLASSIFIER is None:
        from config.settings import REPORT_CONCEPTS_PATH

        store: Any = None
        try:
            from src.data.market_store import get_store
            store = get_store()
        except Exception as exc:  # noqa: BLE001
            logger.warning("MarketStore unavailable, concept registry disabled: %s", exc)

        _REPORT_CONCEPT_CLASSIFIER = ConceptClassifier(
            REPORT_CONCEPTS_PATH, market_store=store
        )
    return _REPORT_CONCEPT_CLASSIFIER
