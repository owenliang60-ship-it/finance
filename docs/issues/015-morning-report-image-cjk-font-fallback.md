# 015: 晨报图片中文字体 fallback 到 DejaVu 导致方块字

**日期**: 2026-04-29
**严重度**: MEDIUM（晨报图片可读性损坏，文字版数据本身不受影响）
**影响范围**: `scripts/morning_report.py --image-report`

## 现象

Telegram 晨报图片中英文和数字正常，但中文标题、列名、概念标签显示为方块字，例如 `2. PMARP □□`、`□□/□□`。

## 根因

图片晨报用 Pillow 直接绘制文字。云端 aliyun 没有候选列表中的 macOS 字体、Noto CJK 或 `wqy-microhei`，于是 `_load_visual_font()` 最终 fallback 到 DejaVu。DejaVu 不覆盖 CJK glyph，Pillow 绘制中文时显示 tofu 方块。

云端实际可用中文字体：

```text
/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc
/usr/share/fonts/truetype/unifont/unifont.ttf
```

## 修复

把 `wqy-zenhei.ttc` 和 `unifont.ttf` 加入 `_VISUAL_FONT_CANDIDATES`，并保证排在 DejaVu fallback 之前。新增测试断言 aliyun CJK 字体候选不会被 DejaVu 抢先。

## 教训

所有服务器端图片/PDF渲染只要含中文，都不能假设英文字体 fallback 能处理 CJK。上线前要在目标机器上用实际 font path 做一次 smoke。
