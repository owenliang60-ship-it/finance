"""Core calculation engine for US Theme Basket Crowding Indicator V2.2 (Read-Only).

Design lineage:
- Upgraded 3-dimension (Return 1/3, Volume 1/3, Valuation 1/3) x 5 sub-indicator 0-10 crowding model.
- Neon x Fable dual-brain debate consensus:
  1. Post-percentile causal EMA3 (span=3) on joint valid mask -> exact additivity across sub-scores.
  2. Asymmetric rolling window: 500 valid bars for price/vol/volume; bounded 500..1000 bars for TTM SP.
  3. Dual-threshold hysteresis lamp: Warm enters >= 8.0 / exits < 7.5; Crowded enters >= 9.0 / exits < 8.5.
  4. Semantic 3-way low-score (<5.0) tags distinguishing high-volume pullbacks from dry low-volume troughs.
- Codex (`gpt-6.2-astra`) full-chain audit hardening:
  - Causal implied-shares filter on `historical_market_cap` + dual-class issuer cross-fill + 15d price-ratio bridge.
  - Pre-2026-07-13 PIT quarterly fund disclosures (`fmp_fund_disclosure_holdings`) wired for QQQ & SOX/SOXX.
  - T-1 portfolio & T-1 market-cap weighting for daily returns (>=70% coverage gate, zero silent equal-weight flip).
  - PIT `fundamental_vintage` version replay + `fmp_earnings` (±21d) announce fallback + explicit negative-TTM revocation.
  - Past-only FX lookup, canonical ticker alias (`SKHYV -> SKHY`), `BK/BNY` & `non_common_instrument` market DV dedup,
    real cumulative bar count for IPO warmup, and split-suspect masking on dollar volume.
"""
from __future__ import annotations

import bisect
import json
import math
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

import numpy as np
import pandas as pd

from backtest.research.prosperity_health import PriceBook
from src.data.market_store import MarketStore
from terminal.prosperity.config import DATE_REMOVING_REPAIRS

MA_WINDOW = 200
MOM_V1_WINDOW = 243
VOL_WINDOW = 20
VOL_SMOOTH_DAYS = 5
DD_GATE_LOOKBACK = 60
DD_GATE_FREE_BUFFER = 0.05
DD_GATE_ZERO_AT = 0.20
MIN_LISTING_BARS = 30
PCTILE_WINDOW = 500
VAL_PCTILE_MIN_WINDOW = 500
VAL_PCTILE_MAX_WINDOW = 1000
POST_EMA_SPAN = 3

HYST_WARM_IN = 8.0
HYST_WARM_OUT = 7.5
HYST_CROWD_IN = 9.0
HYST_CROWD_OUT = 8.5

FX_FALLBACK_USD_PER_UNIT = {
    "USD": 1.0,
    "CAD": 0.73,
    "GBP": 1.27,
    "AUD": 0.66,
    "ILS": 0.27,
    "CHF": 1.13,
    "JPY": 0.0067,
    "HKD": 0.128,
    "BRL": 0.19,
    "DKK": 0.145,
    "SEK": 0.095,
}

TICKER_CANONICAL_ALIAS = {
    "SKHYV": "SKHY",
}

DUAL_CLASS_DV_MAP = {
    "GOOGL": ["GOOG"],
    "BRK-B": ["BRK-A"],
    "FOXA": ["FOX"],
    "NWSA": ["NWS"],
}


def _safe_round(val: Optional[float], ndigits: int = 2) -> Optional[float]:
    if val is None:
        return None
    f = float(val)
    if not math.isfinite(f):
        return None
    return round(f, ndigits)


def canon_sym(raw: Optional[str]) -> str:
    s = (raw or "").strip().upper()
    return TICKER_CANONICAL_ALIAS.get(s, s)


def load_baskets_pit(conn: sqlite3.Connection) -> Dict[str, dict]:
    baskets: Dict[str, dict] = {}
    etf_meta = [
        ("SOX", "SOXX", "费城半导体 (SOX)", "PIT历史季报持仓(2021.11起22期) + 官方周快照(2026-07-13起)"),
        ("IGV", None, "北美软件 (IGV)", "官方ETF周快照 (2026-07-13起PIT；此前为首期快照代理回放)"),
        ("XLF", None, "标普金融 (XLF)", "官方ETF周快照 (2026-07-13起PIT；此前为首期快照代理回放)"),
        ("QQQ", "QQQ", "纳斯达克100 (QQQ)", "PIT历史季报持仓(2021.05起24期) + 官方周快照(2026-07-13起)"),
    ]
    has_fdh_table = bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='fmp_fund_disclosure_holdings'"
        ).fetchone()
    )

    for code, fdh_symbol, title, source_note in etf_meta:
        snaps_raw = conn.execute(
            "SELECT snapshot_date, symbol, raw_asset, included, filter_reason "
            "FROM fmp_etf_holdings_snapshot WHERE basket=? ORDER BY snapshot_date",
            (code,),
        ).fetchall()
        by_snap: Dict[str, Tuple[Set[str], Set[str]]] = {}
        all_primary: Set[str] = set()
        all_dv_extra: Set[str] = set()
        for sdate, sym, raw_asset, inc, reason in snaps_raw:
            prim, dvx = by_snap.setdefault(sdate, (set(), set()))
            if inc == 1 and sym:
                s_up = canon_sym(sym)
                prim.add(s_up)
                all_primary.add(s_up)
                for sec in DUAL_CLASS_DV_MAP.get(s_up, []):
                    dvx.add(sec)
                    all_dv_extra.add(sec)
            elif reason == "dual_class_secondary" and raw_asset:
                r_up = canon_sym(raw_asset)
                dvx.add(r_up)
                all_dv_extra.add(r_up)

        first_weekly_date = min(by_snap.keys()) if by_snap else "9999-12-31"

        if fdh_symbol and has_fdh_table:
            fdh_rows = conn.execute(
                "SELECT composition_available_date, symbol, raw_symbol, included, filter_reason "
                "FROM fmp_fund_disclosure_holdings "
                "WHERE basket_symbol=? AND composition_available_date IS NOT NULL AND composition_available_date < ? "
                "ORDER BY composition_available_date",
                (fdh_symbol, first_weekly_date),
            ).fetchall()
            for cad, sym, raw_sym, inc, reason in fdh_rows:
                cad10 = cad[:10]
                prim, dvx = by_snap.setdefault(cad10, (set(), set()))
                if inc == 1 and sym:
                    s_up = canon_sym(sym)
                    prim.add(s_up)
                    all_primary.add(s_up)
                    for sec in DUAL_CLASS_DV_MAP.get(s_up, []):
                        dvx.add(sec)
                        all_dv_extra.add(sec)
                elif reason == "dual_class_secondary" and raw_sym:
                    r_up = canon_sym(raw_sym)
                    dvx.add(r_up)
                    all_dv_extra.add(r_up)

        snap_dates = sorted(d for d in by_snap.keys() if by_snap[d][0])
        snapshots = [
            (sd, sorted(by_snap[sd][0]), sorted(by_snap[sd][1] - by_snap[sd][0]))
            for sd in snap_dates
        ]
        baskets[code] = {
            "id": code,
            "name": title,
            "kind": "official_etf",
            "has_pre2026_pit_holdings": bool(fdh_symbol and has_fdh_table),
            "weekly_pit_start": first_weekly_date if first_weekly_date != "9999-12-31" else None,
            "source_note": source_note,
            "snapshots": snapshots,
            "all_members": sorted(all_primary),
            "all_dv_extra": sorted(all_dv_extra - all_primary),
            "ipo_warmup_bars": MIN_LISTING_BARS,
        }

    curated = [
        (
            "MEMORY",
            "存储芯片核心 (Memory)",
            "手工纯正小篮子 (MU / WDC / STX / SNDK)",
            ["MU", "WDC", "STX", "SNDK"],
            [],
        ),
        (
            "SEMI_EQUIP",
            "半导体设备五巨头 (Semi Equip)",
            "手工纯正小篮子 (ASML / AMAT / LRCX / KLAC / TER)",
            ["ASML", "AMAT", "LRCX", "KLAC", "TER"],
            [],
        ),
        (
            "HYPERSCALERS",
            "超大规模云五巨头 (Hyperscalers)",
            "手工纯正小篮子 (MSFT / AMZN / GOOGL+GOOG / META / ORCL)",
            ["MSFT", "AMZN", "GOOGL", "META", "ORCL"],
            ["GOOG"],
        ),
    ]
    for code, title, source_note, members, dv_extra in curated:
        baskets[code] = {
            "id": code,
            "name": title,
            "kind": "curated_core",
            "has_pre2026_pit_holdings": True,
            "weekly_pit_start": None,
            "source_note": source_note,
            "snapshots": [("2000-01-01", sorted(members), sorted(dv_extra))],
            "all_members": sorted(members),
            "all_dv_extra": sorted(dv_extra),
            "ipo_warmup_bars": 0,
        }
    return baskets


def load_market_daily_dv(
    conn: sqlite3.Connection, calendar_set: Set[str], suspect_all: Dict[str, Set[str]]
) -> Dict[str, Tuple[float, int]]:
    excl_syms = {
        r[0]
        for r in conn.execute(
            "SELECT symbol FROM security_master "
            "WHERE is_etf=1 OR is_fund=1 OR reason='non_common_instrument'"
        ).fetchall()
    }
    excl_syms.update({"SPY", "QQQ", "SOXX", "IVV", "VOO", "IWM", "DIA", "SMH", "IGV", "XLF", "DRAM", "TEST", "^VIX"})

    rows = conn.execute(
        "SELECT date, symbol, close * volume AS dv FROM daily_price WHERE close > 0 AND volume > 0"
    ).fetchall()
    present_by_day: Dict[str, Set[str]] = defaultdict(set)
    for d, sym, _dv in rows:
        if d in calendar_set:
            present_by_day[d].add(sym)

    tot_dv: Dict[str, float] = defaultdict(float)
    cnt: Dict[str, int] = defaultdict(int)
    for d, sym, dv in rows:
        if d not in calendar_set or sym in excl_syms or sym.endswith(".HK") or sym.startswith("^"):
            continue
        if sym == "BK" and "BNY" in present_by_day[d]:
            continue
        if sym == "SKHYV" and "SKHY" in present_by_day[d]:
            continue
        if d in suspect_all.get(sym, set()):
            continue
        if dv is not None and math.isfinite(dv) and dv > 0:
            tot_dv[d] += float(dv)
            cnt[d] += 1
    return {d: (tot_dv[d], cnt[d]) for d in tot_dv}


def load_fx_table(conn: sqlite3.Connection) -> Dict[str, Tuple[List[str], List[float]]]:
    has_fx = bool(
        conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='fx_daily'").fetchone()
    )
    if not has_fx:
        return {}
    rows = conn.execute(
        "SELECT currency, date, usd_per_unit FROM fx_daily WHERE usd_per_unit > 0 ORDER BY currency, date"
    ).fetchall()
    by_ccy: Dict[str, Tuple[List[str], List[float]]] = {}
    for ccy, d, rate in rows:
        ds, rs = by_ccy.setdefault(ccy, ([], []))
        ds.append(d)
        rs.append(float(rate))
    return by_ccy


def fx_to_usd(fx_table: Dict[str, Tuple[List[str], List[float]]], ccy: Optional[str], day: str) -> Optional[float]:
    """Past-only FX lookup (never peeks into future `rs[0]` when `day < ds[0]`)."""
    c = (ccy or "USD").upper()
    if c == "USD":
        return 1.0
    if c in fx_table:
        ds, rs = fx_table[c]
        idx = bisect.bisect_right(ds, day) - 1
        if idx < 0:
            return None
        gap_days = (datetime.strptime(day[:10], "%Y-%m-%d") - datetime.strptime(ds[idx][:10], "%Y-%m-%d")).days
        if gap_days > 90:
            return None
        return rs[idx]
    return FX_FALLBACK_USD_PER_UNIT.get(c)


def sanitize_symbol_mcap_series(
    pts: List[Tuple[str, float]], adj_close_by_date: Dict[str, float]
) -> List[Tuple[str, float]]:
    """Causally drop transient FMP unit/share-count glitches while accepting >=25-bar corporate actions."""
    if not pts:
        return []
    clean: List[Tuple[str, float]] = []
    sh_hist: List[float] = []
    pending_regime: List[float] = []

    for d, mc in pts:
        ac = adj_close_by_date.get(d)
        if ac is None or not math.isfinite(ac) or ac <= 0 or mc <= 0:
            continue
        imp_sh = mc / ac
        if len(sh_hist) >= 10:
            ref_med = float(np.median(sh_hist[-60:]))
            ratio = imp_sh / ref_med if ref_med > 0 else 1.0
            if ratio > 3.2 or ratio < (1.0 / 3.2):
                if pending_regime and (0.75 <= imp_sh / float(np.median(pending_regime)) <= 1.33):
                    pending_regime.append(imp_sh)
                else:
                    pending_regime = [imp_sh]
                if len(pending_regime) >= 25:
                    sh_hist.extend(pending_regime)
                    pending_regime = []
                    clean.append((d, mc))
                continue
            pending_regime = []
        sh_hist.append(imp_sh)
        clean.append((d, mc))
    return clean


def compute_eff_date(
    fdate10: str,
    acc_d: Optional[str],
    filing_d: Optional[str],
    earn_ann_by_sym: Dict[str, List[Tuple[str, str]]],
    sym: str,
) -> str:
    f_dt = datetime.strptime(fdate10, "%Y-%m-%d")
    min_plausible = (f_dt + timedelta(days=6)).strftime("%Y-%m-%d")
    valid_effs = []
    for cand in (acc_d, filing_d):
        c10 = (cand or "")[:10]
        if len(c10) == 10 and c10 >= min_plausible:
            valid_effs.append(c10)
    if not valid_effs:
        best_ann: Optional[str] = None
        best_dist = 999
        for ef_d, ann_d in earn_ann_by_sym.get(sym, []):
            dist = abs((datetime.strptime(ef_d, "%Y-%m-%d") - f_dt).days)
            if dist <= 21 and ann_d >= min_plausible and dist < best_dist:
                best_dist = dist
                best_ann = ann_d
        if best_ann:
            valid_effs.append(best_ann)
    if valid_effs:
        raw_eff = min(valid_effs)
        return (datetime.strptime(raw_eff, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    return (f_dt + timedelta(days=65)).strftime("%Y-%m-%d")


def load_pit_ttm_fundamentals(
    conn: sqlite3.Connection,
    symbols: Sequence[str],
    fx_table: Dict[str, Tuple[List[str], List[float]]],
    max_cal_date: str,
) -> Dict[str, List[Tuple[str, str, Optional[float], Optional[float], int]]]:
    if not symbols:
        return {}
    marks = ",".join("?" for _ in DATE_REMOVING_REPAIRS)
    removed_dates: Dict[str, Set[str]] = defaultdict(set)
    for sym, orig_date in conn.execute(
        f"SELECT symbol, original_date FROM fundamental_current_archive "
        f"WHERE source_table='income_quarterly' AND reason IN ({marks})",
        DATE_REMOVING_REPAIRS,
    ).fetchall():
        if orig_date:
            removed_dates[sym].add(orig_date[:10])

    sym_marks = ",".join("?" for _ in symbols)

    earn_ann_by_sym: Dict[str, List[Tuple[str, str]]] = defaultdict(list)
    for sym, fdate, ann_d in conn.execute(
        f"SELECT symbol, fiscal_date, announce_date FROM fmp_earnings "
        f"WHERE symbol IN ({sym_marks}) AND fiscal_date IS NOT NULL AND announce_date IS NOT NULL",
        tuple(symbols),
    ).fetchall():
        f10, a10 = fdate[:10], ann_d[:10]
        if a10 > f10:
            earn_ann_by_sym[sym].append((f10, a10))

    vintage_by_qf: Dict[
        Tuple[str, str],
        List[Tuple[str, Optional[str], Optional[str], Optional[str], Optional[float], Optional[float]]],
    ] = defaultdict(list)
    for sym, fdate, obs_at, fil_d, acc_d, payload_str in conn.execute(
        f"SELECT symbol, fiscal_date, observed_at, filing_date, accepted_date, payload "
        f"FROM fundamental_vintage WHERE statement='income' AND symbol IN ({sym_marks}) "
        f"ORDER BY symbol, fiscal_date, observed_at",
        tuple(symbols),
    ).fetchall():
        f10 = fdate[:10]
        obs10 = (obs_at or "")[:10]
        try:
            pl = json.loads(payload_str)
        except Exception:
            continue
        rev_v = pl.get("revenue")
        ni_v = pl.get("netIncome", pl.get("bottomLineNetIncome"))
        ccy_v = pl.get("reportedCurrency") or "USD"
        vintage_by_qf[(sym, f10)].append((obs10, fil_d, acc_d, ccy_v, rev_v, ni_v))

    q_rows = conn.execute(
        f"SELECT symbol, date, filing_date, accepted_date, reported_currency, revenue, net_income "
        f"FROM income_quarterly WHERE symbol IN ({sym_marks}) ORDER BY symbol, date",
        tuple(symbols),
    ).fetchall()

    by_sym: Dict[str, List[dict]] = defaultdict(list)
    for sym, fdate, filing_d, acc_d, ccy, rev, ni in q_rows:
        fdate10 = fdate[:10]
        if fdate10 > max_cal_date or fdate10 in removed_dates.get(sym, set()):
            continue
        v_list = vintage_by_qf.get((sym, fdate10))
        if v_list:
            for idx_v, (obs10, v_fil, v_acc, v_ccy, v_rev, v_ni) in enumerate(v_list):
                if v_rev is None or not math.isfinite(float(v_rev)):
                    continue
                eff = compute_eff_date(fdate10, v_acc or acc_d, v_fil or filing_d, earn_ann_by_sym, sym)
                if idx_v == 0 or len(obs10) != 10:
                    known_from = eff
                else:
                    obs_next = (datetime.strptime(obs10, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
                    known_from = max(eff, obs_next)
                rate = fx_to_usd(fx_table, v_ccy or ccy, fdate10)
                if rate is None:
                    continue
                by_sym[sym].append(
                    {
                        "fiscal_date": fdate10,
                        "known_from": known_from,
                        "seq": idx_v,
                        "ni_usd": float(v_ni) * rate if v_ni is not None and math.isfinite(float(v_ni)) else None,
                        "rev_usd": float(v_rev) * rate,
                    }
                )
        else:
            if rev is None or not math.isfinite(float(rev)):
                continue
            eff = compute_eff_date(fdate10, acc_d, filing_d, earn_ann_by_sym, sym)
            rate = fx_to_usd(fx_table, ccy, fdate10)
            if rate is None:
                continue
            by_sym[sym].append(
                {
                    "fiscal_date": fdate10,
                    "known_from": eff,
                    "seq": 0,
                    "ni_usd": float(ni) * rate if ni is not None and math.isfinite(float(ni)) else None,
                    "rev_usd": float(rev) * rate,
                }
            )

    ttm_events: Dict[str, List[Tuple[str, str, Optional[float], Optional[float]]]] = {}
    for sym, items in by_sym.items():
        uniq_fdates = sorted({it["fiscal_date"] for it in items})
        if len(uniq_fdates) < 2:
            continue
        eff_candidates = sorted({it["known_from"] for it in items})
        events: List[Tuple[str, str, Optional[float], Optional[float], int]] = []
        for eff in eff_candidates:
            known = [it for it in items if it["known_from"] <= eff and it["fiscal_date"] < eff]
            dedup: Dict[str, dict] = {}
            for it in sorted(known, key=lambda x: (x["known_from"], x["seq"])):
                dedup[it["fiscal_date"]] = it
            ordered = [dedup[d] for d in sorted(dedup)]
            if len(ordered) < 2:
                continue
            gaps = [
                (datetime.strptime(ordered[k]["fiscal_date"], "%Y-%m-%d")
                 - datetime.strptime(ordered[k - 1]["fiscal_date"], "%Y-%m-%d")).days
                for k in range(1, len(ordered))
            ]
            k_win = 2 if (gaps and float(np.median(gaps[-4:])) >= 150) else 4
            if len(ordered) < k_win:
                continue
            tail = ordered[-k_win:]
            span_days = (
                datetime.strptime(tail[-1]["fiscal_date"], "%Y-%m-%d")
                - datetime.strptime(tail[0]["fiscal_date"], "%Y-%m-%d")
            ).days
            adj_gaps = [
                (datetime.strptime(tail[k]["fiscal_date"], "%Y-%m-%d")
                 - datetime.strptime(tail[k - 1]["fiscal_date"], "%Y-%m-%d")).days
                for k in range(1, len(tail))
            ]
            max_span = 315 if k_win == 4 else 220
            min_span = 235 if k_win == 4 else 150
            adj_ok = all((60 <= g <= 125) if k_win == 4 else (150 <= g <= 220) for g in adj_gaps)
            if span_days > max_span or span_days < min_span or not adj_ok:
                events.append((eff, tail[-1]["fiscal_date"], None, None, k_win))
                continue
            ttm_rev = sum(x["rev_usd"] for x in tail)
            if ttm_rev <= 0:
                events.append((eff, tail[-1]["fiscal_date"], None, None, k_win))
                continue
            ttm_ni = (
                sum(x["ni_usd"] for x in tail)
                if all(x["ni_usd"] is not None for x in tail)
                else None
            )
            events.append((eff, tail[-1]["fiscal_date"], ttm_ni, ttm_rev, k_win))
        if events:
            ttm_events[sym] = events
    return ttm_events


def roll_pctile_bounded(
    arr: np.ndarray, min_window: int = PCTILE_WINDOW, max_window: int = PCTILE_WINDOW
) -> Tuple[np.ndarray, np.ndarray]:
    """Bounded rolling percentile in [0, 100] requiring exact `min_window - 1` valid prior observations."""
    out = np.full(len(arr), np.nan, dtype=float)
    win_used = np.zeros(len(arr), dtype=int)
    min_valid = min_window - 1
    for i in range(min_window - 1, len(arr)):
        cur = arr[i]
        if np.isnan(cur):
            continue
        lo = max(0, i - max_window + 1)
        prev = arr[lo:i]
        valid = prev[~np.isnan(prev)]
        if len(valid) < min_valid:
            continue
        gt = np.sum(cur > valid)
        eq = np.sum(cur == valid)
        out[i] = float((gt + 0.5 * eq) / len(valid) * 100.0)
        win_used[i] = len(valid) + 1
    return out, win_used


def ema_joint_valid(arr: np.ndarray, joint_mask: np.ndarray, span: int = POST_EMA_SPAN) -> np.ndarray:
    """Causal EMA stepping synchronously only on days where `joint_mask` is True; emits NaN on gaps."""
    alpha = 2.0 / (span + 1.0)
    out = np.full(len(arr), np.nan, dtype=float)
    state: Optional[float] = None
    for i in range(len(arr)):
        if not joint_mask[i]:
            continue
        v = float(arr[i])
        state = v if state is None else (alpha * v + (1.0 - alpha) * state)
        out[i] = state
    return out


def compute_dd_gate(dd_val: float) -> float:
    if dd_val <= DD_GATE_FREE_BUFFER:
        return 1.0
    if dd_val >= DD_GATE_ZERO_AT:
        return 0.0
    return float((DD_GATE_ZERO_AT - dd_val) / (DD_GATE_ZERO_AT - DD_GATE_FREE_BUFFER))


def run_hysteresis_states(
    crowd_ema: np.ndarray, dd_gate: np.ndarray, d_vol: np.ndarray, start_idx: int
) -> Tuple[List[str], List[str], List[int]]:
    """Run deterministic 8.0/7.5 & 9.0/8.5 hysteresis lamp + non-contradictory low-score semantic tags."""
    n = len(crowd_ema)
    codes = [""] * n
    labels = [""] * n
    entries: List[int] = []
    hot_level = 0

    for i in range(start_idx, n):
        c = crowd_ema[i]
        if np.isnan(c):
            continue
        prev_hot = hot_level > 0
        if hot_level == 2:
            if c < HYST_WARM_OUT:
                hot_level = 0
            elif c < HYST_CROWD_OUT:
                hot_level = 1
        elif hot_level == 1:
            if c >= HYST_CROWD_IN:
                hot_level = 2
            elif c < HYST_WARM_OUT:
                hot_level = 0
        else:
            if c >= HYST_CROWD_IN:
                hot_level = 2
            elif c >= HYST_WARM_IN:
                hot_level = 1

        if not prev_hot and hot_level > 0:
            entries.append(i)

        if hot_level == 2:
            codes[i] = "CROWDED"
            labels[i] = "拥挤红灯 (≥9.0，退<8.5灭)"
        elif hot_level == 1:
            codes[i] = "WARM"
            labels[i] = "偏热黄灯 (≥8.0，退<7.5灭)"
        elif c >= 5.0:
            codes[i] = "NEUTRAL"
            labels[i] = "常态中性"
        else:
            vol_v = d_vol[i]
            if not np.isnan(vol_v) and vol_v >= 7.0:
                codes[i] = "PULLBACK_HIGHVOL"
                if not np.isnan(dd_gate[i]) and dd_gate[i] == 0.0:
                    labels[i] = "放量回调换手 (非冷清)"
                else:
                    labels[i] = "放量分歧低分 (非缩量)"
            elif not np.isnan(vol_v) and vol_v <= 4.0:
                codes[i] = "COLD"
                labels[i] = "缩量冷清低位"
            else:
                codes[i] = "LOW_MILD"
                labels[i] = "温和低分区"
    return codes, labels, entries


def _connect_readonly_sqlite(db_path: Path, immutable: bool = False) -> sqlite3.Connection:
    if immutable:
        return sqlite3.connect(f"file:{db_path}?immutable=1", uri=True)
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
        return conn
    except sqlite3.OperationalError:
        return sqlite3.connect(f"file:{db_path}?immutable=1", uri=True)


def compute_crowding_payload(db_path: Path, *, immutable: bool = False) -> dict:
    """Compute full V2.2 crowding payload for all 7 US equity theme baskets (strictly read-only)."""
    if not db_path.is_file():
        raise FileNotFoundError(f"market.db not found: {db_path}")

    conn = _connect_readonly_sqlite(db_path, immutable=immutable)
    store: Optional[MarketStore] = None
    try:
        baskets = load_baskets_pit(conn)
        names_map = dict(conn.execute("SELECT symbol, company_name FROM security_master").fetchall())

        all_syms: Set[str] = {"SPY", "QQQ", "SOXX"}
        for b in baskets.values():
            all_syms.update(b["all_members"])
            all_syms.update(b["all_dv_extra"])

        existing_syms = {
            r[0]
            for r in conn.execute(
                f"SELECT DISTINCT symbol FROM daily_price WHERE symbol IN ({','.join('?' for _ in all_syms)})",
                tuple(all_syms),
            ).fetchall()
        }
        all_syms = all_syms & existing_syms
        if "SPY" not in all_syms:
            raise ValueError("SPY daily_price missing in market.db")

        store = MarketStore(db_path, read_only=True)
        pb = PriceBook.load(store, sorted(all_syms))
        store.close()
        store = None

        suspect_dates: Dict[str, Set[str]] = defaultdict(set)
        for sym in all_syms:
            ser = pb._series.get(sym)
            if ser is None:
                continue
            for ndate, _ratio, kind in ser.notable:
                if kind == "split_suspect":
                    suspect_dates[sym].add(ndate)
            for lo, hi in pb._unresolved.get(sym, []):
                for d in ser.dates:
                    if lo <= d <= hi:
                        suspect_dates[sym].add(d)

        raw_calendar = list(pb.calendar)
        mkt_dv_map = load_market_daily_dv(conn, set(raw_calendar), suspect_dates)

        calendar: List[str] = []
        recent_counts: List[int] = []
        for d in raw_calendar:
            tot_dv, n_sym = mkt_dv_map.get(d, (0.0, 0))
            if n_sym < 500 or tot_dv <= 0:
                continue
            if len(recent_counts) >= 10:
                med = float(np.median(recent_counts[-20:]))
                if n_sym < 0.90 * med:
                    continue
            calendar.append(d)
            recent_counts.append(n_sym)

        n_days = len(calendar)
        if n_days < MA_WINDOW + PCTILE_WINDOW:
            raise ValueError(f"Insufficient clean trading days ({n_days}) for {MA_WINDOW}+{PCTILE_WINDOW} lookback")

        cal_idx = {d: i for i, d in enumerate(calendar)}
        adj_close_matrix: Dict[str, np.ndarray] = {s: np.full(n_days, np.nan) for s in all_syms}
        adj_close_by_sym_date: Dict[str, Dict[str, float]] = {}
        cum_bars_matrix: Dict[str, np.ndarray] = {s: np.zeros(n_days, dtype=int) for s in all_syms}

        for sym in all_syms:
            ser = pb._series.get(sym)
            if ser is None:
                continue
            s_map = dict(zip(ser.dates, ser.closes))
            adj_close_by_sym_date[sym] = s_map
            arr = adj_close_matrix[sym]
            cb = cum_bars_matrix[sym]
            running_bars = 0
            for i, d in enumerate(calendar):
                if d in s_map and d not in suspect_dates.get(sym, set()):
                    arr[i] = s_map[d]
                    running_bars += 1
                cb[i] = running_bars

        ret_matrix: Dict[str, np.ndarray] = {}
        for sym in all_syms:
            c = adj_close_matrix[sym]
            r = np.full(n_days, np.nan)
            r[1:] = c[1:] / c[:-1] - 1.0
            for sd in suspect_dates.get(sym, set()):
                if sd in cal_idx:
                    r[cal_idx[sd]] = np.nan
            ret_matrix[sym] = r

        sym_marks = ",".join("?" for _ in all_syms)
        dv_matrix: Dict[str, np.ndarray] = {s: np.full(n_days, np.nan) for s in all_syms}
        for sym, d, c, v in conn.execute(
            f"SELECT symbol, date, close, volume FROM daily_price "
            f"WHERE symbol IN ({sym_marks}) AND close > 0 AND volume >= 0",
            tuple(all_syms),
        ).fetchall():
            idx = cal_idx.get(d)
            if idx is not None and v is not None and d not in suspect_dates.get(sym, set()):
                dv_matrix[sym][idx] = float(c) * float(v)

        mcap_raw: Dict[str, List[Tuple[str, float]]] = defaultdict(list)
        for sym, d, mc in conn.execute(
            f"SELECT symbol, date, market_cap FROM historical_market_cap "
            f"WHERE symbol IN ({sym_marks}) AND market_cap > 0 ORDER BY symbol, date",
            tuple(all_syms),
        ).fetchall():
            mcap_raw[sym].append((d, float(mc)))

        mcap_clean: Dict[str, List[Tuple[str, float]]] = {}
        n_mcap_dropped = 0
        for sym, pts in mcap_raw.items():
            cleaned = sanitize_symbol_mcap_series(pts, adj_close_by_sym_date.get(sym, {}))
            n_mcap_dropped += len(pts) - len(cleaned)
            mcap_clean[sym] = cleaned

        mcap_matrix: Dict[str, np.ndarray] = {s: np.full(n_days, np.nan) for s in all_syms}
        for sym, pts in mcap_clean.items():
            arr = mcap_matrix[sym]
            for d, mc in pts:
                idx = cal_idx.get(d)
                if idx is not None:
                    arr[idx] = mc

        for prim, secs in DUAL_CLASS_DV_MAP.items():
            if prim not in mcap_matrix:
                continue
            p_arr = mcap_matrix[prim]
            for sec in secs:
                if sec not in mcap_matrix:
                    continue
                s_arr = mcap_matrix[sec]
                fill_mask = np.isnan(p_arr) & (~np.isnan(s_arr))
                p_arr[fill_mask] = s_arr[fill_mask]

        for sym in all_syms:
            arr = mcap_matrix[sym]
            ac = adj_close_matrix[sym]
            last_valid_idx: Optional[int] = None
            last_valid_mc: float = np.nan
            for i, d in enumerate(calendar):
                if not np.isnan(arr[i]):
                    last_valid_idx = i
                    last_valid_mc = arr[i]
                elif last_valid_idx is not None:
                    gap = (datetime.strptime(d, "%Y-%m-%d") - datetime.strptime(calendar[last_valid_idx], "%Y-%m-%d")).days
                    if (
                        0 < gap <= 15
                        and not np.isnan(ac[i])
                        and not np.isnan(ac[last_valid_idx])
                        and ac[last_valid_idx] > 0
                    ):
                        arr[i] = last_valid_mc * (ac[i] / ac[last_valid_idx])

        spy_ret = ret_matrix["SPY"]
        fx_table = load_fx_table(conn)
        ttm_events = load_pit_ttm_fundamentals(conn, sorted(all_syms), fx_table, calendar[-1])
    finally:
        if store is not None:
            store.close()
        conn.close()

    ni_matrix: Dict[str, np.ndarray] = {s: np.full(n_days, np.nan) for s in all_syms}
    rev_matrix: Dict[str, np.ndarray] = {s: np.full(n_days, np.nan) for s in all_syms}
    for sym, evs in ttm_events.items():
        effs = [e[0] for e in evs]
        ni_arr = ni_matrix[sym]
        rev_arr = rev_matrix[sym]
        for i, d in enumerate(calendar):
            j = bisect.bisect_right(effs, d) - 1
            if j >= 0:
                _, fdate, ni_val, rev_val, k_win = evs[j]
                if rev_val is None:
                    continue
                stale_days = (datetime.strptime(d, "%Y-%m-%d") - datetime.strptime(fdate, "%Y-%m-%d")).days
                max_stale = 270 if k_win == 2 else 200
                if stale_days <= max_stale:
                    if ni_val is not None:
                        ni_arr[i] = ni_val
                    rev_arr[i] = rev_val

    mkt_tot_dv = np.array([mkt_dv_map[d][0] for d in calendar], dtype=float)
    spy_nav = np.ones(n_days, dtype=float)
    for i in range(1, n_days):
        spy_nav[i] = spy_nav[i - 1] * (1.0 + (spy_ret[i] if not np.isnan(spy_ret[i]) else 0.0))

    results: dict = {
        "version": "v2.2_codex_audited",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "as_of": calendar[-1],
        "calendar_start": calendar[0],
        "n_trading_days": n_days,
        "sanitized_mcap_anomalies": n_mcap_dropped,
        "baskets": {},
        "equal_basket_regime_summary": [],
    }

    buckets_def = [
        ("< 5.0 (低分区)", lambda x: x < 5.0),
        ("5.0 - 8.0 (常态中性)", lambda x: 5.0 <= x < 8.0),
        ("8.0 - 9.0 (偏热区)", lambda x: 8.0 <= x < 9.0),
        (">= 9.0 (拥挤红区)", lambda x: x >= 9.0),
    ]

    for code, b in baskets.items():
        snaps = b["snapshots"]
        if not snaps:
            continue
        snap_dates = [sn[0] for sn in snaps]
        ipo_warm = b["ipo_warmup_bars"]

        def active_members_on(day_idx: int) -> Tuple[List[str], List[str]]:
            d = calendar[day_idx]
            k = bisect.bisect_right(snap_dates, d) - 1
            if k < 0:
                k = 0
            _, mem_raw, dvx_raw = snaps[k]
            mem = [
                s for s in mem_raw
                if s in all_syms and cum_bars_matrix[s][day_idx] >= ipo_warm
            ]
            dvx = [
                s for s in dvx_raw
                if s in all_syms and cum_bars_matrix[s][day_idx] >= ipo_warm
            ]
            return mem, dvx

        b_ret = np.full(n_days, np.nan)
        b_dv_1d = np.zeros(n_days, dtype=float)
        turnover_1d = np.full(n_days, np.nan)
        tot_mcap_series = np.full(n_days, np.nan)
        sp_series = np.full(n_days, np.nan)
        ep_series = np.full(n_days, np.nan)
        last_known_mc_by_sym: Dict[str, float] = {}

        for i in range(n_days):
            members_i, dv_extra_i = active_members_on(i)
            if not members_i:
                continue

            if i >= 1:
                members_prev, _ = active_members_on(i - 1)
                if members_prev:
                    rets = np.array([ret_matrix[s][i] for s in members_prev], dtype=float)
                    mcaps_prev = np.array([mcap_matrix[s][i - 1] for s in members_prev], dtype=float)
                    has_mc = (~np.isnan(mcaps_prev)) & (mcaps_prev > 0)
                    tot_mc_prev = float(np.sum(mcaps_prev[has_mc])) if np.any(has_mc) else 0.0
                    valid_both = (~np.isnan(rets)) & has_mc
                    cov_mc_prev = float(np.sum(mcaps_prev[valid_both])) if np.any(valid_both) else 0.0
                    if tot_mc_prev > 0 and cov_mc_prev >= 0.70 * tot_mc_prev:
                        w = mcaps_prev[valid_both] / cov_mc_prev
                        b_ret[i] = float(np.sum(w * rets[valid_both]))

            dv_all_i = sorted(set(members_i) | set(dv_extra_i))
            b_dv_1d[i] = sum(
                0.0 if np.isnan(dv_matrix[s][i]) else float(dv_matrix[s][i])
                for s in dv_all_i
            )

            expected_mc = sum(last_known_mc_by_sym.get(s, 0.0) for s in members_i)
            dv_sum, mc_sum, n_ok = 0.0, 0.0, 0
            rev_sum, mc_sp_sum = 0.0, 0.0
            ni_sum, mc_ep_sum = 0.0, 0.0
            for s in members_i:
                dv_v = dv_matrix[s][i]
                mc_v = mcap_matrix[s][i]
                if not np.isnan(mc_v) and mc_v > 0:
                    last_known_mc_by_sym[s] = float(mc_v)
                if not np.isnan(dv_v) and not np.isnan(mc_v) and mc_v > 0:
                    dv_sum += dv_v
                    mc_sum += mc_v
                    n_ok += 1
                    for sec in DUAL_CLASS_DV_MAP.get(s, []):
                        if sec in dv_extra_i and not np.isnan(dv_matrix[sec][i]):
                            dv_sum += dv_matrix[sec][i]
                if not np.isnan(mc_v) and mc_v > 0:
                    rev_v = rev_matrix[s][i]
                    ni_v = ni_matrix[s][i]
                    if not np.isnan(rev_v) and rev_v > 0:
                        rev_sum += rev_v
                        mc_sp_sum += mc_v
                    if not np.isnan(ni_v):
                        ni_sum += ni_v
                        mc_ep_sum += mc_v

            min_cnt_ok = n_ok >= max(1, math.ceil(0.6 * len(members_i)))
            mcap_cov_ok = (expected_mc <= 0.0) or (mc_sum >= 0.70 * expected_mc)
            if min_cnt_ok and mc_sum > 0 and mcap_cov_ok:
                turnover_1d[i] = dv_sum / mc_sum
                tot_mcap_series[i] = mc_sum
                if mc_sp_sum >= 0.70 * mc_sum:
                    sp_series[i] = rev_sum / mc_sp_sum
                if mc_ep_sum >= 0.70 * mc_sum:
                    ep_series[i] = ni_sum / mc_ep_sum

        b_nav = np.ones(n_days, dtype=float)
        for i in range(1, n_days):
            b_nav[i] = b_nav[i - 1] * (1.0 + (b_ret[i] if not np.isnan(b_ret[i]) else 0.0))

        ma200 = pd.Series(b_nav).rolling(MA_WINDOW, min_periods=MA_WINDOW).mean().to_numpy()
        bias200 = b_nav / ma200 - 1.0
        mom243 = np.full(n_days, np.nan)
        mom243[MOM_V1_WINDOW:] = b_nav[MOM_V1_WINDOW:] / b_nav[:-MOM_V1_WINDOW] - 1.0

        ex_ret = b_ret - spy_ret
        exvol20 = np.full(n_days, np.nan)
        dd60 = np.full(n_days, np.nan)
        dd_gate = np.full(n_days, np.nan)
        for i in range(VOL_WINDOW, n_days):
            seg = ex_ret[i - VOL_WINDOW + 1 : i + 1]
            if np.sum(~np.isnan(seg)) >= 16:
                exvol20[i] = float(np.nanstd(seg, ddof=1) * math.sqrt(252))
        for i in range(DD_GATE_LOOKBACK - 1, n_days):
            pk = float(np.max(b_nav[i - DD_GATE_LOOKBACK + 1 : i + 1]))
            d_val_dd = 1.0 - b_nav[i] / pk
            dd60[i] = d_val_dd
            dd_gate[i] = compute_dd_gate(d_val_dd)

        dv_share_1d = np.where(mkt_tot_dv > 0, b_dv_1d / mkt_tot_dv, np.nan)
        b_dv_5d = pd.Series(b_dv_1d).rolling(VOL_SMOOTH_DAYS, min_periods=VOL_SMOOTH_DAYS).mean().to_numpy()
        dv_share_5d = pd.Series(dv_share_1d).rolling(VOL_SMOOTH_DAYS, min_periods=VOL_SMOOTH_DAYS).mean().to_numpy()
        turnover_5d = pd.Series(turnover_1d).rolling(VOL_SMOOTH_DAYS, min_periods=VOL_SMOOTH_DAYS).mean().to_numpy()

        p_bias200, _ = roll_pctile_bounded(bias200, PCTILE_WINDOW, PCTILE_WINDOW)
        p_exvol_raw, _ = roll_pctile_bounded(exvol20, PCTILE_WINDOW, PCTILE_WINDOW)
        p_dvshare5, _ = roll_pctile_bounded(dv_share_5d, PCTILE_WINDOW, PCTILE_WINDOW)
        p_turn5, _ = roll_pctile_bounded(turnover_5d, PCTILE_WINDOW, PCTILE_WINDOW)
        p_sp_raw, val_win_used = roll_pctile_bounded(sp_series, VAL_PCTILE_MIN_WINDOW, VAL_PCTILE_MAX_WINDOW)
        p_sp_crowd = 100.0 - p_sp_raw

        s_mom_raw = p_bias200 / 10.0
        s_exvol_ug = p_exvol_raw / 10.0
        s_exvol_raw = s_exvol_ug * dd_gate
        s_dvshare_raw = p_dvshare5 / 10.0
        s_turn_raw = p_turn5 / 10.0
        s_val_raw = p_sp_crowd / 10.0

        crowd_raw = (
            0.5 * (s_mom_raw + s_exvol_raw)
            + 0.5 * (s_dvshare_raw + s_turn_raw)
            + s_val_raw
        ) / 3.0

        joint_valid = ~np.isnan(crowd_raw)
        valid_raw_idxs = [i for i in range(n_days) if joint_valid[i]]
        if not valid_raw_idxs:
            raise ValueError(f"Basket {code} produced zero valid crowding observations")
        first_i = valid_raw_idxs[0]
        last_i = valid_raw_idxs[-1]

        s_mom = ema_joint_valid(s_mom_raw, joint_valid, POST_EMA_SPAN)
        s_exvol = ema_joint_valid(s_exvol_raw, joint_valid, POST_EMA_SPAN)
        s_dvshare = ema_joint_valid(s_dvshare_raw, joint_valid, POST_EMA_SPAN)
        s_turn = ema_joint_valid(s_turn_raw, joint_valid, POST_EMA_SPAN)
        s_val = ema_joint_valid(s_val_raw, joint_valid, POST_EMA_SPAN)

        d_ret = 0.5 * (s_mom + s_exvol)
        d_vol = 0.5 * (s_dvshare + s_turn)
        d_val = s_val
        crowd = (d_ret + d_vol + d_val) / 3.0

        crowd_direct_ema = ema_joint_valid(crowd_raw, joint_valid, POST_EMA_SPAN)
        max_lin_err = float(np.nanmax(np.abs(crowd[joint_valid] - crowd_direct_ema[joint_valid])))
        if not (math.isfinite(max_lin_err) and max_lin_err < 1e-11):
            raise RuntimeError(f"Linear EMA additivity violated on {code}: {max_lin_err}")

        state_codes, state_labels, hyst_entries = run_hysteresis_states(crowd, dd_gate, d_vol, first_i)

        p_mom243_v1, _ = roll_pctile_bounded(mom243, PCTILE_WINDOW, PCTILE_WINDOW)
        p_dvs1_v1, _ = roll_pctile_bounded(dv_share_1d, PCTILE_WINDOW, PCTILE_WINDOW)
        p_trn1_v1, _ = roll_pctile_bounded(turnover_1d, PCTILE_WINDOW, PCTILE_WINDOW)
        p_ep_raw_v1, _ = roll_pctile_bounded(ep_series, PCTILE_WINDOW, PCTILE_WINDOW)
        p_ep_inv_v1 = 100.0 - p_ep_raw_v1
        p_sp_500_raw, _ = roll_pctile_bounded(sp_series, PCTILE_WINDOW, PCTILE_WINDOW)
        p_sp_500_inv = 100.0 - p_sp_500_raw
        p_val_v1 = np.full(n_days, np.nan)
        for i in range(PCTILE_WINDOW - 1, n_days):
            ep_w = ep_series[i - PCTILE_WINDOW + 1 : i + 1]
            ve = ep_w[~np.isnan(ep_w)]
            if len(ve) >= PCTILE_WINDOW - 1 and np.min(ve) > 0:
                p_val_v1[i] = p_ep_inv_v1[i]
            else:
                p_val_v1[i] = p_sp_500_inv[i]
        crowd_v1 = (
            0.5 * (p_mom243_v1 / 10.0 + s_exvol_ug)
            + 0.5 * (p_dvs1_v1 / 10.0 + p_trn1_v1 / 10.0)
            + (p_val_v1 / 10.0)
        ) / 3.0

        fwd20_ret = np.full(n_days, np.nan)
        fwd60_ret = np.full(n_days, np.nan)
        fwd60_mdd = np.full(n_days, np.nan)
        all_fwd_idxs = []
        for i in range(first_i, n_days):
            if np.isnan(crowd[i]):
                continue
            if i + 20 < n_days:
                fwd20_ret[i] = b_nav[i + 20] / b_nav[i] - 1.0
            if i + 60 < n_days:
                fwd60_ret[i] = b_nav[i + 60] / b_nav[i] - 1.0
                path60 = b_nav[i : i + 61]
                fwd60_mdd[i] = float(np.min(path60 / np.maximum.accumulate(path60) - 1.0))
                all_fwd_idxs.append(i)

        if all_fwd_idxs:
            base_r60 = float(np.mean(fwd60_ret[all_fwd_idxs]) * 100)
            base_mdd60 = float(np.mean(fwd60_mdd[all_fwd_idxs]) * 100)
            base_p15 = float(np.mean(fwd60_mdd[all_fwd_idxs] <= -0.15) * 100)
        else:
            base_r60, base_mdd60, base_p15 = 0.0, 0.0, 0.0

        b_stats = [
            {
                "bucket": "全部日期基线 (无条件平均)",
                "n_days": len(all_fwd_idxs),
                "fwd60_mean": _safe_round(base_r60, 2),
                "fwd60_vs_base": 0.0,
                "fwd60_mdd_mean": _safe_round(base_mdd60, 2),
                "prob_dd15_60d": _safe_round(base_p15, 1),
                "is_baseline": True,
            }
        ]
        for label, pred in buckets_def:
            idxs = [i for i in all_fwd_idxs if pred(crowd[i])]
            if not idxs:
                b_stats.append({"bucket": label, "n_days": 0, "is_baseline": False})
                continue
            r60 = fwd60_ret[idxs]
            mdd60 = fwd60_mdd[idxs]
            m_r60 = float(np.mean(r60) * 100)
            b_stats.append(
                {
                    "bucket": label,
                    "n_days": len(idxs),
                    "fwd60_mean": _safe_round(m_r60, 2),
                    "fwd60_vs_base": _safe_round(m_r60 - base_r60, 2),
                    "fwd60_mdd_mean": _safe_round(float(np.mean(mdd60) * 100), 2),
                    "prob_dd15_60d": _safe_round(float(np.mean(mdd60 <= -0.15) * 100), 1),
                    "is_baseline": False,
                }
            )

        episodes = []
        for idx_e in hyst_entries:
            episodes.append(
                {
                    "date": calendar[idx_e],
                    "score": _safe_round(crowd[idx_e], 2),
                    "raw_score": _safe_round(crowd_raw[idx_e], 2),
                    "fwd20_ret": None if np.isnan(fwd20_ret[idx_e]) else _safe_round(fwd20_ret[idx_e] * 100, 2),
                    "fwd60_ret": None if np.isnan(fwd60_ret[idx_e]) else _safe_round(fwd60_ret[idx_e] * 100, 2),
                    "fwd60_mdd": None if np.isnan(fwd60_mdd[idx_e]) else _safe_round(fwd60_mdd[idx_e] * 100, 2),
                }
            )

        latest_members, latest_dvx = active_members_on(last_i)
        n_price_ok = sum(1 for s in latest_members if not np.isnan(dv_matrix[s][last_i]))

        const_rows = []
        tot_mc_latest, tot_dv5_latest = 0.0, 0.0
        last5_slice = slice(max(0, last_i - 4), last_i + 1)
        for s in latest_members:
            mc_v = float(mcap_matrix[s][last_i]) if not np.isnan(mcap_matrix[s][last_i]) else 0.0
            syms_dv = [s] + [sec for sec in DUAL_CLASS_DV_MAP.get(s, []) if sec in latest_dvx]
            dv5_v = 0.0
            for sd in syms_dv:
                seg_dv = dv_matrix[sd][last5_slice]
                dv5_v += float(np.nansum(seg_dv)) / 5.0
            if mc_v > 0 or dv5_v > 0:
                tot_mc_latest += mc_v
                tot_dv5_latest += dv5_v
                lbl = s if len(syms_dv) == 1 else f"{s}+{'+'.join(syms_dv[1:])}"
                const_rows.append(
                    {
                        "symbol": lbl,
                        "name": (names_map.get(s) or s)[:26],
                        "mcap_bn": _safe_round(mc_v / 1e9, 1) or 0.0,
                        "dv5_bn": _safe_round(dv5_v / 1e9, 2) or 0.0,
                    }
                )
        const_rows.sort(key=lambda x: x["dv5_bn"], reverse=True)
        for r in const_rows:
            r["mcap_wt_pct"] = _safe_round(r["mcap_bn"] * 1e9 / tot_mc_latest * 100, 2) if tot_mc_latest > 0 else 0.0
            r["dv_wt_pct"] = _safe_round(r["dv5_bn"] * 1e9 / tot_dv5_latest * 100, 2) if tot_dv5_latest > 0 else 0.0

        ytd_year = calendar[last_i][:4]
        ytd_prev_idx = max(0, bisect.bisect_left(calendar, f"{ytd_year}-01-01") - 1)
        ytd_base = b_nav[ytd_prev_idx]
        full_base = b_nav[first_i]

        series_rows = []
        for i in range(first_i, n_days):
            if np.isnan(crowd[i]):
                continue
            series_rows.append(
                {
                    "d": calendar[i],
                    "nav_full": _safe_round(b_nav[i] / full_base * 100.0, 2),
                    "nav_2026": _safe_round(b_nav[i] / ytd_base * 100.0, 2),
                    "c": _safe_round(crowd[i], 2),
                    "c_raw": _safe_round(crowd_raw[i], 2),
                    "c_v1": None if np.isnan(crowd_v1[i]) else _safe_round(crowd_v1[i], 2),
                    "st": state_codes[i],
                    "st_lbl": state_labels[i],
                    "s_mom": _safe_round(s_mom[i], 2),
                    "s_vol": _safe_round(s_exvol[i], 2),
                    "s_dvs": _safe_round(s_dvshare[i], 2),
                    "s_trn": _safe_round(s_turn[i], 2),
                    "s_val": _safe_round(s_val[i], 2),
                }
            )

        latest = {
            "date": calendar[last_i],
            "crowding": _safe_round(crowd[last_i], 2),
            "crowding_raw": _safe_round(crowd_raw[last_i], 2),
            "crowd_v1": None if np.isnan(crowd_v1[last_i]) else _safe_round(crowd_v1[last_i], 2),
            "state_code": state_codes[last_i],
            "state_label": state_labels[last_i],
            "d_ret": _safe_round(d_ret[last_i], 2),
            "d_vol": _safe_round(d_vol[last_i], 2),
            "d_val": _safe_round(d_val[last_i], 2),
            "bias200_pct": _safe_round(bias200[last_i] * 100, 2),
            "p_bias200": _safe_round(p_bias200[last_i], 1),
            "s_mom_raw": _safe_round(s_mom_raw[last_i], 2),
            "s_mom": _safe_round(s_mom[last_i], 2),
            "exvol20_pct": _safe_round(exvol20[last_i] * 100, 2),
            "p_exvol_raw": _safe_round(p_exvol_raw[last_i], 1),
            "dd60_pct": _safe_round(dd60[last_i] * 100, 1),
            "dd_gate": _safe_round(dd_gate[last_i], 2),
            "s_exvol_raw": _safe_round(s_exvol_raw[last_i], 2),
            "s_exvol": _safe_round(s_exvol[last_i], 2),
            "dv5_usd_bn": _safe_round(b_dv_5d[last_i] / 1e9, 2),
            "dv5_share_pct": _safe_round(dv_share_5d[last_i] * 100, 2),
            "p_dvshare": _safe_round(p_dvshare5[last_i], 1),
            "s_dvshare_raw": _safe_round(s_dvshare_raw[last_i], 2),
            "s_dvshare": _safe_round(s_dvshare[last_i], 2),
            "turn5_pct": _safe_round(turnover_5d[last_i] * 100, 3),
            "p_turn": _safe_round(p_turn5[last_i], 1),
            "s_turn_raw": _safe_round(s_turn_raw[last_i], 2),
            "s_turn": _safe_round(s_turn[last_i], 2),
            "sp_pct": _safe_round(sp_series[last_i] * 100, 2),
            "ps_multiple": _safe_round(1.0 / sp_series[last_i], 2) if sp_series[last_i] > 0 else None,
            "val_window_days": int(val_win_used[last_i]),
            "p_sp_raw_pctile": _safe_round(p_sp_raw[last_i], 1),
            "p_sp_crowd": _safe_round(p_sp_crowd[last_i], 1),
            "s_val_raw": _safe_round(s_val_raw[last_i], 2),
            "s_val": _safe_round(s_val[last_i], 2),
            "max_crowding_hist": _safe_round(float(np.nanmax(crowd)), 2),
            "max_crowding_date": calendar[int(np.nanargmax(crowd))],
        }

        results["baskets"][code] = {
            "id": code,
            "name": b["name"],
            "kind": b["kind"],
            "has_pre2026_pit_holdings": b["has_pre2026_pit_holdings"],
            "weekly_pit_start": b["weekly_pit_start"],
            "source_note": b["source_note"],
            "n_members": n_price_ok,
            "valid_start": calendar[first_i],
            "valid_end": calendar[last_i],
            "n_valid_days": len(series_rows),
            "latest": latest,
            "bucket_stats": b_stats,
            "episodes_ge8": episodes,
            "constituents_top": const_rows[:20],
            "constituents_total_count": len(const_rows),
            "series": series_rows,
        }

    summary_table = []
    for code, b in results["baskets"].items():
        base = b["bucket_stats"][0]
        s_map = {st["bucket"]: st for st in b["bucket_stats"]}
        low_st = s_map["< 5.0 (低分区)"]
        hot8 = s_map["8.0 - 9.0 (偏热区)"]
        hot9 = s_map[">= 9.0 (拥挤红区)"]
        n_hot = hot8["n_days"] + hot9["n_days"]
        if n_hot > 0:
            hot_r60 = (
                ((hot8["fwd60_mean"] or 0.0) * hot8["n_days"] if hot8["n_days"] else 0.0)
                + ((hot9["fwd60_mean"] or 0.0) * hot9["n_days"] if hot9["n_days"] else 0.0)
            ) / n_hot
            hot_p15 = (
                ((hot8["prob_dd15_60d"] or 0.0) * hot8["n_days"] if hot8["n_days"] else 0.0)
                + ((hot9["prob_dd15_60d"] or 0.0) * hot9["n_days"] if hot9["n_days"] else 0.0)
            ) / n_hot
        else:
            hot_r60, hot_p15 = None, None
        summary_table.append(
            {
                "code": code,
                "name": b["name"],
                "latest_crowd": b["latest"]["crowding"],
                "state_code": b["latest"]["state_code"],
                "state_label": b["latest"]["state_label"],
                "base_r60": base["fwd60_mean"],
                "base_p15": base["prob_dd15_60d"],
                "low_r60": low_st.get("fwd60_mean"),
                "hot_n": n_hot,
                "hot_r60": None if hot_r60 is None else _safe_round(hot_r60, 2),
                "hot_vs_base": None if hot_r60 is None or base["fwd60_mean"] is None else _safe_round(hot_r60 - base["fwd60_mean"], 2),
                "hot_p15": None if hot_p15 is None else _safe_round(hot_p15, 1),
            }
        )
    results["equal_basket_regime_summary"] = summary_table
    return results
