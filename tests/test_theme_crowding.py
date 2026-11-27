"""Unit and CLI safety tests for US Theme Basket Crowding Indicator V2.2."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from backtest.theme_crowding import (
    compute_dd_gate,
    ema_joint_valid,
    fx_to_usd,
    render_dashboard_html,
    roll_pctile_bounded,
    run_hysteresis_states,
    sanitize_symbol_mcap_series,
)
from scripts.theme_crowding_dashboard import main as cli_main


def test_roll_pctile_bounded_extremes_ties_and_bounds() -> None:
    inc = np.arange(10, dtype=float)
    p_inc, w_inc = roll_pctile_bounded(inc, min_window=5, max_window=5)
    assert np.isnan(p_inc[3])
    assert p_inc[4] == pytest.approx(100.0)
    assert w_inc[4] == 5

    dec = np.arange(10, 0, -1, dtype=float)
    p_dec, _ = roll_pctile_bounded(dec, min_window=5, max_window=5)
    assert p_dec[4] == pytest.approx(0.0)

    ties = np.full(8, 42.0)
    p_ties, _ = roll_pctile_bounded(ties, min_window=5, max_window=5)
    assert p_ties[4] == pytest.approx(50.0)

    # Bounded expanding window 5..8 caps at 8 observations (7 prior + today)
    long_inc = np.arange(15, dtype=float)
    _, w_exp = roll_pctile_bounded(long_inc, min_window=5, max_window=8)
    assert w_exp[4] == 5
    assert w_exp[7] == 8
    assert w_exp[12] == 8

    # Missing 1 slot inside min_window-1 prior slots must emit NaN until 4 clean prior values exist
    gapped = np.array([1.0, np.nan, 3.0, 4.0, 5.0, 6.0, 7.0])
    p_gap, _ = roll_pctile_bounded(gapped, min_window=5, max_window=5)
    assert np.isnan(p_gap[4]) and np.isnan(p_gap[5])
    assert p_gap[6] == pytest.approx(100.0)


def test_dd_gate_piecewise_linear() -> None:
    assert compute_dd_gate(0.00) == pytest.approx(1.0)
    assert compute_dd_gate(0.05) == pytest.approx(1.0)
    assert compute_dd_gate(0.125) == pytest.approx(0.5)
    assert compute_dd_gate(0.20) == pytest.approx(0.0)
    assert compute_dd_gate(0.35) == pytest.approx(0.0)


def test_ema_joint_valid_exact_additivity_even_with_internal_nan_gaps() -> None:
    s1 = np.array([np.nan, 2.0, np.nan, 8.0, 6.0])
    s2 = np.array([np.nan, 4.0, 10.0, 6.0, 4.0])
    s3 = np.array([np.nan, 6.0, 10.0, 4.0, 2.0])
    s4 = np.array([np.nan, 8.0, 10.0, 2.0, 8.0])
    s5 = np.array([np.nan, 5.0, 10.0, 9.0, 3.0])

    raw = (0.5 * (s1 + s2) + 0.5 * (s3 + s4) + s5) / 3.0
    mask = ~np.isnan(raw)

    e_subs = [ema_joint_valid(arr, mask, span=3) for arr in (s1, s2, s3, s4, s5)]
    combo = (0.5 * (e_subs[0] + e_subs[1]) + 0.5 * (e_subs[2] + e_subs[3]) + e_subs[4]) / 3.0
    direct = ema_joint_valid(raw, mask, span=3)

    assert np.isnan(combo[0]) and np.isnan(combo[2])
    assert np.allclose(combo[mask], direct[mask], atol=1e-14)


def test_hysteresis_lamp_and_low_score_semantics() -> None:
    crowd = np.array([6.0, 8.1, 7.6, 9.2, 8.6, 8.4, 7.4, 4.2, 4.3, 4.4])
    dd_gate = np.array([1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 0.0, 1.0, 1.0])
    d_vol = np.array([5.0, 8.0, 7.5, 9.0, 8.5, 8.0, 6.0, 8.2, 3.1, 5.5])

    codes, labels, entries = run_hysteresis_states(crowd, dd_gate, d_vol, start_idx=0)
    assert entries == [1]  # Single hot episode across index 1..5 until dropping <7.5 at index 6
    assert codes[:7] == ["NEUTRAL", "WARM", "WARM", "CROWDED", "CROWDED", "WARM", "NEUTRAL"]
    assert codes[7] == "PULLBACK_HIGHVOL" and "放量回调" in labels[7]
    assert codes[8] == "COLD" and labels[8] == "缩量冷清低位"
    assert codes[9] == "LOW_MILD" and labels[9] == "温和低分区"


def test_sanitize_symbol_mcap_drops_transient_glitch_keeps_permanent_merger() -> None:
    dates = [f"2026-01-{d:02d}" for d in range(1, 32)] + [f"2026-02-{d:02d}" for d in range(1, 28)]
    adj_close = {d: 100.0 for d in dates}

    # Case 1: 8-day 1000x drop (like BRK-B Nov 2026) -> all 8 corrupted points dropped
    pts_glitch = []
    for idx, d in enumerate(dates[:30]):
        mc = 100e9 if not (12 <= idx < 20) else 0.1e9
        pts_glitch.append((d, mc))
    cleaned_glitch = sanitize_symbol_mcap_series(pts_glitch, adj_close)
    assert len(cleaned_glitch) == 30 - 8
    assert all(mc == pytest.approx(100e9) for _, mc in cleaned_glitch)

    # Case 2: Permanent 4x share-count step (like WBD merger >=25 bars) -> accepted once >=25 bars confirm
    pts_merger = [(d, 100e9 if idx < 15 else 400e9) for idx, d in enumerate(dates)]
    cleaned_merger = sanitize_symbol_mcap_series(pts_merger, adj_close)
    assert any(mc == pytest.approx(400e9) for _, mc in cleaned_merger)


def test_fx_to_usd_past_only_no_future_peek() -> None:
    fx_table = {"EUR": (["2021-09-03", "2021-09-10"], [1.18, 1.19])}
    assert fx_to_usd(fx_table, "USD", "2020-01-01") == pytest.approx(1.0)
    assert fx_to_usd(fx_table, "EUR", "2021-06-30") is None  # Must NOT peek at 2021-09-03!
    assert fx_to_usd(fx_table, "EUR", "2021-09-05") == pytest.approx(1.18)
    assert fx_to_usd(fx_table, "EUR", "2022-06-30") is None  # >90 days stale -> fail-closed


def test_cli_refuses_overwriting_input_db_and_renders_valid_html(tmp_path: Path) -> None:
    fake_db = tmp_path / "market.db"
    fake_db.write_bytes(b"sqlite-placeholder")
    fake_wal = tmp_path / "market.db-wal"
    fake_wal.write_bytes(b"wal")

    assert cli_main(["--market-db", str(fake_db), "--out-html", str(fake_db)]) == 4
    assert cli_main(["--market-db", str(fake_db), "--out-html", str(fake_wal)]) == 4
    assert cli_main(["--market-db", str(fake_db), "--out-html", str(tmp_path)]) == 4

    sample_payload = {
        "version": "v2.2_codex_audited",
        "as_of": "2026-11-25",
        "baskets": {
            c: {
                "id": c,
                "name": f"Test </script> {c}",
                "kind": "curated_core",
                "weekly_pit_start": None,
                "source_note": "test",
                "n_members": 4,
                "valid_start": "2023-11-17",
                "valid_end": "2026-11-25",
                "n_valid_days": 1,
                "latest": {
                    "crowding": 5.0,
                    "crowding_raw": 5.0,
                    "state_code": "NEUTRAL",
                    "state_label": "常态中性",
                    "d_ret": 5.0,
                    "d_vol": 5.0,
                    "d_val": 5.0,
                    "bias200_pct": 1.0,
                    "p_bias200": 50.0,
                    "s_mom_raw": 5.0,
                    "s_mom": 5.0,
                    "exvol20_pct": 15.0,
                    "p_exvol_raw": 50.0,
                    "dd60_pct": 2.0,
                    "dd_gate": 1.0,
                    "s_exvol_raw": 5.0,
                    "s_exvol": 5.0,
                    "dv5_usd_bn": 10.0,
                    "dv5_share_pct": 2.0,
                    "p_dvshare": 50.0,
                    "s_dvshare_raw": 5.0,
                    "s_dvshare": 5.0,
                    "turn5_pct": 1.0,
                    "p_turn": 50.0,
                    "s_turn_raw": 5.0,
                    "s_turn": 5.0,
                    "sp_pct": 10.0,
                    "ps_multiple": 10.0,
                    "val_window_days": 500,
                    "p_sp_crowd": 50.0,
                    "s_val_raw": 5.0,
                    "s_val": 5.0,
                },
                "bucket_stats": [{"bucket": "全部日期基线 (无条件平均)", "n_days": 1, "fwd60_mean": 5.0, "fwd60_vs_base": 0.0, "fwd60_mdd_mean": -5.0, "prob_dd15_60d": 0.0, "is_baseline": True}],
                "episodes_ge8": [],
                "constituents_top": [],
                "constituents_total_count": 0,
                "series": [{"d": "2026-11-25", "nav_full": 100.0, "nav_2026": 100.0, "c": 5.0, "c_raw": 5.0, "c_v1": 5.0, "st": "NEUTRAL", "st_lbl": "常态中性", "s_mom": 5.0, "s_vol": 5.0, "s_dvs": 5.0, "s_trn": 5.0, "s_val": 5.0}],
            }
            for c in ("SEMI_EQUIP", "QQQ", "XLF", "MEMORY", "SOX", "HYPERSCALERS", "IGV")
        },
        "equal_basket_regime_summary": [
            {"code": c, "name": c, "latest_crowd": 5.0, "state_code": "NEUTRAL", "state_label": "常态中性", "base_r60": 5.0, "base_p15": 0.0, "low_r60": None, "hot_n": 0, "hot_r60": None, "hot_vs_base": None, "hot_p15": None}
            for c in ("SEMI_EQUIP", "QQQ", "XLF", "MEMORY", "SOX", "HYPERSCALERS", "IGV")
        ],
    }
    html = render_dashboard_html(sample_payload)
    assert "</script>" in html
    assert "Test <\\/script> SOX" in html
