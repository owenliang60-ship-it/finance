# 005: IV 回填日期范围过大，浪费 API credits

**日期**: 2026-03-08
**严重程度**: 低（数据不损失，但浪费约 4500 credits）
**根因**: 照搬脚本示例参数，未确认实际需求

---

## 发生了什么

为 20 只新增股票回填 IV 历史数据时，使用 `--start 2024-02-25`（530 个交易日 ≈ 2 年），实际只需要 252 个交易日（1 年）。

- 系统 IV rank/percentile 计算只用最近 252 天（`OPTIONS_IV_LOOKBACK_DAYS = 252`）
- 多回填的 1 年数据不会被使用，白白消耗约 4500 MarketData.app credits

## 根因分析

`scripts/backfill_iv.py` 的示例命令用了 2 年范围：
```
python3 scripts/backfill_iv.py --start 2024-02-25 --end 2026-02-24
```
CC 直接复制示例参数，没有交叉检查 `config/settings.py` 中的 lookback 配置。

## 正确做法

```bash
# 回填 1 年（252 交易日 ≈ 365 日历天）
python3 scripts/backfill_iv.py --start $(date -d '-1 year' +%Y-%m-%d) --symbols ...
```

## 防范措施

- **IV 回填一律用 1 年起始日**：`--start` = 当前日期 - 365 天
- **MarketData.app credits 有限**（10K/天），每次回填前估算 credits 消耗：`symbols × trading_days`
- 脚本示例参数仅供参考，实际参数必须根据业务需求确定
