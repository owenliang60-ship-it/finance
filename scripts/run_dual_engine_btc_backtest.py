"""
Run a historical backtest for the BTC dual-engine timing system.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the BTC dual-engine backtest.")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument(
        "--risk-mode",
        choices=["surf", "balanced", "fortress"],
        default="balanced",
    )
    parser.add_argument(
        "--transaction-cost-bps",
        type=float,
        default=10.0,
    )
    parser.add_argument(
        "--rebalance-dead-zone-pct",
        type=float,
        default=5.0,
        help="Ignore target-position changes smaller than this percentage.",
    )
    parser.add_argument(
        "--start-date",
        help="Report window start date (YYYY-MM-DD). Earlier data is still used for signal warmup.",
    )
    parser.add_argument(
        "--chart-path",
        help="Optional output path for an SVG NAV chart.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    from backtest.adapters.crypto import CryptoAdapter
    from backtest.timing.dual_engine_backtest import run_dual_engine_backtest
    from src.timing.dual_engine import DualEngineConfig, DualEngineState

    daily_adapter = CryptoAdapter(symbols=[args.symbol], interval="1d")
    intraday_adapter = CryptoAdapter(symbols=[args.symbol], interval="4h")

    daily_data = daily_adapter.load_all().get(args.symbol)
    intraday_data = intraday_adapter.load_all().get(args.symbol)
    if daily_data is None or intraday_data is None:
        raise SystemExit(f"Missing cache data for {args.symbol}; fetch 1d and 4h klines first.")

    result = run_dual_engine_backtest(
        symbol=args.symbol,
        price_4h_df=intraday_data,
        price_daily_df=daily_data,
        state=DualEngineState(risk_mode=args.risk_mode),
        config=DualEngineConfig(risk_mode=args.risk_mode),
        transaction_cost_bps=args.transaction_cost_bps,
        rebalance_dead_zone_pct=args.rebalance_dead_zone_pct,
        start_timestamp=args.start_date,
    )

    backtest = result.backtest
    assert backtest is not None
    chart_path = _resolve_chart_path(args)
    _write_nav_svg(
        chart_path,
        strategy_nav=backtest.strategy_nav,
        buyhold_nav=backtest.buyhold_nav,
        title=f"{args.symbol} Dual Engine vs Buy & Hold",
        subtitle=_build_subtitle(args, backtest),
    )
    summary = {
        "symbol": args.symbol,
        "risk_mode": args.risk_mode,
        "start_date": args.start_date,
        "bars": len(result.evaluations),
        "target_position_pct_latest": result.evaluations[-1].target_position_pct if result.evaluations else 0.0,
        "cagr": backtest.strategy_metrics.cagr,
        "buyhold_cagr": backtest.buyhold_metrics.cagr,
        "excess_cagr": backtest.excess_cagr,
        "max_drawdown": backtest.strategy_metrics.max_drawdown,
        "buyhold_max_drawdown": backtest.buyhold_metrics.max_drawdown,
        "mean_exposure": backtest.mean_exposure,
        "n_rebalances": backtest.n_rebalances,
        "chart_path": str(chart_path),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))

def _resolve_chart_path(args: argparse.Namespace) -> Path:
    if args.chart_path:
        return Path(args.chart_path)

    reports_dir = PROJECT_ROOT / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    start_label = args.start_date or "full_history"
    filename = f"dual_engine_{args.symbol.lower()}_{args.risk_mode}_{start_label}.svg"
    return reports_dir / filename


def _build_subtitle(args: argparse.Namespace, backtest) -> str:
    start_label = args.start_date or "full history"
    return (
        f"Window: {start_label} | "
        f"Strategy CAGR {backtest.strategy_metrics.cagr:+.2%} | "
        f"B&H CAGR {backtest.buyhold_metrics.cagr:+.2%} | "
        f"MaxDD {backtest.strategy_metrics.max_drawdown:+.2%} | "
        f"Mean exposure {backtest.mean_exposure:.1%} | "
        f"Rebalances {backtest.n_rebalances}"
    )


def _write_nav_svg(
    output_path: Path,
    strategy_nav,
    buyhold_nav,
    title: str,
    subtitle: str,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    normalized_strategy = _normalize_nav(strategy_nav)
    normalized_buyhold = _normalize_nav(buyhold_nav)
    all_points = normalized_strategy + normalized_buyhold
    if len(all_points) < 2:
        raise ValueError("Need at least two NAV points to render a chart")

    timestamps = [datetime.fromisoformat(dt) for dt, _ in all_points]
    values = [nav for _, nav in all_points]
    min_ts = min(timestamps).timestamp()
    max_ts = max(timestamps).timestamp()
    min_val = min(values)
    max_val = max(values)
    if max_ts <= min_ts:
        max_ts = min_ts + 1
    if max_val <= min_val:
        max_val = min_val + 1

    width = 1280
    height = 720
    margin_left = 88
    margin_right = 36
    margin_top = 96
    margin_bottom = 72
    plot_width = width - margin_left - margin_right
    plot_height = height - margin_top - margin_bottom

    def x_pos(dt_str: str) -> float:
        ts = datetime.fromisoformat(dt_str).timestamp()
        return margin_left + (ts - min_ts) / (max_ts - min_ts) * plot_width

    def y_pos(value: float) -> float:
        return margin_top + (1 - (value - min_val) / (max_val - min_val)) * plot_height

    def polyline_points(series) -> str:
        return " ".join(f"{x_pos(dt):.1f},{y_pos(nav):.1f}" for dt, nav in series)

    strategy_points = polyline_points(normalized_strategy)
    buyhold_points = polyline_points(normalized_buyhold)

    y_ticks = []
    for idx in range(6):
        frac = idx / 5
        value = min_val + (max_val - min_val) * frac
        y = y_pos(value)
        y_ticks.append((y, value))

    start_label = normalized_strategy[0][0][:10]
    end_label = normalized_strategy[-1][0][:10]
    strategy_last = normalized_strategy[-1][1]
    buyhold_last = normalized_buyhold[-1][1]

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <rect width="{width}" height="{height}" fill="#fffaf2" />
  <rect x="{margin_left}" y="{margin_top}" width="{plot_width}" height="{plot_height}" fill="#fffdf8" stroke="#e8dcc7" stroke-width="1" />
  <text x="{margin_left}" y="46" font-size="28" font-family="Georgia, serif" fill="#2d2419">{_escape_xml(title)}</text>
  <text x="{margin_left}" y="76" font-size="15" font-family="Helvetica, Arial, sans-serif" fill="#6c6255">{_escape_xml(subtitle)}</text>
"""
    for y, value in y_ticks:
        svg += (
            f'  <line x1="{margin_left}" y1="{y:.1f}" x2="{width - margin_right}" y2="{y:.1f}" '
            f'stroke="#eee2cf" stroke-width="1" />\n'
            f'  <text x="{margin_left - 12}" y="{y + 5:.1f}" text-anchor="end" '
            f'font-size="13" font-family="Helvetica, Arial, sans-serif" fill="#7a7064">{value:.0f}</text>\n'
        )
    svg += f"""
  <line x1="{margin_left}" y1="{height - margin_bottom}" x2="{width - margin_right}" y2="{height - margin_bottom}" stroke="#cbbda8" stroke-width="1.5" />
  <text x="{margin_left}" y="{height - margin_bottom + 32}" font-size="13" font-family="Helvetica, Arial, sans-serif" fill="#7a7064">{start_label}</text>
  <text x="{width - margin_right}" y="{height - margin_bottom + 32}" text-anchor="end" font-size="13" font-family="Helvetica, Arial, sans-serif" fill="#7a7064">{end_label}</text>
  <polyline fill="none" stroke="#0f766e" stroke-width="3" points="{strategy_points}" />
  <polyline fill="none" stroke="#c2410c" stroke-width="3" points="{buyhold_points}" />
  <circle cx="{x_pos(normalized_strategy[-1][0]):.1f}" cy="{y_pos(strategy_last):.1f}" r="4.5" fill="#0f766e" />
  <circle cx="{x_pos(normalized_buyhold[-1][0]):.1f}" cy="{y_pos(buyhold_last):.1f}" r="4.5" fill="#c2410c" />
  <rect x="{width - 300}" y="32" width="248" height="64" rx="10" fill="#fffdf8" stroke="#e8dcc7" stroke-width="1" />
  <line x1="{width - 280}" y1="56" x2="{width - 248}" y2="56" stroke="#0f766e" stroke-width="3" />
  <text x="{width - 236}" y="61" font-size="14" font-family="Helvetica, Arial, sans-serif" fill="#2d2419">Dual Engine ({strategy_last:.1f})</text>
  <line x1="{width - 280}" y1="82" x2="{width - 248}" y2="82" stroke="#c2410c" stroke-width="3" />
  <text x="{width - 236}" y="87" font-size="14" font-family="Helvetica, Arial, sans-serif" fill="#2d2419">Buy &amp; Hold ({buyhold_last:.1f})</text>
</svg>
"""
    output_path.write_text(svg, encoding="utf-8")


def _normalize_nav(nav_series):
    base = float(nav_series[0][1])
    if base <= 0:
        raise ValueError("NAV base must be positive")
    return [(dt, round(float(nav) / base * 100.0, 4)) for dt, nav in nav_series]


def _escape_xml(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


if __name__ == "__main__":
    main()
