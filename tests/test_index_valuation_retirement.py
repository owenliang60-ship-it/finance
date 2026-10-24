"""Archived valuation cannot run from public entrypoints or return to reports."""
import json
from unittest.mock import Mock

from scripts import morning_report as morning


def test_historical_cli_stops_before_backup_database_or_network(monkeypatch, tmp_path, capsys):
    from scripts import backfill_index_pe_history as history
    forbidden = Mock(side_effect=AssertionError("archived writer executed"))
    monkeypatch.setattr(history, "open_write_dependencies", forbidden)
    monkeypatch.setattr(history, "FMPClient", forbidden)
    db = tmp_path / "not-created.db"
    assert history.main(["--db", str(db)]) == 2
    assert "archived" in capsys.readouterr().err.lower()
    forbidden.assert_not_called()
    assert not db.exists()


def test_pit_cli_stops_before_valuation_or_database(monkeypatch, tmp_path, capsys):
    from scripts import update_fmp_forward as forward
    forbidden = Mock(side_effect=AssertionError("archived valuation executed"))
    monkeypatch.setattr(forward, "run_valuation_phase", forbidden)
    assert forward.main(["--mode", "weekly", "--phase", "valuation",
                         "--data-root", str(tmp_path)]) == 2
    assert "archived" in capsys.readouterr().err.lower()
    forbidden.assert_not_called()
    assert not (tmp_path / "market.db").exists()


def test_stale_chart_payload_is_absent_from_html_and_pdf_sections():
    signals = {"pmarp": {"hits": []}, "index_valuation_chart": {
        "path": "/old/archived-pe.png", "caption": "retired chart", "available": True}}
    html = morning.build_html_payload(signals, None, "2026-10-24")
    pages = morning.build_morning_visual_sections(signals, None)
    for payload in (html, pages):
        rendered = json.dumps(payload, ensure_ascii=False)
        assert "三指数估值" not in rendered
        assert "archived-pe.png" not in rendered
        assert "PMARP" in rendered


def test_delivery_does_not_prepare_retired_chart(monkeypatch, tmp_path):
    prepared = Mock(return_value={"caption": "retired", "path": None})
    monkeypatch.setattr(morning, "_prepare_index_valuation_chart", prepared, raising=False)
    monkeypatch.setattr(morning, "compile_morning_html_report", lambda *a, **kw: tmp_path / "report.html")
    assert morning._deliver_morning_report(
        {"pmarp": {"hits": []}}, None, "daily", "html", True,
        str(tmp_path), False, "2026-10-24", no_telegram=True)
    prepared.assert_not_called()
