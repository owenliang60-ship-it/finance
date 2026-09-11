# Issue 072: 基金披露的 filer CIK 被当成成分公司身份

**Status**: OPEN — 云端隔离试跑在身份预检停下，修复方案待审批
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
