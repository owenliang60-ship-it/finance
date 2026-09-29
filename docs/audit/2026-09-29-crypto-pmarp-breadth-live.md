# Crypto PMARP 市场宽度 — 已上线

2026-09-29 已将历史逐日 Crypto USDT 单币永续市场宽度接入原 Quant 日报，包含 BTC，排除 TradFi 与指数合约。
运行版本 `3fddc43425106c29b3df0647f1a00d2834cc6d19`，从 `828c9b848a2d6811d9a186ebe4b653aeceed2683` fast-forward 部署。

## 投递与时间

- 原 cron：`6 8 * * * /root/workspace/Quant/scanners/run_daily_scan.sh`，Asia/Shanghai。
- 原 PMARP / RVOL / NUPL / 趋势榜入口不改；趋势榜执行后独立运行宽度，互相不阻断，最后汇总错误。
- 正常日宽度仍需约500余次串行请求，预计在动量榜之后约10–15分钟完成，受接口延迟影响。
- 已补发 **2026-09-28 UTC** 日线报告，即北京时间 **2026-09-29 08:00** 截止数据。
- Telegram 确认时间 `2026-09-29T10:15:21+08:00`，message_id **1931**；本次手动宽度消息 **1条**，趋势榜 **0条**。
- 去重回执：`/root/workspace/Quant/results/daily_rankings/delivery_receipts/2026-09-28-breadth-ok.json`。
- 下一次自然任务：**2026-09-30 08:06 Asia/Shanghai**，尚未发生，不将手动补发称为自然触发验证。

## 本次数据

有效506 / 当日525，预热19。
极强18/506 =3.5573122529644268%，一年分位P71.5068493150685。
极弱3/506 =0.5928853754940712%，一年分位P28.493150684931507。
分位比较期2025-09-28至2026-09-27；不含当天，并列计入。

## 验证与保护

- 最新main在worktree整合，Crypto相关测试 **319 passed in9.14s**；生产 **319 passed in 17.47s**。
- 已核验三个随版本附带的退市价格文件SHA；迁入 **607** 个校验通过的滚动价格缓存，随后仅补缺失数据。
- 独立标准库EMA/PMARP复算：**610个合约、366天、0差异**，不调用producer计算函数。
- 独立校验脚本初次在`/tmp`启动时因默认manifest父目录索引报错而中止；改为必须显式传`--manifest`后完成。该问题仅在验收脚本，报告计算代码未改，校验失败期间未发消息。
- 补发仅调用已有Quant发送路由与成功回执封装，取得Telegram正向确认；没有重复今早已发的10/14日榜单。
- crontab及三个Quant入口文件SHA前后完全一致，无服务重启、无新cron。
- 合并保留了主工作区原有4个未提交文件，字节哈希检查一致。

## 备份与回滚

云端：`/root/workspace/Quant/backups/crypto-pmarp-breadth-20260929T015315Z`。
含旧HEAD/三个旧入口实现、cron前后快照、入口SHA、生产测试、独立复算、投递确认与首期输入压缩归档。
首期输入归档SHA256：`8dddf09f272e23da0e2b29a82f8cc967ea854b813a73b1307b3f5b1c264b6ced`。

如需回滚，应在worktree中针对宽度入口恢复并验证，再按授权部署；保留缓存和审计证据，不回滚数据库，也不整体逆转合并节点以免影响同期Prosperity改动。

本地证据：`reports/crypto-pmarp-breadth-live-2026-09-29/`。
运行说明：`docs/runbooks/crypto-pmarp-breadth.md`。
