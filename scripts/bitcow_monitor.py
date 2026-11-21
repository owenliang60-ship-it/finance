from __future__ import annotations

# BTC 奶牛（BitCow）日线信号监控：慢层 50W SMA + 中层 EMA84±ATR14 + 偏离 D + DVOL。
# 核心计算只用标准库；网络用 requests；投递用 src.telegram_bot（延迟导入）。

import argparse
import json
import logging
import math
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NamedTuple, Sequence

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DAY_MS = 86_400_000
MIN_DAILY_BARS = 900
BINANCE_KLINES_URL = "https://data-api.binance.vision/api/v3/klines"
DERIBIT_DVOL_URL = "https://www.deribit.com/api/v2/public/get_volatility_index_data"
LABEL_RE = re.compile(r"^[A-Za-z0-9-]{1,20}$")
MAX_RETRY_AFTER_SEC = 15

logger = logging.getLogger("bitcow_monitor")


class DataError(RuntimeError):
    pass


class Bar(NamedTuple):
    open_ms: int
    high: float
    low: float
    close: float


def _retry_after(resp: Any) -> float:
    headers = getattr(resp, "headers", None) or {}
    ra = headers.get("Retry-After") or headers.get("retry-after")
    if ra is None:
        return 0.0
    try:
        wait = float(ra)
    except (TypeError, ValueError):
        wait = math.nan
    if not (math.isfinite(wait) and 0 <= wait <= MAX_RETRY_AFTER_SEC):
        raise DataError("HTTP 429 Retry-After 超限")
    return wait


def fetch_json(url: str, params: dict[str, Any], timeout: int = 20, max_retries: int = 3) -> Any:
    reason = ""
    for attempt in range(1, max_retries + 1):
        wait = float(2 ** attempt)
        try:
            resp = requests.get(url, params=params, timeout=timeout)
        except (requests.ConnectionError, requests.Timeout) as exc:
            reason = type(exc).__name__
        except requests.RequestException as exc:
            raise DataError("请求失败: {}".format(type(exc).__name__)) from None
        else:
            status = resp.status_code
            if status == 429 or status >= 500:
                reason = "HTTP {}".format(status)
                if status == 429:
                    wait = max(wait, _retry_after(resp))
            elif status >= 400:
                raise DataError("HTTP {}".format(status))
            else:
                try:
                    return resp.json()
                except ValueError:
                    raise DataError("JSON 解析失败") from None
        if attempt < max_retries:
            logger.warning("fetch_retry attempt=%d reason=%s", attempt, reason)
            time.sleep(wait)
    raise DataError("重试耗尽: {}".format(reason))


def closed_daily_bars(raw: list[Any], now_ms: int) -> list[Bar]:
    if not isinstance(raw, list):
        raise DataError("K线响应不是列表")
    bars: list[Bar] = []
    for row in raw:
        try:
            if int(row[6]) >= now_ms:
                continue
            bar = Bar(int(row[0]), float(row[2]), float(row[3]), float(row[4]))
        except (TypeError, ValueError, IndexError, KeyError):
            raise DataError("K线行格式非法") from None
        if not all(math.isfinite(x) and x > 0 for x in bar[1:]):
            raise DataError("K线价格非有限正数")
        if not (bar.high >= bar.low and bar.low <= bar.close <= bar.high):
            raise DataError("K线 OHLC 不一致")
        bars.append(bar)
    for a, b in zip(bars, bars[1:]):
        if b.open_ms - a.open_ms != DAY_MS:
            raise DataError("K线重复、缺口或乱序")
    if len(bars) < MIN_DAILY_BARS:
        raise DataError("已收盘日线不足 {} 根".format(MIN_DAILY_BARS))
    if bars[-1].open_ms != (now_ms // DAY_MS - 1) * DAY_MS:
        raise DataError("末根日线不是 UTC 昨日")
    return bars


def ema_atr(bars: Sequence[Bar], ema_len: int = 84, atr_len: int = 14) -> tuple[list[float], list[float]]:
    if len(bars) < max(ema_len, atr_len):
        raise DataError("K线数量不足以计算 EMA/ATR")
    k = 2.0 / (ema_len + 1)
    ema = [bars[0].close]
    atr = [bars[0].high - bars[0].low]
    for i in range(1, len(bars)):
        h, lo, c, pc = bars[i].high, bars[i].low, bars[i].close, bars[i - 1].close
        ema.append(c * k + ema[-1] * (1.0 - k))
        tr = max(h - lo, abs(h - pc), abs(lo - pc))
        atr.append((atr[-1] * (atr_len - 1) + tr) / atr_len)
    if not all(math.isfinite(x) and x > 0 for x in atr[-2:]):
        raise DataError("ATR 非正或非有限")
    return ema, atr


def mid_step(prev_state: int, close: float, ema: float, atr: float) -> tuple[int, str | None]:
    upper, lower = ema + atr, ema - atr
    if close > upper:
        new = 1
    elif close < lower:
        new = -1
    else:
        new = prev_state
    if prev_state == 0:
        return new, None
    if prev_state == 1:
        if new == -1:
            return -1, "flip_short"
        if close - lower < 0.5 * atr:
            return 1, "warn_long_near"
        if close < ema:
            return 1, "warn_long_mid"
        return 1, None
    if new == 1:
        return 1, "flip_long"
    if upper - close < 0.5 * atr:
        return -1, "warn_short_near"
    if close > ema:
        return -1, "warn_short_mid"
    return -1, None


def _utc(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def _day(ms: int) -> str:
    return _utc(ms).date().isoformat()


def slow_layer(bars: Sequence[Bar], sma_len: int = 50) -> dict[str, Any]:
    weeks = [b for b in bars if _utc(b.open_ms).weekday() == 6]
    if len(weeks) < sma_len + 1:
        raise DataError("周线数量不足以计算 50W SMA")
    since = _day(weeks[sma_len - 1].open_ms)
    bulls: list[bool] = []
    sma = 0.0
    for i in range(sma_len - 1, len(weeks)):
        sma = sum(w.close for w in weeks[i - sma_len + 1 : i + 1]) / sma_len
        bull = weeks[i].close > sma
        if bulls and bull != bulls[-1]:
            since = _day(weeks[i].open_ms)
        bulls.append(bull)
    flipped = None
    if _utc(bars[-1].open_ms).weekday() == 6 and bulls[-1] != bulls[-2]:
        flipped = "bull" if bulls[-1] else "bear"
    return {
        "bull": bulls[-1],
        "wclose": weeks[-1].close,
        "sma50": sma,
        "weekly_date": _day(weeks[-1].open_ms),
        "since": since,
        "flipped": flipped,
        "daily_vs_sma50_pct": (bars[-1].close / sma - 1.0) * 100.0,
    }


def d_level(d: float) -> int:
    a = abs(d)
    if a >= 7.0:
        return 3
    if a >= 6.5:
        return 2
    if a >= 4.5:
        return 1
    return 0


WARN_SEV = {None: 0, "warn_long_mid": 1, "warn_short_mid": 1, "warn_long_near": 2, "warn_short_near": 2}
WARN_DESC = {
    "warn_long_near": "极近多头ATR止损（距边界不足0.5 ATR）",
    "warn_long_mid": "跌破EMA84中线，进入止损警戒区",
    "warn_short_near": "极近空头ATR止损（距边界不足0.5 ATR）",
    "warn_short_mid": "站上EMA84中线，进入止损警戒区",
}
WARN_TEXT = {k: "中层预警：" + v for k, v in WARN_DESC.items()}
FLIP_TEXT = {
    "flip_short": "触发多头ATR止损 / 中层翻空：收盘跌破下沿",
    "flip_long": "触发空头ATR止损 / 中层翻多：收盘站上上沿",
}
SLOW_TEXT = {"bull": "慢层翻多：周线收盘站上50W SMA", "bear": "慢层翻空：周线收盘跌破50W SMA"}
D_TIER = {
    (1, True): "+4.5 档（过热对冲）",
    (1, False): "-4.5 档（深跌警戒）",
    (2, True): "+6.5 档（强止盈）",
    (2, False): "-6.5 档（极端恐慌）",
    (3, True): "+7.0 档（历史极值）",
    (3, False): "-7.0 档（历史极值）",
}


def evaluate(bars: Sequence[Bar], dvol: dict[str, Any]) -> dict[str, Any]:
    ema, atr = ema_atr(bars)
    state, mid_since = 0, None
    states: list[int] = []
    events: list[str | None] = []
    for i, b in enumerate(bars):
        prev = state
        state, event = mid_step(prev, b.close, ema[i], atr[i])
        if event in FLIP_TEXT or (prev == 0 and state != 0 and mid_since is None):
            mid_since = _day(b.open_ms)
        states.append(state)
        events.append(event)
    if states[-1] == 0:
        raise DataError("中层状态未初始化")
    slow = slow_layer(bars)
    close, e, a = bars[-1].close, ema[-1], atr[-1]
    upper, lower = e + a, e - a
    d = (close - e) / a
    prev_d = (bars[-2].close - ema[-2]) / atr[-2]
    lvl, prev_lvl = d_level(d), d_level(prev_d)
    event = events[-1]

    alerts: list[str] = []
    if slow["flipped"]:
        alerts.append(SLOW_TEXT[slow["flipped"]])
    if event in FLIP_TEXT:
        alerts.append(FLIP_TEXT[event])
    elif WARN_SEV.get(event, 0) > 0 and states[-2] == states[-1] and WARN_SEV[event] > WARN_SEV.get(events[-2], 0):
        alerts.append(WARN_TEXT[event])
    if lvl > 0 and (lvl > prev_lvl or prev_lvl == 0 or (d > 0) != (prev_d > 0)):
        alerts.append("偏离D={:+.2f} 进入 {}".format(d, D_TIER[(lvl, d > 0)]))

    if states[-1] == 1:
        stop_price, stop_dist_atr, stop_dist_pct = lower, (close - lower) / a, (close - lower) / close * 100.0
    else:
        stop_price, stop_dist_atr, stop_dist_pct = upper, (upper - close) / a, (upper / close - 1.0) * 100.0
    return {
        "as_of": _day(bars[-1].open_ms),
        "close": close,
        "slow": slow,
        "mid_state": states[-1],
        "mid_since": mid_since,
        "mid_event": event,
        "ema84": e,
        "atr14": a,
        "upper": upper,
        "lower": lower,
        "stop_price": stop_price,
        "stop_dist_atr": stop_dist_atr,
        "stop_dist_pct": stop_dist_pct,
        "d": d,
        "d_level": lvl,
        "d_plus7": e + 7.0 * a,
        "d_minus7": e - 7.0 * a,
        "dvol": dvol,
        "alerts": alerts,
    }


def _dvol_na(reason: str) -> dict[str, Any]:
    return {"available": False, "value": None, "percentile": None, "reason": reason}


def dvol_stats(raw_rows: list[Any], as_of_ms: int, now_ms: int, window_days: int = 365) -> dict[str, Any]:
    closes: dict[int, float] = {}
    for row in raw_rows:
        try:
            ts, val = int(row[0]), float(row[4])
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        if ts + DAY_MS > now_ms or not (math.isfinite(val) and val > 0):
            continue
        closes[ts] = val
    keys = sorted(closes)
    if not keys or keys[-1] != as_of_ms:
        return _dvol_na("数据过期")
    expected = [as_of_ms - (window_days - 1 - i) * DAY_MS for i in range(window_days)]
    if not all(k in closes for k in expected):
        return _dvol_na("样本不足")
    vals = [closes[k] for k in expected]
    current = vals[-1]
    pct = 100.0 * sum(1 for v in vals if v <= current) / len(vals)
    return {"available": True, "value": current, "percentile": pct, "reason": ""}


def fetch_dvol(fetch: Any, now_ms: int, as_of_ms: int) -> dict[str, Any]:
    params = {
        "currency": "BTC",
        "start_timestamp": now_ms - 400 * DAY_MS,
        "end_timestamp": now_ms,
        "resolution": "86400",
    }
    try:
        rows = fetch(DERIBIT_DVOL_URL, params)["result"]["data"]
        return dvol_stats(rows, as_of_ms, now_ms)
    except Exception as exc:
        logger.warning("dvol_unavailable error=%s", type(exc).__name__)
        return _dvol_na("接口异常")


def _check_label(label: str) -> str:
    if label and not LABEL_RE.fullmatch(label):
        raise ValueError("label 只允许 1-20 位字母、数字或连字符")
    return label


def render(report: dict[str, Any], label: str = "") -> str:
    _check_label(label)
    tag = "【{}】".format(label) if label else ""
    r, slow, dvol = report, report["slow"], report["dvol"]
    alerts = r["alerts"]
    if alerts:
        lines = ["🚨 *{}【奶牛信号报警】BTC 日线 ({})*".format(tag, r["as_of"])]
        lines += ["• " + a for a in alerts] + [""]
    else:
        lines = ["📊 *{}【奶牛日线状态】BTC ({}) · 无新信号*".format(tag, r["as_of"])]
    lines.append("收盘：{:,.2f} USDT（{} UTC 日线）".format(r["close"], r["as_of"]))
    lines.append(
        "慢层：{} · 自 {} · 周收盘 {:,.2f} vs 50W SMA {:,.2f}（{}）· 日收盘偏离 {:+.2f}%".format(
            "多头" if slow["bull"] else "空头", slow["since"], slow["wclose"], slow["sma50"],
            slow["weekly_date"], slow["daily_vs_sma50_pct"],
        )
    )
    long_side = r["mid_state"] == 1
    mid = "中层：{} · 自 {} · EMA84 {:,.2f} · 通道 {:,.2f} 至 {:,.2f} · ATR止损沿（{}）{:,.2f} 距离 {:.2f} ATR / {:.2f}%".format(
        "多头" if long_side else "空头", r["mid_since"], r["ema84"], r["lower"], r["upper"],
        "下沿" if long_side else "上沿", r["stop_price"], r["stop_dist_atr"], r["stop_dist_pct"],
    )
    event = r["mid_event"]
    if event in WARN_DESC:
        mid += " · {}：{}".format("新进警戒" if WARN_TEXT[event] in alerts else "持续警戒", WARN_DESC[event])
    lines.append(mid)
    tier = D_TIER.get((r["d_level"], r["d"] > 0), "常态区间")
    lines.append("偏离D：{:+.2f} · {} · D=+7 价 {:,.2f} · D=-7 价 {:,.2f}".format(r["d"], tier, r["d_plus7"], r["d_minus7"]))
    if dvol.get("available"):
        lines.append("DVOL：{:.2f} · 1年分位 {:.1f}%".format(dvol["value"], dvol["percentile"]))
    else:
        lines.append("DVOL：⚠️ 不可用（{}）".format(dvol.get("reason") or "未知"))
    text = "\n".join(lines)
    unsafe = any(ch in text for ch in "_[`") or any(line.count("*") % 2 for line in text.splitlines())
    if unsafe or len(text) >= 4000:
        raise DataError("消息不满足 Telegram Markdown v1 安全约束")
    return text


def validate_state_path(path: Path) -> Path:
    if Path(path).suffix.lower() != ".json":
        raise ValueError("state 路径必须是 .json 文件")
    return Path(path)


def load_state(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except ValueError:
        logger.warning("state_corrupt path=%s", path)
        return {}
    if not isinstance(data, dict):
        logger.warning("state_corrupt path=%s", path)
        return {}
    return data


def save_state(path: Path, as_of: str, alert_count: int) -> None:
    path = validate_state_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=".state-", suffix=".tmp", delete=False)
    try:
        with tmp:
            json.dump({"last_sent_date": as_of, "alert_count": alert_count}, tmp)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp.name, path)
    except BaseException:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        raise


def run(
    now_ms: int,
    state_path: Path,
    dry_run: bool = False,
    force: bool = False,
    label: str = "",
    fetch: Any = fetch_json,
    send: Any = None,
) -> int:
    state_path = validate_state_path(state_path)
    _check_label(label)
    as_of_ms = (now_ms // DAY_MS - 1) * DAY_MS
    expected = _day(as_of_ms)
    if not dry_run and not force and load_state(state_path).get("last_sent_date") == expected:
        logger.info("already_sent date=%s", expected)
        return 0
    try:
        raw = fetch(BINANCE_KLINES_URL, {"symbol": "BTCUSDT", "interval": "1d", "limit": 1000})
        bars = closed_daily_bars(raw, now_ms)
    except Exception as exc:
        logger.error("binance_unavailable error=%s detail=%s", type(exc).__name__, exc)
        return 1
    dvol = fetch_dvol(fetch, now_ms, as_of_ms)
    try:
        report = evaluate(bars, dvol)
        text = render(report, label)
    except DataError as exc:
        logger.error("evaluate_failed detail=%s", exc)
        return 1
    if dry_run:
        print(text)
        return 0
    if send is None:
        from src.telegram_bot import send_message as send
    if not send(text, channel="private"):
        logger.error("send_failed date=%s", expected)
        return 1
    save_state(state_path, report["as_of"], len(report["alerts"]))
    logger.info("sent date=%s alerts=%d", expected, len(report["alerts"]))
    return 0


def _label_arg(value: str) -> str:
    if not LABEL_RE.fullmatch(value):
        raise argparse.ArgumentTypeError("label 只允许 1-20 位字母、数字或连字符")
    return value


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(description="BTC 奶牛日线信号监控（Telegram 私聊）")
    parser.add_argument("--dry-run", action="store_true", help="只打印消息，不发送、不写 state")
    parser.add_argument("--force", action="store_true", help="忽略当日防重，强制重发")
    parser.add_argument("--label", type=_label_arg, default=None, help="消息首行标签（字母/数字/连字符）")
    parser.add_argument(
        "--state-path", type=Path, default=PROJECT_ROOT / "data" / "bitcow_monitor" / "state.json"
    )
    args = parser.parse_args(argv)
    try:
        return run(
            int(time.time() * 1000), args.state_path,
            dry_run=args.dry_run, force=args.force, label=args.label or "",
        )
    except ValueError as exc:
        logger.error("invalid_argument detail=%s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
