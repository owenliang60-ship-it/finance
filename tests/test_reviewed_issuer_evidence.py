"""Curated identity evidence is exact, bounded and backed by frozen sources."""
import hashlib
import json
from pathlib import Path

from src.data.fund_issuer_identity import load_issuer_overrides, resolve_issuer_identity

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {"AAL", "ACN", "APA", "CEG", "CMG", "INVH", "KMX", "MRVL", "NEE", "RIVN", "SNDK", "VNT"}


def test_twelve_reviewed_securities_have_replayable_primary_evidence():
    rows = load_issuer_overrides(ROOT / "config/baskets")
    assert {r["symbol"] for r in rows} == EXPECTED
    for row in rows:
        path = ROOT / row["source_path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == row["source_sha256"]
        evidence = json.loads(path.read_text())
        assert evidence["security"]["cusip"] == row["cusip"]
        assert evidence["security"]["isin"] == row["isin"]
        data = evidence["registry"]["response"]["data"]
        data = data if isinstance(data, list) else [data]
        assert len(data) == 1 and data[0]["id"] == row["issuer_lei"]
        assert evidence["review"]["identity_kind"] == "retrospective_entity_mapping"
        assert row["valid_from"] <= row["valid_to"] < row["reviewed_at"]


def test_reviewed_source_fills_only_exact_missing_identity_without_rewriting_raw():
    rows = load_issuer_overrides(ROOT / "config/baskets")
    assert len(rows) == 12
    for row in rows:
        raw = {"symbol": row["symbol"], "cusip": row["cusip"], "isin": row["isin"],
               "lei": "N/A", "assetCat": "EC"}
        source = {"raw_symbol": row["symbol"], "cusip": row["cusip"], "isin": row["isin"],
                  "issuer_lei": "N/A", "asset_category": "EC", "raw_payload_json": raw,
                  "holding_date": row["valid_from"], "source_kind": "disclosure"}
        assert resolve_issuer_identity(source, rows) == (row["issuer_lei"], "reviewed_security")
        assert source["issuer_lei"] == raw["lei"] == "N/A"
        source["holding_date"] = "2099-01-01"
        assert resolve_issuer_identity(source, rows)[0] is None
