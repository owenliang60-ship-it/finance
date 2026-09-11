# 三指数 PE 云端隔离试跑：身份门未通过

## 结论

不是生产验收成功，也没有新的真实 PE PNG。只读复制和云端代码测试通过；source-only 试跑发现代码错误地将基金 filer CIK 当成公司身份，按前置门停止。未启动逐股基本面/HMC/拆股/FX网络补齐、C1历史聚合或PIT写入。

## 执行证据

- Boss“可以继续”批准的是隔离库最多3,000 HTTP/3小时；没有生产推广授权。
- 冻结代码：`6a34b0245d5a0e650b155f36bbf1885af6233e5b`；本地 feature 与云端独立 checkout 一致，生产 main仍为`4bb2906`。
- 云端根目录：`/tmp/finance-index-pe-trial-20260911/`。独立 git clone + 本地bundle，不推远端、不修改生产checkout；独立 `market.db`，非 symlink，SQLite只读backup，1,024,442,368 bytes，quick_check=ok。
- 估值日：2026-09-10（三ETF当前价格共同截至日）。
- 云端85项相关测试通过。source进程 07:35:35–07:37:10 UTC，约95秒，正常结束；source子预算150次/600秒。
- 实耗 **48次FMP HTTP**（含重试计数）；总预算尚余2,952次，不代表自动批准另一个新3小时窗口。原窗口截止10:35:35 UTC，此前也已主动停止新请求。

| 篮子 | 原始披露 | 规范化快照 | 结果 |
|---|---:|---:|---|
| SPY | 1 | 0 | 2021Q1 TERN alias基金CIK不匹配，后续披露未拉 |
| QQQ | 22 | 23（含live） | 源保存完成，身份门失败 |
| SOXX | 20 | 21（含live） | 源保存完成，身份门失败 |

source脚本进程退出0仅表示按篮子收集诊断完成，不代表身份/产品验收PASS。`source-report.json`记录SPY failed，其余complete指源采集完成；`source-inspection.json`记录跨成分同CIK及live身份缺口。最终裁定为 **SOURCE_IDENTITY_GATE_FAILED**。

## 发现与解释

1. `cik` 是 filer：QQQ `0001067839`，SPY `0000884394`，SOXX `0001100663`。QQQ对应实体经[SEC filing index](https://www.sec.gov/Archives/edgar/data/1067839/000094040023000455/0000940400-23-000455-index.htm)核实。当前 verifier 将此误作公司身份，会把整个篮子识别成一家公司。
2. 可用的 issuer LEI 候选为3,217/3,336股票行；119行缺失/N/A，尚未逐条证明身份。LEI、CUSIP/ISIN分别是发行人和证券维度，不能互相冒充。[SEC字段定义](https://www.sec.gov/Archives/edgar/data/2081199/000141036826021333/xslFormNPORT-P_X01/primary_doc.xml)
3. alias被基金CIK错误限定到SOXX；live `securityCusip`没有被读取；持久化披露没有LEI/assetCat/raw字段，需明确schema修复而非临时伪填数据。
4. 两行QQQ期货误入股票集合（NQM6/NQU6），金额很小但说明instrument分类未贯穿真实披露。

`get-api-docs` 的 chub目录没有FMP条目（完整名无结果，fmp只有无关FFmpeg条目）；因此使用保存的实际响应和SEC原始文档核实，不根据文档猜字段。

## 完成的前置测量

GOOGL/GOOG 在2021-09-10至2026-09-08的 **1,248个同日正市值观测全部完全相同**；1% band全部通过，无需为这组调阈值。这只验证双类HMC约定的一致性，不证明市场数据本身绝对正确。

FOXA/FOX 1,248对、NWSA/NWS 1,253对存在差异；差异本身不能证明“各类市值应相加”，仍需结合股数/公司总值核验，不把本次比值统计当约定认证。DISCA/DISCK 无同日市值对，仍是待补缺口。

## 产物与后续

本地 `reports/rendered/index-pe-trial-20260911/` 保存两个操作脚本、48个原始响应、source日志/报告/检查结果、哈希清单。云端副本保留，生产库和cron未改，无后台进程继续运行或新增自动跟进；`basket_weekly_pe_history`及`fmp_basket_valuation`均0行。

按 `writing-plans` 和工作区“设计变更先审批”规则，下一步先审批源身份修复计划。该修复改变原来的身份契约，需要证据驱动，不能直接删掉失败门。详 `docs/plans/2026-09-11-index-pe-source-identity-repair.md`。
