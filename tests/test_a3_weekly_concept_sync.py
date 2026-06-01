import csv
from pathlib import Path
from scripts.build_company_concept_registry import (
    _read_csv_symbols, _normalize_review_csv, _append_csv_atomic, REVIEW_CSV_FIELDS,
)


def _write(p: Path, header: list[str], rows: list[list[str]]):
    with p.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh); w.writerow(header)
        for r in rows: w.writerow(r)


def test_normalize_dedups_duplicate_business_role(tmp_path):
    # legacy 17-col header: business_role at pos 2 AND pos 11
    header = ["review_reason", "symbol", "business_role", "company_name", "fmp_sector",
              "fmp_industry", "market_cap_b", "mcap_tier", "description", "l1", "l2",
              "l3_themes", "business_role", "prefill_source", "confidence", "needs_review", "boss_notes"]
    row = ["ok", "AAA", "", "Co", "Tech", "Semis", "12.0", "small", "desc", "信息技术",
           "半导体", "存储", "代工", "rule", "0.70", "0", ""]
    src = tmp_path / "legacy.csv"; _write(src, header, [row])
    dst = tmp_path / "canon.csv"
    assert _normalize_review_csv(src, dst) == 1
    out = list(csv.DictReader(dst.open(encoding="utf-8")))
    assert list(out[0].keys()) == REVIEW_CSV_FIELDS          # 16 unique fields
    assert out[0]["business_role"] == "代工"                  # coalesced non-empty (pos 11)


def test_append_csv_atomic_normalizes(tmp_path):
    canon = tmp_path / "canon.csv"
    _write(canon, REVIEW_CSV_FIELDS, [["ok", "AAA"] + [""] * 14])
    _append_csv_atomic(canon, [{"symbol": "BBB", "l1": "x"}])
    assert _read_csv_symbols(canon) == {"AAA", "BBB"}
