"""Independent physical-source checks for reviewed PIT security exclusions.

This module shares only the evidence-file loader with the producer. It never
calls the correction matcher, application function, or valuation builder.
"""
import json
import math
import sqlite3
from datetime import date, datetime, timezone
from collections.abc import Mapping

from src.data.security_source_corrections import load_security_source_corrections


def _object(value):
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, Mapping):
        raise ValueError("expected object")
    return value


def _query(conn, sql, params):
    cursor = conn.execute(sql, params)
    columns = [item[0] for item in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _same_number(left, right):
    if isinstance(left, bool) or isinstance(right, bool):
        return False
    try:
        values = float(left), float(right)
    except (TypeError, ValueError):
        return False
    return all(math.isfinite(value) and value >= 0 for value in values) and values[0] == values[1]


def verify_pit_security_corrections(conn, snapshot_date, rows, config_dir):
    """Return evidence/exclusion errors without writing or trusting producer output."""
    records = load_security_source_corrections(config_dir)
    scoped = [record for record in records if record["action"] == "classify_cvr"
              and snapshot_date in record["required_snapshot_dates"]]
    errors, products, authorized = [], {}, {}
    scoped_baskets = {record["basket"] for record in scoped}
    for row in rows:
        basket = row.get("basket")
        try:
            payload = _object(row.get("members_json"))
        except (TypeError, ValueError):
            if basket in scoped_baskets:
                errors.append(f"{basket}:pit_correction_product_payload_invalid")
            continue
        products.setdefault(basket, []).append((row, payload))

    for record in scoped:
        basket, asset = record["basket"], record["raw_symbol"]
        prefix = f"{basket}:{snapshot_date}:{record['id']}:pit_correction"
        try:
            sources = _query(conn,
                "SELECT * FROM fmp_fund_disclosure_holdings WHERE basket_symbol=? "
                "AND holding_date=? AND source_kind='live'", [basket, snapshot_date])
            candidates = []
            for source in sources:
                try:
                    raw = _object(source.get("raw_payload_json"))
                except (TypeError, ValueError):
                    raw = {}
                if source.get("raw_symbol") == asset or raw.get("asset") == asset:
                    candidates.append((source, raw))
            if len(candidates) != 1:
                raise ValueError(f"physical_source_count:{len(candidates)}")
            source, raw = candidates[0]
            cusip = raw.get("securityCusip") or raw.get("cusip")
            if (raw.get("securityCusip") and raw.get("cusip")
                    and raw["securityCusip"] != raw["cusip"]):
                raise ValueError("physical_cusip_conflict")
            if "symbol" in raw and raw["symbol"] != basket:
                raise ValueError("physical_raw_basket_mismatch")
            expected_identity = (asset, record["raw_name"], record["raw_cusip"], record["raw_isin"])
            if (raw.get("asset"), raw.get("name"), cusip, raw.get("isin")) != expected_identity:
                raise ValueError("physical_raw_identity_mismatch")
            normalized = {"raw_symbol": raw.get("asset"), "name": raw.get("name"),
                          "cusip": cusip, "isin": raw.get("isin"),
                          "issuer_lei": raw.get("lei"), "asset_category": raw.get("assetCat")}
            if any(source.get(field) != value for field, value in normalized.items()):
                raise ValueError("physical_normalized_identity_mismatch")
            if (not _same_number(source.get("weight_pct"), raw.get("weightPercentage"))
                    or not _same_number(source.get("market_value"), raw.get("marketValue"))):
                raise ValueError("physical_raw_numbers_mismatch")
            day = date.fromisoformat(snapshot_date)
            available = date.fromisoformat(source["composition_available_date"])
            fetched = datetime.fromisoformat(source["fetched_at"].replace("Z", "+00:00"))
            if fetched.tzinfo is None:
                raise ValueError('physical_source_timestamp_timezone_missing')
            if available > day or fetched.astimezone(timezone.utc).date() != day:
                raise ValueError("physical_source_not_available_at_snapshot")
            expected_pit = {"raw_asset": raw["asset"], "name": raw["name"],
                            "weight_pct": raw["weightPercentage"], "market_value": raw["marketValue"],
                            "updated_at": raw["updatedAt"]}
            holdings = _query(conn,
                "SELECT * FROM fmp_etf_holdings_snapshot WHERE basket=? AND snapshot_date=?",
                [basket, snapshot_date])
            matches = [holding for holding in holdings if holding.get("raw_asset") == asset]
            if len(matches) != 1:
                raise ValueError(f"pit_source_count:{len(matches)}")
            holding = matches[0]
            if any(holding.get(field) != value for field, value in expected_pit.items()):
                raise ValueError("pit_source_join_mismatch")
            if not isinstance(raw["updatedAt"], str) or not raw["updatedAt"]:
                raise ValueError("pit_source_timestamp_missing")
            expected_exclusion = {"correction_id": record["id"], "raw_asset": asset,
                                  "name": raw["name"], "weight_pct": raw["weightPercentage"],
                                  "valuation_filter_reason": "reviewed_cvr"}
            authorized[(basket, record["id"])] = expected_exclusion
            valuations = products.get(basket, [])
            if len(valuations) != 1:
                raise ValueError(f"product_count:{len(valuations)}")
            row, payload = valuations[0]
            if row.get("snapshot_date", snapshot_date) != snapshot_date:
                raise ValueError("product_snapshot_date_mismatch")
            exclusions = payload.get("non_equity_exclusions", [])
            if not isinstance(exclusions, list):
                raise ValueError("exclusions_shape")
            excluded = [item for item in exclusions if isinstance(item, Mapping)
                        and item.get("correction_id") == record["id"]]
            if len(excluded) != 1 or any(excluded[0].get(field) != value
                                        for field, value in expected_exclusion.items()):
                raise ValueError("exclusion_mismatch_or_count")
            members = payload.get("members")
            if not isinstance(members, list):
                raise ValueError("members_shape")
            targets = {value for entry in (source, holding) for field in ("symbol", "covered_by")
                       if (value := entry.get(field))}
            targets.add(asset)
            for member in members:
                if not isinstance(member, Mapping):
                    raise ValueError("member_shape")
                names = [member.get(field) for field in ("raw_asset", "raw_symbol", "symbol")]
                if any(name in targets or isinstance(name, str) and name.startswith("unmapped:")
                       and name.rsplit(":", 1)[-1] == asset for name in names):
                    raise ValueError("cvr_still_in_equity_members")
        except (KeyError, TypeError, ValueError, sqlite3.Error) as exc:
            errors.append(f"{prefix}:{exc}")

    # Catch fabricated exclusions even outside the reviewed date/basket, where
    # no source queries or additional historical requirements are appropriate.
    for basket, valuations in products.items():
        for _, payload in valuations:
            exclusions = payload.get("non_equity_exclusions", [])
            if not isinstance(exclusions, list):
                continue
            for exclusion in exclusions:
                if not isinstance(exclusion, Mapping) or exclusion.get("valuation_filter_reason") != "reviewed_cvr":
                    continue
                identifier = exclusion.get("correction_id")
                expected = (authorized.get((basket, identifier))
                            if isinstance(identifier, str) else None)
                if expected is None or any(exclusion.get(field) != value for field, value in expected.items()):
                    errors.append(f"{basket}:{snapshot_date}:pit_correction_unsupported_exclusion")
    return errors
