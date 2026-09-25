"""Curated identity evidence is exact, bounded and backed by frozen sources."""
import hashlib
import json
from pathlib import Path

from src.data.fund_issuer_identity import load_issuer_overrides, resolve_issuer_identity

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {"AAL", "ACN", "APA", "CEG", "CMG", "INVH", "KMX", "MRVL", "NEE", "RIVN", "SNDK", "VNT"}


def test_twelve_reviewed_securities_have_replayable_primary_evidence():
    rows = [r for r in load_issuer_overrides(ROOT / "config/baskets") if not r.get("canonical_issuer_key") and r["reviewed_at"] == "2026-09-11"]
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
    rows = [r for r in load_issuer_overrides(ROOT / "config/baskets") if not r.get("canonical_issuer_key") and r["reviewed_at"] == "2026-09-11"]
    assert len(rows) == 12
    for row in rows:
        raw = {"symbol": row["symbol"], "cusip": row["cusip"], "isin": row["isin"],
               "lei": "N/A", "assetCat": "EC"}
        source = {"raw_symbol": row["symbol"], "cusip": row["cusip"], "isin": row["isin"],
                  "issuer_lei": "N/A", "asset_category": "EC", "raw_payload_json": raw,
                  "holding_date": row["valid_from"], "source_kind": "disclosure"}
        assert resolve_issuer_identity(source, rows) == ("lei:" + row["issuer_lei"], "reviewed_security")
        assert source["issuer_lei"] == raw["lei"] == "N/A"
        source["holding_date"] = "2099-01-01"
        assert resolve_issuer_identity(source, rows)[0] is None


CURRENT_REVIEWED = set("XOM STX CB BE AON OKE RCL GRMN FERG ILMN P ACGL WTW SW AMCR BG IVZ SPCX FER SKHYV ASML LIN ETN MDT ACN TT JCI CRH CBRS FLEX CRDO CCL RDDT LYB ALLE APTV PNR NCLH TEL STE EG NXPI".split())


def test_current_reviewed_securities_have_exact_primary_evidence():
    records = [r for r in load_issuer_overrides(ROOT / "config/baskets")
               if r["reviewed_at"] == "2026-09-25"]
    assert {r["symbol"] for r in records} == CURRENT_REVIEWED
    for row in records:
        path = ROOT / row["source_path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == row["source_sha256"]
        evidence = json.loads(path.read_text())
        assert "2026-09-18" <= row["valid_from"] <= row["reviewed_at"] <= row["valid_to"] <= "2026-12-31"
        if "registry" in evidence:
            assert evidence["security"]["cusip"] == row["cusip"]
            assert evidence["security"]["isin"] == row["isin"]
            data = evidence["registry"]["response"]["data"]
            data = data if isinstance(data, list) else [data]
            assert len(data) == 1 and data[0]["id"] == row["issuer_lei"]
            assert evidence["review"]["identity_kind"] == "prospective_security_mapping"
        elif "sec_issuer" in evidence:
            issuer = evidence["sec_issuer"]
            assert issuer["section"] in {"ISSUER", "SUBJECT COMPANY", "FILER"}
            primary = evidence["primary_source"]
            assert hashlib.sha256(primary["text"].encode()).hexdigest() == primary["sha256"]
            if "issuer_registry" in evidence:
                assert evidence["issuer_registry"]["response"]["data"]["id"] == row["issuer_lei"]
            if issuer["form"].startswith("SCHEDULE"):
                import xml.etree.ElementTree as ET
                document = ET.fromstring(primary["text"])
                issuer_node = next(n for n in document.iter() if n.tag.split("}")[-1] == "issuerInfo")
                fields = {n.tag.split("}")[-1]: n.text for n in issuer_node.iter()}
                assert (fields.get("issuerCik") or fields.get("issuerCIK")) == issuer["cik"]
                assert fields["issuerCusipNumber"] in (row["cusip"], row["isin"])
            assert any(s["cusip"] == row["cusip"] and s["isin"] == row["isin"]
                       and s["valid_from"] <= row["valid_from"] and s["valid_to"] >= row["valid_to"]
                       for s in evidence["reviewed_securities"])
            if row.get("canonical_issuer_key", "").startswith("sec-cik:"):
                assert row["canonical_issuer_key"] == "sec-cik:" + issuer["cik"]
        else:
            raise AssertionError("unrecognized evidence type")
        raw = {"asset": row["symbol"], "securityCusip": row["cusip"], "isin": row["isin"]}
        live = {"raw_symbol": row["symbol"], "cusip": row["cusip"], "isin": row["isin"],
                "issuer_lei": None, "asset_category": None, "raw_payload_json": raw,
                "holding_date": row["valid_from"], "source_kind": "live"}
        expected = row.get("canonical_issuer_key") or "lei:" + row["issuer_lei"]
        assert resolve_issuer_identity(live, records) == (expected, "reviewed_security")
        live["holding_date"] = "2099-01-01"
        assert resolve_issuer_identity(live, records)[0] is None
