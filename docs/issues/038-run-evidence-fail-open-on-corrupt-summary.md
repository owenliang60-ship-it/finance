# Issue 038: Run evidence 解码降级为空状态会让 partial resume 错误完成

**Status**: 已修复（FMP forward EPS review round 9）
**Date**: 2026-07-13
**Severity**: HIGH — 数据采集失败证据可被静默清空，后续单票 resume 可能把整批错误标成 complete
**Related**: `scripts/update_fmp_forward.py` · `scripts/verify_fmp_forward.py` · `src/data/fmp_forward_ingestion.py`

## 触发

整批 earnings 失败后，若 manifest `summary_json` 畸形，或 attempt 状态写入失败且异常 finalizer 的二次读库也失败，旧实现会把 run-wide unresolved 集降级为 `{}` / 空列表。之后只 resume 一只成功标的，其余失败票被遗忘，writer 与 verifier 都可能放行。

## 根因

`summary_json` 实际是完成裁决的证据 SSOT，却被当成可选日志：

1. resume 对 JSON decode error 使用 `{}` fallback；
2. verifier 只验 JSON 语法，不验 `run_state` / symbol lists / attempts schema；
3. failure finalizer 依赖异常后的二次读库，读不到时写空骨架，并且没有合并本轮内存 attempt。

## 修复

- `parse_forward_run_evidence()` 成为 resume/verifier 共用的严格 schema validator；缺失、坏 JSON、错类型、空/重复 symbol 均 fail closed。
- normal finalize 与 failure finalizer 共用 `_build_run_evidence()`，从函数入口已验证的历史 evidence 与本轮内存 summary 确定性合并。
- 异常 finalizer 不再二次读库；写失败后仍保存历史 unresolved、本轮 success/failure、attempt 与 error。
- 回归覆盖首次 full-run 写失败、partial-resume 写+读失败、`{}`/`[]`/错类型 evidence，以及 verifier 结构化失败。

## 教训

凡是参与 gate 的持久化 JSON 都是 schema，不是日志。解析失败不能补默认值；异常收尾不能依赖故障点之后的再次读取，必须在进入副作用区前捕获可信状态，并与内存中的最新事实合并。
