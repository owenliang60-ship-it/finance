# M11 网页最小版：请 Codex 审查

日期：2026-11-03。审查对象：分支 `prosperity/m11-web`，worktree `/Users/owen/CC workspace/Finance/.worktrees/prosperity-m11`，HEAD `e4ff8e83`，基线 main `f29a05ef`。未 push。
代码提交：`8230ecf2`（行视图 + 入池绑定）→ `6f584ec7`（渲染）→ `b9118675`（CLI）→ `46a516d4`（压缩标记）→ `64ca7cc1`（验收 + 文档）→ `e4ff8e83`（CC `/code-review high` 10 条修复）。
Plan：`docs/plans/2026-11-02-prosperity-m11-web.md`（Boss 已批准）。验收证据：`reports/prosperity/m11-web-20261102/README.md`。

## 审查规则

- 只读审查。不改代码，不 push，不 merge。
- 不写 `/tmp/m12/prosperity.db`。要跑 CLI 时，`--db` 指向它的副本，`--out` 指向新目录。CC 用真实快照测输出守卫被权限拦下，所以守卫只在合成库上验证过。
- 每条发现写清：位置、复现命令与输出、影响、建议。不确定的写"待核实"。

## 输入

| 项 | 值 |
|---|---|
| 快照 | `/tmp/m12/prosperity.db`，sha256 前 16 位 `ca0aa05a7cfccf75`，无 `-wal`（`/tmp` 可能被清理，丢了就用 `scripts/sync_prosperity.py` 重拉，数字会变） |
| 池文件 | `reports/prosperity/pool-q1-20261102/pool-20261031.json`（36 只）；负例 `reports/prosperity/m11-web-20261102/negatives/n1–n3`、`pool-q1-20261102/pool-20261031-ter.json` |
| `code_version` | `13e5f9d5183f1b8e`，本分支没改 |
| Python | 本地测试 3.13；云端 3.10（M11 本身暂不在云端跑，M13 才上） |

## 重点 1：页面显示与库不一致（最大风险）

Boss 会照着页面看排名和评级。请直接查库核对，不要只信 CC 的核对脚本。

- `verify_web.py` 只用标准库，不 import 项目代码，但它和生成器出自同一个作者（CC），用的是同一种 plan 解读。如果解读错了，两边会一起错。请对照 plan §9.1–9.2 原文，抽查：徽章顺序与取舍、名称截断 28 字、观察/排除行的中文原因、四族小分。
- 不显示的代码：`params_bootstrap` 只在榜头；`listing_date_unknown`、`eps_split_retrospective` 不显示（D5）。请确认没有漏掉该显示的徽章。
- `verify_web.py` 的入池绑定比生成器弱：不查 `criteria`，不重算 `content_hash`（这一支只有单元测试覆盖）。

## 重点 2：入池绑定（`src/data/prosperity_web.py:103` `pool_binding`）

三层：榜级（F1 live 周榜、as_of、criteria、code_version、content_hash）→ 计数（`board_mismatch`）→ 逐成员（在榜内、ranked、STRICT/FULL、rank 与 grade 一致）。任一不符就整张榜不标，榜头写原因。

- 畸形池文件会不会让 CLI 崩溃，而不是写原因？`pool_binding` 只捕获 `ValueError/RuntimeError/TypeError/KeyError/OverflowError`。请试：顶层是数组、`members` 里有 null、数字是超大整数、深层嵌套。
- 第 113 行 `check_freshness=False`：网页不查池文件新鲜度（本地快照常超过 8 天）。✓ 只说明"池文件属于这张榜"，不说明池文件新鲜。这是 plan 的设计，请判断有没有误导。
- 库里没有 F1 live 周榜时（重建冻结链后、首个 live 周六前），页面上没有任何入池说明，只有目录"最新周榜"一行显示"无榜"。这算不算静默？

## 重点 3：输出不能覆盖输入（`scripts/prosperity_web.py:34` `_overwrites_input`）

审查发现原守卫只比路径字符串：macOS 默认 APFS 不分大小写，硬链接也有自己的路径，`--out PROSPERITY.DB` 能用 HTML 换掉 600 MB 快照。现在的做法：已存在的文件用 `os.path.samefile`；还不存在的 `-wal/-shm/.state` 按"同一目录 + `casefold` 名字"比较。

- 请找还能绕过的写法：符号链接目录、`..`、Unicode NFC/NFD（APFS 不区分规范化，`casefold` 不处理规范化）、指向池文件的硬链接。
- 测试：`tests/test_prosperity_web_cli.py` 的 `test_output_may_not_overwrite_an_input`、`test_output_may_not_be_the_same_file_under_another_name`。

## 重点 4：CC 偏离 plan 的 4 处（请判断是否合理）

| # | plan 原文 | 现在 | 原因 |
|---|---|---|---|
| 1 | 分数 `f'{x:.1f}'` | 向下取一位小数（`_score`，第 205 行）；页首写明 | 64.99 分 FULL 原来显示成 65.0，和"≥ 65 为 STRICT"矛盾（10-31 live 榜 7 行，季末榜 99 行）。也可以改为两位小数，这是显示口径，最后由 Boss 定 |
| 2 | 最新周榜 = `list_boards(weekly)` 中 as_of 最大的一张 | 只取 `mode='live'`（第 151 行，直接查 `store._conn`，与 `scripts/prosperity_health.py` 同一做法） | 真实库每个方案有 21 张回放周榜，重建链后会取到回放榜。用了私有属性 `_conn`，请判断要不要给 `ProsperityStore` 加正式方法 |
| 3 | `<td class="g">`、`<span class="b">`、闭合标签 | 省略 `</td></tr>`，数字列靠 CSS 列位置右对齐，徽章用 `<i>` / `<b>` | 第一次生成 23.4 MB，超过 12 MB 上限；改后 11.35 MB，内容不变 |
| 4 | `verify_web.py --code-version` 有默认值 | 必填 | 版本一变，默认值就对不上 |

## 重点 5：共享模块改动

- `wal_bytes` 从两个 CLI 移到 `src/data/prosperity_backup.py`。`scripts/sync_prosperity.py` 会把这个文件的**整份源码**通过 stdin 发到云端 Python 3.10 上执行（`remote_program`）。新函数只用标准库和 `Path`，`tests/test_prosperity_backup.py` 通过；请确认远端程序仍能运行，不受影响。
- `scripts/prosperity_health.py`（M12）只改了 import，M12 测试通过。

## 重点 6：页面本身

- 只在 Chrome headless 下截过图。没有在 Safari 和 Telegram 内置浏览器里打开过。11 MB 的页面在这两个环境里能不能打开、折叠块能不能用，都没有验证。
- 大小：11.35 MB，上限 12 MB。每季新增 4 张季末榜，约 +0.57 MB，2026Q4 冻结后约 12.5 MB，超过上限（北极星 §4.2 已记，M13 前由 Boss 定处理方式）。
- 生成耗时约 70 秒，峰值内存约 0.78 GB，88 张榜一张一张读。
- "近似PIT" 徽章出现在 68,984 行（所有回放榜行），占体积最大，也可能淹没其他徽章。plan 规定按行显示，CC 没改；请判断要不要提给 Boss。

## 已知局限（不需要再报）

- M11 不读 `market.db`，所以不检查"第一档 ∩ STRICT/FULL ∩ 成交额前 100 的股票是否都进了池"。这由 M10 选池负责（plan §6）。
- 云端生成与 Telegram 投递属 M13。

## CC 已跑过的检查

- 单元测试：web 21 个 + CLI 8 个 = 29 passed。
- 全量 pytest：12 failed / 4329 passed，与基线（5fabac95）相比新增失败 0、消失 0（`failure-diff.txt`）。
- 真实快照：独立核对 72,580 行差异 0；两个篡改各差异 1、退出 1；四个负例都不标入池，榜头写原因（`verify.txt`）。
