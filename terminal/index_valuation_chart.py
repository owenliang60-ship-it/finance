"""Read-only five-year weekly valuation chart; never fetches or writes data."""

import argparse
import json
import math
import sqlite3
from datetime import date
from pathlib import Path

from src.data.fmp_forward_ingestion import load_index_pe_basket_configs
from terminal.index_pe_weekly import RESULT_HASH_FIELDS, weekly_result_hash

CONFIG_DIR = Path(__file__).parent.parent / "config" / "baskets"
COLORS = {"ttm": "#087F8C", "hindsight": "#7654AB", "pit": "#D76D56"}
FOOTNOTE = (
    "非官网口径；后视镜为事后分析；虚线为最新共识补齐。分析师共识不保证与 GAAP 实际口径一致。"
)


def percentile(values, current):
    return (
        100 * sum(v <= current for v in values) / len(values)
        if values and current is not None
        else None
    )


def line_segments(rows, key, date_key="valuation_date", style_key=None):
    """No interpolation across a NULL or missing week; tail is explicitly dashed."""
    segments, points, last, style = [], [], None, None
    for row in rows:
        value = row.get(key)
        day = row[date_key]
        current_style = (
            "dashed"
            if style_key and row.get(style_key) == "latest_consensus_tail"
            else "solid"
        )
        gap = (
            last is not None
            and (date.fromisoformat(day) - date.fromisoformat(last[0])).days > 10
        )
        if value is None or gap or (style is not None and current_style != style):
            if points:
                segments.append((style, points))
            points = (
                [last]
                if value is not None
                and not gap
                and last is not None
                and current_style != style
                else []
            )
        if value is None:
            last, style = None, None
            continue
        last, style = (day, float(value)), current_style
        points.append(last)
    if points:
        segments.append((style, points))
    return segments


def dashed_strokes(points, dash=7, gap=5):
    """Carry dash phase across short weekly edges; do not reset each week."""
    phase = 0.0
    for a, b in zip(points, points[1:]):
        length = math.dist(a, b)
        offset = 0.0
        while offset < length:
            step = min((dash if phase < dash else dash + gap) - phase, length - offset)
            end = offset + step
            if phase < dash:
                yield (
                    tuple((a[i] + (b[i] - a[i]) * offset / length) for i in (0, 1)),
                    tuple((a[i] + (b[i] - a[i]) * end / length) for i in (0, 1)),
                )
            phase = (phase + step) % (dash + gap)
            offset = end


def summarize(history, pit, as_of):
    def latest(rows, key):
        return next((r for r in reversed(rows) if r.get(key) is not None), None)

    ttm = latest(history, "ttm_pe_gaap")
    hs = latest(history, "hindsight_ntm_pe_gaap")
    p = latest(pit, "fwd_pe_ntm")
    return {
        "ttm": ttm,
        "hindsight": hs,
        "pit": p,
        "ttm_percentile": percentile(
            [r["ttm_pe_gaap"] for r in history if r.get("ttm_pe_gaap") is not None],
            ttm["ttm_pe_gaap"] if ttm else None,
        ),
        "hindsight_percentile": percentile(
            [
                r["hindsight_ntm_pe_gaap"]
                for r in history
                if r.get("hindsight_ntm_pe_gaap") is not None
                and r.get("quality_tier") == "actual_only"
            ],
            hs["hindsight_ntm_pe_gaap"]
            if hs and hs.get("quality_tier") == "actual_only"
            else None,
        ),
        "stale": any(
            (date.fromisoformat(as_of) - date.fromisoformat(r[k])).days > 14
            for r, k in (
                (ttm, "valuation_date"),
                (hs, "valuation_date"),
                (p, "snapshot_date"),
            )
            if r
        ),
    }


def load_chart_data(db_path, as_of, years=5):
    end = date.fromisoformat(as_of)
    try:
        start = end.replace(year=end.year - years)
    except ValueError:
        start = end.replace(year=end.year - years, day=28)
    configs = load_index_pe_basket_configs(CONFIG_DIR)
    output = {"as_of": as_of, "from_date": start.isoformat(), "panels": []}
    for basket in sorted(configs, key=lambda b: configs[b]["display_order"]):
        output["panels"].append(
            {"basket": basket, "history": [], "pit": [], "warnings": []}
        )
    path = Path(db_path)
    if not path.exists():
        for p in output["panels"]:
            p["warnings"].append("估值数据库尚未准备")
        return output
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("BEGIN")
    try:
        try:
            conn.execute("CREATE TABLE _index_chart_write_probe (x INTEGER)")
        except sqlite3.OperationalError:
            pass
        else:
            raise ValueError("chart connection is writable")
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        for panel in output["panels"]:
            basket = panel["basket"]
            if {"basket_weekly_pe_history", "basket_pe_backfill_runs"} <= tables:
                columns = ", ".join(["basket", "run_id", *RESULT_HASH_FIELDS])
                rows = [
                    dict(r)
                    for r in conn.execute(
                        f"SELECT {columns} FROM basket_weekly_pe_history WHERE basket=? ORDER BY valuation_date",
                        [basket],
                    )
                ]
                events = [
                    dict(r)
                    for r in conn.execute(
                        "SELECT * FROM basket_pe_backfill_runs WHERE basket=? ORDER BY rowid",
                        [basket],
                    )
                ]
                owned = {}
                for row in rows:
                    owned.setdefault(row["run_id"], []).append(row)
                certified = True
                for owner, group in owned.items():
                    stream = [e for e in events if e["run_id"] == owner]
                    kinds = [e["event_kind"] for e in stream]
                    if (
                        kinds.count("run_started") != 1
                        or kinds[0] != "run_started"
                        or kinds.count("run_completed") != 1
                        or kinds[-1] != "run_completed"
                        or "run_failed" in kinds
                    ):
                        certified = False
                        break
                    terminal = json.loads(stream[-1]["payload_json"])
                    if terminal.get("weekly_rows") != len(group) or terminal.get(
                        "result_hash"
                    ) != weekly_result_hash(group):
                        certified = False
                        break
                if certified:
                    panel["history"] = [
                        r
                        for r in rows
                        if start.isoformat() <= r["valuation_date"] <= as_of
                    ]
                else:
                    panel["warnings"].append("历史物化数据未通过认证")
                if events and events[-1]["event_kind"] != "run_completed":
                    panel["warnings"].append("最近一次刷新未完成；展示上次认证数据")
            if {"fmp_basket_valuation", "fmp_forward_runs"} <= tables:
                for raw in conn.execute(
                    "SELECT v.* FROM fmp_basket_valuation v JOIN fmp_forward_runs r "
                    "ON r.snapshot_date=v.snapshot_date AND r.run_kind='weekly' AND r.status='complete' "
                    "WHERE v.basket=? AND v.snapshot_date BETWEEN ? AND ? ORDER BY v.snapshot_date",
                    [
                        configs[basket]["source_basket"],
                        max(start.isoformat(), "2026-07-13"),
                        as_of,
                    ],
                ):
                    row = dict(raw)
                    payload = json.loads(row["members_json"])
                    if (
                        not isinstance(payload, dict)
                        or payload.get("status") not in ("complete", "partial")
                        or payload.get("earnings_basis")
                        != "analyst_consensus_not_verified_gaap"
                        or row["mcap_coverage_ntm"] < 0.9
                        or row["weight_coverage"] < 0.9
                        or not row["ntm_net_income"]
                        or row["ntm_net_income"] <= 0
                        or row["fwd_pe_ntm"] is None
                        or not math.isclose(
                            row["fwd_pe_ntm"],
                            payload.get("ntm_total_mcap", 0) / row["ntm_net_income"],
                            rel_tol=1e-9,
                        )
                    ):
                        panel["warnings"].append("部分 PIT 记录未通过发布门")
                        continue
                    panel["pit"].append(
                        {k: v for k, v in row.items() if k != "members_json"}
                    )
            if not panel["history"]:
                panel["warnings"].append("历史数据尚未准备")
            if not panel["pit"]:
                panel["warnings"].append("真实 PIT NTM 尚未生成")
    except (sqlite3.Error, ValueError, KeyError, TypeError) as exc:
        for panel in output["panels"]:
            panel.update(history=[], pit=[], warnings=["估值数据暂不可用"])
        output["error"] = str(exc)
    finally:
        conn.close()
    return output


def render_chart(data, output_path, font_loader=None):
    from PIL import Image, ImageDraw, PngImagePlugin

    if font_loader is None:
        from terminal.report_fonts import load_visual_font

        font_loader = load_visual_font
    width, height = 1800, 1510
    image = Image.new("RGB", (width, height), "#F8FAFC")
    draw = ImageDraw.Draw(image)
    font = lambda size, bold=False: font_loader(size, bold=bold)
    draw.text((84, 40), "三指数估值 · 五年周频", font=font(45, True), fill="#172B3A")
    draw.text(
        (84, 105),
        f"报告截至 {data['as_of']}  |  统一聚合公式：Σ市值 / Σ盈利",
        font=font(24),
        fill="#51616C",
    )
    for x, label, color in (
        (84, "GAAP TTM", COLORS["ttm"]),
        (410, "后视镜 NTM", COLORS["hindsight"]),
        (775, "真实 PIT NTM（分析师共识）", COLORS["pit"]),
    ):
        draw.line((x, 172, x + 46, 172), fill=color, width=5)
        draw.text((x + 60, 155), label, font=font(24), fill="#233746")
    if data.get("demo"):
        draw.text((1360, 60), "合成数据示意", font=font(27, True), fill="#A32929")
    start, end = (
        date.fromisoformat(data["from_date"]),
        date.fromisoformat(data["as_of"]),
    )
    span = max((end - start).days, 1)
    for index, panel in enumerate(data["panels"]):
        y = 225 + index * 385
        draw.rounded_rectangle(
            (64, y, 1736, y + 361),
            radius=12,
            fill="#FFFFFF",
            outline="#DCE4E8",
            width=1,
        )
        draw.text((90, y + 14), panel["basket"], font=font(33, True), fill="#172B3A")
        summary = summarize(panel["history"], panel["pit"], data["as_of"])
        summaries = []
        for kind, key in (
            ("ttm", "ttm_pe_gaap"),
            ("hindsight", "hindsight_ntm_pe_gaap"),
            ("pit", "fwd_pe_ntm"),
        ):
            row = summary[kind]
            name = {"ttm": "TTM", "hindsight": "后视镜", "pit": "PIT"}[kind]
            if row:
                day = row.get("valuation_date", row.get("snapshot_date"))
                text = f"{name} {row[key]:.1f}× @ {day}"
                rank = summary.get(kind + "_percentile")
                if rank is not None:
                    text += f"  P{rank:.0f}"
                if (
                    kind == "hindsight"
                    and row.get("quality_tier") == "latest_consensus_tail"
                ):
                    count = row.get("hindsight_estimate_quarters")
                    text += f"（最多{count}Q共识）" if count is not None else "（共识尾部）"
                summaries.append(text)
            else:
                summaries.append(name + " N/A")
        draw.text(
            (240, y + 23), "   |   ".join(summaries), font=font(20), fill="#354854"
        )
        status = list(panel.get("warnings", []))
        if summary["stale"]:
            status.insert(0, "数据已超过14天，标记过期")
        for kind, field in (
            ("ttm", "mcap_coverage_ttm"),
            ("hindsight", "mcap_coverage_hindsight"),
            ("pit", "mcap_coverage_ntm"),
        ):
            if summary[kind]:
                status.append(f"{kind.upper()} 市值覆盖 {summary[kind][field]:.1%}")
        draw.text(
            (90, y + 63),
            " | ".join(status),
            font=font(19),
            fill="#9C4A2D" if summary["stale"] else "#657985",
        )
        values = [
            r[k]
            for r in panel["history"]
            for k in ("ttm_pe_gaap", "hindsight_ntm_pe_gaap")
            if r.get(k) is not None
        ]
        values += [r["fwd_pe_ntm"] for r in panel["pit"]]
        top = max(5, math.ceil(max(values, default=25) * 1.12 / 5) * 5)
        left, right, upper, lower = 132, 1690, y + 112, y + 307
        xy = lambda point: (
            left + (date.fromisoformat(point[0]) - start).days / span * (right - left),
            lower - point[1] / top * (lower - upper),
        )
        for step in range(5):
            tick = top * step / 4
            yy = lower - tick / top * (lower - upper)
            draw.line((left, yy, right, yy), fill="#E3E9ED", width=1)
            draw.text((85, yy - 10), f"{tick:.0f}×", font=font(17), fill="#70818D")
        for year in range(start.year, end.year + 1):
            day = date(year, 1, 1)
            if not start <= day <= end:
                continue
            xx = xy((day.isoformat(), 0))[0]
            draw.line((xx, upper, xx, lower), fill="#EDF1F3", width=1)
            draw.text((xx - 22, lower + 14), str(year), font=font(18), fill="#70818D")
        for kind, key, rows, datekey in [
            ("ttm", "ttm_pe_gaap", panel["history"], "valuation_date"),
            ("hindsight", "hindsight_ntm_pe_gaap", panel["history"], "valuation_date"),
            ("pit", "fwd_pe_ntm", panel["pit"], "snapshot_date"),
        ]:
            for style, points in line_segments(
                rows, key, datekey, "quality_tier" if kind == "hindsight" else None
            ):
                coords = [xy(p) for p in points]
                strokes = (
                    dashed_strokes(coords)
                    if style == "dashed"
                    else zip(coords, coords[1:])
                )
                for a, b in strokes:
                    draw.line((a, b), fill=COLORS[kind], width=3)
                if kind == "pit" or len(coords) == 1:
                    for x0, y0 in coords:
                        draw.ellipse(
                            (x0 - 4, y0 - 4, x0 + 4, y0 + 4), fill=COLORS[kind]
                        )
        if not values:
            draw.text((640, y + 185), "估值数据尚未准备", font=font(28), fill="#84939C")
    draw.text((84, 1405), FOOTNOTE, font=font(22), fill="#546570")
    draw.text(
        (84, 1445),
        "分位：TTM 用全部有效点；后视镜仅用 actual-only。缺失周不连线；SOXX 披露起点前保留空白。",
        font=font(20),
        fill="#657985",
    )
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("Description", FOOTNOTE)
    image.save(path, "PNG", optimize=True, pnginfo=metadata)
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--as-of", default=date.today().isoformat())
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    render_chart(load_chart_data(args.db, args.as_of), args.output)


if __name__ == "__main__":
    main()
