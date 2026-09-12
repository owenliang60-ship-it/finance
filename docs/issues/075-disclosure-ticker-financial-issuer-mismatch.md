# Issue 075: 披露证券身份正确，但ticker查到了另一家公司的财报

**Status**: FIX VERIFIED IN ISOLATED DB — 生产待审批
**Date**: 2026-09-11
**Severity**: HIGH

## 发现

最终轮SPY因AUD FX门失败。追查发现AUD不是SPY真实成分的新币种：披露的MOB实际写着Monster Beverage、CUSIP61174X109、ISIN US61174X1090；MOB income却来自Mobilicom CIK1898643。Monster自己的[SEC文件](https://www.sec.gov/Archives/edgar/data/865752/000110465926070506/tm2616810d1_8k.htm)确认MNST，[Mobilicom SEC目录](https://www.sec.gov/edgar/browse/index.html?cik=1898643)确认MOB属于另一公司。因此未扩AUD白名单。

扫描667个已拉财报symbol、21组同ISIN多ticker记录，发现7组财报CIK不相交，另2组旧查找无数据：

| 原披露ticker | 本证券的财报查找 | 精确CUSIP | 目标issuer CIK |
|---|---|---|---|
| MOB | MNST | 61174X109 | 0000865752 |
| GNE | GE | 369604301 | 0000040545 |
| SYM | GEN | 668771108 | 0000849399 |
| OCN | OMC | 681919106 | 0000029989 |
| PLL | PRU | 744320102 | 0001137774 |
| PARA | VIAC | 92556H206 | 0000813828 |
| VIA | VTRS | 92556V106 | 0001792044 |
| OEUR | O | 756109104 | 0000726728 |
| FB | META | 30303M102 | 0001326801 |

全部位于SPY历史source；不修改真正MOB/SYM等公司的财报，也不把所有同名ticker全局重命名。PARA→VIAC是对旧Paramount证券使用保留的历史查找系列（CIK813828）；不声称VIAC是当前交易代码。SEC的[实际上市公告](https://www.sec.gov/Archives/edgar/data/813828/000119312519306334/d842322d8k.htm)确认其旧class B代码；当前PARA端点返回另一CIK1826011。

其他主来源：[GE年报](https://www.sec.gov/Archives/edgar/data/40545/000130817923000210/ge4125011-ars.pdf)、[Gen](https://www.sec.gov/Archives/edgar/data/849399/000084939926000028/gen-20260806.htm)、[Omnicom证券](https://www.sec.gov/Archives/edgar/data/29989/000009375126000003/xslSCHEDULE_13G_X01/primary_doc.xml)、[Prudential](https://www.sec.gov/Archives/edgar/data/1137774/000113777426000048/R1.htm)、[Viatris](https://www.sec.gov/Archives/edgar/data/1792044/000179204426000041/vtrs-20260630.htm)、[Realty Income](https://www.sec.gov/Archives/edgar/data/726728/000072672823000061/o-20221231xars.pdf)、[Meta](https://www.sec.gov/Archives/edgar/data/1326801/000162828026028526/R1.htm)。证券编号还与已保存披露及SEC持仓表交叉核对；配置保留逐项source_url。

## 修复与边界

- 复用既有authoritative alias，仅在CUSIP+ISIN同时匹配时改变数据查找；不改原始ticker、LEI、filer CIK、raw payload、日期或权重。
- 新增issuer_cik绑定；目标财报CIK不匹配不得计算。producer与独立verifier分别检查旧快照是否已按最新规则重解析，不能配置改了但仍静默使用旧alias字段。
- 旧快照需从保留的原始证券字段更新派生alias元数据；未经此步骤的新配置会明确拒绝。当前只改隔离库，生产source/财报均未改。
- 13项新测试RED→GREEN，相关173项通过。SOXX、QQQ不受这批9个映射影响；SPY此前未成功落任何周频产品，最终PIT仍0行。
- 此扫描覆盖同ISIN多ticker的财报主体冲突，不冒称证明所有单ticker vendor数据绝无错误。完整新源数据和历史披露之间仍应逐步加强身份核验。

复验：按raw_symbol重新分组22组，7个CIK冲突全部消除（原首次按normalized symbol分组21组）。93c8a6f云端173tests、全量3563 passed/4 skipped；零HTTP SPY重跑中，累计FMP1693不变。

最终：SPY262周双线全部通过，三篮子15项只读检查全PASS，九期六篮子PIT均通过，1640个数值独立SQL一致。62行派生alias元数据修正，14,643条raw/日期/证券字段保持原样。未扩AUD白名单，未将Mobilicom财报重命名成Monster。生产未改。
