# Issue 072: 基金披露的 filer CIK 被当成成分公司身份

**Status**: IDENTITY FIX VERIFIED — 冻结历史窗口身份门已通过；真实估值与上线验收仍未完成
**Date**: 2026-09-11
**Severity**: HIGH — 三指数历史 verifier 必然误判、SPY alias 预检失败
**Related**: `src/data/fmp_forward_ingestion.py`、`scripts/verify_index_pe_history.py`、`config/soxx_symbol_aliases.json`；issue045/048

## 真实复现

以 `6a34b02` 在云端生产 DB 的 SQLite backup 副本试跑，只调用披露源端点，累计48次HTTP：

- SPY 2021Q1 返回506行，同一 `cik=0000884394`；其中 Teradyne `symbol=TERN`、CUSIP `880770102`、ISIN `US8807701029`。alias 写死 SOXX 的 `0001100663`，在 SPY 上立即报 `CIK mismatch for configured alias TERN`。
- QQQ 2021Q1 中 AAPL/NXPI/GOOG/GOOGL 等公司共享 `cik=0001067839`。它是 [Invesco QQQ Trust 的申报 CIK](https://www.sec.gov/Archives/edgar/data/1067839/000094040023000455/0000940400-23-000455-index.htm)，不是成分公司 CIK。SOXX 对应 `0001100663` 同样是基金主体。
- `_company_identities` 直接把该字段映射成 ticker→公司身份，`_company_identity_check` 会把几乎整个 ETF 判成同一家公司。要求字段非空的测试并不能验证它代表谁。
- 同次发现：live 返回的是 `securityCusip` 而非 `cusip`，且无 cik/lei；当前 normalizer 丢掉这个 CUSIP。披露有 `lei` 和 `assetCat`，当前表也没有保存这两列或完整 raw payload。

原始披露的 `lei` 是发行人维度；[SEC N-PORT C.1](https://www.sec.gov/Archives/edgar/data/2081199/000141036826021333/xslFormNPORT-P_X01/primary_doc.xml) 明确区分 issuer LEI 和证券 CUSIP/ISIN。本次43份原始披露含3,336行 `assetCat=EC`，其中3,217行有20位字母数字 LEI 候选，119行缺失/N/A。这里只做形状统计，**不是119行已解决、也不是3,217行已全部经独立身份核验**。

## 相邻缺陷

QQQ 2026Q1/Q2 的 NQM6/NQU6 是 `assetCat=DE` 的 Nasdaq期货，当前却 `included=1`。权重分别 -0.0032286%/+0.0017111%，量很小，但非股票可进入逐股财报请求。原因：披露 adapter 丢 assetCat，名称期货过滤又仅覆盖空 ticker。

## 为什么此前测试没抓住

端点 fixture 已有多个成分共享基金 CIK，但 verifier 合成数据把 cik 填成按公司不同的值；各模块各自为绿，却没有真实端点形状贯穿到 verifier 的契约测试。本次云端身份前置门按设计阻止了继续回填，未产出错误估值。

## 修复边界

方案见 `docs/plans/2026-09-11-index-pe-source-identity-repair.md`。保留 filer CIK 原义，另做 issuer identity 与精确证券标识；不得用放宽覆盖率、删除 duplicate-company 检查、给每个 ticker 编造不同 CIK 的方式过关。LEI 缺口与历史多股权仍需证据，不自动猜测。

本轮只在临时库落入44个规范化快照（QQQ23、SOXX21；SPY0），两张估值表仍为0行，生产库未写入。原始响应、脚本及哈希保存在 `reports/rendered/index-pe-trial-20260911/`；完整结果见同日 cloud-trial audit。

## 后续实施（上段为修复前停点）

Boss批准方案A后，`dc61062`修复源字段、精确证券alias、LEI/审查证据解析与独立verifier；additive迁移无数据删除。12个真实样本+故障注入通过，核心修复全量3505 passed/4 skipped。离线重放45快照成功，再用23HTTP补齐SPY，总计71次；67个源快照、估值表仍0行。

实际五年窗口仍有41证券/463行身份缺口，另有DISCA/DISCK、UA/UAA未登记股类；保持100%身份门，未开始逐股网络补齐。`26cec1b`还撤销FOXA/NWSA未证实的市值相加约定：HMC/close对应公司整体股数，不能凭两类市值不同就求和。保守配置414项相关测试通过。

完整证据与待核名单见 `docs/audit/2026-09-11-index-pe-source-identity-repair.md`。代码修复完成不等于数据已认证，更不等于项目已上线。

最终`26cec1b`隔离全量3506 passed/4 skipped、云端91 passed，零失败；原始数据缺口继续阻断发布，不删除或伪填未知身份。

## 后续补证与LEI边界（2026-09-11 17时）

首批12证券审核证据已提交78411b9，相关416tests、云端30tests及133行独立身份对拍通过，原表hash不变；缺失41证券/463行→29证券/330行。FMP新增0次（累计71）。

查证同时发现LEI不只是缺值：CTAS候选指向LATM Management LLC，KHC混入食品子公司，STE候选是融资实体；有些公司仅搜到不相干名称。合法LEI校验位不能代替上市发行人验证。现有LEI+双证券编号强制schema无法表达所有权威身份证据，已写待批的verified-issuer-key修订，未私自放宽。详`docs/audit/2026-09-11-index-pe-issuer-evidence-followup.md`。

## 身份缺口关闭（2026-09-11 19时，前文停点已被替代）

Boss要求小修不再另卡计划审批并指示继续后，`c7595c6`实现typed LEI/SEC issuer CIK、严格受约束的ISIN-only匹配、保留原文的证券级定点纠错及独立verifier；`a85d883`补34条SEC审核记录和两个历史股类组。原46条审核记录均有冻结证据。

冻结导出source中SPY10,580、QQQ2,124、SOXX583条纳入/covered记录，身份门全部通过；13,287条独立核验一致、原始payload hash未变。相关438tests、隔离全量3530 passed/4 skipped。发行人身份修复已验证，但未知market-cap convention继续排除，不把身份门PASS扩大成数值认证或生产LIVE。云端仍停在原授权窗口，FMP累计71次；本地冻结副本数值试算另记验收结果。
