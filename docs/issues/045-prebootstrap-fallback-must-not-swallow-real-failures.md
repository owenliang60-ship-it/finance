# Issue 045: Pre-bootstrap 兼容 fallback 吞掉真实故障会冻结错误分母

**Status**: 已修复（Extended Primary Universe Stop A self-review fix wave）
**Date**: 2026-08-20
**Severity**: HIGH — 数据库损坏可被误报成 cold start，writer 继续运行并自证错误分母
**Related**: `src/data/universe_resolver.py` · `scripts/update_data.py` · `scripts/update_fmp_forward.py` · price fetchers

## 触发

`current_base_universe()` 抛 `sqlite3.DatabaseError("database disk image malformed")` 时，价格线回退成 Core + 空 yfinance；FMP forward writer 回退成旧 Core + Extended JSON，并把该错误分母冻结进 manifest。两条路径都只留 warning/error log，继续执行。

## 根因

rollout 需要兼容 Stop B→C 的短暂 cold-start，于是调用方写了 `except Exception: legacy_fallback`。但“允许降级的两个确定状态”和“所有未知异常”被混成了一类；错误分母随后又由同一 manifest verifier 验证，形成 self-certification。

## 修复

- `is_prebootstrap_universe_error()` 只识别 membership/SM 为空的两个冻结错误。
- price/forward producer 仅在明确 cold-start 时回退；数据库损坏、overlay 失败和未知 RuntimeError 全部冒泡。
- pre-bootstrap combined price targets 保留 Core FMP + Extended−Core yfinance，而不是空 complement。
- 回归故障注入覆盖 DatabaseError、overlay failure 和 cold-start 三类。

## 教训

兼容降级必须基于可枚举状态或异常类型，不得基于 broad catch；参与 denominator/manifest 的 producer 尤其必须 fail closed。
