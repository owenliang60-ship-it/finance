"""Conservative fiscal-alias writes and reviewed repairs of current statements.

No vintage is deleted, relabeled, or backdated. Public repair callers must hold
the shared market.db writer lock; this module owns SQLite atomicity only.
"""
import hashlib
import json
import re
from datetime import datetime, timezone
from uuid import uuid4


STATEMENT_TABLES = frozenset({
    "income_quarterly", "balance_sheet_quarterly", "cash_flow_quarterly",
})
_NON_FINANCIAL = frozenset({
    "symbol", "date", "fiscal_year", "period", "filing_date", "accepted_date",
})


# Reviewed provider dates: (symbol, cik, provider date, fiscal_year, period,
# currency) -> period end. Only exact matches change; no rule is inferred.
# AEM 2024Q3: docs/audit/2026-10-10-cron-recovery.md. The rest are isolated
# dates (one-year neighbours two years apart), period ends from SEC 10-Q/10-K
# report dates (KR, ACI, AGL, LB) or the calendar quarter end of December
# fiscal-year foreign issuers (AEM, TECK, RIO half-year, BEP):
# docs/audit/2026-10-25-isolated-statement-dates.md. PGR 2026Q2 balance: FMP
# once dated it at the month end before the 10-Q quarter end (SEC report date
# 2026-06-30): docs/audit/2026-10-25-pgr-balance-dates.md.
REVIEWED_STATEMENT_DATES = {
    ("AEM", "0000002809", "2024-10-30", "2024", "Q3", "USD"): "2024-09-30",
    ("AEM", "0000002809", "2023-01-27", "2022", "Q4", "USD"): "2022-12-31",
    ("TECK", "0000886986", "2024-10-23", "2024", "Q3", "CAD"): "2024-09-30",
    ("RIO", "0000863064", "2017-05-31", "2017", "Q2", "USD"): "2017-06-30",
    ("BEP", "0001533232", "2018-08-03", "2018", "Q2", "USD"): "2018-06-30",
    ("KR", "0000056873", "2021-04-28", "2021", "Q1", "USD"): "2021-05-22",
    ("ACI", "0001646972", "2023-05-23", "2023", "Q1", "USD"): "2023-06-17",
    ("AGL", "0001831097", "2021-02-18", "2020", "Q4", "USD"): "2020-12-31",
    ("LB", "0001995807", "2024-08-03", "2024", "Q2", "USD"): "2024-06-30",
    ("PGR", "0000080661", "2026-05-31", "2026", "Q2", "USD"): "2026-06-30",
}


def reviewed_statement_date(row):
    """Correct reviewed provider dates in current/valuation inputs.

    Raw vintage payloads remain untouched. Rows already stored under a wrong
    date need the one-time `apply_reviewed_identity_updates` repair.
    """
    key = (row.get("symbol"), row.get("cik"), row.get("date"),
           str(row.get("fiscal_year", row.get("fiscalYear"))), row.get("period"),
           row.get("reported_currency", row.get("reportedCurrency")))
    period_end = REVIEWED_STATEMENT_DATES.get(key)
    return {**row, "date": period_end} if period_end else row


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def fiscal_group_hash(rows):
    """SHA256 of exact full persisted rows, independent of query ordering."""
    ordered = sorted((dict(row) for row in rows), key=lambda row: _json(row))
    return hashlib.sha256(_json(ordered).encode("utf-8")).hexdigest()


def _fiscal_key(row):
    year, quarter = row.get("fiscal_year"), row.get("period")
    if year in (None, "") or quarter in (None, ""):
        return None  # Legacy unknown keys never authorize deleting another date.
    text = str(year)
    if not re.fullmatch(r"[1-9]\d{3}(?:\.0+)?", text) or quarter not in {"Q1", "Q2", "Q3", "Q4"}:
        raise ValueError("invalid fiscal year/quarter: {!r}/{!r}".format(year, quarter))
    return text.split(".")[0], quarter


def fiscal_group_rows(store, table, symbol, fiscal_year, period):
    """Read full current rows for an explicitly valid fiscal identity."""
    if table not in STATEMENT_TABLES:
        raise ValueError("invalid quarterly statement table: {!r}".format(table))
    key = _fiscal_key({"fiscal_year": fiscal_year, "period": period})
    if key is None:
        raise ValueError("repair requires a complete fiscal key")
    return [row for row in store._get_rows(table, symbol, limit=0)
            if _fiscal_key(row) == key]


def _equivalent_financials(old, new):
    # Includes all persisted numeric columns, currency and CIK. NULL != zero;
    # absent incoming columns compare as NULL rather than accepting partial overlap.
    return all(old.get(key) == new.get(key)
               for key in (old.keys() | new.keys()) - _NON_FINANCIAL)


def _archive(conn, table, rows, operation_id, reason, context):
    stamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    for row in rows:
        conn.execute(
            "INSERT INTO fundamental_current_archive "
            "(operation_id, archived_at, source_table, symbol, original_date, "
            "reason, context, payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (operation_id, stamp, table, row["symbol"], row["date"], reason,
             _json(context), _json(dict(row))))


def _recompute_metrics_in_conn(store, conn, symbol, operation_id):
    """Reuse all existing formulas, but capture writes and request all history."""
    from src.data.metrics_calculator import compute_metrics

    class MetricsInputs:
        rows = None

        def get_income(self, sym, limit=20):
            return store.get_income(sym, limit=0)

        def get_balance_sheet(self, sym, limit=20):
            return store.get_balance_sheet(sym, limit=0)

        def get_cash_flow(self, sym, limit=20):
            return store.get_cash_flow(sym, limit=0)

        def upsert_metrics(self, sym, rows):
            self.rows = rows
            return len(rows)

    inputs = MetricsInputs()
    count = compute_metrics(symbol, inputs)
    rows = inputs.rows or []
    if count != len(rows):
        raise ValueError("metrics computation returned inconsistent row count")
    previous = store.get_metrics(symbol, limit=0)
    _archive(conn, "metrics_quarterly", previous, operation_id,
             "metrics_recomputed", {"symbol": symbol})
    conn.execute("DELETE FROM metrics_quarterly WHERE symbol = ?", (symbol,))
    return store._upsert_rows_in_conn(conn, "metrics_quarterly", rows)


def write_statement_in_conn(store, conn, table, rows):
    """Prevalidate a window, then replace only financially equivalent aliases.

    Incoming dates determine the new representation only when each fiscal key
    has exactly one date and every displaced row is financially equivalent.
    Differing-date financial revisions need an explicit reviewed repair.
    """
    if table not in STATEMENT_TABLES:
        raise ValueError("invalid quarterly statement table: {!r}".format(table))
    by_key, by_date, existing = {}, {}, {}
    removals = []
    for row in rows:
        symbol, date = row["symbol"], row["date"]
        key = _fiscal_key(row)
        date_key = symbol, date
        if date_key in by_date and by_date[date_key] != row:
            raise ValueError("conflicting current rows for the same date")
        by_date[date_key] = row
        if key is None:
            continue
        group_key = symbol, key
        if group_key in by_key and by_key[group_key]["date"] != date:
            raise ValueError("ambiguous incoming fiscal quarter has multiple dates")
        by_key[group_key] = row
        if symbol not in existing:
            existing[symbol] = store._get_rows(table, symbol, limit=0)

    for (symbol, key), incoming in by_key.items():
        for old in existing[symbol]:
            old_key = _fiscal_key(old)
            if old["date"] == incoming["date"]:
                if old_key is not None and old_key != key:
                    raise ValueError("conflicting fiscal identity at an existing date")
                continue  # Same-date restatement retains the established semantics.
            if old_key != key:
                continue
            if not _equivalent_financials(old, incoming):
                raise ValueError("conflicting fiscal alias requires reviewed repair: {} {} {}".format(
                    symbol, key, old["date"]))
            removals.append((old, incoming["date"]))

    operation_id = uuid4().hex
    for old, keep_date in removals:
        _archive(conn, table, [old], operation_id, "equivalent_fiscal_alias",
                 {"keep_date": keep_date})
        conn.execute("DELETE FROM {} WHERE symbol = ? AND date = ?".format(table),
                     (old["symbol"], old["date"]))
    count = store._upsert_rows_in_conn(conn, table, list(by_date.values()))
    for symbol in sorted({old["symbol"] for old, _ in removals}):
        _recompute_metrics_in_conn(store, conn, symbol, operation_id)
    return count


def apply_fiscal_repairs(store, mappings):
    """Apply reviewed table/symbol/fiscal_year/period/keep_date/group_hash maps.

    All maps are checked before any mutation. Exact replay succeeds only when
    an earlier archive proves the same plan and the surviving group is unchanged.
    Caller must acquire FileLock before constructing a writable MarketStore.
    """
    result = {"groups_repaired": 0, "groups_unchanged": 0,
              "rows_removed": 0, "metrics_rows": 0}
    with store.transaction() as conn:
        pending, seen = [], set()
        for supplied in mappings:
            plan = {key: supplied[key] for key in
                    ("table", "symbol", "fiscal_year", "period", "keep_date", "group_hash")}
            plan["symbol"] = str(plan["symbol"]).upper()
            fiscal_key = _fiscal_key(plan)
            if fiscal_key is None:
                raise ValueError("repair requires a complete fiscal key")
            plan["fiscal_year"] = fiscal_key[0]
            identity = plan["table"], plan["symbol"], fiscal_key
            if identity in seen:
                raise ValueError("duplicate repair mapping for fiscal group")
            seen.add(identity)
            group = fiscal_group_rows(store, plan["table"], plan["symbol"], *fiscal_key)
            operation_id = hashlib.sha256(_json(plan).encode("utf-8")).hexdigest()
            current_hash = fiscal_group_hash(group)
            if current_hash != plan["group_hash"]:
                proof = conn.execute(
                    "SELECT context FROM fundamental_current_archive "
                    "WHERE operation_id = ? AND reason = 'reviewed_fiscal_repair' LIMIT 1",
                    (operation_id,)).fetchone()
                if proof is not None and json.loads(proof[0])["after_hash"] == current_hash:
                    result["groups_unchanged"] += 1
                    continue
                raise ValueError("current fiscal group hash differs from reviewed mapping")
            kept = [row for row in group if row["date"] == plan["keep_date"]]
            if len(kept) != 1:
                raise ValueError("keep_date must identify exactly one current row")
            removed = [row for row in group if row["date"] != plan["keep_date"]]
            if not removed:
                result["groups_unchanged"] += 1
                continue
            pending.append((plan, operation_id, kept, removed))

        for plan, operation_id, kept, removed in pending:
            _archive(conn, plan["table"], removed, operation_id, "reviewed_fiscal_repair",
                     {"plan": plan, "after_hash": fiscal_group_hash(kept)})
            for row in removed:
                conn.execute("DELETE FROM {} WHERE symbol = ? AND date = ?".format(plan["table"]),
                             (plan["symbol"], row["date"]))
            result["groups_repaired"] += 1
            result["rows_removed"] += len(removed)

        for symbol in sorted({plan["symbol"] for plan, _, _, _ in pending}):
            result["metrics_rows"] += _recompute_metrics_in_conn(store, conn, symbol, uuid4().hex)
    return result


def apply_reviewed_identity_updates(store, mappings):
    """Correct explicitly reviewed current dates/labels, never financial values.

    Caller holds the shared writer lock and creates a SQLite backup first.
    Every old row must match its full hash. All changes, archival and metric
    recomputation share one transaction; stale/replayed plans fail closed.
    Vintage payloads and timestamps are not rewritten.
    """
    from datetime import date

    mappings = [{**plan, "symbol": str(plan["symbol"]).strip().upper()} for plan in mappings]
    result = {"rows_updated": 0, "metrics_rows": 0}
    with store.transaction() as conn:
        pending, seen = [], set()
        for plan in mappings:
            table, symbol, old_date = plan["table"], plan["symbol"], plan["date"]
            changes = plan["changes"]
            if table not in STATEMENT_TABLES or not changes or not set(changes) <= {
                    "date", "period", "fiscal_year"}:
                raise ValueError("reviewed identity update cannot change financial fields")
            identity = table, symbol, old_date
            if identity in seen:
                raise ValueError("duplicate reviewed identity update")
            seen.add(identity)
            rows = [r for r in store._get_rows(table, symbol, limit=0) if r["date"] == old_date]
            if len(rows) != 1 or fiscal_group_hash(rows) != plan["row_hash"]:
                raise ValueError("current row hash differs from reviewed identity update")
            updated = {**rows[0], **changes}
            if date.fromisoformat(updated["date"]).isoformat() != updated["date"]:
                raise ValueError("invalid reviewed date")
            if _fiscal_key(updated) is None:
                raise ValueError("reviewed identity must have a fiscal year and quarter")
            pending.append((plan, rows[0]))

        operation_id = hashlib.sha256(_json(mappings).encode("utf-8")).hexdigest()
        for plan, old in pending:
            _archive(conn, plan["table"], [old], operation_id,
                     "reviewed_identity_update", {"plan": plan})
            columns = sorted(plan["changes"])
            cursor = conn.execute("UPDATE {} SET {} WHERE symbol = ? AND date = ?".format(
                plan["table"], ", ".join(column + " = ?" for column in columns)),
                [plan["changes"][column] for column in columns] + [plan["symbol"], plan["date"]])
            if cursor.rowcount != 1:
                raise ValueError("reviewed identity update did not change exactly one row")
            result["rows_updated"] += 1

        for table, symbol in {(p["table"], p["symbol"]) for p, _ in pending}:
            keys = [_fiscal_key(r) for r in store._get_rows(table, symbol, limit=0)]
            known = [key for key in keys if key is not None]
            if len(known) != len(set(known)):
                raise ValueError("reviewed update leaves duplicate fiscal quarters")
        for symbol in sorted({plan["symbol"] for plan, _ in pending}):
            result["metrics_rows"] += _recompute_metrics_in_conn(store, conn, symbol, operation_id)
    return result
