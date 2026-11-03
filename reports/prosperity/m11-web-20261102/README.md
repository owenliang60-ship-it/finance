# M11 网页最小版验收（2026-11-03）

Plan：`docs/plans/2026-11-02-prosperity-m11-web.md`。分支 `prosperity/m11-web`，基线 main `f29a05ef`（rebase 后 plan 提交为 `5fabac95`）。
输入快照 `/tmp/m12/prosperity.db`（11-01 拉取，sha256 前 16 位 `ca0aa05a7cfccf75`，无 `-wal`），全程未变。
`code_version` `13e5f9d5183f1b8e`，没有改动。

## 结论

| # | 检查 | 结果 |
|---|---|---|
| A1 | 单元测试 | 通过：`tests/test_prosperity_web.py` 19 个 + `tests/test_prosperity_web_cli.py` 7 个，共 26 passed |
| A2 | 真实数据生成 | 通过：退出 0，88 节，72,580 行，70.3 s，峰值 RSS 0.77 GB，**11.35 MB**（上限 12 MB）。见下文"大小" |
| A3 | 独立核对 | 通过：72,580 行、36 个成交额排名，差异 0；改一个分数 → 差异 1、退出 1；只改一个成交额排名 → 差异 1、退出 1 |
| A4 | 入池标记 | 通过：F1 10-31 节标 36 只，名单和成交额排名逐值一致；四个负例都是 0 只，榜头写原因（下表） |
| A4b | 输出不覆盖输入 | 通过：`--out` 指向快照库、库的 `-wal`、池文件时都退出 4；两个文件 SHA256 不变，没有生成 `-wal` |
| A5 | 页面检查 | 通过：`<script` 0 处，外部 src/href 0 处；Chrome headless 1200 px 截图 4 张（`shots/`） |
| A6 | 全量测试 | 见 `branch-pytest.txt` 与 `failure-diff.txt` |

## 大小（A2）

第一次生成 23.4 MB，是 plan 试算（9.8 MB）的两倍多。原因：每格的 class、徽章 `<span class=…>`、闭合标签。
提交 `46a516d4` 只改写法，不改内容：
- 每行省略 `</td>` 与 `</tr>`（HTML5 允许省略）。
- 数字列靠 CSS 列位置右对齐，不再给每格写 class。
- 徽章用 `<i>`（普通）和 `<b>`（红色）。

结果 11.35 MB。按段统计：季末榜平均每张 0.13 MB，2026Q2 的 4 张共 0.57 MB。
所以 2026Q3 冻结后页面会超过 12 MB，M13 投递前要定处理方式（北极星 §4.2 已写明）。
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
/usr/bin/python3 -I $R/verify_web.py --db /tmp/m12/prosperity.db --html $R/out/prosperity-web.html --pool $P/pool-20261031.json
```

`verify.txt` 是全部核对的输出：主核对、两个篡改、四个负例。HTML 都在 `out/`（gitignore，每份约 11 MB，可重新生成）。
`verify_web.py` 只用标准库，不 import 本仓库。它直接查 `board`、`score`、`stock_period`、`stock_input` 表，自己重建每一行和入池绑定。
它不重算 `content_hash`（那是 M8 规范化 JSON 的哈希，重写一份等于复制实现），这一支由单元测试覆盖。

## 截图（A5）

- `shots/toc.png`：页首、目录矩阵（22 行 × 4 方案）、F1 首节开头。
- `shots/f1.png`：F1 10-31 主表，入池行浅绿底、✓、成交额排名。
- `shots/details.png`：折叠块展开（BELOW 行，红色降级徽章）。
- `shots/dark.png`：`data-theme="dark"` 深色模式。

截图用的是从完整页面切出来的副本（`out/shot-*.html`），因为 headless Chrome 只截首屏。
