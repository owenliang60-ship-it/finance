"""End-to-end v2 builder integration test (Task 12).

3-stock fixture covering every classify branch:
    NVDA   — anchor (manual override hit, no LLM call, written to DB by build)
    AMZN   — anchor (manual override hit, no LLM call, written to DB by build)
    OBSCURE — rule + anchor miss → LLM mock returns failure → CSV row with
              blank l1/l2 for Boss; in this test Boss "edits" OBSCURE in the
              CSV then we feed it through Phase 5 + Phase 6 to verify the
              full review loop persists Boss's edits.

Pipeline phases exercised end-to-end:
    Phase 1 (rebuild_concept_tree)  via build_registry --save --force-save
    Phase 3 (classify chain)        — manual / LLM fail branches both fire
    Phase 4 (write 15-col CSV)      — only OBSCURE lands in the review queue
    Phase 5 (read_reviewed_csv)     — Boss-edited row parses cleanly
    Phase 6 (save_to_market_db)     — WAL backup + 3-segment display_tags upsert

External services mocked:
    terminal.llm_concept_prefill.prefill_one — LLM CLI never invoked
    FMP profile fetch                        — bypassed by passing profiles dict
"""
from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).parent.parent
TAXONOMY_V2 = PROJECT_ROOT / "config" / "concepts" / "concept_taxonomy_v2.json"


def _profiles() -> dict[str, dict]:
    return {
        "NVDA": {
            "symbol": "NVDA", "companyName": "NVIDIA Corporation",
            "sector": "Technology", "industry": "Semiconductors",
            "description": "GPU and AI accelerator",
        },
        "AMZN": {
            "symbol": "AMZN", "companyName": "Amazon.com Inc.",
            "sector": "Consumer Cyclical", "industry": "Internet Retail",
            "description": "AWS cloud + e-commerce platform",
        },
        "OBSCURE": {
            "symbol": "OBSCURE", "companyName": "Obscure Holdings",
            "sector": "Unknown", "industry": "Mystery",
            "description": "Unknown business model",
        },
    }


def _llm_failed_result():
    """Mock prefill_one return value when the LLM CLI fails."""
    from terminal.llm_concept_prefill import LLMResult
    return LLMResult(
        l1=None, l2=None, l3_themes=[],
        business_role="", confidence=0.0,
        source="llm_failed", evidence="timeout (mocked)", needs_review=1,
    )


def test_full_v2_pipeline_3_stocks(tmp_path):
    """Drive the full v2 pipeline against a 3-stock fixture; verify the
    concepts taxonomy lands, anchors persist directly, and the LLM-fail
    row round-trips through Boss's CSV edit → Phase 5 → Phase 6 cleanly."""
    from src.data.market_store import MarketStore
    from terminal.company_concepts import ConceptRegistry
    from scripts.build_company_concept_registry import (
        build_registry, read_reviewed_csv, save_to_market_db,
    )

    store = MarketStore(tmp_path / "market.db")
    registry = ConceptRegistry(taxonomy_path=TAXONOMY_V2, watchlist_path=None)
    csv_path = tmp_path / "review.csv"

    # ---- Phase 1 + 3 + 4: build_registry mocked LLM for OBSCURE ----
    with patch(
        "scripts.build_company_concept_registry.prefill_one",
        return_value=_llm_failed_result(),
    ) as mocked_llm:
        result = build_registry(
            store=store, registry=registry,
            universe_symbols=["NVDA", "AMZN", "OBSCURE"],
            profiles=_profiles(),
            portfolio_holdings=["NVDA", "AMZN"],
            broad_top_symbols=["NVDA", "AMZN", "OBSCURE"],
            review_csv_path=csv_path,
            save=True,
            force_save=True,   # OBSCURE failed → bypass gate
        )

    # LLM called exactly once (only OBSCURE missed rule + anchor)
    assert mocked_llm.call_count == 1
    assert mocked_llm.call_args_list[0].kwargs.get("symbol") == "OBSCURE"
    # AMZN is a multi_segment_anchor (manual). NVDA hits the Semiconductors
    # keyword rule. OBSCURE falls through to the LLM which we mocked to fail.
    assert result.manual == 1          # AMZN anchor
    assert result.rule == 1            # NVDA semiconductor rule
    assert result.llm_failed == 1      # OBSCURE
    assert result.saved is True
    assert result.forced_save is True

    # ---- concepts table has the full 11 + 60 + 42 = 113 taxonomy ----
    conn = sqlite3.connect(str(tmp_path / "market.db"))
    conn.row_factory = sqlite3.Row
    level_counts = dict(conn.execute(
        "SELECT level, COUNT(*) FROM concepts GROUP BY level"
    ).fetchall())
    assert level_counts == {1: 11, 2: 60, 3: 42}

    # ---- company_concept_tags after build: NVDA + AMZN persisted; ----
    # ---- OBSCURE excluded because needs_review=1 (build skips it).  ----
    cct_post_build = {
        row["symbol"]: dict(row) for row in conn.execute(
            "SELECT symbol, primary_concept_id, secondary_concept_id, "
            "display_tags, source, needs_review FROM company_concept_tags"
        ).fetchall()
    }
    assert set(cct_post_build.keys()) == {"NVDA", "AMZN"}
    assert cct_post_build["NVDA"]["primary_concept_id"] == "semiconductor"
    assert cct_post_build["NVDA"]["secondary_concept_id"] == "gpu_accelerator"
    assert cct_post_build["AMZN"]["primary_concept_id"] == "ai_compute_cloud"
    assert cct_post_build["AMZN"]["secondary_concept_id"] == "hyperscaler"

    # ---- CSV review manifest: ONE row per universe symbol with the routing ----
    # ---- flag in review_reason. OBSCURE (hard) surfaces at the top so Boss   ----
    # ---- starts at the rows that actually need attention.                    ----
    rows = list(csv.DictReader(csv_path.open()))
    assert {r["symbol"] for r in rows} == {"NVDA", "AMZN", "OBSCURE"}
    assert rows[0]["symbol"] == "OBSCURE"
    assert rows[0]["l1"] == ""
    assert rows[0]["l2"] == ""
    assert rows[0]["prefill_source"] == "llm_failed"
    assert rows[0]["needs_review"] == "1"
    assert rows[0]["review_reason"] == "hard_needs_review"
    # NVDA + AMZN are auto-classified (rule + anchor) → ok rows in the manifest.
    by_sym = {r["symbol"]: r for r in rows}
    assert by_sym["NVDA"]["review_reason"] == "ok"
    assert by_sym["AMZN"]["review_reason"] == "ok"

    # ---- Boss edits CSV: fills in OBSCURE's classification ----
    edited = dict(rows[0])
    edited["l1"] = "工业与航天"
    edited["l2"] = "工程与建筑"
    edited["l3_themes"] = ""
    edited["business_role"] = "Mystery industrials"
    edited["needs_review"] = "0"
    edited["prefill_source"] = "manual"
    edited["confidence"] = "0.85"
    # Write the edited row back
    from scripts.build_company_concept_registry import REVIEW_CSV_FIELDS
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=REVIEW_CSV_FIELDS)
        w.writeheader()
        w.writerow({k: edited.get(k, "") for k in REVIEW_CSV_FIELDS})

    # ---- Phase 5: parse the Boss-edited CSV. extend_pool = {OBSCURE} so ----
    # ---- the coverage check passes (NVDA/AMZN already in DB, not under  ----
    # ---- the review file's purview).                                    ----
    taxonomy = json.loads(TAXONOMY_V2.read_text(encoding="utf-8"))
    parsed = read_reviewed_csv(
        csv_path,
        extend_pool={"OBSCURE"},
        taxonomy=taxonomy,
    )
    assert len(parsed) == 1
    obscure_parsed = parsed[0]
    assert obscure_parsed["symbol"] == "OBSCURE"
    assert obscure_parsed["primary_concept_id"] == "industrial_aerospace"
    assert obscure_parsed["secondary_concept_id"] == "engineering_construction"
    assert obscure_parsed["theme_ids"] == []

    # ---- Phase 6: save_to_market_db writes OBSCURE with rebuilt display_tags ----
    saved = save_to_market_db(
        rows=parsed, store=store, market_db_path=tmp_path / "market.db",
    )
    assert saved == 1

    # WAL-safe backup is taken at the rebuild boundary (build_registry --save
    # earlier in this test produced exactly one pre-rebuild snapshot). The
    # standalone save_to_market_db call above does NOT make its own backup —
    # see test_apply_reviewed_csv_backs_up_before_rebuild for the rollback
    # contract on the apply_reviewed_csv path.
    backups = list(tmp_path.glob("market.db.backup-*-pre-rebuild*"))
    assert len(backups) == 1
    phase6_backups = list(tmp_path.glob("market.db.backup-*-phase6*"))
    assert phase6_backups == [], (
        "stale 'phase6'-labeled backup format must be gone — backups are now "
        "labeled 'pre-rebuild' to reflect when they're actually taken"
    )

    # ---- Final DB state: NVDA + AMZN from anchor write; OBSCURE from Phase 6 ----
    cct_final = {
        row["symbol"]: dict(row) for row in conn.execute(
            "SELECT symbol, primary_concept_id, secondary_concept_id, "
            "display_tags, source, needs_review FROM company_concept_tags"
        ).fetchall()
    }
    conn.close()
    assert set(cct_final.keys()) == {"NVDA", "AMZN", "OBSCURE"}

    # NVDA: rule path (Semiconductors). Display string built from registry's
    # rule resolution + concepts.label. Don't pin the exact L3 (rule may or
    # may not append a theme); assert the 2-segment prefix is correct.
    assert cct_final["NVDA"]["primary_concept_id"] == "semiconductor"
    assert cct_final["NVDA"]["display_tags"].startswith("半导体 / ")
    assert cct_final["NVDA"]["source"] == "rule"

    # AMZN: anchor 3-segment (anchor declares theme_ids=[ai_compute])
    assert cct_final["AMZN"]["source"] == "manual"
    assert cct_final["AMZN"]["display_tags"].startswith("AI算力与云 / 超大规模云")

    # OBSCURE: Phase 6 wrote 2-segment display from concepts.label
    assert cct_final["OBSCURE"]["primary_concept_id"] == "industrial_aerospace"
    assert cct_final["OBSCURE"]["secondary_concept_id"] == "engineering_construction"
    assert cct_final["OBSCURE"]["display_tags"] == "工业与航天 / 工程与建筑"
    assert cct_final["OBSCURE"]["needs_review"] == 0
