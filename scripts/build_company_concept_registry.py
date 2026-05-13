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
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data.market_store import MarketStore  # noqa: E402
from terminal.company_concepts import ConceptRegistry  # noqa: E402
from terminal.llm_concept_prefill import LLMResult, prefill_one  # noqa: E402

logger = logging.getLogger(__name__)

GATE_PRIORITY_COVERAGE = 1.0
GATE_TAIL_NEEDS_REVIEW_MAX = 0.30
SOFT_REVIEW_CONFIDENCE_THRESHOLD = 0.7

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
    """Phase 2: pull FMP /profile/<symbol> and write to JSON keyed by symbol.

    Output schema matches the existing _load_profiles consumer:
        {"AAPL": {"symbol": "AAPL", "companyName": "...", "sector": "...",
                  "industry": "...", "description": "...", ...}, ...}

    Backs up existing JSON first. Rate-limit handled by FMP client (2s).
    Does NOT touch company.db — market_cap continues to flow through the
    existing data pipeline.
    """
    _backup_file(profiles_path, "preprofiles")
    profiles_path.parent.mkdir(parents=True, exist_ok=True)
    out: dict[str, dict] = {}
    for sym in symbols:
        try:
            profile = _fetch_fmp_profile(sym)
        except Exception as exc:  # noqa: BLE001
            logger.warning("FMP profile fetch failed for %s: %s", sym, exc)
            continue
        if profile:
            out[sym.upper()] = profile
    profiles_path.write_text(
        json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    logger.info("Wrote %d profiles -> %s", len(out), profiles_path)
    return len(out)


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
) -> BuildResult:
    """Run the v2 build pipeline. Concepts tree + company tags persist together
    iff gate passes (or --force-save). Dry-run leaves the DB untouched.
    """
    portfolio_set = {s.upper() for s in (portfolio_holdings or [])}
    broad_top_set = {s.upper() for s in (broad_top_symbols or [])}
    market_caps = {k.upper(): v for k, v in (market_caps or {}).items()}

    seen: set[str] = set()
    full_universe: list[str] = []
    for src in (universe_symbols, registry.watchlist_symbols,
                portfolio_set, broad_top_set):
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
        if row["needs_review"] == 1:
            csv_rows.append(
                _row_to_csv(row, profile, "hard_needs_review", cap_usd, concepts_by_id)
            )
        elif (
            row["source"] in ("rule", "llm")
            and row["confidence"] < SOFT_REVIEW_CONFIDENCE_THRESHOLD
        ):
            csv_rows.append(
                _row_to_csv(row, profile, "soft_low_confidence", cap_usd, concepts_by_id)
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
    with review_csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=REVIEW_CSV_FIELDS)
        w.writeheader()
        for r in csv_rows:
            w.writerow(r)

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
        6. l2 not in 60 L2 set → per-row
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
            row_errors.append(f"l2 '{l2_label}' not in 60 L2")
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


def save_to_market_db(
    *,
    rows: list[dict],
    store: MarketStore,
    market_db_path: Path,
) -> int:
    """Phase 6: WAL-safe backup + upsert with rebuilt 3-segment display_tags.

    Builds the display_tags by joining the concepts.label values for the
    referenced (L1, L2, L3_first) IDs. Boss-supplied display_tags (if any) are
    overwritten — DB labels are SSOT for display.
    """
    _backup_sqlite(market_db_path, "phase6")

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


def _read_portfolio_holdings() -> list[str]:
    try:
        from terminal.company_store import get_store as _get_company_store
    except Exception as exc:
        logger.warning("terminal.company_store import failed: %s", exc)
        return []
    try:
        rows = _get_company_store().get_all_open_holdings()
    except Exception as exc:
        logger.warning("get_all_open_holdings failed: %s", exc)
        return []
    return [str(r["symbol"]).upper() for r in rows if r.get("symbol")]


def _read_broad_top(n: int = 100) -> list[str]:
    try:
        from src.data.dollar_volume import get_latest_date, get_rankings
    except Exception as exc:
        logger.warning("src.data.dollar_volume import failed: %s", exc)
        return []
    try:
        date = get_latest_date()
        if not date:
            logger.warning("dollar_volume DB has no rankings yet — broad_top empty")
            return []
        rankings = get_rankings(date=date, limit=n)
    except Exception as exc:
        logger.warning("dollar_volume get_rankings failed: %s", exc)
        return []
    return [str(r["symbol"]).upper() for r in rankings if r.get("symbol")]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", default="broad",
                        help="Universe source: 'broad' / 'extended' / path to JSON.")
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
    parser.add_argument("--validate-only", action="store_true",
                        help="Run Phase 5 validation, emit _rejected.csv + summary, do not save.")
    parser.add_argument(
        "--review-csv",
        type=Path,
        default=PROJECT_ROOT / "reports" / "concept_registry"
        / f"needs_review_{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.csv",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    store = MarketStore()
    cfg_dir = PROJECT_ROOT / "config" / "concepts"
    registry = ConceptRegistry(
        taxonomy_path=cfg_dir / "concept_taxonomy_v2.json",
        watchlist_path=cfg_dir / "concept_watchlist.json",
    )

    if args.refresh_profiles:
        symbols = _load_universe(EXTENDED_UNIVERSE_PATH)
        count = refresh_profiles(symbols)
        print(f"Refreshed {count} profiles -> {PROFILES_PATH}")
        return 0

    if args.rebuild_display:
        summary = rebuild_display_tags(store=store, registry=registry)
        for k, v in summary.items():
            print(f"{k}: {v}")
        return 0

    if args.read_reviewed_csv:
        extend_pool = set(_load_universe(EXTENDED_UNIVERSE_PATH))
        try:
            parsed_rows = read_reviewed_csv(
                args.read_reviewed_csv,
                extend_pool=extend_pool,
                taxonomy=registry._taxonomy,
                validate_only=args.validate_only,
            )
        except CSVValidationError as exc:
            print(f"CSV VALIDATION FAILED:\n{exc}", file=sys.stderr)
            return 2
        if args.validate_only:
            print(f"validate_only: {len(parsed_rows)} rows parsed; "
                  f"see _rejected.csv + _rejected_summary.txt for issues")
            return 0
        if args.save:
            # Phase 6 save path is added by Task 8 (save_to_market_db).
            from scripts.build_company_concept_registry import save_to_market_db  # noqa: E402
            save_to_market_db(rows=parsed_rows, store=store, market_db_path=store.db_path)
            print(f"saved {len(parsed_rows)} reviewed rows")
        else:
            print(f"validated {len(parsed_rows)} rows (use --save to persist)")
        return 0

    if args.symbols == "broad":
        universe = _load_universe(PROJECT_ROOT / "data" / "scans" / "broad_universe.json")
    elif args.symbols == "extended":
        universe = _load_universe(PROJECT_ROOT / "data" / "pool" / "extended_universe.json")
    else:
        universe = _load_universe(Path(args.symbols))

    profiles = _load_profiles(PROFILES_PATH)
    portfolio = _read_portfolio_holdings()
    broad_top = _read_broad_top(100)
    market_caps = _load_market_caps_from_company_db()

    save = args.save and not args.dry_run

    try:
        result = build_registry(
            store=store, registry=registry,
            universe_symbols=universe,
            profiles=profiles,
            portfolio_holdings=portfolio,
            broad_top_symbols=broad_top,
            review_csv_path=args.review_csv,
            save=save,
            force_save=args.force_save,
            market_caps=market_caps,
        )
    except BuildGateError as exc:
        print(f"GATE FAILED: {exc}", file=sys.stderr)
        return 2

    # Always emit taxonomy_reference.csv alongside the review CSV
    _write_taxonomy_reference_csv(
        registry._taxonomy,
        args.review_csv.parent / "taxonomy_reference.csv",
    )

    print(result.as_summary())
    return 0


if __name__ == "__main__":
    sys.exit(main())
