# M11 网页最小版验收（2026-11-03）

Plan：`docs/plans/2026-11-02-prosperity-m11-web.md`。分支 `prosperity/m11-web`，基线 main `f29a05ef`（rebase 后 plan 提交为 `5fabac95`）。
输入快照 `/tmp/m12/prosperity.db`（11-01 拉取，sha256 前 16 位 `ca0aa05a7cfccf75`，无 `-wal`），全程未变。
`code_version` `13e5f9d5183f1b8e`，没有改动。

## 结论

| # | 检查 | 结果 |
|---|---|---|
| A1 | 单元测试 | 通过：`tests/test_prosperity_web.py` 21 个 + `tests/test_prosperity_web_cli.py` 8 个，共 29 passed（审查修复后） |
| A2 | 真实数据生成 | 通过：退出 0，88 节，72,580 行，70.3 s，峰值 RSS 0.77 GB，**11.35 MB**（上限 12 MB）。见下文"大小" |
| A3 | 独立核对 | 通过：72,580 行、36 个成交额排名，差异 0；改一个分数 → 差异 1、退出 1；只改一个成交额排名 → 差异 1、退出 1（审查修复后重跑，`verify.txt`） |
| A4 | 入池标记 | 通过：F1 10-31 节标 36 只，名单和成交额排名逐值一致；四个负例都是 0 只，榜头写原因（下表） |
| A4b | 输出不覆盖输入 | 通过。修复前在真实快照上：`--out` 指向快照库、库的 `-wal`、池文件时都退出 4，两个文件 SHA256 不变。审查发现硬链接和大小写变体能绕过，修复后只在合成库上测（`a4b-cli-tests.txt`，8 passed），不再拿真实快照试 |
| A5 | 页面检查 | 通过：`<script` 0 处，外部 src/href 0 处；Chrome headless 1200 px 截图 4 张（`shots/`） |
| A6 | 全量测试 | 通过：新增失败 0、消失 0，共有 12 个（breadth 7 + morning_report 5，与基线相同）。见 `branch-pytest.txt` 与 `failure-diff.txt` |

## 大小（A2）

第一次生成 23.4 MB，是 plan 试算（9.8 MB）的两倍多。原因：每格的 class、徽章 `<span class=…>`、闭合标签。
提交 `46a516d4` 只改写法，不改内容：
- 每行省略 `</td>` 与 `</tr>`（HTML5 允许省略）。
- 数字列靠 CSS 列位置右对齐，不再给每格写 class。
- 徽章用 `<i>`（普通）和 `<b>`（红色）。

结果 11.35 MB。按段统计：季末榜平均每张 0.13 MB，2026Q2 的 4 张共 0.57 MB。
所以 2026Q3 冻结后约 11.9 MB，2026Q4 冻结后约 12.5 MB，超过上限；2026Q4 冻结前要定处理方式（北极星 §4.2 已写明）。
最大的单项是"近似PIT"徽章：回放榜 68,984 行全是 approximate，只有 live 榜不是。plan 9.1 规定按行显示，本次没有改。

## 入池负例（A4）

| 负例 | 池文件 | F1 10-31 榜头 | ✓ |
|---|---|---|---|
| ① as_of 改成 2026-10-24 | `negatives/n1-as-of.json` | 未标入池：池文件校验失败（ValueError） | 0 |
| ② 成员 SNDK 换成 BMNR（10-31 F1 排名 250，BELOW），来源哈希不动 | `negatives/n2-bmnr.json` | 未标入池：池成员与本榜不符（BMNR） | 0 |
| ③ SNDK 评级 STRICT → FULL | `negatives/n3-grade.json` | 未标入池：池成员与本榜不符（SNDK） | 0 |
| ④ 可排名数 848 → 899，切点 170 → 180，加入排名 178 的 TER | `../pool-q1-20261102/pool-20261031-ter.json` | 未标入池：池文件与本榜不符（ranked） | 0 |

负例 ① 报"校验失败"而不是"as_of 不符"：只改 as_of 后价格窗口不再属于这一天，M10 校验器先拦下。这与 plan Task 1 的测试预期相同。
`verify_web.py` 对四个负例也都是差异 0：它自己的绑定同样判定不标，页面上也确实没有标。

## 复现

```bash
PY="/Users/owen/CC workspace/Finance/.venv/bin/python"
R=reports/prosperity/m11-web-20261102
P=reports/prosperity/pool-q1-20261102
"$PY" scripts/prosperity_web.py --db /tmp/m12/prosperity.db --out $R/out/prosperity-web.html --pool $P/pool-20261031.json
/usr/bin/python3 -I $R/verify_web.py --db /tmp/m12/prosperity.db --html $R/out/prosperity-web.html \
  --code-version 13e5f9d5183f1b8e --pool $P/pool-20261031.json
```

`verify.txt` 是全部核对的输出：主核对、两个篡改、四个负例。HTML 都在 `out/`（gitignore，每份约 11 MB，可重新生成）。
`verify_web.py` 只用标准库，不 import 本仓库。它直接查 `board`、`score`、`stock_period`、`stock_input` 表，自己重建每一行和入池绑定。
它不重算 `content_hash`（那是 M8 规范化 JSON 的哈希，重写一份等于复制实现），这一支由单元测试覆盖。

## 审查修复（2026-11-03，`/code-review high` 10 条）

| # | 问题 | 核实 | 处理 |
|---|---|---|---|
| 1 | 输出路径守卫只比字符串，macOS 默认 APFS 不分大小写，硬链接路径也不同，`--out PROSPERITY.DB` 会用 HTML 换掉库 | 成立：本机卷不分大小写；新测试修复前硬链接一项 `assert 0 == 4` | 已存在的输入用 `os.path.samefile`；尚不存在的 `-wal` 等按同目录 + 名字不分大小写比较 |
| 2 | "最新周榜"取 as_of 最大的周榜，不看 mode；重建冻结链后、首个 live 周六前会取到回放榜 | 成立：真实库每方案 21 张回放周榜；新测试修复前取到 `('2026-10-03', 'replay')` | 只取 `mode='live'` 的最新周榜；`verify_web.py` 同样改 |
| 3 | 分数四舍五入到 1 位，64.99 显示 65.0 但评级 FULL，与"≥ 65 为 STRICT"矛盾 | 成立：10-31 live 榜 7 行（ROST 64.99 FULL 等），季末榜 99 行 | 分数向下取一位小数（ROST 显示 64.9），页首说明写明；`verify_web.py` 同样改 |
| 4 | README 引用的 `branch-pytest.txt`、`failure-diff.txt` 未入库 | 成立（当时全量测试还在跑） | 本次提交 |
| 5 | runbook 的 `--pool data/pool/prosperity_pool.json` 本地不存在，同步脚本不拉 | 成立：`sync_to_cloud.sh` 只拉 `universe.json` | runbook 加 `scp` 一步，并说明池文件要与库快照同一次拉取 |
| 6 | 主表/折叠块拆分用 `r not in top`，O(n·m) | 成立 | 改为一次遍历 |
| 7 | `verify_web.py --code-version` 写死 13e5f9d5 | 成立 | 改为必填，runbook 写明取页首的值 |
| 8 | 北极星说 2026Q3 后超 12 MB，按自己的数字是 11.9 MB；plan 基线行过时 | 成立 | 改为 2026Q4 后约 12.5 MB 超限；plan 基线行改为 `f29a05ef` |
| 9 | `_wal_bytes` 在两个 CLI 里各抄一份 | 成立 | 移到 `src/data/prosperity_backup.py` 的 `wal_bytes`，两个 CLI 共用（不在 `code_version` 范围内） |
| 10 | 是否显示入池列靠 `pool_note.startswith('入池')` 判断 | 成立 | 改为"有任一行带成交额排名"；`pool_binding` 只在绑定成功时给出排名 |

修复后真实快照重新生成：88 节、72,580 行、70.3 s、峰值 RSS 0.78 GB、11.35 MB。

## 截图（A5）

- `shots/toc.png`：页首、目录矩阵（22 行 × 4 方案）、F1 首节开头。
- `shots/f1.png`：F1 10-31 主表，入池行浅绿底、✓、成交额排名。
- `shots/details.png`：折叠块展开（BELOW 行，红色降级徽章）。
- `shots/dark.png`：`data-theme="dark"` 深色模式。

截图用的是从完整页面切出来的副本（`out/shot-*.html`），因为 headless Chrome 只截首屏。
