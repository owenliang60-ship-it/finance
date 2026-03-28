# 007: 晨报市场级社交数据永远不显示

**日期**: 2026-03-27
**严重度**: MEDIUM（Section E/F 自上线以来从未在晨报中出现）
**恢复时间**: ~10 分钟

## 发生了什么

Phase A/B 新增的市场级社交数据（market_sentiment + social_trending + social_trending_sectors）代码已集成到晨报，但 Section E（市场情绪脉搏）和 Section F（社交热门）从未在实际晨报中显示。

## 根因

**双重 bug**：

### Bug 1: 严格日期匹配
`morning_report.py` line 524 要求 `row.get("date") == today_utc` 精确匹配今天 UTC 日期。但：
- 云端 social cron 在 10:05 北京时间运行
- 本地 launchd pull 在 09:00 运行
- 晨报在 07:00 运行

→ 晨报运行时 DB 里永远只有昨天的数据，日期检查永远失败。

### Bug 2: source 名称不匹配
DB 中 `source` 字段存的是 `"x"`（与 Adanos API 一致），但晨报代码中查询用的是 `"twitter"`。

→ 即使日期匹配通过，X 平台数据也永远查不到，只剩 Reddit 一半。

三处出现 `"twitter"` 而非 `"x"`：
- `format_section_market_pulse()` 循环
- 市场情绪加载循环
- 热门数据加载循环

## 修复

1. **日期检查放宽**: `== today_utc` → `in {today_utc, yesterday_utc}`，接受最近 2 天数据
2. **trending fallback**: 先查今天，无数据则查昨天
3. **source 名称**: `"twitter"` → `"x"` 全部修正（3 处）
4. **日期标注**: 非今天的数据在 Section 标题加 `[YYYY-MM-DD]` 标签

## 教训

- **集成测试不够**: 新功能写了单元测试但没做 E2E 验证（用真实 DB 跑一次晨报看输出）
- **命名一致性**: 数据写入和读取应使用同一套常量，不要在不同文件中硬编码字符串
- **时序依赖**: 涉及多个 cron 的功能，必须画出时序图确认数据在消费时已就绪
