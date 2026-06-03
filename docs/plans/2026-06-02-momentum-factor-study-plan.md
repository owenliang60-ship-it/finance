# Momentum Factor Study (Phase 0) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Race four volume/price momentum factor constructions in the existing `backtest/factor_study/` framework over the extended pool (~955), gate on strict size/vol neutrality, and select a per-horizon winner that becomes a production-ready Factor for the morning report momentum section.

**Architecture:** Two new indicators (`volume_ratio` + `mfi`) live in `src/indicators/`; four new `Factor` subclasses wrap them (mirroring the existing `RSRatingBFactor`/`RVOLFactor` pattern) in `backtest/factor_study/factors.py`; a new decoupled bias-diagnostics module (`backtest/factor_study/bias_diagnostics.py`) computes per-date cross-sectional Spearman(score, size) and Spearman(score, vol) using the same `adapter.slice_to_date` + `Factor.compute` pattern the runner uses (it does NOT touch the verified runner); a thin orchestration script (`scripts/run_momentum_horse_race.py`) wires the horse-race config, runs IC + bias + cross-candidate correlation, applies lexicographic winner selection, and runs the P0→P3 daily-frequency sensitivity check; output is a research report + the winning Factor already registered in `ALL_FACTORS`.

**Tech Stack:** Python, pytest, existing backtest/factor_study framework, market.db (SQLite)

**North-star alignment:** 分析层 — feeds the morning report momentum section (P3). Upstream design: docs/design/2026-06-02-morning-report-redesign.md §4 Phase 0.

---

## Grounding notes (verified against real code — read before implementing)

These are the facts every task is built on. Each cites the real file:line read during planning.

1. **Factor contract** — `Factor.compute(price_dict, date) -> Dict[str, float]` (`backtest/factor_study/protocol.py:46-62`). `price_dict` is `{symbol: DataFrame}` already sliced to `date` (anti-look-ahead). `FactorMeta(name, score_name, score_range, higher_is_stronger, min_data_days=70)` (`protocol.py:15-28`).
2. **Slice DataFrames carry full OHLCV** — verified at runtime: columns `['date','open','high','low','close','volume','change','changePercent']` (`src/data/market_store.py:719-754` `get_daily_prices_df`; adapter `_load_prices` at `backtest/adapters/us_stocks.py:300-312`). So MFI (needs H/L/C/V) and volume-ratio (needs V) have everything from the same `price_dict`.
3. **Existing reuse targets in `src/indicators/rs_rating.py`:**
   - `compute_rs_rating_b(price_dict)` (`rs_rating.py:38`) does ret/vol risk-adjustment + cross-sectional `scipy.stats.zscore(ddof=1)` clipped ±3 (`rs_rating.py:112-114`), but with **fixed windows 3m/1m/1w (63/21/10d)** and a **5-day reversal skip** (`rs_rating.py:65-74`), composite weighted 0.40/0.35/0.25, output `rs_rank` 0-99 percentile. **It does NOT expose a {3,10,30} risk-adj z directly** — Candidate 1 must compute its own `R_w/σ_w` per window. We reuse the *pattern* (ret/vol then cross-sectional clipped z), not the function.
   - `_clenow_momentum(prices: pd.Series, window: int) -> float` (`rs_rating.py:143`) = annualized log-price regression slope × R². **Directly reusable** for Candidate 3 with windows {3,10,30}. Note: `compute_rs_rating_c` (`rs_rating.py:178`) hardcodes 63/21/10 and `MIN_TRADING_DAYS=70` (`rs_rating.py:31`) — we call `_clenow_momentum` directly, not `compute_rs_rating_c`.
4. **`src/indicators/rvol.py` is a z-score, NOT a ratio** — `calculate_rvol` returns `(current - mean)/std` over a `lookback` window (`rvol.py:48-55`). Confirmed: we do NOT reuse it as the volume leg. The spec's `RVOL_w = mean(vol_w)/mean(vol_B)` ratio is net-new (`docs/design/2026-06-02-morning-report-redesign.md:91`).
5. **IC pipeline** — `analyze_ic(factor_meta, score_history, return_matrices, computation_dates, n_quantiles)` returns `(List[ICResult], ICDecayCurve)` (`ic_analysis.py:46`). `ICResult` fields: `mean_ic, std_ic, ic_ir (=mean/std, ic_analysis.py:152), ic_hit_rate, n_ic_obs, t_stat, p_value, quantile_returns, top_bottom_spread (=Q_top - Q1, ic_analysis.py:170-173)` (`ic_analysis.py:22-35`). IC = per-date Spearman(score, fwd_return) averaged (`ic_analysis.py:127-141`).
6. **Config** — `FactorStudyConfig` (`backtest/config.py:125-156`): fields `market, computation_freq ('D'|'W'), forward_horizons, n_quantiles, benchmark_symbols (List[str]), start_date, end_date, oos_start_date, oos_fraction=0.3, min_oos_dates=50`. `FREQ_DAYS={'D':1,'W':5,...}` (`config.py:58-64`). `us_factor_study(**overrides)` (`config.py:159`) defaults `benchmark_symbols=['QQQ','POOL_AVG']`, `computation_freq='W'`. Excess-return matrix is built automatically when `benchmark_symbols` is set (`runner.py:124-138` → `build_excess_return_matrix`, `forward_returns.py:71`).
7. **Runner** — `FactorStudyRunner(config, adapter)`; `add_factor(factor)`; `run() -> List[FactorStudyResults]` (`runner.py:63-187`). It computes scores **internally** via `_compute_scores` (`runner.py:189-226`) and does **NOT expose `score_history`** publicly — `run()` returns only `FactorStudyResults` (no per-symbol scores). **Implication:** the bias diagnostics + cross-candidate correlation cannot read scores out of the runner; they must recompute the score panel themselves via `adapter.slice_to_date` + `factor.compute`, mirroring `runner._compute_scores` (`runner.py:204-213`). This keeps the verified runner untouched. (FLAGGED — see Open Questions.)
8. **Adapter** — `USStocksAdapter(symbols, universe, mcap_threshold)` (`us_stocks.py:38`). `universe="extended"` → `get_extended_symbols()` (`us_stocks.py:283-285`; `src/data/extended_universe_manager.py:113-120`), currently **955 symbols** (verified). `slice_to_date(date)` returns `{sym: df<=date}` with `len>=70` (`us_stocks.py:174-227`). Point-in-time market cap: `get_bulk_market_caps_at(date) -> {sym: mcap}` (`market_store.py:1484-1493`, as-of `date <= ?`, no look-ahead). Extended-pool coverage in `historical_market_cap`: **930/955 = 97.4%** (verified) — date range 2021-04-13 → 2026-05-29.
9. **Registry** — `ALL_FACTORS: Dict[str, Type[Factor]]` + `get_factor(name)` + `list_factors()` (`factors.py:343-378`). New factors register here.
10. **Sweep** — `get_default_sweep(name)` returns `[]` for unknown factor names (`sweep.py:91-95`), so new factors run IC analysis (Track 1) fine even without a sweep entry; Track-2 event study just produces nothing. We only need IC (Track 1) for the horse race, so **no sweep entries are required**.
11. **CLI runner exists** — `scripts/run_factor_study.py` (`:46-203`) supports `--universe extended --freq W --horizons 3,10,30 --benchmark QQQ,POOL_AVG`. Our horse-race script reuses `us_factor_study` + `USStocksAdapter` programmatically rather than the CLI (we need multi-factor + bias + selection in one pass).
12. **Test conventions** — tests live in `tests/` (indicator unit tests, e.g. `tests/test_rs_rating.py`) and `tests/test_factor_study/` (framework tests). Synthetic price dicts via `pd.date_range(..., freq="B")` (`tests/test_rs_rating.py:31-47`). `tests/conftest.py` only guards `data/{price,fundamental,pool}` from deletion — our tests use synthetic in-memory data so they are safe. Project root is on `sys.path` via the indicator tests' `sys.path.insert` idiom (`tests/test_rs_rating.py:16-17`).

**Execution context (HARD):** all work happens in the Task 0 worktree. Define once, then run **every** bash block from there:
```bash
WT="/Users/owen/CC workspace/Finance/.claude/worktrees/momentum-factor-study"
PY="/Users/owen/CC workspace/Finance/.venv/bin/python"   # 主 .venv 绝对路径（worktree 不复制 .venv，per MEMORY）
```
Each bash step below assumes **CWD = `$WT`**. The interpreter is always the absolute `$PY` (written out in full in each block). Commits already pin the tree via `git -C "$WT"`. See Task 0 Step 0.3 for the contract + verification gate.

---

## SPEC-vs-CODE mismatches found (resolved in-plan)

| # | Spec assumption (§4 P0) | Code reality | Resolution in this plan |
|---|---|---|---|
| M1 | "复用 `rs_rating.py` Method B 的 ret/vol + 截面 z" for windows {3,10,30} | `compute_rs_rating_b` hardcodes 63/21/10 windows + 5d reversal skip + 0-99 rank output (`rs_rating.py:72-128`); no {3,10,30} entry point | Candidate 1 computes its own `R_w/σ_w` per window {3,10,30}, then the **same** cross-sectional `np.clip(zscore(.,ddof=1),-3,3)` as `rs_rating.py:112`. We reuse the *pattern*, write a small shared helper. No reversal skip (spec says {3,10,30} raw windows). |
| M2 | Output filename `docs/research/2026-<date>-momentum-factor-study.md` | n/a | Use today's date at run time → `docs/research/2026-06-<dd>-momentum-factor-study.md`. |
| M3 | "扩展池 ~949" (spec §4) vs "~955" (spec §2) | extended_universe.json = **955** symbols (verified); 930 have PIT mcap | Use `universe="extended"` (whatever count it currently is, 955); after `slice_to_date` min-70-days + min-data filters the effective N per date is lower. Report the actual N. The 25 symbols lacking mcap are simply excluded from the **size diagnostic** per date (not from IC). |
| M4 | Bias diagnostic + cross-candidate corr "与 IC 并行" (parallel to IC) | Runner does not expose `score_history`; `run()` returns only `FactorStudyResults` (`runner.py:93-187`) | Bias diagnostics recompute the score panel independently via `adapter.slice_to_date`+`factor.compute` (mirrors `runner._compute_scores`, `runner.py:204-213`). Verified runner stays untouched (project rule: wrapper, don't rewrite verified logic). |
| M5 | Candidate 2 "legs winsorized ±3 before combining" + "z(log RVOL_w)" | No existing combined-leg factor | Candidate 2 winsorizes **each** cross-sectional z-leg to ±3 (matching `rs_rating.py:112` clip semantics) THEN blends `α·z_price + (1-α)·z_vol`. |
| M6 | HARD GATE `mean(|daily_corr|)` (abs each day THEN mean) | No existing bias code to contradict; spec explicit | Implement exactly: `np.mean(np.abs(daily_corrs))`. Also report `signed_mean = np.mean(daily_corrs)` as a separate diagnostic field. |

---

## Task 0 — Worktree + branch setup

**Files:** none (git only)

Per project rule "代码开发管理一律用 git + worktree". Create an isolated worktree so P0 can proceed in parallel with P1/P2.

- [ ] Step 0.1 — Create worktree off `main`:
  ```bash
  cd "/Users/owen/CC workspace/Finance" && git worktree add ".claude/worktrees/momentum-factor-study" -b feat/momentum-factor-study main
  ```
- [ ] Step 0.2 — Confirm the worktree has its own checkout (the `.venv` is NOT copied; always use the absolute interpreter path `/Users/owen/CC workspace/Finance/.venv/bin/python` per MEMORY constraint):
  ```bash
  git -C "/Users/owen/CC workspace/Finance/.claude/worktrees/momentum-factor-study" status
  ```
  Expected: clean tree on `feat/momentum-factor-study`.
- [ ] Step 0.3 — Pin the execution context and verify it (run this, and start every later bash block from here):
  ```bash
  cd "$WT" && pwd && git rev-parse --abbrev-ref HEAD && git rev-parse --show-toplevel
  ```
  Expected: `pwd` and `--show-toplevel` both = `$WT`; branch = `feat/momentum-factor-study`. If `pwd` is the main repo, STOP — every relative `tests/...` path below would hit the wrong tree.

> **Path/CWD contract (HARD).** All file paths below are canonical repo-relative (`src/...`, `backtest/...`, `tests/...`) and resolve correctly **only when CWD = `$WT`** — the `cd "$WT"` is load-bearing, not cosmetic. Imports resolve to the worktree because each test does `sys.path.insert(0, PROJECT_ROOT)` with PROJECT_ROOT = the test's own worktree root (`test_rs_rating.py:16-17` idiom), so a stray editable install in the main `.venv` cannot shadow the worktree source — **provided** the collected test files are the worktree's (hence CWD = `$WT`).

---

## Task 1 — `volume_ratio` indicator (new)

`RVOL_w = mean(vol over w) / mean(vol over the baseline B days *preceding* the window, non-overlapping)`, then `log`, then (at factor level) cross-sectional z. The indicator itself returns the **per-symbol raw `log(ratio)`** (cross-sectional z happens in the Factor where the full panel is known). Baseline B: 120d for w∈{3,10}, 250d for w=30 (`docs/design/...:93`).

**Files:**
- Create: `src/indicators/volume_ratio.py`
- Test: `tests/test_volume_ratio.py`

- [ ] Step 1.1 — Write failing test `tests/test_volume_ratio.py`:
  ```python
  """Tests for volume_ratio indicator (relative volume ratio, log-scaled)."""
  import sys
  from pathlib import Path

  import numpy as np
  import pandas as pd
  import pytest

  PROJECT_ROOT = Path(__file__).parent.parent
  sys.path.insert(0, str(PROJECT_ROOT))

  from src.indicators.volume_ratio import (
      compute_log_volume_ratio,
      DEFAULT_BASELINE,
  )


  def _vol_df(volumes):
      n = len(volumes)
      dates = pd.date_range("2024-01-01", periods=n, freq="B")
      return pd.DataFrame({"date": dates, "volume": volumes})


  def test_baseline_mapping():
      assert DEFAULT_BASELINE[3] == 120
      assert DEFAULT_BASELINE[10] == 120
      assert DEFAULT_BASELINE[30] == 250


  def test_flat_volume_ratio_is_zero_log():
      # constant volume → recent mean == baseline mean → ratio 1 → log 0
      df = _vol_df(np.full(300, 1_000_000.0))
      val = compute_log_volume_ratio(df, window=10, baseline=120)
      assert val == pytest.approx(0.0, abs=1e-9)


  def test_recent_surge_gives_positive_log_ratio():
      vols = np.full(300, 1_000_000.0)
      vols[-10:] = 3_000_000.0  # recent 10d triple volume
      df = _vol_df(vols)
      val = compute_log_volume_ratio(df, window=10, baseline=120)
      assert val > 0.0  # surge → ratio > 1 → log > 0


  def test_insufficient_data_returns_none():
      df = _vol_df(np.full(50, 1_000_000.0))
      assert compute_log_volume_ratio(df, window=30, baseline=250) is None


  def test_zero_baseline_returns_none():
      # 120d baseline window all-zero (recent 3d excluded — non-overlapping) → guard
      df = _vol_df(np.concatenate([np.zeros(120), np.full(3, 5.0)]))
      assert compute_log_volume_ratio(df, window=3, baseline=120) is None


  def test_exact_baseline_without_window_returns_none():
      # non-overlapping baseline needs baseline + window rows; exactly baseline is short
      df = _vol_df(np.full(120, 1_000_000.0))
      assert compute_log_volume_ratio(df, window=3, baseline=120) is None
  ```
- [ ] Step 1.2 — Run it, expect FAIL (module doesn't exist):
  ```bash
  "/Users/owen/CC workspace/Finance/.venv/bin/python" -m pytest tests/test_volume_ratio.py -q
  ```
  Expected: `ModuleNotFoundError: No module named 'src.indicators.volume_ratio'` → all fail/error.
- [ ] Step 1.3 — Minimal implementation `src/indicators/volume_ratio.py`:
  ```python
  """
  Volume Ratio 指标 — 相对成交量比率 (log-scaled)

  RVOL_w = mean(volume over last w days) / mean(volume over the B days PRECEDING
           that window (non-overlapping)).
  返回 log(RVOL_w)。截面 z-score 在 Factor 层做（需要全 panel）。

  用相对量比而非绝对成交额 (Amihud: $-volume 单调于市值)。
  Baseline B: 120d for w in {3,10}, 250d for w=30 (非重叠规范)。
  """
  from typing import Optional

  import numpy as np
  import pandas as pd

  # 窗口 → 基线长度映射 (docs/design/2026-06-02-morning-report-redesign.md:93)
  DEFAULT_BASELINE = {3: 120, 10: 120, 30: 250}


  def compute_log_volume_ratio(
      df: pd.DataFrame,
      window: int,
      baseline: int,
  ) -> Optional[float]:
      """
      计算单只股票的 log 相对成交量比率。

      RVOL_w = mean(vol[-w:]) / mean(vol[-baseline-w:-w])   # baseline 不与近端窗口重叠
      返回 log(RVOL_w)；数据不足 (< baseline+window) 或基线均值<=0 返回 None。

      Args:
          df: 含 'volume' 列、按日期正序的 DataFrame
          window: 近端窗口 (3/10/30)
          baseline: 基线窗口 (120/250)
      """
      if df is None or "volume" not in df.columns:
          return None
      vol = df["volume"].astype(float).values
      if len(vol) < baseline + window:
          return None

      recent_mean = float(np.mean(vol[-window:]))
      # 非重叠基线：分母排除最近 window 天，避免近端放量自我稀释 (design:95)
      baseline_mean = float(np.mean(vol[-baseline - window:-window]))

      if baseline_mean <= 0 or recent_mean <= 0:
          return None

      ratio = recent_mean / baseline_mean
      return float(np.log(ratio))
  ```
- [ ] Step 1.4 — Run, expect PASS:
  ```bash
  "/Users/owen/CC workspace/Finance/.venv/bin/python" -m pytest tests/test_volume_ratio.py -q
  ```
  Expected: 6 passed.
- [ ] Step 1.5 — Commit:
  ```bash
  git -C "/Users/owen/CC workspace/Finance/.claude/worktrees/momentum-factor-study" add src/indicators/volume_ratio.py tests/test_volume_ratio.py && git -C "/Users/owen/CC workspace/Finance/.claude/worktrees/momentum-factor-study" commit -m "feat(indicators): volume_ratio — log relative-volume ratio (P0 candidate-2 vol leg)"
  ```

---

## Task 2 — `mfi` indicator (Money Flow Index, new)

MFI ∈ [0,100], needs OHLC + volume. Typical price = (H+L+C)/3; positive/negative money flow ratio over a window. ~30 lines (`docs/design/...:91`).

**Files:**
- Create: `src/indicators/mfi.py`
- Test: `tests/test_mfi.py`

- [ ] Step 2.1 — Write failing test `tests/test_mfi.py`:
  ```python
  """Tests for MFI (Money Flow Index) indicator."""
  import sys
  from pathlib import Path

  import numpy as np
  import pandas as pd
  import pytest

  PROJECT_ROOT = Path(__file__).parent.parent
  sys.path.insert(0, str(PROJECT_ROOT))

  from src.indicators.mfi import compute_mfi


  def _ohlcv(closes, volume=1_000_000.0):
      n = len(closes)
      dates = pd.date_range("2024-01-01", periods=n, freq="B")
      c = np.asarray(closes, dtype=float)
      return pd.DataFrame({
          "date": dates,
          "high": c * 1.01,
          "low": c * 0.99,
          "close": c,
          "volume": np.full(n, volume),
      })


  def test_all_up_days_mfi_near_100():
      df = _ohlcv(np.linspace(100, 200, 60))
      val = compute_mfi(df, window=14)
      assert val is not None
      assert val > 90.0  # uninterrupted rising typical price → MFI saturates high

  def test_all_down_days_mfi_near_zero():
      df = _ohlcv(np.linspace(200, 100, 60))
      val = compute_mfi(df, window=14)
      assert val is not None
      assert val < 10.0

  def test_bounded_range():
      np.random.seed(7)
      df = _ohlcv(100 + np.cumsum(np.random.randn(80)))
      val = compute_mfi(df, window=14)
      assert val is not None
      assert 0.0 <= val <= 100.0

  def test_insufficient_data_returns_none():
      df = _ohlcv(np.linspace(100, 110, 5))
      assert compute_mfi(df, window=14) is None

  def test_window_param_respected():
      df = _ohlcv(np.linspace(100, 130, 40))
      assert compute_mfi(df, window=30) is not None
      assert compute_mfi(df, window=3) is not None
  ```
- [ ] Step 2.2 — Run, expect FAIL:
  ```bash
  "/Users/owen/CC workspace/Finance/.venv/bin/python" -m pytest tests/test_mfi.py -q
  ```
  Expected: `ModuleNotFoundError: No module named 'src.indicators.mfi'`.
- [ ] Step 2.3 — Minimal implementation `src/indicators/mfi.py`:
  ```python
  """
  MFI 指标 — Money Flow Index (资金流指数, bounded [0,100])

  typical_price = (high + low + close) / 3
  raw_money_flow = typical_price * volume
  当 typical_price 上升: positive flow; 下降: negative flow
  money_flow_ratio = sum(pos_flow, w) / sum(neg_flow, w)
  MFI = 100 - 100 / (1 + money_flow_ratio)

  参考: Quong & Soudack, "Volume-Weighted RSI: Money Flow" (1989)
  """
  from typing import Optional

  import numpy as np
  import pandas as pd


  def compute_mfi(df: pd.DataFrame, window: int = 14) -> Optional[float]:
      """
      计算单只股票最新的 MFI 值。

      Args:
          df: 含 [high, low, close, volume] 列、按日期正序的 DataFrame
          window: 回看窗口 (默认 14)

      Returns:
          MFI ∈ [0, 100]，数据不足返回 None
      """
      required = {"high", "low", "close", "volume"}
      if df is None or not required.issubset(df.columns):
          return None
      if len(df) < window + 1:
          return None

      high = df["high"].astype(float).values
      low = df["low"].astype(float).values
      close = df["close"].astype(float).values
      volume = df["volume"].astype(float).values

      typical = (high + low + close) / 3.0
      raw_flow = typical * volume
      tp_diff = np.diff(typical)  # len n-1, aligned to days 1..n-1

      # 取最近 window 个 diff
      tail_diff = tp_diff[-window:]
      tail_flow = raw_flow[-window:]  # flow on the day of each diff (day t)

      pos = float(np.sum(tail_flow[tail_diff > 0]))
      neg = float(np.sum(tail_flow[tail_diff < 0]))

      if neg <= 0:
          # 无负向资金流 → 全正 → MFI 顶
          return 100.0 if pos > 0 else 50.0

      money_ratio = pos / neg
      return float(100.0 - 100.0 / (1.0 + money_ratio))
  ```
- [ ] Step 2.4 — Run, expect PASS:
  ```bash
  "/Users/owen/CC workspace/Finance/.venv/bin/python" -m pytest tests/test_mfi.py -q
  ```
  Expected: 5 passed.
- [ ] Step 2.5 — Commit:
  ```bash
  git -C "/Users/owen/CC workspace/Finance/.claude/worktrees/momentum-factor-study" add src/indicators/mfi.py tests/test_mfi.py && git -C "/Users/owen/CC workspace/Finance/.claude/worktrees/momentum-factor-study" commit -m "feat(indicators): MFI — Money Flow Index bounded [0,100] (P0 candidate-4)"
  ```

---

## Task 3 — Shared cross-sectional helper for the momentum factors

The four factors all do "per-symbol raw value → cross-sectional clipped z". Extract one helper to avoid duplicating the `np.clip(zscore(ddof=1),-3,3)` pattern (`rs_rating.py:112`).

**Files:**
- Create: `backtest/factor_study/momentum_factors.py` (helper section; factors added in Tasks 4-7)
- Test: `tests/test_factor_study/test_momentum_factors.py`

- [ ] Step 3.1 — Write failing test for the helper in `tests/test_factor_study/test_momentum_factors.py`:
  ```python
  """Tests for momentum factor candidates (P0 horse race)."""
  import numpy as np
  import pandas as pd
  import pytest

  from backtest.factor_study.momentum_factors import (
      cross_sectional_z,
  )


  def test_cross_sectional_z_clips_at_3():
      raw = {f"S{i}": float(i) for i in range(20)}
      raw["OUT"] = 1e6  # extreme outlier
      z = cross_sectional_z(raw)
      assert max(z.values()) <= 3.0 + 1e-9
      assert min(z.values()) >= -3.0 - 1e-9

  def test_cross_sectional_z_centered():
      raw = {f"S{i}": float(i) for i in range(50)}
      z = cross_sectional_z(raw)
      assert abs(np.mean(list(z.values()))) < 0.2  # roughly centered

  def test_cross_sectional_z_drops_none_and_nan():
      raw = {"A": 1.0, "B": None, "C": float("nan"), "D": 2.0, "E": 3.0,
             "F": 4.0, "G": 5.0}
      z = cross_sectional_z(raw)
      assert "B" not in z and "C" not in z
      assert "A" in z and "D" in z

  def test_cross_sectional_z_too_few_returns_empty():
      assert cross_sectional_z({"A": 1.0}) == {}
  ```
- [ ] Step 3.2 — Run, expect FAIL:
  ```bash
  "/Users/owen/CC workspace/Finance/.venv/bin/python" -m pytest tests/test_factor_study/test_momentum_factors.py -q
  ```
  Expected: `ModuleNotFoundError: No module named 'backtest.factor_study.momentum_factors'`.
- [ ] Step 3.3 — Implement helper section of `backtest/factor_study/momentum_factors.py`:
  ```python
  """
  动量因子候选 (P0 赛马) — 四个量价动量构造，包装为 Factor 子类。

  共享:
  - cross_sectional_z: 截面 winsorized z-score (复用 rs_rating.py:112 的 clip(zscore,-3,3) 语义)

  候选:
  - MomentumRiskAdjFactor   (Candidate 1, 纯价格对照组)
  - MomentumVolumeConfirmedFactor (Candidate 2, 头牌)
  - MomentumClenowFactor    (Candidate 3, 复用 _clenow_momentum)
  - MomentumMFIFactor       (Candidate 4)
  """
  import logging
  from typing import Dict, List, Optional

  import numpy as np
  import pandas as pd
  from scipy.stats import zscore as scipy_zscore

  from backtest.factor_study.protocol import Factor, FactorMeta

  logger = logging.getLogger(__name__)


  def cross_sectional_z(
      raw: Dict[str, Optional[float]],
      clip: float = 3.0,
  ) -> Dict[str, float]:
      """对一个截面的原始值做 winsorized z-score (clip ±3)。

      丢弃 None / NaN；少于 5 只返回 {}（与 ic_analysis 的 5-symbol 下限一致）。
      """
      items = [(s, v) for s, v in raw.items()
               if v is not None and not (isinstance(v, float) and np.isnan(v))]
      if len(items) < 5:
          return {}
      syms = [s for s, _ in items]
      vals = np.array([v for _, v in items], dtype=float)
      if np.std(vals, ddof=1) < 1e-12:
          return {s: 0.0 for s in syms}
      z = np.clip(scipy_zscore(vals, ddof=1), -clip, clip)
      return dict(zip(syms, z.astype(float)))
  ```
- [ ] Step 3.4 — Run, expect PASS:
  ```bash
  "/Users/owen/CC workspace/Finance/.venv/bin/python" -m pytest tests/test_factor_study/test_momentum_factors.py -q
  ```
  Expected: 4 passed.
- [ ] Step 3.5 — Commit:
  ```bash
  git -C "/Users/owen/CC workspace/Finance/.claude/worktrees/momentum-factor-study" add backtest/factor_study/momentum_factors.py tests/test_factor_study/test_momentum_factors.py && git -C "/Users/owen/CC workspace/Finance/.claude/worktrees/momentum-factor-study" commit -m "feat(factor_study): cross_sectional_z helper for momentum candidates"
  ```

---

## Task 4 — Candidate 1: `MomentumRiskAdjFactor` (price-only control)

`score = z_cs(R_w/σ_w)`, windows {3,10,30}. `R_w` = window return `close[-1]/close[-1-w]-1`; `σ_w` = stdev of daily returns over the window. One Factor instance per window (so the registry exposes `Momentum_RiskAdj_3`, `_10`, `_30`). This mirrors `compute_rs_rating_b`'s ret/vol pattern (`rs_rating.py:82-85`) but with the spec's {3,10,30} windows and no reversal skip (M1).

**Files:**
- Modify: `backtest/factor_study/momentum_factors.py` (add factor + a `risk_adj_raw` helper)
- Test: `tests/test_factor_study/test_momentum_factors.py` (append)

- [ ] Step 4.1 — Append failing tests:
  ```python
  from backtest.factor_study.momentum_factors import (
      MomentumRiskAdjFactor,
      risk_adj_value,
  )


  def _trending_dict(n_stocks=12, n_days=80, seed=3):
      np.random.seed(seed)
      dates = pd.date_range("2025-01-01", periods=n_days, freq="B")
      out = {}
      for i in range(n_stocks):
          drift = 0.002 * (i - n_stocks // 2)
          rets = np.random.randn(n_days) * 0.015 + drift
          close = 100 * np.exp(np.cumsum(rets))
          out[f"STK{i:02d}"] = pd.DataFrame({
              "date": dates, "open": close, "high": close * 1.01,
              "low": close * 0.99, "close": close,
              "volume": np.random.randint(1_000_000, 5_000_000, n_days),
          })
      return out


  def test_risk_adj_value_positive_for_uptrend():
      close = 100 * np.exp(np.cumsum(np.full(40, 0.01)))
      df = pd.DataFrame({"close": close})
      assert risk_adj_value(df, window=10) > 0

  def test_risk_adj_factor_meta_window():
      f = MomentumRiskAdjFactor(window=10)
      assert f.meta.name == "Momentum_RiskAdj_10"
      assert f.meta.higher_is_stronger is True

  def test_risk_adj_factor_compute_returns_z_scores():
      f = MomentumRiskAdjFactor(window=10)
      scores = f.compute(_trending_dict(), "2025-04-01")
      assert len(scores) >= 5
      assert all(-3.0001 <= v <= 3.0001 for v in scores.values())

  def test_risk_adj_strongest_uptrend_ranks_high():
      f = MomentumRiskAdjFactor(window=10)
      scores = f.compute(_trending_dict(), "2025-04-01")
      # STK11 has the largest positive drift → should be top-ranked
      top = max(scores, key=scores.get)
      assert top == "STK11"
  ```
- [ ] Step 4.2 — Run, expect FAIL (ImportError for `MomentumRiskAdjFactor`/`risk_adj_value`):
  ```bash
  "/Users/owen/CC workspace/Finance/.venv/bin/python" -m pytest tests/test_factor_study/test_momentum_factors.py -q
  ```
- [ ] Step 4.3 — Implement in `backtest/factor_study/momentum_factors.py`:
  ```python
  def risk_adj_value(df: pd.DataFrame, window: int) -> Optional[float]:
      """R_w / σ_w for one symbol.

      R_w = close[-1]/close[-1-window] - 1
      σ_w = stdev(daily returns over the window) (ddof=1)
      数据不足或波动~0 返回 None。
      """
      if df is None or "close" not in df.columns:
          return None
      close = df["close"].astype(float).values
      if len(close) < window + 1:
          return None
      r_w = close[-1] / close[-1 - window] - 1.0
      daily = np.diff(close[-(window + 1):]) / close[-(window + 1):-1]
      sigma = float(np.std(daily, ddof=1)) if len(daily) > 1 else 0.0
      if sigma <= 1e-10:
          return None
      return float(r_w / sigma)


  class MomentumRiskAdjFactor(Factor):
      """Candidate 1 — z_cs(R_w/σ_w)，纯价格风险调整动量 (对照组)。"""

      def __init__(self, window: int):
          self.window = window

      @property
      def meta(self) -> FactorMeta:
          return FactorMeta(
              name=f"Momentum_RiskAdj_{self.window}",
              score_name="z_riskadj",
              score_range=(-3, 3),
              higher_is_stronger=True,
              min_data_days=self.window + 1,
          )

      def compute(self, price_dict, date: str) -> Dict[str, float]:
          raw = {s: risk_adj_value(df, self.window)
                 for s, df in price_dict.items()}
          return cross_sectional_z(raw)
  ```
- [ ] Step 4.4 — Run, expect PASS:
  ```bash
  "/Users/owen/CC workspace/Finance/.venv/bin/python" -m pytest tests/test_factor_study/test_momentum_factors.py -q
  ```
  Expected: previous 4 + 4 new = 8 passed.
- [ ] Step 4.5 — Commit:
  ```bash
  git -C "/Users/owen/CC workspace/Finance/.claude/worktrees/momentum-factor-study" add backtest/factor_study/momentum_factors.py tests/test_factor_study/test_momentum_factors.py && git -C "/Users/owen/CC workspace/Finance/.claude/worktrees/momentum-factor-study" commit -m "feat(factor_study): Candidate 1 MomentumRiskAdjFactor (price-only control)"
  ```

---

## Task 5 — Candidate 2: `MomentumVolumeConfirmedFactor` (top pick)

`score = α·z(R_w/σ_w) + (1−α)·z(log RVOL_w)`. α (price-leg weight) swept {0.3,0.5,0.7}. Each leg is winsorized to ±3 before blending (M5). Volume leg uses `compute_log_volume_ratio` (Task 1) with baseline from `DEFAULT_BASELINE`. One instance per (window, α).

**Files:**
- Modify: `backtest/factor_study/momentum_factors.py`
- Test: `tests/test_factor_study/test_momentum_factors.py` (append)

- [ ] Step 5.1 — Append failing tests:
  ```python
  from backtest.factor_study.momentum_factors import (
      MomentumVolumeConfirmedFactor,
  )


  def test_volconfirmed_meta_encodes_window_and_alpha():
      f = MomentumVolumeConfirmedFactor(window=10, alpha=0.5)
      assert f.meta.name == "Momentum_VolConf_10_a050"

  def test_volconfirmed_alpha_1_equals_pure_price_ranking():
      # alpha=1.0 → vol leg weight 0 → ranking identical to RiskAdj sign
      price = MomentumRiskAdjFactor(window=10)
      volc = MomentumVolumeConfirmedFactor(window=10, alpha=1.0)
      d = _trending_dict(n_days=160)   # 需 baseline(120)+window(10) 才能算出量腿，否则 volc 返回 {}
      sp = price.compute(d, "2025-04-01")
      sv = volc.compute(d, "2025-04-01")
      common = sorted(set(sp) & set(sv))
      assert len(common) >= 5          # 防真空通过：common 为空时空 Series 比较 .all() 也返回 True
      rank_p = pd.Series({k: sp[k] for k in common}).rank()
      rank_v = pd.Series({k: sv[k] for k in common}).rank()
      assert (rank_p == rank_v).all()

  def test_volconfirmed_compute_in_clip_range():
      f = MomentumVolumeConfirmedFactor(window=10, alpha=0.5)
      # need >=120 days for the 120d baseline of w=10
      np.random.seed(11)
      dates = pd.date_range("2024-06-01", periods=160, freq="B")
      d = {}
      for i in range(10):
          close = 100 * np.exp(np.cumsum(np.random.randn(160) * 0.015 + 0.001 * i))
          d[f"S{i}"] = pd.DataFrame({
              "date": dates, "open": close, "high": close * 1.01,
              "low": close * 0.99, "close": close,
              "volume": np.random.randint(1_000_000, 9_000_000, 160).astype(float),
          })
      scores = f.compute(d, "2025-01-01")
      assert len(scores) >= 5
      # blended z of two ±3 legs stays within [-3, 3]
      assert all(-3.0001 <= v <= 3.0001 for v in scores.values())
  ```
- [ ] Step 5.2 — Run, expect FAIL (ImportError):
  ```bash
  "/Users/owen/CC workspace/Finance/.venv/bin/python" -m pytest tests/test_factor_study/test_momentum_factors.py -q
  ```
- [ ] Step 5.3 — Implement:
  ```python
  from src.indicators.volume_ratio import compute_log_volume_ratio, DEFAULT_BASELINE


  class MomentumVolumeConfirmedFactor(Factor):
      """Candidate 2 — α·z(R_w/σ_w) + (1-α)·z(log RVOL_w)。头号推荐。

      α = 价格腿权重 (0.5 即 50/50)。两腿各自 winsorize ±3 后混合。
      """

      def __init__(self, window: int, alpha: float):
          self.window = window
          self.alpha = alpha
          self.baseline = DEFAULT_BASELINE[window]

      @property
      def meta(self) -> FactorMeta:
          atag = f"a{int(round(self.alpha * 100)):03d}"
          return FactorMeta(
              name=f"Momentum_VolConf_{self.window}_{atag}",
              score_name="z_volconf",
              score_range=(-3, 3),
              higher_is_stronger=True,
              min_data_days=self.baseline + self.window,   # 非重叠基线需 baseline+window 天
          )

      def compute(self, price_dict, date: str) -> Dict[str, float]:
          price_raw = {s: risk_adj_value(df, self.window)
                       for s, df in price_dict.items()}
          vol_raw = {s: compute_log_volume_ratio(df, self.window, self.baseline)
                     for s, df in price_dict.items()}
          z_price = cross_sectional_z(price_raw)   # ±3 winsorized
          z_vol = cross_sectional_z(vol_raw)       # ±3 winsorized
          common = set(z_price) & set(z_vol)
          if len(common) < 5:
              return {}
          a = self.alpha
          return {s: a * z_price[s] + (1.0 - a) * z_vol[s] for s in common}
  ```
- [ ] Step 5.4 — Run, expect PASS:
  ```bash
  "/Users/owen/CC workspace/Finance/.venv/bin/python" -m pytest tests/test_factor_study/test_momentum_factors.py -q
  ```
  Expected: 11 passed.
- [ ] Step 5.5 — Commit:
  ```bash
  git -C "/Users/owen/CC workspace/Finance/.claude/worktrees/momentum-factor-study" add backtest/factor_study/momentum_factors.py tests/test_factor_study/test_momentum_factors.py && git -C "/Users/owen/CC workspace/Finance/.claude/worktrees/momentum-factor-study" commit -m "feat(factor_study): Candidate 2 MomentumVolumeConfirmedFactor (price+vol, alpha sweep)"
  ```

---

## Task 6 — Candidate 3: `MomentumClenowFactor` (reuse `_clenow_momentum`)

`score = z_cs(_clenow_momentum(close, w))`, windows {3,10,30}. Directly reuse `_clenow_momentum` (`rs_rating.py:143`) — do NOT reimplement (project rule). Note 3d is marginal/noisy; we still build it and let the race expose weakness.

**Files:**
- Modify: `backtest/factor_study/momentum_factors.py`
- Test: `tests/test_factor_study/test_momentum_factors.py` (append)

- [ ] Step 6.1 — Append failing tests:
  ```python
  from backtest.factor_study.momentum_factors import MomentumClenowFactor


  def test_clenow_factor_meta():
      f = MomentumClenowFactor(window=30)
      assert f.meta.name == "Momentum_Clenow_30"

  def test_clenow_factor_uptrend_top_ranked():
      f = MomentumClenowFactor(window=30)
      scores = f.compute(_trending_dict(n_days=80), "2025-04-01")
      assert len(scores) >= 5
      top = max(scores, key=scores.get)
      assert top == "STK11"  # largest drift

  def test_clenow_factor_reuses_indicator():
      # sanity: the factor delegates to _clenow_momentum (same module)
      import backtest.factor_study.momentum_factors as mf
      from src.indicators.rs_rating import _clenow_momentum as canonical
      assert mf._clenow_momentum is canonical
  ```
- [ ] Step 6.2 — Run, expect FAIL:
  ```bash
  "/Users/owen/CC workspace/Finance/.venv/bin/python" -m pytest tests/test_factor_study/test_momentum_factors.py -q
  ```
- [ ] Step 6.3 — Implement:
  ```python
  from src.indicators.rs_rating import _clenow_momentum


  class MomentumClenowFactor(Factor):
      """Candidate 3 — z_cs(Clenow 动量)，复用 _clenow_momentum (rs_rating.py:143)。"""

      def __init__(self, window: int):
          self.window = window

      @property
      def meta(self) -> FactorMeta:
          return FactorMeta(
              name=f"Momentum_Clenow_{self.window}",
              score_name="z_clenow",
              score_range=(-3, 3),
              higher_is_stronger=True,
              min_data_days=self.window,
          )

      def compute(self, price_dict, date: str) -> Dict[str, float]:
          raw = {}
          for s, df in price_dict.items():
              if df is None or "close" not in df.columns or len(df) < self.window:
                  continue
              raw[s] = _clenow_momentum(df["close"].astype(float), self.window)
          return cross_sectional_z(raw)
  ```
- [ ] Step 6.4 — Run, expect PASS:
  ```bash
  "/Users/owen/CC workspace/Finance/.venv/bin/python" -m pytest tests/test_factor_study/test_momentum_factors.py -q
  ```
  Expected: 14 passed.
- [ ] Step 6.5 — Commit:
  ```bash
  git -C "/Users/owen/CC workspace/Finance/.claude/worktrees/momentum-factor-study" add backtest/factor_study/momentum_factors.py tests/test_factor_study/test_momentum_factors.py && git -C "/Users/owen/CC workspace/Finance/.claude/worktrees/momentum-factor-study" commit -m "feat(factor_study): Candidate 3 MomentumClenowFactor (reuses _clenow_momentum)"
  ```

---

## Task 7 — Candidate 4: `MomentumMFIFactor` + register all candidates

`score = z_cs(compute_mfi(df, w))`, windows {3,10,30}. MFI is already bounded [0,100]; we still cross-sectional-z it so it lives on the same scale as the others for the race. Then register every candidate instance in `ALL_FACTORS`.

**Files:**
- Modify: `backtest/factor_study/momentum_factors.py`
- Modify: `backtest/factor_study/factors.py` (extend `ALL_FACTORS` via a `register_momentum_candidates()` call OR direct dict merge)
- Test: `tests/test_factor_study/test_momentum_factors.py` (append) + `tests/test_factor_study/test_momentum_registry.py`

- [ ] Step 7.1 — Append failing MFI tests:
  ```python
  from backtest.factor_study.momentum_factors import MomentumMFIFactor


  def test_mfi_factor_meta():
      f = MomentumMFIFactor(window=14)
      assert f.meta.name == "Momentum_MFI_14"

  def test_mfi_factor_compute_z():
      f = MomentumMFIFactor(window=10)
      scores = f.compute(_trending_dict(n_days=80), "2025-04-01")
      assert len(scores) >= 5
      assert all(-3.0001 <= v <= 3.0001 for v in scores.values())
  ```
- [ ] Step 7.2 — Create failing `tests/test_factor_study/test_momentum_registry.py`:
  ```python
  """All P0 momentum candidates must be in the global registry."""
  from backtest.factor_study.factors import ALL_FACTORS, get_factor


  EXPECTED = [
      "Momentum_RiskAdj_3", "Momentum_RiskAdj_10", "Momentum_RiskAdj_30",
      "Momentum_Clenow_3", "Momentum_Clenow_10", "Momentum_Clenow_30",
      "Momentum_MFI_3", "Momentum_MFI_10", "Momentum_MFI_30",
      "Momentum_VolConf_3_a030", "Momentum_VolConf_3_a050", "Momentum_VolConf_3_a070",
      "Momentum_VolConf_10_a030", "Momentum_VolConf_10_a050", "Momentum_VolConf_10_a070",
      "Momentum_VolConf_30_a030", "Momentum_VolConf_30_a050", "Momentum_VolConf_30_a070",
  ]


  def test_all_candidates_registered():
      for name in EXPECTED:
          assert name in ALL_FACTORS, f"missing {name}"

  def test_get_factor_builds_instances():
      for name in EXPECTED:
          f = get_factor(name)
          assert f.meta.name == name
  ```
- [ ] Step 7.3 — Run both, expect FAIL:
  ```bash
  "/Users/owen/CC workspace/Finance/.venv/bin/python" -m pytest tests/test_factor_study/test_momentum_factors.py tests/test_factor_study/test_momentum_registry.py -q
  ```
- [ ] Step 7.4 — Implement `MomentumMFIFactor` + a `MOMENTUM_CANDIDATES` factory in `momentum_factors.py`:
  ```python
  from src.indicators.mfi import compute_mfi


  class MomentumMFIFactor(Factor):
      """Candidate 4 — z_cs(MFI)，价量一体结构独立交叉验证。"""

      def __init__(self, window: int):
          self.window = window

      @property
      def meta(self) -> FactorMeta:
          return FactorMeta(
              name=f"Momentum_MFI_{self.window}",
              score_name="z_mfi",
              score_range=(-3, 3),
              higher_is_stronger=True,
              min_data_days=self.window + 1,
          )

      def compute(self, price_dict, date: str) -> Dict[str, float]:
          raw = {s: compute_mfi(df, self.window) for s, df in price_dict.items()}
          return cross_sectional_z(raw)


  WINDOWS = [3, 10, 30]
  ALPHAS = [0.3, 0.5, 0.7]


  def build_momentum_candidates() -> Dict[str, Factor]:
      """Instantiate every P0 candidate keyed by meta.name."""
      out: Dict[str, Factor] = {}
      for w in WINDOWS:
          for cls in (MomentumRiskAdjFactor, MomentumClenowFactor, MomentumMFIFactor):
              f = cls(window=w)
              out[f.meta.name] = f
          for a in ALPHAS:
              f = MomentumVolumeConfirmedFactor(window=w, alpha=a)
              out[f.meta.name] = f
      return out
  ```
- [ ] Step 7.5 — Register in `backtest/factor_study/factors.py` — append after the `ALL_FACTORS` literal (`factors.py:343-354`):
  ```python
  # ── P0 Momentum candidates (horse race) ──────────────────
  def _register_momentum_candidates() -> None:
      from backtest.factor_study.momentum_factors import build_momentum_candidates
      for name, factor in build_momentum_candidates().items():
          # store the class so get_factor() can re-instantiate; but these are
          # parameterized, so register a zero-arg builder via a tiny shim.
          ALL_FACTORS[name] = factor.__class__ if False else _bind(factor)

  def _bind(instance: Factor):
      """Return a zero-arg callable producing this configured instance.

      get_factor() does `ALL_FACTORS[name]()`; momentum candidates are
      parameterized, so we register a builder that ignores args and returns
      a fresh equivalently-configured instance.
      """
      cls = instance.__class__
      kwargs = {k: v for k, v in vars(instance).items()}
      return lambda: cls(**kwargs)

  _register_momentum_candidates()
  ```
  > NOTE on fidelity: `get_factor` does `ALL_FACTORS[name]()` (`factors.py:373`) and `list_factors`/CLI iterate `ALL_FACTORS` — both only need `ALL_FACTORS[name]` to be a **zero-arg callable returning a Factor**. A class satisfies that; so does the `_bind` closure. This keeps `get_factor`/`list_factors`/CLI working unchanged. The horse-race script (Task 9) uses `build_momentum_candidates()` directly and does NOT depend on this shim, so even if the shim were dropped the race still runs — the registration exists so the **winning** factor is reachable via `get_factor(winner_name)` from production (P3).
- [ ] Step 7.6 — Run, expect PASS:
  ```bash
  "/Users/owen/CC workspace/Finance/.venv/bin/python" -m pytest tests/test_factor_study/test_momentum_factors.py tests/test_factor_study/test_momentum_registry.py -q
  ```
  Expected: momentum_factors 16 passed + registry 2 passed.
- [ ] Step 7.7 — Guard against regressions in the existing registry / framework:
  ```bash
  "/Users/owen/CC workspace/Finance/.venv/bin/python" -m pytest tests/test_factor_study/ -q
  ```
  Expected: all pre-existing framework tests still pass (no breakage from registry mutation).
- [ ] Step 7.8 — Commit:
  ```bash
  git -C "/Users/owen/CC workspace/Finance/.claude/worktrees/momentum-factor-study" add backtest/factor_study/momentum_factors.py backtest/factor_study/factors.py tests/test_factor_study/test_momentum_factors.py tests/test_factor_study/test_momentum_registry.py && git -C "/Users/owen/CC workspace/Finance/.claude/worktrees/momentum-factor-study" commit -m "feat(factor_study): Candidate 4 MFI + register all 18 momentum candidate instances"
  ```

---

## Task 8 — Bias diagnostics module (SIZE + VOL), decoupled from runner

Per-date cross-sectional `Spearman(score, log(market_cap))` (SIZE) and `Spearman(score, realized_vol)` (VOL). HARD GATE metric = `mean(|daily_corr|)` (abs each day THEN mean, M6); `signed_mean` reported separately. PIT market cap from `get_bulk_market_caps_at(date)` (`market_store.py:1484`); realized vol = stdev of 30d daily returns from the **sliced** (PIT) df. Builds its own score panel via `adapter.slice_to_date` + `factor.compute` (M4 — runner does not expose scores).

**Files:**
- Create: `backtest/factor_study/bias_diagnostics.py`
- Test: `tests/test_factor_study/test_bias_diagnostics.py`

- [ ] Step 8.1 — Write failing test `tests/test_factor_study/test_bias_diagnostics.py`:
  ```python
  """Tests for SIZE/VOL bias diagnostics (P0 hard gate)."""
  import numpy as np
  import pandas as pd
  import pytest

  from backtest.factor_study.bias_diagnostics import (
      BiasResult,
      spearman_panel,
      realized_vol_30d,
  )


  def test_realized_vol_30d_matches_manual():
      np.random.seed(1)
      close = 100 * np.exp(np.cumsum(np.random.randn(60) * 0.02))
      df = pd.DataFrame({"close": close})
      rv = realized_vol_30d(df)
      daily = np.diff(close[-31:]) / close[-31:-1]
      assert rv == pytest.approx(float(np.std(daily, ddof=1)), rel=1e-9)

  def test_realized_vol_insufficient_returns_none():
      df = pd.DataFrame({"close": [100.0, 101.0, 102.0]})
      assert realized_vol_30d(df) is None

  def test_spearman_panel_abs_mean_vs_signed_mean():
      # day1: perfect +corr, day2: perfect -corr → signed mean ~0, abs mean ~1
      panel = {
          "2025-01-01": ({"A": 1.0, "B": 2.0, "C": 3.0, "D": 4.0, "E": 5.0},
                          {"A": 1.0, "B": 2.0, "C": 3.0, "D": 4.0, "E": 5.0}),
          "2025-01-08": ({"A": 1.0, "B": 2.0, "C": 3.0, "D": 4.0, "E": 5.0},
                          {"A": 5.0, "B": 4.0, "C": 3.0, "D": 2.0, "E": 1.0}),
      }
      res = spearman_panel(panel)
      assert res.abs_mean == pytest.approx(1.0, abs=1e-9)
      assert abs(res.signed_mean) < 1e-9
      assert res.n_days == 2

  def test_spearman_panel_skips_thin_days():
      panel = {
          "2025-01-01": ({"A": 1.0, "B": 2.0}, {"A": 1.0, "B": 2.0}),  # <5 → skip
      }
      res = spearman_panel(panel)
      assert res.n_days == 0
  ```
- [ ] Step 8.2 — Run, expect FAIL (ModuleNotFoundError):
  ```bash
  "/Users/owen/CC workspace/Finance/.venv/bin/python" -m pytest tests/test_factor_study/test_bias_diagnostics.py -q
  ```
- [ ] Step 8.3 — Implement `backtest/factor_study/bias_diagnostics.py`:
  ```python
  """
  偏差诊断 (P0 硬门槛) — SIZE / VOL 中性检测。

  对每个计算日算截面 Spearman(factor_score, X):
    SIZE: X = log(market_cap)  (PIT, get_bulk_market_caps_at)
    VOL : X = realized_vol_30d (PIT, 30d 日收益 stdev)

  硬门槛用 abs_mean = mean(|daily_corr|) (每日绝对值再平均，避免正负抵消)。
  signed_mean = mean(daily_corr) 仅作方向诊断。

  与 runner 解耦: 自行用 adapter.slice_to_date + factor.compute 重建分数面板
  (runner 不暴露 score_history)。
  """
  import logging
  from dataclasses import dataclass
  from typing import Dict, List, Optional, Tuple

  import numpy as np
  import pandas as pd
  from scipy.stats import spearmanr

  from backtest.factor_study.protocol import Factor

  logger = logging.getLogger(__name__)


  @dataclass
  class BiasResult:
      label: str            # "size" | "vol"
      abs_mean: float       # mean(|daily corr|)  ← HARD GATE 用这个
      signed_mean: float    # mean(daily corr)    ← 仅诊断方向
      n_days: int


  def realized_vol_30d(df: pd.DataFrame) -> Optional[float]:
      """30d 日收益标准差 (ddof=1)。数据不足返回 None。"""
      if df is None or "close" not in df.columns:
          return None
      close = df["close"].astype(float).values
      if len(close) < 31:
          return None
      daily = np.diff(close[-31:]) / close[-31:-1]
      return float(np.std(daily, ddof=1))


  def spearman_panel(
      panel: Dict[str, Tuple[Dict[str, float], Dict[str, float]]],
      label: str = "",
  ) -> BiasResult:
      """panel: {date: (scores, covariate)} → BiasResult。

      每日要求 >=5 个共同 symbol (与 ic_analysis 5-symbol 下限一致)。
      """
      daily_corrs: List[float] = []
      for _date, (scores, cov) in panel.items():
          common = [s for s in scores if s in cov]
          if len(common) < 5:
              continue
          xs = np.array([scores[s] for s in common], dtype=float)
          ys = np.array([cov[s] for s in common], dtype=float)
          mask = ~(np.isnan(xs) | np.isnan(ys))
          if mask.sum() < 5:
              continue
          corr, _ = spearmanr(xs[mask], ys[mask])
          if not np.isnan(corr):
              daily_corrs.append(float(corr))
      if not daily_corrs:
          return BiasResult(label=label, abs_mean=0.0, signed_mean=0.0, n_days=0)
      arr = np.array(daily_corrs)
      return BiasResult(
          label=label,
          abs_mean=float(np.mean(np.abs(arr))),
          signed_mean=float(np.mean(arr)),
          n_days=len(arr),
      )


  def diagnose_factor_bias(
      factor: Factor,
      adapter,
      computation_dates: List[str],
  ) -> Tuple[BiasResult, BiasResult]:
      """对一个 factor 在 computation_dates 上算 SIZE + VOL 偏差。

      复用 runner._compute_scores 的循环形态 (runner.py:204-213) 但解耦。
      """
      from backtest.adapters.us_stocks import _get_bulk_mcaps

      size_panel: Dict[str, Tuple[Dict[str, float], Dict[str, float]]] = {}
      vol_panel: Dict[str, Tuple[Dict[str, float], Dict[str, float]]] = {}

      for comp_date in computation_dates:
          sliced = adapter.slice_to_date(comp_date)
          if not sliced:
              continue
          scores = factor.compute(sliced, comp_date)
          if not scores:
              continue

          # SIZE covariate: log(PIT market cap)
          mcaps = _get_bulk_mcaps(comp_date)  # market_store.get_bulk_market_caps_at
          size_cov = {s: float(np.log(mcaps[s]))
                      for s in scores if s in mcaps and mcaps[s] > 0}
          if size_cov:
              size_panel[comp_date] = (scores, size_cov)

          # VOL covariate: realized 30d vol (PIT, from sliced df)
          vol_cov = {}
          for s in scores:
              rv = realized_vol_30d(sliced.get(s))
              if rv is not None:
                  vol_cov[s] = rv
          if vol_cov:
              vol_panel[comp_date] = (scores, vol_cov)

      return (
          spearman_panel(size_panel, label="size"),
          spearman_panel(vol_panel, label="vol"),
      )
  ```
- [ ] Step 8.4 — Run, expect PASS:
  ```bash
  "/Users/owen/CC workspace/Finance/.venv/bin/python" -m pytest tests/test_factor_study/test_bias_diagnostics.py -q
  ```
  Expected: 4 passed.
- [ ] Step 8.5 — Add an integration test that `diagnose_factor_bias` runs against a tiny stub adapter (no DB), proving the wiring (mock `_get_bulk_mcaps`). Append to the test file:
  ```python
  def test_diagnose_factor_bias_wires_panel(monkeypatch):
      from backtest.factor_study import bias_diagnostics as bd
      from backtest.factor_study.momentum_factors import MomentumRiskAdjFactor

      np.random.seed(5)
      dates = pd.date_range("2025-01-01", periods=60, freq="B")
      data = {}
      for i in range(8):
          close = 100 * np.exp(np.cumsum(np.random.randn(60) * 0.015 + 0.001 * i))
          data[f"S{i}"] = pd.DataFrame({"date": dates, "close": close})

      class StubAdapter:
          def slice_to_date(self, d):
              cutoff = pd.Timestamp(d)
              return {s: df[df["date"] <= cutoff].reset_index(drop=True)
                      for s, df in data.items()}

      monkeypatch.setattr(bd, "_get_bulk_mcaps" if hasattr(bd, "_get_bulk_mcaps")
                          else "__noop__", lambda d: {}, raising=False)
      # patch the imported symbol inside the function namespace instead:
      import backtest.adapters.us_stocks as us
      monkeypatch.setattr(us, "_get_bulk_mcaps",
                          lambda d: {f"S{i}": 1e9 * (i + 1) for i in range(8)})

      f = MomentumRiskAdjFactor(window=10)
      size, vol = bd.diagnose_factor_bias(f, StubAdapter(), ["2025-02-01", "2025-03-01"])
      assert size.label == "size" and size.n_days >= 1
      assert vol.label == "vol" and vol.n_days >= 1
  ```
  > NOTE: `diagnose_factor_bias` imports `_get_bulk_mcaps` from `backtest.adapters.us_stocks` (`us_stocks.py:23`) inside the function, so patching `us_stocks._get_bulk_mcaps` is the correct seam.
- [ ] Step 8.6 — Run, expect PASS:
  ```bash
  "/Users/owen/CC workspace/Finance/.venv/bin/python" -m pytest tests/test_factor_study/test_bias_diagnostics.py -q
  ```
  Expected: 5 passed.
- [ ] Step 8.7 — Commit:
  ```bash
  git -C "/Users/owen/CC workspace/Finance/.claude/worktrees/momentum-factor-study" add backtest/factor_study/bias_diagnostics.py tests/test_factor_study/test_bias_diagnostics.py && git -C "/Users/owen/CC workspace/Finance/.claude/worktrees/momentum-factor-study" commit -m "feat(factor_study): SIZE/VOL bias diagnostics (abs-mean hard gate, PIT, decoupled from runner)"
  ```

---

## Task 9 — Winner selection (lexicographic) + cross-candidate correlation

Pure functions operating on the artifacts from the runner (`ICResult` lists) + bias results. Lexicographic per-horizon: GATE (`size.abs_mean<0.05 AND vol.abs_mean<0.10`) → OOS IC_IR → top-bottom spread → IS/OOS stability. Candidate 2 reports winning α. Cross-candidate score correlation reported.

**Files:**
- Create: `backtest/factor_study/winner_selection.py`
- Test: `tests/test_factor_study/test_winner_selection.py`

- [ ] Step 9.1 — Write failing test `tests/test_factor_study/test_winner_selection.py`:
  ```python
  """Tests for lexicographic per-horizon winner selection (P0)."""
  import numpy as np
  import pytest

  from backtest.factor_study.winner_selection import (
      CandidateRecord,
      passes_gate,
      pick_winner_for_horizon,
      cross_candidate_correlation,
      SIZE_GATE,
      VOL_GATE,
  )


  def test_gate_thresholds():
      assert SIZE_GATE == 0.05
      assert VOL_GATE == 0.10

  def test_passes_gate_boundary():
      assert passes_gate(0.049, 0.099, 120, 120) is True
      assert passes_gate(0.05, 0.05, 120, 120) is False   # not strictly < 0.05
      assert passes_gate(0.01, 0.11, 120, 120) is False    # vol fails

  def test_thin_coverage_fails_closed():
      # 没测到偏差 (n_days=0) 必须 fail closed，不能当"完美中性"通过
      assert passes_gate(0.0, 0.0, 0, 0) is False
      assert passes_gate(0.0, 0.0, 5, 5) is False          # 低于 MIN_BIAS_DAYS
      assert passes_gate(0.0, 0.0, 120, 120) is True

  def _rec(name, size_abs, vol_abs, oos_ir, spread, stab, size_n=120, vol_n=120,
           has_oos=True, oos_obs=120):
      return CandidateRecord(
          name=name, horizon=10, size_abs_mean=size_abs, vol_abs_mean=vol_abs,
          size_n_days=size_n, vol_n_days=vol_n,
          oos_ic_ir=oos_ir, has_oos_ic=has_oos, oos_n_obs=oos_obs,
          top_bottom_spread=spread, is_oos_stability=stab,
      )

  def test_gate_eliminates_high_ic_but_biased():
      cands = [
          _rec("Biased", 0.20, 0.02, 5.0, 0.10, 0.9),   # huge IC, fails size gate
          _rec("Clean",  0.02, 0.03, 1.2, 0.04, 0.8),   # survives
      ]
      winner = pick_winner_for_horizon(cands)
      assert winner.name == "Clean"

  def test_tie_break_by_spread_then_stability():
      cands = [
          _rec("A", 0.01, 0.01, 1.50, 0.03, 0.5),
          _rec("B", 0.01, 0.01, 1.50, 0.06, 0.4),  # same IR, bigger spread → wins
      ]
      assert pick_winner_for_horizon(cands).name == "B"

  def test_no_survivor_returns_none():
      cands = [_rec("X", 0.9, 0.9, 9.0, 0.5, 0.9)]
      assert pick_winner_for_horizon(cands) is None

  def test_zero_coverage_candidate_not_winner():
      # abs_mean 看着完美 (0.0) 但根本没测到偏差 → 不得入选
      cands = [
          _rec("NoData", 0.0, 0.0, 9.0, 0.5, 0.9, size_n=0, vol_n=0),
          _rec("Clean",  0.02, 0.03, 1.0, 0.04, 0.8),
      ]
      assert pick_winner_for_horizon(cands).name == "Clean"

  def test_missing_oos_ic_not_winner():
      # 没有 OOS IC 的候选不得入选（即便 IS 漂亮）— 契约是 OOS 优先，绝不退回 IS
      cands = [
          _rec("NoOOS", 0.02, 0.03, 9.0, 0.5, 0.9, has_oos=False, oos_obs=0),
          _rec("Clean", 0.02, 0.03, 1.0, 0.04, 0.8),
      ]
      assert pick_winner_for_horizon(cands).name == "Clean"

  def test_thin_oos_sample_not_winner():
      # OOS 存在但观测数不足 (< MIN_OOS_OBS) → fail closed
      cands = [
          _rec("ThinOOS", 0.02, 0.03, 9.0, 0.5, 0.9, oos_obs=3),
          _rec("Clean",   0.02, 0.03, 1.0, 0.04, 0.8),
      ]
      assert pick_winner_for_horizon(cands).name == "Clean"

  def test_cross_candidate_correlation_symmetric():
      panel = {
          "A": {"S0": 1.0, "S1": 2.0, "S2": 3.0, "S3": 4.0, "S4": 5.0},
          "B": {"S0": 1.0, "S1": 2.0, "S2": 3.0, "S3": 4.0, "S4": 5.0},
      }
      corr = cross_candidate_correlation(panel)
      assert corr[("A", "B")] == pytest.approx(1.0, abs=1e-9)
  ```
- [ ] Step 9.2 — Run, expect FAIL:
  ```bash
  "/Users/owen/CC workspace/Finance/.venv/bin/python" -m pytest tests/test_factor_study/test_winner_selection.py -q
  ```
- [ ] Step 9.3 — Implement `backtest/factor_study/winner_selection.py`:
  ```python
  """
  选胜判据 (P0, 字典序，约束优先) — docs/design/...:104-109

  1. GATE: size_abs_mean<0.05 AND vol_abs_mean<0.10 (在该 horizon)
  2. 幸存者按 OOS IC_IR 降序
  3. tie-break: top-bottom spread → IS/OOS 稳定性
  逐 horizon 各选一个 (可不同)。Candidate 2 额外报获胜 α。
  跨候选分数相关另算 (cross_candidate_correlation)。
  """
  from dataclasses import dataclass
  from itertools import combinations
  from typing import Dict, List, Optional, Tuple

  import numpy as np
  from scipy.stats import spearmanr

  SIZE_GATE = 0.05
  VOL_GATE = 0.10
  MIN_BIAS_DAYS = 20   # 偏差估计至少需要这么多有效日；否则 fail closed（没测到 ≠ 中性）
  MIN_OOS_OBS = 20     # 单 horizon 的 OOS IC 至少这么多观测数才够格选胜；否则 fail closed
                       # 注：runner 已在结果层强制 OOS 日数>=min_oos_dates(50)，此为 per-horizon 兜底
                       # （长 horizon 的 OOS forward-return 截断会让某 horizon 的 OOS IC 缺失/过薄）


  @dataclass
  class CandidateRecord:
      name: str
      horizon: int
      size_abs_mean: float
      vol_abs_mean: float
      size_n_days: int          # 支撑 size_abs_mean 的有效逐日相关天数 (coverage)
      vol_n_days: int           # 支撑 vol_abs_mean 的有效逐日相关天数 (coverage)
      oos_ic_ir: Optional[float]  # 该 horizon 的 OOS IC_IR；None=无 OOS（绝不退回 IS）
      has_oos_ic: bool          # 该 horizon 是否真有 OOS IC（缺失 → fail closed）
      oos_n_obs: int            # 该 horizon 的 OOS IC 观测数 (ICResult.n_ic_obs)
      top_bottom_spread: float
      is_oos_stability: float   # e.g. 1 - |IS_IR - OOS_IR| / (|IS_IR|+eps); higher better


  def passes_gate(size_abs_mean: float, vol_abs_mean: float,
                  size_n_days: int, vol_n_days: int,
                  min_days: int = MIN_BIAS_DAYS) -> bool:
      # FAIL CLOSED on thin coverage: abs_mean 默认 0.0，没测到偏差 ≠ 完美中性。
      if size_n_days < min_days or vol_n_days < min_days:
          return False
      return size_abs_mean < SIZE_GATE and vol_abs_mean < VOL_GATE


  def pick_winner_for_horizon(
      candidates: List[CandidateRecord],
      min_oos_obs: int = MIN_OOS_OBS,
  ) -> Optional[CandidateRecord]:
      # 选胜契约 = "OOS IC_IR 优先" → 必须真有 OOS 且样本达标，绝不用 IS 顶替。
      survivors = [c for c in candidates
                   if c.has_oos_ic and c.oos_n_obs >= min_oos_obs
                   and passes_gate(c.size_abs_mean, c.vol_abs_mean,
                                   c.size_n_days, c.vol_n_days)]
      if not survivors:
          return None
      survivors.sort(
          key=lambda c: (c.oos_ic_ir, c.top_bottom_spread, c.is_oos_stability),
          reverse=True,
      )
      return survivors[0]


  def cross_candidate_correlation(
      score_panel: Dict[str, Dict[str, float]],
  ) -> Dict[Tuple[str, str], float]:
      """score_panel: {candidate_name: {symbol: score}} on one date.

      Returns Spearman rank corr between every candidate pair on common symbols.
      """
      out: Dict[Tuple[str, str], float] = {}
      names = sorted(score_panel)
      for a, b in combinations(names, 2):
          sa, sb = score_panel[a], score_panel[b]
          common = sorted(set(sa) & set(sb))
          if len(common) < 5:
              out[(a, b)] = float("nan")
              continue
          xs = np.array([sa[s] for s in common])
          ys = np.array([sb[s] for s in common])
          corr, _ = spearmanr(xs, ys)
          out[(a, b)] = float(corr)
      return out
  ```
- [ ] Step 9.4 — Run, expect PASS:
  ```bash
  "/Users/owen/CC workspace/Finance/.venv/bin/python" -m pytest tests/test_factor_study/test_winner_selection.py -q
  ```
  Expected: 10 passed.
- [ ] Step 9.5 — Commit:
  ```bash
  git -C "/Users/owen/CC workspace/Finance/.claude/worktrees/momentum-factor-study" add backtest/factor_study/winner_selection.py tests/test_factor_study/test_winner_selection.py && git -C "/Users/owen/CC workspace/Finance/.claude/worktrees/momentum-factor-study" commit -m "feat(factor_study): lexicographic winner selection + cross-candidate correlation"
  ```

---

## Task 10 — Horse-race orchestration script

Wires it all: build candidates → `us_factor_study(market='us_stocks', computation_freq='W', forward_horizons=[3,10,30], benchmark_symbols=['QQQ','POOL_AVG'])` → `USStocksAdapter(universe='extended')` → `FactorStudyRunner.run()` for IC (IS+OOS, excess returns) → `diagnose_factor_bias` per candidate → assemble `CandidateRecord`s → `pick_winner_for_horizon` per horizon → `cross_candidate_correlation` on the last computation date → emit a JSON/CSV result bundle under `reports/factor_study/momentum_horse_race/`.

This is a script (not a unit-tested library), but it gets one smoke test on a tiny symbol list to prove the wiring end-to-end without the full ~955-symbol run.

**Files:**
- Create: `scripts/run_momentum_horse_race.py`
- Test: `tests/test_factor_study/test_horse_race_smoke.py`

- [ ] Step 10.1 — Write failing smoke test `tests/test_factor_study/test_horse_race_smoke.py`:
  ```python
  """Smoke test: horse-race orchestration runs end-to-end on a tiny symbol set."""
  import pytest

  from scripts.run_momentum_horse_race import run_horse_race


  @pytest.mark.slow
  def test_horse_race_smoke_small_universe():
      # 6 real symbols, weekly, just proves wiring (IC + bias + selection)
      result = run_horse_race(
          symbols=["NVDA", "MSFT", "AAPL", "AMD", "AVGO", "TSLA"],
          horizons=[10],
          start="2024-01-01",
          end="2026-01-01",
          write_outputs=False,
      )
      assert "per_horizon_winner" in result
      assert "candidate_records" in result
      assert "cross_candidate_corr" in result
      # every candidate record has both bias metrics populated
      for rec in result["candidate_records"]:
          assert rec.size_abs_mean >= 0.0
          assert rec.vol_abs_mean >= 0.0
  ```
  > The smoke test imports `scripts.run_momentum_horse_race`; ensure `scripts/__init__.py` exists or add `sys.path` insert at top of test (mirror `tests/test_rs_rating.py:16-17`). Check first:
  ```bash
  ls "/Users/owen/CC workspace/Finance/scripts/__init__.py" 2>/dev/null || echo "no scripts/__init__.py — add sys.path.insert in the script + test"
  ```
- [ ] Step 10.2 — Run, expect FAIL:
  ```bash
  "/Users/owen/CC workspace/Finance/.venv/bin/python" -m pytest tests/test_factor_study/test_horse_race_smoke.py -q
  ```
- [ ] Step 10.3 — Implement `scripts/run_momentum_horse_race.py`:
  ```python
  #!/usr/bin/env python3
  """
  动量因子赛马 (P0) — 编排脚本

  流程:
    1. build_momentum_candidates() → 18 个候选实例
    2. us_factor_study(freq=W, horizons=[3,10,30], benchmark=QQQ+POOL_AVG)
    3. USStocksAdapter(universe="extended")  (~955)
    4. FactorStudyRunner.run() → IC (IS+OOS, excess)
    5. diagnose_factor_bias(candidate, adapter, oos_dates) → SIZE/VOL abs_mean
    6. CandidateRecord 组装 → pick_winner_for_horizon (逐 3/10/30)
    7. cross_candidate_correlation (最后一个计算日)
    8. 落 JSON/CSV → reports/factor_study/momentum_horse_race/
  """
  import argparse
  import json
  import logging
  import sys
  from pathlib import Path

  _ROOT = Path(__file__).resolve().parent.parent
  sys.path.insert(0, str(_ROOT))

  from backtest.config import us_factor_study, FREQ_DAYS
  from backtest.adapters.us_stocks import USStocksAdapter
  from backtest.factor_study.runner import FactorStudyRunner
  from backtest.factor_study.momentum_factors import build_momentum_candidates
  from backtest.factor_study.bias_diagnostics import diagnose_factor_bias
  from backtest.factor_study.winner_selection import (
      CandidateRecord, pick_winner_for_horizon, cross_candidate_correlation,
  )

  logger = logging.getLogger(__name__)
  # reports/ (NOT data/): `.gitignore` 忽略 /data/*，研究 artifact 必须可提交 (Task 12.5)
  _OUT = _ROOT / "reports" / "factor_study" / "momentum_horse_race"


  def _stability(is_ir: float, oos_ir: float) -> float:
      denom = abs(is_ir) + 1e-6
      return 1.0 - abs(is_ir - oos_ir) / denom


  def run_horse_race(
      symbols=None,
      universe="extended",
      horizons=(3, 10, 30),
      benchmarks=("QQQ", "POOL_AVG"),
      freq="W",
      start=None,
      end=None,
      oos_start=None,
      write_outputs=True,
  ) -> dict:
      candidates = build_momentum_candidates()  # {name: Factor}

      cfg = us_factor_study(
          computation_freq=freq,
          forward_horizons=list(horizons),
          benchmark_symbols=list(benchmarks),
      )
      if start:
          cfg.start_date = start
      if end:
          cfg.end_date = end
      if oos_start:
          cfg.oos_start_date = oos_start

      adapter = (USStocksAdapter(symbols=list(symbols))
                 if symbols else USStocksAdapter(universe=universe))

      runner = FactorStudyRunner(cfg, adapter)
      for f in candidates.values():
          runner.add_factor(f)
      results = runner.run()  # List[FactorStudyResults], 1 per (factor × benchmark)

      # Recompute the computation_dates the runner used (for bias diagnostics).
      all_dates = adapter.get_trading_dates()
      if cfg.start_date:
          all_dates = [d for d in all_dates if d >= cfg.start_date]
      if cfg.end_date:
          all_dates = [d for d in all_dates if d <= cfg.end_date]
      comp_dates = all_dates[:: FREQ_DAYS[cfg.freq if hasattr(cfg, "freq") else cfg.computation_freq]]

      # Bias diagnostics computed once per candidate over OOS dates (the
      # decision-relevant window). Fall back to all comp_dates if no OOS.
      sample_res = results[0] if results else None
      bias_dates = (sample_res.oos_dates if sample_res and sample_res.oos_dates
                    else comp_dates)
      bias = {name: diagnose_factor_bias(f, adapter, bias_dates)
              for name, f in candidates.items()}

      # Assemble CandidateRecord per (candidate, horizon). Use QQQ benchmark
      # excess results (first benchmark). Selection IC_IR is OOS-only (NO IS
      # fallback); IS IC is kept only to derive the IS/OOS stability metric.
      records_by_h = {h: [] for h in horizons}
      bench0 = benchmarks[0]
      for res in results:
          if res.benchmark_label != bench0:
              continue
          name = res.factor_name
          size_b, vol_b = bias[name]
          is_ir = {ic.horizon: ic.ic_ir for ic in (res.ic_results or [])}
          oos_ir = {ic.horizon: ic.ic_ir for ic in (res.oos_ic_results or [])}
          oos_obs = {ic.horizon: ic.n_ic_obs for ic in (res.oos_ic_results or [])}
          # spread/tie-break 也取 OOS-only：契约是 OOS 优先，不让 IS 冒充 OOS。
          spread = {ic.horizon: ic.top_bottom_spread
                    for ic in (res.oos_ic_results or [])}
          for h in horizons:
              records_by_h[h].append(CandidateRecord(
                  name=name, horizon=h,
                  size_abs_mean=size_b.abs_mean, vol_abs_mean=vol_b.abs_mean,
                  size_n_days=size_b.n_days, vol_n_days=vol_b.n_days,
                  oos_ic_ir=oos_ir.get(h),          # None if no OOS IC — 绝不退回 IS
                  has_oos_ic=h in oos_ir,
                  oos_n_obs=oos_obs.get(h, 0),
                  top_bottom_spread=spread.get(h, 0.0),
                  is_oos_stability=_stability(is_ir.get(h, 0.0), oos_ir.get(h, 0.0)),
              ))

      per_horizon_winner = {h: pick_winner_for_horizon(recs)
                            for h, recs in records_by_h.items()}

      # Cross-candidate correlation on the last computation date.
      last_panel = {}
      if comp_dates:
          sliced = adapter.slice_to_date(comp_dates[-1])
          for name, f in candidates.items():
              s = f.compute(sliced, comp_dates[-1])
              if s:
                  last_panel[name] = s
      cc_corr = cross_candidate_correlation(last_panel)

      bundle = {
          "config": {"freq": freq, "horizons": list(horizons),
                     "benchmarks": list(benchmarks),
                     "n_comp_dates": len(comp_dates)},
          "per_horizon_winner": {h: (w.name if w else None)
                                 for h, w in per_horizon_winner.items()},
          "candidate_records": [r for recs in records_by_h.values() for r in recs],
          "bias": {n: {"size_abs": s.abs_mean, "size_signed": s.signed_mean,
                       "size_n_days": s.n_days,
                       "vol_abs": v.abs_mean, "vol_signed": v.signed_mean,
                       "vol_n_days": v.n_days}
                   for n, (s, v) in bias.items()},
          "cross_candidate_corr": {f"{a}|{b}": c for (a, b), c in cc_corr.items()},
      }

      if write_outputs:
          _OUT.mkdir(parents=True, exist_ok=True)
          serializable = {**bundle,
                          "candidate_records": [vars(r) for r in bundle["candidate_records"]]}
          (_OUT / "horse_race_result.json").write_text(
              json.dumps(serializable, indent=2, ensure_ascii=False), encoding="utf-8")
          logger.info("Horse-race bundle 写入 %s", _OUT)

      return bundle


  def main():
      p = argparse.ArgumentParser(description="动量因子赛马 (P0)")
      p.add_argument("--universe", default="extended")
      p.add_argument("--horizons", default="3,10,30")
      p.add_argument("--freq", default="W", choices=["D", "W"])
      p.add_argument("--start"); p.add_argument("--end"); p.add_argument("--oos-start")
      p.add_argument("-v", "--verbose", action="store_true")
      a = p.parse_args()
      logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                          format="%(asctime)s [%(levelname)s] %(message)s")
      res = run_horse_race(
          universe=a.universe,
          horizons=tuple(int(h) for h in a.horizons.split(",")),
          freq=a.freq, start=a.start, end=a.end, oos_start=a.oos_start,
      )
      print(json.dumps(res["per_horizon_winner"], indent=2, ensure_ascii=False))


  if __name__ == "__main__":
      main()
  ```
  > FIDELITY NOTE — `cfg.computation_freq` is the real field (`config.py:130`); the `cfg.freq if hasattr...` guard above is defensive and resolves to `cfg.computation_freq`. Simplify to `FREQ_DAYS[cfg.computation_freq]` during implementation (the runner uses exactly `FREQ_DAYS.get(self._config.computation_freq, 5)`, `runner.py:116`). The `computation_dates` recomputation mirrors `runner.py:110-117` exactly so bias diagnostics align with the IC dates.
- [ ] Step 10.4 — If `scripts/__init__.py` is missing, prepend `sys.path` insert to the test (mirror `tests/test_rs_rating.py:16-17`) OR import via file path. Then run the smoke test (it hits real market.db, mark `slow`):
  ```bash
  "/Users/owen/CC workspace/Finance/.venv/bin/python" -m pytest tests/test_factor_study/test_horse_race_smoke.py -q -m slow
  ```
  Expected: 1 passed (a few seconds on 6 symbols).
- [ ] Step 10.5 — Sanity-run the wiring on a slightly larger but still fast slice (verify no exceptions, real numbers come out):
  ```bash
  "/Users/owen/CC workspace/Finance/.venv/bin/python" scripts/run_momentum_horse_race.py --universe pool --freq W --start 2024-01-01 -v 2>&1 | tail -25
  ```
  Expected: prints `per_horizon_winner` JSON (winners or `null` if all gated out on this small set — both are valid wiring outcomes). NOTE: use `pool` (~147) for the fast sanity run; the full `extended` run happens in Task 12.
- [ ] Step 10.6 — Commit:
  ```bash
  git -C "/Users/owen/CC workspace/Finance/.claude/worktrees/momentum-factor-study" add scripts/run_momentum_horse_race.py tests/test_factor_study/test_horse_race_smoke.py && git -C "/Users/owen/CC workspace/Finance/.claude/worktrees/momentum-factor-study" commit -m "feat(factor_study): momentum horse-race orchestration (IC + bias + selection + cross-corr)"
  ```

---

## Task 11 — P0→P3 daily-frequency sensitivity check on the winner

After a weekly winner is chosen per horizon, re-run that **single** factor at `computation_freq='D'` and confirm the OOS IC sign/direction matches the weekly result. Direction flip → flag re-pick (`docs/design/...:115`).

**Files:**
- Create: `backtest/factor_study/daily_sensitivity.py`
- Test: `tests/test_factor_study/test_daily_sensitivity.py`

- [ ] Step 11.1 — Write failing test `tests/test_factor_study/test_daily_sensitivity.py`:
  ```python
  """Tests for the P0→P3 daily-frequency sensitivity (IC sign-flip detector)."""
  import pytest

  from backtest.factor_study.daily_sensitivity import (
      SensitivityResult,
      direction_consistent,
  )


  def test_direction_consistent_same_sign():
      assert direction_consistent(weekly_ic=0.04, daily_ic=0.02) is True
      assert direction_consistent(weekly_ic=-0.03, daily_ic=-0.05) is True

  def test_direction_flip_detected():
      assert direction_consistent(weekly_ic=0.04, daily_ic=-0.02) is False

  def test_near_zero_daily_is_flagged_inconsistent():
      # daily IC collapses to ~0 → not a confident same-direction confirmation
      assert direction_consistent(weekly_ic=0.05, daily_ic=0.0005,
                                  min_abs=0.002) is False
      # default floor (0.002, not 0.0) must also catch it — regression guard for the
      # old fail-open where a missing/zero daily IC passed as "consistent":
      assert direction_consistent(weekly_ic=0.05, daily_ic=0.0005) is False
      assert direction_consistent(weekly_ic=0.05, daily_ic=0.0) is False

  def test_missing_daily_ic_is_inconsistent():
      # runner produced no IC for this (benchmark,horizon) → daily_ic None → fail closed
      assert direction_consistent(weekly_ic=0.05, daily_ic=None) is False
  ```
- [ ] Step 11.2 — Run, expect FAIL:
  ```bash
  "/Users/owen/CC workspace/Finance/.venv/bin/python" -m pytest tests/test_factor_study/test_daily_sensitivity.py -q
  ```
- [ ] Step 11.3 — Implement `backtest/factor_study/daily_sensitivity.py`:
  ```python
  """
  P0→P3 软门槛 — winner 的日频敏感性 (docs/design/...:115)

  weekly 选出 winner 后，对 winner 跑一次 daily computation，
  确认 OOS IC 符号/方向不反 (晨报是日频产品)。方向反转 → 回候选池重选。
  """
  from dataclasses import dataclass
  from typing import List, Optional

  from backtest.config import us_factor_study
  from backtest.factor_study.runner import FactorStudyRunner


  @dataclass
  class SensitivityResult:
      factor_name: str
      horizon: int
      weekly_oos_ic: float
      daily_oos_ic: Optional[float]   # None: runner 没产出该 (benchmark,horizon) 的 IC
      consistent: bool
      found_daily_ic: bool            # 是否真的找到 daily IC（没找到 → fail closed）


  def direction_consistent(
      weekly_ic: float, daily_ic: Optional[float], min_abs: float = 0.002,
  ) -> bool:
      """Same sign AND daily IC magnitude above min_abs floor.

      daily_ic is None when the runner produced no IC for this (benchmark,horizon)
      → NOT consistent (fail closed). Default min_abs>0 so a ~0 daily IC (incl. the
      old 0.0 sentinel) is never silently judged 'same direction'.
      """
      if daily_ic is None or abs(daily_ic) < min_abs:
          return False
      return (weekly_ic >= 0) == (daily_ic >= 0)


  def run_daily_sensitivity(
      factor, adapter, horizon: int, weekly_oos_ic: float,
      benchmarks=("QQQ",), start=None, end=None, oos_start=None,
  ) -> SensitivityResult:
      cfg = us_factor_study(
          computation_freq="D",
          forward_horizons=[horizon],
          benchmark_symbols=list(benchmarks),
      )
      if start:
          cfg.start_date = start
      if end:
          cfg.end_date = end
      if oos_start:
          cfg.oos_start_date = oos_start

      runner = FactorStudyRunner(cfg, adapter)
      runner.add_factor(factor)
      results = runner.run()

      daily_ic = None   # None until a matching OOS IC is actually found (fail closed)
      for res in results:
          if res.benchmark_label != benchmarks[0]:
              continue
          # OOS-only：契约是 daily OOS 方向验证，无 OOS → 留 None → found=False（绝不退回 IS）
          for ic in (res.oos_ic_results or []):
              if ic.horizon == horizon:
                  daily_ic = ic.ic_ir  # OOS IC_IR direction (matches selection metric)
      found = daily_ic is not None
      return SensitivityResult(
          factor_name=factor.meta.name, horizon=horizon,
          weekly_oos_ic=weekly_oos_ic, daily_oos_ic=daily_ic,
          consistent=found and direction_consistent(weekly_oos_ic, daily_ic, min_abs=0.002),
          found_daily_ic=found,
      )
  ```
  > FIDELITY NOTE — `run_daily_sensitivity` reuses the **same verified runner**; only the unit-tested pure function `direction_consistent` is covered by tests. The runner-driven `run_daily_sensitivity` is exercised in the Task 12 full run (it requires real DB + an already-chosen winner factor, so a unit test would just re-test the runner). Its `found_daily_ic` fail-closed path (no IC produced ⇒ consistent=False) is asserted on that real run in Task 12.3. Daily freq on ~955 symbols is heavier — Task 12 runs it only for the chosen winner per horizon (≤3 factors), not all 18.
- [ ] Step 11.4 — Run, expect PASS:
  ```bash
  "/Users/owen/CC workspace/Finance/.venv/bin/python" -m pytest tests/test_factor_study/test_daily_sensitivity.py -q
  ```
  Expected: 4 passed.
- [ ] Step 11.5 — Commit:
  ```bash
  git -C "/Users/owen/CC workspace/Finance/.claude/worktrees/momentum-factor-study" add backtest/factor_study/daily_sensitivity.py tests/test_factor_study/test_daily_sensitivity.py && git -C "/Users/owen/CC workspace/Finance/.claude/worktrees/momentum-factor-study" commit -m "feat(factor_study): P0->P3 daily-frequency IC sign-flip sensitivity check"
  ```

---

## Task 12 — Full extended-pool run + research report

Execute the real horse race on the extended pool, run daily sensitivity on the winners, and write the research report. This is the deliverable.

**Files:**
- Create: `docs/research/2026-06-<dd>-momentum-factor-study.md` (dd = run date)
- (Output artifacts) `reports/factor_study/momentum_horse_race/horse_race_result.json`

- [ ] Step 12.1 — Run the full weekly horse race on the extended pool (this is the heavy compute; expect minutes, run in background if needed). Use an explicit OOS start to fix the split:
  ```bash
  "/Users/owen/CC workspace/Finance/.venv/bin/python" scripts/run_momentum_horse_race.py --universe extended --freq W --start 2021-06-01 --oos-start 2024-06-01 -v 2>&1 | tee /tmp/horse_race_full.log | tail -40
  ```
  Expected: completes; prints `per_horizon_winner` for 3/10/30; writes `reports/factor_study/momentum_horse_race/horse_race_result.json`. Verify the JSON has bias metrics for all 18 candidates and cross-candidate correlations.
- [ ] Step 12.2 — Confirm the bundle is well-formed and inspect winners + gate survivors:
  ```bash
  "/Users/owen/CC workspace/Finance/.venv/bin/python" -c "
  import json, pathlib
  b = json.load(open('reports/factor_study/momentum_horse_race/horse_race_result.json'))
  print('winners:', b['per_horizon_winner'])
  print('n candidates with bias:', len(b['bias']))
  # gate survivors per the design thresholds
  surv = {n: v for n, v in b['bias'].items()
          if v['size_abs']<0.05 and v['vol_abs']<0.10
          and v['size_n_days']>=20 and v['vol_n_days']>=20}  # coverage floor = MIN_BIAS_DAYS
  print('gate survivors:', len(surv), sorted(surv)[:10])
  # OOS fail-closed visibility: any candidate-horizon missing OOS IC is excluded from selection
  miss = [(r['name'], r['horizon']) for r in b['candidate_records'] if not r['has_oos_ic']]
  print('candidate-horizons missing OOS IC (excluded):', len(miss), miss[:10])
  "
  ```
- [ ] Step 12.3 — For each per-horizon winner, run the daily sensitivity check (≤3 factors). Drive it via a one-off snippet that loads the winner from `build_momentum_candidates()` and calls `run_daily_sensitivity` (reuse the same `--oos-start`):
  ```bash
  "/Users/owen/CC workspace/Finance/.venv/bin/python" -c "
  import json
  from backtest.adapters.us_stocks import USStocksAdapter
  from backtest.factor_study.momentum_factors import build_momentum_candidates
  from backtest.factor_study.daily_sensitivity import run_daily_sensitivity
  b = json.load(open('reports/factor_study/momentum_horse_race/horse_race_result.json'))
  cands = build_momentum_candidates()
  adapter = USStocksAdapter(universe='extended')
  # weekly OOS IC_IR per (winner, horizon) read from candidate_records
  recs = {(r['name'], r['horizon']): r['oos_ic_ir'] for r in b['candidate_records']}
  for h, name in b['per_horizon_winner'].items():
      if not name: 
          print(h, 'NO WINNER (all gated out)'); continue
      w_ic = recs.get((name, int(h)), 0.0)
      sr = run_daily_sensitivity(cands[name], adapter, int(h), w_ic,
                                 start='2021-06-01', oos_start='2024-06-01')
      print(h, name, 'weekly_ir=%.3f daily_ir=%s consistent=%s found=%s' % (
          sr.weekly_oos_ic, sr.daily_oos_ic, sr.consistent, sr.found_daily_ic))
  " 2>&1 | tail -15
  ```
  Expected: prints consistency per horizon. Any `consistent=False` (direction flip) **or `found=False` (no daily IC produced)** MUST be flagged in the report → re-pick from gate survivors.
- [ ] Step 12.4 — Write the research report `docs/research/2026-06-<dd>-momentum-factor-study.md` (dd = today). It MUST contain, grounded in the run artifacts (no invented numbers — pull from the JSON + logs):
  - Header: date, north-star alignment (分析层), link to `docs/design/2026-06-02-morning-report-redesign.md §4 Phase 0`.
  - Method: 18 candidates (4 families × {3,10,30}, Cand-2 × α{0.3,0.5,0.7}), weekly comp freq, extended pool (actual N), excess returns vs QQQ+POOL_AVG, IS/OOS split point.
  - Results table per horizon: candidate, OOS IC_IR, top-bottom spread, size_abs, vol_abs, gate PASS/FAIL.
  - **Bias scatter description**: x=size_abs, y=vol_abs per candidate×window; winners near origin. (The numeric coords come from the `bias` block; render via the existing `report.py` HTML pattern OR a static table — the design wants the scatter; a markdown table of (size_abs, vol_abs) per candidate is the minimum acceptable, with the existing factor_study HTML report as the richer artifact.)
  - **Per-horizon winner** + Candidate-2 winning α (if a VolConf instance wins).
  - **Cross-candidate correlation** matrix (from `cross_candidate_corr`) — confirm winner is not a re-skin of another arm.
  - **"加量是否提升 IC" verdict**: compare Candidate 1 (price-only) vs Candidate 2 (price+vol) OOS IC_IR at each horizon — does adding volume improve IC?
  - **P0→P3 daily sensitivity**: per-horizon consistency result; any flips flagged with re-pick.
  - **3d caveat**: explicit note on whether the 3d price leg is weak/reversal-prone (expected per `docs/design/...:161`) and whether the volume leg carries it.
  - Conclusion: the production winner factor name(s) + how P3 should call it (`get_factor("<winner_name>")`).
- [ ] Step 12.5 — Commit the report + artifacts:
  ```bash
  git -C "/Users/owen/CC workspace/Finance/.claude/worktrees/momentum-factor-study" add docs/research/2026-06-*-momentum-factor-study.md reports/factor_study/momentum_horse_race/ && git -C "/Users/owen/CC workspace/Finance/.claude/worktrees/momentum-factor-study" commit -m "research(P0): momentum factor study — per-horizon winners + bias gate + daily sensitivity"
  ```

---

## Task 13 — Full test sweep + finish

- [ ] Step 13.1 — Run the full new test surface + the framework regression set:
  ```bash
  "/Users/owen/CC workspace/Finance/.venv/bin/python" -m pytest tests/test_volume_ratio.py tests/test_mfi.py tests/test_factor_study/ -q
  ```
  Expected: all green (new indicator/factor/bias/selection/sensitivity tests + pre-existing factor_study tests).
- [ ] Step 13.2 — Confirm the winning factor is reachable from production via the registry (P3 dependency):
  ```bash
  "/Users/owen/CC workspace/Finance/.venv/bin/python" -c "
  from backtest.factor_study.factors import get_factor, list_factors
  ms = [n for n in list_factors() if n.startswith('Momentum_')]
  print('registered momentum factors:', len(ms))
  print(get_factor(ms[0]).meta)  # builds an instance
  "
  ```
  Expected: 18 registered; `get_factor` returns a working instance.
- [ ] Step 13.3 — Per project rule "不经 Boss 不 merge/push": STOP here. Report the per-horizon winners, the "加量提升 IC" verdict, and any daily-sensitivity flips to Boss. Do NOT merge `feat/momentum-factor-study` into `main` or push without explicit approval. Leave the worktree in place.

---

## Verification summary (Boss-facing, no code needed)

- **P0 deliverable check** (`docs/design/...:170`): research report gives per-horizon winning indicator + bias scatter (winners near origin) + an explicit "does adding volume improve IC" conclusion.
- **Gate works**: a candidate with high IC but `size_abs >= 0.05` or `vol_abs >= 0.10` is eliminated (Task 9 test `test_gate_eliminates_high_ic_but_biased` proves the logic; Task 12 shows it on real data).
- **Daily sanity**: winners confirmed not to flip IC direction at daily frequency, or flagged for re-pick.
- **Production-ready**: winner reachable via `get_factor("<name>")` for P3 integration.

---

## Risks & self-justification

- **Biggest risk:** the bias diagnostics recompute the score panel independently of the runner (M4), so a divergence between the runner's `computation_dates` and the diagnostics' dates would mean IC and bias are measured on different days. Mitigation: Task 10 recomputes `computation_dates` with the **exact** `runner.py:110-117` logic (`all_dates[::FREQ_DAYS[freq]]` after start/end filter) and feeds bias diagnostics the runner's own `oos_dates` from `FactorStudyResults`. Verified the slice carries OHLCV so both paths see identical data.
- **Why not modify the runner to expose scores?** Project rule: don't rewrite verified logic; wrap it. The runner is load-bearing for other studies (RS, PMARP, crypto). Adding a public score accessor is a larger blast radius than a decoupled diagnostics pass. If the recompute cost proves too high on ~955 symbols × daily, revisit by adding an opt-in `return_scores` flag to `run()` in a follow-up (out of scope here).
- **Why z-score MFI (already [0,100])?** To put all four candidates on one comparable scale for cross-candidate correlation and the same gate; IC (Spearman, rank-based) is invariant to the monotone z transform, so it does not distort MFI's predictive ranking.
- **Daily-freq cost on full pool:** Task 12.3 runs daily sensitivity only for the ≤3 winners, not all 18 — bounded compute.

## Open questions for Boss

1. **OOS split point** — the plan fixes `--oos-start 2024-06-01` for reproducibility (≈2yr IS / 2yr OOS given data from 2021-02). Acceptable, or prefer the default last-30% fraction?
2. **Benchmark for selection metric** — ✅ RESOLVED (Boss, 2026-06-02): **QQQ is the primary selection benchmark**; POOL_AVG stays computed + reported as a comparison column. Low-risk by construction: a cross-sectional factor's IC_IR / top-bottom spread / bias gate are invariant to a per-date constant shift (`r_i − r_QQQ` and `r_i − pool_mean` shift every stock equally on a given date → ranks unchanged), so the choice only moves reported excess-return *magnitudes* + the `daily_sensitivity` `benchmark_label` filter (already defaults to `QQQ`, consistent with `run_horse_race`'s `bench0`).
3. **Bias scatter rendering** — minimum is a markdown table of (size_abs, vol_abs); is the existing `report.py` Chart.js HTML scatter required for P0 sign-off, or is the table sufficient and the HTML deferred to P3?
