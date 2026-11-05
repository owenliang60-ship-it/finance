# 景气 M14 晨报试运行段：本地真实数据验收（2026-11-05）

Plan：`docs/plans/2026-11-03-prosperity-m14-morning.md`。分支 `prosperity/m14-morning`。`code_version` 不变。

## 输入（都只读）

| 输入 | 路径 | 说明 |
|---|---|---|
| prosperity.db | `/tmp/m12/prosperity.db` | 11-01 快照，sha256 前 16 位 `ca0aa05a7cfccf75` |
| market.db | `/tmp/m13/market.db` | 11-03 本地副本，sha256 前 16 位 `97db51f9b25a22c9` |
| 池文件 | `reports/prosperity/pool-q1-20261102/pool-20261031.json` | 10-31，36 只（`m10-pool-20261101/` 下同名文件是旧规则，会报条件不匹配） |
| Premium 池 | `/tmp/m12/premium_pool.json` | as_of 2026-10-30，47 只；读时跳过新鲜度 |

时钟 2026-11-01T05:00Z。脚本 `/tmp/m14/accept.py`：`load_bound_prosperity_pool` → 直接用 `MarketStore(db_path=…)` 取 127 行价格与市值观测（as_of 取价格最新日 2026-11-02）→ `scan_selection_compass` → `pool_comparison` → `format_section_prosperity_compass`。不跑完整晨报（issue057）。

## 结果（B5）

| 项 | 结果 |
|---|---|
| 绑定 | available，36 只 |
| EMA30 | 覆盖 36/36，命中 9 只：V、XOM、CVX、COST、TXN、WFC、AMGN、ADI、MDT（按市值降序） |
| 与 Premium | 交集 14、新增 22、移出 33；独立集合运算一致（`counts_match: true`） |
| 输入不变 | 两库 SHA256 前后相同 |
| 文本段 | `section.txt`，人工看过；图片段本地渲染看过（Premium 列原用"✓"，本地字体无字形显示成方框，改为"是"） |

证据：`result.json`、`section.txt`。
