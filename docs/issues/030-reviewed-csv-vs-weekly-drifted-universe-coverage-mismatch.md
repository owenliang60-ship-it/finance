# Issue 030: Reviewed CSV vs Weekly-Drifted Universe Coverage Mismatch

**Status**: Worked-around (二次发生)；结构性解决待 A3 — **A3 已实现自动双向对账（coverage self-check），pin 手段退役**（`docs/plans/2026-06-01-a3-weekly-concept-sync-plan.md` Task 2/3，`weekly_sync` drift-in/churn-out 双向 + preflight/postflight lockstep）
**Date**: 2026-05-30 (首次 2026-05-17)
**Severity**: P2 流程摩擦（fails-safe，不污染数据）
**Related**: docs/plans/2026-05-24-concept-registry-422-new-symbols-relodge.md (Step 9) + docs/plans/2026-05-16 new24 落库

## 触发
concept registry 手动落库（`build_company_concept_registry --read-reviewed-csv --save`）时，reviewed CSV 的 symbol 集是在**算 delta 那一刻**冻结的（本次 = 本地 955-universe 派生的 967），但云端 `extended_universe.json` 被**周频刷新 cron（Sat 09:00）独立更新**。两者之间存在时间差 → 云端 universe 漂移领先于 reviewed CSV。

- 2026-05-17: reviewed CSV(5/15) vs 周频刷新(5/16) 差 **24** 新票
- 2026-05-30: reviewed CSV(基于本地 955) vs 云端漂移到 **964** 差 **19** 新票（AG/AVAV/BNY/BROS/DOCU/ESI/HBM/IAG/ICLR/LGN/LINE/LUMN/MAIR/MGM/OC/RIOT/SAIL/UHAL/VMI）

## 根因
`scripts/build_company_concept_registry.py` 的 coverage 检查：
```
extend_pool = _effective_extend_pool(csv_path, _load_universe(--extended-universe-path))
            = _load_universe(path) ∪ manifest.full_universe      # line 1164
missing = extend_pool - seen_symbols(CSV)                          # line 787, 单向
```
- coverage 只查 `universe − CSV` **单向**（防"漏审 universe 成员"），不查反向（CSV 多出 churn-out 不报错）。
- 当云端 universe 比 CSV **新**（drift-in），`missing` = 那些 universe 有、CSV 无的新票 → `raise CSVValidationError` → exit 2 → `--save` 中止（**fails-safe，不写脏数据**）。
- `manifest ∪` 双保险只能救 churn-out（CSV ⊃ universe），救不了 drift-in（universe ⊃ CSV）。

## 影响面
- 手动落库被 coverage 阻断，无法 `--save`。
- drift-in 的新票若不处理，会一直走晨报 legacy fallback 单桶分类（非 registry 三段），但不影响已审票。

## Workaround（5/17 + 5/30 同款）
把 `--extended-universe-path` 指向 **reviewed CSV 自己的 manifest pinned 集**（而非漂移的云端 universe），使 `extend_pool = pinned ∪ manifest = CSV 集`，coverage = 0 通过：
```bash
python3 -c "import json; m=json.load(open('reports/concept_registry/<csv>_manifest.json')); json.dump(m['full_universe'], open('/tmp/pinned_universe.json','w'))"
python3 -m scripts.build_company_concept_registry \
  --read-reviewed-csv reports/concept_registry/<csv>.csv \
  --extended-universe-path /tmp/pinned_universe.json --save
```
落已审集，drift-in 新票 **deferred**（记入 ongoing → A3 首跑目标）。

## 教训
1. **reviewed CSV 是时间点快照，universe 是活动态**——任何手动落库前必须对账两者差集（双向），不能假设 CSV == 当前 universe。
2. coverage 单向检查 fails-safe 但会卡死手动落库；pinned-universe 是绕过手段不是修复。
3. **结构性解法 = A3 周频 concept-build cron**：在 `weekly_refresh` 末尾 `--reclassify`，让 registry 跟着 universe 一起换血，drift-in / churn-out 双向自动处理，消灭手动落库与对账。这是 A3 必须设计的核心能力（不是简单追加一行）。
4. 云端 `extended_universe.json` 不走 git（rsync/cron 写），所以 cloud 与 local 的 universe 可能不同步——落库时以**落库目标端（云端）**的 universe 为准对账。
