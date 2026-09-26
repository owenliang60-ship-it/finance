"""Symbol alias config (prosperity north star P1): one company, one code."""
import json
import pathlib

import pytest

from src.data.symbol_aliases import load_symbol_aliases, resolve_alias

CONFIG_DIR = pathlib.Path(__file__).parents[1] / "config"


def _entry(**kw):
    base = {"alias": "OLD", "canonical": "NEW", "kind": "rename",
            "status": "verified", "reviewed_at": "2026-09-27",
            "evidence": "same-day market cap within 2% on 100/100 days"}
    base.update(kw)
    return base


def _write(tmp_path, entries):
    (tmp_path / "symbol_aliases.json").write_text(
        json.dumps({"schema_version": 1, "aliases": entries}))
    return tmp_path


def test_missing_file_fails_loud(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_symbol_aliases(tmp_path)


def test_valid_entries_load(tmp_path):
    out = load_symbol_aliases(_write(tmp_path, [
        _entry(),
        _entry(alias="WRK", canonical="SW", kind="merger", effective_date="2024-07-05"),
    ]))
    assert [(e["alias"], e["canonical"]) for e in out] == [("OLD", "NEW"), ("WRK", "SW")]


@pytest.mark.parametrize("entries", [
    [_entry(), _entry(canonical="OTHER")],                       # alias listed twice
    [_entry(), _entry(alias="NEW", canonical="NEWER")],          # chain: canonical is an alias
    [_entry(alias="SAME", canonical="SAME")],                    # self-map
    [_entry(kind="spinoff")],                                    # unknown kind
    [_entry(status="maybe")],                                    # unknown status
    [_entry(kind="merger")],                                     # merger needs effective_date
    [_entry(effective_date="2024-13-01", kind="merger")],        # bad date
    [_entry(evidence="")],                                       # evidence required
    [_entry(effective_date="2024-07-05")],                       # only mergers carry a date
])
def test_invalid_entries_fail_loud(tmp_path, entries):
    with pytest.raises(ValueError):
        load_symbol_aliases(_write(tmp_path, entries))


def test_resolve_alias_rename_always_maps():
    aliases = [_entry(alias="ABC", canonical="COR")]
    assert resolve_alias("ABC", "2022-06-30", aliases) == "COR"
    assert resolve_alias("COR", "2022-06-30", aliases) == "COR"
    assert resolve_alias("XYZ", "2022-06-30", aliases) == "XYZ"


def test_resolve_alias_merger_splits_on_effective_date():
    aliases = [_entry(alias="WRK", canonical="SW", kind="merger",
                      effective_date="2024-07-05")]
    # before the merger the canonical code's history is vendor backfill: drop it
    assert resolve_alias("SW", "2023-06-30", aliases) is None
    assert resolve_alias("WRK", "2023-06-30", aliases) == "WRK"
    # from the effective date the old code folds into the survivor
    assert resolve_alias("WRK", "2024-07-05", aliases) == "SW"
    assert resolve_alias("SW", "2024-09-30", aliases) == "SW"


def test_production_config_covers_scanned_duplicates():
    pairs = {(e["alias"], e["canonical"]): e for e in load_symbol_aliases(CONFIG_DIR)}
    expected = {
        ("ABC", "COR"), ("ANTM", "ELV"), ("BLL", "BALL"), ("FLT", "CPAY"),
        ("PEAK", "DOC"), ("CDAY", "DAY"), ("FI", "FISV"), ("GPS", "GAP"),
        ("MMC", "MRSH"), ("PKI", "RVTY"), ("DISCA", "WBD"), ("DISCK", "WBD"),
        ("CUK", "CCL"), ("VMRK", "EQR"), ("WRK", "SW"),
    }
    assert set(pairs) == expected
    assert pairs[("VMRK", "EQR")]["status"] == "pending_verification"
    assert pairs[("WRK", "SW")]["kind"] == "merger"
    assert pairs[("WRK", "SW")]["effective_date"] == "2024-07-05"
