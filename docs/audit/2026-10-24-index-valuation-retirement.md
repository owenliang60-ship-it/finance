# 三指数估值退役验收 — 2026-10-24

Boss 取消 CRH 修复/补跑，要求下线并归档三指数估值模块。本次已完成合并、推送
和生产部署，功能版本 `1a3cd322`；云端完成时间为 **16:20:44 SGT**。

## 已生效行为

- `finance_forward` 保留 yfinance/FMP 原始预期采集和 data-stage 校验；停止三指数
  五年重算、六篮子派生估值及对应估值认证。
- 晨报 HTML/PDF/PNG 移除 `0d` 生成与展示，旧图表 payload 也不会重新带入栏目。
- 公共三指数 backfill CLI 与 `--phase valuation` 在数据库/网络副作用前返回归档提示。
- `crh` heartbeat 已删除。取消的 CRH 修复从未合并至 main、从未部署或修改生产数据。
- 个股 EPS/earnings/原始 holdings 快照继续服务其他模块。独立旧 SOXX 一次性研究
  工具没有 cron，明确在本次三指数/六篮子生产功能退役范围之外。

## 归档与数据保留

Git 已推送两个归档标签：

- `archive/index-valuation-2026-10-24` → `9f229c71`：退役前生产代码。
- `archive/crh-recovery-cancelled-2026-10-24` → `212bd157`：取消的隔离修复候选与验收证据。

生产在共享 `market_db_writer` 锁内，通过既有 SQLite backup helper 保存包含 WAL
的一致快照，完成 quick_check 和 SHA256 后才切换代码。锁一直保持到最终验证结束。

- 路径：`aliyun:/root/workspace/Finance/data/market.db.backup-20261024081845-pre-index-valuation-archive`
- 大小：1,455,824,896 bytes。
- SHA256：`3c4e3744f554147096550b3dc8d50833c295a168d749a3b43fd642a784d8cac4`。
- SQLite quick_check：`ok`。

退役前后全表指纹完全一致：历史周频产品 **779 行**、运行清单 **607 行**、共识估值
**84 行**。crontab 文本也完全一致；通过修改其调用的脚本移除估值阶段，没有删除
共享采集 cron。没有执行数据修复、估值重算、市场数据 HTTP 或手动消息推送。
这些历史产品保留已知的源数据一致性限制，不宣称已重新认证。

## 验证证据

- TDD：新增退役边界测试先出现 6 个预期失败，再实施停止入口与移除栏目。
- 本地采集/估值相关套件：**177 passed**。
- `pytest tests -q --tb=short`：**4861 passed, 2 failed, 4 skipped**，380.83 秒。
  本次全量运行收集测试后，审查发现两条旧 HTML 测试仍要求 `0d`；失败仅为这两条
  已退役行为断言。它们已改为验证栏目消失与其余栏目顺序保留。
- 最终晨报/退役/cron/HTML 复测：**191 passed, 1 skipped**，17.54 秒，覆盖上述两条修正。
- 云端 Python 3.10 隔离验证：**99 passed**，5.75 秒。
- 部署后的真实生产代码检查：
  `python3 -m pytest tests/test_index_valuation_retirement.py tests/test_forward_cron_entrypoint.py tests/test_morning_html_report.py -q --tb=short`
  → **22 passed**，3.03 秒。测试使用临时数据/模拟采集，不发生产采集请求。
- Python 3.10 AST、Shell、`git diff --check`、新增 diff 凭证模式检查通过。
- 部署包装器故障演练：锁忙、已有收据、备份失败三种情况均停止且不部署；既有收据
  不会被覆盖。一个独立 reviewer 审查退役补丁、修正和部署包装器，无剩余阻塞项。
- 退役前只读检查共享 FMP data 阶段：`rc=0`，961/995（96.58%），保留既有 drift 警告。

完整部署收据：`reports/index-valuation-archive-20261024/production-receipt.json`。
云端收据与 smoke 日志：`data/index-valuation-archive-20261024/`。
没有等待或安排新的自然运行监控；下次 cron 将使用已验证的采集-only 入口。
恢复要求见 [归档说明](../../archive/index-valuation/README.md)。
