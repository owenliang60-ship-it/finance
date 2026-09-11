# 三指数 PE 整窗更新与失败恢复

状态：C1、Task 5 PIT 聚合及晨报图表接线已实现，尚未部署；真实历史数据准备与最终验收未完成。云端仍为 market.db 唯一写入方，正式执行继续沿用 `market_db_writer` 外层资源锁与备份策略。

## 写入契约

1. 正常运行在逐股请求前持久化 run_started，冻结窗口和 expected_weeks。
2. 计算整个五年窗口；空批、漏周、跨篮子、错误 run_id/版本/日期范围均拒绝。
3. BEGIN IMMEDIATE 内复用原 R5 校验写入全部候选，清理旧窗口以及被替代的周内日期，追加唯一 run_completed。
4. 同一候选事务临时开启 query_only，以现有 verifier、固定 sample=50 验证；只有明确通过才提交。
5. 失败时数据、清理和 completed 同时回滚；外部只读连接一直看到上次提交的数据。随后尽力追加 run_failed（rows_written=false），命令非零退出。若收尾写入也失败，保留原始异常和 partial report，另记 manifest_persist_error 并输出错误日志。

一个 basket 是一次事务，其他 basket 保持独立。actual_only 不得降级为 tail/unpublishable，同周改取样日也不能绕过该检查。新窗口不得回退到已完成窗口之前；新方法学必须显式迁移。

旧表迁移边界：缺少 run_id 的空周频表可自动重建；有数据的旧表保留原样，Store 构造仅告警，不影响价格、forward ingestion 等其他读写。周频 PE 的两个写入入口拒绝旧 schema，backfill 在 source/API 调用前做同一预检；旧行如何归档和重新回填须显式处理，不猜测 run_id。

## 恢复步骤

- 先查看 backfill 的 JSON 结果：`failed_baskets`、各篮子 `error` 和 `verification.checks`。run manifest 中 run_failed 是明确失败状态，不代表旧产品已被替换。
- 修复导致失败的源数据/配置后，以**新 run_id**重跑完整五年窗口；不做 tail-only 补写，不修改旧 completed，不恢复整库覆盖同期其他数据。
- 新完整 run 通过验收后取代旧产品并恢复发布。测试覆盖“成功 → 验收失败 → 保留旧产品 → 新 run 成功”。
- 若在 universe/calendar 冻结前失败，尽力记录 `run_started(preflight_failed=true, expected_weeks=[])` + `run_failed`；该失败 run 不认证任何数据。若数据库本身不可写，JSON 中的 `error` 仍是原始失败，`manifest_persist_error` 记录收尾写入失败，`manifest` 仅包含确认写成功的事件。依靠 CLI 非零退出/外层告警，不伪造完成记录；若只落下 started 而没有终态，须先人工核对该异常 run，不能假定新 run 自动消除旧异常。
- 存在历史（C1 前）异常事件、跨版本数据或真实盈利从 actual_only 退级时，先人工归因，不能用重命名 run_id 或改 hash 绕过验收。

## 只读验收命令（部署及数据准备完成后）

周频顺序为 yfinance → FMP ingestion → 历史源/整窗刷新并认证 → PIT 共识估值 → 两个 verifier。history 可能修正 HMC/FX，须在 PIT 冻结前完成。NTM 未过双门则六篮子批次回滚；blend 辅助线可为 NULL/partial。PIT snapshot 只读既有 complete weekly 源，估值失败不要对已 complete 的 ingestion 执行 resume。

晨报估值路径只读两张估值表和完成记录，不触网、不写估值表。`0d. 三指数估值` 位于 `0b` 后、PMARP 前；同一 PNG 自包含嵌入 HTML 或作为 PDF 页面。任一序列最后有效点超过 14 天标过期；缺失数据/绘图失败展示说明，不阻断其余晨报。HTML 已成功发送后，摘要失败不再触发重复 PDF 发送。

```bash
python -m scripts.verify_index_pe_history --baskets SPY,QQQ,SOXX \
  --years 5 --as-of YYYY-MM-DD --sample 50 --mode ro --db data/market.db
```

as-of 必须是正在认证的运行窗口，不要照搬示例日期。该命令只读；数据回填须在已批准的预算/写锁/备份范围内执行。

## C1 实测证据

合成五年窗口：首跑 as-of=2026-01-16（首周 2021-01-22），次跑推进至 2026-01-23（首周 2021-01-29）。旧首周清理、新末周加入、当前行全归属第二个 run，内外两个 verifier 均通过。覆盖其他篮子不受影响、同周取样日变更、R5 降级、缺周、终态写失败、清理失败、验收返回失败/抛异常与失败后恢复。该证据不代表生产历史覆盖率已通过。
