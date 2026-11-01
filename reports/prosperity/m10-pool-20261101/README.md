# 景气 M10 精选池文件 · 验收证据（2026-11-01）

Plan：`docs/plans/2026-11-01-prosperity-m10-pool.md`；规则：北极星 §4.1（2026-11-01 修订）。

## 输入快照（只读）

| 文件 | 来源 | SHA256 |
|---|---|---|
| `market.db` | 主仓库 `data/market.db`，2026-11-01 09:00 launchd 拉取，SPY 最新 2026-10-30 | `0f872769ea1fb88d80d512a3217cc8d83ee016bdb7dd42fc17022cdf2ed5555c` |
| `prosperity.db` | `scripts/sync_prosperity.py --db /tmp/m10/prosperity.db`，2026-11-01 08:26Z 起拉取，sha256 + quick_check + 候选校验通过 | `ca0aa05a7cfccf753799137d3bbe1df3d44004fe8f51e794533faf586ca09ef6` |

`code_version` = `13e5f9d5183f1b8e`（与云端相同，本分支未改）；F1 `scheme_hash` = `3f268258d997bbdc`。

## A6 · 10-31 池独立核对 — 通过

- `acceptance.py` 用生产函数 `liquidity_ranks` + `build_pool` 生成 `pool-20261031.json`，并以 `now=2026-11-01T04:00Z` 通过完整校验。
- `verify_pool.py` 只用 sqlite3、不 import `src`/`terminal`，用 SPY 交易日历重算（生产用"一半成员有价格"规则），逐只比对代码、周榜排名、评级、成交额排名、窗口：`verify.txt` → **expected 45 (STRICT 17); pool 45; differences: 0**。
- 窗口 2026-09-21 至 2026-10-30，30 个交易日；可排名覆盖 848/899 = 0.943；成交额可排名 898/899。
- 行业：科技 27、金融 6、工业 4、能源 3、医疗 2、通信/必需消费/可选消费各 1（不过滤时前 40 有 16 只能源）。

## A7 · 22 个冻结周榜回放 — 与预期一致

`history.json` 逐期记录只数、STRICT 数、可排名覆盖率、窗口与校验结果。每期只数与 2026-11-01 试算完全一致（26–50 只）。

- 2022-12-03 起 17 期：完整结构校验通过（关闭新鲜度）。
- 2021-09-04 至 2022-09-03 共 5 期：可排名覆盖率 0.834–0.891 < 0.90，校验按设计拒绝（`pool_ranked_coverage_below_floor`）。这是北极星的 90% 门槛，plan A7 已预先登记，不算验收失败；只影响历史回看，不影响以后每周发布。

## A8 · 回归 — 通过

- `code_version` 仍为 `13e5f9d5183f1b8e`。
- 景气全套：`pytest tests/test_prosperity_*.py tests/test_run_prosperity_wrapper.py -q` → 474 passed。
- 全量：同一 worktree、同一解释器（`.venv` Python 3.13）、同一命令 `pytest -q -rfE --tb=no -p no:cacheprovider`。
  基线 = 未改代码时的本 worktree（main `4324997d`）：`baseline-pytest.txt` 12 failed / 4188 passed / 4 skipped。
  本分支：`branch-pytest.txt` 12 failed / 4224 passed / 4 skipped（+36 为 M10 新测试）。
  `failure-diff.txt`：**新增 0，消失 0**，共有 12：

| 失败 nodeid | 原因 |
|---|---|
| `tests/test_breadth_buy_quality.py::test_load_event_dates_s1_active_snapshot` | 裸 worktree 无 `data/breadth_study_1b/daily_breadth.csv`（`FileNotFoundError`） |
| `tests/test_breadth_buy_quality.py::test_load_event_dates_s1_with_delisted_snapshot` | 同上 |
| `tests/test_breadth_buy_quality.py::test_load_event_dates_s2_active_snapshot` | 同上 |
| `tests/test_breadth_buy_quality.py::test_load_event_dates_s2_with_delisted_snapshot` | 同上 |
| `tests/test_breadth_buy_quality.py::test_events_csv_columns_and_rows` | 同上（研究数据文件缺失） |
| `tests/test_breadth_buy_quality.py::test_load_target_closes_respects_sample_window` | 同上（研究数据文件缺失） |
| `tests/test_breadth_buy_quality.py::test_all_days_baseline_shape` | 同上（研究数据文件缺失） |
| `tests/test_morning_report.py::TestFormatSectionD::test_missing_industry_uses_bucket_not_unclassified` | 裸 worktree 的概念注册表为空，L2 标签落到"其他"/"AI算力/云"，断言期望的 `计算芯片/GPU加速器` 不在输出里 |
| `tests/test_morning_report.py::TestFormatSectionD::test_dv_section_is_flat_with_l2_column_in_rank_order` | 同上（概念注册表依赖 live data） |
| `tests/test_morning_report.py::TestLayeredSections::test_dv_layered_section` | 同上 |
| `tests/test_morning_report.py::TestLayeredSections::test_layered_dv_groups_mu_under_l2_memory_chip_not_legacy_semi` | 同上 |
| `tests/test_morning_report.py::TestLayeredSections::test_rows_by_layer_and_bucket_no_silent_drop_for_unregistered` | 同上 |

与记忆 `project_worktree_needs_live_data_symlink`"裸 worktree 全量测试固定 12 个数据相关失败属预期"一致，均与 M10 无关。

## 复现

```bash
cd "/Users/owen/CC workspace/Finance/.worktrees/prosperity-m10"
PY="/Users/owen/CC workspace/Finance/.venv/bin/python"
"$PY" scripts/sync_prosperity.py --db /tmp/m10/prosperity.db
R=reports/prosperity/m10-pool-20261101
"$PY" $R/acceptance.py --market-db "/Users/owen/CC workspace/Finance/data/market.db" --db /tmp/m10/prosperity.db --out $R
/usr/bin/python3 $R/verify_pool.py --market-db "/Users/owen/CC workspace/Finance/data/market.db" --db /tmp/m10/prosperity.db --pool $R/pool-20261031.json
```

A9（云端首个自然周六 2026-11-07）待部署后核对。
