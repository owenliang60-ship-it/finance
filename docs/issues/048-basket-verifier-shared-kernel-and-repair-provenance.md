# Issue 048: Basket verifier 共享计算内核且无法独立证明历史 repair

**Status**: OPEN — 已校准定位并补强篡改检测；recurring pipeline 需要 immutable manifest
**Date**: 2026-07-14
**Severity**: MEDIUM — 可发现 source/output 漂移，但不能作为独立方法学实现
**Related**: `scripts/verify_basket_ttm_pe.py` · `mcap_sanity_json`

## 现象

verifier 使用 SQLite `mode=ro + query_only` 从 source tables 重算每日结果，并核对成员证据、coverage、methodology version 与 raw HMC sanity；但它复用了 producer 的季度选择、市值选择、权重合并和 sanity 内核。因此正确定位是：

> 只读 source recomputation + persisted-evidence/tamper detector

而不是独立的方法学 oracle。producer 与 verifier 若共享同一个纯函数 bug，可能同错同过。

另外，forced range refresh 会替换旧 HMC 行。post-refresh source tables 无法重建 pre-refresh 异常或证明当时确实发起过 repair。`pre_refresh_status`、`refresh_windows` 与 attempt/success 字段属于 producer-side provenance。

## 本轮补强

- base evidence 只比较 post-refresh raw tables 可复现的 candidate/invalid 事件，避免 clean padding 假红；
- repair-only evidence 单独检查窗口包含关系、attempt/success 布尔关系和 status-derived quarantine；
- verifier 现核对 `member_count` 与 allowlisted `methodology_version=1.0`；
- query 不再信任 producer 的 `quarantined` 布尔值，只从最终 status 推导 gap。

## Recurring pipeline 关闭条件

- 写入 append-only run manifest，保留 pre/post refresh row hash、窗口、响应状态与 run id；
- verifier 在单一数据库快照或资源锁下运行，避免活跃 writer 导致跨阶段读到不同时间点；
- 对关键公式增加异构/独立实现的抽样 reconciliation，而不是复制整套 producer；
- 明确 expected start/end manifest，避免用户给较晚 `--min-date` 只验证 suffix。

## 2026-09-11：三指数周频 verifier 四项 P1 修复（C1 前停止）

原分支遗留四个未提交修复，本次接手并补全。修复记录见
`docs/audit/2026-09-11-index-pe-pre-c1-repair.md`。

- 旧空表显式迁移到 `run_id NOT NULL`，并清理列缓存；旧表已有行则拒绝自动迁移，保留原数据。
- hindsight 重建所需证据全行检查，估计季度要求成员自己的 snapshot date；按篮子检查抽样是否实际完成重建，不能用另一篮子的成功抵消零对账。
- manifest 起点必须存在且为物理首事件、终态必须为物理末事件；旧异常 run 不能被后续合法 run 洗白。
- 成员分母从具体 `(holding_date, source_kind)` 快照独立重建，估值日双日期门控后再选择最新快照；同一 effective date 的 live/disclosure 不可相加。成员集合、逐成员权重和快照归属必须匹配。

补充踩坑：首次成员对账补丁按 effective date 聚合，导致未来尚不可用快照也进入历史分母；60%/40% 可被加成 120%/80%，正确数据反而拒绝。根因是“生效日期”被误当成“快照唯一标识”。本次用多快照正反测试覆盖。

本 issue 的整体状态仍为 OPEN：C1 滑窗整批写入与窗口外旧行处理尚未实现；同库原始数据和证据同时被改写也不在 verifier 的独立证明能力内。本轮不表示云端真实覆盖率已验收。

### 同日续做：C1 已实现（上段停点记录已被推进）

后续展示契约：Boss 已确认共识口径显式标注。hindsight 是事后解释，不是历史可交易 forward 信号，共识尾部不能参加 actual-only 分位。reader 先验证完整已拥有行的 manifest hash，再截取五年窗口，避免截断后重算 hash 的假失败。短周频线段的虚线相位必须跨线段延续，否则每段不足 7px 时“虚线”会实际画成实线。HTML 成功投递后摘要失败不得再发送 PDF。对应回归在 chart 与 morning 测试中。

Boss 随后授权继续，整窗写入/旧行清理/completed 现为同一事务，候选内只读认证成功才提交；五年滑动一周、同周取样日变更和失败保留旧数据/重跑恢复通过。源预检失败也保留明确 started+failed 形状，避免 terminal-only 记录毒化所有后续重跑。C1 已关闭；Task 5 的共识盈利是否可称为 GAAP 存在两文档冲突，已向 Boss 提出选择，云端 PIT 表仍为空。详 `docs/plans/2026-09-11-index-pe-c1-continuation.md`。

### 同日复审：迁移阻断范围与失败收尾（已修复，未部署）

- 原 populated legacy table 守卫从 `MarketStore.__init__` 抛错，影响所有 writer-mode 使用方。改为保留旧行并告警，只在周频 PE 的两个窄写入入口拒绝缺少 run_id 的 schema；backfill 在 source/API 之前预检。空表迁移与只读 verifier 的拒绝逻辑保持不变。
- 原错误路径向故障 store 写 started/failed，第二个异常会替换原异常并丢失报告。现先绑定 partial report，再 best-effort 写失败事件；次生失败另记 `manifest_persist_error` 和日志，裸 raise 保持原异常对象及 traceback。
- 故障注入覆盖 preflight started、preflight failed、正常 started 写入及整窗提交四处故障；额外验证 CLI 所用汇总报告保留诊断并可 JSON 序列化。旧行完全保留、价格/forward 写入仍可用、PE 两入口拒写、预检零 API 请求均有测试。
- 残余边界：存储确实不可写时无法保证落下失败终态；现不掩盖该情况，也不补造证据。后续仍须按 runbook 人工核对异常 run。
