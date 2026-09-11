# 三指数PE续窗验收记录

## 最终结果（22:39认证完成，22:42 PNG检查通过）

**隔离验收完成；尚未合并、push或部署晨报。** 代码验收版本93c8a6f，full-suite **3563 passed / 4 skipped**，云端相关173passed。当前工作分支相对main4bb2906包含此前整条PE交付，不只是本轮小修。

| 篮子 | TTM有效周 | 后视镜有效周 | 历史开始日 | 9/10 TTM | 9/10后视镜尾部 | 最新真实PIT NTM（9/5） |
|---|---:|---:|---|---:|---:|---:|
| SPY | 262/262 | 262/262 | 2021-09-10 | 25.80× | 20.38× | 20.40× |
| QQQ | 262/262 | 262/262 | 2021-09-10 | 30.71× | 23.04× | 23.21× |
| SOXX | 251/251 | 251/251 | 2021-11-26 | 36.72× | 19.31× | 19.14× |

- 三篮子最终只读verifier **15项全部PASS**，sample=50；九个weekly vintage（7/13–9/5）的六篮子PIT全部通过，54个NTM值全部发布。8/8 XLF已恢复；辅助blend仍允许独立NULL，不把它当主线失败。
- 独立SQL重算 **1640个数值，零差异**（容忍1e-12）；14,643条原始披露及日期/证券字段保持不变，62行仅派生alias元数据更新。SQLite quick_check=ok。
- 最终累计FMP HTTP **1693/3000**；绑定修复后的重跑新增0HTTP。最终云端验收在本轮22:51:48截止前结束；没有后台进程、cron或heartbeat继续工作。
- 最终PNG为1800×1510 RGB，约175KB，目视检查文字、折线、虚实线、日期与空白边界通过；晨报HTML小节预览使用现有renderer，PNG以data URI嵌入，不依赖本地绝对路径。
- 文件：`reports/rendered/index-pe-trial-20260911/index-pe-weekly-certified-20260910.png`；HTML为该目录`morning-preview/morning_report_2026-09-10.html`。此前部分PNG保留，但已被这版替代。
- 完整证据在本地`evidence/binding-final/`（最终只读检查、逐篮/逐期结果、代码绑定变更与绘图数据）；原始响应包`renewed-input-evidence.tgz` SHA256为`339aba6d4c253b1ca4b0a4e2c9ee324f085fb72481d8b23f25d8996aa5e0305e`，本地与云端一致。绘图JSON与最终认证JSON双端hash亦一致。

### 上线前必须知道的边界

1. 五年紫线是**后视镜**，近期虚线用最新共识补尾；真正PIT仅来自已经留存的9期，不伪造过去五年的历史分析师预测。三线均为Σ公司市值/Σ盈利，非官网口径，分析师共识不承诺GAAP等价。
2. 100%有效周不等于每个公司都100%完整。最新SPY权重覆盖98.69%/98.74%、QQQ98.65%/98.95%、SOXX100%/100%（TTM/后视镜）；无法验证的公司继续排除，完整清单在最终证据中。
3. **C1运行成本高于原粗估**：SPY本次零HTTP整窗组装从13:44:24至14:30:24 UTC，约46分钟，另有认证/PIT耗时。部署时不能沿用Phase1的95–105分钟SLO来描述加上历史整窗后的整条任务；周六链路需要更长窗口。未擅自改成增量计算或降低sample。
4. 生产代码仍4bb2906，生产库中的KLAC/MCHP/BRK-B等旧数据未被本轮写改。批准上线后须在writer lock与备份下推广已验证的源修复、历史产品及PIT，再做生产验收；不能直接覆盖整库或只复制PNG冒充上线。

以下保留过程记录；其中“进行中/待重拉”的描述均为历史停点。

## 授权和边界

Boss在看过部分真实PNG后回复“继续”，确认续开最多3小时云端隔离验收。

- 本轮窗口：2026-09-11 **11:51:48–14:51:48 UTC**（19:51:48–22:51:48 CST）。
- 累计FMP HTTP上限3000，从先前已用71继续累加；失败重试也计数，不重新领额度。
- 临时代码目录：`aliyun:/tmp/finance-index-pe-trial-20260911/code`，起始版本`641c29d`，云端79项相关测试通过。
- 新副本：`renewed-market.db`，由旧隔离库`identity-replay.db`只读SQLite backup生成；不是生产库或symlink。
- 沿用`resource-market_db_writer.lock`防止与生产FMP作业并发；不调用cron wrapper，不改cron、不发送Telegram。
- 运行脚本`renewed_trial.py`复用既有backfill与两个估值内核，不另写计算逻辑。每个HTTP发出前持久记录次数；截止时间和累计次数同时硬限制。相同请求的income/splits/FX在本轮缓存，市值强制重拉不缓存。
- 生产代码仍为`4bb2906`；未merge/push/部署，生产原始市值未修写。

## 进行中实证

SOXX完成第一轮：251个可核验周的TTM和后视镜均可发布，sample=50独立verifier全通过；最新9/10市值及权重覆盖均100%。源补齐与计算共345秒。QQQ/SPY继续执行，尚不能视为全项目完成。

实际供应商重拉已返回KLAC 6月异常窗口的正确量级（例如6/10从279.57亿变为2795.69亿），以及MCHP 2月异常的修正值；不是本地把旧数乘10或除2。KLAC 6/12 10:1拆股记录已入试跑库。EUR/TWD分别1382/1387行，均覆盖2021-09-03至2026-09-10。NVDA/TSM/MU/ASML各40季财报及公开日期已补齐。

所有原始HTTP响应、次数ledger及逐篮结果保存在云端`evidence/renewed/`；本地最终同步和独立SQL对账待全轮结束后补记。

## 普通回填的新坏响应修复

QQQ在HTTP attempt310收到HONA 53条市值，其中6/26仍为0。原写门拒绝正确，但异常导致整批停止；本地提交`803ba91`，把原范围校验提取为无副作用共享函数。坏响应的完整区间拒绝、记录原因、原内存/DB保持；正常回填的坏响应计入实际尝试分母熔断，强制重拉保留skip哈希证据。数据库故障继续抛错，不当作vendor数据错误。详issue073。

12项RED→GREEN；相关413tests、最终全量 **3550 passed / 4 skipped**，Python3.10 AST及Ruff通过。原云端采集进程继续跑原641c29d，没有热改；结束后再用新代码和新run_id在同一DB继续。续跑从前一批已结束的HTTP ledger与结果交叉读取累计用量，沿用原14:51:48Z截止，不重新计时或清零。

## 第一批结束与最终副本

第一批于12:30:46Z结束，累计847次、client计数与HTTP ledger一致、quick_check=ok。QQQ/SPY因HONA新响应0值失败，SOXX251周双线通过。临时诊断脚本仍继续计算并冻结了已通过六篮子门的八期PIT；8/8因XLF拒绝。**这不是生产执行顺序**：生产wrapper在history失败即停止。旧临时结果保留，不作为最终交付。

最终wrapper已修正为三个history basket全部完成后才冻结PIT。为不改写第一批已冻结的PIT，实际改为建立新的`renewed-final.db`：以原始无产品baseline备份为骨架，仅导入第一批的五张已采集source表，保留原`renewed-market.db`完整不动。没有清空或重写旧PIT；预算和截止时间仍继承同一窗口。

新代码803ba91云端95tests通过；`evidence/renewed-resume/`保存最终轮的记录，每次新HTTP从848开始。成功income/split/FX原始响应按相同参数复用，market-cap强制重拉不复用缓存。8/8 XLF主要缺口为BRK-B的11.79%权重被市值隔离，另有APO/CBOE；它们均在SPY数据修复范围，待最终sanity检查再判，不放松权重门。

21:15更新：最终轮SOXX251周、QQQ262周双线全部通过；SPY拆股采集已完成，正在sanity重拉。BRK-B 8/5–11供应商新响应已恢复约1.1万亿美元量级（见issue074），没有手工乘倍率，等待最终PIT门确认。

## 证券到财报主体的补充核验

21:19第二批止于SPY的AUD FX失败，累计1693次且计数一致。核查不是缺少合法币种：SPY历史MOB实际是Monster，却拉到了Mobilicom财报。扩扫同ISIN多ticker发现7组不同财报CIK、2组旧查询无数据；全部SPY历史source。没有增加AUD白名单，而是按精确CUSIP/ISIN修正9个查找绑定，并加入expected issuer CIK和旧alias元数据预检（issue075）。

代码93c8a6f：13项新测试RED→GREEN，相关173及云端173通过，最终全量3563 passed/4 skipped。`binding_final_trial.py`在renewed-final.db上只更新派生alias字段，保留raw ticker/LEI/CIK/证券编号/日期/权重，SPY用新run_id重跑；PIT表此前仍0，不重写冻结vintage。当前这轮禁止HTTP，累计仍1693；22:51:48硬截止不变。

再次按raw_symbol分组共22组同ISIN多代码（首次按规范化symbol为21组），财报CIK冲突7→0，证据`evidence/binding-final/security-symbol-sweep.json`。22:17时SPY已完成200个周点，最终整批认证和PIT仍待结束。
