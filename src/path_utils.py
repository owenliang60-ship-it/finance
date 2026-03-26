"""Path helpers for shared worktree-safe resources."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Iterable


def _has_required_paths(root: Path, required_paths: Iterable[Path]) -> bool:
    return all((root / rel_path).exists() for rel_path in required_paths)


def resolve_shared_repo_root(
    repo_root: Path,
    required_paths: Iterable[Path | str] | None = None,
) -> Path:
    """
    Resolve the repository root that actually owns ignored shared data.

    In worktrees, the checked-out tree often lacks ignored directories such as
    `data/`. In that case, fall back to the primary worktree listed by git.
    """
    repo_root = Path(repo_root).resolve()
    required = tuple(Path(path) for path in (required_paths or ("data",)))

    if _has_required_paths(repo_root, required):
        return repo_root

    try:
        output = subprocess.check_output(
            ["git", "worktree", "list", "--porcelain"],
            cwd=repo_root,
            text=True,
        )
    except Exception:
        return repo_root

    for line in output.splitlines():
        if not line.startswith("worktree "):
            continue
        candidate = Path(line[len("worktree ") :]).resolve()
        if _has_required_paths(candidate, required):
            return candidate

    return repo_root
