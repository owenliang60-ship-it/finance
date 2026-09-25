"""N-PORT issuer identities. Fund CIK is never an issuer-identity fallback."""
import json
import re
from collections import defaultdict
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

MISSING_CUSIPS = (None, "", "N/A", "000000000")
MISSING_LEIS = (None, "", "N/A")


def issuer_record_key(record):
    return record.get("canonical_issuer_key") or "lei:" + record["issuer_lei"]


def valid_issuer_key(value):
    if not isinstance(value, str):
        return False
    if value.startswith("lei:"):
        return valid_issuer_lei(value[4:]) is not None
    return bool(re.fullmatch(r"sec-cik:[0-9]{10}", value) and int(value[8:]) > 0)


def canonical_lei_key(lei, overrides, day):
    keys = {issuer_record_key(r) for r in overrides
            if r["valid_from"] <= day <= r["valid_to"]
            and (lei == r.get("issuer_lei") or lei in r.get("equivalent_leis", []))}
    return next(iter(keys)) if len(keys) == 1 else ("lei:" + lei if not keys else None)


def issuer_record_match(record, cusip, isin, day):
    """Return match/conflict/none; a conflicting valid CUSIP is not ignored."""
    if record["isin"] != isin or not record["valid_from"] <= day <= record["valid_to"]:
        return "none"
    if record.get("cusip") == cusip and cusip not in MISSING_CUSIPS:
        return "match"
    if record.get("match_mode") == "isin_allow_missing_cusip" and cusip in record["missing_cusip_values"]:
        return "match"
    return "conflict"


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
        key = row.get("canonical_issuer_key")
        if key is None and valid_issuer_lei(row.get("issuer_lei")):
            key = "lei:" + row["issuer_lei"]
        mode = row.get("match_mode", "cusip_isin")
        missing = row.get("missing_cusip_values", [])
        if mode not in ("cusip_isin", "isin_allow_missing_cusip"):
            raise ValueError("unknown issuer security match mode")
        if mode == "isin_allow_missing_cusip" and (
                not isinstance(missing, list) or not missing
                or any(v not in MISSING_CUSIPS for v in missing)):
            raise ValueError("ISIN-only mode requires explicit missing CUSIP values")
        valid_cusip = bool(re.fullmatch(r"[A-Z0-9]{9}", str(row.get("cusip", "")))
                           and row["cusip"] != "000000000")
        if (not valid_issuer_key(key)
                or (row.get("issuer_lei") is not None and not valid_issuer_lei(row["issuer_lei"]))
                or (key.startswith("lei:") and row.get("issuer_lei") not in (None, key[4:]))
                or not (valid_cusip or (mode == "isin_allow_missing_cusip" and row.get("cusip") is None))
                or not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]", str(row.get("isin", "")))
                or not str(row.get("source_url", "")).startswith("https://")
                or not re.fullmatch(r"[a-f0-9]{64}", str(row.get("source_sha256", "")))
                or not row.get("reason")):
            raise ValueError("issuer override requires exact security and reviewed source evidence")
        aliases = row.get("equivalent_leis", [])
        corrections = row.get("expected_raw_leis", [])
        if (not isinstance(aliases, list) or any(not valid_issuer_lei(v) for v in aliases)
                or not isinstance(corrections, list)
                or any(not isinstance(v, str) or not v or v in MISSING_LEIS for v in corrections)
                or set(aliases) & set(corrections)
                or row.get("issuer_lei") in corrections):
            raise ValueError("issuer aliases and scoped corrections must be separate")
        if key.startswith("sec-cik:") and (
                row.get("sec_role") != "issuer"
                or urlparse(str(row.get("sec_issuer_url", ""))).hostname not in ("www.sec.gov", "data.sec.gov", "sec.gov")
                or not str(row["sec_issuer_url"]).startswith("https://")
                or "equivalent_leis" not in row
                or (not aliases and not row.get("issuer_lei") and row.get("lei_search_reviewed") is not True)):
            raise ValueError("SEC issuer key requires issuer-role evidence and reviewed LEI equivalence")
        try:
            start, end = date.fromisoformat(row["valid_from"]), date.fromisoformat(row["valid_to"])
            date.fromisoformat(row["reviewed_at"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("issuer override requires explicit validity and review dates") from exc
        if start > end:
            raise ValueError("issuer override validity is reversed")
    for index, first in enumerate(records):
        first_leis = set(first.get("equivalent_leis", [])) | ({first["issuer_lei"]} if first.get("issuer_lei") else set())
        for second in records[index + 1:]:
            second_leis = set(second.get("equivalent_leis", [])) | ({second["issuer_lei"]} if second.get("issuer_lei") else set())
            if (first_leis & second_leis and issuer_record_key(first) != issuer_record_key(second)
                    and max(first["valid_from"], second["valid_from"]) <= min(first["valid_to"], second["valid_to"])):
                raise ValueError("overlapping issuer aliases disagree on canonical identity")
    return records


def disclosure_security_issuers(rows, overrides=()):
    """Inventory every snapshot; parse only those selected by live rows.

    The cache belongs to this immutable source/override context, not to the
    process. An absent recent security never revives an older snapshot.
    """
    snapshots = {}
    overrides = tuple(overrides)
    for row in rows:
        if row.get("source_kind") != "disclosure":
            continue
        snapshot = snapshots.setdefault((row["basket_symbol"], row["holding_date"]), {
            "composition_available_date": row["composition_available_date"],
            "rows": [], "overrides": overrides, "by_as_of": {},
        })
        snapshot["composition_available_date"] = max(
            snapshot["composition_available_date"], row["composition_available_date"])
        snapshot["rows"].append(row)
    return snapshots


def _disclosure_securities_as_of(snapshot, as_of):
    if as_of in snapshot["by_as_of"]:
        return snapshot["by_as_of"][as_of]
    overrides = snapshot["overrides"]
    if "original_evidence" not in snapshot:
        groups = defaultdict(list)
        for row in snapshot["rows"]:
            if row.get("cusip") not in MISSING_CUSIPS:
                key, reason = resolve_issuer_identity(row, overrides)
                groups[row["cusip"]].append((row, key, reason))
        snapshot["original_evidence"] = groups
    active = tuple(r for r in overrides if r["valid_from"] <= as_of <= r["valid_to"])
    securities = {}
    for cusip, entries in snapshot["original_evidence"].items():
        keys = {key for _, key, _ in entries if key is not None}
        isins = {row["isin"] for row, _, _ in entries if row.get("isin")}
        isin = next(iter(isins)) if len(isins) == 1 else None
        conflict = (len(keys) > 1 or len(isins) > 1 or any(reason in (
            "issuer_security_conflict", "issuer_evidence_conflict") for _, _, reason in entries))
        key = next(iter(keys)) if len(keys) == 1 else None
        if conflict:
            status = "conflict"
        elif key is None or any(original is None for _, original, _ in entries):
            status = "unresolved"
        elif active != overrides and any(
                resolve_issuer_identity(row, active)[0] != key for row, _, _ in entries):
            # Evaluate the original security/date again with still-live proof.
            # Never expose a corrected wrong raw LEI when its override expires.
            status = "unresolved"
        else:
            status = "resolved"
        securities[cusip] = (key if status == "resolved" else None, isin, status)
    snapshot["by_as_of"][as_of] = securities
    return securities


def latest_pit_disclosure(security_issuers, basket_symbol, as_of):
    eligible = [key for key, snapshot in security_issuers.items()
                if key[0] == basket_symbol and key[1] <= as_of
                and snapshot["composition_available_date"] <= as_of]
    return max(eligible, key=lambda key: key[1]) if eligible else None


def resolve_issuer_identity(row, overrides=(), security_issuers=None):
    """Resolve normalized source columns, rejecting inconsistent raw evidence.

    Returns (typed issuer key or None, reason). Reviewed corrections are scoped
    to exact security, date and expected wrong value; the raw source is retained.
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
    matches = [(r, issuer_record_match(r, row.get("cusip"), row.get("isin"), row["holding_date"])) for r in overrides]
    if any(status == "conflict" for _, status in matches):
        return None, "issuer_security_conflict"
    matching = [r for r, status in matches if status == "match"]
    candidates = {issuer_record_key(r) for r in matching}
    if any(key == "sec-cik:" + str(row.get("cik")) for key in candidates):
        return None, "filer_cik_is_not_issuer"
    corrected = source_lei not in MISSING_LEIS and matching and all(
        source_lei in r.get("expected_raw_leis", []) for r in matching)
    if source_lei not in MISSING_LEIS and not corrected:
        if not valid_issuer_lei(source_lei):
            return None, "issuer_lei_invalid"
        key = canonical_lei_key(source_lei, overrides, row["holding_date"])
        if key is None:
            return None, "issuer_evidence_conflict"
        candidates.add(key)
    inherited = False
    if is_live and security_issuers is not None and row.get("cusip") not in MISSING_CUSIPS:
        snapshot_key = latest_pit_disclosure(
            security_issuers, row.get("basket_symbol"), row["holding_date"])
        evidence = (_disclosure_securities_as_of(
                        security_issuers[snapshot_key], row["holding_date"]).get(row["cusip"])
                    if snapshot_key is not None else None)
        if evidence is not None:
            key, isin, status = evidence
            if status == "conflict" or (isin and row.get("isin") and isin != row["isin"]):
                return None, "issuer_evidence_conflict"
            if status == "resolved":
                inherited = not candidates
                candidates.add(key)
    if "sec-cik:" + str(row.get("cik")) in candidates:
        return None, "filer_cik_is_not_issuer"
    if len(candidates) > 1:
        return None, "issuer_evidence_conflict"
    if not candidates:
        return None, "issuer_identity_unresolved"
    reason = ("disclosure_security_issuer" if inherited else
              "reviewed_security_correction" if corrected else
              "disclosed_lei" if valid_issuer_lei(source_lei) else "reviewed_security")
    return next(iter(candidates)), reason


def audit_snapshot_identities(rows, overrides=(), source_rows=None):
    """Pre-company-API gate, with each physical source snapshot kept separate."""
    errors, resolved = [], []
    groups = defaultdict(lambda: defaultdict(set))
    security_issuers = (disclosure_security_issuers(source_rows, overrides)
                       if source_rows is not None else None)
    for row in rows:
        key = (row.get("basket_symbol"), row["holding_date"], row["source_kind"])
        label = f"{key}:{row.get('raw_row_index')}:{row.get('raw_symbol')}"
        if row.get("filter_reason") == "unrecognized_asset_category":
            errors.append(f"{label}:unrecognized_asset_category")
        if not row.get("included") and not row.get("covered_by"):
            continue
        issuer_key, reason = resolve_issuer_identity(row, overrides, security_issuers)
        if issuer_key is None:
            errors.append(f"{label}:{reason}")
            continue
        target = row.get("covered_by") or (
            row.get("alias_symbol") if row.get("alias_mode") == "authoritative" else row.get("symbol"))
        if not target:
            errors.append(f"{label}:equity_target_missing")
            continue
        groups[key][target].add(issuer_key)
        resolved.append({"snapshot": key, "raw_row_index": row.get("raw_row_index"),
                         "symbol": target, "canonical_issuer_key": issuer_key,
                         "issuer_lei": issuer_key[4:] if issuer_key.startswith("lei:") else None,
                         "reason": reason})
        if reason == "disclosure_security_issuer":
            resolved[-1]["evidence_disclosure_date"] = latest_pit_disclosure(
                security_issuers, row.get("basket_symbol"), row["holding_date"])[1]
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
