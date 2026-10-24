# 三指数估值 — 已归档（2026-10-24）

Boss 决定下线本模块，取消 CRH 修复与历史补跑。退出生产运行，保留代码、
历史产物、原始证据和测试供查阅。此决定不改变北极星中的个股前瞻预期数据层。

已于 2026-10-24 16:20 SGT 部署，功能版本 `1a3cd322`。
实际备份路径、指纹和测试证据见 [退役验收](../../docs/audit/2026-10-24-index-valuation-retirement.md)。

## 已停止的入口

- `scripts/run_forward_data.sh` 不再调用三指数历史回填、六篮子派生估值、
  历史估值认证或 full valuation verifier。
- 晨报 HTML/PDF/PNG 不再生成或展示「0d. 三指数估值」，包括旧 payload 中
  残留的图表字段。
- `scripts/backfill_index_pe_history.py` 公共 CLI 与
  `scripts/update_fmp_forward.py --phase valuation` 返回明确归档提示及退出码 2，
  在备份、数据库写入和网络请求之前停止。
- 本聊天的 `crh` 恢复 heartbeat 已删除。

仍运行的 `finance_forward` 任务只负责 yfinance/FMP 个股预期与原始快照采集，
随后执行 data-stage 校验。这些数据仍被景气引擎等模块使用。原 cron 行、写锁、
错误上抛及底层采集覆盖率规则保留。退役没有重新采集或修改市场数据。

## 归档位置与边界

| 内容 | 位置 |
|---|---|
| 退役前生产代码 | Git tag `archive/index-valuation-2026-10-24` → `9f229c71` |
| 已取消、从未上线的 CRH 修复候选 | Git tag `archive/crh-recovery-cancelled-2026-10-24` → `212bd157` |
| 运行前整库一致快照 | 云端 `market.db.backup-<timestamp>-pre-index-valuation-archive`；部署收据记录实际路径与 SHA256 |
| 历史产品与完成/失败记录 | 原库 `basket_weekly_pe_history`、`basket_pe_backfill_runs`、`fmp_basket_valuation`，原样保留 |
| 原始预期、财报、持仓与 vintage | 原表保留；共享采集仍运行 |
| 既往运行资料 | `docs/runbooks/index-pe-weekly-window.md` 与 `docs/audit/`，仅作历史参考 |

历史表保留不代表仍是当前有效估值。最后发布日及源数据一致性以归档收据为准；
14:00 财报更新后，QQQ/SOXX 旧产品与当前源存在已知差异，退役时不修复、不洗白。

以下计算实现及相关配置保留原路径，避免破坏共享工具和历史证据的导入关系：
`terminal/index_pe_weekly.py`、`terminal/hindsight_ntm_valuation.py`、
`terminal/forward_valuation.py`、`terminal/index_valuation_chart.py`，以及历史
backfill/verifier、数据库 schema、`config/baskets/`。它们不再从生产定时任务
或晨报调用。私有 `_archived_main` 和派生估值函数仅保留给隔离参考测试；
只读 verifier 可以用于查阅旧数据。没有删除数据表或大规模移动共享代码。

独立的 `scripts/backfill_soxx_historical_pe.py` 是退役前三指数模块复用的早期
SOXX 单 ETF 一次性研究工具，写入独立的 `basket_ttm_valuation`。它没有 cron，
不在本次三指数/六篮子生产模块退役范围内；保持原状，不作为生产恢复入口。

## 恢复要求

恢复生产需要 Boss 重新批准。先在独立 worktree 检出归档 tag，重新核验数据来源、
发布时点、任务依赖和资源预算，再恢复运行入口。不要直接运行旧 runbook 的补跑
命令，不要将 CRH 实验库或旧整库快照覆盖当前生产库。源数据更新必须先于估值发布。
