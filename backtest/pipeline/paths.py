from __future__ import annotations

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
    current_root = resolve_repo_root()
    current_data = current_root / "data"
    if current_data.exists():
        return current_root
    main_root = resolve_main_repo_root()
    return main_root if (main_root / "data").exists() else current_root
