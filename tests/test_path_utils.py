from pathlib import Path

from src import path_utils


def test_resolve_shared_repo_root_prefers_root_with_required_markers(tmp_path, monkeypatch):
    worktree_root = tmp_path / "worktree"
    main_root = tmp_path / "main"
    worktree_root.mkdir()
    main_root.mkdir()

    # Simulate a worktree-local state DB directory without market caches.
    (worktree_root / "data" / "crypto").mkdir(parents=True)

    # Simulate the primary checkout that actually owns the ignored cache dirs.
    (main_root / "data" / "crypto" / "binance_daily_cache").mkdir(parents=True)
    (main_root / "data" / "crypto" / "binance_4h_cache").mkdir(parents=True)

    monkeypatch.setattr(
        path_utils.subprocess,
        "check_output",
        lambda *args, **kwargs: (
            f"worktree {worktree_root.resolve()}\n"
            f"worktree {main_root.resolve()}\n"
        ),
    )

    resolved = path_utils.resolve_shared_repo_root(
        worktree_root,
        required_paths=(
            "data/crypto/binance_daily_cache",
            "data/crypto/binance_4h_cache",
        ),
    )

    assert resolved == main_root.resolve()
