import datetime as dt
import json
import sqlite3

import pytest

from scripts import whole_port_drawdown as job

TODAY = "2026-10-15"


def cells(net=1100, us=500, btc=300, team=0, **override):
    rows = [[None, "当前市值"], ["美股", us], ["BTC", btc], ["独立团资金", team],
            ["A股", 50], ["现金", 250], ["total", 1100], ["Net Total", net]]
    for label, value in override.items():
        rows = [[label, value] if r[0] == label else r for r in rows]
    return rows


def make_db(path, snapshots):
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TABLE whole_port_daily (
            snapshot_date TEXT PRIMARY KEY, captured_at TEXT NOT NULL,
            timezone TEXT NOT NULL, sheet_tab TEXT NOT NULL,
            cell_range TEXT NOT NULL, cells_json TEXT NOT NULL)""")
        for date, rows in snapshots.items():
            payload = rows if isinstance(rows, str) else json.dumps(rows, ensure_ascii=False)
            conn.execute("INSERT INTO whole_port_daily VALUES (?, ?, ?, ?, ?, ?)", (
                date, date + "T09:00:00+08:00", "Asia/Shanghai", "Whole Port", "B2:C9", payload))
    return path


def test_report_has_current_hwm_and_net_total_deadline(tmp_path):
    db = make_db(tmp_path / "h.db", {
        "2026-09-23": cells(net=1000, us=400, btc=200, team=0),
        "2026-09-30": cells(net=1250, us=600, btc=300, team=0),
        "2026-10-01": cells(net=1250, us=550, btc=350, team=0),
        TODAY: cells(net=1100, us=500, btc=360, team=0),
    })
    assert job.build_message(db, TODAY) == ("ok", "\n".join([
        "Whole Port 回撤 · 2026-10-15",
        "",
        "Net Total",
        "当前 1,100 ｜ HWM 1,250（2026-09-30）",
        "距 HWM -150（-12.0%）",
        "距 -20% 死线还差 100",
        "",
        "美股",
        "当前 500 ｜ HWM 600（2026-09-30）",
        "距 HWM -100（-16.7%）",
        "",
        "BTC",
        "当前 360 ｜ HWM 360（2026-10-15）",
        "距 HWM 0（0.0%）",
        "",
        "独立团资金",
        "当前 0 ｜ HWM 0（2026-09-23）",
        "距 HWM 0（n/a）",
        "",
        "按余额计，未扣除资金划转",
    ]))


def test_deadline_breached_and_pre_start_rows_ignored(tmp_path):
    db = make_db(tmp_path / "h.db", {
        "2026-09-22": cells(net=99999),
        "2026-09-23": cells(net=1000),
        TODAY: cells(net=700),
    })
    status, text = job.build_message(db, TODAY)
    assert status == "ok"
    assert "当前 700 ｜ HWM 1,000（2026-09-23）" in text
    assert "距 HWM -300（-30.0%）" in text
    assert "已跌破 -20% 死线 100" in text
    assert "99,999" not in text


def test_missing_today_is_unknown_and_never_uses_yesterday(tmp_path):
    db = make_db(tmp_path / "h.db", {"2026-10-14": cells(net=1234)})
    status, text = job.build_message(db, TODAY)
    assert status.startswith("unknown: ")
    assert text.splitlines()[0] == "Whole Port 回撤 · 2026-10-15 · UNKNOWN"
    assert "今日快照缺失" in text
    assert "1,234" not in text
    assert text.endswith("按余额计，未扣除资金划转")


@pytest.mark.parametrize("bad_date", [TODAY, "2026-09-30"])
@pytest.mark.parametrize("bad_rows, reason", [
    ("not json", "无法解析"),
    (cells(**{"美股": "#N/A"}), "美股"),
    (cells(**{"BTC": None}), "BTC"),
    (cells(**{"Net Total": True}), "Net Total"),
    ([r for r in cells() if r[0] != "独立团资金"], "独立团资金"),
    (cells() + [["BTC", 1]], "BTC"),
])
def test_any_bad_row_is_unknown(tmp_path, bad_date, bad_rows, reason):
    snapshots = {"2026-09-30": cells(), TODAY: cells()}
    snapshots[bad_date] = bad_rows
    status, text = job.build_message(make_db(tmp_path / "h.db", snapshots), TODAY)
    assert status.startswith("unknown: ")
    assert bad_date in text and reason in text
    assert "HWM" not in text


def test_missing_db_is_unknown_and_not_created(tmp_path):
    db = tmp_path / "absent.db"
    status, text = job.build_message(db, TODAY)
    assert status.startswith("unknown: ") and "history.db" in text
    assert not db.exists()


def test_missing_table_is_unknown(tmp_path):
    db = tmp_path / "empty.db"
    sqlite3.connect(db).close()
    status, text = job.build_message(db, TODAY)
    assert status.startswith("unknown: ") and "快照表" in text


def test_messages_have_no_telegram_markdown_chars(tmp_path):
    ok = job.build_message(make_db(tmp_path / "a.db", {TODAY: cells()}), TODAY)[1]
    bad = job.build_message(make_db(tmp_path / "b.db", {TODAY: "x"}), TODAY)[1]
    for text in (ok, bad):
        assert not set(text) & set("_*`[")


def test_main_sends_private_and_logs_no_amounts(tmp_path, monkeypatch, capsys):
    db = make_db(tmp_path / "h.db", {TODAY: cells(net=4321)})
    sent = []
    monkeypatch.setattr("sys.argv", ["drawdown", "--db", str(db)])
    clock = lambda: dt.datetime(2026, 10, 15, 1, 5, tzinfo=dt.timezone.utc)
    assert job.main(send=lambda text, channel: sent.append((channel, text)) or True,
                    clock=clock) == 0
    assert [c for c, _ in sent] == ["private"]
    assert "4,321" in sent[0][1]
    out = capsys.readouterr()
    assert out.out == "whole_port_drawdown sent ok\n"
    assert "4,321" not in out.out + out.err


def test_main_reports_unknown_reason_without_amounts(tmp_path, monkeypatch, capsys):
    db = make_db(tmp_path / "h.db", {"2026-10-14": cells(net=4321)})
    monkeypatch.setattr("sys.argv", ["drawdown", "--db", str(db)])
    clock = lambda: dt.datetime(2026, 10, 15, 1, 5, tzinfo=dt.timezone.utc)
    assert job.main(send=lambda text, channel: True, clock=clock) == 0
    assert capsys.readouterr().out.startswith("whole_port_drawdown sent unknown: 今日快照缺失")


def test_main_send_failure_exits_nonzero(tmp_path, monkeypatch):
    db = make_db(tmp_path / "h.db", {TODAY: cells()})
    monkeypatch.setattr("sys.argv", ["drawdown", "--db", str(db)])
    clock = lambda: dt.datetime(2026, 10, 15, 1, 5, tzinfo=dt.timezone.utc)
    assert job.main(send=lambda text, channel: False, clock=clock) == 1


def test_main_rejects_shared_databases(monkeypatch):
    monkeypatch.setattr("sys.argv", ["drawdown", "--db", "data/market.db"])
    with pytest.raises(SystemExit):
        job.main(send=lambda text, channel: True)
