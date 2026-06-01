"""Build company concept registry from v2 SSOT + profiles + LLM prefill.

v2 classify chain (registry → LLM orchestration):
    manual (anchor)      ─┐
    rule (keyword)        ├─ deterministic, NO LLM
    unclassified          ┘   ↓
                            prefill_one() ──> llm | llm_failed | llm_fallback

Pipeline phases (single-process):
    Phase 1: rebuild_concept_tree(registry.concepts) — atomic taxonomy upsert
    Phase 3: classify chain — manual / rule / llm wrapper
    Phase 4: write review CSV (15 columns, two queues hard + soft)
    Phase 5: layered gate check (priority_coverage + tail_needs_review_rate)
    Phase 6: upsert company_concept_tags iff gate passes (or --force-save)

`source` values produced by this script:
    manual          — anchor hit in concept_taxonomy_v2.json
    rule            — keyword rule hit
    llm             — LLM prefill returned a validated (l1, l2, l3) triple
    llm_failed      — LLM CLI failed or unparseable
    llm_fallback    — LLM returned but l1/l2 not in taxonomy / parent mismatch
    unclassified    — should not appear after _classify_v2 (only if LLM is skipped)

`needs_review=1` rows: source ∈ {llm_failed, llm_fallback, unclassified}.

CLI:
    python scripts/build_company_concept_registry.py --symbols broad --dry-run
    python scripts/build_company_concept_registry.py --symbols broad --save
    python scripts/build_company_concept_registry.py --rebuild-display
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data.market_store import MarketStore  # noqa: E402
from src.telegram_bot import send_message  # noqa: E402
from terminal.company_concepts import ConceptRegistry  # noqa: E402
from terminal.llm_concept_prefill import LLMResult, prefill_one  # noqa: E402

logger = logging.getLogger(__name__)

GATE_PRIORITY_COVERAGE = 1.0
GATE_TAIL_NEEDS_REVIEW_MAX = 0.30
SOFT_REVIEW_CONFIDENCE_THRESHOLD = 0.7

# --reclassify override whitelist (plan 2026-05-16 §3.6). An old source=llm row
# is overwritten with the new deterministic result ONLY when its FMP
# "{sector}|{industry}" is listed here — i.e. the correct bucket was ADDED by
# this rebuild, so run-1's LLM had no way to choose it. telecom_operator is the
# only L2 the rebuild adds, so the whitelist has exactly one entry. Every other
# llm↔map disagreement goes to the deterministic_conflict queue for Boss, never
# auto-overwritten (auto-overwrite would erase correct fine-grained LLM calls
# like DLR/EQIX datacenter REIT, COIN crypto exchange, VEEV enterprise SaaS).
RECLASSIFY_OVERRIDE_WHITELIST: frozenset[str] = frozenset(
    {"Communication Services|Telecommunications Services"}
)

PROFILES_PATH = PROJECT_ROOT / "data" / "fundamental" / "profiles.json"
EXTENDED_UNIVERSE_PATH = PROJECT_ROOT / "data" / "pool" / "extended_universe.json"
COMPANY_DB_PATH = PROJECT_ROOT / "data" / "company.db"

REVIEW_CSV_FIELDS: list[str] = [
    "review_reason",
    "symbol",
    "company_name",
    "fmp_sector",
    "fmp_industry",
    "market_cap_b",
    "mcap_tier",
    "description",
    "l1",
    "l2",
    "l3_themes",
    "business_role",
    "prefill_source",
    "confidence",
    "needs_review",
    "boss_notes",
]


class BuildGateError(RuntimeError):
    """Raised when --save fails the layered gate without --force-save."""


class CSVValidationError(RuntimeError):
    """Raised when --read-reviewed-csv hits any of 10 fail-fast checks."""


@dataclass
class BuildResult:
    symbols: int
    priority_list_size: int
    priority_coverage: float
    tagged: int
    manual: int
    rule: int
    llm: int
    llm_failed: int
    llm_fallback: int
    unclassified: int
    needs_review: int           # hard queue (gate denominator)
    soft_review: int            # soft queue (rule/llm with confidence < 0.7)
    tail_needs_review_rate: float
    review_csv: str | None
    saved: bool
    forced_save: bool

    def as_summary(self) -> str:
        forced_marker = " (forced)" if self.forced_save else ""
        lines = [
            "Company Concept Registry (v2)",
            f"symbols: {self.symbols}",
            f"priority_list_size: {self.priority_list_size}",
            f"priority_coverage: {self.priority_coverage * 100:.1f}%",
            f"tagged: {self.tagged}",
            f"manual: {self.manual}",
            f"rule: {self.rule}",
            f"llm: {self.llm}",
            f"llm_failed: {self.llm_failed}",
            f"llm_fallback: {self.llm_fallback}",
            f"unclassified: {self.unclassified}",
            f"needs_review (hard): {self.needs_review}",
            f"soft_review (low_conf): {self.soft_review}",
            f"tail_needs_review_rate: {self.tail_needs_review_rate * 100:.1f}%",
            f"review_csv: {self.review_csv or '-'}",
            f"saved: {'yes' if self.saved else 'no'}{forced_marker}",
        ]
        return "\n".join(lines)


def _backup_file(path: Path, label: str) -> Path | None:
    """Backup any JSON/plain file with .backup-<ts>-<label> suffix. Returns
    backup path or None when source doesn't exist. Used for JSON; SQLite DBs
    must use _backup_sqlite() (WAL-safe)."""
    if not path.exists():
        return None
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    backup = path.with_name(f"{path.name}.backup-{ts}-{label}")
    shutil.copy2(path, backup)
    logger.info("Backed up %s -> %s", path, backup)
    return backup


def _fetch_fmp_profile(symbol: str) -> dict:
    """Call FMP /profile/<symbol>. Returns flattened dict. Honors client rate-limit."""
    from src.data.fmp_client import fmp_client  # existing module-level singleton
    resp = fmp_client.get_profile(symbol)
    if isinstance(resp, dict):
        return dict(resp)
    return {}


def refresh_profiles(
    symbols: list[str],
    profiles_path: Path = PROFILES_PATH,
) -> int:
    """Phase 2: pull FMP /profile/<symbol> and merge into JSON keyed by symbol.

    Output schema matches the existing _load_profiles consumer:
        {"AAPL": {"symbol": "AAPL", "companyName": "...", "sector": "...",
                  "industry": "...", "description": "...", ...},
         ...,
         "_meta": {"updated_at": "YYYY-MM-DD HH:MM:SS", "count": N}}

    Merge semantics (parity with fundamental_fetcher.update_profiles):
        - Load existing JSON; failed-fetch symbols KEEP their previous entry
          so a transient FMP outage doesn't shrink the cache.
        - _meta.updated_at advances every run (even when every fetch failed)
          so data_health._check_fundamental_freshness reflects the cron run.
        - Write goes through a temp file + os.replace for atomicity.

    Backs up existing JSON first. Rate-limit handled by FMP client (2s).
    Does NOT touch company.db — market_cap continues to flow through the
    existing data pipeline.

    Returns the count of symbols successfully fetched THIS run (not the total
    cache size), matching the original Phase 2 contract.
    """
    _backup_file(profiles_path, "preprofiles")
    profiles_path.parent.mkdir(parents=True, exist_ok=True)

    existing: dict[str, dict] = {}
    if profiles_path.exists():
        try:
            existing = json.loads(profiles_path.read_text(encoding="utf-8")) or {}
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Existing profiles.json unreadable (%s); starting empty", exc)
            existing = {}

    fetched = 0
    for sym in symbols:
        try:
            profile = _fetch_fmp_profile(sym)
        except Exception as exc:  # noqa: BLE001
            logger.warning("FMP profile fetch failed for %s: %s", sym, exc)
            continue
        if profile:
            existing[sym.upper()] = profile
            fetched += 1

    existing["_meta"] = {
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "count": sum(1 for k in existing if k != "_meta"),
    }

    tmp = profiles_path.with_suffix(profiles_path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(existing, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    import os
    os.replace(tmp, profiles_path)
    logger.info(
        "Refreshed %d profiles (%d total) -> %s",
        fetched, existing["_meta"]["count"], profiles_path,
    )
    return fetched


def _classify_v2(
    registry: ConceptRegistry,
    profile: dict,
    taxonomy: dict,
) -> dict:
    """Drop-in replacement for registry.classify() with LLM fallback wiring.

    Invariant: registry only emits source ∈ {manual, rule, unclassified}.
    LLM-only sources (llm, llm_failed, llm_fallback) are stamped here.
    """
    row = registry.classify(profile)
    if row.get("source") in ("manual", "rule"):
        return row
    assert row.get("source") == "unclassified", (
        f"registry returned unexpected source={row.get('source')}; "
        "v2 invariant: registry only emits manual|rule|unclassified"
    )
    symbol = (profile.get("symbol") or "").upper()
    llm = prefill_one(symbol=symbol, profile=profile, taxonomy=taxonomy)
    row["l1"] = llm.l1
    row["l2"] = llm.l2
    row["l3_themes"] = list(llm.l3_themes)
    row["business_role"] = llm.business_role or row.get("business_role", "")
    row["confidence"] = llm.confidence
    row["source"] = llm.source
    row["evidence"] = llm.evidence
    row["needs_review"] = llm.needs_review
    # Rebuild display_tags using concepts known to the registry
    row["display_tags"] = registry._auto_display_tags(llm.l1, llm.l2, llm.l3_themes)
    return row


def _mcap_to_tier(market_cap_usd: float | None) -> str:
    """Map market cap to L4 tier enum.

    Boundaries (spec §3.4): >=$1T mega, >=$300B large, >=$100B mid,
    >=$10B small, <$10B → '' (extend pool filter excludes these in practice).
    """
    if market_cap_usd is None or market_cap_usd <= 0:
        return ""
    if market_cap_usd >= 1_000_000_000_000:
        return "mega"
    if market_cap_usd >= 300_000_000_000:
        return "large"
    if market_cap_usd >= 100_000_000_000:
        return "mid"
    if market_cap_usd >= 10_000_000_000:
        return "small"
    return ""


# Back-compat alias for the original Task 4b naming.
_mcap_tier = _mcap_to_tier


def _load_market_caps_from_company_db(
    company_db_path: Path = COMPANY_DB_PATH,
) -> dict[str, float]:
    """Read-only cross-DB query: companies.market_cap. Empty when DB absent."""
    if not company_db_path.exists():
        return {}
    import sqlite3
    conn = sqlite3.connect(str(company_db_path))
    try:
        out: dict[str, float] = {}
        for r in conn.execute(
            "SELECT symbol, market_cap FROM companies "
            "WHERE market_cap IS NOT NULL AND market_cap > 0"
        ):
            sym = str(r[0]).upper()
            try:
                out[sym] = float(r[1])
            except (TypeError, ValueError):
                continue
        return out
    finally:
        conn.close()


def _write_taxonomy_reference_csv(
    taxonomy: dict,
    out_path: Path,
) -> None:
    """Emit reports/concept_registry/taxonomy_reference.csv (spec §6.3).

    Columns: level / id / name_cn / parent_id / typical_members(empty).
    Boss uses this as a lookup table while editing the main review CSV.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["level", "id", "name_cn", "parent_id", "typical_members"])
        for c in taxonomy.get("concepts", []):
            w.writerow([
                c.get("level", ""),
                c.get("concept_id", ""),
                c.get("label", ""),
                c.get("parent_id") or "",
                "",
            ])


def _row_to_csv(
    row: dict,
    profile: dict,
    review_reason: str,
    market_cap_usd: float | None = None,
    concepts_by_id: dict[str, str] | None = None,
) -> dict[str, str]:
    """Render one classify row + profile + market_cap into 16-col CSV dict.

    l1/l2/l3 columns carry the Chinese label (concept.label) so Boss can read
    and edit in his native language. Phase 5 (`read_reviewed_csv`) maps labels
    back to concept_id via the same taxonomy. Missing l1/l2 (failure rows)
    leave the column blank.
    """
    cap_b = market_cap_usd / 1_000_000_000 if market_cap_usd else None
    concepts_by_id = concepts_by_id or {}
    l1_label = concepts_by_id.get(row.get("l1") or "", "") if row.get("l1") else ""
    l2_label = concepts_by_id.get(row.get("l2") or "", "") if row.get("l2") else ""
    l3_labels = [
        concepts_by_id.get(tid, "") for tid in (row.get("l3_themes") or [])
    ]
    return {
        "review_reason": review_reason,
        "symbol": row["symbol"],
        "company_name": profile.get("companyName", "") or profile.get("company_name", ""),
        "fmp_sector": profile.get("sector", ""),
        "fmp_industry": profile.get("industry", ""),
        "market_cap_b": f"{cap_b:.2f}" if cap_b is not None else "",
        "mcap_tier": _mcap_to_tier(market_cap_usd),
        "description": (profile.get("description", "") or "")[:500],
        "l1": l1_label,
        "l2": l2_label,
        "l3_themes": ";".join(s for s in l3_labels if s),
        "business_role": row.get("business_role", ""),
        "prefill_source": row.get("source", ""),
        "confidence": f"{row.get('confidence', 0.0):.2f}",
        "needs_review": str(int(row.get("needs_review", 0))),
        "boss_notes": "",
    }


def write_review_csv(
    *,
    rows: list[dict],
    csv_path: Path,
    taxonomy: dict,
    profiles: dict[str, dict] | None = None,
    market_caps: dict[str, float] | None = None,
) -> None:
    """Phase 4 standalone: write the 15-col review CSV (16 with review_reason).

    Sort order: needs_review=1 first, then by confidence ascending so the
    lowest-confidence rows surface at the top of Boss's review queue.
    """
    profiles = profiles or {}
    market_caps = {k.upper(): v for k, v in (market_caps or {}).items()}
    concepts_by_id = {c["concept_id"]: c["label"] for c in taxonomy.get("concepts", [])}

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    sorted_rows = sorted(
        rows,
        key=lambda r: (-int(r.get("needs_review", 0)), float(r.get("confidence", 1.0))),
    )
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=REVIEW_CSV_FIELDS)
        w.writeheader()
        for r in sorted_rows:
            sym = r["symbol"].upper()
            profile = dict(profiles.get(sym) or {})
            cap = market_caps.get(sym)
            if r.get("needs_review", 0) == 1:
                reason = "hard_needs_review"
            elif (
                r.get("source") in ("rule", "llm")
                and float(r.get("confidence", 1.0)) < SOFT_REVIEW_CONFIDENCE_THRESHOLD
            ):
                reason = "soft_low_confidence"
            else:
                reason = "ok"
            w.writerow(_row_to_csv(r, profile, reason, cap, concepts_by_id))


def _row_to_db(row: dict) -> dict:
    """Map v2 classify row → market_store.upsert_company_concepts() input shape."""
    return {
        "symbol": row["symbol"],
        "primary_concept_id": row["l1"],
        "secondary_concept_id": row["l2"],
        "tertiary_concept_id": None,
        "theme_ids": list(row.get("l3_themes") or []),
        "display_tags": row.get("display_tags", ""),
        "business_role": row.get("business_role", ""),
        "confidence": float(row.get("confidence", 0.0)),
        "source": row.get("source", "unknown"),
        "evidence": row.get("evidence", ""),
        "needs_review": int(row.get("needs_review", 0)),
    }


def build_registry(
    *,
    store: MarketStore,
    registry: ConceptRegistry,
    universe_symbols: Iterable[str],
    profiles: dict[str, dict],
    portfolio_holdings: Iterable[str] | None,
    broad_top_symbols: Iterable[str] | None,
    review_csv_path: Path,
    save: bool,
    force_save: bool,
    gate_priority_coverage: float = GATE_PRIORITY_COVERAGE,
    gate_tail_needs_review_max: float = GATE_TAIL_NEEDS_REVIEW_MAX,
    market_caps: dict[str, float] | None = None,
    symbols_only: bool = False,
) -> BuildResult:
    """Run the v2 build pipeline. Concepts tree + company tags persist together
    iff gate passes (or --force-save). Dry-run leaves the DB untouched.

    symbols_only: when True, classify ONLY universe_symbols — skip the
    watchlist / portfolio / broad_top union. Used for narrow targeted
    reclassify runs (e.g. patching a handful of symbols) so the universe
    is not silently inflated. portfolio_holdings / broad_top_symbols are
    still honored for the priority-coverage gate calc.
    """
    portfolio_set = {s.upper() for s in (portfolio_holdings or [])}
    broad_top_set = {s.upper() for s in (broad_top_symbols or [])}
    market_caps = {k.upper(): v for k, v in (market_caps or {}).items()}

    union_sources = (
        (universe_symbols,)
        if symbols_only
        else (universe_symbols, registry.watchlist_symbols,
              portfolio_set, broad_top_set)
    )
    seen: set[str] = set()
    full_universe: list[str] = []
    for src in union_sources:
        for s in src:
            up = s.upper()
            if up not in seen:
                seen.add(up)
                full_universe.append(up)

    taxonomy = registry._taxonomy  # SSOT dict used by LLM prefill
    concepts_by_id = {c["concept_id"]: c["label"] for c in taxonomy.get("concepts", [])}

    rows: list[dict] = []
    csv_rows: list[dict[str, str]] = []
    for sym in full_universe:
        profile = dict(profiles.get(sym) or {})
        profile.setdefault("symbol", sym)
        row = _classify_v2(registry, profile, taxonomy)
        rows.append(row)
        cap_usd = market_caps.get(sym)
        # Phase 4 manifest: ONE row per universe symbol. review_reason is the
        # routing flag — hard rows surface first so Boss starts at the top of
        # the file and can stop when he reaches "ok". Without this manifest,
        # --read-reviewed-csv would report coverage errors for every clean
        # symbol the dry-run skipped, breaking the dry-run → review → save loop.
        if row["needs_review"] == 1:
            reason = "hard_needs_review"
        elif (
            row["source"] in ("rule", "llm")
            and row["confidence"] < SOFT_REVIEW_CONFIDENCE_THRESHOLD
        ):
            reason = "soft_low_confidence"
        else:
            reason = "ok"
        csv_rows.append(
            _row_to_csv(row, profile, reason, cap_usd, concepts_by_id)
        )

    priority = registry.priority_list(
        broad_top_symbols=list(broad_top_symbols or []),
        portfolio_holdings=list(portfolio_holdings or []),
    )

    by_sym = {r["symbol"]: r for r in rows}
    priority_in_universe = priority & set(by_sym.keys())
    if priority_in_universe:
        priority_clean = sum(
            1 for s in priority_in_universe if by_sym[s]["needs_review"] == 0
        )
        priority_coverage = priority_clean / len(priority_in_universe)
    else:
        priority_coverage = 1.0

    tail_set = set(by_sym.keys()) - priority
    if tail_set:
        tail_needs = sum(1 for s in tail_set if by_sym[s]["needs_review"] == 1)
        tail_rate = tail_needs / len(tail_set)
    else:
        tail_rate = 0.0

    review_csv_path.parent.mkdir(parents=True, exist_ok=True)
    # Sort by review_reason bucket (hard → soft → ok), then by confidence asc
    # within each bucket so the most uncertain rows surface at the top.
    reason_rank = {
        "hard_needs_review": 0,
        "soft_low_confidence": 1,
        "ok": 2,
    }
    sorted_csv_rows = sorted(
        csv_rows,
        key=lambda r: (
            reason_rank.get(r.get("review_reason", "ok"), 3),
            float(r.get("confidence", "1") or 1),
        ),
    )
    with review_csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=REVIEW_CSV_FIELDS)
        w.writeheader()
        for r in sorted_csv_rows:
            w.writerow(r)

    # Manifest sidecar: the immutable record of what dry-run intended Boss to
    # review. apply_reviewed_csv unions this set with the caller's extend_pool
    # so a hand edit that DROPS a row (watchlist/portfolio/broad_top symbol not
    # in extended pool) fails validation rather than silently disappearing
    # when rebuild_concept_tree wipes the old tags.
    _write_review_manifest(review_csv_path, full_universe)

    by_source: dict[str, int] = {}
    for r in rows:
        by_source[r["source"]] = by_source.get(r["source"], 0) + 1
    needs_review_rows = [r for r in rows if r["needs_review"] == 1]
    soft_review_rows = [
        r for r in rows
        if r["needs_review"] == 0
        and r["source"] in ("rule", "llm")
        and r["confidence"] < SOFT_REVIEW_CONFIDENCE_THRESHOLD
    ]

    gate_failed: list[str] = []
    if not list(broad_top_symbols or []):
        gate_failed.append(
            "broad_top is empty — dollar_volume DB likely has no rankings yet "
            "(run scripts/collect_dollar_volume.py first)"
        )
    if priority_coverage < gate_priority_coverage:
        gate_failed.append(
            f"priority_coverage {priority_coverage:.2%} < {gate_priority_coverage:.0%}"
        )
    if tail_rate >= gate_tail_needs_review_max:
        gate_failed.append(
            f"tail_needs_review_rate {tail_rate:.2%} >= {gate_tail_needs_review_max:.0%}"
        )

    if save and gate_failed and not force_save:
        raise BuildGateError("; ".join(gate_failed))

    will_save = save and (not gate_failed or force_save)
    if will_save:
        # Backup BEFORE rebuild_concept_tree wipes company_concept_tags. If the
        # upsert (or anything else after) fails, this backup is the pre-mutation
        # state Boss can restore from. Backing up after rebuild would only
        # capture the already-cleared DB.
        _backup_sqlite(store.db_path, "pre-rebuild")
        store.rebuild_concept_tree(registry.concepts)
        db_rows = [_row_to_db(r) for r in rows if r["needs_review"] == 0]
        if db_rows:
            store.upsert_company_concepts(db_rows)

    return BuildResult(
        symbols=len(rows),
        priority_list_size=len(priority),
        priority_coverage=priority_coverage,
        tagged=len(rows),
        manual=by_source.get("manual", 0),
        rule=by_source.get("rule", 0),
        llm=by_source.get("llm", 0),
        llm_failed=by_source.get("llm_failed", 0),
        llm_fallback=by_source.get("llm_fallback", 0),
        unclassified=by_source.get("unclassified", 0),
        needs_review=len(needs_review_rows),
        soft_review=len(soft_review_rows),
        tail_needs_review_rate=tail_rate,
        review_csv=str(review_csv_path),
        saved=will_save,
        forced_save=will_save and bool(gate_failed),
    )


def rebuild_display_tags(
    *, store: MarketStore, registry: ConceptRegistry
) -> dict[str, int]:
    """Recompute display_tags for all company_concept_tags rows from current
    concepts.label (v2). Manual anchor rows have their canonical labels in
    concepts, so they're rebuilt the same way — no special preservation."""
    conn = store._get_conn()
    concepts_by_id = {
        row[0]: row[1]
        for row in conn.execute("SELECT concept_id, label FROM concepts").fetchall()
    }

    rebuilt = 0
    updates: list[tuple[str, str]] = []
    for row in conn.execute("SELECT * FROM company_concept_tags").fetchall():
        labels: list[str] = []
        for cid in (row["primary_concept_id"], row["secondary_concept_id"]):
            if cid and cid in concepts_by_id:
                labels.append(concepts_by_id[cid])
        try:
            theme_ids = json.loads(row["theme_ids"] or "[]")
        except (TypeError, ValueError):
            theme_ids = []
        if theme_ids:
            first = theme_ids[0]
            if first in concepts_by_id:
                labels.append(concepts_by_id[first])
        new_display = " / ".join(labels)
        if new_display != row["display_tags"]:
            updates.append((new_display, row["symbol"]))
        rebuilt += 1

    with conn:
        for new_display, symbol in updates:
            conn.execute(
                "UPDATE company_concept_tags SET display_tags = ? WHERE symbol = ?",
                (new_display, symbol),
            )

    return {"rebuilt": rebuilt, "updated": len(updates)}


def read_reviewed_csv(
    csv_path: Path,
    *,
    extend_pool: set[str],
    taxonomy: dict,
    validate_only: bool = False,
) -> list[dict]:
    """Phase 5: parse Boss-reviewed CSV, validate against taxonomy, return
    upsert-ready row dicts. Raises CSVValidationError on any of the 10 checks
    unless `validate_only=True` (then emits _rejected.csv + summary.txt and
    returns the rows that DID pass).

    The 10 fail-fast checks (spec §10.2):
        1. missing extend pool symbols → coverage-level error
        2. duplicate symbol → per-row error
        3. l1 empty / whitespace → per-row
        4. l2 empty / whitespace → per-row
        5. l1 not in 11 L1 set → per-row
        6. l2 not in L2 pool → per-row
        7. l2.parent_id != l1.concept_id → per-row
        8. l3 alias unresolvable → per-row
        9. resolved l3 element level != 3 → per-row (overlap with #8 but explicit)
       10. validate_only mode emits dual artifact (rejected.csv + summary.txt)
    """
    l1_label_to_id = {
        c["label"]: c["concept_id"]
        for c in taxonomy["concepts"] if c["level"] == 1
    }
    l2_label_to_id = {
        c["label"]: (c["concept_id"], c.get("parent_id"))
        for c in taxonomy["concepts"] if c["level"] == 2
    }
    l3_concepts = {
        c["concept_id"]: c for c in taxonomy["concepts"] if c["level"] == 3
    }
    l3_alias_to_id: dict[str, str] = {}
    for cid, c in l3_concepts.items():
        # cid → cid keeps the parser in lockstep with
        # ConceptRegistry.resolve_l3_alias, which accepts label, alias, or
        # bare concept_id (idempotent). Boss copying ai_compute from the id
        # column of taxonomy_reference.csv must not trip "not in pool".
        l3_alias_to_id[cid] = cid
        l3_alias_to_id[c["label"]] = cid
        for alias in c.get("aliases", []):
            l3_alias_to_id[alias] = cid

    rejected: list[tuple[dict, list[str]]] = []
    coverage_errors: list[str] = []
    parsed: list[dict] = []
    seen_symbols: set[str] = set()

    with csv_path.open(encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        raw_rows = list(reader)

    for raw in raw_rows:
        sym = raw.get("symbol", "").strip().upper()
        l1_label = raw.get("l1", "").strip()
        l2_label = raw.get("l2", "").strip()
        l3_raw = raw.get("l3_themes", "").strip()

        row_errors: list[str] = []
        if not sym:
            row_errors.append("symbol empty")
        else:
            if sym in seen_symbols:
                row_errors.append(f"duplicate symbol {sym}")
            seen_symbols.add(sym)

        if not l1_label:
            row_errors.append("l1 empty")
        elif l1_label not in l1_label_to_id:
            row_errors.append(f"l1 '{l1_label}' not in 11 L1")

        l2_id: str | None = None
        if not l2_label:
            row_errors.append("l2 empty")
        elif l2_label not in l2_label_to_id:
            row_errors.append(
                f"l2 '{l2_label}' not in {len(l2_label_to_id)} L2 pool"
            )
        else:
            l2_id, l2_parent = l2_label_to_id[l2_label]
            if l1_label in l1_label_to_id and l2_parent != l1_label_to_id[l1_label]:
                row_errors.append(
                    f"l2 parent mismatch (l2.parent={l2_parent}, "
                    f"l1={l1_label_to_id[l1_label]})"
                )

        l3_ids: list[str] = []
        if l3_raw:
            for token in (t.strip() for t in l3_raw.split(";") if t.strip()):
                tid = l3_alias_to_id.get(token)
                if tid is None:
                    row_errors.append(f"L3 '{token}' not in pool")
                else:
                    # Defense-in-depth: verify level=3
                    if l3_concepts[tid].get("level") != 3:
                        row_errors.append(f"L3 '{token}' resolved level != 3")
                    else:
                        l3_ids.append(tid)

        if row_errors:
            rejected.append((raw, row_errors))
        else:
            l1_id = l1_label_to_id[l1_label]
            try:
                conf = float(raw.get("confidence", 0) or 0)
            except ValueError:
                conf = 0.0
            try:
                nr = int(raw.get("needs_review", 0) or 0)
            except ValueError:
                nr = 0
            parsed.append({
                "symbol": sym,
                "primary_concept_id": l1_id,
                "secondary_concept_id": l2_id,
                "tertiary_concept_id": None,
                "theme_ids": l3_ids,
                "business_role": raw.get("business_role", ""),
                "confidence": conf,
                "source": raw.get("prefill_source", "manual") or "manual",
                "needs_review": nr,
                "evidence": "csv_review",
            })

    missing = extend_pool - seen_symbols
    if missing:
        sample = sorted(missing)[:10]
        suffix = "..." if len(missing) > 10 else ""
        coverage_errors.append(
            f"missing {len(missing)} symbols from extend pool: {sample}{suffix}"
        )

    if validate_only and (rejected or coverage_errors):
        rejected_path = csv_path.with_name(f"{csv_path.stem}_rejected.csv")
        with rejected_path.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=REVIEW_CSV_FIELDS + ["_errors"])
            w.writeheader()
            for raw, errs in rejected:
                merged = {k: raw.get(k, "") for k in REVIEW_CSV_FIELDS}
                merged["_errors"] = " | ".join(errs)
                w.writerow(merged)

        summary_path = csv_path.with_name(f"{csv_path.stem}_rejected_summary.txt")
        lines = [
            f"Per-row failures: {len(rejected)}",
            f"Total per-row error count: {sum(len(e) for _, e in rejected)}",
            "",
            "Coverage-level errors:",
        ]
        if coverage_errors:
            lines.extend(f"  - {e}" for e in coverage_errors)
        else:
            lines.append("  (none)")
        summary_path.write_text("\n".join(lines), encoding="utf-8")
        return parsed

    if rejected or coverage_errors:
        msg_lines: list[str] = []
        for raw, errs in rejected[:20]:
            sym = raw.get("symbol", "?")
            msg_lines.append(f"  {sym}: {'; '.join(errs)}")
        if len(rejected) > 20:
            msg_lines.append(f"  ... and {len(rejected) - 20} more rows")
        msg_lines.extend(coverage_errors)
        raise CSVValidationError("\n".join(msg_lines))

    return parsed


def _manifest_path_for(csv_path: Path) -> Path:
    return csv_path.with_name(f"{csv_path.stem}_manifest.json")


def _write_review_manifest(csv_path: Path, symbols: Iterable[str]) -> Path:
    """Snapshot the dry-run's expected review set next to the CSV. This is the
    rollback line of defense against manual CSV edits that delete priority
    rows — apply_reviewed_csv unions this with the caller's extend_pool so a
    dropped row fails the coverage check.
    """
    payload = {
        "symbols": sorted({str(s).upper() for s in symbols}),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "csv": csv_path.name,
    }
    manifest = _manifest_path_for(csv_path)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return manifest


def _load_review_manifest(csv_path: Path) -> set[str] | None:
    """Return the manifest's expected symbol set, or None when missing or
    unparseable. A missing manifest is non-fatal — older CSVs without a
    sidecar still validate against the caller-supplied extend_pool.
    """
    manifest = _manifest_path_for(csv_path)
    if not manifest.exists():
        return None
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Review manifest unreadable (%s); falling back", exc)
        return None
    syms = data.get("symbols") or data.get("full_universe") or []
    return {str(s).upper() for s in syms}


# ---- A3 weekly-sync helpers: CSV symbol I/O + schema normalize (plan 2026-06-01) ----

def _read_csv_symbols(csv_path: Path) -> set[str]:
    """Symbol set from a review CSV (any column order)."""
    if not csv_path.exists():
        return set()
    with csv_path.open(encoding="utf-8") as fh:
        return {
            (r.get("symbol") or "").strip().upper()
            for r in csv.DictReader(fh)
            if (r.get("symbol") or "").strip()
        }


def _db_tag_symbols(store: "MarketStore") -> set[str]:
    """Symbol set currently in company_concept_tags."""
    conn = store._get_conn()
    return {row[0].upper() for row in conn.execute("SELECT symbol FROM company_concept_tags")}


def _normalize_review_csv(src: Path, dst: Path) -> int:
    """Normalize any historical review CSV to the canonical 16-field schema.

    Handles the legacy 17-col header with a DUPLICATE `business_role` column
    (5/24, 5/30) by coalescing duplicates: first non-empty positional value wins.
    Writes via temp + os.replace.
    """
    with src.open(encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    if not rows:
        raise ValueError(f"empty CSV: {src}")
    header, *data = rows
    col_idx: dict[str, list[int]] = {}
    for i, name in enumerate(header):
        col_idx.setdefault(name.strip(), []).append(i)
    out: list[dict[str, str]] = []
    for raw in data:
        rec: dict[str, str] = {}
        for field in REVIEW_CSV_FIELDS:
            val = ""
            for i in col_idx.get(field, []):
                if i < len(raw) and raw[i].strip():
                    val = raw[i]
                    break
            rec[field] = val
        out.append(rec)
    tmp = dst.with_suffix(dst.suffix + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=REVIEW_CSV_FIELDS)
        w.writeheader()
        w.writerows(out)
    os.replace(tmp, dst)
    return len(out)


def _append_csv_atomic(csv_path: Path, new_csv_rows: list[dict]) -> None:
    """Append rows to a review CSV atomically, normalizing to REVIEW_CSV_FIELDS."""
    existing: list[dict] = []
    if csv_path.exists():
        with csv_path.open(encoding="utf-8") as fh:
            existing = list(csv.DictReader(fh))
    combined = existing + list(new_csv_rows)
    tmp = csv_path.with_suffix(csv_path.suffix + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=REVIEW_CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        for row in combined:
            w.writerow({k: row.get(k, "") for k in REVIEW_CSV_FIELDS})
    os.replace(tmp, csv_path)


# ---- A3 weekly_sync: universe-drift concept sync (plan 2026-06-01) ----


@dataclass
class WeeklySyncResult:
    drift_in: list[str] = field(default_factory=list)
    churn_out: list[str] = field(default_factory=list)
    auto_saved: list[str] = field(default_factory=list)   # deterministic
    queued: list[str] = field(default_factory=list)        # llm/needs review
    failed: list[str] = field(default_factory=list)        # no profile / classify error
    error: str | None = None                               # fatal (fail-closed)

    def summary_text(self) -> str:
        if self.error:
            return f"⚠️ Concept 周刷失败: {self.error}"
        parts = [
            "🏷️ Concept 周刷",
            f"自动落库 {len(self.auto_saved)} / 待审 {len(self.queued)}"
            f" / churn-out {len(self.churn_out)} / 失败 {len(self.failed)}",
        ]
        if not self.drift_in:
            parts = ["🏷️ Concept 周刷：本周无新增票"]
        return " · ".join(parts)


def _failed_review_row(sym: str) -> dict:
    """Minimal review row for a drift-in symbol we couldn't classify (error)."""
    return {"symbol": sym, "l1": None, "l2": None, "l3_themes": [],
            "business_role": "", "confidence": 0.0, "source": "sync_failed",
            "evidence": "weekly_sync: classify failed", "needs_review": 1}


def weekly_sync(
    *,
    registry: "ConceptRegistry",
    taxonomy: dict,
    canonical_csv: Path,
    extended_universe_path: Path,
    profiles_path: Path,
    market_db_path: Path,
    queue_dir: Path,
    run_date: str,
    classify_fn=_classify_v2,
    refresh_fn=refresh_profiles,
    store_factory=None,
    telegram_fn=None,
) -> WeeklySyncResult:
    """7a 分类 → 7b 增量落库（preflight/postflight）→ 7c 队列 + Telegram(必发).

    Deterministic ({manual,rule}) auto-saved incrementally; LLM/failed queued.
    Design §5 + D2(每周必推)/D3(churn KEEP). Single try/except/finally so the
    Telegram summary fires on success, no-drift, AND fatal error.
    `_load_profiles` confirmed at build:1223; `_classify_v2` hits anchor by
    SYMBOL first (company_concepts:123) — so classify BEFORE any profile gate
    (P1.5), else anchor drift-in would be wrongly dropped.
    """
    res = WeeklySyncResult()
    res._deterministic = []   # list[(row, profile)] → persist
    res._queue = []           # list[(row, profile)] → review CSV
    res._failed_rows = []     # list[dict] → review CSV (failed bucket gets an artifact, P1.4)
    try:
        base = _read_csv_symbols(canonical_csv)
        universe = {s.upper() for s in _load_universe(extended_universe_path)}
        res.drift_in = sorted(universe - base)
        res.churn_out = sorted(base - universe)   # KEEP (D3): not deleted

        if res.drift_in:
            refresh_fn(res.drift_in, profiles_path=profiles_path)   # FMP, delta only
            profiles = _load_profiles(profiles_path)
            for sym in res.drift_in:
                profile = dict(profiles.get(sym) or {})
                profile.setdefault("symbol", sym)
                try:
                    row = classify_fn(registry, profile, taxonomy)   # anchor-by-symbol works w/o profile
                except Exception as exc:                              # P1.3: never bubble out
                    logger.warning("classify failed for %s: %s", sym, exc)
                    res.failed.append(sym)
                    res._failed_rows.append(_failed_review_row(sym))
                    continue
                row["symbol"] = sym
                if row.get("source") in ("manual", "rule"):          # deterministic (NOT needs_review gate)
                    res._deterministic.append((row, profile))
                    res.auto_saved.append(sym)
                else:                                                 # llm / llm_fallback / etc → queue
                    res._queue.append((row, profile))
                    res.queued.append(sym)

            # coverage self-check (issue 030 automated): nothing silently dropped
            accounted = set(res.auto_saved) | set(res.queued) | set(res.failed)
            if accounted != set(res.drift_in):
                res.error = f"coverage gap: drift_in={len(res.drift_in)} accounted={len(accounted)}"
            elif store_factory is not None:
                _weekly_sync_persist(                                 # defined in Task 3
                    res, canonical_csv=canonical_csv, profiles_path=profiles_path,
                    market_db_path=market_db_path, queue_dir=queue_dir,
                    taxonomy=taxonomy, run_date=run_date, store_factory=store_factory)
    except Exception as exc:                                          # P1.3 fatal → still notify
        logger.exception("weekly_sync fatal")
        res.error = f"weekly_sync fatal: {exc}"
    finally:                                                          # P1.2: always push (D2)
        if telegram_fn is not None:
            try:
                telegram_fn(res.summary_text(), channel="group")
            except Exception as exc:
                logger.warning("weekly_sync telegram failed: %s", exc)
    return res


def _weekly_sync_persist(
    res: "WeeklySyncResult",
    *,
    canonical_csv: Path,
    profiles_path: Path,
    market_db_path: Path,
    queue_dir: Path,
    taxonomy: dict,
    run_date: str,
    store_factory,
) -> None:
    """7b deterministic save (incremental, preflight/postflight) + 7c queue CSV."""
    concepts_by_id = {c["concept_id"]: c["label"] for c in taxonomy.get("concepts", [])}
    profiles = _load_profiles(profiles_path)

    store = store_factory()
    # preflight: canonical CSV ⇔ DB lockstep before mutating
    csv_syms, db_syms = _read_csv_symbols(canonical_csv), _db_tag_symbols(store)
    if csv_syms != db_syms:
        res.error = (f"preflight lockstep broken: csv-only={sorted(csv_syms - db_syms)[:5]} "
                     f"db-only={sorted(db_syms - csv_syms)[:5]}")
        return

    if res._deterministic:
        _backup_sqlite(store.db_path, "pre-weekly-sync")
        db_rows = [_row_to_db(row) for row, _prof in res._deterministic]
        save_to_market_db(rows=db_rows, store=store, market_db_path=market_db_path)  # P1.1 keyword-only
        csv_rows = [
            _row_to_csv(row, dict(profiles.get(row["symbol"].upper()) or {}), "ok", None, concepts_by_id)
            for row, _prof in res._deterministic
        ]
        _append_csv_atomic(canonical_csv, csv_rows)
        _write_review_manifest(canonical_csv, _read_csv_symbols(canonical_csv))
        # postflight: re-verify lockstep
        if _read_csv_symbols(canonical_csv) != _db_tag_symbols(store):
            res.error = "postflight lockstep broken — backup retained, manual review"
            return

    # 7c: queued + failed both get a review artifact (P1.4 — no symbol left only in a counter)
    review_rows = [row for row, _prof in res._queue] + res._failed_rows
    if review_rows:
        review_profiles = {r["symbol"].upper(): dict(p) for r, p in res._queue}
        write_review_csv(
            rows=review_rows, csv_path=queue_dir / f"needs_review_{run_date}.csv",
            taxonomy=taxonomy, profiles=review_profiles,
        )


# ---- --reclassify: re-run classify over a run-1 review CSV (plan 2026-05-16) ----

def _classify_review_reason(
    *, source: str, confidence: float, needs_review: int
) -> str:
    """Bucket a classified row into a review_reason (hard / soft / ok).

    Same routing build_registry / write_review_csv apply inline; used here so
    --reclassify labels its re-rendered rows consistently.
    """
    if int(needs_review) == 1:
        return "hard_needs_review"
    if source in ("rule", "llm") and (
        float(confidence) < SOFT_REVIEW_CONFIDENCE_THRESHOLD
    ):
        return "soft_low_confidence"
    return "ok"


def _profile_for_reclassify(raw: dict, profiles: dict[str, dict]) -> dict:
    """Profile fed to classify() / prefill_one() during --reclassify.

    profiles.json is the SSOT run-1 itself classified from; the CSV's
    fmp_sector / fmp_industry / description / company_name columns are the
    fallback when a symbol is absent from (or blank in) the cache.
    """
    sym = (raw.get("symbol") or "").strip().upper()
    prof = dict(profiles.get(sym) or {})
    prof.setdefault("symbol", sym)
    if not prof.get("sector"):
        prof["sector"] = raw.get("fmp_sector", "")
    if not prof.get("industry"):
        prof["industry"] = raw.get("fmp_industry", "")
    if not prof.get("companyName"):
        prof["companyName"] = raw.get("company_name", "")
    if not prof.get("description"):
        prof["description"] = raw.get("description", "")
    return prof


def _render_classified_row(
    raw: dict, new_row: dict, concepts_by_id: dict[str, str]
) -> dict:
    """Overlay a fresh classify/LLM result onto a run-1 CSV row.

    Only classification columns change; metadata columns (company_name,
    description, market_cap_b, mcap_tier, fmp_sector, fmp_industry,
    boss_notes) are preserved from `raw` so a re-render never clears the
    company-DB market caps run-1 recorded (plan §3.4 / Boss P2-2).
    """
    out = dict(raw)
    l3_labels = [
        concepts_by_id.get(tid, "") for tid in (new_row.get("l3_themes") or [])
    ]
    out["l1"] = concepts_by_id.get(new_row.get("l1") or "", "")
    out["l2"] = concepts_by_id.get(new_row.get("l2") or "", "")
    out["l3_themes"] = ";".join(s for s in l3_labels if s)
    out["business_role"] = new_row.get("business_role", "")
    out["prefill_source"] = new_row.get("source", "")
    out["confidence"] = f"{float(new_row.get('confidence', 0.0)):.2f}"
    out["needs_review"] = str(int(new_row.get("needs_review", 0)))
    out["review_reason"] = _classify_review_reason(
        source=new_row.get("source", ""),
        confidence=float(new_row.get("confidence", 0.0)),
        needs_review=int(new_row.get("needs_review", 0)),
    )
    return out


def reclassify_csv(
    *,
    input_csv: Path,
    output_csv: Path,
    registry: ConceptRegistry,
    profiles: dict[str, dict],
    taxonomy: dict,
) -> dict:
    """--reclassify mode: re-run classify over a run-1 review CSV with the new
    industry_map, routing each row by its OLD prefill_source (plan §3.6).

    Per-row routing (old source → action):
        manual                              → passthrough (anchor wins always)
        rule  + new rule/manual             → overwrite with deterministic result
        rule  + new unclassified            → fresh LLM (prefill_one)
        llm   + industry ∈ whitelist        → overwrite with deterministic result
        llm   + non-whitelist, (l1,l2) drift→ passthrough + deterministic_conflict
        llm   + non-whitelist, consistent   → passthrough
        other (llm_failed/fallback/...)     → passthrough

    Passthrough rows keep every original CSV value (no label↔id round-trip).
    Writes `output_csv` + its manifest, where the manifest records ALL symbols
    (incl passthrough) so apply_reviewed_csv's coverage union does not miss them.
    Returns a stats dict.
    """
    with input_csv.open(encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        raw_rows = list(reader)

    concepts_by_id = {
        c["concept_id"]: c["label"] for c in taxonomy.get("concepts", [])
    }
    l1_label_to_id = {
        c["label"]: c["concept_id"]
        for c in taxonomy["concepts"] if c["level"] == 1
    }
    l2_label_to_id = {
        c["label"]: c["concept_id"]
        for c in taxonomy["concepts"] if c["level"] == 2
    }

    out_rows: list[dict] = []
    stats = {
        "total": len(raw_rows),
        "passthrough": 0,
        "overwrite_rule": 0,
        "overwrite_llm_whitelist": 0,
        "fresh_llm": 0,
        "deterministic_conflict": 0,
    }
    conflict_symbols: list[str] = []
    whitelist_symbols: list[str] = []

    for raw in raw_rows:
        sym = (raw.get("symbol") or "").strip().upper()
        old_source = (raw.get("prefill_source") or "").strip()
        profile = _profile_for_reclassify(raw, profiles)
        new = registry.classify(profile)
        new_source = new.get("source")

        if old_source == "manual":
            out_rows.append(dict(raw))
            stats["passthrough"] += 1
            continue

        if old_source == "rule":
            if new_source in ("manual", "rule"):
                out_rows.append(
                    _render_classified_row(raw, new, concepts_by_id)
                )
                stats["overwrite_rule"] += 1
            else:  # new unclassified — industry not in map → fresh LLM
                llm = prefill_one(
                    symbol=sym, profile=profile, taxonomy=taxonomy
                )
                llm_row = {
                    "l1": llm.l1, "l2": llm.l2,
                    "l3_themes": list(llm.l3_themes),
                    "business_role": llm.business_role,
                    "confidence": llm.confidence,
                    "source": llm.source,
                    "needs_review": llm.needs_review,
                }
                out_rows.append(
                    _render_classified_row(raw, llm_row, concepts_by_id)
                )
                stats["fresh_llm"] += 1
            continue

        if old_source == "llm":
            key = registry._industry_key(profile)
            if key in RECLASSIFY_OVERRIDE_WHITELIST and new_source in (
                "manual", "rule"
            ):
                out_rows.append(
                    _render_classified_row(raw, new, concepts_by_id)
                )
                stats["overwrite_llm_whitelist"] += 1
                whitelist_symbols.append(sym)
                continue
            # Non-whitelist: never auto-overwrite an LLM call. Flag a conflict
            # when the new deterministic map disagrees with the old (l1,l2);
            # otherwise keep the old row untouched.
            if new_source in ("manual", "rule"):
                old_pair = (
                    l1_label_to_id.get((raw.get("l1") or "").strip()),
                    l2_label_to_id.get((raw.get("l2") or "").strip()),
                )
                new_pair = (new.get("l1"), new.get("l2"))
                if old_pair != new_pair:
                    conflicted = dict(raw)
                    conflicted["review_reason"] = "deterministic_conflict"
                    out_rows.append(conflicted)
                    stats["deterministic_conflict"] += 1
                    conflict_symbols.append(sym)
                    continue
            out_rows.append(dict(raw))
            stats["passthrough"] += 1
            continue

        # llm_failed / llm_fallback / unclassified / unknown → passthrough
        out_rows.append(dict(raw))
        stats["passthrough"] += 1

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(
            fh, fieldnames=REVIEW_CSV_FIELDS, extrasaction="ignore"
        )
        w.writeheader()
        for r in out_rows:
            w.writerow({k: r.get(k, "") for k in REVIEW_CSV_FIELDS})

    _write_review_manifest(output_csv, [r["symbol"] for r in out_rows])

    stats["conflict_symbols"] = sorted(conflict_symbols)
    stats["whitelist_symbols"] = sorted(whitelist_symbols)
    return stats


# ---- Phase 6: save_to_market_db + WAL-safe backup + display_tags ----

def _build_display_tags(row: dict, concepts_by_id: dict[str, str]) -> str:
    """Build the 2- or 3-segment Chinese display string.

    L1_label / L2_label              (when no theme)
    L1_label / L2_label / L3_first   (theme present)

    First L3 only — additional themes are addressable via theme_ids but not
    visualized in this canonical string. Missing concept_ids are skipped so a
    partially-classified row still renders what it can.
    """
    parts: list[str] = []
    for cid in (row.get("primary_concept_id"), row.get("secondary_concept_id")):
        if cid and cid in concepts_by_id:
            parts.append(concepts_by_id[cid])
    theme_ids = row.get("theme_ids") or []
    if theme_ids and theme_ids[0] in concepts_by_id:
        parts.append(concepts_by_id[theme_ids[0]])
    return " / ".join(parts)


def _backup_sqlite(db_path: Path, label: str) -> Path | None:
    """WAL-safe SQLite snapshot using the official backup API.

    Why not shutil.copy2: market.db (and company.db) both run journal_mode=WAL.
    A plain file copy may miss in-flight transactions still in the -wal
    sidecar, leaving the backup logically incomplete. sqlite3.Connection.backup()
    coordinates with WAL and produces a consistent destination file regardless
    of pending writes. Returns the new backup path, or None when db_path missing.
    """
    if not db_path.exists():
        return None
    import sqlite3
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    backup_path = db_path.with_name(f"{db_path.name}.backup-{ts}-{label}")
    src = sqlite3.connect(str(db_path))
    dst = sqlite3.connect(str(backup_path))
    try:
        with dst:
            src.backup(dst)
    finally:
        src.close()
        dst.close()
    logger.info("WAL-safe backup %s -> %s", db_path, backup_path)
    return backup_path


def apply_reviewed_csv(
    *,
    store: MarketStore,
    registry: ConceptRegistry,
    csv_path: Path,
    extend_pool: set[str],
) -> int:
    """Phase 5+6 end-to-end save for the reviewed CSV.

    Order is load-bearing:
        1. Parse + validate the CSV against ``extend_pool ∪ manifest`` (no DB
           writes yet — fail fast on bad input or dropped rows). The manifest
           sidecar is the dry-run's own record of expected symbols, so a hand
           edit that deletes a row not present in extended pool still fails.
        2. WAL-safe backup of market.db (label=pre-rebuild). This is the
           rollback target if any later step fails.
        3. rebuild_concept_tree — wipes company_concept_tags + symbol_concept_edges
           as part of its FK-cascade requirement.
        4. save_to_market_db — re-upsert the parsed rows with fresh display_tags.

    Step 2 MUST precede step 3. If we backed up after rebuild, the backup would
    capture the already-cleared tags and recovery would be impossible.
    """
    effective_pool = _effective_extend_pool(csv_path, extend_pool)
    parsed_rows = read_reviewed_csv(
        csv_path, extend_pool=effective_pool, taxonomy=registry._taxonomy,
    )
    _backup_sqlite(store.db_path, "pre-rebuild")
    store.rebuild_concept_tree(registry.concepts)
    return save_to_market_db(
        rows=parsed_rows, store=store, market_db_path=store.db_path,
    )


def _effective_extend_pool(csv_path: Path, extend_pool: Iterable[str]) -> set[str]:
    """Union the caller-supplied extend_pool with the manifest sidecar (when
    present). The caller's pool is usually ``extended_universe`` — alone it
    can't catch a dropped watchlist or portfolio row whose symbol isn't in
    the extended universe.
    """
    base = {str(s).upper() for s in extend_pool}
    manifest = _load_review_manifest(csv_path)
    if manifest:
        return base | manifest
    return base


def save_to_market_db(
    *,
    rows: list[dict],
    store: MarketStore,
    market_db_path: Path,
) -> int:
    """Phase 6: upsert tags with rebuilt 3-segment display_tags.

    Builds display_tags by joining concepts.label for the referenced
    (L1, L2, L3_first) IDs. Boss-supplied display_tags (if any) are
    overwritten — DB labels are SSOT for display.

    Note: this function does NOT back up the DB. Backups belong at the
    destructive-mutation boundary (rebuild_concept_tree) and are taken by
    callers (apply_reviewed_csv, build_registry --save) BEFORE rebuild. Doing
    a backup here would either be redundant (caller already backed up) or
    too late (post-rebuild backup captures cleared state).
    """
    del market_db_path  # kept in signature for caller back-compat, unused here

    conn = store._get_conn()
    concepts_by_id = {
        r[0]: r[1] for r in conn.execute("SELECT concept_id, label FROM concepts")
    }
    for r in rows:
        r["display_tags"] = _build_display_tags(r, concepts_by_id)
    return store.upsert_company_concepts(rows)


# ---- CLI helpers ----

def _load_universe(path: Path) -> list[str]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return [str(s).upper() for s in data]
    if isinstance(data, dict):
        stocks = data.get("stocks")
        if isinstance(stocks, dict):
            return [str(s).upper() for s in stocks.keys()]
        if isinstance(data.get("symbols"), list):
            return [str(s).upper() for s in data["symbols"]]
    return []


def _load_profiles(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        return {str(k).upper(): v for k, v in data.items()}
    out: dict[str, dict] = {}
    for entry in data:
        sym = str(entry.get("symbol") or "").upper()
        if sym:
            out[sym] = entry
    return out


def _read_portfolio_holdings(company_db_path: Path | None = None) -> list[str]:
    """Return open-holding symbols from the company DB at ``company_db_path``.

    Critical: we instantiate ``CompanyStore(company_db_path)`` directly rather
    than calling ``get_store()``. The singleton honors its first-call db_path
    and silently returns the cached instance on subsequent calls with a
    different path — which would make ``--company-db`` a footgun (worktree
    runs would read main-workspace holdings only on cold start).
    """
    try:
        from terminal.company_store import CompanyStore
    except Exception as exc:
        logger.warning("terminal.company_store import failed: %s", exc)
        return []
    try:
        if company_db_path is not None and not company_db_path.exists():
            logger.warning("company.db missing at %s — portfolio empty", company_db_path)
            return []
        rows = CompanyStore(company_db_path).get_all_open_holdings()
    except Exception as exc:
        logger.warning("get_all_open_holdings failed: %s", exc)
        return []
    return [str(r["symbol"]).upper() for r in rows if r.get("symbol")]


def _read_broad_top(
    n: int = 100,
    dollar_volume_db_path: Path | None = None,
) -> list[str]:
    """Return today's top-N broad-universe symbols by dollar volume.

    ``dollar_volume_db_path=None`` falls back to ``src.data.dollar_volume``'s
    module-level default (the project's ``data/dollar_volume.db``). When the
    caller passes a path that doesn't exist yet, we skip the read entirely —
    ``get_latest_date`` would otherwise create an empty DB file at that path
    via ``init_db``-like side effects.
    """
    try:
        from src.data.dollar_volume import get_latest_date, get_rankings
    except Exception as exc:
        logger.warning("src.data.dollar_volume import failed: %s", exc)
        return []
    if dollar_volume_db_path is not None and not dollar_volume_db_path.exists():
        logger.warning(
            "dollar_volume DB missing at %s — broad_top empty", dollar_volume_db_path,
        )
        return []
    try:
        kwargs: dict = {}
        if dollar_volume_db_path is not None:
            kwargs["db_path"] = dollar_volume_db_path
        date = get_latest_date(**kwargs)
        if not date:
            logger.warning("dollar_volume DB has no rankings yet — broad_top empty")
            return []
        rankings = get_rankings(date=date, limit=n, **kwargs)
    except Exception as exc:
        logger.warning("dollar_volume get_rankings failed: %s", exc)
        return []
    return [str(r["symbol"]).upper() for r in rankings if r.get("symbol")]


def _maybe_load_env_file(data_root: Path | None) -> None:
    """When --data-root points at another workspace's data dir, try to load
    that workspace's .env so FMP_API_KEY etc. are visible without manual
    export. Only sets variables that aren't already in os.environ — explicit
    env always wins. Silently skips when no .env is present.
    """
    if data_root is None:
        return
    env_path = data_root.parent / ".env"
    if not env_path.exists():
        return
    import os
    try:
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except OSError as exc:
        logger.warning("Failed to read %s: %s", env_path, exc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", default="broad",
                        help="Universe source: 'broad' / 'extended' / path to JSON.")
    parser.add_argument("--symbols-only", action="store_true",
                        help="Classify ONLY --symbols; skip watchlist / "
                             "portfolio / broad_top union (narrow reclassify).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Compute coverage + write review CSV without writing tags.")
    parser.add_argument("--save", action="store_true",
                        help="Persist company tags after gate passes.")
    parser.add_argument("--force-save", action="store_true",
                        help="Bypass the layered gate.")
    parser.add_argument("--rebuild-display", action="store_true",
                        help="Recompute display_tags from current concepts.label.")
    parser.add_argument("--refresh-profiles", action="store_true",
                        help="Phase 2: refresh FMP profiles for extend pool (writes profiles.json), then exit.")
    parser.add_argument("--read-reviewed-csv", type=Path,
                        help="Phase 5: parse Boss-reviewed CSV, validate, then write to market.db (with --save).")
    parser.add_argument("--reclassify", type=Path, default=None,
                        help="Re-run classify over an existing review CSV with "
                             "the current industry_map (plan 2026-05-16 §3.6). "
                             "Reads the CSV, re-routes by old prefill_source, "
                             "writes a new CSV to --review-csv. Never touches "
                             "market.db. Requires --review-csv.")
    parser.add_argument("--validate-only", action="store_true",
                        help="Run Phase 5 validation, emit _rejected.csv + summary, do not save.")
    parser.add_argument(
        "--review-csv",
        type=Path,
        default=None,
        help="Output path for the Phase 4 review CSV (default: reports/concept_registry/needs_review_<date>.csv).",
    )
    parser.add_argument("--weekly-sync", action="store_true",
                        help="Sync registry to current extended_universe drift (A3): "
                             "deterministic auto-save, LLM queue, Telegram summary.")
    parser.add_argument("--canonical-csv", type=Path, default=None,
                        help="Canonical reviewed CSV (default: reports/concept_registry/reviewed_current.csv)")
    # ---- Path overrides — let worktree runs target main workspace data ----
    parser.add_argument("--data-root", type=Path, default=None,
                        help="Root for data files. Defaults to PROJECT_ROOT/data. "
                             "Equivalent to setting --profiles-path, "
                             "--extended-universe-path, --market-db, --company-db all at once.")
    parser.add_argument("--profiles-path", type=Path, default=None,
                        help="Override fundamental/profiles.json path.")
    parser.add_argument("--extended-universe-path", type=Path, default=None,
                        help="Override pool/extended_universe.json path.")
    parser.add_argument("--market-db", type=Path, default=None,
                        help="Override market.db path.")
    parser.add_argument("--company-db", type=Path, default=None,
                        help="Override company.db path.")
    parser.add_argument("--dollar-volume-db", type=Path, default=None,
                        help="Override dollar_volume.db path (defaults to <data-root>/dollar_volume.db).")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    # Resolve effective paths: --data-root provides defaults, then individual
    # overrides win over those. Module-level constants remain the floor.
    data_root = args.data_root if args.data_root else PROJECT_ROOT / "data"
    profiles_path = args.profiles_path or (data_root / "fundamental" / "profiles.json")
    extended_universe_path = args.extended_universe_path or (
        data_root / "pool" / "extended_universe.json"
    )
    market_db_path = args.market_db or (data_root / "market.db")
    company_db_path = args.company_db or (data_root / "company.db")
    dollar_volume_db_path = args.dollar_volume_db or (data_root / "dollar_volume.db")
    review_csv = args.review_csv or (
        PROJECT_ROOT / "reports" / "concept_registry"
        / f"needs_review_{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.csv"
    )

    # --data-root pointing at another workspace usually means the .env lives
    # next to that workspace too. Load it so FMP_API_KEY checks succeed without
    # Boss having to source/export it manually before each worktree run.
    _maybe_load_env_file(args.data_root)

    cfg_dir = PROJECT_ROOT / "config" / "concepts"
    registry = ConceptRegistry(
        taxonomy_path=cfg_dir / "concept_taxonomy_v2.json",
        watchlist_path=cfg_dir / "concept_watchlist.json",
    )

    # MarketStore.__init__ creates the .db file (mkdir parents + sqlite3.connect
    # + _init_db). Don't instantiate it for commands that have no business
    # touching market.db: --refresh-profiles (FMP only) and --validate-only
    # (CSV parsing only). Lazy-init keeps those paths side-effect-free.
    def _open_store() -> MarketStore:
        return MarketStore(market_db_path)

    if args.weekly_sync:
        import datetime as _dt
        canonical_csv = args.canonical_csv or (
            PROJECT_ROOT / "reports" / "concept_registry" / "reviewed_current.csv")
        taxonomy = json.loads(
            (cfg_dir / "concept_taxonomy_v2.json").read_text(encoding="utf-8"))
        res = weekly_sync(
            registry=registry, taxonomy=taxonomy, canonical_csv=canonical_csv,
            extended_universe_path=extended_universe_path, profiles_path=profiles_path,
            market_db_path=market_db_path, queue_dir=canonical_csv.parent,
            run_date=_dt.date.today().isoformat(),
            store_factory=_open_store, telegram_fn=send_message)
        print(res.summary_text())
        return 2 if res.error else 0

    if args.refresh_profiles:
        # Fail-fast guards: silent no-ops on either of these have caused
        # cron-time data loss before (profiles.json clobbered by an empty
        # universe; 401 storms because FMP_API_KEY rotated unnoticed).
        import os
        if not os.environ.get("FMP_API_KEY", "").strip():
            print(
                "FMP_API_KEY is empty — refusing to call FMP. "
                "Set it via .env or env var before --refresh-profiles.",
                file=sys.stderr,
            )
            return 2
        symbols = _load_universe(extended_universe_path)
        if not symbols:
            print(
                f"Extended universe is empty: {extended_universe_path}. "
                "Refusing to overwrite profiles.json with an empty cache.",
                file=sys.stderr,
            )
            return 2
        count = refresh_profiles(symbols, profiles_path=profiles_path)
        print(f"Refreshed {count} profiles -> {profiles_path}")
        return 0

    if args.rebuild_display:
        summary = rebuild_display_tags(store=_open_store(), registry=registry)
        for k, v in summary.items():
            print(f"{k}: {v}")
        return 0

    if args.reclassify:
        # Reads old CSV → re-classifies → writes new CSV. No validate, no save
        # (plan §3.7) — keep MarketStore unopened so the run has zero side
        # effects on data_root, exactly like --validate-only.
        if args.review_csv is None:
            print(
                "--reclassify requires --review-csv <output path>.",
                file=sys.stderr,
            )
            return 2
        stats = reclassify_csv(
            input_csv=args.reclassify,
            output_csv=review_csv,
            registry=registry,
            profiles=_load_profiles(profiles_path),
            taxonomy=registry._taxonomy,
        )
        print(f"reclassify: {stats['total']} rows -> {review_csv}")
        print(f"  passthrough            : {stats['passthrough']}")
        print(f"  overwrite (rule)       : {stats['overwrite_rule']}")
        print(f"  overwrite (llm/telecom): {stats['overwrite_llm_whitelist']}")
        print(f"  fresh LLM              : {stats['fresh_llm']}")
        print(f"  deterministic_conflict : {stats['deterministic_conflict']}")
        if stats["whitelist_symbols"]:
            print(f"  telecom overwritten    : "
                  f"{', '.join(stats['whitelist_symbols'])}")
        if stats["conflict_symbols"]:
            print(f"  conflict queue         : "
                  f"{', '.join(stats['conflict_symbols'])}")
        return 0

    if args.read_reviewed_csv:
        # The validate / dry-validate / save paths all reuse the manifest-aware
        # pool so a dropped priority row fails coverage regardless of mode.
        extend_pool = _effective_extend_pool(
            args.read_reviewed_csv,
            _load_universe(extended_universe_path),
        )
        if args.validate_only:
            # Validation never writes — keep MarketStore unopened so a
            # validate-only run has zero side effects on data_root.
            try:
                parsed_rows = read_reviewed_csv(
                    args.read_reviewed_csv,
                    extend_pool=extend_pool,
                    taxonomy=registry._taxonomy,
                    validate_only=True,
                )
            except CSVValidationError as exc:
                print(f"CSV VALIDATION FAILED:\n{exc}", file=sys.stderr)
                return 2
            print(f"validate_only: {len(parsed_rows)} rows parsed; "
                  f"see _rejected.csv + _rejected_summary.txt for issues")
            return 0
        if args.save:
            try:
                saved = apply_reviewed_csv(
                    store=_open_store(), registry=registry,
                    csv_path=args.read_reviewed_csv,
                    extend_pool=extend_pool,
                )
            except CSVValidationError as exc:
                print(f"CSV VALIDATION FAILED:\n{exc}", file=sys.stderr)
                return 2
            print(f"saved {saved} reviewed rows")
            return 0
        try:
            parsed_rows = read_reviewed_csv(
                args.read_reviewed_csv,
                extend_pool=extend_pool,
                taxonomy=registry._taxonomy,
            )
        except CSVValidationError as exc:
            print(f"CSV VALIDATION FAILED:\n{exc}", file=sys.stderr)
            return 2
        print(f"validated {len(parsed_rows)} rows (use --save to persist)")
        return 0

    if args.symbols == "broad":
        universe = _load_universe(data_root / "scans" / "broad_universe.json")
    elif args.symbols == "extended":
        universe = _load_universe(extended_universe_path)
    else:
        universe = _load_universe(Path(args.symbols))

    profiles = _load_profiles(profiles_path)
    portfolio = _read_portfolio_holdings(company_db_path)
    broad_top = _read_broad_top(100, dollar_volume_db_path)
    market_caps = _load_market_caps_from_company_db(company_db_path)

    save = args.save and not args.dry_run

    try:
        result = build_registry(
            store=_open_store(), registry=registry,
            universe_symbols=universe,
            profiles=profiles,
            portfolio_holdings=portfolio,
            broad_top_symbols=broad_top,
            review_csv_path=review_csv,
            save=save,
            force_save=args.force_save,
            market_caps=market_caps,
            symbols_only=args.symbols_only,
        )
    except BuildGateError as exc:
        print(f"GATE FAILED: {exc}", file=sys.stderr)
        return 2

    # Always emit taxonomy_reference.csv alongside the review CSV
    _write_taxonomy_reference_csv(
        registry._taxonomy,
        review_csv.parent / "taxonomy_reference.csv",
    )

    print(result.as_summary())
    return 0


if __name__ == "__main__":
    sys.exit(main())
