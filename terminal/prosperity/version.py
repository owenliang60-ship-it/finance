"""Engine code version: a hash of every source file that can change a factor, sample or rank.

Scope = the engine package plus the read/compute-path modules outside it (statement
alignment, membership and reads, beta, settings). A new in-repo import from any hashed
file must be added to `CODE_VERSION_SOURCES` or to `CODE_VERSION_EXCLUDED` with a reason;
tests/test_prosperity_schemes.py enforces this. Frozen parameter packages are checked
against the full version (Boss D-8, 2026-09-29).
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]

CODE_VERSION_SOURCES = (
    "terminal/prosperity",
    "src/data/prosperity_quality.py",
    "src/data/prosperity_history.py",
    "src/data/fundamental_value_checks.py",
    "src/data/metrics_calculator.py",      # _statement_by_income_date (statements.py)
    "src/data/fiscal_repair.py",           # _fiscal_key, used inside that alignment
    "src/data/market_store.py",            # membership resolution and every read
    "src/data/symbol_aliases.py",          # replay members via approximate_members_as_of
    "src/indicators/beta.py",              # packet beta
    "config/settings.py",                  # SUE constants and FUNDAMENTAL_QUARTER_GAP_MAX_DAYS
)

CODE_VERSION_EXCLUDED: Mapping[str, str] = {
    "src/data/fx_validation.py": "only MarketStore.upsert_fx_daily (write path)",
    "src/data/fmp_forward_ingestion.py": "only MarketStore.remap_unmatched_earnings (write path)",
    "scripts/backfill_extended_fundamentals.py": "only the D9 report's three_table_ok; M6 never calls it",
}


def code_version(root: Path = REPO_ROOT, sources: Sequence[str] = CODE_VERSION_SOURCES) -> str:
    files = []
    for rel in sources:
        path = root / rel
        if not path.exists():
            raise FileNotFoundError(f"code_version source missing: {rel}")
        files.extend(sorted(path.rglob("*.py")) if path.is_dir() else [path])
    digest = hashlib.sha256()
    for path in sorted(files, key=lambda p: p.relative_to(root).as_posix()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]
