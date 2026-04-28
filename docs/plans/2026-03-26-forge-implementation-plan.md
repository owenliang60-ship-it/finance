# Forge — 接口级实现计划

> **依赖**: `docs/plans/2026-03-26-forge-strategy-optimizer.md` (v0.2)
> **目标**: 定义 runner.py / evaluator.py / campaign.lock.json 的函数签名、数据结构、字段，使得实现者可以机械性编码

---

## 1. campaign.lock.json — 实验配置锁

```jsonc
{
  // 元数据
  "campaign_id": "helen_v2_btc_001",
  "created_at": "2026-03-26T00:00:00Z",
  "strategy_name": "helen",
  "base_infra_sha": "663ed22",           // git SHA，evaluator/adapter 代码版本

  // 数据源
  "symbol": "BTCUSDT",
  "interval": "4h",
  "data_dir": "../data/crypto",           // 相对 forge/ 的路径
  "data_snapshot_hash": "",               // 必须在创建 campaign 时由 `forge init` 填入，不可为 null

  // 时间窗口
  "warmup_start": "2017-08-17",
  "visible_windows": [
    {"name": "A", "start": "2019-01-01", "end": "2021-12-31"},
    {"name": "B", "start": "2020-01-01", "end": "2022-12-31"},
    {"name": "C", "start": "2021-01-01", "end": "2023-06-30"}
  ],
  "holdout_window": {"name": "holdout", "start": "2023-07-01", "end": "2026-03-26"},

  // 回测参数
  "transaction_cost_bps": 10.0,
  "rebalance_dead_zone_pct": 5.0,
  "days_per_year": 2190,                  // 365 × 6 (4H bars)

  // 门槛
  "gate_max_mdd": -0.55,                  // 每个 visible window 都必须 > 此值
  "gate_min_exposure": 0.20,              // 每个 visible window 都必须 > 此值

  // 棘轮
  "score_function": "min_excess_cagr",    // visible_score = min(window excess_cagr)

  // 停机规则
  "max_rounds": 50,
  "stale_stop_rounds": 20,               // 连续 N 轮无改进 → 停
  "structural_unlock_after_stale": 10,    // 参数面连续 N 轮无改进 → 解锁 Level 2
  "holdout_meltdown_threshold": -0.15,    // holdout excess_cagr 恶化超过此值 → 停

  // 参数面
  "parameter_surface_manifest": "manifests/helen_surface.yaml"
}
```

---

## 2. manifests/helen_surface.yaml — 参数面白名单

```yaml
# Helen v2.0 可调参数（Level 1 参数优化模式）
# agent 修改 candidate 时，Level 1 只允许改这些值

parameters:
  ema_period:
    type: int
    default: 144
    range: [50, 300]
    step: 10

  right_bear_slope_pct:
    type: float
    default: -0.03
    range: [-0.10, 0.0]
    step: 0.005

  right_neutral_slope_pct:
    type: float
    default: 0.0
    range: [-0.02, 0.05]
    step: 0.005

  right_trend_slope_pct:
    type: float
    default: 0.03
    range: [0.01, 0.20]
    step: 0.01

  pmarp_lookback:
    type: int
    default: 150
    range: [50, 300]
    step: 10

  bbwp_lookback:
    type: int
    default: 252
    range: [100, 500]
    step: 25

  rvol_window:
    type: int
    default: 20
    range: [10, 60]
    step: 5

  left_max_hold_bars:
    type: int
    default: 540
    range: [90, 1080]
    step: 90
```

---

## 3. evaluator.py — 评分裁判

### 3.1 公开接口

```python
"""
Forge evaluator — 不可改的评分裁判。

用法:
    python evaluator.py                        # 评估 candidate vs champion
    python evaluator.py --champion-only        # 只输出 champion baseline
    python evaluator.py --campaign lock.json   # 指定 campaign 文件
"""

import json
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class WindowResult:
    """单个时间窗口的回测结果"""
    name: str                # "A" / "B" / "C" / "holdout"
    start: str
    end: str
    cagr: float
    buyhold_cagr: float
    excess_cagr: float       # cagr - buyhold_cagr
    max_drawdown: float
    mean_exposure: float
    n_rebalances: int
    sharpe: float


@dataclass
class ForgeResult:
    """evaluator 的完整输出"""
    status: str              # "PASS" / "FAIL_GATE" / "FAIL_GUARD" / "ERROR"
    error_message: str       # 非空 when status=ERROR

    # Visible（agent 可见）
    visible_score: float     # min(window excess_cagr)
    visible_windows: List[WindowResult]

    # Holdout（agent 不可见，仅写入 private log）
    holdout: Optional[WindowResult]

    # 元数据
    strategy_hash: str       # candidate 文件的 SHA256
    data_hash: str           # 数据快照 hash
    infra_sha: str           # git SHA


def evaluate(
    campaign_path: Path,
    strategy_path: Path,
    params_path: Optional[Path] = None,
) -> ForgeResult:
    """
    核心评估函数。

    1. 加载 campaign.lock.json
    2. 加载数据（CryptoAdapter）
    3. 动态 import strategy_path 中的策略
    4. Level 1 如有 params_path，则用 JSON 覆盖构造 DualEngineConfig
    5. 对每个 visible window 跑回测
    6. 对 holdout window 跑回测
    7. 检查门槛
    8. 计算 visible_score
    9. 返回 ForgeResult
    """
    ...


def print_agent_result(result: ForgeResult, best_score: float) -> None:
    """
    打印 agent 可见的 stdout 输出。
    Holdout 字段打印 HIDDEN。
    """
    ...


def compute_data_hash(data_dir: Path, symbol: str, interval: str) -> str:
    """对数据文件计算 SHA256，用于 campaign.lock 的可复现性检查。"""
    ...
```

### 3.2 策略接口 Contract — 包裹现有接口，不发明新协议

> **Review 修复 #1**: 不引入 `evaluate_bar()` + dict state 新协议。
> candidate/champion 直接就是 `dual_engine.py` 的副本，保留原有的
> `DualEngineConfig` / `DualEngineState` / `evaluate_dual_engine_snapshot()`。
> evaluator 通过 `importlib` 从 candidate 文件 import 这些类，用法和
> `run_dual_engine_backtest()` 完全一致，确保 baseline 不漂移。

`strategies/helen_candidate.py` 的结构：

```python
"""
Helen 策略 — Forge candidate 副本。
直接复用 DualEngineConfig / DualEngineState / evaluate_dual_engine_snapshot 的接口。
Agent 可修改此文件中的任何逻辑。
evaluator 通过 importlib 加载此文件。
"""

# --- 直接从主仓 import 基础设施（指标计算、回测工具） ---
from src.indicators.bbwp import analyze_bbwp, calculate_bbwp
from src.indicators.pmarp import analyze_pmarp, calculate_pmarp
from src.indicators.rvol import analyze_rvol, calculate_rvol_series

# --- 以下为策略代码，agent 可以自由修改 ---
# （初始版本 = dual_engine.py 的完整副本）

@dataclass(frozen=True)
class DualEngineConfig:
    ema_period: int = 144
    # ... 与 src/timing/dual_engine.py 完全一致
    left_max_hold_bars: int = 540

@dataclass
class DualEngineState:
    # ... 与 src/timing/dual_engine.py 完全一致

def evaluate_dual_engine_snapshot(snapshot, state=None, config=None):
    # ... 与 src/timing/dual_engine.py 完全一致
    # agent 可以修改任何内部逻辑

def build_dual_engine_snapshot(df_4h, df_daily, config=None):
    # ... 与 src/timing/dual_engine.py 完全一致
```

**evaluator 的验证方式**：动态 import candidate，检查是否存在
`DualEngineConfig` / `DualEngineState` / `evaluate_dual_engine_snapshot` /
`build_dual_engine_snapshot` 四个名字。不检查签名细节——如果 import 成功
且回测能跑通，就是合格的 candidate。

### 3.3 Level 1 参数面闭环设计

> **Review 修复 #2**: Level 1 不改 Python 代码。agent 只修改一个 JSON 参数
> 覆盖文件，evaluator 用它构造 `DualEngineConfig`。

```
Level 1 流程:
  1. runner 复制 champion → candidate（Python 文件不变）
  2. runner 同时复制 champion_params.json → candidate_params.json
  3. agent 只修改 candidate_params.json（不碰 Python）
  4. evaluator: config = DualEngineConfig(**candidate_params)
  5. mutation guard: diff candidate.py vs champion.py == 0（字节一致）
                     diff candidate_params.json 只含白名单 key
```

`candidate_params.json` 示例：
```json
{
  "ema_period": 144,
  "right_bear_slope_pct": -0.03,
  "right_neutral_slope_pct": 0.0,
  "right_trend_slope_pct": 0.03,
  "pmarp_lookback": 150,
  "bbwp_lookback": 252,
  "rvol_window": 20,
  "left_max_hold_bars": 540
}
```

Level 1 mutation guard 变成纯 JSON diff：只有白名单 key 允许变化，value 在范围内。
不需要 AST 分析。

Level 2（结构进化）解锁后，agent 才可以改 `candidate.py` 本身。

**Hypothesis 来源统一**：
- Level 1: agent 在 stdout 首行输出 `HYPOTHESIS: ...`，runner 从 agent_output 提取
- Level 2: 仍以 stdout 的 `HYPOTHESIS:` 为主，candidate 文件头注释只作为冗余副本
- 不再依赖从 `candidate.py` 头部提取，因为 Level 1 的 Python 文件必须保持字节一致

### 3.4 评估流程伪代码

> **Review 修复 #4**: 直接调用 `run_dual_engine_backtest()`（现有公共函数），
> 用 `start_timestamp` 参数做窗口切片。不发明 `run_window_backtest` 等新 helper。

```python
def evaluate(campaign_path, strategy_path, params_path=None):
    campaign = load_campaign(campaign_path)

    # 1. 动态 import candidate 策略
    strategy_mod = importlib.import_module_from_path(strategy_path)
    ConfigClass = strategy_mod.DualEngineConfig
    StateClass = strategy_mod.DualEngineState

    # 2. 构造 config（Level 1: 从 params JSON 覆盖；Level 2: 用 candidate 内置默认值）
    if params_path and params_path.exists():
        params = json.loads(params_path.read_text())
        config = ConfigClass(**params)
    else:
        config = ConfigClass()

    # 3. 数据加载（复用主仓 CryptoAdapter）
    adapter_4h = CryptoAdapter(symbols=[campaign.symbol], interval="4h")
    adapter_1d = CryptoAdapter(symbols=[campaign.symbol], interval="1d")
    df_4h = adapter_4h.load_all()[campaign.symbol]
    df_1d = adapter_1d.load_all()[campaign.symbol]

    # 4. 对每个窗口，调用现有 run_dual_engine_backtest()
    #    全量数据都传入（含 warmup），用 start_timestamp 切窗口
    #    run_dual_engine_backtest 内部会用全量预热指标，只在 start 后计分
    all_windows = campaign.visible_windows + [campaign.holdout_window]
    window_results = {}

    for window in all_windows:
        # 每个窗口独立 state，保证窗口间不互相污染
        result = run_dual_engine_backtest(
            symbol=campaign.symbol,
            price_4h_df=df_4h,
            price_daily_df=df_1d,
            state=StateClass(),
            config=config,
            transaction_cost_bps=campaign.transaction_cost_bps,
            rebalance_dead_zone_pct=campaign.rebalance_dead_zone_pct,
            start_timestamp=window["start"],
        )
        # NOTE: run_dual_engine_backtest 的 start_timestamp 只控制起点，
        #       不控制终点。需要对 result 做 end-date 截断。
        #       这是 evaluator 唯一需要新增的 helper。
        trimmed = trim_result_to_end(result, window["end"])
        window_results[window["name"]] = to_window_result(trimmed, window)

    # 5. 分离 visible vs holdout
    visible = [window_results[w["name"]] for w in campaign.visible_windows]
    holdout = window_results.get("holdout")

    # 6. 门槛检查
    for wr in visible:
        if wr.max_drawdown < campaign.gate_max_mdd:
            return ForgeResult(status="FAIL_GATE", ...)
        if wr.mean_exposure < campaign.gate_min_exposure:
            return ForgeResult(status="FAIL_GATE", ...)

    # 7. 计算 visible_score
    visible_score = min(wr.excess_cagr for wr in visible)

    return ForgeResult(
        status="PASS",
        visible_score=visible_score,
        visible_windows=visible,
        holdout=holdout,
        strategy_hash=hash_file(strategy_path),
        data_hash=compute_data_hash(...),
        infra_sha=get_git_sha(),
    )
```

**唯一需要新增的 helper**：`trim_result_to_end(result, end_date)` —— 截断
NAV 序列到 end_date，重算 metrics。现有 `_trim_continuous_result()` 只做起点截断，
需要扩展支持终点截断。这是 Phase 1 的一个明确工作项。

---

## 4. runner.py — 控制面

### 4.1 公开接口

```python
"""
Forge runner — 控制面。负责循环、mutation guard、晋级、日志、停机。

用法:
    python runner.py --rounds 50 --strategy helen
    python runner.py --rounds 50 --strategy helen --campaign campaign.lock.json
"""

import argparse
import json
import subprocess
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class RoundResult:
    """单轮实验结果"""
    round_num: int
    hypothesis: str
    status: str                    # PASS / FAIL_GATE / FAIL_GUARD / ERROR
    visible_score: float
    best_visible_score: float
    accepted: bool
    strategy_hash: str
    # visible window details
    window_results: list           # List[WindowResult]
    # holdout（仅写入 private log）
    holdout_excess_cagr: Optional[float]
    holdout_mdd: Optional[float]


def run_campaign(
    strategy_name: str = "helen",
    campaign_path: Optional[Path] = None,
    max_rounds: int = 50,
) -> None:
    """
    主循环入口。

    每轮：
    1. 复制 champion → candidate
    2. 调用 claude -p（传入 forge.md + champion + public log）
    3. Mutation guard 检查
    4. 调用 evaluator
    5. 比较 visible_score
    6. Promote 或 discard
    7. 写日志
    8. 检查停机规则
    """
    ...


def _copy_champion_to_candidate(
    strategy_name: str,
    current_level: str,
) -> None:
    """复制 champion → candidate；Level 1 同时复制 champion_params → candidate_params。"""
    ...


def _invoke_agent(
    forge_md: str,
    champion_code: str,
    public_log_tail: str,
    best_score: float,
    current_level: str,           # "parameter" / "structural"
) -> str:
    """
    调用 claude -p，返回 agent 的输出。
    Agent 必须：
    1. 在 stdout 首行输出 `HYPOTHESIS: ...`
    2. Level 1 只修改 candidate_params.json
    3. Level 2 才允许修改 candidate.py
    """
    ...


def _mutation_guard(
    strategy_name: str,
    campaign: dict,
    current_level: str,
) -> tuple[bool, str]:
    """
    检查 candidate 的修改是否合规。

    检查项:
    - candidate 文件是否可 import（语法正确）
    - candidate 是否暴露 `DualEngineConfig` / `DualEngineState` / `evaluate_dual_engine_snapshot` / `build_dual_engine_snapshot`
    - 如果 Level 1（参数模式）：
      - candidate.py 必须与 champion.py 字节一致
      - candidate_params.json 只允许改白名单 key，且 value 在范围内
    - 如果 Level 2（结构模式）：
      - candidate.py 可以变化，但必须保留上述 4 个导出名字
      - candidate_params.json 必须与 champion_params.json 一致或直接删除，避免 Python 逻辑与 JSON 覆盖双重漂移
    - 没有修改 runner.py / evaluator.py / campaign.lock.json / private log

    Returns:
        (passed: bool, reason: str)
    """
    ...


def _extract_hypothesis(agent_output: str) -> str:
    """从 agent stdout 的 `HYPOTHESIS:` 首行提取本轮实验假设。"""
    ...


def _write_public_log(round_result: RoundResult, log_path: Path) -> None:
    """追加一行到 experiments_public.tsv（不含 holdout）。"""
    ...


def _write_private_log(
    round_result: RoundResult,
    campaign: dict,
    log_path: Path,
) -> None:
    """追加一行到 experiments_private.jsonl（含 holdout + hashes）。"""
    ...


def _promote_candidate(strategy_name: str) -> None:
    """candidate → champion（文件覆盖）。"""
    ...


def _discard_candidate(strategy_name: str) -> None:
    """删除 candidate 文件。"""
    ...


def _check_stop_rules(
    campaign: dict,
    round_num: int,
    stale_count: int,
    initial_holdout_baseline: float,
    current_holdout: float,
) -> tuple[bool, str]:
    """
    检查是否应停机。

    > Review 修复 #3: holdout baseline 用 campaign 初始 champion 的值，
    > 不随 promote 更新。理由：如果跟着 champion 更新，meltdown 检测
    > 会被"缓慢恶化"骗过（每次只差一点就不触发）。
    > 初始 baseline 是 Helen v2.0 人工验证过的版本，是绝对锚点。

    规则:
    - round_num >= max_rounds → 停
    - stale_count >= stale_stop_rounds → 停
    - current_holdout < initial_holdout_baseline + holdout_meltdown_threshold → 停

    Returns:
        (should_stop: bool, reason: str)
    """
    ...


def _determine_level(
    campaign: dict,
    stale_count: int,
    error_count: int,
) -> str:
    """
    判断当前应使用 Level 1（参数面）还是 Level 2（结构进化）。

    > Review 修复 #5: 只有 stale_count（有效但无改进）触发升级，
    > error_count（guard 失败/语法错误）不计入。
    > 错误率不是探索成熟度。

    如果 stale_count >= structural_unlock_after_stale → "structural"
    否则 → "parameter"
    """
    ...
```

### 4.2 主循环伪代码

```python
def run_campaign(strategy_name, campaign_path, max_rounds):
    campaign = load_campaign(campaign_path)
    forge_md = load_forge_md()
    champion_path = path_for(strategy_name, "champion.py")
    candidate_path = path_for(strategy_name, "candidate.py")
    champion_params_path = path_for(strategy_name, "champion_params.json")
    candidate_params_path = path_for(strategy_name, "candidate_params.json")
    public_log = path_for("logs", "experiments_public.tsv")

    # 初始化：评估 champion 作为 baseline
    champion_result = evaluate_champion(campaign, strategy_name)
    best_score = champion_result.visible_score
    # Review 修复 #3: 初始 baseline 是绝对锚点，不随 promote 更新
    initial_holdout_baseline = champion_result.holdout.excess_cagr

    # Review 修复 #5: 分离两种计数
    stale_count = 0   # 有效实验但无改进 → 触发 Level 2 升级 + stale 停机
    error_count = 0   # guard 失败/语法错误 → 不触发升级，连续过多则告警

    for round_num in range(1, max_rounds + 1):
        level = _determine_level(campaign, stale_count, error_count)

        # 1. 复制 champion → candidate（Level 1 同时复制 params.json）
        _copy_champion_to_candidate(strategy_name, level)

        # 2. 调用 agent
        public_log_tail = read_last_n_lines(public_log, 20)
        champion_code = read_file(champion_path)
        agent_output = _invoke_agent(forge_md, champion_code, public_log_tail, best_score, level)
        hypothesis = _extract_hypothesis(agent_output)

        # 3. Mutation guard
        passed, reason = _mutation_guard(strategy_name, campaign, level)
        if not passed:
            round_result = RoundResult(hypothesis=hypothesis, status="FAIL_GUARD", ...)
            _write_public_log(round_result, ...)
            _write_private_log(round_result, ...)
            _discard_candidate(strategy_name)
            error_count += 1  # 不计入 stale_count
            if error_count >= 5:
                print(f"WARNING: {error_count} consecutive guard failures")
            continue

        # 4. 评估 candidate
        params_path = candidate_params_path if level == "parameter" else None
        candidate_result = evaluate(campaign_path, candidate_path, params_path=params_path)
        if candidate_result.status == "ERROR":
            error_count += 1  # runtime error 也不计入 stale
            _discard_candidate(strategy_name)
            continue

        error_count = 0  # 成功评估，重置 error 计数

        # 5. 比较
        accepted = (
            candidate_result.status == "PASS"
            and candidate_result.visible_score > best_score
        )

        # 6. Promote 或 discard
        if accepted:
            _promote_candidate(strategy_name)
            best_score = candidate_result.visible_score
            stale_count = 0
        else:
            _discard_candidate(strategy_name)
            stale_count += 1

        # 7. 日志
        round_result = build_round_result(round_num, hypothesis, candidate_result, accepted)
        _write_public_log(round_result, ...)
        _write_private_log(round_result, ...)

        # 8. 停机检查
        should_stop, stop_reason = _check_stop_rules(
            campaign, round_num, stale_count,
            initial_holdout_baseline,  # 修复 #3: 不跟随 champion 更新
            candidate_result.holdout.excess_cagr if candidate_result.holdout else None,
        )
        if should_stop:
            print(f"FORGE STOPPED: {stop_reason}")
            break

    print_summary(best_score, round_num, public_log)
```

---

## 5. 实现顺序 Checklist

### Phase 1: 骨架 + 数据管道（可验证：champion 能跑通评估）

- [ ] 创建 `forge/` 目录结构
- [ ] 编写 `campaign.lock.json`（Helen BTC 配置）+ `forge init` 填入 data_snapshot_hash
- [ ] 编写 `manifests/helen_surface.yaml`
- [ ] 从 `src/timing/dual_engine.py` 完整复制为 `strategies/helen_champion.py`
  - **不改接口**，保留 DualEngineConfig / DualEngineState / evaluate_dual_engine_snapshot
  - 只改 import 路径（指标从主仓 import，策略逻辑自包含）
- [ ] 扩展 `_trim_continuous_result()` 支持 end-date 截断（唯一需要新增的 helper）
- [ ] 编写 `evaluator.py` 核心
  - importlib 动态加载 candidate/champion
  - 对每个窗口调用现有 `run_dual_engine_backtest(start_timestamp=...)` + end 截断
  - 门槛检查 + visible_score = min(excess_cagr) 计算
  - stdout 输出格式（holdout=HIDDEN）
- [ ] 验证：`python evaluator.py --champion-only` 输出 Helen v2.0 baseline
- [ ] **Baseline 一致性验证**：champion 的 visible window C 结果必须与 `run_dual_engine_btc_backtest.py --start-date 2021-01-01` 截至 2023-06-30 的结果完全一致

### Phase 2: runner 控制面（可验证：手动 1 轮循环跑通）

- [ ] 编写 `runner.py`
  - champion → candidate 复制
  - claude -p 调用
  - hypothesis 从 agent stdout 的 `HYPOTHESIS:` 提取
  - mutation guard（Level 1 = 纯 JSON diff；Level 2 = Python contract 检查）
  - promote / discard
  - public + private 日志
- [ ] 编写 `forge.md`（agent 指令）
- [ ] 验证：`python runner.py --rounds 1` 手动跑通 1 轮

### Phase 3: 停机规则 + Level 2 解锁（可验证：3-5 轮自动循环）

- [ ] 实现停机规则（max_rounds / stale / holdout meltdown）
- [ ] 实现 Level 1 → Level 2 升级逻辑
- [ ] 实现参数面 mutation guard（Level 1 只允许改白名单参数）
- [ ] 验证：`python runner.py --rounds 5` 自动跑 5 轮

### Phase 4: 端到端验证（可验证：跑 20-50 轮，检查 holdout）

- [ ] 跑 20 轮，检查 experiments_public.tsv 的 visible_score 趋势
- [ ] 检查 experiments_private.jsonl 的 holdout 是否恶化
- [ ] 对比 champion 和原始 Helen v2.0 的 holdout 表现
- [ ] 编写 `README.md`

---

## 6. 测试策略

| 层级 | 测试什么 | 方法 |
|------|---------|------|
| evaluator | 窗口切片正确性 | 对比 Window C (2021-01 → 2023-06) 与 `run_dual_engine_backtest --start-date 2021-01-01` 截至 2023-06-30，验证起点+终点都正确 |
| evaluator | 门槛判定 | 构造一个超过 MDD 门槛的策略，断言 FAIL_GATE |
| evaluator | visible_score 计算 | 手算 min(3 窗口 excess_cagr) 对比 |
| evaluator | Level 1 params 覆盖生效 | 修改 `candidate_params.json` 中单个参数，断言结果相对默认配置发生变化 |
| runner | mutation guard | 构造一个改了 evaluator.py 的 diff，断言 FAIL_GUARD |
| runner | hypothesis 提取 | mock agent stdout 为 `HYPOTHESIS: ...`，断言 public/private log 都写入相同假设 |
| runner | promote/discard | 跑 2 轮，1 接受 1 拒绝，检查文件状态 |
| runner | 停机 | 设 stale_stop_rounds=3，跑到自动停 |
| E2E | holdout 不泄漏 | 检查 public log 无 holdout 值，private log 有 |

---

## 变更记录

| 版本 | 日期 | 内容 |
|------|------|------|
| v0.1 | 2026-03-26 | 接口级实现计划，覆盖 campaign.lock / evaluator / runner / strategy contract / 实现顺序 |
| v0.2 | 2026-03-26 | Review 修复 5 个问题：(1) 策略 contract 改为包裹现有 DualEngineConfig/State/evaluate_dual_engine_snapshot，不发明新协议 (2) Level 1 参数面改为 JSON 覆盖 + 纯 JSON diff guard (3) holdout meltdown baseline 固定为初始 champion，不随 promote 漂移 (4) evaluator 直接调用 run_dual_engine_backtest + end-date 截断，不发明新 helper (5) stale_count/error_count 分离，错误率不计入探索成熟度 |
