"""Social Attention Indicators — 分析层（情绪分析 - 日频）

Two signals derived from Adanos social sentiment data:

1. Weighted Buzz Score: Reddit + X buzz weighted by mentions volume
2. Attention Z-Score: Today's combined mentions vs 20-day rolling mean/std
   (analogous to RVOL for volume)

Usage:
    from src.indicators.social_attention import (
        weighted_buzz, attention_zscore, get_social_signals,
    )
    signals = get_social_signals("NVDA")
"""
import logging
from typing import Any, Dict, List, Optional

from src.data.market_store import get_store

logger = logging.getLogger(__name__)

# Z-score thresholds (analogous to RVOL)
ZSCORE_ALERT = 2.0      # "注意力异动"
ZSCORE_EXTREME = 4.0    # "极端异动"
MIN_HISTORY_DAYS = 10   # Cold-start minimum
ROLLING_WINDOW = 20     # Trading days for mean/std


def weighted_buzz(
    reddit_buzz: Optional[float],
    reddit_mentions: Optional[int],
    x_buzz: Optional[float],
    x_mentions: Optional[int],
) -> Optional[float]:
    """Compute mentions-weighted buzz score across Reddit and X.

    Whichever source has more mentions gets more weight.
    If one source is missing, returns the other directly.
    """
    r_ok = reddit_buzz is not None and reddit_mentions is not None and reddit_mentions > 0
    x_ok = x_buzz is not None and x_mentions is not None and x_mentions > 0

    if r_ok and x_ok:
        total = reddit_mentions + x_mentions
        return (reddit_buzz * reddit_mentions + x_buzz * x_mentions) / total
    elif r_ok:
        return reddit_buzz
    elif x_ok:
        return x_buzz
    return None


def attention_zscore(
    mentions_history: List[int],
    window: int = ROLLING_WINDOW,
) -> Optional[float]:
    """Compute z-score of latest day's mentions vs rolling window.

    Args:
        mentions_history: List of daily combined mentions, newest first.
        window: Rolling window for mean/std calculation.

    Returns:
        Z-score float, or None if insufficient data.
    """
    if len(mentions_history) < MIN_HISTORY_DAYS:
        return None

    current = mentions_history[0]
    # Use up to `window` days excluding today for baseline
    baseline = mentions_history[1:window + 1]
    if len(baseline) < MIN_HISTORY_DAYS - 1:
        return None

    mean = sum(baseline) / len(baseline)
    variance = sum((x - mean) ** 2 for x in baseline) / len(baseline)
    std = variance ** 0.5

    if std < 1e-6:
        return 0.0

    return (current - mean) / std


def _get_daily_mentions(
    symbol: str,
    days: int = ROLLING_WINDOW + 5,
) -> List[Dict[str, Any]]:
    """Fetch daily combined mentions (reddit + x) from market.db.

    Returns list of {date, reddit_mentions, x_mentions, combined,
    reddit_buzz, x_buzz} dicts, newest first.
    """
    store = get_store()
    rows = store.get_social_sentiment(symbol, limit=days * 2)
    if not rows:
        return []

    # Group by date, combining reddit + x
    by_date: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        d = row["date"]
        if d not in by_date:
            by_date[d] = {
                "date": d,
                "reddit_mentions": 0, "x_mentions": 0,
                "reddit_buzz": None, "x_buzz": None,
                "reddit_sentiment": None, "x_sentiment": None,
                "reddit_bullish": None, "x_bullish": None,
                "reddit_bearish": None, "x_bearish": None,
                "reddit_trend": None, "x_trend": None,
            }

        src = row["source"]
        mentions = row.get("total_mentions") or 0
        if src == "reddit":
            by_date[d]["reddit_mentions"] = mentions
            by_date[d]["reddit_buzz"] = row.get("buzz_score")
            by_date[d]["reddit_sentiment"] = row.get("sentiment_score")
            by_date[d]["reddit_bullish"] = row.get("bullish_pct")
            by_date[d]["reddit_bearish"] = row.get("bearish_pct")
            by_date[d]["reddit_trend"] = row.get("trend")
        elif src == "x":
            by_date[d]["x_mentions"] = mentions
            by_date[d]["x_buzz"] = row.get("buzz_score")
            by_date[d]["x_sentiment"] = row.get("sentiment_score")
            by_date[d]["x_bullish"] = row.get("bullish_pct")
            by_date[d]["x_bearish"] = row.get("bearish_pct")
            by_date[d]["x_trend"] = row.get("trend")

    for d in by_date:
        by_date[d]["combined"] = by_date[d]["reddit_mentions"] + by_date[d]["x_mentions"]

    # Sort newest first
    result = sorted(by_date.values(), key=lambda x: x["date"], reverse=True)
    return result


def get_social_signals(symbol: str) -> Dict[str, Any]:
    """Compute all social attention signals for a symbol.

    Returns dict with:
        - weighted_buzz: float or None
        - attention_zscore: float or None
        - combined_mentions: int (today)
        - reddit_mentions / x_mentions: int (today)
        - reddit_buzz / x_buzz: float (today)
        - reddit_sentiment / x_sentiment: float (today)
        - reddit_bullish / x_bullish: int % (today)
        - reddit_bearish / x_bearish: int % (today)
        - reddit_trend / x_trend: str (today)
        - alert_level: "extreme" | "alert" | None
        - has_data: bool
    """
    daily = _get_daily_mentions(symbol)

    if not daily:
        return {"has_data": False, "symbol": symbol}

    latest = daily[0]
    mentions_history = [d["combined"] for d in daily]

    wb = weighted_buzz(
        latest["reddit_buzz"], latest["reddit_mentions"],
        latest["x_buzz"], latest["x_mentions"],
    )
    az = attention_zscore(mentions_history)

    alert_level = None
    if az is not None:
        if az >= ZSCORE_EXTREME:
            alert_level = "extreme"
        elif az >= ZSCORE_ALERT:
            alert_level = "alert"

    return {
        "symbol": symbol,
        "has_data": True,
        "date": latest["date"],
        "weighted_buzz": round(wb, 1) if wb is not None else None,
        "attention_zscore": round(az, 2) if az is not None else None,
        "combined_mentions": latest["combined"],
        "reddit_mentions": latest["reddit_mentions"],
        "x_mentions": latest["x_mentions"],
        "reddit_buzz": latest["reddit_buzz"],
        "x_buzz": latest["x_buzz"],
        "reddit_sentiment": latest["reddit_sentiment"],
        "x_sentiment": latest["x_sentiment"],
        "reddit_bullish": latest["reddit_bullish"],
        "x_bullish": latest["x_bullish"],
        "reddit_bearish": latest["reddit_bearish"],
        "x_bearish": latest["x_bearish"],
        "reddit_trend": latest["reddit_trend"],
        "x_trend": latest["x_trend"],
        "alert_level": alert_level,
    }


def scan_social_signals(symbols: List[str]) -> Dict[str, Any]:
    """Scan all symbols for social attention signals.

    Returns dict with:
        - alerts: list of symbols with Z >= 2.0
        - extreme_sentiment: list of symbols with bullish >= 60 or <= 20
        - trend_reversals: list of symbols where reddit/x trend diverge
        - all_signals: {symbol: signals_dict}
    """
    all_signals = {}
    alerts = []
    extreme_sentiment = []
    trend_reversals = []

    for sym in symbols:
        sig = get_social_signals(sym)
        if not sig.get("has_data"):
            continue

        all_signals[sym] = sig

        # Alert: attention anomaly
        if sig.get("alert_level"):
            alerts.append(sig)

        # Extreme sentiment
        r_bull = sig.get("reddit_bullish")
        x_bull = sig.get("x_bullish")
        for bull, src in [(r_bull, "reddit"), (x_bull, "x")]:
            if bull is not None and (bull >= 60 or bull <= 20):
                extreme_sentiment.append({
                    "symbol": sym,
                    "source": src,
                    "bullish_pct": bull,
                    "bearish_pct": sig.get("{}_bearish".format(src)),
                })

        # Trend reversal: reddit and x diverge
        r_trend = sig.get("reddit_trend")
        x_trend = sig.get("x_trend")
        if r_trend and x_trend and r_trend != x_trend:
            trend_reversals.append({
                "symbol": sym,
                "reddit_trend": r_trend,
                "x_trend": x_trend,
            })

    # Sort alerts by z-score descending
    alerts.sort(key=lambda x: x.get("attention_zscore") or 0, reverse=True)

    return {
        "alerts": alerts,
        "extreme_sentiment": extreme_sentiment,
        "trend_reversals": trend_reversals,
        "all_signals": all_signals,
        "symbols_with_data": len(all_signals),
    }
