# Issue 073: 单条非正市值导致整个指数估值批次异常

**Status**: FIX VERIFIED IN ISOLATED DB — 未部署生产
**Date**: 2026-09-11
**Severity**: HIGH — 一个成分的历史0值阻断其他成员与后续日期
**Scope**: `terminal/historical_market_cap_sanity.py`及历史/PIT估值调用方

## 真实复现

云端隔离试跑副本冻结后复制到本地，SHA256双端一致为`d68e992883f9a1f8b461305a5aebf51772cb5c100e687042726add2ea7904a3b`。在禁止网络的离线全流程中，QQQ、SPY均抛`ValueError: market_cap must be positive`。9份现有complete weekly源的PIT估值也被该异常阻断。

扫描原表发现7个证券共21条0市值记录；另有非公司指数^VIX的620条0值。QQQ/SPY历史目标集合与这些异常证券的交集为HONA，其2026-06-26的market_cap=0。本记录不判断上游为什么写0、不把它修成猜测市值，也不改生产库。

## 原因与修复

sanity扫描器原本把“值必须为正”当函数输入前提，在开始分类前抛异常，导致无法把本就要检查的坏数据归类并生成重拉窗口。修复为：

- 保留每个观察日期；非正/缺失/非有限值标`invalid_mcap`，候选原因`invalid_market_cap_value`，进入原有隔离及强制重拉流程。
- 不把坏值写入比较基准；后续数据必须重新符合可信股数状态才能恢复，不能因跨过坏行而接受新错误regime。
- 拆股落在坏行时，保留事件日期并延后至首个有效市值观察验证，不丢掉拆股也不重复调整。
- 下游as-of取值保持最新记录隔离语义，不能回退昨日市值绕门；原始行和覆盖分母保留。
- 数据写入CRUD仍拒绝无效的新市值。本修复只允许读取历史坏数据并正确分类，不降低写入约束。

## 验证

8项新增边界测试先RED后GREEN：0/负值/NULL/NaN/Inf、坏值跨拆股、错误新regime拒绝、首行0值不可作anchor；as-of不可穿透隔离回取旧市值。相关6套225tests通过，真实副本复跑结果另见同日离线验收。

原始证据在`reports/rendered/index-pe-trial-20260911/evidence/offline-valuation-result.json`及`offline-source-provenance.json`。修复后结果另存`offline-valuation-result-mcap-fix.json`，不覆盖失败物证。零新增API，未merge/push/部署。

`d7110cd`真实复跑：QQQ/SPY都不再抛异常，分别完成262周认证（58个TTM、119个后视镜有效周）；SOXX251周维持NULL，PIT因覆盖门而非异常拒绝。354个可发布数值经独立SQL核对一致，HONA原始0行保留。全量3538 passed/4 skipped，相关225passed，Python3.10 AST与Ruff通过。

## 续窗中的新响应边界（20:09）

上述修复解决读取已有坏行；真实网络续跑HTTP attempt310再次从FMP取得HONA 53行，其中2026-06-26仍为0。range-replace正确拒绝写入，却把QQQ整批中止。保留`evidence/renewed/http-response-0310.json.gz`原始响应。

本轮直接补齐同类失败路径，不另开计划：提取原CRUD纯校验供实际写入和network dry-run共用，非正/非有限/错symbol/date/重复date的完整响应区间均拒绝。普通mcap阶段记`invalid_responses`、保留原区间且计入实际尝试分母的>20%熔断；强制重拉记`skipped + rejection_reason + 相同pre/post hash`，不伪称修复成功。数据库写入故障仍向外抛，不能冒充坏vendor数据；内存只在真实写入成功后更新。

12项新增边界测试先RED后GREEN，413项相关回归通过。运行中的云端代码未热改；待原批次结束、修复完整验证后，以新run_id在同一批准窗口和累计预算内重跑。
