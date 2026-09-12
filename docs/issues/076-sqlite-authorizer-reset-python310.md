# Issue 076: SQLite authorizer 清理在 Python 3.10 不接受 None

- 日期：2026-09-12
- 状态：RESOLVED（一次性上线迁移脚本；未影响生产）
- 现象：生产副本演练已完成候选写入，但 `set_authorizer(None)` 后的 `PRAGMA query_only=ON` 报 `not authorized`；异常清理同样报错。连接关闭后未提交事务自动回滚，生产库仍未动。
- 根因：本地 Python 3.13 支持用 None 解除 authorizer，云端 Python 3.10 不支持；AST 语法检查发现不了标准库行为差异。
- 修复：清理时显式安装返回 `sqlite3.SQLITE_OK` 的回调；回滚失败另记，不覆盖最初异常。新增回归在本地和云端实际解释器运行，之后重跑完整副本推广认证。
- 边界：该回调仅约束一次性推广事务的可写表；认证阶段仍启用 SQLite query_only。没有放松 PE、issuer 或覆盖率门。
