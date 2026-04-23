from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path


def resolve_repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def resolve_main_repo_root() -> Path:
    current_root = resolve_repo_root()
    try:
        output = subprocess.check_output(
            ["git", "worktree", "list", "--porcelain"],
            cwd=current_root,
            text=True,
        )
        for line in output.splitlines():
            if line.startswith("worktree "):
                return Path(line.replace("worktree ", "", 1)).resolve()
    except Exception:
        pass
    return current_root


def resolve_shared_data_root() -> Path:
    def has_real_market_db(root: Path) -> bool:
        db_path = root / "data" / "market.db"
        if not db_path.exists():
            return False
        try:
            with sqlite3.connect(db_path) as conn:
                row = conn.execute("SELECT COUNT(*) FROM daily_price").fetchone()
            return bool(row and row[0] > 0)
        except Exception:
            return False

    current_root = resolve_repo_root()
    if has_real_market_db(current_root):
        return current_root
    main_root = resolve_main_repo_root()
    if has_real_market_db(main_root):
        return main_root
    return current_root
