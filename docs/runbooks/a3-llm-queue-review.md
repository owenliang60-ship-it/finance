# Runbook: A3 LLM-Queue Review-Apply (anti-clobber)

> 周频 concept sync（`--weekly-sync`）每周自动把**确定性票**（`source ∈ {manual, rule}`，含 anchor）增量落库到 `company_concept_tags`，把 **LLM / 分类失败票**写入审核队列 `reports/concept_registry/needs_review_<date>.csv` 并 Telegram 通知。本 runbook 描述 Boss 人工 review 队列后**如何安全 apply 回 DB，不 clobber cron 已自动落的行**。

**Related**: `docs/plans/2026-06-01-a3-weekly-concept-sync-design.md`（D2/D3/§5）· `docs/plans/2026-06-01-a3-weekly-concept-sync-plan.md`（Task 2/3）· issue 030 · issue 031

---

## 背景：为什么会 clobber

`--read-reviewed-csv <csv> --save` 是 **wipe + rebuild** 语义——它按 CSV 重建 `company_concept_tags`。

- 队列 CSV（`needs_review_<date>.csv`）**只含本周待审的 LLM/失败票**，不含 cron 已自动落的确定性行，也不含历史已审票。
- 如果直接 `--read-reviewed-csv needs_review_<date>.csv --save`，wipe+rebuild 会用这个**部分集**重建整张表 → 清掉 cron 自动落的确定性行和所有历史已审行。

**防 clobber 铁律**：apply 前必须把 review 过的队列行**合并进 canonical `reviewed_current.csv`**（全集快照），再对 **canonical CSV** 跑 `--save`。永远不要对队列 CSV 单独 `--save`。

---

## Review-Apply 流程

### 1. 每周自动产出（cron，无需人工）

`weekly_refresh`（Sat）末尾的非阻塞 step 7（`--weekly-sync`）：

- 确定性票（`{manual, rule}`，含 anchor）→ 增量 upsert `company_concept_tags` + append 进 canonical `reviewed_current.csv`（CSV ⇔ DB lockstep，preflight/postflight 自检）。
- LLM / 分类失败票 → 写 `reports/concept_registry/needs_review_<date>.csv`。
- 不论成败 → Telegram 群组单行摘要（D2）。
- churn-out（离开 universe 的老票）→ **KEEP，不删 tags**（D3）。

### 2. Boss 人工 review 队列 CSV

打开 `reports/concept_registry/needs_review_<date>.csv`，对每行填/改 `l1` / `l2` / `l3_themes`（写 **label**，不是 concept_id），按需调 `business_role` / `confidence` / `boss_notes`，把 `needs_review` 改 `0`。

> 队列 CSV 与 canonical CSV 同为 16 字段 schema（`REVIEW_CSV_FIELDS`）；只编辑、不增删列。

### 3. 合并进 canonical（防 clobber 关键步骤）

把 review 完成的队列行**合并进 canonical `reviewed_current.csv`**（同 symbol 覆盖、新 symbol 追加），保证 canonical 仍是**全集快照**（= 当前 DB 应有的全部 tags）。

- canonical CSV 是 weekly-sync 自己维护的 lockstep 快照（与 `company_concept_tags` 的 symbol 集逐周对齐）。
- 合并后 canonical 应包含：历史已审行 + cron 自动落的确定性行 + 本周 review 完成的队列行。
- **不要**用队列 CSV 替换 canonical；是把队列行**并进** canonical。

### 4. 云端 apply（market.db 云端独占写）

`company.db` / `market.db` 的 concept tags 由**云端独占写入**（P3 所有权模型）。apply 必须在云端跑，对 **canonical CSV**（不是队列 CSV）：

```bash
ssh aliyun
cd /root/workspace/Finance
python3 scripts/build_company_concept_registry.py \
  --read-reviewed-csv reports/concept_registry/reviewed_current.csv --save
```

> 跑 `--save` 前可先 `--read-reviewed-csv reports/concept_registry/reviewed_current.csv`（不带 `--save`）做 validate-only，确认 10 项 fail-fast 检查 + coverage 全过再落。

### 5. WAL-safe backup + pull 回本地

- `--save` 路径内已含 WAL-safe backup（`_backup_sqlite`，`src.backup(dst)`）。
- 回本地：`./sync_to_cloud.sh --pull`（含 A3 canonical CSV + manifest pull-only，Task 7）。

### 6. 偶尔提交 canonical 快照（走"push 先确认"）

需要把 canonical 快照入 git 时：

```bash
git add reports/concept_registry/reviewed_current.csv
git commit -m "data(concept): apply <date> reviewed LLM queue"
```

> 任何 push/merge 必须先经 Boss 确认（feedback: 合并/推送必须先确认）。

---

## 检查清单

- [ ] review 的是队列 CSV（`needs_review_<date>.csv`），不是直接改 DB。
- [ ] apply 前已把队列行**合并进 canonical `reviewed_current.csv`**（全集）。
- [ ] `--save` 的对象是 **canonical CSV**，不是队列 CSV（否则 wipe+rebuild clobber）。
- [ ] 在**云端**跑（market.db 云端独占写）。
- [ ] apply 后 `sync_to_cloud.sh --pull` 回本地。
- [ ] canonical CSV 的 symbol 集 == `company_concept_tags` symbol 集（lockstep 自检）。
