# 005: crontab 管道写入导致清空

**日期**: 2026-03-20
**严重度**: HIGH（生产 crontab 被清空，所有定时任务停止）
**恢复时间**: ~3 分钟（从对话历史中恢复）

## 发生了什么

执行 `ssh aliyun "crontab -l" | sed '...' | ssh aliyun "crontab -"` 时，`sed` 命令因转义字符错误失败，输出为空。空内容被管道传给 `crontab -`，导致云端 crontab 被清空。

## 根因

`crontab -` 从 stdin 读取并**覆盖写入**，不做确认。如果 stdin 是空的，crontab 就变成空的。管道中任何一步失败都可能导致下游收到空输入。

## 修复

从当前对话历史中找到完整的 crontab 内容，写入本地文件后用 `cat file | ssh aliyun "crontab -"` 恢复。

## 防范规则

1. **永远不要** `sed ... | crontab -` 管道直写
2. 操作 crontab 的正确流程：
   ```bash
   # 1. 导出到文件
   ssh aliyun "crontab -l > /tmp/crontab_backup.txt"
   # 2. 编辑文件
   # 3. 写回
   cat new_crontab.txt | ssh aliyun "crontab -"
   # 4. 验证
   ssh aliyun "crontab -l | grep -c '^[^#]'"
   ```
3. 关键 crontab 操作前先做快照备份
