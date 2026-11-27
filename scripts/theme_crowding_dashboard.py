#!/usr/bin/env python3
"""CLI entry point for US Theme Basket Crowding Indicator V2.2 (strictly read-only on market.db).

Usage:
    python scripts/theme_crowding_dashboard.py \
        --market-db data/market.db \
        --out-html notes/theme_crowding_v2.html \
        [--out-json notes/theme_crowding_v2.json]

Exit codes:
    0: written successfully
    4: invalid CLI path (missing parent dir, output is directory, or output collides with input DB / WAL / SHM)
    6: missing database or calculation/validation failure (nothing written)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backtest.theme_crowding.dashboard import render_dashboard_html  # noqa: E402
from backtest.theme_crowding.engine import compute_crowding_payload  # noqa: E402


def _collides_with_db(out_path: Path, db_path: Path) -> bool:
    out_resolved = out_path.parent.resolve() / out_path.name
    db_resolved = db_path.parent.resolve() / db_path.name
    for suffix in ("", "-wal", "-shm", ".state"):
        cand = db_resolved.with_name(db_resolved.name + suffix)
        if out_resolved == cand:
            return True
        if out_resolved.exists() and cand.exists():
            try:
                if os.path.samefile(out_resolved, cand):
                    return True
            except OSError:
                pass
    return False


def _atomic_write_text(content: str, out_path: Path) -> None:
    resolved_out = out_path.parent.resolve() / out_path.name
    fd, tmp_name = tempfile.mkstemp(prefix=".crowding-", suffix=".tmp", dir=resolved_out.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, resolved_out)
        tmp_name = ""
    finally:
        if tmp_name:
            Path(tmp_name).unlink(missing_ok=True)


def _validate_out_arg(out_arg: Path, db_arg: Path, flag_name: str) -> Optional[str]:
    if out_arg.exists() and out_arg.is_dir():
        return f"{flag_name} must be a file, not a directory: {out_arg}"
    if not out_arg.parent.is_dir():
        return f"{flag_name} parent directory does not exist: {out_arg.parent}"
    if _collides_with_db(out_arg, db_arg):
        return f"{flag_name} would overwrite input database or sidecar: {out_arg}"
    return None


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Build US Theme Basket Crowding V2.2 HTML dashboard (read-only)")
    parser.add_argument(
        "--market-db",
        type=Path,
        default=PROJECT_ROOT / "data" / "market.db",
        help="Path to read-only market.db (default: data/market.db)",
    )
    parser.add_argument(
        "--out-html",
        type=Path,
        required=True,
        help="Output path for self-contained light-theme HTML dashboard",
    )
    parser.add_argument(
        "--out-json",
        type=Path,
        default=None,
        help="Optional output path for JSON payload",
    )
    parser.add_argument(
        "--immutable",
        action="store_true",
        help="Open SQLite with ?immutable=1 (for read-only OS sandboxes)",
    )
    args = parser.parse_args(argv)

    err = _validate_out_arg(args.out_html, args.market_db, "--out-html")
    if err:
        print(err, file=sys.stderr)
        return 4
    if args.out_json is not None:
        err_j = _validate_out_arg(args.out_json, args.market_db, "--out-json")
        if err_j:
            print(err_j, file=sys.stderr)
            return 4
        if (args.out_html.parent.resolve() / args.out_html.name) == (args.out_json.parent.resolve() / args.out_json.name):
            print("--out-html and --out-json must not point to the same file", file=sys.stderr)
            return 4

    try:
        payload = compute_crowding_payload(args.market_db, immutable=args.immutable)
        html = render_dashboard_html(payload)
        if args.out_json is not None:
            json_text = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)
            _atomic_write_text(json_text, args.out_json)
        _atomic_write_text(html, args.out_html)
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 6

    print(
        f"OK as_of={payload['as_of']} baskets={len(payload['baskets'])} "
        f"html={args.out_html} ({args.out_html.stat().st_size / 1024:.1f} KB)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
