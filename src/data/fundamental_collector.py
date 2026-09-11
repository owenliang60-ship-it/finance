"""Shared fundamentals collection kernel — the single write path (P1-3/P1-4).

Backfill (T10), the events line (T11), reconcile (T12) and `--scope core` all
collect through `collect_fundamentals_for_symbol`. One implementation means
one set of atomicity, coverage and vintage semantics; a second write path
would silently reintroduce the holes this kernel exists to close.

Atomic boundary (P1-4) — per DATASET, not per symbol:

    ONE transaction = current-table upsert
                    + fundamental_vintage append (income/balance/cashflow only)
                    + coverage_status upsert
                    + job_writer(...) if provided

Anything raising inside that block rolls the whole dataset back; the kernel
then records `fetch_failed` coverage (and the manifest job) in a NEW
transaction, because a failure that leaves no trace is worse than the failure.
Datasets are independent: a symbol can come back {income: ok, balance:
fetch_failed, ...} and only the failed ones need re-collecting.

Two things this kernel deliberately does NOT do:
  - never zero-fill: `provider_empty` writes no rows at all, only a coverage
    row with a TTL, so "the provider has nothing" stays distinguishable from
    "we fetched zeros";
  - never write `data/fundamental/profiles.json` (R2-P2-3). `company_profile`
    is the SSOT; `rebuild_profiles_json` refreshes the legacy mirror once at
    the end of a batch, merging the table over whatever the file already holds.

observed_at normalization: callers hand in either a pure date ("2026-08-24")
or a full timestamp. `record_vintage_in_conn` is strict on the write side and
rejects pure dates (they are not lexicographically comparable against
same-day revisions), so a pure date is normalized here — deterministically —
to "<date>T00:00:00Z" before any vintage/profile write. Read-side
`known_as_of` with that same pure date uses an exclusive next-day bound, so a
normalized row is still visible "as of" the day it was collected.

Consequence worth knowing before you pick an `observed_at`: because the
normalization is deterministic, two collections of the same symbol on the same
pure DATE share a vintage PK. Identical content is skipped (change-only
append), but CHANGED content collides and surfaces as `fetch_failed` rather
than clobbering append-only history. Runners that may touch a symbol twice in
one day (a backfill and a reconcile --repair, say) should pass a full UTC
timestamp, which is distinct per run.
"""
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.data.market_store import (
    COLLECTION_DATASET_TABLES,
    MarketStore,
    _is_pure_date,
)

logger = logging.getLogger(__name__)

try:
    from config.settings import FUNDAMENTAL_DIR as _FUNDAMENTAL_DIR
except ImportError:  # pragma: no cover - mirrors market_store's path fallback
    _FUNDAMENTAL_DIR = Path(__file__).parent.parent.parent / "data" / "fundamental"

# Same file `src/data/fundamental_fetcher.PROFILES_FILE` points at; resolved
# from config rather than imported from that module to avoid dragging the HTTP
# client and tool registry into every kernel import.
DEFAULT_PROFILES_PATH = _FUNDAMENTAL_DIR / "profiles.json"

# Collection order. `profile` first: it is the cheapest call and its identity
# fields are what every later interpretation of the numbers hangs off.
DATASETS = ("profile", "income", "balance", "cashflow", "ratios")

# dataset key -> current table. Also the `coverage_status.dataset` value
# (that column holds TABLE names; the manifest's `dataset` column holds the
# DATASET KEY — see COLLECTION_DATASET_TABLES's note in market_store).
DATASET_TABLES = COLLECTION_DATASET_TABLES

# CONTROLLER RULING #10: statements pull `limit_quarters`; ratios pull 4 to
# preserve current-table parity with the legacy `get_ratios(limit=4)` default;
# the profile endpoint takes no limit at all.
RATIOS_LIMIT = 4

# Routine collection is always "the latest thing the provider knows", including
# deep/historical pulls — those are approximate PIT by construction (T10).
VINTAGE_QUALITY = "latest_known"

_DETAIL_MAX_CHARS = 200

assert set(DATASETS) == set(DATASET_TABLES), "DATASETS out of sync with table map"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_observed_at(observed_at: str) -> str:
    """Pure date -> "<date>T00:00:00Z"; full timestamps pass through."""
    if not observed_at or not isinstance(observed_at, str):
        raise ValueError(f"observed_at must be a non-empty string, got {observed_at!r}")
    if _is_pure_date(observed_at):
        return observed_at + "T00:00:00Z"
    return observed_at


def _safe_detail(value: Any) -> str:
    """Short, key-free diagnostic string for coverage.detail / job.last_error.

    Reuses `fmp_client._sanitize_log_text` (imported lazily to keep this
    module importable without the HTTP client) rather than re-deriving the
    apikey mask: a third-party exception can carry the request URL, and that
    URL carries the key.
    """
    text = str(value)
    try:
        from src.data.fmp_client import _sanitize_log_text
        text = _sanitize_log_text(text)
    except Exception:  # pragma: no cover - defensive: never fail while failing
        pass
    return text[:_DETAIL_MAX_CHARS]


def _fetch_dataset(client: Any, dataset: str, symbol: str,
                   limit_quarters: int) -> Tuple[List[Dict], str]:
    """One provider call, with the per-dataset limit of RULING #10."""
    if dataset == "profile":
        return client.get_dataset_with_status("profile", symbol)
    limit = RATIOS_LIMIT if dataset == "ratios" else limit_quarters
    return client.get_dataset_with_status(dataset, symbol, limit=limit)


def _record_failure(store: MarketStore, symbol: str, dataset: str, table: str,
                    detail: str, job_writer: Optional[Callable]) -> None:
    """Record `fetch_failed` in a NEW transaction (the dataset's own
    transaction is already rolled back at this point).

    If even this write fails the DB is in real trouble: log it loudly and
    return, so the caller still gets its per-dataset status dict and the
    remaining datasets still get their chance. The manifest job stays
    non-terminal in that case, so the work is retried rather than counted done.
    """
    try:
        with store.transaction() as conn:
            store._upsert_coverage_status_in_conn(conn, [{
                "symbol": symbol,
                "dataset": table,
                "status": "fetch_failed",
                "detail": detail,
            }])
            if job_writer is not None:
                job_writer(conn, dataset, "fetch_failed", detail=detail)
    except Exception:
        logger.error(
            "could not record fetch_failed for %s/%s — coverage and manifest "
            "will under-report this failure", symbol, dataset, exc_info=True)


def _collect_one_dataset(symbol: str, dataset: str, *, client: Any,
                         store: MarketStore, limit_quarters: int,
                         observed_at: str,
                         job_writer: Optional[Callable]) -> str:
    """Fetch + atomically write one dataset. Returns its status."""
    table = DATASET_TABLES[dataset]

    # Note `except Exception`, not BaseException: a KeyboardInterrupt mid-run
    # must propagate so the runner's own cleanup (T10 step 5) can release
    # claimed jobs instead of this loop swallowing it as a dataset failure.
    try:
        rows, status = _fetch_dataset(client, dataset, symbol, limit_quarters)
    except Exception as exc:
        detail = _safe_detail(exc)
        logger.warning("fetch raised for %s/%s: %s", symbol, dataset, detail)
        _record_failure(store, symbol, dataset, table, detail, job_writer)
        return "fetch_failed"

    if status not in ("ok", "provider_empty"):
        detail = ("provider fetch failed" if status == "fetch_failed"
                  else f"unexpected provider status: {status!r}")
        _record_failure(store, symbol, dataset, table, detail, job_writer)
        return "fetch_failed"

    # An "ok" with no payload is an empty result, whatever the client called
    # it — treat it as provider_empty rather than writing a hollow `ok`.
    write_status = "provider_empty" if not rows else status

    try:
        with store.transaction() as conn:
            if write_status == "ok":
                written = store.write_symbol_dataset_in_conn(
                    conn, symbol, dataset, rows,
                    observed_at=observed_at, quality=VINTAGE_QUALITY,
                    updated_at=observed_at)
                if written["rows"] == 0:
                    # Provider sent rows but none were writable (e.g. every
                    # row missing its fiscal date). Committing coverage `ok`
                    # over an empty table would be a silent hole, so fail the
                    # dataset and let the retry timer own it.
                    raise ValueError(
                        f"provider returned {len(rows)} row(s) for {dataset} but "
                        f"none were writable (missing fiscal date?)")
            store._upsert_coverage_status_in_conn(conn, [{
                "symbol": symbol,
                "dataset": table,
                "status": write_status,
            }])
            if job_writer is not None:
                job_writer(conn, dataset, write_status, detail=None)
    except Exception as exc:
        logger.warning("write rolled back for %s/%s: %s", symbol, dataset,
                       _safe_detail(exc))
        _record_failure(store, symbol, dataset, table, _safe_detail(exc), job_writer)
        return "fetch_failed"

    return write_status


def collect_fundamentals_for_symbol(symbol: str, *, client: Any, store: MarketStore,
                                    limit_quarters: int = 8, observed_at: str,
                                    job_writer: Optional[Callable] = None,
                                    dataset_keys: Optional[List[str]] = None,
                                    ) -> Dict[str, str]:
    """Collect the requested datasets for one symbol (all five by default).

    THE fundamentals write path.

    Args:
        symbol: ticker; upper-cased before any write.
        client: anything exposing T4's
            `get_dataset_with_status(kind, symbol, limit=...) -> (rows, status)`.
        store: `MarketStore` owning the transaction boundary.
        limit_quarters: quarters requested for income/balance/cashflow
            (ratios always 4, profile unlimited — RULING #10).
        observed_at: when this collection was observed. A pure date is
            normalized to "<date>T00:00:00Z" (see module docstring).
        job_writer: optional manifest hook, called INSIDE the dataset's
            transaction as `job_writer(conn, dataset, status, detail=None)`
            with the DATASET KEY (not the table name). It takes `conn` so the
            manifest commits or rolls back with the data — a writer opening
            its own transaction would break that guarantee. T10 binds
            run_id/symbol and maps status via `JOB_STATUS_MAP` in a closure.
            On rollback it is called a second time, in the recovery
            transaction, with `fetch_failed`.
        dataset_keys: optional subset of DATASETS. Backfill resume passes only
            the jobs it claimed; routine/event/reconcile callers omit it and
            retain the full five-dataset collection.

    Returns:
        {dataset: status} for every selected dataset, status ∈
        {ok, provider_empty, fetch_failed}. Cross-dataset atomicity is
        explicitly NOT provided: partial states are the point — resume only
        re-collects what failed.
    """
    sym = symbol.upper()
    observed_ts = _normalize_observed_at(observed_at)

    if dataset_keys is None:
        selected = DATASETS
    else:
        requested = set(dataset_keys)
        unknown = requested - set(DATASETS)
        if unknown:
            raise ValueError("unknown dataset key(s): {}".format(
                ", ".join(sorted(unknown))))
        selected = tuple(dataset for dataset in DATASETS if dataset in requested)

    statuses: Dict[str, str] = {}
    for dataset in selected:
        statuses[dataset] = _collect_one_dataset(
            sym, dataset, client=client, store=store,
            limit_quarters=limit_quarters, observed_at=observed_ts,
            job_writer=job_writer)
    return statuses


# ---------------------------------------------------------------------------
# Legacy mirror
# ---------------------------------------------------------------------------

def _load_existing_profiles(path: Path) -> Dict[str, Any]:
    """Existing mirror entries, `_meta` stripped. Unreadable/malformed file ->
    empty dict plus a warning (same tolerance as
    `build_company_concept_registry.refresh_profiles`)."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8")) or {}
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("existing %s unreadable (%s); starting from an empty mirror",
                       path, exc)
        return {}
    if not isinstance(data, dict):
        logger.warning("existing %s is not a JSON object; starting from an empty mirror",
                       path)
        return {}
    return {k: v for k, v in data.items() if k != "_meta"}


def rebuild_profiles_json(store: MarketStore, path=None) -> int:
    """Refresh the legacy `data/fundamental/profiles.json` mirror from the
    `company_profile` table. Call ONCE at the end of a batch, never per symbol.

    MERGE with table priority, not a wholesale rebuild: existing JSON entries
    are the base, every `company_profile` row is overlaid on top (the table is
    SSOT per symbol, replacing that entry wholesale — not deep-merged), and an
    entry the table has never heard of is never dropped. Legacy writers still
    merge profiles straight into this file that never enter the table —
    `scripts/build_company_concept_registry.refresh_profiles` does exactly that
    for the extend pool — and dropping them would flip `data_health`'s 80%
    fundamental-coverage check to FAIL. Those foreign entries are preserved
    for the duration of the transition; Stop G retires them.

    Readers that still go to disk: `src/data/data_health.py` (coverage +
    freshness), `portfolio/exposure/analyzer.py:_load_profiles`,
    `scripts/morning_report.py` metadata cache. Format matches what
    `src/data/fundamental_fetcher.update_profiles` used to write:
    `{SYMBOL: {...provider fields..., "_updated_at": ...}, "_meta": {...}}`.
    `_meta.updated_at` MUST stay in the naive-local "%Y-%m-%d %H:%M:%S" form —
    `data_health._check_fundamental_freshness` and `data_validator` strptime it
    with exactly that format, and anything else (ISO, offsets) degrades them to
    a permanent unparseable-WARN / `is_fresh=False`.

    Guard kept from the incident that produced the same guard in
    `build_company_concept_registry.py`: if the merge would leave nothing at
    all (empty table AND no usable existing JSON), raise instead of writing an
    empty mirror.

    Written tmp-then-`os.replace` so a reader never sees a half-written file.

    Args:
        store: SSOT for per-symbol entries (`company_profile` table).
        path: mirror location; defaults to DEFAULT_PROFILES_PATH.

    Returns:
        Total number of profile entries in the written mirror.
    """
    path = Path(path) if path is not None else DEFAULT_PROFILES_PATH
    profiles: Dict[str, Any] = _load_existing_profiles(path)
    preserved = len(profiles)

    conn = store._get_conn()
    rows = conn.execute(
        "SELECT symbol, payload, updated_at FROM company_profile ORDER BY symbol"
    ).fetchall()
    if not rows:
        logger.warning("company_profile is empty — %s keeps its existing %d entries",
                       path, preserved)

    from_table = 0
    for row in rows:
        try:
            payload = json.loads(row["payload"])
        except (TypeError, ValueError):
            logger.warning("unparseable company_profile payload for %s — skipped",
                           row["symbol"])
            continue
        if not isinstance(payload, dict):
            logger.warning("non-object company_profile payload for %s — skipped",
                           row["symbol"])
            continue
        payload.setdefault("symbol", row["symbol"])
        payload.setdefault("_updated_at", row["updated_at"])
        profiles[row["symbol"]] = payload
        from_table += 1

    if not profiles:
        raise ValueError(
            f"company_profile is empty and {path} has no usable entries — "
            f"refusing to write an empty mirror")

    count = len(profiles)
    merged = {key: profiles[key] for key in sorted(profiles)}
    # Naive local time, legacy format — see docstring. Not ISO, not UTC-aware.
    merged["_meta"] = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "count": count,
        "source": "market.db:company_profile",
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(merged, fh, ensure_ascii=False, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp_path, path)
    logger.info("refreshed %s: %d entries (%d from company_profile)",
                path, count, from_table)
    return count
