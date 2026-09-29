# 景气 M4 真实库验收（2026-09-29）

数据：已验收快照 `data/backups/prosperity/phase0-accepted-input-20260929/market.db`（SHA 3caffdb7…），只读。代码：`prosperity/m4-inputs` 分支（含 code review 修复）。
结论：**全部通过**（24/24）。本表由脚本从各目录 summary/jsonl 生成；完整 JSONL 在主仓库同名目录（不进 git）。

| 样本 | 结果 | 实际 |
|---|---|---|
| live 成员 915、泄漏 0、单股错误 0 | ✅ | 915 / 0 / 0 |
| KLAC 断点前挂 eps_split_rescaled，相邻比值 1/3–3 | ✅ | 换算 8 季，最大相邻比 1.34 |
| ANET 断点前挂 eps_split_rescaled，相邻比值 1/3–3 | ✅ | 换算 8 季，最大相邻比 1.17 |
| ORLY 断点前挂 eps_split_rescaled，相邻比值 1/3–3 | ✅ | 换算 9 季，最大相邻比 1.26 |
| APH 断点前挂 eps_split_rescaled，相邻比值 1/3–3 | ✅ | 换算 10 季，最大相邻比 1.90 |
| MNST 断点前挂 eps_split_rescaled，相邻比值 1/3–3 | ✅ | 换算 9 季，最大相邻比 1.31 |
| DELL 无 eps_split_* 标签 | ✅ | 无标签 |
| FTV 无 eps_split_* 标签 | ✅ | 无标签 |
| LH 无 eps_split_* 标签 | ✅ | 无标签 |
| BKNG 无 eps_split_* 标签 | ✅ | 无标签 |
| CMG 无 eps_split_* 标签 | ✅ | 无标签 |
| BHP semiannual_reporter；2023-12-31 EPS 空 + 冲突 | ✅ | flags=['semiannual_reporter', 'unit_unverified'] |
| FER 2025-06-30 EPS 空 + 冲突；unit_unverified | ✅ | flags=['semiannual_reporter', 'unit_unverified'] |
| ASML 2024-07-01 replay 当前财季不是 2024-06-30 | ✅ | current_fiscal=None |
| ASML 2026-09-29 live 当前财季存在 | ✅ | 2026-06-30 |
| WLK 2026-05-02 replay 当前财季 = 2025-12-31 | ✅ | 2025-12-31 |
| WLK 2026-05-05 replay 当前财季 = 2026-04-02，5/5 可用，earnings_floor | ✅ | 2026-04-02 2026-05-05 earnings_floor |
| NVDA 2026-08-15 NTM 四季 07-26/10-26/01-26/04-26，quarter_sum | ✅ | quarter_sum ['2026-07-26', '2026-10-26', '2027-01-26', '2027-04-26'] |
| YPF 2025-03-31、2025-06-30 流量字段全空，q10_unit_scale | ✅ | 两季 7 个字段置空 |
| TSM unit_unverified；NTM/TTM/公告前/修正为空；营收毛利正常 | ✅ | per-share 输入全空，营收/毛利有值 |
| NVDA 2026-09-29 replay 三表点时等级 = strict | ✅ | {'consensus': 'strict', 'statements': 'strict', 'street_eps': 'approximate'} |
| 价格未回溯拆股的 3 只（BKNG/CRWD/KLAC）挂 price_split_rescaled；KLAC 2025-12-31 回看市盈率回到正常量级 | ✅ | live 标签 ['BKNG', 'CRWD', 'KLAC']；KLAC 回看 PE 34.9，live beta 3.11 |
| 2021-12-31 冷启动（记录，不设门槛） | ✅ | 848 包，load 3.244s + build 3.796s；同进程再算 2022-01-07 约 4.2s |
| 拆股资格放宽后 D9 报告：SUE 缺口与拆股疑点不变，仅 BRK-B/CMG/EQIX/NVR 状态变化 | ✅ | 一致=True，状态变化 ['BRK-B', 'CMG', 'EQIX', 'NVR'] |

live 汇总：有当前财季 915，≥8 季三表 895，EPS 窗口 ≥13 季 861，NTM 可用 758，unit_unverified 151；EPS 标签 {'eps_conflicting_quarter': 102, 'eps_split_rescaled': 44}；flags {'eps_behind_current': 6, 'price_split_rescaled': 3, 'semiannual_reporter': 27, 'stale_current_fiscal': 19, 'unit_unverified': 151}。

## 过程中发现并修订

- 首轮验收：MNST 早期 EPS 被除 4（同一断点报两次），APH 被误判为 GAAP 断（高增长抵消跳变）。Boss 批准换算判定改为相邻断点合并 + 单季跳变（plan v1.4），MNST/APH 真实序列已固化为测试。
- 原 ASML 2022 公告下界样本预期有误（快照 2024 前缺资产负债表/现金流量表），改用 WLK；ASML 利润表行的下界单独核对通过（7/2 → 7/20）。
- code review high（1 个 reviewer，10 条）：修 7 条——价格序列未回溯拆股（BKNG/CRWD/KLAC，内存换算并挂 price_split_rescaled）、Q10 以多数单位段为基准、EPS 预期值跟随换算/置空、FY 回退加上限、E2 跳变跨缺失快照、无一致预期不标 strict、非成员 --symbols 显式列出；3 条不改（见交付说明）。

## 已知限制

- APH 2025-12-31、2026-03-31 两季 street EPS 疑似未回溯拆股（0.51→0.97→1.06→0.68，同期 GAAP 平滑），未经一手来源证实；现有检测需要两侧各 3 季稳定，识别不了 2 季孤岛。Boss 决定记为已知限制，留给 M7 对拍。
- 拆股检查只覆盖有拆股元数据的股票；`no_suspect_detected` 不等于证明干净。EPS 断点可能早于拆股日两年（部分回溯），启发式无法完全排除真实的单季腰斩被误认成拆股。
- 历史成员按当前 security_master 资格过滤（沿用 M1）；约 59 只的首个周度一致预期快照晚于 2026-07-13，它们在这段时间公告的季度没有公告前一致预期。
