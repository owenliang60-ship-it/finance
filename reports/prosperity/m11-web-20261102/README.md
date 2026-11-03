# M11 网页最小版验收（2026-11-03）

Plan：`docs/plans/2026-11-02-prosperity-m11-web.md`。分支 `prosperity/m11-web`，基线 main `f29a05ef`（rebase 后 plan 提交为 `5fabac95`）。
输入快照 `/tmp/m12/prosperity.db`（11-01 拉取，sha256 前 16 位 `ca0aa05a7cfccf75`，无 `-wal`），全程未变。
`code_version` `13e5f9d5183f1b8e`，没有改动。

## 结论

| # | 检查 | 结果 |
|---|---|---|
| A1 | 单元测试 | 通过：`tests/test_prosperity_web.py` 26 个 + `tests/test_prosperity_web_cli.py` 10 个，共 36 passed（两轮审查修复后） |
| A2 | 真实数据生成 | 通过：退出 0，88 节，72,580 行，69.2 s，峰值 RSS 0.77 GB，**10.25 MB**（上限 12 MB）。见下文"大小" |
| A3 | 独立核对 | 通过：72,580 行、36 个成交额排名，差异 0。三个篡改各差异 1、退出 1：改一个分数、只改一个成交额排名、删掉一张季末榜的"本榜全部为近似 PIT"。核对脚本拿错池文件（n6）去核对正常页面，报 901 处差异（`verify.txt`） |
| A4 | 入池标记 | 通过：F1 10-31 节标 36 只，名单和成交额排名逐值一致；六个负例都是 0 只，榜头写原因（下表） |
| A4b | 输出不覆盖输入 | 通过。修复前在真实快照上：`--out` 指向快照库、库的 `-wal`、池文件时都退出 4，两个文件 SHA256 不变。两轮审查共发现四种绕过（硬链接、大小写、符号链接 `--db`、Unicode NFC/NFD），修复后只在合成库上测，不再拿真实快照试：`a4b-cli-tests.txt`（10 passed）、`check_paths.txt`（CC 自写脚本复查 Codex 列的 8 种写法，全部退出 4，库未变） |
| A5 | 页面检查 | 通过：`<script` 0 处，外部 src/href 0 处；Chrome headless 1200 px 截图 5 张（`shots/`，修复后重截）。Safari、Telegram 内置浏览器未验收 |
| A6 | 全量测试 | 通过：12 failed / 4336 passed（Codex 修复后），新增失败 0、消失 0，共有 12 个（breadth 7 + morning_report 5，与基线相同）。见 `branch-pytest.txt` 与 `failure-diff.txt` |

## 大小（A2）

第一次生成 23.4 MB，是 plan 试算（9.8 MB）的两倍多。原因：每格的 class、徽章 `<span class=…>`、闭合标签。
提交 `46a516d4` 只改写法，不改内容：
- 每行省略 `</td>` 与 `</tr>`（HTML5 允许省略）。
- 数字列靠 CSS 列位置右对齐，不再给每格写 class。
- 徽章用 `<i>`（普通）和 `<b>`（红色）。

结果 11.35 MB。最大的单项是"近似PIT"徽章：84 张季末榜（回放）每行都是 approximate，共 68,984 行，约 1.1 MB。
2026-11-03 Boss 采纳 Codex 建议：整张榜都是近似 PIT 时只在榜头写一次，混合榜仍逐行显示（plan §16）。结果 **10.25 MB**。
按段统计：季末榜平均每张 0.12 MB，2026Q2 的 4 张共 0.51 MB。约 4 个季度后（2027Q2 冻结后约 12.3 MB）超过上限（北极星 §4.2 已写明）。

## 入池负例（A4）

| 负例 | 池文件 | F1 10-31 榜头 | ✓ |
|---|---|---|---|
| ① as_of 改成 2026-10-24 | `negatives/n1-as-of.json` | 未标入池：池文件校验失败（ValueError） | 0 |
| ② 成员 SNDK 换成 BMNR（10-31 F1 排名 250，BELOW），来源哈希不动 | `negatives/n2-bmnr.json` | 未标入池：池成员与本榜不符（BMNR） | 0 |
| ③ SNDK 评级 STRICT → FULL | `negatives/n3-grade.json` | 未标入池：池成员与本榜不符（SNDK） | 0 |
| ④ 可排名数 848 → 899，切点 170 → 180，加入排名 178 的 TER | `../pool-q1-20261102/pool-20261031-ter.json` | 未标入池：池文件与本榜不符（ranked） | 0 |
| ⑤ `criteria.liquidity.top` 100 → 101（Codex 建议补） | `negatives/n5-criteria.json` | 未标入池：池文件校验失败（PoolCriteriaMismatch） | 0 |
| ⑥ `source.content_hash` 改成 64 个 f（Codex 建议补） | `negatives/n6-source-hash.json` | 未标入池：池文件与本榜不符（content_hash） | 0 |

负例 ① 报"校验失败"而不是"as_of 不符"：只改 as_of 后价格窗口不再属于这一天，M10 校验器先拦下。这与 plan Task 1 的测试预期相同。
`verify_web.py` 对六个负例也都是差异 0：它自己的绑定同样判定不标，页面上也确实没有标。
Codex 指出原核对脚本不查 `criteria` 和来源哈希，拿 ⑤⑥ 也会判定 36 只有效。现在它按 plan §4.1 手写一份 criteria 比较，并把来源哈希与 `board.content_hash` 列比较（不重算）。

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

## Codex 审查修复（2026-11-03，1 个 P1、2 个 P2）

Codex 另写了不 import 项目代码的 SQL/HTML 核对程序，88 张榜、72,580 行差异 0，与本目录结论一致。下面三条与两条建议都已处理。

| # | 问题 | 核实 | 处理 |
|---|---|---|---|
| P1 | `--db` 是符号链接时，WAL 检查和输出守卫看的是链接旁边，存储类读的是真实库：真实库有 WAL 也放行，`--out real.db-wal` 能写进真实 WAL 路径 | 成立：新测试修复前退出 0 | CLI 一开始就解析 `--db`，打开、哈希、WAL 检查和输出守卫都用真实路径 |
| P2 | APFS 把 NFC 的 `café.db` 和 NFD 的 `café.db-wal` 当成同名，只做 `casefold` 认不出 | 成立：新测试修复前退出 0 | 名字先做 NFC 规范化再 `casefold` 比较 |
| P2 | 没有 F1 live 周榜时，池文件错误被静默丢掉 | 成立 | 页首写"未标入池：库中无 F1 live 周榜"，附池文件错误 |
| 建议 | 核对脚本不查 `criteria` 和来源哈希 | 成立 | 补负例 ⑤⑥，核对脚本补这两项 |
| 建议 | 写明 ✓ 不代表池文件新鲜 | 采纳 | 标了入池的榜头加"✓ 仅表示该期池文件与本榜匹配，未验证当前新鲜度。" |
| 建议 | 整张榜都是近似 PIT 时放榜头 | Boss 2026-11-03 拍板采纳 | 见上文"大小" |

CC 没有运行 Codex 的复现脚本（`/tmp/m11-path-review/check.py`，非本会话所写，被权限拦下），而是按 Codex 列出的情形自写 `check_paths.py`，只用一次性合成库。

## 截图（A5）

- `shots/toc.png`：完整页面首屏：页首、目录矩阵（22 行 × 4 方案）、F1 首节开头。
- `shots/f1.png`：F1 10-31 主表，入池行浅绿底、✓、成交额排名、新鲜度说明，分数向下取整（ROST 64.9）。
- `shots/quarter.png`：F1 2026Q2 季末榜，榜头"本榜全部为近似 PIT"，行内不再逐行标。
- `shots/details.png`：折叠块展开（BELOW 行，红色降级徽章）。
- `shots/dark.png`：`data-theme="dark"` 深色模式。

除首屏外，截图用的是从完整页面切出来的副本（`out/shot-*.html`）。headless Chrome 截图不跟随 URL 里的 `#锚点`：一个 30 节的最小测试页同样只截到空白，与页面本身无关。所以完整页面的锚点跳转只在代码层面验证过（单元测试检查每个目录链接都指向存在的节），没有在浏览器里点过。
