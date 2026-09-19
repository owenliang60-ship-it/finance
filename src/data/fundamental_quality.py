"""Read-only fundamental data-quality auditor (weekly Premium feeder).

This module observes only. It classifies every eligible Extended ("base")
security into clean, warning, proactive-verification, or repair-candidate sets
so that a separate, explicitly-invoked collection executor can decide what to
fetch. No coverage_status row, statement row, or metrics row is created,
updated, or deleted here — the audit is safe to run against the live DB.

Contract highlights (frozen by the task brief):

* ``as_of`` is either a pure ``YYYY-MM-DD`` date (normalized to 23:59:59 UTC,
  end of day) or a timezone-aware ISO datetime (normalized to UTC). Malformed
  values and nonpositive ``refresh_days`` / ``stale_after_days`` /
  ``cooldown_days`` raise ``ValueError``.
* Fundamental readiness reuses ``terminal.selection_compass
  ._evaluate_fundamental`` — the canonical gate used by the Premium builder —
  rather than a second readiness definition.
* Latest balance-sheet / cash-flow alignment reuses
  ``src.data.metrics_calculator._statement_by_income_date`` so the auditor
  reports the same fiscal ambiguity the metrics engine would raise, without
  editing rows.
* ``fetch_failed`` / ``provider_empty`` coverage states with an active or
  unknown retry timer block the whole symbol; a valid due timer permits repair.
* A future or unparseable timestamp is UNKNOWN, never fresh.

Reason strings in ``issues`` are stable: coverage/timer/verification reasons
are defined by this module; canonical raw-predicate reasons are passed through
verbatim (``missing_quarters``, ``quarter_gap``, ``missing_raw_value``,
``invalid_fiscal_period``, ``fiscal_quarter_sequence``, ``invalid_row``,
``invalid_date``, ``missing_yoy_match``).
"""
from __future__ import annotations

import re
import sqlite3
from datetime import date, datetime, time, timezone
from typing import Any, Dict, List, Optional, Tuple

from src.data.metrics_calculator import _statement_by_income_date
from src.data.universe_resolver import current_base_universe
from terminal.selection_compass import _evaluate_fundamental

SCHEMA_VERSION = 1

# (dataset key, coverage_status.dataset == table name)
REQUIRED_STATEMENTS: Tuple[Tuple[str, str], ...] = (
    ("income", "income_quarterly"),
    ("balance", "balance_sheet_quarterly"),
    ("cashflow", "cash_flow_quarterly"),
)

RETRY_GATED_STATUSES = frozenset({"fetch_failed", "provider_empty"})
TERMINAL_STATUSES = frozenset({"not_applicable", "identity_blocked"})

# Coverage / timer / verification reasons (module-owned vocabulary).
ISSUE_MISSING_COVERAGE = "missing_coverage_status"
ISSUE_MISSING_STATEMENT = "missing_required_statement"
ISSUE_FETCH_FAILED = "statement_fetch_failed"
ISSUE_PROVIDER_EMPTY = "statement_provider_empty"
ISSUE_TIMER_ACTIVE = "retry_timer_active"
ISSUE_TIMER_UNKNOWN = "retry_timer_unknown"
ISSUE_TERMINAL_NOT_APPLICABLE = "terminal_not_applicable"
ISSUE_TERMINAL_IDENTITY_BLOCKED = "terminal_identity_blocked"
ISSUE_FISCAL_STALE = "fiscal_stale"
ISSUE_INVALID_FISCAL_DATE = "invalid_fiscal_date"
ISSUE_VERIFICATION_OVERDUE = "verification_overdue"
ISSUE_VERIFICATION_UNKNOWN = "verification_unknown"
ISSUE_METRICS_NOT_CURRENT = "metrics_not_current"
ISSUE_CROSS_STATEMENT_MISSING = "cross_statement_missing"
ISSUE_AMBIGUOUS_QUARTER = "ambiguous_fiscal_quarter"
ISSUE_EARNINGS_HINT = "earnings_hint"
ISSUE_EVENT_UNKNOWN = "event_evidence_unknown"

# Canonical ``_raw_inputs_ready`` reasons that only signal insufficient history.
# They are warnings, never automatic repair triggers.
STRUCTURAL_RAW_REASONS = frozenset({
    "missing_quarters", "quarter_gap",
})
# Canonical raw reasons that mean the stored rows themselves deserve a recheck.
RECHECK_RAW_REASONS = frozenset({
    "missing_raw_value", "invalid_fiscal_period", "fiscal_quarter_sequence",
    "invalid_row", "invalid_date", "missing_yoy_match",
})

# Issues whose persistence after a recent successful source check defers them
# instead of proving the data is healthy (brief rule 6).
DEFERRABLE_ISSUES = frozenset({
    ISSUE_FISCAL_STALE, ISSUE_INVALID_FISCAL_DATE, ISSUE_EARNINGS_HINT,
    ISSUE_CROSS_STATEMENT_MISSING, ISSUE_AMBIGUOUS_QUARTER,
}) | RECHECK_RAW_REASONS

# "missing / new metrics" repair priority.
_MISSING_METRICS_REASONS = frozenset({
    ISSUE_MISSING_COVERAGE, ISSUE_MISSING_STATEMENT, ISSUE_METRICS_NOT_CURRENT,
})
_FAILED_REASONS = frozenset({ISSUE_FETCH_FAILED, ISSUE_PROVIDER_EMPTY})

_PURE_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_EVENT_MIN_ANNOUNCE_AGE_DAYS = 2
_EVENT_MAX_ANNOUNCE_AGE_DAYS = 45
_EVENT_MAX_LAST_UPDATED_AGE_DAYS = 9
_EVENT_MIN_FISCAL_LEAD_DAYS = 45

_MISSING_TABLE_MARKER = "no such table"


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

def _normalize_as_of(as_of: str) -> datetime:
    """Pure date -> end-of-day UTC; aware ISO datetime -> UTC; else ValueError."""
    if not isinstance(as_of, str) or not as_of.strip():
        raise ValueError(f"malformed as_of: {as_of!r}")
    text = as_of.strip()
    if _PURE_DATE_RE.match(text):
        try:
            parsed = date.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(f"malformed as_of: {as_of!r}") from exc
        return datetime.combine(parsed, time(23, 59, 59), tzinfo=timezone.utc)
    iso = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed_dt = datetime.fromisoformat(iso)
    except ValueError as exc:
        raise ValueError(f"malformed as_of: {as_of!r}") from exc
    if parsed_dt.tzinfo is None:
        raise ValueError(f"as_of datetime must include a UTC offset: {as_of!r}")
    return parsed_dt.astimezone(timezone.utc)


def _format_ts(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_ts(value: Any) -> Optional[datetime]:
    """Parse a DB timestamp; naive values are UTC by storage convention."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if _PURE_DATE_RE.match(text):
        try:
            parsed = date.fromisoformat(text)
        except ValueError:
            return None
        return datetime.combine(parsed, time(0, 0, 0), tzinfo=timezone.utc)
    iso = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed_dt = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if parsed_dt.tzinfo is None:
        parsed_dt = parsed_dt.replace(tzinfo=timezone.utc)
    return parsed_dt.astimezone(timezone.utc)


def _parse_iso_date(value: Any) -> Optional[date]:
    """Strict YYYY-MM-DD fiscal-date parser; anything else is invalid."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not _PURE_DATE_RE.match(text):
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _validate_positive_int(name: str, value: Any) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer, got {value!r}")


# ---------------------------------------------------------------------------
# Read-only store access
# ---------------------------------------------------------------------------

def _read_coverage(store: Any) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """symbol -> dataset -> coverage row fields (one read-only query).

    Mirrors ``scripts/reconcile_fundamentals._coverage_rows`` but keeps the
    ``last_attempt_at`` / ``last_success_at`` columns the auditor needs. This
    never writes, so unlike the reconcile Phase 1 primitive it cannot leave a
    stale annotation behind.
    """
    conn = store._get_conn()
    rows = conn.execute(
        "SELECT symbol, dataset, status, last_attempt_at, last_success_at, "
        "next_retry_at FROM coverage_status"
    ).fetchall()
    coverage: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for row in rows:
        coverage.setdefault(row["symbol"], {})[row["dataset"]] = {
            "status": row["status"],
            "last_attempt_at": row["last_attempt_at"],
            "last_success_at": row["last_success_at"],
            "next_retry_at": row["next_retry_at"],
        }
    return coverage


def _read_earnings(store: Any, symbol: str) -> Tuple[Optional[List[Dict[str, Any]]], str]:
    """Return (rows, state). A missing table is explicit UNKNOWN; other DB
    errors propagate unchanged."""
    try:
        return store.get_fmp_earnings(symbol), "ok"
    except sqlite3.OperationalError as exc:
        if _MISSING_TABLE_MARKER in str(exc).lower():
            return None, "missing_table"
        raise


# ---------------------------------------------------------------------------
# Small classifiers
# ---------------------------------------------------------------------------

def _unique(values: List[str]) -> List[str]:
    seen = set()
    ordered = []
    for value in values:
        if value not in seen:
            seen.add(value)
            ordered.append(value)
    return ordered


def _timer_state(next_retry_at: Any, as_of_dt: datetime) -> str:
    """due -> repair allowed; active/unknown -> the symbol is blocked."""
    ts = _parse_ts(next_retry_at)
    if ts is None:
        return "unknown"
    if ts > as_of_dt:
        return "active"
    return "due"


def _recently_checked(cov: Dict[str, Any], as_of_dt: datetime,
                      cooldown_days: int) -> bool:
    """True only for a parseable, non-future attempt/success inside cooldown."""
    for field in ("last_attempt_at", "last_success_at"):
        ts = _parse_ts(cov.get(field))
        if ts is not None and ts <= as_of_dt and (as_of_dt - ts).days < cooldown_days:
            return True
    return False


def _success_timestamps_as_of(cov_map: Dict[str, Dict[str, Any]],
                              as_of_dt: datetime) -> Optional[List[datetime]]:
    """All required statements' valid, non-future last_success_at, or None."""
    timestamps = []
    for _key, table in REQUIRED_STATEMENTS:
        cov = cov_map.get(table)
        ts = _parse_ts(cov.get("last_success_at")) if cov else None
        if ts is None or ts > as_of_dt:
            return None
        timestamps.append(ts)
    return timestamps


def _statement_failure_reason(status: str) -> str:
    return ISSUE_FETCH_FAILED if status == "fetch_failed" else ISSUE_PROVIDER_EMPTY


def _terminal_reason(status: str) -> str:
    return (ISSUE_TERMINAL_NOT_APPLICABLE if status == "not_applicable"
            else ISSUE_TERMINAL_IDENTITY_BLOCKED)


# ---------------------------------------------------------------------------
# Event hint (cached fmp_earnings)
# ---------------------------------------------------------------------------

def _evaluate_earnings(rows: Optional[List[Dict[str, Any]]], *,
                       income_latest: Optional[date], as_of_dt: datetime
                       ) -> Dict[str, Any]:
    """Classify the cached earnings feed as hint / none / unknown.

    Only an announce age in [2, 45] days and a last_updated age in [0, 9] days
    makes a row usable. A usable row whose valid ``fiscal_date`` is at most
    ``as_of`` and more than 45 days newer than the latest income quarter is an
    ``earnings_hint``. Any other feed state is UNKNOWN — never proof that no
    earnings happened.
    """
    if rows is None:
        return {"state": "unknown", "hint": None}

    as_of_date = as_of_dt.date()
    fresh_rows = []
    for row in rows:
        announce = _parse_iso_date(row.get("announce_date"))
        updated = _parse_ts(row.get("last_updated"))
        if announce is None or updated is None:
            continue
        announce_age = (as_of_date - announce).days
        updated_age = (as_of_dt - updated).days
        if (_EVENT_MIN_ANNOUNCE_AGE_DAYS <= announce_age
                <= _EVENT_MAX_ANNOUNCE_AGE_DAYS
                and 0 <= updated_age <= _EVENT_MAX_LAST_UPDATED_AGE_DAYS):
            fresh_rows.append((row, announce, updated))

    if not fresh_rows:
        return {"state": "unknown", "hint": None}

    examined_fiscal = False
    for row, _announce, _updated in fresh_rows:
        fiscal = _parse_iso_date(row.get("fiscal_date"))
        if fiscal is None or fiscal > as_of_date:
            continue
        examined_fiscal = True
        if income_latest is not None and (fiscal - income_latest).days > _EVENT_MIN_FISCAL_LEAD_DAYS:
            return {
                "state": "hint",
                "hint": {
                    "announce_date": row.get("announce_date"),
                    "fiscal_date": row.get("fiscal_date"),
                    "last_updated": row.get("last_updated"),
                },
            }

    if examined_fiscal:
        return {"state": "none", "hint": None}
    return {"state": "unknown", "hint": None}


# ---------------------------------------------------------------------------
# Per-symbol evaluation
# ---------------------------------------------------------------------------

class _SymbolResult:
    __slots__ = ("record", "blocked", "candidates", "oldest_success",
                 "all_successes_valid", "sort_key")

    def __init__(self, record, blocked, candidates, oldest_success,
                 all_successes_valid, sort_key):
        self.record = record
        self.blocked = blocked
        self.candidates = candidates
        self.oldest_success = oldest_success
        self.all_successes_valid = all_successes_valid
        self.sort_key = sort_key


def _evaluate_symbol(symbol: str, *, store: Any, cov_map: Dict[str, Dict[str, Any]],
                     as_of_dt: datetime, as_of_date: date,
                     refresh_days: int, stale_after_days: int,
                     cooldown_days: int) -> _SymbolResult:
    entries: List[Dict[str, Any]] = []

    def add(reason: str, statement: Optional[str], kind: str,
            recent: Optional[bool] = None) -> None:
        entries.append({
            "reason": reason, "statement": statement, "kind": kind,
            "recent": recent,
        })

    income_rows = store.get_income(symbol, limit=20)
    balance_rows = store.get_balance_sheet(symbol, limit=20)
    cashflow_rows = store.get_cash_flow(symbol, limit=20)
    metrics_rows = store.get_metrics(symbol, limit=8)
    rows_by_key = {
        "income": income_rows, "balance": balance_rows, "cashflow": cashflow_rows,
    }

    evidence: Dict[str, Any] = {
        "income_date": None, "balance_date": None, "cashflow_date": None,
        "metrics_date": None, "oldest_success_at": None, "event_hint": None,
        "event_evidence": None, "coverage": {},
    }

    blocked = False
    for key, table in REQUIRED_STATEMENTS:
        rows = rows_by_key[key]
        cov = cov_map.get(table)
        latest_raw = rows[0].get("date") if rows else None
        evidence[f"{key}_date"] = latest_raw
        evidence["coverage"][key] = (cov or {}).get("status") if cov else None

        if cov is None:
            add(ISSUE_MISSING_COVERAGE, key, "candidate")
            if not rows:
                add(ISSUE_MISSING_STATEMENT, key, "candidate")
        else:
            status = cov["status"]
            if status in TERMINAL_STATUSES:
                add(_terminal_reason(status), key, "terminal")
                blocked = True
            elif status in RETRY_GATED_STATUSES:
                add(_statement_failure_reason(status), key, "candidate")
                timer = _timer_state(cov.get("next_retry_at"), as_of_dt)
                if timer == "active":
                    add(ISSUE_TIMER_ACTIVE, key, "blocking")
                    blocked = True
                elif timer == "unknown":
                    add(ISSUE_TIMER_UNKNOWN, key, "blocking")
                    blocked = True
            if status == "stale":
                add("statement_marked_stale", key, "deferrable")
            elif status not in TERMINAL_STATUSES | RETRY_GATED_STATUSES | {"ok"}:
                add("unknown_coverage_status", key, "candidate")
            if status not in TERMINAL_STATUSES:
                attempt = _parse_ts(cov.get("last_attempt_at"))
                if attempt is None or attempt > as_of_dt:
                    add(ISSUE_VERIFICATION_UNKNOWN, key, "candidate")
            if not rows and status not in TERMINAL_STATUSES and status not in RETRY_GATED_STATUSES:
                recent = _recently_checked(cov, as_of_dt, cooldown_days)
                add(ISSUE_MISSING_STATEMENT, key,
                    "source_deferred" if recent else "candidate", recent=recent)

        if rows:
            latest_date = _parse_iso_date(latest_raw)
            if latest_date is None or latest_date > as_of_date:
                add(ISSUE_INVALID_FISCAL_DATE, key, "deferrable")
            elif (as_of_date - latest_date).days > stale_after_days:
                add(ISSUE_FISCAL_STALE, key, "deferrable")

        if cov is not None and cov["status"] not in TERMINAL_STATUSES:
            success = _parse_ts(cov.get("last_success_at"))
            if success is None or success > as_of_dt:
                add(ISSUE_VERIFICATION_UNKNOWN, key, "candidate")
            elif (as_of_dt - success).days > refresh_days:
                add(ISSUE_VERIFICATION_OVERDUE, key, "candidate")

    income_latest = _parse_iso_date(evidence["income_date"])
    latest_metric_raw = metrics_rows[0].get("date") if metrics_rows else None
    evidence["metrics_date"] = latest_metric_raw
    if income_latest is not None:
        latest_metric = _parse_iso_date(latest_metric_raw)
        if latest_metric is None or latest_metric != income_latest:
            add(ISSUE_METRICS_NOT_CURRENT, None, "candidate")

        # Latest BS / CF alignment, reuse the metrics engine's matcher. Only
        # the newest income quarter is validated; older history is not required.
        latest_income_row = income_rows[0]
        for key, statement in (("balance", "balance"), ("cashflow", "cashflow")):
            try:
                aligned = _statement_by_income_date(
                    [latest_income_row], rows_by_key[key], statement)
            except ValueError as exc:
                message = str(exc).lower()
                if "invalid fiscal year/quarter" in message:
                    add("invalid_fiscal_period", "income", "deferrable")
                else:
                    add(ISSUE_AMBIGUOUS_QUARTER, key, "deferrable")
            else:
                if latest_income_row.get("date") not in aligned:
                    add(ISSUE_CROSS_STATEMENT_MISSING, key, "deferrable")

    # Canonical readiness gate (also drives the covered count). The canonical
    # caller passes a pure date, so keep the comparison tz-naive.
    income_status = (cov_map.get("income_quarterly") or {}).get("status")
    readiness = _evaluate_fundamental(
        income_rows=income_rows, metrics=metrics_rows,
        coverage_status=income_status, as_of=as_of_date.isoformat(),
    )
    raw = readiness.get("raw") or {}
    if income_rows and not raw.get("ready"):
        reason = raw.get("reason")
        if reason:
            kind = "structural" if reason in STRUCTURAL_RAW_REASONS else "deferrable"
            add(reason, "income", kind)

    # Cached earnings feed.
    earnings_rows, feed_state = _read_earnings(store, symbol)
    event = _evaluate_earnings(
        None if feed_state == "missing_table" else earnings_rows,
        income_latest=income_latest, as_of_dt=as_of_dt,
    )
    evidence["event_evidence"] = (
        "missing_table" if feed_state == "missing_table" else event["state"])
    evidence["event_hint"] = event["hint"]
    if event["state"] == "hint":
        add(ISSUE_EARNINGS_HINT, None, "deferrable")
    elif event["state"] == "unknown":
        add(ISSUE_EVENT_UNKNOWN, None, "event")

    # Cooldown state: only meaningful when every required statement has a
    # valid, recent successful verification.
    success_ts = _success_timestamps_as_of(cov_map, as_of_dt)
    oldest_success = min(success_ts) if success_ts else None
    evidence["oldest_success_at"] = _format_ts(oldest_success)
    all_recent_success = bool(success_ts) and all(
        (as_of_dt - ts).days < cooldown_days for ts in success_ts)

    reasons = _unique([entry["reason"] for entry in entries])
    deferred_reasons: List[str] = []
    candidates: List[str] = []
    if blocked:
        deferred_reasons = list(reasons)
    else:
        for entry in entries:
            kind = entry["kind"]
            if kind in ("structural", "event"):
                continue
            if kind == "terminal" or kind == "blocking":
                deferred_reasons.append(entry["reason"])
            elif kind == "source_deferred":
                deferred_reasons.append(entry["reason"])
            elif kind == "deferrable":
                if all_recent_success:
                    deferred_reasons.append(entry["reason"])
                else:
                    candidates.append(entry["reason"])
            else:
                candidates.append(entry["reason"])

    repair_eligible = bool(candidates) and not blocked
    deferred_reasons = _unique(deferred_reasons)

    bucket = 2
    if any(reason in _MISSING_METRICS_REASONS for reason in candidates):
        bucket = 0
    elif any(reason in _FAILED_REASONS for reason in candidates):
        bucket = 1
    success_sort = (0, oldest_success.timestamp()) if oldest_success else (1, 0.0)

    record = {
        "issues": reasons,
        "evidence": evidence,
        "repair_eligible": repair_eligible,
        "deferred_reasons": deferred_reasons,
        "fundamental_ready": bool(readiness.get("ready")),
    }
    return _SymbolResult(
        record=record, blocked=blocked, candidates=candidates,
        oldest_success=oldest_success, all_successes_valid=success_ts is not None,
        sort_key=(bucket, success_sort, symbol),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _audit_fundamentals(store: Any, *, as_of: str, refresh_days: int = 30,
                       stale_after_days: int = 120,
                       cooldown_days: int = 7) -> Dict[str, Any]:
    """Read-only fundamental quality audit over the explicit Extended base pool.

    Returns a JSON-compatible report; see the module docstring for the frozen
    contract. The audit performs only SELECT-style reads.
    """
    as_of_dt = _normalize_as_of(as_of)
    _validate_positive_int("refresh_days", refresh_days)
    _validate_positive_int("stale_after_days", stale_after_days)
    _validate_positive_int("cooldown_days", cooldown_days)
    as_of_iso = _format_ts(as_of_dt)
    as_of_date = as_of_dt.date()

    symbols = sorted({
        str(symbol).strip().upper()
        for symbol in current_base_universe(store)
        if str(symbol).strip()
    })
    if not symbols:
        raise RuntimeError("empty fundamental quality universe")
    coverage = _read_coverage(store)

    records: Dict[str, Dict[str, Any]] = {}
    repair_ranked: List[Tuple[Any, str]] = []
    verification_ranked: List[Tuple[Any, str]] = []
    issues_map: Dict[str, List[str]] = {}

    for symbol in symbols:
        result = _evaluate_symbol(
            symbol, store=store, cov_map=coverage.get(symbol, {}),
            as_of_dt=as_of_dt, as_of_date=as_of_date,
            refresh_days=refresh_days, stale_after_days=stale_after_days,
            cooldown_days=cooldown_days,
        )
        records[symbol] = result.record
        for reason in result.record["issues"]:
            issues_map.setdefault(reason, []).append(symbol)
        if result.record["repair_eligible"]:
            repair_ranked.append((result.sort_key, symbol))
        if (not result.record["repair_eligible"] and not result.blocked
                and not result.record["deferred_reasons"]
                and result.all_successes_valid and result.oldest_success is not None
                and (as_of_dt - result.oldest_success).days >= cooldown_days):
            verification_ranked.append((result.oldest_success, symbol))

    repair_targets = [symbol for _, symbol in sorted(repair_ranked, key=lambda item: item[0])]
    verification_targets = [symbol for _, symbol in sorted(verification_ranked)]
    deferred = sorted(s for s in symbols if records[s]["deferred_reasons"])

    covered = sum(1 for record in records.values() if record["fundamental_ready"])
    total = len(symbols)
    status = "FAIL" if repair_targets else (
        "WARN" if any(record["issues"] for record in records.values()) else "CLEAN")

    return {
        "schema_version": SCHEMA_VERSION,
        "as_of": as_of_iso,
        "universe": {"count": total, "symbols": symbols},
        "coverage": {
            "fundamental_ready": {
                "covered": covered,
                "total": total,
                "ratio": covered / total if total else 0.0,
            },
        },
        "status": status,
        "symbols": records,
        "repair_targets": repair_targets,
        "verification_targets": verification_targets,
        "deferred": deferred,
        "issues": {reason: sorted(tickers) for reason, tickers in sorted(issues_map.items())},
    }


def audit_fundamentals(store: Any, *, as_of: str, refresh_days: int = 30,
                       stale_after_days: int = 120,
                       cooldown_days: int = 7) -> Dict[str, Any]:
    """Read one SQLite snapshot; never commit or end a caller-owned transaction."""
    conn = store._get_conn()
    own_snapshot = not conn.in_transaction
    if own_snapshot:
        conn.execute("BEGIN")
    try:
        return _audit_fundamentals(
            store, as_of=as_of, refresh_days=refresh_days,
            stale_after_days=stale_after_days, cooldown_days=cooldown_days,
        )
    finally:
        if own_snapshot:
            conn.rollback()
