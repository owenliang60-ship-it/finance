# 004: 新增副作用函数未隔离，测试套件摧毁真实基本面数据

**日期**: 2026-02-15
**严重程度**: 高（数据丢失，需 17 分钟重建 + 云端重新同步）
**根因**: 给已有函数加了会删数据的副作用，没检查所有调用方的测试是否隔离

---

## 发生了什么

基本面数据从 101 条变成 3 条（只剩 AAPL、MSFT、NVDA），而且在不知情的情况下把损坏的数据同步到了云端。

## 时间线

1. 给 `pool_manager.py` 新增 `cleanup_stale_data()` 函数，在 `refresh_universe()` 末尾自动调用
2. 写了 7 个单元测试，全部用 `tmp_path` + `mock.patch` 隔离，测试通过
3. 跑完整测试套件 654 个测试，全部通过
4. 验证基本面数据：**3 条**（已被摧毁）
5. 不知情地把 3 条数据同步到云端
6. 发现问题后重建数据（17 分钟），重新同步

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

`cleanup_stale_data()` 会删除不在股票池中的价格 CSV 和基本面 JSON 条目。

### 哪里炸了

`test_auto_pool_admission.py` 里有 4 个测试调用 `refresh_universe()`，用来测试"池刷新时保留分析来源股票"等逻辑。这些测试：

- **mock 了** `fmp_client`（让 API 返回假数据，只有 2-3 只股票）
- **没有 mock** `PRICE_DIR` 和 `FUNDAMENTAL_DIR`（指向真实数据目录）

改动之前这完全没问题——`refresh_universe()` 只写 `universe.json`（在测试中也被覆盖了），不碰价格和基本面。

改动之后，`refresh_universe()` 在末尾调用 `cleanup_stale_data()`，而这个函数：

1. 拿到传入的股票列表（测试中只有 AAPL、MSFT 两只）
2. 扫描 **真实的** `data/fundamental/` 目录
3. 把所有不在列表中的条目全部删除
4. 101 条 → 2 条

4 个测试依次运行，每个都用不同的假池覆盖真实数据。最后一个测试的池有 AAPL + MSFT + NVDA，所以最终剩 3 条。

### 为什么没立刻发现

- 654 个测试全部通过（删数据不影响测试逻辑）
- 之前验证时只看了 universe.json（101 只，正确）和 CSV 数量（103 个，正确），没二次检查基本面
- 基本面 JSON 的文件大小没有显著变化（3 条大公司的嵌套数据也有 55KB）

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

## 影响

- 基本面数据全量重建：17 分钟（101 只 × 5 种报表 × 2s 间隔）
- 云端数据被污染后重新同步
- 无持久性损失（数据可从 FMP API 重建）
