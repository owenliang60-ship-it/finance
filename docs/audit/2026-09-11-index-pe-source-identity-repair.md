# 三指数源身份修复：代码验收与数据缺口

## 当前裁定

方案A的代码已实现，真实源采集/离线重放完成；**历史身份门仍FAIL，没有新的真实PE图，也未接入生产晨报**。这不是把先前错误结果换个标签发布，而是保留缺口，不计算未通过的产品。

代码：`dc61062`（身份修复）+`26cec1b`（撤销未证实的FOX/News类间市值相加）；均仅在feature分支与云端临时checkout，main/生产仍`4bb2906`。

## 实现与测试

- 原始filer CIK保留；additive增加issuer_lei、asset_category、raw_payload_json；有旧行也能正常初始化Store，不猜旧身份。
- live读取securityCusip，双CUSIP冲突拒绝；明确期货无论ticker是否非空都排除，未知披露资产类别不当股票。
- LEI格式/校验位与原始字段一致性检查；人工审核补充接口必须精确证券标识、有效范围、来源链接/hash、审核日期，不能覆盖冲突源值。生产配置中的例外列表仍为空。
- producer在逐股请求前检查；verifier独立解析原始payload，按具体holding_date/source_kind/证券映射，不复用producer身份resolver。两侧均不再使用基金CIK作issuer键。
- TERN按CUSIP+ISIN跨ETF匹配；CREE保留精确旧证券标识与更名证据。更名日期核实于[Wolfspeed发行人公告](https://investor.wolfspeed.com/news/news-details/2021/Cree-Inc.-Officially-Changes-Company-Name-to-Wolfspeed-Inc.-Marking-Successful-Transition-to-Global-Semiconductor-Powerhouse/default.aspx)。
- 12个真实源样本带原始响应SHA，初始15 RED/1 PASS；补充证券错配、校验位占位符、override冲突/有效期与跨快照证据借用测试。原合成fixture现在明确区分基金CIK和模拟issuer，不修改真实fixture去“适配”错误。
- `dc61062`隔离数据副本全量 **3505 passed / 4 skipped**（335.82秒）；保守市值配置修订后相关 **414 passed**。最终`26cec1b`全量复跑 **3506 passed / 4 skipped**（310.46秒，17个既有数值/弃用警告），零失败。
- 云端首轮相关38 passed，最终代码相关 **91 passed**；Python3.10 AST、Ruff未定义名和diff whitespace检查通过。主线程单遍检查；未启动subagent。
- 全量测试在临时worktree的数据副本上，三数据库使用SQLite backup，不在主数据symlink上跑。

## 真实试跑与预算

前轮48次FMP响应被离线重放，**45个快照全部成功**，SPY原先的TERN alias错误已解除。随后追加23次HTTP补齐SPY。总计 **71 / 3000次**，剩余2929次；原3小时窗口截止2026-09-11T10:35:35Z，不自动续期。无逐股income/HMC/split/FX网络回填。

源快照总数：SPY23、QQQ23、SOXX21（各含9/11 live）。估值日9/10，live晚于估值日，不回填穿越；实际五年窗口使用SPY21、QQQ21、SOXX20个披露快照。三个篮子的前置身份门均不通过。

| 篮子 | 窗口内已解析股票行 | 未解析股票行 | 涉及证券数 |
|---|---:|---:|---:|
| SPY | 10206 | 374 | 33 |
| QQQ | 2066 | 58 | 7 |
| SOXX | 552 | 31 | 4 |

跨篮子去重为 **41个证券、463条持仓记录**。解析成功表示LEI通过格式/校验位及源字段一致性门；不宣称每行都获得独立外部身份认证。source SQL与vendor数据同源，不能证明所有vendor映射天然正确。

最新2026Q2披露：QQQ98/98行issuer可解析；SPY492/502，未解析权重约0.956%；SOXX28/30，未解析权重约3.148%。这些低权重**不构成放宽100%身份前置门的授权**，历史其他日期也仍有缺口。

## 历史双股权与市值口径

- SPY真实源捕获DISCA/DISCK、UA/UAA同issuer。config未覆盖的组被正确报告，不删除去重门来通过；历史类间股数/市值仍未核验，DISCA/DISCK在副本中没有可对拍的同日HMC。
- GOOGL/GOOG的1248对历史同日市值全部相同，1%band通过；不改阈值。
- 2026-09-08 FOXA HMC/close≈431M股、FOX≈424M股，对应FOXA近两季company basic shares 431M/424M；NWSA和NWS各≈556.5M股，均等于NWSA当季company basic shares。**不同价格/市值不能证明它们是分股类市值。**
- 据此撤销FOXA、NWSA的`split_across_classes`声明，使用既有列表形状表示未声明convention；对应公司估值沿用现有fail-closed排除，未实现或猜测新的聚合算法。保留成员权重分母。恢复需核实各类股数与公司总市值。
- 新增TDD覆盖“真实配置未知时不得相加”；原split算法测试显式传入合成约定，避免将玩具场景当成真实FOX证据。

## 待核身份清单

行数为本轮窗口内的持仓记录数（可跨篮子重复），区间为缺口首次/末次持仓日，不等于公司无LEI的完整生命周期。逐行CUSIP、ISIN、权重、源类别与reason见`identity-final-gates.json`。

| 证券 | 篮子 | 缺口行 | 持仓日期范围 |
|---|---|---:|---|
| AAL | SPY | 13 | 2021-06-30 → 2024-06-30 |
| ACN | SPY | 8 | 2021-06-30 → 2023-03-31 |
| AMTM | SPY | 1 | 2024-09-30 → 2024-09-30 |
| APA | SPY | 5 | 2021-06-30 → 2022-06-30 |
| APTV | SPY | 16 | 2021-06-30 → 2025-12-31 |
| BKR | SPY | 21 | 2021-06-30 → 2026-06-30 |
| CEG | SPY | 1 | 2022-03-31 → 2022-03-31 |
| CMG | SPY | 18 | 2021-06-30 → 2025-09-30 |
| CRDO | SOXX | 1 | 2026-06-30 → 2026-06-30 |
| CSGP | SPY | 10 | 2022-09-30 → 2024-12-31 |
| CTAS | QQQ,SPY | 37 | 2021-06-30 → 2026-06-30 |
| CTLT | SPY | 14 | 2021-06-30 → 2024-09-30 |
| CVX | SPY | 21 | 2021-06-30 → 2026-06-30 |
| EXPE | SPY | 17 | 2021-06-30 → 2025-06-30 |
| EXR | SPY | 21 | 2021-06-30 → 2026-06-30 |
| FER | QQQ | 1 | 2026-03-31 → 2026-03-31 |
| INVH | SPY | 16 | 2022-09-30 → 2026-06-30 |
| J | SPY | 13 | 2022-09-30 → 2025-09-30 |
| KHC | QQQ,SPY | 6 | 2021-06-30 → 2022-03-31 |
| KMX | SPY | 18 | 2021-06-30 → 2025-09-30 |
| LCID | QQQ | 8 | 2021-12-31 → 2023-09-30 |
| LH | SPY | 9 | 2024-06-30 → 2026-06-30 |
| MRVL | QQQ,SOXX | 37 | 2021-06-30 → 2025-12-31 |
| MTSI | SOXX | 4 | 2025-09-30 → 2026-06-30 |
| NCLH | SPY | 21 | 2021-06-30 → 2026-06-30 |
| NEE | SPY | 11 | 2021-06-30 → 2023-12-31 |
| OLED | SOXX | 8 | 2021-09-30 → 2025-06-30 |
| PENN | SPY | 5 | 2021-06-30 → 2022-06-30 |
| PHM | SPY | 21 | 2021-06-30 → 2026-06-30 |
| RAL | SPY | 1 | 2025-06-30 → 2025-06-30 |
| RIVN | QQQ | 2 | 2022-12-31 → 2023-03-31 |
| SGEN | QQQ | 10 | 2021-06-30 → 2023-09-30 |
| SNDK | SPY | 2 | 2025-12-31 → 2026-03-31 |
| STE | SPY | 20 | 2021-06-30 → 2026-03-31 |
| TDG | SPY | 21 | 2021-06-30 → 2026-06-30 |
| TEL | SPY | 1 | 2024-09-30 → 2024-09-30 |
| TKO | SPY | 6 | 2025-03-31 → 2026-06-30 |
| TPL | SPY | 6 | 2024-12-31 → 2026-03-31 |
| VNO | SPY | 7 | 2021-06-30 → 2022-12-31 |
| VNT | SPY | 2 | 2021-06-30 → 2021-09-30 |
| WRK | SPY | 3 | 2021-06-30 → 2021-12-31 |

## 产物与边界

云端保留：

- `/tmp/finance-index-pe-trial-20260911/market.db`：首轮副本，保留原状；
- `identity-replay.db`：修复后重放及SPY补齐的第二隔离库，quick_check=ok；
- `evidence/`：71次请求对应响应/计数、重放与最终身份报告。

本地对应证据：`reports/rendered/index-pe-trial-20260911/`（包含replay_identity.py、complete_spy_sources.py、audit_identity_final.py、evidence-hashes.json与完整JSON；gitignored原始产物保留）。两个PE物化表仍0行。未push/merge主线/部署、未写生产库/cron、未发Telegram，无新后台监控。

按批准计划Task3停在真实身份证据门。后续先逐证券核实发行人身份与历史股类约定；未解决项保持UNKNOWN，不能伪造LEI或用ticker代替公司。当前不能宣称三指数晨报PE项目完成上线。
