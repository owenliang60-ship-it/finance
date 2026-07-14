#!/usr/bin/env python3
"""Independent read-only verification for historical basket GAAP TTM PE."""
import argparse
import json
import math
import sqlite3
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple


PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from terminal.historical_basket_valuation import (
    compute_member_ttm_income_usd,
    merge_covered_weights,
    select_asof_market_cap,
    select_four_continuous_asof_quarters,
)
from terminal.historical_market_cap_sanity import (
    accepted_market_cap_status,
    scan_market_cap_candidates,
)


EXPECTED_DISCLOSURES = 19
MIN_PUBLISHABLE_COVERAGE = 0.95
MIN_WEIGHT_COVERAGE = 0.90
KNOWN_ANCHORS = {
    "KLAC": ("2026-06-10", "2026-06-23"),
    "MCHP": ("2026-02-02", "2026-02-06"),
}


def _iso_date(value: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError(f"not an ISO date: {value!r}") from exc


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only verification of historical basket TTM PE")
    parser.add_argument("--basket", default="SOXX")
    parser.add_argument("--min-date", type=_iso_date)
    parser.add_argument("--db", type=Path,
                        default=PROJECT_ROOT / "data" / "market.db")
    return parser.parse_args(argv)


def connect_readonly(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _rows(conn: sqlite3.Connection, query: str,
          params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
    return [dict(row) for row in conn.execute(query, params).fetchall()]


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?", [table]
    ).fetchone() is not None


def _check(name: str, passed: bool, detail: Any) -> Dict[str, Any]:
    return {"name": name, "passed": bool(passed), "detail": detail}


def _close(left: Any, right: Any, tolerance: float = 1e-9) -> bool:
    if left is None or right is None:
        return left is None and right is None
    return math.isclose(float(left), float(right), rel_tol=tolerance,
                        abs_tol=tolerance)


def _decode_outputs(rows: Sequence[Mapping[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str]]:
    decoded = []
    errors = []
    for raw in rows:
        row = dict(raw)
        for field in ("members_json", "warnings_json", "mcap_sanity_json"):
            try:
                row[field] = json.loads(str(row[field]))
            except (KeyError, TypeError, ValueError):
                errors.append(f"{row.get('valuation_date')}:{field}")
                row[field] = []
        decoded.append(row)
    return decoded, errors


def _load_sources(
    conn: sqlite3.Connection, basket: str, output_rows: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    holdings = _rows(
        conn, "SELECT * FROM fmp_fund_disclosure_holdings "
        "WHERE basket_symbol = ? ORDER BY holding_date, source_kind, raw_row_index",
        [basket])
    symbols = sorted({
        str(row["symbol"]).upper() for row in holdings
        if row.get("included") == 1 and row.get("symbol")
    } | {
        str(row["covered_by"]).upper() for row in holdings if row.get("covered_by")
    } | {
        str(row["alias_symbol"]).upper() for row in holdings
        if row.get("alias_symbol")
    })
    source: Dict[str, Any] = {
        "holdings": holdings,
        "trading_dates": [row["date"] for row in _rows(
            conn, "SELECT date FROM daily_price WHERE symbol = ? ORDER BY date",
            [basket])],
        "income": {}, "mcap": {}, "price": {}, "splits": {}, "sanity": {},
        "fx": {},
    }
    for symbol in symbols:
        source["income"][symbol] = _rows(
            conn, "SELECT * FROM income_quarterly WHERE symbol = ? ORDER BY date",
            [symbol])
        source["mcap"][symbol] = _rows(
            conn, "SELECT * FROM historical_market_cap WHERE symbol = ? ORDER BY date",
            [symbol])
        source["price"][symbol] = _rows(
            conn, "SELECT * FROM daily_price WHERE symbol = ? ORDER BY date", [symbol])
        source["splits"][symbol] = _rows(
            conn, "SELECT * FROM fmp_stock_splits WHERE symbol = ? ORDER BY date",
            [symbol])
        source["sanity"][symbol] = scan_market_cap_candidates(
            source["mcap"][symbol], source["price"][symbol],
            source["splits"][symbol])
    for row in _rows(conn, "SELECT * FROM fx_daily ORDER BY currency, date"):
        source["fx"].setdefault(row["currency"], []).append(row)
    return source


def _holding_groups(holdings: Sequence[Mapping[str, Any]]) -> List[Tuple[Dict[str, Any], List[Dict[str, Any]]]]:
    grouped: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for row in holdings:
        grouped.setdefault(
            (str(row["holding_date"]), str(row["source_kind"])), []).append(dict(row))
    result = []
    for rows in grouped.values():
        first = rows[0]
        result.append(({
            "holding_date": first["holding_date"],
            "composition_effective_date": first["composition_effective_date"],
            "composition_available_date": first["composition_available_date"],
            "source_kind": first["source_kind"],
        }, sorted(rows, key=lambda row: row["raw_row_index"])))
    priority = {"live": 0, "disclosure": 1}
    return sorted(result, key=lambda item: (
        item[0]["composition_effective_date"],
        priority[item[0]["source_kind"]], item[0]["holding_date"]))


def _recompute_row(
    valuation_date: str, holding_rows: Sequence[Mapping[str, Any]], source: Mapping[str, Any],
) -> Dict[str, Any]:
    weights = merge_covered_weights(holding_rows)
    eligible_weight = float(weights["eligible_weight"])
    metric_members = []
    mcap_weight = income_weight = fx_weight = 0.0
    used_mcap_dates: Dict[str, str] = {}
    aliases = {
        str(row["symbol"]).upper(): {
            "symbol": str(row["alias_symbol"]).upper(),
            "mode": str(row.get("alias_mode") or "fallback"),
            "reason": str(row.get("alias_reason") or "configured corporate alias"),
        }
        for row in holding_rows
        if row.get("included") == 1 and row.get("symbol")
        and row.get("alias_symbol")
    }
    raw_snapshot_warnings = holding_rows[0].get("snapshot_warnings_json", "[]")
    if isinstance(raw_snapshot_warnings, str):
        raw_snapshot_warnings = json.loads(raw_snapshot_warnings)
    expected_warnings = sorted(str(value) for value in (
        list(weights["warnings"]) + list(raw_snapshot_warnings)))
    resolutions: Dict[str, Dict[str, Any]] = {}
    for raw_symbol, weight in sorted(weights["weights"].items()):
        alias = aliases.get(raw_symbol)
        if alias and alias["mode"] == "authoritative":
            candidates = [alias["symbol"]]
        else:
            candidates = [raw_symbol]
        if (alias and alias["mode"] != "authoritative"
                and alias["symbol"] != raw_symbol):
            candidates.append(alias["symbol"])
        evaluated = []
        for candidate in candidates:
            statuses = {str(row["date"]): str(row["status"])
                        for row in source["sanity"].get(candidate, [])}
            quarantine = {day for day, status in statuses.items()
                          if not accepted_market_cap_status(status)}
            candidate_mcap = select_asof_market_cap(
                source["mcap"].get(candidate, []), valuation_date,
                statuses, quarantine)
            candidate_quarters = select_four_continuous_asof_quarters(
                source["income"].get(candidate, []), valuation_date,
                source["trading_dates"])
            candidate_income = (compute_member_ttm_income_usd(
                candidate_quarters, source["fx"], valuation_date)
                if candidate_quarters is not None else None)
            evaluated.append((candidate, candidate_mcap,
                              candidate_quarters, candidate_income))
        symbol, market_cap, quarters, income = next((
            item for item in evaluated if item[1] is not None and item[3] is not None
        ), evaluated[0])
        resolutions[raw_symbol] = {
            "resolved_symbol": symbol,
            "alias_mode": alias["mode"] if alias and symbol != raw_symbol else None,
            "alias_reason": alias["reason"] if alias and symbol != raw_symbol else None,
        }
        if market_cap is not None:
            mcap_weight += weight
            used_mcap_dates[symbol] = market_cap["date"]
        if quarters is not None:
            income_weight += weight
        if income is not None:
            fx_weight += weight
        if market_cap is not None and income is not None:
            metric_members.append({
                "symbol": symbol, "weight": float(weight),
                "market_cap": float(market_cap["market_cap"]),
                "net_income": float(income["ttm_net_income_usd"]),
            })
    covered_weight = sum(row["weight"] for row in metric_members)
    coverage = covered_weight / eligible_weight if eligible_weight else 0.0
    weighted_yield = (sum(
        row["weight"] * row["net_income"] / row["market_cap"]
        for row in metric_members) / covered_weight if covered_weight else None)
    primary = (1.0 / weighted_yield if coverage >= MIN_WEIGHT_COVERAGE
               and weighted_yield is not None and weighted_yield > 0 else None)
    total_mcap = sum(row["market_cap"] for row in metric_members)
    total_income = sum(row["net_income"] for row in metric_members)
    secondary = total_mcap / total_income if total_mcap > 0 and total_income > 0 else None
    return {
        "eligible_weight": eligible_weight, "covered_weight": covered_weight,
        "weight_coverage": coverage, "weighted_earnings_yield": weighted_yield,
        "rebalance_weighted_ttm_pe_gaap_proxy": primary,
        "uncapped_mcap_basket_pe_gaap": secondary,
        "covered_market_cap": total_mcap, "ttm_net_income_usd": total_income,
        "covered_count": len(metric_members),
        "mcap_weight_coverage": mcap_weight / eligible_weight,
        "income_weight_coverage": income_weight / eligible_weight,
        "fx_weight_coverage": fx_weight / eligible_weight,
        "used_mcap_dates": used_mcap_dates,
        "warnings_json": expected_warnings,
        "resolutions": resolutions,
    }


def _asof_evidence_errors(rows: Sequence[Mapping[str, Any]]) -> List[str]:
    errors = []
    for row in rows:
        valuation = date.fromisoformat(str(row["valuation_date"]))
        for member in row["members_json"]:
            mcap_date = member.get("market_cap_date")
            if mcap_date:
                observed = date.fromisoformat(str(mcap_date))
                if observed > valuation or (valuation - observed).days > 7:
                    errors.append(f"{row['valuation_date']}:{member.get('symbol')}:mcap")
            for accepted in member.get("accepted_dates", []):
                if str(accepted)[:10] >= row["valuation_date"]:
                    errors.append(
                        f"{row['valuation_date']}:{member.get('symbol')}:accepted")
            for fx in member.get("fx_evidence", []):
                fx_date = date.fromisoformat(str(fx["fx_date"]))
                if fx_date > valuation or (valuation - fx_date).days > 7:
                    errors.append(f"{row['valuation_date']}:{member.get('symbol')}:fx")
    return errors


def verify_database(
    conn: sqlite3.Connection, basket: str = "SOXX", min_date: Optional[str] = None,
) -> Dict[str, Any]:
    basket = basket.upper()
    required = {
        "fmp_fund_disclosure_holdings", "fx_daily", "fmp_stock_splits",
        "basket_ttm_valuation", "daily_price", "income_quarterly",
        "historical_market_cap",
    }
    missing = sorted(table for table in required if not _table_exists(conn, table))
    if missing:
        raise ValueError(f"required tables missing: {', '.join(missing)}")
    query = "SELECT * FROM basket_ttm_valuation WHERE basket_symbol = ?"
    params: List[Any] = [basket]
    if min_date:
        query += " AND valuation_date >= ?"
        params.append(min_date)
    query += " ORDER BY valuation_date"
    raw_outputs = _rows(conn, query, params)
    if not raw_outputs:
        raise ValueError("no basket valuation output rows")
    outputs, json_errors = _decode_outputs(raw_outputs)
    source = _load_sources(conn, basket, outputs)
    holdings = source["holdings"]
    checks = [_check("read_only_connection", True, "mode=ro + query_only")]

    disclosure_dates = sorted({row["holding_date"] for row in holdings
                               if row["source_kind"] == "disclosure"})
    raw_duplicates = _rows(
        conn, "SELECT basket_symbol, holding_date, source_kind, raw_row_index, COUNT(*) n "
        "FROM fmp_fund_disclosure_holdings WHERE basket_symbol = ? "
        "GROUP BY basket_symbol, holding_date, source_kind, raw_row_index HAVING n > 1",
        [basket])
    checks.append(_check(
        "source_snapshots", len(disclosure_dates) >= EXPECTED_DISCLOSURES
        and not raw_duplicates,
        {"disclosure_count": len(disclosure_dates),
         "range": [disclosure_dates[0], disclosure_dates[-1]] if disclosure_dates else [],
         "raw_duplicates": len(raw_duplicates)}))

    snapshot_quality = []
    for metadata, rows in _holding_groups(holdings):
        eligible = [row for row in rows if row.get("included") == 1
                    or row.get("covered_by")]
        weight = sum(float(row.get("weight_pct") or 0) for row in rows)
        snapshot_quality.append({
            "holding_date": metadata["holding_date"], "members": len(eligible),
            "weight": weight,
            "plausible": 25 <= len(eligible) <= 31 and 99.5 <= weight <= 100.5,
        })
    checks.append(_check("snapshot_plausibility",
                         all(row["plausible"] for row in snapshot_quality),
                         snapshot_quality))

    first = outputs[0]["valuation_date"]
    last = outputs[-1]["valuation_date"]
    expected_start = min_date or first
    expected_end = source["trading_dates"][-1]
    calendar = [day for day in source["trading_dates"]
                if expected_start <= day <= expected_end]
    output_dates = [row["valuation_date"] for row in outputs]
    checks.append(_check("trading_calendar_denominator", output_dates == calendar,
                         {"expected_range": [expected_start, expected_end],
                          "calendar": len(calendar), "outputs": len(output_dates),
                          "missing": sorted(set(calendar) - set(output_dates)),
                          "extra": sorted(set(output_dates) - set(calendar))}))

    published = [row for row in outputs
                 if row.get("rebalance_weighted_ttm_pe_gaap_proxy") is not None]
    publishable_pct = len(published) / len(calendar) if calendar else 0.0
    checks.append(_check("publishable_coverage",
                         bool(calendar and publishable_pct >= MIN_PUBLISHABLE_COVERAGE),
                         {"published": len(published), "calendar": len(calendar),
                          "pct": publishable_pct * 100}))

    coverage_errors = []
    for row in published:
        if float(row["weight_coverage"]) < MIN_WEIGHT_COVERAGE:
            coverage_errors.append(f"{row['valuation_date']}:gate")
        if any(float(row[field]) + 1e-12 < float(row["weight_coverage"])
               for field in ("mcap_weight_coverage", "income_weight_coverage",
                             "fx_weight_coverage")):
            coverage_errors.append(f"{row['valuation_date']}:source_coverage")
    checks.append(_check("coverage_gate_consistency", not coverage_errors,
                         coverage_errors))

    asof_errors = _asof_evidence_errors(outputs)
    checks.append(_check("asof_evidence", not asof_errors, asof_errors))

    groups = _holding_groups(holdings)
    recompute_errors = []
    published_bad_mcap = []
    compare_fields = (
        "eligible_weight", "covered_weight", "weight_coverage",
        "weighted_earnings_yield", "rebalance_weighted_ttm_pe_gaap_proxy",
        "uncapped_mcap_basket_pe_gaap", "covered_market_cap",
        "ttm_net_income_usd", "covered_count", "mcap_weight_coverage",
        "income_weight_coverage", "fx_weight_coverage",
    )
    for row in outputs:
        eligible = [item for item in groups
                    if item[0]["composition_effective_date"] <= row["valuation_date"]]
        if not eligible:
            recompute_errors.append(f"{row['valuation_date']}:no_composition")
            continue
        metadata, holding_rows = eligible[-1]
        if metadata["holding_date"] != row["holding_date"]:
            recompute_errors.append(f"{row['valuation_date']}:holding_date")
            continue
        recalculated = _recompute_row(row["valuation_date"], holding_rows, source)
        for field in compare_fields:
            if not _close(row.get(field), recalculated.get(field)):
                recompute_errors.append(f"{row['valuation_date']}:{field}")
        if sorted(str(value) for value in row["warnings_json"]) != \
                recalculated["warnings_json"]:
            recompute_errors.append(f"{row['valuation_date']}:warnings_json")
        output_members = {
            str(member.get("raw_symbol") or "").upper(): member
            for member in row["members_json"] if member.get("raw_symbol")
        }
        for raw_symbol, expected in recalculated["resolutions"].items():
            observed = output_members.get(raw_symbol)
            if (observed is None
                    or observed.get("resolved_symbol") != expected["resolved_symbol"]
                    or observed.get("alias_mode") != expected["alias_mode"]
                    or observed.get("alias_reason") != expected["alias_reason"]):
                recompute_errors.append(
                    f"{row['valuation_date']}:{raw_symbol}:alias_evidence")
        if row.get("rebalance_weighted_ttm_pe_gaap_proxy") is not None:
            # Check the source dates claimed by the published row against a
            # fresh scan of raw HMC/price/split tables. Recalculation itself
            # excludes bad dates, so only inspecting its accepted members
            # would hide exactly the contamination this check is for.
            for member in row["members_json"]:
                symbol = str(member.get("symbol") or "").upper()
                mcap_date = member.get("market_cap_date")
                if not symbol or not mcap_date:
                    continue
                status = next((item["status"] for item in source["sanity"].get(
                    symbol, []) if item["date"] == mcap_date), None)
                if not accepted_market_cap_status(str(status)):
                    published_bad_mcap.append(
                        f"{row['valuation_date']}:{symbol}:{mcap_date}:{status}")
    checks.append(_check("source_recompute_all_rows", not recompute_errors,
                         {"rows_recomputed": len(outputs), "errors": recompute_errors}))

    output_duplicates = _rows(
        conn, "SELECT basket_symbol, valuation_date, COUNT(*) n "
        "FROM basket_ttm_valuation WHERE basket_symbol = ? "
        "GROUP BY basket_symbol, valuation_date HAVING n > 1", [basket])
    checks.append(_check("duplicates_and_json", not output_duplicates and not json_errors,
                         {"duplicate_pk": len(output_duplicates),
                          "invalid_json": json_errors}))

    drift = {
        "source": [] if len(disclosure_dates) >= EXPECTED_DISCLOSURES else [
            f"disclosures:{len(disclosure_dates)}/{EXPECTED_DISCLOSURES}"],
        "live_tail": sorted({row["data_quality_tier"] for row in outputs
                             if "live" in str(row["data_quality_tier"])}),
        "membership": sorted({str(w) for row in outputs for w in row["warnings_json"]
                              if "membership" in str(w) or "covered_by" in str(w)}),
        "market_cap": sorted({member.get("symbol") for row in outputs
                              for member in row["members_json"]
                              if member.get("exclusion_reason") ==
                              "market_cap_missing_stale_or_quarantined"}),
        "fundamentals": sorted({member.get("symbol") for row in outputs
                                for member in row["members_json"]
                                if member.get("exclusion_reason") ==
                                "four_visible_continuous_quarters_missing"}),
        "fx": sorted({member.get("symbol") for row in outputs
                      for member in row["members_json"]
                      if member.get("exclusion_reason") == "fx_missing_or_stale"}),
        "computation": [row["valuation_date"] for row in outputs
                        if row.get("rebalance_weighted_ttm_pe_gaap_proxy") is None],
    }
    checks.append(_check("drift_classification", True, drift))

    anchors = {}
    for symbol, (start, end) in KNOWN_ANCHORS.items():
        rows = [row for row in source["sanity"].get(symbol, [])
                if start <= row["date"] <= end]
        anchors[symbol] = {
            "range": [start, end], "rows": len(rows),
            "statuses": sorted({row["status"] for row in rows}),
            "accepted": all(accepted_market_cap_status(row["status"]) for row in rows),
        }
    checks.append(_check("market_cap_jump_split_sanity", not published_bad_mcap,
                         {"published_invalid": published_bad_mcap,
                          "known_anchors": anchors}))

    return {
        "basket": basket, "date_range": [first, last],
        "checks": checks, "passed": all(check["passed"] for check in checks),
    }


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    conn: Optional[sqlite3.Connection] = None
    try:
        conn = connect_readonly(args.db)
        report = verify_database(conn, args.basket, args.min_date)
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if report["passed"] else 1
    except (FileNotFoundError, sqlite3.Error, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
