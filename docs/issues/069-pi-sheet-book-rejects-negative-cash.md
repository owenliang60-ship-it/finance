# 069 — PI sheet book 把负现金当非法值，连续三晚失败

日期：2026-09-09。状态：已修复（`fix/pi-negative-cash`），待合并部署。

## 发现
`portfolio/holdings/sheet_book.py:_parse_cash` 里有一行 `if value < 0: raise SheetBookError("cash value invalid")`。
Boss 的 book 现金合计因小额融资变成负数后，Portfolio Intelligence 22:00 cron 于 2026-09-07/08/09 连续 FAIL rc=1，报告未生成。
设计 plan（`docs/plans/2026-06-10-pi-sheet-book.md`）没有写这条约束的理由，属于写 parser 时的防御性假设：
把"负数"和"公式错误/空值"混为一谈。

## 根因
book-of-record 的现金是券商余额，加杠杆时合法为负。真正需要拦的只有缺失和非数值（`#N/A`），`_to_float_strict` 已覆盖。
下游 `run_intelligence` 的 NAV = 股票 + 期权 + 现金，`cash_pct` 和 `_format_usd_compact` 都能正确处理负数，无需改动。

## 本次处理
删除负数拦截，保留 missing / non-numeric 严格校验；新增 `test_negative_cash_allowed`。

## 教训
"严格校验"要区分**数据损坏**（拦）和**业务上合法但不常见的取值**（放行）。给 book-of-record 字段加范围检查前先问：这个范围是数据契约还是我的假设？

## 验证
`tests/test_portfolio/test_sheet_book.py` + `tests/test_telegram_routing.py` 共 55 passed。
