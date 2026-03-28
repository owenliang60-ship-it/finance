"""Forge evaluator for champion and candidate strategy variants."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backtest.adapters.crypto import CryptoAdapter
from backtest.timing.continuous_engine import trim_continuous_result_window
from forge import common


@dataclass
class WindowResult:
    """Single visible or holdout window result."""

    name: str
    start: str
    end: str
    cagr: float
    buyhold_cagr: float
    excess_cagr: float
    max_drawdown: float
    mean_exposure: float
    n_rebalances: int
    sharpe: float


@dataclass
class ForgeResult:
    """Complete evaluator output."""

    status: str
    error_message: str = ""
    visible_score: float = 0.0
    visible_windows: list[WindowResult] = field(default_factory=list)
    holdout: Optional[WindowResult] = None
    strategy_hash: str = ""
    data_hash: str = ""
    infra_sha: str = ""


def evaluate(
    campaign_path: Path,
    strategy_path: Path,
    params_path: Optional[Path] = None,
) -> ForgeResult:
    """Evaluate a strategy file against visible windows plus hidden holdout."""
    campaign_path = common.resolve_campaign_path(campaign_path)
    strategy_path = strategy_path.resolve()
    params_path = params_path.resolve() if params_path else None

    strategy_hash = common.hash_files(
        [strategy_path] + ([params_path] if params_path and params_path.exists() else [])
    )
    infra_sha = common.get_short_git_sha(PROJECT_ROOT)

    try:
        campaign = common.load_json(campaign_path)
        data_dir = common.resolve_campaign_data_dir(campaign, campaign_path)
        data_hash = compute_data_hash(
            data_dir=data_dir,
            symbol=campaign["symbol"],
            interval=campaign["interval"],
        )
        expected_data_hash = campaign.get("data_snapshot_hash", "")
        if expected_data_hash and expected_data_hash != data_hash:
            return ForgeResult(
                status="ERROR",
                error_message=(
                    "Campaign data hash mismatch: "
                    f"expected={expected_data_hash}, actual={data_hash}"
                ),
                strategy_hash=strategy_hash,
                data_hash=data_hash,
                infra_sha=infra_sha,
            )

        strategy_mod = common.dynamic_import(strategy_path)
        _validate_strategy_exports(strategy_mod)
        config = _build_config(strategy_mod, params_path)
        df_4h, df_1d = _load_price_frames(campaign, campaign_path)

        all_windows = list(campaign["visible_windows"]) + [campaign["holdout_window"]]
        window_results: dict[str, WindowResult] = {}
        for window in all_windows:
            backtest_result = strategy_mod.run_backtest(
                symbol=campaign["symbol"],
                price_4h_df=df_4h,
                price_daily_df=df_1d,
                config=config,
                transaction_cost_bps=float(campaign["transaction_cost_bps"]),
                rebalance_dead_zone_pct=float(campaign["rebalance_dead_zone_pct"]),
                start_timestamp=window["start"],
            )
            if backtest_result is None:
                raise ValueError(f"run_backtest returned None for window {window['name']}")
            trimmed = trim_continuous_result_window(
                backtest_result,
                end_timestamp=window["end"],
                days_per_year=int(campaign["days_per_year"]),
            )
            window_results[window["name"]] = _to_window_result(trimmed, window)

        visible_windows = [window_results[item["name"]] for item in campaign["visible_windows"]]
        holdout = window_results.get(campaign["holdout_window"]["name"])
        # Score = min(excess_cagr / |mdd|) across windows — Calmar-style risk-adjusted
        raw_visible_score = min(
            window.excess_cagr / abs(window.max_drawdown)
            if abs(window.max_drawdown) > 1e-12
            else window.excess_cagr
            for window in visible_windows
        )
        gate_error = _check_visible_gates(campaign, visible_windows)
        status = "PASS" if gate_error is None else "FAIL_GATE"
        error_message = gate_error or ""
        visible_score = raw_visible_score if status == "PASS" else 0.0

        return ForgeResult(
            status=status,
            error_message=error_message,
            visible_score=visible_score,
            visible_windows=visible_windows,
            holdout=holdout,
            strategy_hash=strategy_hash,
            data_hash=data_hash,
            infra_sha=infra_sha,
        )
    except Exception as exc:
        return ForgeResult(
            status="ERROR",
            error_message=str(exc),
            strategy_hash=strategy_hash,
            data_hash="",
            infra_sha=infra_sha,
        )


def print_agent_result(result: ForgeResult, best_score: float) -> None:
    """Print the agent-visible evaluator summary."""
    payload = {
        "status": result.status,
        "error_message": result.error_message,
        "visible_score": result.visible_score,
        "best_visible_score": best_score,
        "visible_windows": [asdict(item) for item in result.visible_windows],
        "holdout": "HIDDEN",
        "strategy_hash": result.strategy_hash,
        "data_hash": result.data_hash,
        "infra_sha": result.infra_sha,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def compute_data_hash(data_dir: Path, symbol: str, interval: str) -> str:
    """Hash the exact data files used by evaluator for reproducibility."""
    daily_path = (data_dir / "binance_daily_cache" / f"{symbol}.csv").resolve()
    intraday_path = (data_dir / "binance_4h_cache" / f"{symbol}.csv").resolve()
    return common.hash_files([daily_path, intraday_path])


def _validate_strategy_exports(strategy_mod) -> None:
    required = ("StrategyConfig", "run_backtest")
    missing = [name for name in required if not hasattr(strategy_mod, name)]
    if missing:
        raise AttributeError(f"Strategy missing required exports: {', '.join(missing)}")


def _build_config(strategy_mod, params_path: Optional[Path]):
    config_class = strategy_mod.StrategyConfig
    if params_path and params_path.exists():
        params = common.load_json(params_path)
        return config_class(**params)
    return config_class()


def _load_price_frames(campaign: dict, campaign_path: Path):
    data_dir = common.resolve_campaign_data_dir(campaign, campaign_path)
    symbol = campaign["symbol"]
    daily_adapter = CryptoAdapter(
        symbols=[symbol],
        cache_dir=data_dir / "binance_daily_cache",
        interval="1d",
    )
    intraday_adapter = CryptoAdapter(
        symbols=[symbol],
        cache_dir=data_dir / "binance_4h_cache",
        interval="4h",
    )

    daily_data = daily_adapter.load_all().get(symbol)
    intraday_data = intraday_adapter.load_all().get(symbol)
    if daily_data is None or intraday_data is None:
        raise FileNotFoundError(f"Missing daily or 4h cache data for {symbol}")
    return intraday_data, daily_data


def _to_window_result(trimmed_result, window: dict) -> WindowResult:
    return WindowResult(
        name=window["name"],
        start=window["start"],
        end=window["end"],
        cagr=trimmed_result.strategy_metrics.cagr,
        buyhold_cagr=trimmed_result.buyhold_metrics.cagr,
        excess_cagr=trimmed_result.excess_cagr,
        max_drawdown=trimmed_result.strategy_metrics.max_drawdown,
        mean_exposure=trimmed_result.mean_exposure,
        n_rebalances=trimmed_result.n_rebalances,
        sharpe=trimmed_result.strategy_metrics.sharpe_ratio,
    )


def _check_visible_gates(campaign: dict, visible_windows: list[WindowResult]) -> str | None:
    gate_max_mdd = float(campaign["gate_max_mdd"])
    gate_min_exposure = float(campaign["gate_min_exposure"])

    gate_min_excess_cagr = float(campaign.get("gate_min_excess_cagr", -1.0))

    for window in visible_windows:
        if window.max_drawdown < gate_max_mdd:
            return (
                f"Window {window.name} max_drawdown {window.max_drawdown:.6f} "
                f"breached gate {gate_max_mdd:.6f}"
            )
        if window.mean_exposure < gate_min_exposure:
            return (
                f"Window {window.name} mean_exposure {window.mean_exposure:.6f} "
                f"breached gate {gate_min_exposure:.6f}"
            )
        if window.excess_cagr < gate_min_excess_cagr:
            return (
                f"Window {window.name} excess_cagr {window.excess_cagr:.6f} "
                f"breached gate {gate_min_excess_cagr:.6f}"
            )
    return None


def _resolve_params_path(args: argparse.Namespace, strategy_path: Path) -> Optional[Path]:
    """Resolve params file path with 3-level priority."""
    # Priority 1: explicit --params-path
    if args.params_path:
        return Path(args.params_path).resolve()
    # Priority 2: sibling file derived from strategy_path
    sibling = strategy_path.parent / f"{strategy_path.stem}_params.json"
    if sibling.exists():
        return sibling.resolve()
    # Priority 3: no params
    return None


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate a Forge strategy candidate.")
    parser.add_argument(
        "--campaign",
        default=str(common.resolve_campaign_path()),
        help="Path to campaign.lock.json",
    )
    parser.add_argument("--strategy", choices=["candidate", "champion"], default="candidate")
    parser.add_argument("--strategy-path", help="Override explicit strategy file path")
    parser.add_argument("--params-path", help="Optional JSON params override file")
    parser.add_argument("--best-score", type=float, default=0.0)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    campaign_path = Path(args.campaign).resolve()
    if args.strategy_path:
        strategy_path = Path(args.strategy_path).resolve()
    else:
        strategy_name = common.load_json(campaign_path)["strategy_name"]
        paths = common.get_strategy_paths(strategy_name)
        strategy_path = paths.candidate_path if args.strategy == "candidate" else paths.champion_path

    params_path = _resolve_params_path(args, strategy_path)
    result = evaluate(campaign_path, strategy_path, params_path=params_path)
    print_agent_result(result, best_score=args.best_score)


if __name__ == "__main__":
    main()
