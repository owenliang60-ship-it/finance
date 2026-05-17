---
issue_id: 027
title: 废弃 Concept Registry Phase 2 在生产 market.db 残留孤儿表 — FK 引用阻断 v2 rebuild_concept_tree
date: 2026-05-17
severity: medium
domain: concept-registry
status: resolved
resolved_date: 2026-05-17
---

## 现象

Task 13 落库（`--read-reviewed-csv --save`）在云端 `market.db` 上跑 `rebuild_concept_tree`
时 COMMIT 阶段抛：

```
sqlite3.IntegrityError: FOREIGN KEY constraint failed
  at src/data/market_store.py:1588  (with conn:)
```

WAL-safe backup 已在失败前生成，且整个 rebuild 在单事务内 → 自动 rollback，
market.db 未被改动。

## 根因

`rebuild_concept_tree`（`market_store.py:1568`）只认识 3 个 FK 引用 `concepts` 的表，
rebuild 前逐一处理：

- `concept_themes` → `UPDATE parent_concept_id = NULL`
- `company_concept_tags` → `DELETE`
- `symbol_concept_edges` → `DELETE`

然后 `DELETE FROM concepts` + 重新 INSERT 新 taxonomy（11/61/42）。`PRAGMA
defer_foreign_keys=ON` 把 FK 校验推迟到 COMMIT。

但云端 `market.db` 还有**第 4 个 FK 引用方** `company_concept_candidates`（7 行，
`primary/secondary/tertiary_concept_id → concepts.concept_id`）—— 这张表**不在
main 分支 schema 里**，是已放弃的 Concept Registry Phase 2 在 2026-05-04 cloud
smoke 时写进生产库的残留（连同 `company_concept_evidence` 11 行、`concept_override_log`
0 行）。`.claude/ongoing.md` 当时已标注「云端数据残留……如需完全回滚需单独清理 DB 行」。

rebuild 删光 `concepts` 再插入新集合后，那 7 行 candidate 引用的旧 concept_id 不复
存在 → COMMIT 时 FK 校验失败。

## 影响

- Task 13 落库被硬阻断（exit 非 0），但因单事务 + 失败前 backup，无数据损坏。
- 任何对 `concepts` 做整树重建的操作，只要生产库 schema 比 main 多出带 FK→concepts
  的表，都会复现。

## 解决（2026-05-17）

- 先 dump `company_concept_candidates`（7 行）+ `company_concept_evidence`（11 行）
  到 `reports/concept_registry/abandoned_phase2_db_residue_2026-05-17.json` 存档。
- `DELETE FROM company_concept_candidates`（FK 依赖图确认：无任何表 FK 引用它，
  零连环风险）。`evidence` / `override_log` 无 FK，不阻断，保留。
- 重跑 `--save` → 545 行落库成功，`concepts` 114 行（11/61/42）。

## 教训

- **放弃一个已部署到生产的 plan，只 revert 代码不够** —— DDL 改动会让生产 DB schema
  与 main 分支永久分叉。废弃流程必须包含「清理已写入生产库的表/行」这一步，否则
  残留会埋雷给后续无关功能。
- 「整树重建」类例程若**硬编码已知 FK 引用方清单**，会对 schema 漂移的库脆弱。
  防御性写法：rebuild 前用 `PRAGMA foreign_key_list` 动态枚举所有引用 `concepts`
  的表，或在 `defer_foreign_keys` 下显式校验后给出可读错误（指明是哪张表挡的），
  而不是抛裸 `FOREIGN KEY constraint failed`。
- 单事务 + 变更前 WAL backup 这套组合让这次失败「零损伤」—— 是正确的护栏，值得保留。
