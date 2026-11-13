# M16 动量榜与景气动量精选：验收证据（2026-11-13）

输入：本地 `data/prosperity.db`、`data/market.db`（只读，运行前后 SHA256 相同）；云端 `prosperity_pool.json`（as_of 2026-11-07，36 只）。
榜单日 2026-11-07，价格截至 2026-11-06，`code_version` 13e5f9d5183f1b8e。

```bash
python reports/prosperity/m16-momentum-20261113/accept.py <repo> <data 目录> <池文件> /tmp/m16/work \
  reports/prosperity/m16-momentum-20261113
```

| 项 | 结果 |
|---|---|
| CLI | 主池 888；7 周有效 886、上涨合格 469；14 周有效 885、上涨合格 280；耗时约 4.5 秒 |
| 不可用 | 7 周：SCCO（拆股未决）、VYLR（缺价）；14 周另加 MNST（拆股未决） |
| 拆股调整 | 14 周 APH 2026-08-27 ÷2（FMP 记录日 09-03，三态规则按实际跳变日匹配） |
| 独立核对 `verify.py` | `/usr/bin/python3 -I`，只用标准库：`checked 1771 scored rows, 5 unavailable; differences: 0` |
| 核对负例（7 个） | 删有效行、删不可用记录、分数 +0.01、分数改 NaN、删拆股调整、翻转资格、有效行挪到不可用：全部退出码 1 |
| 投递（假发送器） | `delivered`，顺序 web → pool_text → health_baseline → momentum_text → combo_text；重跑 `already_delivered` |
| 消息长度 | 动量榜 1,243 字符；景气动量精选 1 条 358 字符 |
| 交集 | 7 周 7 只，14 周 8 只，两榜都在 6 只；消息名单与独立从 JSON 算出的名单一致 |
| 负例：池过期 | 动量榜照发且不标 ★；精选消息写 `prosperity_pool_stale` |
| 负例：CLI 失败 | 状态 `failed`，台账只有 web / pool_text / health；重跑只补两条 M16 文本 |
| 100 只真实名单 | 两个窗口都合格的 100 只、名次最靠后：1 条 3,685 字符，名单无遗漏。真实代码较短所以没拆；最长代码的上限情形由单元测试 `test_combo_messages_split_at_the_pool_cap` 覆盖（拆 2 条） |

文件：`result.json`（全部结果）、`momentum-text.txt`、`combo-text.txt`、`combo-cap-100.txt`（消息原文）、`verify.py`、`accept.py`。

已知情况：
- SCCO 每次小比例送股（约 1.2%）前后都会被三态规则判为未决，窗口覆盖到就不打分。这是体检报告同一条规则的保守行为，本次不改。
- 核对脚本不重判拆股：`adjustments` 按 JSON 所列应用并逐条打印，拆股排除的股票逐条打印，供人工看。
- MRNA 14 周 +339% 含 2026-08-19 单日 ×2.77（成交量同步放大约 10 倍，之后继续上行），核对脚本作为"大幅单日变动"打印，不是拆股。
