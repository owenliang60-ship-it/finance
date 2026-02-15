# 004: 新增副作用函数未隔离，测试套件摧毁真实数据

**日期**: 2026-02-15
**严重程度**: 高（价格 CSV 103→6 + 基本面 JSON 101→3，需重建 + 云端重新同步）
**根因**: 给已有函数加了会删数据的副作用，没检查所有调用方的测试是否隔离

---

## 发生了什么

测试套件运行后，真实数据被大面积删除：
- **价格 CSV**: 103 → 6（只剩 AAPL、MSFT、MU、VRT + SPY、QQQ）
- **基本面 JSON**: 101 → 3 条（只剩 AAPL、MSFT、NVDA）

而且第一遍排查只发现了基本面的问题，**价格 CSV 的损失直到后续验证才发现**。中间还在不知情的情况下把损坏的基本面数据同步到了云端。

## 时间线

1. 给 `pool_manager.py` 新增 `cleanup_stale_data()` 函数，在 `refresh_universe()` 末尾自动调用
2. 写了 7 个单元测试，全部用 `tmp_path` + `mock.patch` 隔离，测试通过
3. 跑完整测试套件 654 个测试，全部通过
4. 验证数据，发现基本面只剩 **3 条**（但没检查价格 CSV）
5. 不知情地把 3 条基本面数据同步到云端
6. 修复测试，重建基本面数据（17 分钟），重新同步
7. Boss 要求复查时发现价格 CSV 也只剩 **6 个**
8. 从云端拉回价格数据（云端 `sync_to_cloud --data` 不同步价格，所以未受影响）

## 根因分析

### 改了什么

`refresh_universe()` 原来只做：获取股票 → 过滤 → 保存。我在末尾加了一行：

```python
# refresh_universe() 末尾
save_universe(new_stocks)

# 新增：清理已退出股票的残留数据
if exited:
    cleanup_stale_data(new_symbol_list)
```

`cleanup_stale_data()` 会做两件事：
1. 删除不在股票池 + benchmark 中的**价格 CSV 文件**
2. 删除基本面 JSON 中不在股票池中的**条目**

### 哪里炸了

`test_auto_pool_admission.py` 里有 4 个测试调用 `refresh_universe()`，用来测试"池刷新时保留分析来源股票"等逻辑。这些测试：

- **mock 了** `fmp_client`（让 API 返回假数据，只有 2-3 只股票）
- **没有 mock** `PRICE_DIR` 和 `FUNDAMENTAL_DIR`（指向真实数据目录）

改动之前这完全没问题——`refresh_universe()` 只写 `universe.json`（在测试中也被覆盖了），不碰价格和基本面。

改动之后，`refresh_universe()` 在末尾调用 `cleanup_stale_data()`，而这个函数：

1. 拿到传入的股票列表（测试中只有 AAPL、MSFT 等 2-3 只）
2. 扫描 **真实的** `data/price/` 目录，删除不在列表中的 CSV → 103 个文件被删到只剩几个
3. 扫描 **真实的** `data/fundamental/` 目录，删除不在列表中的条目 → 101 条被删到只剩 2-3 条
4. benchmark（SPY、QQQ）因为在代码中被排除在基本面清理之外，但 CSV 保留

4 个测试依次运行，每个都用不同的假池覆盖真实数据。最终结果取决于最后运行的那个测试的池。

### 为什么没立刻发现

- 654 个测试全部通过（删数据不影响测试逻辑）
- 第一遍排查只发现了基本面问题（因为在 `sync_to_cloud --data` 后对比了云端数字）
- **价格 CSV 直到 Boss 要求复查时才发现**——因为 `sync_to_cloud --data` 不同步价格目录，云端价格没被污染，所以没有数字对比触发警觉
- 教训：**出了一个 bug 时，必须检查所有可能受影响的数据，不能只查发现的那一个**

## 教训

### 核心规则

**给已有函数加副作用时，必须 grep 所有调用方的测试，确认每个测试都隔离了新副作用的作用域。**

具体来说：

```bash
# 改了 refresh_universe() → 搜所有调用它的测试
grep -r "refresh_universe" tests/
# 发现 test_auto_pool_admission.py 有 4 处调用
# → 检查这 4 处是否 mock 了 cleanup_stale_data 涉及的目录
```

### 检查清单（给任何"在已有函数中新增删除/修改数据的操作"）

1. `grep -r "函数名" tests/` — 找到所有测试调用点
2. 对每个调用点，确认新副作用的作用域被 mock/隔离
3. 跑完测试后，**再验证一次真实数据没变**（不只是看数量，看内容）
4. **先验证再同步** — 绝不在未验证的情况下 `sync_to_cloud`

### 修复方式

在 4 个 `refresh_universe()` 测试中加 `mock.patch("src.data.pool_manager.cleanup_stale_data")`：

```python
# 之前
with mock.patch("src.data.pool_manager.fmp_client") as mock_fmp:
    ...
    refresh_universe()

# 之后
with mock.patch("src.data.pool_manager.fmp_client") as mock_fmp, \
     mock.patch("src.data.pool_manager.cleanup_stale_data"):
    ...
    refresh_universe()
```

---

## 影响与恢复

| 数据 | 损坏程度 | 恢复方式 | 耗时 |
|------|---------|---------|------|
| 基本面 JSON | 101→3 条 | FMP API 全量重建 | 17 分钟 |
| 价格 CSV | 103→6 个 | 从云端 rsync 拉回 | 3 秒 |
| 云端基本面 | 被错误同步污染 | 本地重建后重新 push | — |
| 云端价格 | **未受影响** | sync_to_cloud 不同步价格 | — |
| company.db | 未受影响 | — | — |

无永久性数据损失（价格云端有备份，基本面可从 FMP API 重建）。
