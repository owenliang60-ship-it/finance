# 三指数项目 Task 5：六篮子 PIT 共识估值

状态：本地实现与相关回归通过，尚未合并、推送、部署或写生产估值表。工作分支 `codex/index-pe-morning-chart`，基于 C1 提交 `2b54bd2`。

## 已交付

- `terminal/forward_valuation.py`：SPY/QQQ/SOX/MAGS/IGV/XLF 六篮子，aggregate `Σ市值/Σ净利润`；亏损公司保留，不完整公司从两边同步剔除。SOX 原始身份保留，显示 SOXX 的映射留在图表层。
- Rule A NTM 取指定 weekly snapshot 的四个连续预测季，要求窗口锚定 as-of，不跨 FY、不跳过空利润季、不借用别的 vintage/backfill 行。
- blend 取 as-of 已公告且可映射的三个街道 actual EPS × 当时可见稀释股数，再加下一个未报告季的预测 NI。匹配/股份/拆股口径不可靠则留空；非 USD actual 的 EPS 币种或 ADR 倍数不明确也不乘股数。不得拿 income GAAP NI 代替街道 actual。
- 两条线独立计算 mcap/weight coverage；NTM 未过 90% 双门拒绝整个六篮子批次。blend 单独不通过时为 NULL/partial，不伪装 complete，也不阻断合格 NTM。
- `update_fmp_forward.py --mode weekly --phase valuation`：只读已 complete 源快照，估值 dry-run 不创建 client/不触网/不写库；与数据 ingestion/resume 分离，不改写 source manifest。
- 窄存储事务：六篮子候选及认证原子提交；失败全部回滚；已有 vintage 仅允许结果相同的幂等重跑，差异明确拒绝。
- `verify_fmp_forward --stage full`：从仅检查六行和 JSON 升级为源重建、成员证据、四季求和、对称分子分母/覆盖率校验；读连接使用 ro + query_only + 同一事务。
- 周频入口：yfinance → FMP ingestion → 历史整窗刷新/认证 → PIT → 两个 verifier。history 会补齐/修复 HMC、FX，所以技术上必须先于 PIT 冻结。沿用原 cron_wrapper 资源锁和退出码告警，无额外竞争 cron。

## 真数据只读发现

2026-09-11 查询云端最新 complete source 为 2026-09-05（此前已核实 1,012 symbols、五 ETF 各 9 期）。SPY/XLF 的 US DOLLAR、QQQ 的 USD Pending Dividends、CME NASDAQ 100 Index Future 及负权重 CONTRA FUTURE 被旧 normalizer 标为 unknown。

新增明确现金/股息应收/期货识别。新采集使用规范化原因码；读旧快照时保留 raw/source_filter_reason，并将派生排除依据写入 valuation 的 `non_equity_exclusions`，不覆盖已有 PIT 原始快照。未知股票继续计入缺失权重，不能借此消失。

抽查 AAPL/MSFT/NVDA/TSM/ASML 的 HMC as-of 2026-09-05 均已有 2026-09-04 观测。未将这些片段冒充完整六篮子真实估值验收；云端历史披露表/周频产品表尚未部署，仍需完整数据准备。

## 验证

22 个相关测试文件共 **637 passed**，10 个既有弃用警告。覆盖 C1 与全部估值存储/数据采集相邻面；未重跑全仓测试。新增 NTM/blend、空季/错季/未来公告、亏损、覆盖盲区、冻结快照、完整 reader、API 禁止调用、现金/负期货与流水线失败传播测试。

已批准的共识会计口径写入原设计：不得称为已验证 GAAP；非 USD 预测 NI 的币种仍依据可见报表推断并过 FX/量级门，EUR 等近似汇率的误配无法由量级门完整证明，需要云端数据验收继续核对。

## 下一步

Task 6–9 的图表/晨报/运行说明与综合验收。真实五年数据回填和部署继续按主计划 §7，在明确 API 调用量、写表范围和备份后执行。当前未发送 Telegram、未写生产数据。
