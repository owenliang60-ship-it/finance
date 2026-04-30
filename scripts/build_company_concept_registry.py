"""Build company concept registry from taxonomy + overrides + profiles.

Layered gate semantics:
    priority_list = portfolio holdings ∪ watchlist ∪ broad_top_100_by_30d_ADV
        (override seed is NOT auto-added; overrides only apply to symbols
        that are in scope from one of the three sources above)
    save requires priority_coverage == 100% AND tail_needs_review_rate < 30%
    --force-save bypasses the gate (logs forced_save=true)

Review CSV contents (two queues, gate-independent):
    hard_needs_review   — fallback rows (needs_review=1); these block the gate
    soft_low_confidence — rule/legacy rows with confidence < 0.7; these do
                          NOT block the gate but surface keyword-match risk
                          to Boss for spot-check (rule confidence is 0.6 by
                          construction, so all rule rows currently land here).

Execution order is mandatory:
    1) upsert concepts (FK target)
    2) upsert concept_themes
    3) build company rows (manual → rule → legacy → fallback)
    4) write review CSV (hard + soft)
    5) gate check on hard rows only; only on pass (or --force-save) upsert
       company tags

CLI:
    python scripts/build_company_concept_registry.py --symbols broad --dry-run
    python scripts/build_company_concept_registry.py --symbols broad --save
    python scripts/build_company_concept_registry.py --symbols broad --save --force-save
    python scripts/build_company_concept_registry.py --rebuild-display
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
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

logger = logging.getLogger(__name__)

GATE_PRIORITY_COVERAGE = 1.0
GATE_TAIL_NEEDS_REVIEW_MAX = 0.30
# Rule confidence is 0.6 and legacy bucket fallback is 0.4 by construction.
# Anything below this threshold from a non-manual source goes into the soft
# review queue so Boss can spot-check keyword-driven decisions even though
# they don't trip the gate.
SOFT_REVIEW_CONFIDENCE_THRESHOLD = 0.7


class BuildGateError(RuntimeError):
    """Raised when --save fails the layered gate without --force-save."""


@dataclass
class BuildResult:
    symbols: int
    watchlist_added: int
    priority_list_size: int
    priority_coverage: float
    tagged: int
    manual: int
    rule: int
    fallback: int
    legacy: int
    needs_review: int           # hard queue: source=fallback (gate denominator)
    soft_review: int            # soft queue: rule/legacy with confidence < 0.7
    tail_needs_review_rate: float
    review_csv: str | None
    saved: bool
    forced_save: bool

    def as_summary(self) -> str:
        forced_marker = " (forced)" if self.forced_save else ""
        lines = [
            "Company Concept Registry",
            f"symbols: {self.symbols}",
            f"watchlist_added: {self.watchlist_added}",
            f"priority_list_size: {self.priority_list_size}",
            f"priority_coverage: {self.priority_coverage * 100:.1f}%",
            f"tagged: {self.tagged}",
            f"manual: {self.manual}",
            f"rule: {self.rule}",
            f"fallback: {self.fallback}",
            f"legacy: {self.legacy}",
            f"needs_review (hard): {self.needs_review}",
            f"soft_review (low_conf): {self.soft_review}",
            f"tail_needs_review_rate: {self.tail_needs_review_rate * 100:.1f}%",
            f"review_csv: {self.review_csv or '-'}",
            f"saved: {'yes' if self.saved else 'no'}{forced_marker}",
        ]
        return "\n".join(lines)


def _persist_taxonomy(store: MarketStore, registry: ConceptRegistry) -> None:
    store.upsert_concepts(registry.concepts)
    store.upsert_concept_themes(registry.themes)


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
) -> BuildResult:
    """Run the full build pipeline. Concepts/themes + company tags are persisted
    together iff the gate passes (or --force-save); dry-run leaves the DB untouched.
    Resolution still uses the in-memory taxonomy from the registry, so dry-run
    output matches what a save would produce."""
    # Step 3: compose full universe.
    # priority symbols (portfolio + watchlist + broad_top) MUST be tagged so they
    # appear in the gate denominator. Otherwise priority_in_universe silently
    # shrinks and a coverage of "100%" can hide missing names.
    # Override seed is NOT auto-expanded — overrides apply only when a symbol
    # is already in scope (could be from any of the inputs above).
    portfolio_set = {s.upper() for s in (portfolio_holdings or [])}
    broad_top_set = {s.upper() for s in (broad_top_symbols or [])}

    seen: set[str] = set()
    full_universe: list[str] = []
    for src in (universe_symbols, registry.watchlist_symbols,
                portfolio_set, broad_top_set):
        for s in src:
            up = s.upper()
            if up not in seen:
                seen.add(up)
                full_universe.append(up)

    universe_set = {s.upper() for s in universe_symbols}
    watchlist_added = len(registry.watchlist_symbols - universe_set)
    priority_added = len(
        (registry.watchlist_symbols | portfolio_set | broad_top_set) - universe_set
    )

    # Step 4: classify each symbol.
    rows: list[dict] = []
    for sym in full_universe:
        profile = dict(profiles.get(sym) or {})
        profile.setdefault("symbol", sym)
        rows.append(registry.classify(profile))

    # Step 5: priority_list = portfolio ∪ watchlist ∪ broad_top.
    # Override seed is NOT auto-added — overrides only apply to symbols
    # already in scope from one of the three sources above. This matches
    # ConceptRegistry.priority_list() and the file-level docstring contract.
    priority = registry.priority_list(
        broad_top_symbols=list(broad_top_symbols or []),
        portfolio_holdings=list(portfolio_holdings or []),
    )

    # Step 6+7: coverage metrics.
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

    # Step 8: review CSV (always written, both dry-run and save paths).
    # Two queues: hard (needs_review=1, blocks gate) and soft (low-confidence
    # rule/legacy, surfaces keyword-match risk to Boss but does NOT block).
    review_csv_path.parent.mkdir(parents=True, exist_ok=True)
    needs_review_rows = [r for r in rows if r["needs_review"] == 1]
    soft_review_rows = [
        r for r in rows
        if r["needs_review"] == 0
        and r["source"] in ("rule", "legacy")
        and r["confidence"] < SOFT_REVIEW_CONFIDENCE_THRESHOLD
    ]
    csv_fields = [
        "review_reason",
        "symbol", "primary_concept_id", "secondary_concept_id",
        "tertiary_concept_id", "display_tags", "business_role",
        "confidence", "source", "evidence",
    ]
    with review_csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=csv_fields)
        w.writeheader()
        for r in needs_review_rows:
            w.writerow({**{k: r.get(k, "") for k in csv_fields},
                        "review_reason": "hard_needs_review"})
        for r in soft_review_rows:
            w.writerow({**{k: r.get(k, "") for k in csv_fields},
                        "review_reason": "soft_low_confidence"})

    # Source counters.
    by_source: dict[str, int] = {}
    for r in rows:
        by_source[r["source"]] = by_source.get(r["source"], 0) + 1

    # Step 9: gate decision.
    gate_failed: list[str] = []
    # Empty broad_top means the dollar_volume DB has no rankings yet, which
    # silently shrinks the priority denominator to portfolio + watchlist only.
    # Treat it as a gate failure so --force-save leaves an audit trail in
    # BuildResult.forced_save instead of printing a vanilla `saved: yes`.
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
        # Order matters: concepts + themes are FK targets for company tags.
        _persist_taxonomy(store, registry)
        store.upsert_company_concepts(rows)

    return BuildResult(
        symbols=len(rows),
        watchlist_added=watchlist_added,
        priority_list_size=len(priority),
        priority_coverage=priority_coverage,
        tagged=len(rows),
        manual=by_source.get("manual", 0),
        rule=by_source.get("rule", 0),
        fallback=by_source.get("fallback", 0),
        legacy=by_source.get("legacy", 0),
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
    """Recompute display_tags for non-manual-with-display rows; preserve manual strings.

    Manual override rows whose override JSON carries an explicit display_tags
    field are skipped (those are authoritative). Rule/fallback/legacy rows and
    manual rows without a hand-written display_tags are recomputed from current
    concepts.label + active concept_themes.label.
    """
    conn = store._get_conn()
    concepts_by_id = {
        row[0]: row[1]
        for row in conn.execute("SELECT concept_id, label FROM concepts").fetchall()
    }
    themes_by_id = {
        row[0]: row[1]
        for row in conn.execute("SELECT theme_id, label FROM concept_themes").fetchall()
    }

    overrides_with_display = {
        sym for sym, cfg in registry._symbol_overrides.items()
        if cfg.get("display_tags")
    }

    preserved = 0
    rebuilt = 0
    updates: list[tuple[str, str]] = []
    for row in conn.execute("SELECT * FROM company_concept_tags").fetchall():
        symbol = row["symbol"]
        if row["source"] == "manual" and symbol in overrides_with_display:
            preserved += 1
            continue
        labels: list[str] = []
        for cid in (row["primary_concept_id"], row["secondary_concept_id"],
                    row["tertiary_concept_id"]):
            if cid and cid in concepts_by_id:
                labels.append(concepts_by_id[cid])
        try:
            theme_ids = json.loads(row["theme_ids"] or "[]")
        except (TypeError, ValueError):
            theme_ids = []
        for tid in theme_ids:
            if tid in themes_by_id:
                labels.append(themes_by_id[tid])
        new_display = " / ".join(labels)
        if new_display != row["display_tags"]:
            updates.append((new_display, symbol))
        rebuilt += 1

    with conn:
        for new_display, symbol in updates:
            conn.execute(
                "UPDATE company_concept_tags SET display_tags = ? WHERE symbol = ?",
                (new_display, symbol),
            )

    return {
        "manual_display_tags_preserved": preserved,
        "rebuilt": rebuilt,
        "updated": len(updates),
    }


# ---- CLI helpers ----

def _load_universe(path: Path) -> list[str]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return [str(s).upper() for s in data]
    return [str(s).upper() for s in (data.get("symbols") or [])]


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
    """Open positions from company.db. Public API is `get_store().get_all_open_holdings()`."""
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
    """Top N broad-universe symbols by trailing dollar volume.

    `dollar_volume.get_rankings(date, limit)` requires a date; we resolve the
    latest snapshot via `get_latest_date()`. If the dollar_volume DB is empty
    or absent (local dev) we return [] — the build script then runs without
    a broad_top contribution and the gate fails safe rather than silently.
    """
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
                        help="Universe source: 'broad' (default) or path to JSON.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Compute coverage + write review CSV without writing tags.")
    parser.add_argument("--save", action="store_true",
                        help="Persist company tags after gate passes.")
    parser.add_argument("--force-save", action="store_true",
                        help="Bypass the layered gate.")
    parser.add_argument("--rebuild-display", action="store_true",
                        help="Recompute display_tags for non-manual rows.")
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
        taxonomy_path=cfg_dir / "taxonomy.json",
        themes_path=cfg_dir / "concept_themes.json",
        overrides_path=cfg_dir / "company_concept_overrides.json",
        watchlist_path=cfg_dir / "concept_watchlist.json",
    )

    if args.rebuild_display:
        summary = rebuild_display_tags(store=store, registry=registry)
        for k, v in summary.items():
            print(f"{k}: {v}")
        return 0

    if args.symbols == "broad":
        universe = _load_universe(PROJECT_ROOT / "data" / "scans" / "broad_universe.json")
    else:
        universe = _load_universe(Path(args.symbols))

    profiles = _load_profiles(PROJECT_ROOT / "data" / "fundamental" / "profiles.json")
    portfolio = _read_portfolio_holdings()
    broad_top = _read_broad_top(100)

    save = args.save and not args.dry_run
    # broad_top empty is enforced inside build_registry as a gate_failed item,
    # so --force-save leaves a proper audit trail in BuildResult.forced_save.

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
        )
    except BuildGateError as exc:
        print(f"GATE FAILED: {exc}", file=sys.stderr)
        return 2

    print(result.as_summary())
    return 0


if __name__ == "__main__":
    sys.exit(main())
