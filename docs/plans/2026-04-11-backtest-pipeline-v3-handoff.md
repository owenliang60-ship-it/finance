# Backtest Pipeline V3 — 下次会话交接文档

> **作者**: Claude (factor research strategist 角色)
> **交接时间**: 2026-04-11 收尾
> **用途**: 下次会话开工不用重读今天的超长对话，直接按这份文档执行
> **当前阶段**: V3 已在 worktree 分支 committed，等待 merge + 数据底座 backfill + parquet 清理

---

## 30 秒速览

今天（2026-04-11）完成了一条新的回测流水线 **V3 Backtest Pipeline**。目标：**用一条 spec 驱动的 6 步流水线替代现有分散的 `run_rs_backtest.py` / `run_factor_study.py` 入口**，专注美股横截面单因子 + 2-3 因子组合验证，守住 PIT + IS/OOS 先切后拟合 + 统一报告四条纪律，明确**不做** timing / event-driven / holdout ledger / runs DB / 自动 verdict。

代码已在 worktree `codex/backtest-pipeline-v3` 分支 committed（commit `123e897`），28 pipeline 测试 + 168 legacy 回归全部通过。

**现在堵着的**：`historical_market_cap` 表本地云端都 0 行，sample spec 在真数据上跑会被 `UniverseBuildError` 精确中止（这本身是 V3 4 条覆盖规则的正确防线表现，不是 bug）。要看真实回测结果必须先 backfill。

**明天要做**：`A1 merge → A2 backfill → A3 parquet cleanup`，详见下面第 3 节。

---

## 1. 当前状态（事实）

### 1.1 Worktree + 分支

| 项 | 值 |
|---|---|
| Worktree 路径 | `/Users/owen/CC workspace/Finance/.worktrees/backtest-pipeline-v3` |
| 分支名 | `codex/backtest-pipeline-v3` |
| HEAD commit | `123e897 feat(backtest/pipeline): V3 focused factor validation pipeline` |
| 父 commit | `7a56270 feat(telegram): split signals into private/group channels + PDF delivery` |
| 与 main 的关系 | main 之后 1 个 commit |
| Working tree | 基本干净，但有一个 untracked debris 目录 `reports/backtest/pipeline_rs_rating_b_b6d7552328926a0b/`（Codex 实施期早期跑的测试输出，不影响任何东西，可 `rm -rf` 也可 leave）|

### 1.2 代码位置

```
backtest/pipeline/              # 新主流水线
├── spec.py                     # StrategySpec + 7 个子 spec class + 轻量 YAML 解析器
├── runner.py                   # PipelineRunner 6 步 orchestration
├── paths.py                    # 跨 worktree 定位 data/ 目录
├── types.py                    # dataclass 结果类型
├── report.py                   # Markdown + HTML 报告生成
├── primitives/                 # 共享底层
│   ├── pit_data.py             # as-of 数据访问（price + social mentions）
│   ├── universe_builder.py     # PIT reconstitution + 4 条覆盖规则
│   ├── signal_engine.py        # 横截面 per-date rank_pct/zscore + combo
│   ├── portfolio_builder.py    # top_n/threshold + equal/inv_vol + cap-respecting
│   ├── execution.py            # next_open + forward-fill NAV + simple cost
│   └── evaluation.py           # IC + Newey-West t-stat + decile spread + gates
└── factors/                    # factor registry
    ├── _base.py                # PipelineFactor ABC
    ├── registry.py
    ├── rs_rating_b.py          # RS_Rating_B adapter → src.indicators.rs_rating
    ├── pmarp.py                # PMARP adapter → src.indicators.pmarp
    └── attention_zscore.py     # Attention_ZScore adapter → src.indicators.social_attention

backtest/specs/                 # 样例 spec
├── pipeline_rs_rating_b.yaml          # 单因子样例
└── pipeline_three_factor_combo.yaml   # RS + PMARP + Attention 三因子组合

scripts/run_pipeline.py         # 唯一 CLI 入口

tests/pipeline/                 # 新测试（28 个）
├── test_spec.py                # 5 测试
├── test_pit_data.py            # 3 测试
├── test_universe_builder.py    # 5 测试（覆盖 4 条覆盖规则）
├── test_signal_engine.py       # 3 测试
├── test_portfolio_builder.py   # 3 测试（含 cap saturation 回归）
├── test_execution.py           # 2 测试（含 forward-fill NAV 回归）
├── test_evaluation.py          # 3 测试（含负 IS Sharpe gate 回归）
├── test_runner.py              # 2 测试（spec_hash + slice_frame 边界）
├── test_e2e_pipeline.py        # 2 测试（single + three factor E2E）
└── helpers.py                  # seed_pipeline_dbs 共享 fixture
```

### 1.3 测试基线

```
tests/pipeline                  28 passed
tests/test_backtest             88 passed
tests/test_factor_study         80 passed
=====================================
total                          196 passed (5 pre-existing rs_rating.py warnings)
```

运行命令：
```bash
cd "/Users/owen/CC workspace/Finance/.worktrees/backtest-pipeline-v3"
"/Users/owen/CC workspace/Finance/.venv/bin/python" -m pytest \
  tests/pipeline tests/test_backtest tests/test_factor_study -q
```

### 1.4 相关文档

| 文件 | 作用 |
|---|---|
| `docs/plans/2026-04-11-backtest-pipeline-design-v3.md` | Codex 写的 V3 执行方案，是 V3 实施的真蓝图 |
| `docs/plans/2026-04-11-backtest-pipeline-design.md` | 我原来写的工程架构版（V1→V2 迭代），在 main 上是 untracked，**保留作对比但 V3 实施参考的是上面那份** |
| `docs/plans/2026-04-11-backtest-pipeline-v3-handoff.md` | **本文档** |

---

## 2. V3 的 scope 与纪律（给未来记忆用，避免重开讨论）

### 2.1 V3 做了什么

- 美股横截面单因子验证（1 个 factor）
- 美股横截面 2-3 因子组合验证（composition: single / weighted_sum / rank_average）
- 横截面 per-date rank_pct 或 zscore 变换（**不**支持任何时间序列拟合的 transform）
- 固定 IS/OOS 切分（`period.start → train_end → test_end`）
- OOS **从 fresh capital 独立跑**（不继承 IS 持仓，已文档化在 `runner.py` docstring 和 `report.py` header）
- `next_open` T+1 成交 + 简单成本（commission + spread，linear）
- Forward-filled last known close 用于 NAV（避免 missing price 导致 NAV 假 spike）
- PIT universe 重建（based on `market.db.historical_market_cap`），4 条覆盖规则：
  - 头部无数据 → 顺延 effective_start + warning
  - 中间断裂 → abort
  - 单次 min_names 不满足 → skip + warning
  - skip 比例 > 10% → abort
- 统一产物目录 `reports/backtest/{spec_id}_{spec_hash}/`
- 报告首页展示 5 个 gate（`is_sharpe_positive` / `oos_sharpe_positive` / `oos_ic_positive` / `oos_vs_is_sharpe_ratio_gte_0_5` / `annual_turnover_within_limit`），**不输出自动 SUPPORT/REJECT verdict**，Boss 看数字自己判断
- gate `oos_vs_is_sharpe_ratio_gte_0_5` 对负 IS Sharpe 有正确 fallback：`is_sharpe <= 0` 时只要 `oos_sharpe > 0` 就算 pass

### 2.2 V3 明确不做（V1/V2 要的这些都砍了）

- time_series 策略（例：BTC dual engine）
- event_driven 策略
- holdout ledger + 3-use cap（solo quant 纪律用一次性 period 切分替代）
- SQLite `backtest_runs.db` 血缘归档
- Forge → Pipeline 自动桥
- AST 静态审计因子代码（靠 code review 纪律）
- 自动 SUPPORT / PAPER_TRADE / REJECT 机器裁决
- 批量迁移 legacy `backtest/engine.py` / `factor_study/` / `timing/` 模块

这些都在 V3 design doc 里有理由说明，**不要在下一轮对话里重开讨论**。如果真要重开，先读设计文档 section "Alternatives Considered" 和 "Scope"。

### 2.3 今天已经定好不要再争的决策清单

| 决策 | 结论 | 为什么 |
|---|---|---|
| V3 覆盖三种策略族？ | **只做 US 横截面** | 覆盖 3 家族让抽象层太厚，Boss 场景 80% 是横截面 |
| 同 spec 反跑警告？ | **不加** | solo quant 靠 period 固定切分做物理约束 |
| `same_close` 执行？ | **砍** | 日线同收盘是隐性 lookahead |
| factor-first vs strategy-first 毕业？ | **strategy-level spec** | Boss 的产品偏好 "扔一个因子和策略描述，你就开始测" |
| OOS 继承 IS 持仓？ | **fresh capital** | 纯 OOS 能力测试，不被建仓历史污染；已文档化在报告 |
| 自动 verdict 门槛？ | **不输出 verdict，只展示 gates** | 阈值对不同因子族不可比，Boss 看数字判断更靠谱 |
| legacy 模块怎么处理？ | **不动** | V3 独立存在，legacy 继续跑，六个月后再谈收口 |
| AST 静态审计？ | **不做** | 代码审查纪律足够，Boss 自己写因子 |

---

## 3. 明天开工三件事

按优先级顺序执行。每一件都可以独立完成，不用等前一件做完。

### ✅ A1 — Merge V3 分支到 main

**为什么先做**：让 V3 进入主干，后面 backfill + smoke test + 实际使用 pipeline 都基于 merged state。也让其他 agent 看 main 时能看到这份代码。

**两种方式**：

#### 方式 A1-PR（推荐，谨慎路线）

让 Codex 或 human 再做一轮 review 再 merge。

```bash
cd "/Users/owen/CC workspace/Finance/.worktrees/backtest-pipeline-v3"
git push -u origin codex/backtest-pipeline-v3
gh pr create --title "feat(backtest/pipeline): V3 focused factor validation pipeline" --body "$(cat <<'EOF'
## Summary
- Introduce V3 focused factor validation pipeline for US equity cross-sectional single/multi-factor research
- Single-entry CLI `scripts/run_pipeline.py <spec.yaml>` replacing the scattered run_rs_backtest.py / run_factor_study.py path
- 6-step runner: spec → IS/OOS split → PIT universe → signals+combo → portfolio → next_open execution → evaluation → unified report
- Legacy backtest/engine.py, factor_study/, timing/ preserved unchanged

## Scope
- US equity only, cross-sectional factors (1–3 per spec)
- Composition: single / weighted_sum / rank_average
- Per-rebalance rank_pct / zscore transforms (no stateful fit)
- Fixed IS/OOS split, OOS runs from fresh capital
- Fixed next_open execution + simple commission + spread cost
- Report surfaces 5 gates; no auto verdict

## Out of scope (deferred to V4+)
- timing / event-driven strategies
- holdout ledger, runs DB, Forge auto-bridge
- automated machine verdict
- mass migration of legacy modules

## Test plan
- [x] 28 new tests under tests/pipeline/
- [x] Legacy tests/test_backtest + tests/test_factor_study: 168 passing, no regression
- [ ] Real-data smoke test (blocked on historical_market_cap backfill — see handoff doc)

## Known caveats
- `.venv` lacks pyarrow/fastparquet → runner writes JSON sidecar + `.parquet` placeholder stub (see handoff doc A3 for cleanup)
- `historical_market_cap` table must be backfilled via `scripts/fetch_historical_mcap.py` before sample specs can run on real data
- `src/indicators/rs_rating.py:128` RuntimeWarning is pre-existing upstream NaN cast, not V3-introduced

## Docs
- Design: docs/plans/2026-04-11-backtest-pipeline-design-v3.md
- Handoff / next steps: docs/plans/2026-04-11-backtest-pipeline-v3-handoff.md

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

注意 `gh pr create` 前要 push branch。

#### 方式 A1-direct（快速路线）

Boss 已经自己看过这轮 review + fix，直接 merge 到 main 也合理。

```bash
cd "/Users/owen/CC workspace/Finance"
# 先检查 main 有没有冲突文件（别的会话可能改过 backtest/engine.py 或 factor_study/）
git fetch
git status
# 如果干净，merge
git merge --no-ff codex/backtest-pipeline-v3 -m "merge: V3 focused factor validation pipeline"
# 或者 fast-forward（因为 V3 分支是 main 线性后继）
# git merge --ff-only codex/backtest-pipeline-v3
```

Merge 后必须跑：
```bash
"/Users/owen/CC workspace/Finance/.venv/bin/python" -m pytest \
  tests/pipeline tests/test_backtest tests/test_factor_study -q
# expected: 196 passed
```

**Landmines**：
- 不要 amend 现有 commit `123e897`（commit message 已经很详细，改动会让 worktree 和 main 脱轨）
- **如果 main 上有并行会话改过 `backtest/engine.py` 或 `backtest/factor_study/` 的文件**：冲突概率很低因为 V3 完全新路径没动 legacy，但还是要在 merge 前 `git diff main..HEAD -- backtest/` 看一眼
- worktree 自己当前有 `reports/backtest/pipeline_rs_rating_b_b6d7552328926a0b/` untracked，merge 不会带上，可忽略或手动 `rm -rf`

### 📦 A2 — Backfill `historical_market_cap` 到云端 market.db

**为什么必须做**：V3 sample spec 不能在真数据上跑。`historical_market_cap` 表本地 + 云端 **都是 0 行**。

**ongoing.md 顶部**说"历史市值 533 symbols × 5 年日频 (646K rows, FMP)" —— 这是 2026-04-08 session 59 的快照，**不代表当前现实**。我已经在 MEMORY.md 里把对应那条更正过了，但 ongoing.md header 是时间戳快照，保留不改。

#### ⚠️ 与 Broad Universe Plan v3 的重叠（重要）

`ongoing.md` 活跃任务里有另一条 **"Broad Universe 历史数据底座 Plan — v3 待写"**（路径 `docs/plans/2026-04-10-broad-universe-historical-data-backfill.md`），那条任务的目的就是**为 PMARP 广扫验证构建 historical_market_cap 数据底座**，用 yfinance screener $3B+ 作 seed，目标数据规模比我这里的 533 只更大。

两条任务用**同一张表** `historical_market_cap`，**同一个脚本** `scripts/fetch_historical_mcap.py`。`--skip-existing` 参数保证 idempotent，两条不会互相覆盖。但 Boss 要在两种路径里做选择：

**路径 X（快速解锁 V3 smoke test）**
- 先跑 A2 小范围 backfill（533 只 pool + 扩展池，~20 分钟 FMP 时间）
- V3 sample spec 立即可在真数据上跑
- Broad Universe Plan v3 以后写出来再执行时，`--skip-existing` 自动扩展到更大 universe
- **优势**：明天就能看到 V3 真数据报告
- **代价**：多一次脚本运行（但增量的，数据会被复用）

**路径 Y（合并到 Broad Universe Plan v3 一次性做完）**
- 跳过 A2 这条单独任务
- 等 Broad Universe Plan v3 写完 + Boss review 通过 + 执行
- 那条 Plan 执行完会顺带把 V3 sample spec 的依赖也补齐
- **优势**：一次性做对，rigorous；避免 533 只小范围先跑
- **代价**：V3 真数据 smoke test 要等 Broad Universe Plan v3 落地（至少多一个会话的 plan writing + review + execution）

**我的倾向**：**路径 X**。理由：
1. V3 sample spec 跟 PMARP 广扫是两个独立目的 — 前者验证 pipeline 本身、后者验证因子有效性
2. 533 只的快速 backfill 不会对 Broad Universe Plan v3 的严谨性造成任何污染（`--skip-existing` 保证）
3. V3 pipeline 合并到 main 后要尽快做一次真数据跑通来消除 "设计-现实" 的最后一公里不确定性
4. Broad Universe Plan v3 本身还是 "v3 待写" 状态，落地至少要几天

但这是 Boss 拍板，不是我定。**如果 Boss 选路径 Y，那 A2 这一节就跳过，V3 真数据 smoke test 合并到 Broad Universe Plan v3 执行阶段后自然完成。**

**关键约束**：
- market.db **云端独占写入**（P3 所有权模型）
- 绝不在本地直接跑 `fetch_historical_mcap.py` 写本地 market.db ——下一次 `sync_to_cloud.sh --pull` 会把本地版本覆盖掉
- **必须走 `ssh aliyun`** 在云端跑脚本，然后本地 pull

**步骤**：

```bash
# 1. 先看一眼脚本接收什么参数
cat "/Users/owen/CC workspace/Finance/scripts/fetch_historical_mcap.py" | head -40

# 2. 云端确认 pre-state
ssh aliyun "cd /root/workspace/Finance && python3 -c \"
import sqlite3
c = sqlite3.connect('data/market.db')
r = c.execute('SELECT COUNT(*), MIN(date), MAX(date) FROM historical_market_cap').fetchone()
print('pre-backfill:', r)
\""

# 3. 云端跑 backfill（默认 5 年 + 扩展池 + skip-existing）
# 注意：FMP 2 秒/调用限流，533 symbols × 5 年 weekly 很可能要 10-30 分钟
# 建议用 nohup 后台跑，避免 ssh 断线中断
ssh aliyun "cd /root/workspace/Finance && nohup python3 scripts/fetch_historical_mcap.py --years 5 --skip-existing > logs/fetch_mcap_$(date +%Y%m%d).log 2>&1 &"

# 4. 监控进度
ssh aliyun "tail -f /root/workspace/Finance/logs/fetch_mcap_$(date +%Y%m%d).log"

# 5. 跑完后云端验证
ssh aliyun "cd /root/workspace/Finance && python3 -c \"
import sqlite3
c = sqlite3.connect('data/market.db')
r = c.execute('SELECT COUNT(*), MIN(date), MAX(date) FROM historical_market_cap').fetchone()
print('post-backfill:', r)
cnt = c.execute('SELECT COUNT(DISTINCT symbol) FROM historical_market_cap WHERE date=\\\"2021-01-04\\\" AND market_cap >= 100000000000').fetchone()[0]
print('2021-01-04 >= 100B candidates:', cnt)
\""

# 6. 云端 push company.db 无关（company.db 是本地 own），只 pull market.db
./sync_to_cloud.sh --pull

# 7. 本地验证
"/Users/owen/CC workspace/Finance/.venv/bin/python" -c "
import sqlite3
c = sqlite3.connect('/Users/owen/CC workspace/Finance/data/market.db')
r = c.execute('SELECT COUNT(*), MIN(date), MAX(date) FROM historical_market_cap').fetchone()
print('local after pull:', r)
"

# 8. 真 smoke test！切到 worktree 跑 V3 sample spec
cd "/Users/owen/CC workspace/Finance/.worktrees/backtest-pipeline-v3"
"/Users/owen/CC workspace/Finance/.venv/bin/python" scripts/run_pipeline.py \
  backtest/specs/pipeline_rs_rating_b.yaml

# 9. 看产物
ls reports/backtest/pipeline_rs_rating_b_*/
cat reports/backtest/pipeline_rs_rating_b_*/report.md
```

**如果 A1 已经 merge 了**：第 8 步可以直接在主 worktree 跑，不用 cd 到 codex/ worktree。

**预期结果**：
- 531 只股票 × 5 年 weekly = ~138K rows
- report.md 首页有 Gates 5 条，全部基于真数据
- `pipeline_rs_rating_b` spec 首次有机会产生有意义的数字（RS top-20 weekly rebalance 在 2021-2024 IS + 2025 OOS 上的表现）

**Landmines**：
- `scripts/fetch_historical_mcap.py` 用的是 FMP stable endpoint（`/stable/historical-market-capitalization?symbol=XXX`），不是 legacy v3 path——memory 里有这条踩坑记录
- 别在本地跑 backfill 写本地 DB —— 会被覆盖
- `ssh aliyun` 断线中断脚本 → 用 `nohup` + `&` 后台跑，或者用 `screen`/`tmux`
- 若云端磁盘空间紧张，先 `df -h` 看看
- FMP rate limit 2 秒，脚本应该自己等；不要并发跑

### 🧹 A3 — 装 pyarrow + 清理 parquet fallback

**为什么最后做**：纯 housekeeping。pipeline 正确性 0 影响，但产物目录里的 `.parquet` 是 text stub 假文件，下游任何 `pd.read_parquet(path)` 调用都会崩。装了 pyarrow 之后，assertion 立刻变成"`.parquet` 就是真 parquet"，清爽很多。

**步骤**：

```bash
# 1. 装 pyarrow 到主项目 .venv
"/Users/owen/CC workspace/Finance/.venv/bin/pip" install pyarrow

# 2. 修改 runner.py:_write_frame_artifact，删掉 fallback 分支
# 当前代码 runner.py:67-85 是 try/except 结构，改成直接 frame.to_parquet(path)

# 3. 跑 pipeline tests 确认 .parquet 是真的
cd "/Users/owen/CC workspace/Finance"  # 或 worktree
"/Users/owen/CC workspace/Finance/.venv/bin/python" -m pytest tests/pipeline -q

# 4. 跑 sample spec，确认产物目录里 signals_is.parquet 是真 parquet
cd "/Users/owen/CC workspace/Finance/.worktrees/backtest-pipeline-v3"  # 或 main
"/Users/owen/CC workspace/Finance/.venv/bin/python" scripts/run_pipeline.py \
  backtest/specs/pipeline_rs_rating_b.yaml

"/Users/owen/CC workspace/Finance/.venv/bin/python" -c "
import pandas as pd
df = pd.read_parquet('reports/backtest/pipeline_rs_rating_b_*/signals_is.parquet')
print(df.head())
print('shape:', df.shape)
"

# 5. Commit 清理
cd "/Users/owen/CC workspace/Finance"  # merge 后主项目，或还在 worktree
git add backtest/pipeline/runner.py
git commit -m "chore(backtest/pipeline): remove parquet fallback, pyarrow now installed

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

**可选深化**：把 `pyarrow` 固定到 `requirements.txt` 或 `pyproject.toml` 里，避免新 clone 环境缺包。这要看 Finance 项目用什么 dependency 管理 —— 不确定的话先不加。

---

## 4. 待 Boss 拍板的决策

| # | 问题 | 我的倾向 | 备注 |
|---|---|---|---|
| 1 | A1 用 PR 还是直接 merge？ | 直接 merge | Boss 今天已经完整过了 review + fix，再开 PR 让 Codex 看一轮会拉长链路。但走 PR 也合理（留审计痕迹） |
| 2 | A2 路径 X (快速 533 只 backfill) vs 路径 Y (等 Broad Universe Plan v3)？ | **路径 X** | V3 sample spec 跟 PMARP 广扫是独立目的；`--skip-existing` 防 overlap；V3 真数据 smoke 不应该 block 到 Broad Universe Plan v3 落地。详见 §3-A2 子章 "与 Broad Universe Plan v3 的重叠" |
| 3 | A3 parquet cleanup 何时做？ | A2 之后任何时候 | 不影响 A1/A2，独立任务 |
| 4 | PMARP 广扫验证走 V3 pipeline 还是 factor_study？ | **暂时走 factor_study**（那是 PMARP 验证的现有工具链），**V3 稳定后再考虑迁移** | 避免在一件事情上同时变两个变量 |
| 5 | 要不要把 V3 pipeline 写进 ARCHITECTURE.md 和 MEMORY.md 的 LIVE 层清单？ | A1 merge 后再写 | merge 前它还在分支上，写 "LIVE" 会误导其他会话 |

---

## 5. 关键踩坑 / landmines（跨会话都要知道）

1. **Worktree 没自己的 `.venv`** — 任何 Python 命令用主项目的绝对路径 `/Users/owen/CC workspace/Finance/.venv/bin/python`（已知陷阱，memory 里有）
2. **market.db 云端独占写入** — 本地读 + pull，所有 backfill/update 必须走 `ssh aliyun`。违反这条的操作会被下次 sync 覆盖
3. **`historical_market_cap` 当前是 0 行** — 本地云端都空。memory 里 "646K rows" 已修正
4. **parquet fallback 写假 `.parquet` 文件** — 当前 .venv 没 pyarrow，runner 会把 text stub 写成 `.parquet`，真数据在同目录 `.json`。`pd.read_parquet()` 读产物会崩。A3 之前避开直接读 parquet
5. **`src/indicators/rs_rating.py:128` RuntimeWarning** — pre-existing upstream NaN cast bug，V3 测试跑起来会打 5 条 warning 但这不是 V3 引入的。归档到 docs/issues/ 待以后修
6. **`reports/backtest/pipeline_rs_rating_b_b6d7552328926a0b/`** — worktree 里的 untracked debris，Codex 实施期跑的一次早期 smoke test 留下的，不影响 commit，可 `rm -rf` 清理
7. **V3 sample spec 不能直接在本地 smoke test 真数据** — 因为 A2 没做，会被 4 条覆盖规则的 abort 拦下。想看真报告格式只能用 `tests/pipeline/helpers.py:seed_pipeline_dbs` 合成数据（我今天就是这么做的，输出在 `/tmp/v3_smoke_*` 已清理）
8. **3 个 factor adapter 走的是真实 `src.indicators.*` 模块** — RS_Rating_B 用 `src/indicators/rs_rating.py:compute_rs_rating_b`、PMARP 用 `src/indicators/pmarp.py:analyze_pmarp`、Attention_ZScore 用 `src/indicators/social_attention.py:attention_zscore`。这些都是真实生产代码，不是测试桩

---

## 6. 最短恢复路径

下次会话如果 Boss 说 "继续 V3"，按这个顺序读：

**10 分钟足够**：
1. 读本文档第 0 节（30 秒速览）+ 第 3 节（A1/A2/A3 怎么做）
2. `cd .worktrees/backtest-pipeline-v3 && git log -1 && git show --stat HEAD` 看今天的 commit
3. 读 `backtest/pipeline/spec.py` 前 60 行（StrategySpec schema）

**30 分钟深度**：
1. 上面三步
2. 读 `docs/plans/2026-04-11-backtest-pipeline-design-v3.md` 完整 design
3. 读 `backtest/pipeline/runner.py`（6 步 orchestration）
4. 读任一个 primitive（比如 `universe_builder.py` 的 4 条覆盖规则）
5. 读本文档第 5 节（landmines）

---

## 7. Changelog

| 日期 | 作者 | 变更 |
|---|---|---|
| 2026-04-11 | Claude | 初稿 — V3 commit 后的交接文档 |

**下次更新触发**：
- A1 merge 完成 → 本文档第 1.1 节更新 commit hash + 从"活跃"改为"已合并"
- A2 backfill 完成 → 第 3 节加一条"real smoke test 结果"并 reference 产物 path
- A3 parquet 清理完成 → 第 5 节删掉第 4 条踩坑
- 任一步骤发现新问题 → 写新的 changelog entry
