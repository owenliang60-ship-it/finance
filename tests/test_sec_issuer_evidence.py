"""Reviewed SEC issuer records supplement, never overwrite, original sources."""
import hashlib
import json
from pathlib import Path

from src.data.fund_issuer_identity import load_issuer_overrides
from src.data.fmp_forward_ingestion import load_basket_configs, load_share_class_conventions

ROOT = Path(__file__).resolve().parents[1]
REMAINING = set("AMTM APTV BKR CSGP CTAS CTLT CVX EXPE EXR J KHC LH NCLH PENN PHM RAL STE TDG TEL TKO TPL VNO WRK FER LCID SGEN CRDO MTSI OLED".split())


def test_remaining_securities_have_frozen_sec_issuer_evidence():
    records = [r for r in load_issuer_overrides(ROOT / "config/baskets") if r.get("canonical_issuer_key", "").startswith("sec-cik:")]
    assert REMAINING <= {r["symbol"] for r in records}
    for r in records:
        path = ROOT / r["source_path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == r["source_sha256"]
        source = json.loads(path.read_text())
        assert r["canonical_issuer_key"] == "sec-cik:" + source["sec_issuer"]["cik"]
        assert source["sec_issuer"]["section"] in {"ISSUER", "SUBJECT COMPANY", "FILER"}
        assert r["isin"] in {s["isin"] for s in source["reviewed_securities"]}


def test_historical_dual_classes_merge_but_undeclared_market_cap_is_withheld():
    _, groups, _ = load_basket_configs(ROOT / "config/baskets")
    conventions = load_share_class_conventions(ROOT / "config/baskets")
    assert groups["DISCA"] == ["DISCK"]
    assert groups["UAA"] == ["UA"]
    assert conventions["DISCA"] is None
    assert conventions["UAA"] is None
