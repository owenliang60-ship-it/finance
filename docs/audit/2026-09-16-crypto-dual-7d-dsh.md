# Crypto 双榜新增7天 — DSH实施与Codex验收

## 授权与设计
Boss要求在既有30天双榜旁增加7天周期，并指定DSH实施。独立worktree：`.worktrees/crypto-dual-7d`，基线47aafaf。7天Beta/RS均使用42个完整4h收益率（43收盘价），同一昨日UTC成交额前50、BTC基准、Beta严格>1、各前十、周期内交集置顶+⭐。30天口径保持原样。现有4h缓存一次读取/取数供两周期复用。

## DSH执行
使用配置路由deepseek-official/deepseek-flash（DeepSeek-V41-Flash），未改模型/凭证。共3次调用：第一次默认8192单响应上限退出，零代码编辑；第二次32768上限完成实现；第三次按Codex P1意见修复单币行情加载异常中断整榜。所有DSH任务限定代码/测试，不执行网络、发送、commit或部署。

实现改动仅2源码+2测试。Codex另外修复云端浮点边界：完全相同且方差有效的价格路径，beta固定数学值1，避免1.0000000000000002误入>1；非相同序列不做阈值舍入。

## 验证证据
- DSH TDD：初始15 failed/13 passed → 60 passed；异常隔离2 failed → 62 passed。
- Codex最终本地命令：`.venv/bin/python -m pytest tests/test_crypto_daily_rankings.py tests/test_crypto_relative_momentum.py tests/test_crypto_beta_scanner.py tests/test_beta_indicator.py -q` → **63 passed**。
- 云端Python3.10完整隔离worktree同一命令 → **63 passed**；两源码py_compile通过。
- 2026-09-15真实原始4h缓存，独立标准库计算50beta+49RS对拍：beta最大误差1.78e-15，RS7.78e-16。30天rows/momentum_rows完全一致，消息与原生产Markdown逐字一致（尾部换行除外）。
- 7天交集：CVC（Beta#3/RS#10）、龙虾（Beta#6/RS#4）；7天beta有效50/50，RS49/49。
- 验证样本、独立预期、预览：`reports/crypto_daily_rankings/2026-09-16-7d-validation/`。DSH日志/brief位于worktree的`work/crypto-dual-7d/`，未纳入公开Git。

## 运行行为
原JSON增加seven_day嵌套报告；原30天Markdown保持，另写`crypto_dual_top10_7d_YYYY-MM-DD.md`。先落两份结果再发送30天/7天两条消息。dry-run不发送；任一发送失败冒泡。单币失败按周期显式不可用，BTC基础行情无效仍阻断。RS全不可用时不发送不完整双周期结果。
Quant shim与cron不用改；原PMARP/RVOL/NUPL投递保持现状，本次不处理历史待确认项。未额外手动群发；下一次自然daily验收尚待发生。
