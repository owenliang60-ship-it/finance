# M4 Codex 审查修复验收（2026-09-29）

审查文档：`docs/handoffs/2026-09-29-prosperity-m4-codex-review.md`（1 P1 + 5 P2，基于 `bb201762`）。Boss 2026-09-29 确认按推荐修复（F1 用完整序列识别拆股断点；F2 冲突只剔除单季）。

快照：`data/backups/prosperity/phase0-accepted-input-20260929/market.db`，SHA256 前 16 位 `3caffdb7e7c2d2d9`（与审查一致）。

## 逐条修复与证据

| # | 修复 | 反例测试 | 真实快照结果 |
|---|---|---|---|
| F1 P1 拆股单位 | 拆股断点在库内完整 EPS 序列上判定，再按 as_of 截取；证据用到 as_of 之后的季度时加挂 `eps_split_retrospective` | `test_replay_before_three_post_break_quarters_uses_the_full_stored_series`、`test_unconfirmed_break_after_as_of_still_blanks_the_visible_quarters`、`test_replayed_price_and_ttm_eps_share_the_post_split_unit`（修复前复现 18.753） | KLAC TTM：2024-09-30 **2.376**（原 23.76）、2024-12-31 **2.535**（原 18.753）、2025-03-31 **2.739**（原 13.413）、2025-06-30 3.053（不变）；PE 24.8–32.6；前三期挂 retrospective，2025-06-30 不挂；泄漏均为空 |
| F2 P2 strict 整包清空 | strict 读取 `fundamental_current_archive` 中删除日期类修复（`equivalent_fiscal_alias`、`reviewed_fiscal_repair`），从 archived_at 起不再选被删日期；残余对齐冲突只剔除该季（记入 `archive.statement_conflicts`）；同一财季两个 income 日期不再存活为两季 | `test_strict_replay_drops_dates_removed_by_a_recorded_repair`、`test_repair_takes_effect_only_from_when_it_was_recorded`、`test_unrepaired_duplicate_in_strict_replay_drops_only_that_quarter`、`test_alignment_conflict_drops_only_the_conflicting_quarters`、`test_duplicate_fiscal_quarter_in_income_alone_is_not_a_second_quarter`、`test_load_history_reads_date_removing_repairs_as_tombstones` | strict 9/29：空包 **0**（原 5）；AEM/BIP/MDLN/P/SNA 当前财季与季度日期和 live 完全一致；冲突标记 0 |
| F3 P2 时间戳 | vintage 观测时间解析为 UTC instant 比较；每季带 `observed_on`，`packet_leaks` 同时检查 | `test_strict_bound_compares_instants_not_strings`、`test_newest_strict_version_is_chosen_by_instant_across_formats`、`test_quarters_carry_the_observation_date_that_proves_them`、`test_leak_check_reads_the_observation_date_not_only_the_public_date` | strict 9/29：`observed_on` > as_of 的季度 0 |
| F4 P2 本周已报 | 改用已对齐当前财季的 EPS 公告日；M4 plan Task 8 描述同步更正 | `test_reported_this_week_follows_the_aligned_results_announcement` | live 9/29：22 → **6**，翻转的恰为审查列出的 16 只（AMX、DCI 等） |
| F5 P2 无 vintage 回退 | strict 起点后无 vintage 返回空并挂 `strict_vintage_missing` | `test_strict_replay_without_vintage_is_missing_not_current_tables` | 915 只 strict 均有 vintage，未触发 |
| F6 P2 拆股比例 inf | 质量层资格判断抽为 `split_ratio_eligible`，价格层直接调用 | `test_non_finite_split_metadata_is_ignored_not_fatal`、资格参数表加 inf 两例 | D9 当前报告修复前后逐字段相同（除 `code_sha`、`generated_at`） |

## 回归

- live 9/29 与原 M4 验收（`reports/prosperity/m4-acceptance-20260929/live-0929`）逐只比对：915 只中唯一变化是 `reported_this_week` 16 只 True→False；其余字段全部相同（新增字段 `observed_on`、`statement_conflicts` 除外）。
- `tests/test_fundamental_value_checks.py tests/test_prosperity_*.py`：177 passed（基线 160）。
- 全量 `tests/`：4555 passed、12 failed；12 个失败（`test_breadth_buy_quality` 7、`test_morning_report` 5）在 `bb201762` 的 worktree 上同样失败，属于 worktree 数据环境问题，非本分支引入。

## 已知限制（未修，非本次引入）

- ASML strict 只有 8 季：vintage 只从 2024-09-30 起，`bb201762` 同样如此。strict 回放下 SUE 不可算，属于数据深度问题。
- 删除日期类修复原因用白名单识别；将来新增会删日期的修复原因时需要加入 `DATE_REMOVING_REPAIRS`，否则由逐季冲突隔离兜底。

## `/code-review high`（2026-09-29）

10 条意见，逐条对照代码核实后修 4 条，另 6 条不改（理由见下）。修完后 live、strict 9/29 输出与上面的验收逐字节相同，KLAC 四期不变；景气相关测试 181 passed。

修复：
- 拆股断点按 ±20 天匹配季度，不再按日期字符串精确比较（完整历史与 as_of 两次分组可能用同一季的不同别名日期，曾把断点季误除 10）。
- as_of 可见序列自己识别到的断点也采用（as_of 之后的冲突重复行会让完整历史漏掉断点）。
- 资产负债表 / 现金流某行财季身份非法时，只剔除该日期对应的季度，不再让所有季度对齐失败。
- `statement_alignment_conflict` 与 `archive.statement_conflicts` 只报告原本会落在 16 季窗口内的冲突，不再因多年前的冲突标记健康股票。

不改：
- 最新季冲突时当前财季回退到上一季：与"已发业绩但报表未入库"的正常滞后状态相同，有冲突标记，`data_age_days` 与 `stale_current_fiscal` 会反映；这正是 Boss 批准的"只剔除单季"。
- 剔除中间季后季度序列有空洞：空洞本来就可能存在（供应商缺季），`period_days` 如实反映，M6 公式按 60–120 天相邻性检查。
- `reported_this_week` 在报表未入库时为 False：Boss 批准的"已对齐当前财季的公告日"口径，保证标记与包内报表一致。
- 无时区的 observed_at 直接报错：写入端约定带 UTC 偏移，现有数据全部符合；报错会进入 errors 并使 CLI 退出码为 3，比猜时区更安全。
- `MarketStore.known_as_of` 仍按字符串比较、不读修复记录：属于共享数据层，不在 M4 修复范围，另列待办。
- 每次调用都重算完整历史的拆股审计：目前单次 live/strict 全池约十几秒，等 M8/M9 多期回放时再按需缓存。
