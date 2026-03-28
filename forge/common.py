"""Shared helpers for Forge control and evaluation code."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Iterable

from src.path_utils import resolve_shared_repo_root


FORGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = FORGE_ROOT.parent


@dataclass(frozen=True)
class StrategyPaths:
    strategy_name: str
    champion_path: Path
    candidate_path: Path
    champion_params_path: Path
    candidate_params_path: Path


def resolve_campaign_path(campaign_path: Path | None = None) -> Path:
    return (campaign_path or FORGE_ROOT / "campaign.lock.json").resolve()


def get_strategy_paths(strategy_name: str) -> StrategyPaths:
    strategies_dir = FORGE_ROOT / "strategies"
    return StrategyPaths(
        strategy_name=strategy_name,
        champion_path=(strategies_dir / f"{strategy_name}_champion.py").resolve(),
        candidate_path=(strategies_dir / f"{strategy_name}_candidate.py").resolve(),
        champion_params_path=(strategies_dir / f"{strategy_name}_champion_params.json").resolve(),
        candidate_params_path=(strategies_dir / f"{strategy_name}_candidate_params.json").resolve(),
    )


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def load_surface_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload.get("parameters", {})


def resolve_manifest_path(campaign: dict[str, Any], campaign_path: Path) -> Path:
    return (campaign_path.parent / campaign["parameter_surface_manifest"]).resolve()


def resolve_campaign_data_dir(campaign: dict[str, Any], campaign_path: Path) -> Path:
    data_dir = Path(campaign["data_dir"])
    configured = (campaign_path.parent / data_dir).resolve()
    if configured.exists():
        return configured

    shared_root = resolve_shared_repo_root(
        PROJECT_ROOT,
        required_paths=(
            "data/crypto/binance_daily_cache",
            "data/crypto/binance_4h_cache",
        ),
    )
    fallback = shared_root / "data" / "crypto"
    if fallback.exists():
        return fallback.resolve()
    return configured


def dynamic_import(path: Path, module_prefix: str = "forge_dynamic") -> ModuleType:
    module_name = f"{module_prefix}_{path.stem}_{time.time_ns()}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to create import spec for {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def hash_file(path: Path) -> str:
    if not path.exists():
        return ""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8192), b""):
            digest.update(chunk)
    return digest.hexdigest()


def hash_files(paths: Iterable[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted({Path(path).resolve() for path in paths}, key=lambda item: str(item)):
        digest.update(str(path).encode("utf-8"))
        digest.update(b"\0")
        digest.update(hash_file(path).encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def get_short_git_sha(repo_root: Path | None = None) -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo_root or PROJECT_ROOT,
            text=True,
        ).strip()
    except Exception:
        return "unknown"


def read_last_n_lines(path: Path, line_count: int) -> str:
    if not path.exists() or line_count <= 0:
        return ""

    with path.open("r", encoding="utf-8") as handle:
        lines = handle.readlines()
    return "".join(lines[-line_count:])
