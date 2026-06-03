# Issue 033: Worktree 里 universe managers 不走 shared-data-root，静默返回 0 样本

**Status**: 执行 momentum plan 建 worktree 时发现；**已用 `data/pool` symlink 兜底**（worktree 内）；production 模块的根治 deferred
**Date**: 2026-06-02
**Severity**: HIGH 静默失效 —— worktree 内 `--universe extended/pool` 返回 0 只股票、不报错，回测/研究在空池上跑出垃圾结论
**Related**: issue 019（market.db 同类问题，已修）· `src/data/extended_universe_manager.py:33-34` · `src/data/pool_manager.py` · `backtest/pipeline/paths.py:resolve_shared_data_root`

## 触发

在独立 worktree 跑 `USStocksAdapter(universe='extended')`，`get_extended_symbols()` 返回 **0**（pool 同样为空）。无报错。

## 根因

issue 019 修过 **market.db** 的 worktree 解析（`resolve_shared_data_root()` 会回退主仓库），但 **universe JSON 没享受同一修复**：

```python
# src/data/extended_universe_manager.py:33-34
_PROJECT_ROOT = Path(__file__).parent.parent.parent      # = worktree 根
EXTENDED_UNIVERSE_FILE = _PROJECT_ROOT / "data" / "pool" / "extended_universe.json"
```

`data/*` 被 `.gitignore` 挡着不进 worktree checkout → 文件不存在 → `get_extended_symbols()` 返回 `[]`（无 FMP key 时也不会 refresh）。`pool_manager.get_symbols()` 同病（`data/pool/universe.json`）。

issue 019 的教训"worktree 下文件存在≠数据可用"只落实到了 market.db；**路径解析的 worktree 感知没有推广到 universe 层**。

## 兜底（本次）

worktree 内 symlink（gitignored，不进 commit）：
```bash
ln -s "$MAIN/data/pool" "$WT/data/pool"
```
验证：extended 955 / pool 181（原 0/0）。

## Durable 修复（deferred）

让 `extended_universe_manager` / `pool_manager` 的文件路径也走 `resolve_shared_data_root()`（与 `_get_market_store` 一致），而不是 `Path(__file__)...`。一处统一，worktree 自动可用，无需 symlink。

## 教训

1. issue 019 的修复**没推广**：worktree-aware 的 data-root 解析必须覆盖**所有** data/ 依赖（market.db ✓ / universe JSON ✗ / 其它 data/ 文件？），不能只补打中的那一个。
2. 与 issue 019/032 同源的"静默 0 样本/IS 冒充 OOS"家族：研究类代码的失败模式是**不报错、看着像没样本**，最危险。worktree 执行前必须 smoke 验证池子非空。
3. plan 的 Task 0（worktree setup）应显式包含"symlink data/pool + 验证池非空"，否则后续 `--universe` 任务在空池上跑。
