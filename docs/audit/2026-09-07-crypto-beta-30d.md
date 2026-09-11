# Crypto BTC Beta 30D 验收

已确认业务口径：币安USDT永续，昨日完整UTC交易日成交额前50，连续31收盘价产生30日简单收益率；beta降序，附相关系数。

- 本地 beta + crypto suite：30 passed。
- 云端 Python3.10 真实 dry-run：as_of=2026-09-06，528/528排名数据齐全，50入选、46有效，补K线24次。未发Telegram，未修改共享行情缓存。
- 独立 numpy.linalg.lstsq（含截距）对拍46个；最大beta差1.3322676295501878e-15，相关系数差<1e-10。
- 无效四个：MARSCOIN、PONS、哈基米、牛来；历史不足31日/缺日，保留排名位置显示N/A。
- 主线程单遍review：补Telegram旧Markdown特殊字符转义及daily异常冒泡测试，未留阻断项。
- 原daily入口SHA256：6df8b32566b01c65f996d2003a6a2ed199b4d7c8e789baaa4768e4ad6fa144e8。

## 部署步骤
Finance git版本部署后，在Quant现有cron锁下，验证旧入口哈希，备份入口，再复制薄适配器及新入口；原08:06 cron不变。回滚从备份恢复daily入口即可。

正式定时运行尚未自然触发；本次不额外手动发送榜单。

## 已部署 2026-09-07 10:56 北京时间

代码221f0b5已合并push并云端pull；云端30 tests passed，Python3.10编译通过。Quant入口在现有cron锁下完成备份和替换；薄适配器真实路径调用使用mock验证，未发送Telegram。

- 备份：`/root/workspace/Quant/backups/crypto-beta-30d-20260907T025621Z/daily_scan_all.py`。
- 新入口SHA256：`76370cb2d8f3cfcb0bad4f11e8babe54a167942973437a2adb9ac716300d1b55`，与Finance版本一致。
- crontab仍为`6 8 * * * /root/workspace/Quant/scanners/run_daily_scan.sh`。
- 正式成果位置：`/root/workspace/Quant/results/beta_30d/btc_beta_30d_YYYY-MM-DD.json`。
- 本次真实预览：`/tmp/crypto-beta-30d-check/results/btc_beta_30d_2026-09-06.json`；独立验证脚本同目录`independent_check.py`。

## 双榜升级验收

- Boss要求beta Top10 + 180×4h RS Top10；交集置顶单列+榜内双重⭐标记。
- 本地44相关tests通过，云端新增12 tests通过；真实dry-run成功（4h使用同日已冻结永续缓存），45个评分与独立研究结果一致。
- 今日交集USELESS（Beta#1/RS#1）、ZEC（Beta#5/RS#2）；单条消息796字符，未手动发送。
- 原PMARP/RVOL/NUPL停发范围已异步询问，未收到回复；在此决策完成前保留原三项，先替换已获授权的beta输出为双榜。
