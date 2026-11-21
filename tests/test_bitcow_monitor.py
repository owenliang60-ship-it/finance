"""BitCow daily BTC signal monitor tests (offline; fixture tests/fixtures/bitcow)."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts import bitcow_monitor as bm
from scripts.bitcow_monitor import DAY_MS, Bar, DataError, ema_atr

FIXTURE = Path(__file__).parent / "fixtures" / "bitcow" / "btcusdt_1d.csv"


def _load_fixture() -> list[Bar]:
    with FIXTURE.open(newline="") as f:
        return [
            Bar(int(r["open_ms"]), float(r["high"]), float(r["low"]), float(r["close"]))
            for r in csv.DictReader(f)
        ]


ALL_BARS = _load_fixture()


def _ms(day: str) -> int:
    return int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)


def _upto(day: str) -> list[Bar]:
    end = _ms(day)
    bars = [b for b in ALL_BARS if b.open_ms <= end]
    assert bars[-1].open_ms == end
    return bars


def _d(bars: list[Bar]) -> float:
    ema, atr = ema_atr(bars)
    return (bars[-1].close - ema[-1]) / atr[-1]


# ---------------------------------------------------------------- T1 ema_atr


@pytest.mark.parametrize(
    "day, expected, tol",
    [
        ("2022-06-18", -6.914, 1e-3),
        ("2026-02-05", -6.725, 1e-3),
        ("2022-07-02", -6.882, 1e-3),
        ("2024-03-04", 8.760, 1e-3),
        ("2020-03-12", -6.10, 5e-3),
    ],
)
def test_d_historical_anchors(day, expected, tol):
    assert _d(_upto(day)) == pytest.approx(expected, abs=tol)


def test_d_last_1000_matches_full_history():
    full = _upto("2026-11-20")
    assert abs(_d(full) - _d(full[-1000:])) < 1e-9


def test_ema_atr_formulas_small_series():
    bars = [Bar(i * DAY_MS, 12.0 + i, 8.0 + i, 10.0 + i) for i in range(5)]
    ema, atr = ema_atr(bars, ema_len=3, atr_len=2)
    k = 2.0 / 4
    exp_ema = [10.0]
    for b in bars[1:]:
        exp_ema.append(b.close * k + exp_ema[-1] * (1 - k))
    exp_atr = [4.0]
    for i in range(1, 5):
        h, lo, pc = bars[i].high, bars[i].low, bars[i - 1].close
        tr = max(h - lo, abs(h - pc), abs(lo - pc))
        exp_atr.append((exp_atr[-1] * 1 + tr) / 2)
    assert ema == pytest.approx(exp_ema)
    assert atr == pytest.approx(exp_atr)


def test_ema_atr_rejects_short_series_and_zero_atr():
    with pytest.raises(DataError):
        ema_atr([Bar(0, 2.0, 1.0, 1.5)] * 10, ema_len=84, atr_len=14)
    flat = [Bar(i * DAY_MS, 5.0, 5.0, 5.0) for i in range(100)]
    with pytest.raises(DataError):
        ema_atr(flat)


# ---------------------------------------------------------------- T2 mid_step

# ema=100, atr=10 → upper=110, lower=90; near band (<0.5 ATR) = (90, 95) / (105, 110)
@pytest.mark.parametrize(
    "prev, close, expected",
    [
        (1, 89.99, (-1, "flip_short")),
        (1, 90.0, (1, "warn_long_near")),  # close == lower: no flip, near stop
        (1, 94.99, (1, "warn_long_near")),
        (1, 95.0, (1, "warn_long_mid")),
        (1, 99.99, (1, "warn_long_mid")),
        (1, 100.0, (1, None)),
        (1, 120.0, (1, None)),
        (-1, 110.01, (1, "flip_long")),
        (-1, 110.0, (-1, "warn_short_near")),  # close == upper: no flip, near stop
        (-1, 105.01, (-1, "warn_short_near")),
        (-1, 105.0, (-1, "warn_short_mid")),
        (-1, 100.01, (-1, "warn_short_mid")),
        (-1, 100.0, (-1, None)),
        (-1, 80.0, (-1, None)),
        (0, 120.0, (1, None)),
        (0, 80.0, (-1, None)),
        (0, 100.0, (0, None)),
    ],
)
def test_mid_step_scenarios(prev, close, expected):
    assert bm.mid_step(prev, close, 100.0, 10.0) == expected


def _mid_events(bars: list[Bar]) -> dict[str, tuple[int, str | None]]:
    ema, atr = ema_atr(bars)
    state, out = 0, {}
    for i, b in enumerate(bars):
        state, event = bm.mid_step(state, b.close, ema[i], atr[i])
        out[datetime.fromtimestamp(b.open_ms / 1000, tz=timezone.utc).date().isoformat()] = (state, event)
    return out


@pytest.mark.parametrize("window", [None, 1000])
def test_mid_step_real_anchors(window):
    bars = _upto("2026-11-20")
    if window:
        bars = bars[-window:]
    ev = _mid_events(bars)
    assert ev["2026-04-17"] == (1, "flip_long")
    assert ev["2026-08-19"] == (1, "flip_long")
    assert ev["2026-11-06"] == (-1, "flip_short")
    assert ev["2026-11-05"][0] == 1 and ev["2026-11-05"][1].startswith("warn_long")
    assert ev["2026-11-20"][0] == -1 and ev["2026-11-20"][1] not in ("flip_long", "flip_short")


# ---------------------------------------------------------------- T3 slow_layer


def test_slow_layer_sunday_flip_bear():
    s = bm.slow_layer(_upto("2026-11-08"))
    assert s["flipped"] == "bear" and s["bull"] is False
    assert s["weekly_date"] == "2026-11-08" and s["since"] == "2026-11-08"


def test_slow_layer_monday_after_flip_is_not_flipped():
    bars = _upto("2026-11-09")
    s = bm.slow_layer(bars)
    assert s["flipped"] is None and s["bull"] is False
    assert s["weekly_date"] == "2026-11-08"
    assert s["daily_vs_sma50_pct"] == pytest.approx((bars[-1].close / s["sma50"] - 1.0) * 100.0)


@pytest.mark.parametrize("day, flipped", [("2026-09-20", "bull"), ("2025-11-16", "bear")])
def test_slow_layer_historical_flips(day, flipped):
    s = bm.slow_layer(_upto(day))
    assert s["flipped"] == flipped and s["since"] == day


@pytest.mark.parametrize("window", [None, 1000])
def test_slow_layer_2026_11_15_values(window):
    bars = _upto("2026-11-15")
    if window:
        bars = bars[-window:]
    s = bm.slow_layer(bars)
    assert s["weekly_date"] == "2026-11-15"
    assert s["wclose"] == 65351.05
    assert s["sma50"] == pytest.approx(75035.1, abs=0.1)
    assert s["since"] == "2026-11-08"
    assert s["bull"] is False and s["flipped"] is None


def test_slow_layer_rejects_fewer_than_51_weeks():
    with pytest.raises(DataError):
        bm.slow_layer(ALL_BARS[:350])


def test_slow_layer_since_falls_back_to_first_sma_week_without_flip():
    # 60 synthetic rising weeks starting on a Sunday → always bull, never flips
    sunday0 = _ms("2017-08-20")
    bars = [Bar(sunday0 + i * DAY_MS, 1.0 + i + 0.5, 1.0 + i - 0.5, 1.0 + i) for i in range(60 * 7)]
    s = bm.slow_layer(bars)
    first_sma_week = datetime.fromtimestamp((sunday0 + 49 * 7 * DAY_MS) / 1000, tz=timezone.utc).date().isoformat()
    assert s["bull"] is True and s["since"] == first_sma_week


# ---------------------------------------------------------------- T4 d_level + evaluate

DVOL_NA = {"available": False, "value": None, "percentile": None, "reason": "接口异常"}


@pytest.mark.parametrize(
    "d, lvl",
    [(4.49, 0), (-4.49, 0), (4.5, 1), (-4.5, 1), (-6.5, 2), (6.99, 2), (-7.0, 3), (9.0, 3), (0.0, 0)],
)
def test_d_level_boundaries(d, lvl):
    assert bm.d_level(d) == lvl


def _synthetic(closes_tail: list[float], base: float = 115.0, n: int = 400) -> list[Bar]:
    # start on a Thursday so the last bar (n-1 days later) is not a Sunday; slow layer never flips
    start = _ms("2020-01-02")
    closes = [base] * (n - len(closes_tail)) + list(closes_tail)
    return [Bar(start + i * DAY_MS, c + 1.0, c - 1.0, c) for i, c in enumerate(closes)]


@pytest.fixture
def flat_channel(monkeypatch):
    """Pin EMA=100 / ATR=10 so upper=110, lower=90 and D=(close-100)/10."""

    def fake(bars, ema_len=84, atr_len=14):
        return [100.0] * len(bars), [10.0] * len(bars)

    monkeypatch.setattr(bm, "ema_atr", fake)


def _alerts(closes_tail, base=115.0):
    return bm.evaluate(_synthetic(closes_tail, base), DVOL_NA)["alerts"]


def test_evaluate_d_alert_first_entry_upgrade_and_persistence(flat_channel):
    first = _alerts([140.0, 146.0])  # D 4.0 → 4.6
    assert len(first) == 1 and "+4.5" in first[0]
    up = _alerts([146.0, 166.0])  # D 4.6 → 6.6
    assert len(up) == 1 and "+6.5" in up[0]
    top = _alerts([166.0, 172.0])  # D 6.6 → 7.2
    assert len(top) == 1 and "+7.0" in top[0]
    assert _alerts([166.0, 168.0]) == []  # same tier persists → no 🚨
    assert _alerts([172.0, 166.0]) == []  # downgrade → no 🚨


def test_evaluate_d_alert_on_sign_flip_same_tier(flat_channel):
    alerts = _alerts([146.0, 54.0])  # D +4.6 → -4.6, also breaks lower band
    assert any("中层翻空" in a for a in alerts)
    assert any("-4.5" in a for a in alerts)


def test_evaluate_warn_alert_entry_upgrade_and_persistence(flat_channel):
    entry = _alerts([105.0, 97.0])
    assert len(entry) == 1 and "跌破EMA84中线" in entry[0]
    up = _alerts([97.0, 93.0])
    assert len(up) == 1 and "极近多头ATR止损" in up[0]
    assert _alerts([93.0, 92.0]) == []  # near persists
    assert _alerts([97.0, 98.0]) == []  # mid persists
    assert _alerts([93.0, 97.0]) == []  # near → mid downgrade
    short_up = _alerts([103.0, 107.0], base=85.0)
    assert len(short_up) == 1 and "极近空头ATR止损" in short_up[0]
    short_entry = _alerts([95.0, 103.0], base=85.0)
    assert len(short_entry) == 1 and "站上EMA84中线" in short_entry[0]


def test_evaluate_flip_alerts_and_stop_fields(flat_channel):
    rep = bm.evaluate(_synthetic([115.0, 89.0]), DVOL_NA)
    assert rep["mid_state"] == -1 and rep["mid_event"] == "flip_short"
    assert rep["alerts"] == ["触发多头ATR止损 / 中层翻空：收盘跌破下沿"]
    assert rep["stop_price"] == 110.0
    assert rep["stop_dist_atr"] == pytest.approx(2.1)
    assert rep["stop_dist_pct"] == pytest.approx((110.0 / 89.0 - 1.0) * 100.0)
    rep = bm.evaluate(_synthetic([85.0, 111.0], base=85.0), DVOL_NA)
    assert rep["alerts"] == ["触发空头ATR止损 / 中层翻多：收盘站上上沿"]
    assert rep["stop_price"] == 90.0
    assert rep["stop_dist_atr"] == pytest.approx(2.1)
    assert rep["stop_dist_pct"] == pytest.approx((111.0 / 90.0 - 1.0) * 100.0)


def test_evaluate_mid_since_falls_back_to_first_nonzero(flat_channel):
    bars = _synthetic([])
    rep = bm.evaluate(bars, DVOL_NA)
    assert rep["mid_since"] == "2020-01-02" and rep["alerts"] == []


def test_evaluate_mid_state_zero_raises(flat_channel):
    with pytest.raises(DataError):
        bm.evaluate(_synthetic([], base=100.0), DVOL_NA)


def test_evaluate_real_2026_11_20():
    rep = bm.evaluate(_upto("2026-11-20")[-1000:], DVOL_NA)
    # independent pandas ewm(span=84) / ewm(alpha=1/14) recomputation, last 1000 bars
    assert rep["as_of"] == "2026-11-20" and rep["close"] == 58951.51
    assert rep["ema84"] == pytest.approx(75420.637556, abs=1e-4)
    assert rep["atr14"] == pytest.approx(2800.827227, abs=1e-4)
    assert rep["d"] == pytest.approx(-5.880094, abs=1e-5)
    assert rep["d_level"] == 1
    assert rep["d_minus7"] == pytest.approx(55814.846965, abs=1e-4)
    assert rep["d_plus7"] == pytest.approx(95026.428146, abs=1e-4)
    assert rep["mid_state"] == -1 and rep["mid_since"] == "2026-11-06"
    assert rep["stop_price"] == pytest.approx(78221.464783, abs=1e-4)
    assert rep["stop_dist_atr"] == pytest.approx(6.880094, abs=1e-5)
    assert rep["stop_dist_pct"] == pytest.approx(32.687805, abs=1e-5)
    assert rep["slow"]["bull"] is False and rep["dvol"] is DVOL_NA


def test_evaluate_real_flip_days():
    rep = bm.evaluate(_upto("2026-11-06")[-1000:], DVOL_NA)
    assert "触发多头ATR止损 / 中层翻空：收盘跌破下沿" in rep["alerts"]
    rep = bm.evaluate(_upto("2026-11-08")[-1000:], DVOL_NA)
    assert "慢层翻空：周线收盘跌破50W SMA" in rep["alerts"]


def test_evaluate_warn_right_after_flip_is_new_alert(flat_channel):
    alerts = _alerts([89.0, 103.0])  # yesterday flip_short, today back above EMA84
    assert len(alerts) == 1 and "站上EMA84中线" in alerts[0]
    alerts = _alerts([111.0, 93.0], base=85.0)  # yesterday flip_long, today near lower band
    assert len(alerts) == 1 and "极近多头ATR止损" in alerts[0]
