import json
from pathlib import Path
from scripts.build_company_concept_registry import _load_review_manifest, _manifest_path_for


def test_loader_reads_canonical_symbols(tmp_path):
    csv_path = tmp_path / "x.csv"
    csv_path.write_text("symbol\nAAA\n", encoding="utf-8")
    _manifest_path_for(csv_path).write_text(
        json.dumps({"symbols": ["AAA", "BBB"]}), encoding="utf-8"
    )
    assert _load_review_manifest(csv_path) == {"AAA", "BBB"}


def test_loader_falls_back_to_full_universe(tmp_path):
    """Legacy full_universe-schema manifest (issue 031) is now honored."""
    csv_path = tmp_path / "x.csv"
    csv_path.write_text("symbol\nAAA\n", encoding="utf-8")
    _manifest_path_for(csv_path).write_text(
        json.dumps({"full_universe": ["AAA", "BBB"]}), encoding="utf-8"
    )
    assert _load_review_manifest(csv_path) == {"AAA", "BBB"}
