---
name: vol-scan
description: "确定性期权波动率扫描：在云端只读跑 scripts/vol_scan.py，输出 HV 面板、振幅收缩、逐到期日 ATM IV、期限结构与远期波动率、25Δ Skew、30D IV 分位（带新鲜度门控）、财报与板块事件位置、ATM Calendar 模板与 Mid/Natural 情景盈亏网格。Use when the user runs /vol-scan or asks 扫一下波动率、IV 扫描、期限结构、哪个标的适合做 calendar. Not for 构建交易卡 (options) or 深度研究 (deep-analysis)."
invocation: user
arguments:
  - name: symbols
    required: false
  - name: legs
    required: false
---

# /vol-scan — 确定性期权波动率扫描

本 skill 只搬运脚本算出的数字。脚本不打分、不给胜率、不给赔率。你也不给。

入口：`scripts/vol_scan.py`（纯计算在 `terminal/options/vol_scan.py`）。仓库内正本：`docs/skills/vol-scan/SKILL.md`。改动先改仓库，再复制到 `~/.claude/skills/vol-scan/`。

默认标的：`config/settings.py:VOL_SCAN_SYMBOLS`（QQQ, SOXX, NVDA, MU, AMD, TSM）。

## 固定流程（每步一个动作）

1. 在云端运行扫描（MarketData.app 只认云端 IP；本地不带 `--chain-dir` / `--no-chain` 会以退出码 2 拒绝）：
   `ssh aliyun "cd /root/workspace/Finance && python3 scripts/vol_scan.py --json"`
   指定标的时加 `--symbols NVDA,MU`。
2. 读 `symbols.<SYM>.gates`，逐项记下门控状态（见下表）。
3. 列出所有 35 天内、`earnings.date_status == "ESTIMATED"` 的财报日（自身 + `sector_events.events`）。逐个去公司官方 IR 页面核对。
4. 官方已公告的日期，写入 `config/earnings_confirmed.json`（字段 `earnings_date` / `source` / `verified_at`），走代码 worktree 提交，部署后回到第 1 步。官方未公告的，回复里标「估计日期，未经官方确认」。
5. Boss 要看自定义结构时，带 `--legs` 重跑第 1 步。格式：`"NVDA:2026-12-18:C:150:-1;NVDA:2027-01-15:C:150:+1"`（标的:到期日:C|P:行权价:带符号张数）。
6. 回复：每个标的一段，只用下方允许的字段。

## 门控（gates）

| 字段 | 值 | 回复中的处理 |
|------|----|-------------|
| `chain` | `LIVE` / `REPLAY` / `MISSING` / `ERROR` / `SKIPPED` | 非 `LIVE` 时：不报任何 bid/ask、IV 与盈亏网格为「非实时」 |
| `earnings` | `CLEAR` / `CAUTION` / `T5_WARNING` / `BLACKOUT` / `UNKNOWN` | `UNKNOWN` 写「财报日未知，需人工核对」，不得写成安全 |
| `earnings_date_status` | `CONFIRMED` / `ESTIMATED` / `NOT_APPLICABLE` / `UNKNOWN` | 必须原样标出 |
| `iv` | `FRESH` / `STALE` / `MISSING` | 非 `FRESH` 时 30D IV rank / percentile 为 N/A，写出 `iv_history.reason` |
| `iv_30d_live` | `OK` / `UNAVAILABLE` | `UNAVAILABLE` 时写出 `atm_iv.iv_30d_live_reason` |
| `price_data` | `FRESH` / `STALE` / `MISSING` | 非 `FRESH` 时 HV 与振幅标「数据过期」并写出 `hv.latest_price_date` |
| `split_suspect` | true / false | true 时列出 `hv.split_anomalies` |
| `sector_unknown_events` | 列表 | 非空时逐个写「财报日未知」 |

## 输出约束（反信口开河）

- 只引用 JSON 里存在的字段。每个数字写出字段名或含义，例如「30D IV（总方差插值）22.5%」。
- 分位一律写明期限：「30D IV 252 日分位」。不得出现不带期限的「IV 分位」。
- 期限结构只写 `state`、`front_minus_back_pt`、`forwards[].forward_iv`、`inversion_source` 与 `inversion_events`。`inversion_source` 是事实标注：只说「近月前有一个已确认 / 估计的财报」，不推断倒挂成因。
- `forward_iv` 为 null 且 `flag == "BAD_QUOTE"` 时写「报价异常，远期方差为负」，不补数。
- 盈亏网格只报 Mid 与 Natural 两列原值，注明单位是每股（× 100 为每张）和模型假设 `model_assumptions`。
- 禁止：综合评分、胜率、赔率、「便宜 / 贵」「值得做」等判词、任何没有出现在 JSON 里的数字。
- 交易卡与推荐交给 `/options`；本 skill 不出交易卡。

## 约定

- 期权时间 T = DTE / 365（日历日）；HV 用对数收益 × √252。
- IV 是小数（0.45 = 45%）；`*_pt` 字段是波动率点。
- 情景 `iv_scenarios` 的变动是绝对波动率点（−0.10 = 降 10 点），新 IV 下限 1%。
- Natural 口径：进场按对手价，出场每腿扣半个价差。
- 脚本只读：`market.db` 以 `mode=ro` 打开，不写任何数据库，不存快照。每标的 1 次链请求，额度头写在 `chain.meta`。
