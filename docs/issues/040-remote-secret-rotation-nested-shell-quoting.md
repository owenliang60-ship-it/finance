# Issue 040: 跨 SSH 轮换 secret 不应依赖多层 shell/Python 字符串转义

**Status**: 已修正流程（FMP forward EPS cloud rollout）
**Date**: 2026-07-13
**Severity**: MEDIUM — 错误实现可能造成 `.env` 写入失败、内容破坏或 secret 泄漏到命令/日志
**Related**: local/cloud `.env` key rotation

## 触发

将 staged FMP key 安全轮换到本地与云端 `.env` 时，前两次远端更新脚本在 SSH、shell、Python 三层引号组合处触发 `SyntaxError`。解析失败发生在文件写入前，因此没有造成部分写入，但暴露了流程脆弱性。

## 根因

同一段文本同时穿过本地 shell、SSH 远端 shell和 Python 字符串字面量；换行与引号需要在三层分别解释。即使 secret 本身不打印，依赖嵌套字符串拼接仍很难审计，也容易在未来值包含特殊字符时失败。

## 修正

- 轮换前分别创建临时本地/云端 `.env` 备份。
- secret 只通过标准输入/进程环境的数据通道传递，不出现在日志和文档。
- 远端脚本避免嵌入转义换行字面量，使用结构化行处理后原子替换目标字段。
- 写后仅验证 `configured=yes`、interval 值、staging 字段已移除以及 legacy/new endpoint smoke；不回显 key。
- 全部 smoke 通过后才删除临时旧-key 备份。

## 教训

secret rotation 是数据传输问题，不是字符串拼接问题。跨 SSH 时应把代码和 secret 分离：代码走固定脚本/标准输入，secret 走独立数据通道，并以“写前备份、原子替换、布尔验证、smoke、再删备份”为固定协议。
