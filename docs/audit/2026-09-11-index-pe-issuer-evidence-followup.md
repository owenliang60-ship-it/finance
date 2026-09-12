# 三指数发行人证据补齐：冻结历史窗口身份门已通过

## 最新状态（2026-09-11 19时）

Boss要求小修不再单独开计划，随后“搞，继续”。`c7595c6`完成带类型的发行人键与独立核验；`a85d883`加入34条SEC审核规则，覆盖原29个缺口证券以及DISCA/DISCK、UAA/UA两个历史股类组。原12条GLEIF审核记录保留。

- 从云端冻结导出的14,643条source记录重建规范化字段；SPY10,580、QQQ2,124、SOXX583条纳入/covered记录身份门全部通过，producer与独立verifier **13,287条一致**。
- 原始payload hash仍为`196383f18b44327b26e05828de0f7408deee8b76a37a6b5a19d264b613e0f1a6`；未改持仓原文、公开日期或预测vintage。
- SEC证据只采用issuer/subject company或该公司自身申报主体，拒绝基金filer和持股申报人。无LEI结果不被表述为“公司没有LEI”。错误子公司LEI只在精确证券/有效日期/预期原值范围内纠正，不全局合并母子公司。
- DISCA/DISCK与UAA/UA的**同公司身份已确认，类间市值约定未确认**；与FOXA/FOX、NWSA/NWS一样继续按未知约定排除，权重仍计入覆盖分母。身份证明不能替代市值证明。
- 相关438tests通过；独立数据副本全量 **3530 passed / 4 skipped**；Python3.10 AST、Ruff未定义名及diff检查通过。
- FMP累计仍71/3000。原云端窗口10:35:35Z已结束；续期开窗问题已发给Boss，未获答复不新增云端API或回填。接下来仅在冻结DB的本地隔离副本上做数值验收；**身份门通过不代表五年数值已认证或已上线**。

最新证据：`reports/rendered/index-pe-trial-20260911/evidence/canonical-source-check.json`、`canonical-normalized-sources.json.gz`及`docs/references/index-pe-sec-issuer-evidence-20260911/`。以下保留17时的调查与停点记录，不再代表当前剩余身份缺口。

## 冻结真实数据离线验收（19:36）

原云端隔离DB完整复制到本地（1,037,307,904字节，双端SHA256为`d68e992883f9a1f8b461305a5aebf51772cb5c100e687042726add2ea7904a3b`，源端无非空WAL）。运行全程禁用HTTP，代码只写这份任务自己的本地测试副本。

先发现HONA 2026-06-26市值0使QQQ/SPY/PIT整批异常，按Boss小修直接处理要求以TDD修复，提交`d7110cd`；不改原始0值，改为隔离该日期并保留可信比较基准，见issue073。修复后全量 **3538 passed / 4 skipped**；Python3.10 AST、Ruff未定义名、diff检查通过。

| 篮子 | 已认证周记录 | TTM可发布周 | 后视镜可发布周 | 9/10 TTM | 9/10后视镜尾部 |
|---|---:|---:|---:|---:|---:|
| SPY | 262 | 58 | 119 | 25.69× | 20.37× |
| QQQ | 262 | 58 | 119 | 30.52× | 23.05× |
| SOXX | 251 | 0 | 0 | N/A | N/A |

**周记录认证通过并不代表五年覆盖验收通过。** 三个basket的sample=50独立verifier均通过；NULL被正确保留，未用“complete”状态冒称数值齐全。SPY/QQQ TTM首个有效周2025-08-08；后视镜首个有效周2024-06-07。9/10后视镜是2026-09-05共识补尾，不是当年PIT分析师预测。TTM分位仅基于现有58个有效点，不可称为完整五年分位。

SOXX当前市值覆盖79.49%、权重覆盖85.54%：ASML/ASX/TSM/UMC因fx_daily为空而缺失，KLAC因市值状态隔离；均未达到90%。本地没有擅自补固定汇率或改正确市值猜测。旧研究PNG/CSV是不同生成链路的派生结果，不能反向当raw填进本次认证产品。

九个现有weekly源vintage（7/13至9/5）均做PIT计算，按六篮子整批契约全部拒绝并回滚：各期SOX未过NTM门，7/13—8/1另有IGV未过、8/8另有XLF未过；最新9/5只剩SOX。**PIT产品表仍0行**，不单独将SPY/QQQ候选值发布。

- 354个可发布数值经独立SQL从成员JSON重算Σ市值/Σ盈利，误差容忍1e-12内全部一致。
- 14,643条source原始payload、公开/生效/抓取日期、原始CIK/LEI/CUSIP/ISIN全部未改；HONA原始0值保留；SQLite quick_check=ok。
- 复跑耗时SOXX38s、QQQ85s、SPY398s；相关孤立验证worktree已清理，任务原worktree/副本/证据保留，无后台进程。
- PNG：`reports/rendered/index-pe-trial-20260911/index-pe-frozen-real-data-coverage.png`，已目视检查；图内标出各线有效周数，SOXX及PIT空白，不是完整交付也未接入生产晨报。
- 物证：`evidence/offline-valuation-result.json`保留原失败；`offline-valuation-result-mcap-fix.json`为修复后结果；`offline-independent-audit.json`为独立SQL/source核对；`full-tests-d7110cd.log`保留全量结果。

下一步是原计划内真实源数据补齐（历史income、市值缺口/修复、split、FX），不是再开小修计划。原3小时云端窗已到期，**续窗待Boss答复**；累计FMP仍71/3000，未自动续时/增额；未merge、push、部署或发Telegram。

## 已完成结果

2026-09-11 Boss要求“继续”后，在原方案A范围内完成首批人工审核补证，提交`78411b9`，没有改估值公式或原始source记录。

- **12个证券、133条持仓记录**得到有效范围内的审核身份证据；缺口从41证券/463行降到 **29证券/330行**。
- 证券：AAL、ACN、APA、CEG、CMG、INVH、KMX、MRVL、NEE、RIVN、SNDK、VNT。
- 本地相关 **416 passed**，新增审核证据测试先RED后GREEN；云端30项相关测试通过。
- 云端只读对拍：producer resolver和独立verifier对全部新增133行的LEI完全一致；源表前后hash相同，两张PE产品表仍0行。
- 批量公开GLEIF查询65次，补充名称查询3次（另有少量文档/单次探查）；**本轮FMP新增0次，累计仍71/3000**。这些查询不写生产库，不发Telegram，不改cron。
- 新增审核JSON及12份冻结证据位于`config/baskets/issuer_identity_overrides.json`和`docs/references/index-pe-issuer-evidence-20260911/`。每条含精确CUSIP+ISIN、valid_from/to、reviewed_at、source URL、冻结文件SHA。

| 篮子 | 补证前缺口行 | 补证后缺口行 | 状态 |
|---|---:|---:|---|
| SPY | 374 | 280 | FAIL |
| QQQ | 58 | 37 | FAIL |
| SOXX | 31 | 13 | FAIL |

这是**现在审核的历史实体映射**，不冒称这些LEI在历史持仓日已经注册或可见，也不改变当期持仓、acceptedDate或分析师预测vintage。例如[Marvell GLEIF登记](https://api.gleif.org/api/v1/lei-records/254900WVU0BM7ZCJ9E93)初始注册日为2026-02-20；[SEC 2021Q2持仓记录](https://www.sec.gov/Archives/edgar/data/866780/000086678021000004/xslForm13F_X01/13FTothQ22021updated.xml)已列明同一Marvell普通股CUSIP 573874104。补证不是回填一份不存在的2021年LEI观测。

## 为什么不能把其他候选自动填进去

本次实际调用[GLEIF公开接口](https://www.gleif.org/en/lei-data/gleif-api)做按ISIN和按LEI的查询，并逐个看法律实体名称。**映射结果、校验位正确或共同地址都不等于上市发行人身份正确。**

| 证券 | 候选LEI/查询结果 | 核查结果与处理 |
|---|---|---|
| CTAS | 549300E27V6N4OJCJ944 | [登记主体是LATM Management Company LLC](https://api.gleif.org/api/v1/lei-records/549300E27V6N4OJCJ944)，不能直接当Cintas Corp |
| KHC | 5493003STKEZ2S0RNU91 | [登记主体是Kraft Heinz Foods Company](https://api.gleif.org/api/v1/lei-records/5493003STKEZ2S0RNU91)；[上市母公司的记录](https://api.gleif.org/api/v1/lei-records/9845007488EC87F5AF14)不同，且带SEC CIK 1637459的validation authority。原数据两者混用，暂不覆盖冲突值 |
| STE | 549300BRDKZ1HFI2J358 | [指向STERIS IRISH FINCO UNLIMITED COMPANY](https://api.gleif.org/api/v1/lei-records/549300BRDKZ1HFI2J358)；按股票ISIN的另一次映射返回STERIS LIMITED，法律实体/重组关系必须核实 |
| BKR | 549300XU3XH6F05YEQ93 | [登记名为BAKER HUGHES HOLDINGS LLC](https://api.gleif.org/api/v1/lei-records/549300XU3XH6F05YEQ93)，与披露股票发行人名称不同，需核对法人重组和母子关系，不直接填 |
| EXPE | CI7MUJI4USF3V0NJ1H64 / 2549009GW3Z3DQCB8H87 | [Expedia Inc](https://api.gleif.org/api/v1/lei-records/CI7MUJI4USF3V0NJ1H64)与[Expedia Group Inc](https://api.gleif.org/api/v1/lei-records/2549009GW3Z3DQCB8H87)不同实体，不能按相似名称合并 |
| CSGP | RDQ0UPSUOQL9Y4XV6T33 | 当前GLEIF单记录请求404；另有CoStar Group的有效记录。404只意味着未核实，不证明旧编号不存在或属于同一实体 |
| MTSI | 名称搜索命中254900DATW3ZWYAKZM76等 | [该记录法律名是MACOM TECHNOLOGY SOLUTIONS INC](https://api.gleif.org/api/v1/lei-records/254900DATW3ZWYAKZM76)，虽列Holdings为交易名，不能据此把运营公司和上市Holdings当一个法律实体 |
| OLED | 名称搜索唯一命中Universal Display & Fixtures Company | 不是本项目的Universal Display Corporation；按股票ISIN查询无结果，拒绝相似名称误配 |
| CRDO | 名称/ISIN未命中 | 不能据此证明没有LEI；但[SEC明确列示发行人及CUSIP G25457105](https://www.sec.gov/Archives/edgar/data/1807794/000162828025018546/xslSCHEDULE_13G_X01/primary_doc.xml)，[SEC header明确区分subject company CIK 1807794与reporting-owner CIK](https://www.sec.gov/Archives/edgar/data/1807794/000195004726000043/0001950047-26-000043-index-headers.html)，存在另一条权威身份证明路径 |

以上“有值但不对应上市主体”的疑点可能发生在已经非空的source记录，**不是330条缺失行的同义表述**，也不能用补完缺失计数宣称所有身份正确。

## 原schema的结构性限制

- `load_issuer_overrides`强制有效LEI、CUSIP和ISIN同时存在；source CUSIP=N/A/000000000时，即使完整ISIN或SEC发行人记录已可证明身份，也无法表达。
- 现有逻辑只允许补缺失LEI，不能对有明确权威证据的误填LEI做证券级定点纠错；这是当前批准契约，因此本轮未绕过。
- 直接增加另一种ID而不归一也不行：同一公司A类用LEI、C类用CIK，可能绕过公司去重。母子公司又不能因为有关系就全局归一。

因此提出`docs/plans/2026-09-11-index-pe-verified-issuer-key.md`：允许经SEC核实的issuer CIK、受约束的ISIN-only匹配，以及保留原文的定点纠错；仍要求所有纳入成员可核实、同实体统一键、冲突拒绝。**此修订未实施，待Boss批准。**

## 剩余名单

- SOXX：CRDO、MTSI、OLED（13行）。
- QQQ：CTAS、FER、KHC、LCID、SGEN（37行）。
- SPY：AMTM、APTV、BKR、CSGP、CTAS、CTLT、CVX、EXPE、EXR、J、KHC、LH、NCLH、PENN、PHM、RAL、STE、TDG、TEL、TKO、TPL、VNO、WRK（280行）。

另保留DISCA/DISCK、UA/UAA历史股类与FOXA/NWSA市值约定待核项；不因为新增身份证据就自动采用两个股类市值相加。

## 证据位置与运行边界

- `reports/rendered/index-pe-trial-20260911/issuer-research/`：65次批量查询的结果/失败记录与3次名称查询；`research_issuer_registry.py`保留采集过程。
- `.../evidence/identity-reviewed-update.json`：330行剩余明细、新解决133行及原始hash；`verify_reviewed_issuers.py`为只读复核脚本。
- 本地feature及云端临时checkout到78411b9；生产main仍未动。无生产写入、部署或后台定时任务；PE图仍未生成。
- FMP余2929次；原云端试跑窗口截止2026-09-11T10:35:35Z，继续不自动重领额度或续时间。
