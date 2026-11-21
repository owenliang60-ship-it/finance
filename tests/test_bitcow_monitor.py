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
