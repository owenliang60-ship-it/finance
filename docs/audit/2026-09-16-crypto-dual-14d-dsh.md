# Crypto双榜新增14天 — DSH实施/Codex验收

## 需求与实现
Boss追加14天双榜，84个完整4h收益率（85收盘价），其他不变。基线ca64901、worktree codex/crypto-dual-14d。DSH沿用deepseek-official/deepseek-flash（DeepSeek-V41-Flash），1次调用完成，Codex主线程逐行review无阻断项。

复用7天计算块为小私有wrapper，仍调用现有window/compute_beta/score内核；新增fourteen_day嵌套结果。30天日线beta+180根4hRS、7天42根4h计算/字段/消息保持。各周期同一昨日前50、beta>1前十、RS独立前十、周期内交集置顶⭐；同币4h只加载一次。缺口、新币、单币读取异常保留不可用语义，不填默认值。BTC相同有效序列精确beta1守卫保留。

每日消息顺序30天→14天→7天。先存JSON及三份Markdown，再发送；新增`crypto_dual_top10_14d_YYYY-MM-DD.md`。任一发送失败向cron冒泡，dry-run零发送。Quant shim和cron无改动。

## 测试证据
DSH TDD：13 failed/20 passed → 75 passed。
Codex本地与云端Python3.10完整隔离worktree分别执行：
```
python -m pytest tests/test_crypto_daily_rankings.py tests/test_crypto_relative_momentum.py tests/test_crypto_beta_scanner.py tests/test_beta_indicator.py -q
75 passed
python -m py_compile scripts/crypto_daily_rankings.py
```
真实冻结9/15行情：独立标准库计算49个14d beta及48个RS；本地beta最大误差8.89e-16、云端2.23e-15，RS3.13e-16。两环境分别与各自旧版本比较，30d/7d字段完全一致，消息与原预览逐字一致。

最初用本地浮点JSON逐位对比云端，7d beta出现约1e-15的既有数值差；查明为跨环境计算差异。改为云端旧commit同环境重算作基线，精确一致；跨环境独立数学对拍保留1e-10阈值，没有修改业务计算。

14d Beta有效49/50，RS48/49。交集USELESS（Beta#1/RS#7）、ZEC（Beta#4/RS#4）。输入/独立预期/验证JSON/预览：`reports/crypto_daily_rankings/2026-09-16-14d-validation/`。DSH私有工作日志位于worktree `work/crypto-dual-14d/`，未入公开Git。

未手动发送Telegram；自然daily首次投递尚待发生。

## 部署完成
代码`06d4a3f`已合并main并push；云端在原Quant cron锁内备份并pull。备份`/root/workspace/Quant/backups/crypto-14d-20260916T012305Z/`。生产实际模块dry-run（冻结真实scan输入）PASS：49个14d beta/48个RS，交集USELESS/ZEC，30d/7d结果保持一致；三份Markdown及总JSON完整落盘，live_api_calls=0、telegram_sends=0。下次08:06自然daily发送30→14→7，未手动群发。
