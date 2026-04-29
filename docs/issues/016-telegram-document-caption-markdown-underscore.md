# 016: Telegram 图片文件 caption 下划线触发 Markdown parse error

**日期**: 2026-04-29
**严重度**: MEDIUM（晨报图片生成成功，但多数 section 发送失败）
**影响范围**: `send_document()` / `send_photo()` 带文件名 caption 的路径

## 现象

2026-04-29 晨报生成了 5 张图片，但 Telegram 群里只收到 `02_02_pmarp.png`。云端日志显示其余 4 张 `sendDocument` 均 HTTP 400。

## 根因

图片 caption 使用 `path.stem`，例如：

```text
未来资本晨报 1/5 — 01_01_broad_signal
```

`src/telegram_bot.py` 给 `sendDocument` 和 `sendPhoto` 请求统一带了 `parse_mode=Markdown`。Telegram legacy Markdown 会把 `_` 当作斜体标记；`01_01_broad_signal` 有 3 个下划线，实体不闭合，触发 `Bad Request: can't parse entities`。`02_02_pmarp` 只有 2 个下划线，刚好能被解析，所以只有 PMARP 发出。

## 修复

文件/图片 caption 改为纯文本：`send_document()` 和 `send_photo()` 不再传 `parse_mode=Markdown`。文字消息 `send_message()` 保留 Markdown，因为晨报文本格式仍依赖它。

同时修复 Telegram 错误日志：不再直接记录 `raise_for_status()` 生成的完整 bot URL，避免 token 出现在日志里；改为记录脱敏后的 HTTP 状态和 Telegram description。

## 教训

Telegram `parse_mode=Markdown` 只应该用于确实需要格式化的文本消息。文件名、caption、外部字符串默认都应按纯文本处理，或者显式 escape。
