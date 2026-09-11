# 三指数 PE 晨报升级：本地验收与云端停点

日期：2026-09-11。代码验证版本 `89beeb2`，分支 `codex/index-pe-morning-chart`；已对齐当前 main `4bb2906`，生产 main 未改，未推送或部署。

## 结果

Task 5–8 本地代码完成：六篮子 PIT 共识估值、三指数五年周频 PNG、HTML 自包含图片、PDF 复用、错误降级与运行说明。Task 9 的本地测试/语法/变更安全检查和示意图目视检查通过；**真实数据回填、真实 PNG 及生产验收尚未完成**。

| 验证 | 结果 |
|---|---|
| 对齐 main 前完整测试（独立数据副本） | 2840 passed / 2 frozen pre-existing failed / 4 skipped；仅旧持仓 Sheet 配置测试失败，数据副本补齐后原 5 个 breadth 文件缺失失败消失 |
| 对齐 main 后完整测试 | 3468 passed / 4 skipped / 0 failed；主线已有修复覆盖上述旧失败 |
| 请求上限与披露窗口补强后完整测试 | **3472 passed / 4 skipped / 0 failed**，312.71 秒，17 个既有数值/弃用警告 |
| Python 3.10 AST、shell 语法、关键未定义名检查 | 通过 |
| 相对当前 main 的 diff whitespace / 新增凭据字面量检查 | 通过；没有 .env / DB / 本次生成 PNG/HTML/PDF 入库 |
| 图表 | 1800×1510 RGB，当前示意 PNG 160847 bytes；虚线、缺口、日期、覆盖率、分位和共识口径可读 |
| 投递链 | PNG 只生成一次，HTML 内嵌 data URI，PDF 五页示例中只包含一次；HTML 成功后摘要失败不会触发重复 PDF |

测试使用两个临时 git worktree 与数据副本，不在主数据目录运行全量测试。副本使用 APFS clone，基线验证的数据库另做 WAL-safe backup。未运行会更新 Dollar Volume 的真实 morning_report main，未发送 Telegram。

## 主线集成

主线新增了 Extended 主池、财季修复、Premium、Dollar Volume 完整性等功能。仅在隔离副本解决四个冲突文件（导入、样式、文档和板块拼接），保留双方能力。

主线现有 **0c 选股罗盘** 保持原位，新估值图编号调整为 **0d**，置于罗盘后、PMARP 前；旧有板块未重排。已加共存测试并同步当前说明。图仍不采用 ETF 官方口径，共识不保证与 GAAP 实际同口径。

## 数据与预算预检

- 云端只读数据库：2026-09-05 complete weekly source，1,012 symbols；五只 ETF 有 9 期 holdings。PIT 估值表仍为空，历史披露/周频产品表尚未部署。
- 本次额外只读 FMP 日期目录查询 **3 次 HTTP**：SPY/QQQ 目录各 28 期（始于 2019Q3），SOXX 20 期（始于 2021Q3），均到 2026Q2。未拉逐股数据或季度明细。
- 因此补强 source 范围：只抓请求窗口加两个前置季度，跳过价格日历以前的目录；run 的逐股 universe 只包含窗口内可能被使用的组合，保留起点当时已公开的组合。完整 source 的可用日期 floor 仍用于 manifest，不用收缩后的 universe 重建分母。
- 新 `--max-api-requests` 在每次 HTTP 尝试前检查，包括重试；达到上限立即拒绝新请求。新测试验证预算不是只数成功请求或函数调用。
- 云端盘余约 **9.8 GB**，market.db 约 **977 MB**；`/usr/bin/timeout` 可用，可在临时库试跑时同时限制时间和请求量。

## 可查看的预览

`reports/rendered/index-pe-review-20260911/`：

- `index_pe_demo.png`：红字标明“合成数据示意”，不是实际估值。
- `morning_report_2026-09-11.html`：自包含 HTML，显示 0c 罗盘未加载提示与 0d 估值图。
- `morning_report_demo.pdf`：5 页 fallback 示例。

本次示例不写估值表、不参加分位研究。历史 SOXX 分支原已追踪的两张 7 月研究 PNG 保留，未把它们冒充本次产物。

## 下一停点

已向 Boss 请求云端**隔离库**试跑批准：最多 3,000 次 FMP HTTP 请求（含重试）、3 小时；不写生产表、不改 cron、不发 Telegram、不合并发布。细则见 `docs/plans/2026-09-11-index-pe-cloud-trial.md`。

真实验收须检查每快照 vendor CIK 覆盖与历史双股权 sweep、live 身份字段、FX 推断和真实 publishable 覆盖率；不能将 vendor CIK 当成 SEC CIK，也不能以本地 fixture 通过代替数据验收。
