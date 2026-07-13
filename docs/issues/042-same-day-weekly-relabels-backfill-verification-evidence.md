# Issue 042: 同日 weekly 重标使 backfill manifest 无法事后重复验证

**Status**: 已接受的 Phase 1 schema 限制；运行手册已补证据顺序
**Date**: 2026-07-13
**Severity**: MEDIUM — 无数据丢失，但会破坏同日 backfill verifier 的事后可重复性
**Related**: `fmp_estimates` PK / `scripts/verify_fmp_forward.py` / Spec §5.2

## 触发

2026-07-13 production backfill 完成后，独立 verifier PASS：`1,009/1,074 = 93.95%`。随后按冻结 Spec 执行同日 weekly smoke；weekly verifier 同样 PASS，但再次运行 backfill verifier 会得到 `0/1,074`。

## 根因

`fmp_estimates` 主键不含 `snapshot_kind`。同一 `snapshot_date` 的 weekly 会用 `INSERT OR REPLACE` 覆盖重叠未来行，并把 17,592 行从 `backfill` 重标为 `weekly`。这是 Spec 明确规定的“同日先 backfill、后 weekly，更新者覆盖”语义，不是实现偏离。

最终总行数仍是 42,575：weekly 17,592 + 仅历史 backfill 24,983，数据没有丢失；丢失的是“用当前业务表重新证明当时 backfill 4Q coverage”的能力。

## 当前处理

- backfill verifier 必须在任何同日 weekly 之前运行并把结果写入不可变 rollout audit。
- 同日 weekly 后不得把 backfill verifier FAIL 解释为 backfill 当时失败；应同时核对 manifest、当时 verifier 证据和最终 kind 分解。
- 正常未来运行不受影响：backfill 是一次性动作，后续 weekly 使用不同日期。
- 本轮不修改冻结 schema；若未来要求所有历史 manifest 永久可重放，需要单独架构决策（例如把 kind/run id 纳入版本键或保存独立 run-result evidence）。

## 教训

“数据幂等覆盖”与“历史验收可重放”不是同一件事。只要业务键允许后续任务重标，验收证据就必须在重标前固化，或把 run 维度纳入存储模型。
