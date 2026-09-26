# 三指数 PE 整窗更新与失败恢复

## 2026-09-26 已部署并恢复：精确源纠错

最终功能版本 `28b5761d`。SPY/QQQ各261周、SOXX253周均恢复到9/25；六篮子
PIT NTM更新至9/26。两套verifier与SQLite检查通过，正式恢复累计1,119/1,200请求。
SOX/IGV辅助blend线仍为NULL/partial，源数据警告保留。尚未观察后续自然cron。
恢复中还补充了经发行人/官方汇率证据核实的KRW校验范围，以及XLF精确
`IXAZ6 / XAF FINANCIAL DEC26`期货分类；不同合约换月仍需审核，不能按负权重跳过。

`security_source_corrections.json` 为两条已审核的9/25–9/26证券输入定义有限纠错：
SPY `2602335D/436CVR021` 按CVR衍生品排除普通股PE；SOXX
`0EDE.L/NL0009538784` 的有效CUSIP为 `N6596X109`。原始CUSIP、JSON、行和
权重均保留。配置包含精确名称、篮子、来源、日期及实际核验的证据SHA；9/26
物理快照内匹配行须唯一。不同证券/日期/未知冲突不适用，新增季度输入仍需审核。

纠错只在运行时视图应用，禁止将 `reviewed_cvr` 分类写回物理源；producer和
独立verifier都拒绝这种标记。共识估值旧holdings表没有标识字段，必须与同日
完整raw源唯一对应后才允许排除，并有独立PIT见证/产物核对。旧daily PE产品
不在本次修复范围。

恢复顺序仍为历史源/产品 → 六篮子PIT → 两个verifier。9/26 ingestion已complete，
不要重新采集或resume。初次在线预检SPY/SOXX各8次请求均通过身份门后在请求预算处
停止；这不是估值通过。当前缓存估计SPY787、SOXX59次请求，另加源目录查询；
共享源修订后QQQ也须重新认证，若其旧结果不再与源一致再重算。正式恢复前持共享
writer锁、检查磁盘空间、一致性备份并明确总请求硬上限。状态与证据见
`docs/audit/2026-09-26-forward-source-corrections.md`。

状态：2026-09-12已合并、push、部署，生产功能代码b352520；当日窗口SPY/QQQ各261周、SOXX251周双线，10期60条PIT全部通过。原始9/11试跑证据保留不改，最终生产认证与备份见 `docs/audit/2026-09-12-index-pe-rollout.md`。云端仍为market.db唯一写入方，继续沿用`market_db_writer`外层资源锁与备份。实测SPY零HTTP整窗组装约46分钟，不能沿用Phase1原时延描述。首次新wrapper自然运行及自然晨报投递尚未验收，未创建自动跟进。

## 写入契约

1. 正常运行在逐股请求前持久化 run_started，冻结窗口和 expected_weeks。
2. 计算整个五年窗口；空批、漏周、跨篮子、错误 run_id/版本/日期范围均拒绝。
3. BEGIN IMMEDIATE 内复用原 R5 校验写入全部候选，清理旧窗口以及被替代的周内日期，追加唯一 run_completed。
4. 同一候选事务临时开启 query_only，以现有 verifier、固定 sample=50 验证；只有明确通过才提交。
5. 失败时数据、清理和 completed 同时回滚；外部只读连接一直看到上次提交的数据。随后尽力追加 run_failed（rows_written=false），命令非零退出。若收尾写入也失败，保留原始异常和 partial report，另记 manifest_persist_error 并输出错误日志。

一个 basket 是一次事务，其他 basket 保持独立。actual_only 不得降级为 tail/unpublishable，同周改取样日也不能绕过该检查。新窗口不得回退到已完成窗口之前；新方法学必须显式迁移。

旧表迁移边界：缺少 run_id 的空周频表可自动重建；有数据的旧表保留原样，Store 构造仅告警，不影响价格、forward ingestion 等其他读写。周频 PE 的两个写入入口拒绝旧 schema，backfill 在 source/API 调用前做同一预检；旧行如何归档和重新回填须显式处理，不猜测 run_id。

## 恢复步骤

- 同一生效日、且在live首次可用前已经公开的正式披露始终优先；这种永远不会被选中的live仅从本次计算域排除，原始行保留（issue077）。如果live在正式披露前曾可用，或对应更新的调仓生效日，它仍须通过完整发行人门。当前9/12窗口验证不能替代未来新调仓的live身份验收；CUSIP/ISIN不足或冲突时不允许ticker-only推断，也不允许改回旧组合冒充新组合。
- 更新精确证券alias配置后，旧source中的派生alias元数据必须按保留的CUSIP/ISIN重新解析并留存变更清单；保留raw ticker、raw payload、原fetched/accepted/effective日期和权重。新预检会拒绝陈旧alias，并检查已审核target的财报issuer CIK。不得用增加币种支持掩盖查到另一公司的财报（issue075）。
- 一次性验收脚本也必须等所有历史篮子的数据准备成功后才冻结PIT。若诊断阶段已经冻结了中间PIT，保留旧副本；最终验收从已审核source另建无PIT产品的新副本，不改写旧frozen vintage。本次cloud trial的旧批次与最终批次均保留。

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

## 已部署修复：live 身份继承与调仓周末延后（2026-09-25）

以下行为已合并 main 并部署到 aliyun，功能代码 `e730c3af`；云端 Python 3.10 专项 374 passed。估值尚未恢复，当前源数据阻塞见 `docs/audit/2026-09-25-index-pe-live-issuer-identity.md`。

- 季度调仓窗口按 Good Friday 与 2022 年起的 Juneteenth 调休识别实际前一交易日和首个生效交易日，以纽约时间 16:00 为正常收盘界线（自动处理夏冬令时；首个生效日若为圣诞前夜则用 13:00）。价格恰好停在预期前一交易日且首个生效日尚未收盘时返回 `live_deferred`；缺少更早应有的价格仍失败。该窄窗口规则只覆盖 3/6/9/12 月第三个周五，不冒充通用交易所日历，也不改写原始 nominal rebalance 日期。
- live 的发行人可继承同篮子最近一份 PIT 可用披露中的精确 CUSIP 证据；先选完整快照，再查证券，不回退更早披露。缺失证据允许有效人工 override；同 CUSIP 的成功/未解析混合行只表示继承证据不足，不制造发行人冲突。两种明确发行人或非空 ISIN 不同仍阻断。继承必须同时满足披露原日与 live 日的审核有效期；过期纠错不能暴露旧的错误 raw LEI 并把它继承出去。验证端独立重建。
- 每季度按真实未解析清单逐只审核，可能包含旧披露缺 CUSIP 的现有成员；不能只处理预估的新进股。GLEIF 查询可能返回子公司，须核对法人并必要时改用 SEC issuer-role 证据。证据文件带 SHA256、证券标识和有效期，保留原始行。
- 身份预检逐篮子运行 `--dry-run --allow-network --max-api-requests 8`，数据库显式只读指向刚拉取的主库。只验 `source_identity`，不能把后续预算耗尽当身份失败或产品通过；若报告缺失，预检无效。任何 source 冲突未解决，禁止恢复运行。

- 历史补跑时，今天抓取的 live 在本窗口结束后才可用，显式记 `live_skipped.reason=fetched_after_window`，不调用 live API。不得把被窗口截断的价格日历当作今天的完整价格日历。
- `--refresh-live` 在上述历史窗口或尚未收盘的延后状态下明确非零失败，报告保留原因；不能静默降成普通跳过。
- 每条新审核记录明确列出允许缺失 CUSIP 的值：`null`、空串、`N/A`、`000000000`。仅这些值可用精确 ISIN 匹配；其他不同 CUSIP 仍是冲突。发行人 canonical key 沿用原审核值；FER/APTV/TEL 不切换成新 LEI key。
- 继承索引只建立完整快照日期清单，选中的快照按 live 日期缓存解析；普通历史行自身的必需审核仍保留。到期证据须重新取证新增审核记录，不能延长旧记录或靠继承绕过到期。
