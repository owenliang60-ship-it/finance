# Issue 076: SQLite authorizer 清理在 Python 3.10 不接受 None

- 日期：2026-09-12
- 状态：RESOLVED（一次性上线迁移脚本；未影响生产）
- 现象：生产副本演练已完成候选写入，但 `set_authorizer(None)` 后的 `PRAGMA query_only=ON` 报 `not authorized`；异常清理同样报错。连接关闭后未提交事务自动回滚，生产库仍未动。
- 根因：本地 Python 3.13 支持用 None 解除 authorizer，云端 Python 3.10 不支持；AST 语法检查发现不了标准库行为差异。
- 修复：清理时显式安装返回 `sqlite3.SQLITE_OK` 的回调；回滚失败另记，不覆盖最初异常。新增回归在本地和云端实际解释器运行，之后重跑完整副本推广认证。
- 边界：该回调仅约束一次性推广事务的可写表；认证阶段仍启用 SQLite query_only。没有放松 PE、issuer 或覆盖率门。

## 同日发现：跨 Python 版本的 PIT 浮点末位

本地3.13重新聚合的PIT，在云端3.10逐字段严格重放时出现末位差异，例如EPS `6.31755` vs `6.317550000000001`；六篮子均被严格比较拒绝，生产事务再次回滚。历史15项和1640值独立SQL数值校验通过，九期PIT的估值数值在容忍1e-10对拍中未改变。

首次生产PIT改为在写锁和候选事务内调用既有`build_forward_valuations`，由其所属云端解释器现场生成并严格认证。历史完整窗口及run记录仍原样推广。旧本地/试跑冻结PIT不覆写，既有生产PIT非空仍拒绝bootstrap，不放宽verifier容差。未来迁移Python运行时也须先验证冻结PIT的重放兼容性。
