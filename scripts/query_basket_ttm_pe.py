#!/usr/bin/env python3
"""Read-only query and deterministic export for historical basket TTM PE."""
import argparse
import csv
import json
import sqlite3
import statistics
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence


PROJECT_ROOT = Path(__file__).parent.parent
BANNER = (
    "SOXX rebalance-weighted GAAP TTM PE proxy; fixed retrospective "
    "snapshot weights; not official SOXX PE or historical forward PE."
)
PE_FIELDS = (
    "rebalance_weighted_ttm_pe_gaap_proxy",
    "uncapped_mcap_basket_pe_gaap",
)
CSV_FIELDS = (
    "basket_symbol", "valuation_date", "holding_date",
    "composition_effective_date", "composition_available_date",
    "is_ex_post_composition", "weight_basis", "data_quality_tier",
    "is_observed_weight_date", "rebalance_weighted_ttm_pe_gaap_proxy",
    "uncapped_mcap_basket_pe_gaap", "weight_coverage",
    "mcap_weight_coverage", "income_weight_coverage", "fx_weight_coverage",
    "member_count", "covered_count", "warnings_json", "mcap_sanity_json",
)


def _iso_date(value: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError(f"not an ISO date: {value!r}") from exc


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=BANNER)
    parser.add_argument("--basket", default="SOXX")
    parser.add_argument("--as-of", type=_iso_date)
    parser.add_argument("--from-date", type=_iso_date)
    parser.add_argument("--to-date", type=_iso_date)
    parser.add_argument("--csv", type=Path)
    parser.add_argument("--markdown", type=Path)
    parser.add_argument("--db", type=Path,
                        default=PROJECT_ROOT / "data" / "market.db")
    args = parser.parse_args(argv)
    if args.from_date and args.to_date and args.from_date > args.to_date:
        parser.error("--from-date must be on or before --to-date")
    return args


def connect_readonly(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def percentile_at_or_below(values: Sequence[float], current: float) -> float:
    if not values:
        raise ValueError("percentile requires at least one observation")
    return sum(value <= current for value in values) / len(values) * 100.0


def _stats(rows: Sequence[Mapping[str, Any]], field: str) -> Dict[str, Any]:
    observed = [(str(row["valuation_date"]), float(row[field]))
                for row in rows if row.get(field) is not None]
    if not observed:
        return {"count": 0, "current": None, "percentile": None,
                "min": None, "median": None, "max": None,
                "min_date": None, "max_date": None}
    values = [value for _, value in observed]
    current_date, current = observed[-1]
    min_date, minimum = min(observed, key=lambda item: (item[1], item[0]))
    max_date, maximum = max(observed, key=lambda item: (item[1], item[0]))
    return {
        "count": len(values), "current": current,
        "current_date": current_date,
        "percentile": percentile_at_or_below(values, current),
        "min": minimum, "min_date": min_date,
        "median": statistics.median(values),
        "max": maximum, "max_date": max_date,
    }


def load_rows(
    conn: sqlite3.Connection,
    basket: str,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    as_of: Optional[str] = None,
) -> List[Dict[str, Any]]:
    query = "SELECT * FROM basket_ttm_valuation WHERE basket_symbol = ?"
    params: List[Any] = [basket.upper()]
    if from_date:
        query += " AND valuation_date >= ?"
        params.append(from_date)
    upper = min(value for value in (to_date, as_of) if value is not None) \
        if to_date or as_of else None
    if upper:
        query += " AND valuation_date <= ?"
        params.append(upper)
    query += " ORDER BY valuation_date"
    return [dict(row) for row in conn.execute(query, params).fetchall()]


def build_result(rows: Sequence[Mapping[str, Any]], basket: str) -> Dict[str, Any]:
    if not rows:
        raise ValueError("no basket valuation rows in requested range")
    decoded = []
    for row in rows:
        item = dict(row)
        for field in ("warnings_json", "mcap_sanity_json", "members_json"):
            try:
                item[field] = json.loads(str(item.get(field, "[]")))
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"invalid {field} on {item.get('valuation_date')}") from exc
        decoded.append(item)
    current = decoded[-1]
    gaps = [row["valuation_date"] for row in decoded
            if row.get("rebalance_weighted_ttm_pe_gaap_proxy") is None]
    warnings = sorted({str(warning) for row in decoded
                       for warning in row["warnings_json"]})
    sanity_events: Dict[tuple, Dict[str, Any]] = {}
    for row in decoded:
        for event in row["mcap_sanity_json"]:
            key = (event.get("symbol"), event.get("date"), event.get("status"))
            sanity_events[key] = dict(event)
    ordered_sanity = [sanity_events[key] for key in sorted(
        sanity_events, key=lambda item: tuple(str(value or "") for value in item))]
    quarantines = [event for event in ordered_sanity
                   if event.get("quarantined") or event.get("status") in {
                       "invalid_mcap", "unresolved"}]
    live_dates = [row["valuation_date"] for row in decoded
                  if "live" in str(row["data_quality_tier"])]
    anchors = [{
        "valuation_date": row["valuation_date"],
        "holding_date": row["holding_date"],
        "primary_pe": row.get("rebalance_weighted_ttm_pe_gaap_proxy"),
        "uncapped_pe": row.get("uncapped_mcap_basket_pe_gaap"),
        "weight_coverage": row["weight_coverage"],
        "data_quality_tier": row["data_quality_tier"],
    } for row in decoded if int(row["is_observed_weight_date"]) == 1]
    return {
        "banner": BANNER.replace("SOXX", basket.upper(), 1),
        "basket": basket.upper(),
        "date_range": [decoded[0]["valuation_date"], decoded[-1]["valuation_date"]],
        "row_count": len(decoded),
        "current": {
            "valuation_date": current["valuation_date"],
            "holding_date": current["holding_date"],
            "composition_effective_date": current["composition_effective_date"],
            "composition_available_date": current["composition_available_date"],
            "data_quality_tier": current["data_quality_tier"],
            "weight_coverage": current["weight_coverage"],
        },
        "primary": _stats(decoded, PE_FIELDS[0]),
        "secondary": _stats(decoded, PE_FIELDS[1]),
        "observed_weight_anchors": anchors,
        "quality": {
            "publishable_count": len(decoded) - len(gaps),
            "publishable_pct": (len(decoded) - len(gaps)) / len(decoded) * 100.0,
            "gap_dates": gaps,
            "warnings": warnings,
            "quality_tiers": sorted({row["data_quality_tier"] for row in decoded}),
            "live_tail_range": ([min(live_dates), max(live_dates)]
                                if live_dates else None),
            "market_cap_sanity_events": ordered_sanity,
            "quarantine_gaps": quarantines,
        },
        "methodology_caveats": [
            "Historical disclosure weights are fixed retrospective proxies, not official daily index weights.",
            "FMP financial statements are filtered by accepted date but may contain later restatements; this is not a vintage fundamentals database.",
            "Live-tail weights are fetch-date drifted snapshots and carry weaker evidence.",
        ],
    }


def write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in CSV_FIELDS})


def _fmt(value: Any) -> str:
    return "N/A" if value is None else f"{float(value):.2f}"


def write_markdown(path: Path, result: Mapping[str, Any]) -> None:
    primary = result["primary"]
    secondary = result["secondary"]
    lines = [
        f"# {result['basket']} historical GAAP TTM PE proxy",
        "", f"> {result['banner']}", "",
        f"- Range: {result['date_range'][0]} to {result['date_range'][1]}",
        f"- Rows: {result['row_count']}",
        f"- Publishable: {result['quality']['publishable_pct']:.2f}%",
        f"- Live-tail range: {result['quality']['live_tail_range'] or 'None'}",
        "",
        "| Metric | Current | Percentile | Min | Median | Max |",
        "|---|---:|---:|---:|---:|---:|",
        ("| Rebalance-weighted proxy | "
         f"{_fmt(primary['current'])} | {_fmt(primary['percentile'])}% | "
         f"{_fmt(primary['min'])} | {_fmt(primary['median'])} | "
         f"{_fmt(primary['max'])} |"),
        ("| Uncapped mcap basket | "
         f"{_fmt(secondary['current'])} | {_fmt(secondary['percentile'])}% | "
         f"{_fmt(secondary['min'])} | {_fmt(secondary['median'])} | "
         f"{_fmt(secondary['max'])} |"),
        "", "## Observed weight anchors", "",
        "| Holding date | Trading anchor | Primary PE | Coverage | Quality |",
        "|---|---|---:|---:|---|",
    ]
    for row in result["observed_weight_anchors"]:
        lines.append(
            f"| {row['holding_date']} | {row['valuation_date']} | "
            f"{_fmt(row['primary_pe'])} | {row['weight_coverage']:.2%} | "
            f"{row['data_quality_tier']} |")
    lines.extend(["", "## Market-cap sanity", "",
                  f"- Flagged events: {len(result['quality']['market_cap_sanity_events'])}",
                  f"- Quarantine gaps: {len(result['quality']['quarantine_gaps'])}",
                  "", "## Methodology caveats", ""])
    lines.extend(f"- {value}" for value in result["methodology_caveats"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    conn: Optional[sqlite3.Connection] = None
    try:
        conn = connect_readonly(args.db)
        rows = load_rows(
            conn, args.basket, args.from_date, args.to_date, args.as_of)
        result = build_result(rows, args.basket)
        if args.csv:
            write_csv(args.csv, rows)
        if args.markdown:
            write_markdown(args.markdown, result)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (FileNotFoundError, sqlite3.Error, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
