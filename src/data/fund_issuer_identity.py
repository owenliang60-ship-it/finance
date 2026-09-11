"""N-PORT issuer identities. Fund CIK is never an issuer-identity fallback."""
import json
import re
from collections import defaultdict
from datetime import date
from pathlib import Path


def valid_issuer_lei(value):
    """ISO 17442 shape plus ISO 7064 MOD 97-10 check digits."""
    if (not isinstance(value, str) or not re.fullmatch(r"[A-Z0-9]{18}[0-9]{2}", value)
            or value[:18] == "0" * 18):
        return None
    numeric = "".join(c if c.isdigit() else str(ord(c) - 55) for c in value)
    return value if int(numeric) % 97 == 1 else None


def load_issuer_overrides(config_dir):
    path = Path(config_dir) / "issuer_identity_overrides.json"
    if not path.exists():
        return []  # No exceptions: missing identities still fail closed.
    records = json.loads(path.read_text())
    if not isinstance(records, list):
        raise ValueError("issuer identity overrides must be a list")
    for row in records:
        if not isinstance(row, dict):
            raise ValueError("invalid issuer identity override")
        if (not valid_issuer_lei(row.get("issuer_lei"))
                or not re.fullmatch(r"[A-Z0-9]{9}", str(row.get("cusip", "")))
                or not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]", str(row.get("isin", "")))
                or row["cusip"] == "000000000"
                or not str(row.get("source_url", "")).startswith("https://")
                or not re.fullmatch(r"[a-f0-9]{64}", str(row.get("source_sha256", "")))
                or not row.get("reason")):
            raise ValueError("issuer override requires exact security and reviewed source evidence")
        try:
            start, end = date.fromisoformat(row["valid_from"]), date.fromisoformat(row["valid_to"])
            date.fromisoformat(row["reviewed_at"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("issuer override requires explicit validity and review dates") from exc
        if start > end:
            raise ValueError("issuer override validity is reversed")
    return records


def resolve_issuer_identity(row, overrides=()):
    """Resolve normalized source columns, rejecting inconsistent raw evidence.

    Returns (LEI or None, reason). An override may fill missing LEI but may
    never replace a different or malformed nonempty source value.
    """
    raw = row.get("raw_payload_json")
    try:
        raw = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return None, "raw_identity_payload_invalid"
    if not isinstance(raw, dict):
        return None, "raw_identity_payload_missing"
    is_live = row.get("source_kind") == "live"
    raw_symbol = str(raw.get("asset" if is_live else "symbol") or "").strip().upper()
    if row.get("raw_symbol") != raw_symbol:
        return None, "source_identity_field_mismatch:raw_symbol"
    if not is_live and raw.get("assetCat") != "EC":
        return None, "source_not_common_equity"
    raw_cusip = (raw.get("securityCusip") or raw.get("cusip")) if is_live else raw.get("cusip")
    if is_live and raw.get("securityCusip") and raw.get("cusip") != raw_cusip and raw.get("cusip"):
        return None, "raw_cusip_conflict"
    for column, original in (("issuer_lei", raw.get("lei")), ("cusip", raw_cusip),
                             ("isin", raw.get("isin")), ("asset_category", raw.get("assetCat"))):
        if row.get(column) != original:
            return None, f"source_identity_field_mismatch:{column}"
    source_lei = row.get("issuer_lei")
    if source_lei not in (None, "", "N/A") and not valid_issuer_lei(source_lei):
        return None, "issuer_lei_invalid"
    matching = [r for r in overrides if r["cusip"] == row.get("cusip")
                and r["isin"] == row.get("isin")
                and r["valid_from"] <= row["holding_date"] <= r["valid_to"]]
    candidates = {r["issuer_lei"] for r in matching}
    if valid_issuer_lei(source_lei):
        candidates.add(source_lei)
    if len(candidates) > 1:
        return None, "issuer_evidence_conflict"
    if not candidates:
        return None, "issuer_identity_unresolved"
    return next(iter(candidates)), "disclosed_lei" if valid_issuer_lei(source_lei) else "reviewed_security"


def audit_snapshot_identities(rows, overrides=()):
    """Pre-company-API gate, with each physical source snapshot kept separate."""
    errors, resolved = [], []
    groups = defaultdict(lambda: defaultdict(set))
    for row in rows:
        key = (row.get("basket_symbol"), row["holding_date"], row["source_kind"])
        label = f"{key}:{row.get('raw_row_index')}:{row.get('raw_symbol')}"
        if row.get("filter_reason") == "unrecognized_asset_category":
            errors.append(f"{label}:unrecognized_asset_category")
        if not row.get("included") and not row.get("covered_by"):
            continue
        lei, reason = resolve_issuer_identity(row, overrides)
        if lei is None:
            errors.append(f"{label}:{reason}")
            continue
        target = row.get("covered_by") or (
            row.get("alias_symbol") if row.get("alias_mode") == "authoritative" else row.get("symbol"))
        if not target:
            errors.append(f"{label}:equity_target_missing")
            continue
        groups[key][target].add(lei)
        resolved.append({"snapshot": key, "raw_row_index": row.get("raw_row_index"),
                         "symbol": target, "issuer_lei": lei, "reason": reason})
    for key, targets in groups.items():
        by_issuer = defaultdict(set)
        for target, leis in targets.items():
            if len(leis) > 1:
                errors.append(f"{key}:{target}:share_class_issuer_conflict")
            for lei in leis:
                by_issuer[lei].add(target)
        for lei, symbols in by_issuer.items():
            if len(symbols) > 1:
                errors.append(f"{key}:{lei}:duplicate_issuer:{'+'.join(sorted(symbols))}")
    return {"errors": errors, "resolved": resolved}
