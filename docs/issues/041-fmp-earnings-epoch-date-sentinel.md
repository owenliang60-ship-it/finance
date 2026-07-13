# Issue 041: FMP earnings 可能用 1970-01-01 表示未知公告日

**Status**: 已记录、待独立数据质量决策（不阻塞 FMP forward Phase 1 rollout）
**Date**: 2026-07-13
**Severity**: LOW — 不影响 forward 4Q coverage gate，但会污染按公告日排序或历史窗口统计
**Related**: `src/data/fmp_forward_ingestion.py` · `fmp_earnings`

## 触发

2021+ production backfill 的全表只读审计发现一行：BMNR 的 `announce_date=1970-01-01`、`fiscal_date=NULL`、`match_method=none`、`eps_actual=NULL`，但仍带 EPS/revenue estimate。全表查询 `announce_date <= 1971-01-01` 只有这一行。

## 判断

这不是系统生成的真实历史日期，而是上游用 Unix epoch 表示“公告日未知”的 sentinel。当前转换层只验证 ISO date，因此该值形式合法并被保留。

## 当前处理

- backfill 后保留原行作为审计证据；不在 rollout 中擅自删除或改写。
- 随后的同日 weekly replace 已自然移除该行，最终表最早 `announce_date` 恢复为 1985；这不证明上游问题消失，未来 backfill/weekly 仍可能复现。
- 独立 verifier 的 forward coverage 只读 `fmp_estimates`，不受此行影响。
- Phase 2 消费 earnings 时不得把 1970-01-01 当真实事件日期。

## 后续决策

在独立数据质量修复中选择并测试一种明确语义：

1. ingest 时把 epoch sentinel 归一为 `NULL`，同时保留原始 evidence；或
2. 将该行隔离到 invalid/audit 计数，不进入 `fmp_earnings` 业务表。

决定前先对 live endpoint 做更大范围 sentinel 频率审计，避免为单一 BMNR 样本过拟合规则。
