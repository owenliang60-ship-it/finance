# 三指数真实数据隔离试跑（Boss 已批准）

目的：使用云端权威 market.db 的一致性副本生成真实五年周频曲线，先验收数据与图，再决定生产落库和发布。承接原计划 §7；本文件及代码准备不是生产授权。

2026-09-11：Boss 在两项复审修复后回复“可以继续”，批准上述隔离试跑，不包含生产推广。冻结试跑代码 `6a34b02`；云端独立 checkout `/tmp/finance-index-pe-trial-20260911/code`，SQLite backup 副本位于其父目录 `market.db`，路径已断言与生产不同且非 symlink。副本 quick_check=ok，三 ETF 价格截至 2026-09-10；本轮采用该日作为估值日。

先执行 source-only 操作脚本（复用 `_fetch_sources`，无逐股 endpoint）：子预算150次、600秒；响应与累计尝试数写入同目录 `evidence/`。正常结束按实耗扣预算；若异常终止未留最终计数，则保守按整段150次占用，不补发额度。全程3小时从 source-started.json 的 started_at 起算，后续进程仅领取剩余额度/时间。

## 边界与预算

- 云端临时 checkout + 临时 market.db；原 market.db 仅以 mode=ro 打开并通过 SQLite backup 复制。执行前断言目标 resolve 路径不同于生产 DB，禁止用生产 DB symlink 充当副本。
- FMP 请求预算 **总计 3,000 次 HTTP（含失败重试）**，用已实现 `--max-api-requests`；若需重新调用进程，扣除已用请求，不能每次重新领 3,000 次。
- 总时限 **3 小时**，使用已核实的 `/usr/bin/timeout`；预计 1–2 小时，随真实缺口变化，不承诺全部覆盖达标。超时不自动重新开始。
- 目录查询显示五年窗口内每篮子约 20 期，SPY/QQQ 另需前置季度；基金源约 68–70 次调用，其余为缺失 income/HMC、拆股、FX 和强制修复。实际 symbol union 要到披露落入临时库后才能精确统计。
- 不写生产表、不改 cron、不发送消息、不 merge/push/deploy。不触碰 company.db / 真实持仓。

## 顺序

1. 校验云端空间（本次预检余 9.8 GB）和运行环境；冻结代码 SHA；复制 market.db 到临时目录。
2. 拉取窗口内披露，先检查 vendor CIK/证券身份、双股权结构及配置 convention；未知映射不能猜。
3. 在累计预算内补齐临时库的季度基本面、历史市值、拆股/FX；KLAC/MCHP 等异常须修复或隔离。
4. 三指数 C1 整窗计算与 sample=50 验收；数据质量不达标则输出明确缺口，不降低门槛。
5. 对已有、complete 的真实 weekly snapshots 派生六篮子 PIT；不重新拉分析师预期伪造旧 vintage，不将 backfill 快照当 PIT。
6. 独立复核样本和覆盖率，生成真实 PNG、自包含 HTML/PDF；将结果带回本地供 Boss 目视审核。
7. 保留临时结果、请求计数和失败证据；另行申请生产数据推广、合并与部署。

任何异常触发预算/身份/覆盖门时停止相应写入，保留已有认证结果。既有四项 P1 与 C1 的门控不得为试跑“过关”而撤销。

## 本轮结果：身份门停止

source阶段已于2026-09-11 07:35:35–07:37:10 UTC执行，48次HTTP，未进入逐股补数/历史或PIT聚合。发现filer CIK误作issuer身份、跨ETF alias基金CIK错误限定、live CUSIP丢失及两行期货误分类，详issue072。两张估值表仍0行，旧生产完全未改。

剩余网络预算2,952次；不自动续跑，也不重置原3小时时钟。下一步先审批`2026-09-11-index-pe-source-identity-repair.md`；完整试跑证据见`docs/audit/2026-09-11-index-pe-cloud-trial.md`。
