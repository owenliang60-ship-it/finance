# Issue 048: Fatal lock step 不应通过控制流连坐后续独立任务

**Status**: 已修复（Extended Primary Universe Stop A review fix wave）
**Date**: 2026-08-20
**Severity**: MEDIUM — extended refresh 锁忙时 concept weekly sync 被整段跳过
**Related**: `scripts/broad_universe_cron_wrapper.sh`

## 触发与根因

weekly wrapper 的 step 6 取 `market_db_writer` 失败后直接 `exit 75`，导致本应 nonblocking 的 step 7 永远没有执行机会。简单删掉 exit 又会让 concept writer 在别的任务持锁时并发写库。

## 修复

- extended step 对共享锁做可配置的 1800 秒有界等待，失败 rc 延迟到 step 7 之后退出。
- concept step 不被控制流连坐，但写库前独立、非阻塞取得同一资源锁；仍忙则 WARN，绝不裸并发。
- 无论 concept 结果如何，extended 的失败 rc 最终保留，避免误报整体成功。
- Stop C runbook 明确 Stop C→E 过渡期的日志核查与补跑责任。

## 教训

串行 wrapper 要把“是否继续后续步骤”与“最终退出码”分开；后续步骤若也写同一资源，必须独立遵守锁契约，不能用 nonblocking 名义绕锁。
