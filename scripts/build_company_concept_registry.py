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
