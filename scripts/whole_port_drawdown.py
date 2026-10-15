#!/usr/bin/env python3
"""Push today's Whole Port drawdown from high-water marks to the private chat.

Reads only the history database written by snapshot_whole_port.py, read-only.
Amounts go to Telegram only; stdout carries status and structural reasons.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
from pathlib import Path
import sqlite3
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.telegram_bot import send_message

TIMEZONE = ZoneInfo("Asia/Shanghai")
HISTORY_START = "2026-09-23"
TRACKED = ("Net Total", "美股", "BTC", "独立团资金")
DEATH_LINE = 0.8  # Tokamak: 20% total drawdown from the Net Total HWM
FOOTER = "按余额计，未扣除资金划转"


class Unknown(Exception):
    """The report cannot be trusted; the message states this reason."""


def parse_cells(date: str, raw: str) -> dict:
    try:
        pairs = [(name, value) for name, value in json.loads(raw)]
    except (TypeError, ValueError):
        raise Unknown("%s 快照无法解析" % date) from None
    values = {}
    for label in TRACKED:
        found = [value for name, value in pairs if name == label]
        if len(found) != 1:
            raise Unknown("%s 快照标签 %s 出现 %d 次" % (date, label, len(found)))
        value = found[0]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise Unknown("%s 快照 %s 不是有效数字" % (date, label))
        values[label] = value
    return values


def load_history(db_path: Path, today: str) -> list:
    db_path = Path(db_path)
    if not db_path.is_file():
        raise Unknown("history.db 不存在")
    try:
        conn = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT snapshot_date, cells_json FROM whole_port_daily "
                "WHERE snapshot_date BETWEEN ? AND ? ORDER BY snapshot_date",
                (HISTORY_START, today)).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise Unknown("快照表读取失败（%s）" % type(exc).__name__) from None
    return [(date, parse_cells(date, raw)) for date, raw in rows]


def compute(history: list, today: str) -> dict:
    if not history or history[-1][0] != today:
        raise Unknown("今日快照缺失（没有 %s 的快照）" % today)
    items = {}
    for label in TRACKED:
        hwm_date, hwm = None, None
        for date, values in history:  # date order; strict > keeps the earliest tie
            if hwm is None or values[label] > hwm:
                hwm_date, hwm = date, values[label]
        items[label] = (history[-1][1][label], hwm, hwm_date)
    return items


def _num(x) -> str:
    return "{:,}".format(round(x))


def _signed(x) -> str:
    r = round(x)
    return "{:+,}".format(r) if r else "0"


def _pct(gap, hwm) -> str:
    if hwm <= 0:
        return "n/a"
    p = gap / hwm
    return "{:+.1%}".format(p) if round(p, 3) else "0.0%"


def render(today: str, items: dict) -> str:
    lines = ["Whole Port 回撤 · " + today]
    for label in TRACKED:
        current, hwm, hwm_date = items[label]
        gap = current - hwm
        lines += ["", label,
                  "当前 %s ｜ HWM %s（%s）" % (_num(current), _num(hwm), hwm_date),
                  "距 HWM %s（%s）" % (_signed(gap), _pct(gap, hwm))]
        if label == "Net Total":
            room = current - hwm * DEATH_LINE
            lines.append("距 -20% 死线还差 " + _num(room) if round(room) >= 0
                         else "已跌破 -20% 死线 " + _num(-room))
    return "\n".join(lines + ["", FOOTER])


def render_unknown(today: str, reason: str) -> str:
    return "\n".join(["Whole Port 回撤 · %s · UNKNOWN" % today,
                      "原因：" + reason, "未使用前一天数据。", "", FOOTER])


def build_message(db_path: Path, today: str) -> tuple:
    """Return ("ok", text) or ("unknown: <reason>", text)."""
    try:
        return "ok", render(today, compute(load_history(db_path, today), today))
    except Unknown as exc:
        return "unknown: %s" % exc, render_unknown(today, str(exc))


def main(send=send_message, clock=lambda: dt.datetime.now(TIMEZONE)) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True,
                        help="Whole Port history database (opened read-only)")
    args = parser.parse_args()
    if args.db.name in {"market.db", "company.db"}:
        parser.error("use the Whole Port history database")
    today = clock().astimezone(TIMEZONE).date().isoformat()
    status, text = build_message(args.db, today)
    if not send(text, channel="private"):
        print("whole_port_drawdown send failed", file=sys.stderr)
        return 1
    print("whole_port_drawdown sent " + status)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
