"""Dual-axis Extremity Z + S indicator (Python twin of ``extremity_v5_1.pine``).

Pure math: no I/O, no pandas, no timestamps.  Every bar ``i`` only reads bars
``<= i``; the rolling rails of bar ``i`` only read the buffer filled *before*
bar ``i`` (strictly causal, no look-ahead).

Axes
    Z  position: ``(ln C - EWMA84(ln C)) / (c_n * sigma[t-5])``
    S  speed:    ``(ln C - ln C[t-5]) / (sigma[t-5] * sqrt(5))``

``sigma`` is a 25-bar half-life EWMA volatility ruler: Yang-Zhang for equity
(overnight gaps), Garman-Klass for 24h crypto.  The rails are a causal rolling
window (504 equity / 730 crypto bars ~ 2 years) of clamped Z/S with up/down
semi-deviations around the window mean, padded by a ``prior_weight``-bar unit
prior while the window is short.

D1: the EMA anchor is an explicit EWMA seeded with the first close (not
    TradingView's SMA-seeded ``ta.ema``).
D2: when any rail deviation is ``<= STD_EPS`` (halted / flat series) the bar
    is ``warm`` but not ``live``: ``u_dn``/``u_up`` are NaN and every regime
    class and alarm is False.  The buffer keeps rolling, so ``live`` recovers
    once prices move again.
"""

from __future__ import annotations

import math

import numpy as np

RAIL_BARS: dict[str, int] = {"equity": 504, "crypto": 730}
STD_EPS: float = 1e-9

_GK_K = 2.0 * math.log(2.0) - 1.0
_CLAMP = 6.0
_FAKE_DIP_K = 1.2


def _real(name, value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, np.integer, np.floating)):
        raise ValueError(f"{name} must be a real number")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value


def _integer(name, value, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return int(value)


def _ohlc(opens, highs, lows, closes):
    arrays = []
    for name, seq in (("opens", opens), ("highs", highs), ("lows", lows), ("closes", closes)):
        try:
            arr = np.asarray(seq, dtype=np.float64)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} must be a numeric sequence") from exc
        if arr.ndim != 1:
            raise ValueError(f"{name} must be one-dimensional")
        arrays.append(arr)
    n = arrays[0].size
    if n < 1 or any(arr.size != n for arr in arrays):
        raise ValueError("opens/highs/lows/closes must be non-empty and equal-length")
    for arr in arrays:
        if not np.all(np.isfinite(arr)) or np.any(arr <= 0.0):
            raise ValueError("prices must be finite and positive")
    if np.any(arrays[1] < arrays[2]):
        raise ValueError("high must not be below low")
    return arrays


def _ewma(values: np.ndarray, alpha: float) -> np.ndarray:
    """Pine ``ewma()``: seeded with the first non-NaN value; NaN holds the state."""
    out = np.empty(values.size)
    acc = math.nan
    for i, v in enumerate(values.tolist()):
        if not math.isnan(v):
            acc = v if math.isnan(acc) else acc + alpha * (v - acc)
        out[i] = acc
    return out


def _seq_sum(values: np.ndarray) -> float:
    """Old-to-new sequential sum (same rounding as Pine's ``for`` accumulation)."""
    return float(np.cumsum(values)[-1]) if values.size else 0.0


def hysteresis_signals(
    u: np.ndarray,
    k_enter: float,
    hyst_buf: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Debounced two-gate state machine on a standardised deviation series.

    Arms (``enter``) when ``u >= k_enter``; disarms (``exit``) when armed and
    ``u <= k_enter - hyst_buf``.  Non-finite ``u`` fires nothing and keeps the
    armed state.
    """
    try:
        u_arr = np.asarray(u, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise ValueError("u must be a numeric sequence") from exc
    if u_arr.ndim != 1 or u_arr.size < 1:
        raise ValueError("u must be a non-empty one-dimensional sequence")
    k_enter = _real("k_enter", k_enter)
    hyst_buf = _real("hyst_buf", hyst_buf)
    if not k_enter > 0.0:
        raise ValueError("k_enter must be > 0")
    if not 0.0 <= hyst_buf < k_enter:
        raise ValueError("hyst_buf must satisfy 0 <= hyst_buf < k_enter")

    exit_level = k_enter - hyst_buf
    enter = np.zeros(u_arr.size, dtype=bool)
    exit_ = np.zeros(u_arr.size, dtype=bool)
    armed = False
    for i in range(u_arr.size):
        v = float(u_arr[i])
        if not math.isfinite(v):
            continue
        if not armed and v >= k_enter:
            armed = True
            enter[i] = True
        elif armed and v <= exit_level:
            armed = False
            exit_[i] = True
    return enter, exit_


def compute_extremity(
    opens,
    highs,
    lows,
    closes,
    *,
    market: str,
    rail_bars: int | None = None,
    ema_len: int = 84,
    speed_len: int = 5,
    half_life: float = 25.0,
    prior_weight: float = 200.0,
    k_warn: float = 1.5,
    k_ext: float = 2.0,
    k_dip: float = 1.5,
    hyst_buf: float = 0.25,
) -> dict[str, np.ndarray]:
    """Return the 29 per-bar Extremity series (15 float64 + 14 bool) for one OHLC series."""
    o_px, h_px, l_px, c_px = _ohlc(opens, highs, lows, closes)
    if not isinstance(market, str) or market not in RAIL_BARS:
        raise ValueError("market must be 'equity' or 'crypto'")
    ema_len = _integer("ema_len", ema_len, 2)
    speed_len = _integer("speed_len", speed_len, 1)
    rail = RAIL_BARS[market] if rail_bars is None else _integer("rail_bars", rail_bars, 1)
    half_life = _real("half_life", half_life)
    prior_weight = _real("prior_weight", prior_weight)
    k_warn = _real("k_warn", k_warn)
    k_ext = _real("k_ext", k_ext)
    k_dip = _real("k_dip", k_dip)
    hyst_buf = _real("hyst_buf", hyst_buf)
    if not half_life > 0.0:
        raise ValueError("half_life must be > 0")
    alpha = 1.0 - math.pow(0.5, 1.0 / half_life)
    if not alpha > 0.0:
        raise ValueError("half_life is too large: EWMA alpha underflows to 0")
    if not prior_weight >= 0.0:
        raise ValueError("prior_weight must be >= 0")
    if not k_ext > k_warn > 0.0:
        raise ValueError("thresholds must satisfy k_ext > k_warn > 0")
    if not k_dip > 0.0:
        raise ValueError("k_dip must be > 0")
    if not 0.0 <= hyst_buf < k_warn:
        raise ValueError("hyst_buf must satisfy 0 <= hyst_buf < k_warn")

    n = c_px.size
    x, o, h, l = np.log(c_px), np.log(o_px), np.log(h_px), np.log(l_px)

    if market == "crypto":
        d_hl = h - l
        d_xo = x - o
        var_vol = _ewma(np.maximum(0.0, 0.5 * (d_hl * d_hl) - _GK_K * (d_xo * d_xo)), alpha)
    else:
        r_o = np.full(n, np.nan)
        r_o[1:] = o[1:] - x[:-1]
        r_c = x - o
        d_o = r_o - _ewma(r_o, alpha)
        d_c = r_c - _ewma(r_c, alpha)
        var_o = _ewma(d_o * d_o, alpha)
        var_c = _ewma(d_c * d_c, alpha)
        var_rs = _ewma(np.maximum(0.0, (h - x) * (h - o) + (l - x) * (l - o)), alpha)
        n_eff = 2.0 / alpha - 1.0
        k_yz = 0.34 / (1.34 + (n_eff + 1.0) / max(n_eff - 1.0, 1.0))
        var_vol = var_o + k_yz * var_c + (1.0 - k_yz) * var_rs  # NaN on bar 0 propagates

    sigma = np.sqrt(np.maximum(var_vol, 1e-12))  # np.maximum keeps NaN
    sigma_lag = np.full(n, np.nan)
    if n > speed_len:
        sigma_lag[speed_len:] = sigma[:n - speed_len]
    ema_x = _ewma(x, 2.0 / (ema_len + 1.0))
    c_n = (ema_len - 1.0) / (2.0 * math.sqrt(ema_len))
    root_k = math.sqrt(speed_len)

    f = {key: np.full(n, np.nan) for key in (
        "z", "s", "mean_z", "std_up", "std_dn", "std_s", "u_dn", "u_up",
        "z_up_warn", "z_up_ext", "z_dn_warn", "z_dn_ext", "s_ext", "s_dip",
    )}
    b = {key: np.zeros(n, dtype=bool) for key in (
        "warm", "live", "panic_bottom", "deep_oversold", "warn_oversold",
        "climax_top", "slow_grind", "fake_dip",
    )}
    z_hist = np.empty(n)
    s_hist = np.empty(n)
    cnt = 0

    for i in range(ema_len + speed_len, n):
        sl = float(sigma_lag[i])
        ex = float(ema_x[i])
        if math.isnan(ex) or math.isnan(sl) or not sl > 0.0:
            continue
        xi = float(x[i])
        zi = (xi - ex) / (c_n * sl)
        si = (xi - float(x[i - speed_len])) / (sl * root_k)
        if not (math.isfinite(zi) and math.isfinite(si)):
            continue

        start = max(0, cnt - rail)
        zw = z_hist[start:cnt]
        sw = s_hist[start:cnt]
        w_len = float(cnt - start)
        eff_pri = max(0.0, prior_weight - w_len)
        mean = _seq_sum(zw) / max(1.0, w_len + eff_pri)
        dz = zw - mean
        up = dz >= 0.0
        sq = dz * dz
        n_up = float(np.count_nonzero(up))
        n_dn = w_len - n_up
        std_up = math.sqrt(max(0.0, _seq_sum(sq[up]) + eff_pri * 0.5) / max(1.0, n_up + eff_pri * 0.5))
        std_dn = math.sqrt(max(0.0, _seq_sum(sq[~up]) + eff_pri * 0.5) / max(1.0, n_dn + eff_pri * 0.5))
        std_s = math.sqrt(max(0.0, _seq_sum(sw * sw) + eff_pri) / max(1.0, w_len + eff_pri))
        z_up_warn = mean + k_warn * std_up
        z_up_ext = mean + k_ext * std_up
        z_dn_warn = mean - k_warn * std_dn
        z_dn_ext = mean - k_ext * std_dn
        s_ext = k_ext * std_s
        s_dip = k_dip * std_s

        b["warm"][i] = True
        for key, val in (
            ("z", zi), ("s", si), ("mean_z", mean), ("std_up", std_up), ("std_dn", std_dn),
            ("std_s", std_s), ("z_up_warn", z_up_warn), ("z_up_ext", z_up_ext),
            ("z_dn_warn", z_dn_warn), ("z_dn_ext", z_dn_ext), ("s_ext", s_ext), ("s_dip", s_dip),
        ):
            f[key][i] = val

        if std_up > STD_EPS and std_dn > STD_EPS and std_s > STD_EPS:
            climax = zi >= z_up_ext or (zi >= z_up_warn and si >= s_ext)
            b["live"][i] = True
            f["u_dn"][i] = (mean - zi) / std_dn
            f["u_up"][i] = (zi - mean) / std_up
            b["panic_bottom"][i] = zi <= z_dn_ext and si <= -s_ext
            b["deep_oversold"][i] = zi <= z_dn_ext and si > -s_ext
            b["warn_oversold"][i] = z_dn_ext < zi <= z_dn_warn
            b["climax_top"][i] = climax
            b["slow_grind"][i] = zi >= z_up_warn and not climax
            b["fake_dip"][i] = zi > mean - _FAKE_DIP_K * std_dn and si <= -s_dip

        # Push only after the bar is fully judged (no look-ahead).
        z_hist[cnt] = max(-_CLAMP, min(_CLAMP, zi))
        s_hist[cnt] = max(-_CLAMP, min(_CLAMP, si))
        cnt += 1

    enter_dn_warn, exit_dn_warn = hysteresis_signals(f["u_dn"], k_warn, hyst_buf)
    enter_dn_ext, exit_dn_ext = hysteresis_signals(f["u_dn"], k_ext, hyst_buf)
    exit_up_ext = hysteresis_signals(f["u_up"], k_ext, hyst_buf)[1]
    exit_up_warn = hysteresis_signals(f["u_up"], k_warn, hyst_buf)[1]

    return {
        "sigma": sigma, **f, **b,
        "enter_dn_warn": enter_dn_warn, "exit_dn_warn": exit_dn_warn,
        "enter_dn_ext": enter_dn_ext, "exit_dn_ext": exit_dn_ext,
        "exit_up_ext": exit_up_ext, "exit_up_warn": exit_up_warn,
    }
