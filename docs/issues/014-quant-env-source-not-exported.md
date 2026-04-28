# 014 - Quant cron source .env 未 export 导致 Telegram 静默跳过

**日期**: 2026-04-27

## 现象

Quant crypto 日扫 cron 正常运行并生成日志，但 PMARP / RVOL / BTC NUPL 报告没有发到 Telegram。当天日志显示任务 `OK duration=165s`，但每个扫描器都打印：

```text
[警告] Telegram未配置，跳过发送
```

## 根因

`/root/workspace/Quant/scanners/run_daily_scan.sh` 里只执行了：

```bash
source /root/workspace/Quant/.env
```

这样变量只成为当前 shell 的普通变量，不会自动进入 `python3 daily_scan_all.py` 子进程环境。wrapper 自己的失败告警函数能读到变量，但 Python 扫描器里的 `os.environ.get("TELEGRAM_BOT_TOKEN")` / `TELEGRAM_CHAT_ID` 读不到。

## 修复

source `.env` 时开启自动 export：

```bash
set -a
source "$ENV_FILE"
set +a
```

云端修复前已备份：

```text
/root/workspace/Quant/scanners/run_daily_scan.sh.bak.20260427093058
```

## 验证

- `bash -n /root/workspace/Quant/scanners/run_daily_scan.sh` 通过。
- 手工补跑 `/root/workspace/Quant/scanners/run_daily_scan.sh` 成功。
- 日志确认 PMARP / RVOL / BTC NUPL 三段均出现 `[Telegram] 消息已发送`。

## 预防

所有 cron wrapper 读取 `.env` 后需要传给 Python/Node/子 shell 时，必须使用 `set -a; source .env; set +a`，或显式 `export` 需要的变量。只在 wrapper 本身使用的变量才可以不 export。

## Finance 同类检查

同日检查美股 Finance cron，发现 `/root/workspace/Finance/scripts/cron_wrapper.sh` 也只 `source "$ENV_FILE"`。当时云端 `.env` 中 `FMP_API_KEY` 与 Telegram 变量已有 `export`，所以晨报发送和 FMP 路径未暴露同样症状；但 `MARKETDATA_API_KEY`、`FRED_API_KEY`、`FINANCE_ENV` 没有 export，`portfolio_intelligence.py` 直接读取 `FINANCE_ENV`，存在同类 cron 风险。

已按同样方式修复 Finance 统一 wrapper，并同步到本地 `scripts/cron_wrapper.sh`：

```bash
set -a
source "$ENV_FILE"
set +a
```

验证 probe：修复后 Python 子进程可读到 `FMP_API_KEY`、`FRED_API_KEY`、`MARKETDATA_API_KEY`、`FINANCE_ENV`、`TELEGRAM_BOT_TOKEN`、`TELEGRAM_CHAT_ID`、`TELEGRAM_GROUP_CHAT_ID`。`FINNHUB_API_KEY` 在云端 `.env` 中未发现，需另行核对是否仍需要。
