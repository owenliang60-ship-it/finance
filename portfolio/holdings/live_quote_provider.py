"""Live quote provider for Portfolio Intelligence NAV pricing."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import Any, Dict, List, Optional, Tuple

from terminal.options.occ_symbol import build_occ_symbol

logger = logging.getLogger(__name__)


def _extract_credit_headers(headers: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    normalized = {str(k).lower(): str(v) for k, v in headers.items()}
    used = (
        normalized.get("x-api-cost")
        or normalized.get("x-request-cost")
        or normalized.get("x-credit-cost")
    )
    remaining = (
        normalized.get("x-api-quota-remaining")
        or normalized.get("x-api-credits-remaining")
        or normalized.get("x-ratelimit-remaining")
    )
    return used, remaining


@dataclass
class QuoteResult:
    prices: Dict[Any, float] = field(default_factory=dict)
    failed: List[Any] = field(default_factory=list)
    quote_meta: Dict[Any, Dict[str, Any]] = field(default_factory=dict)
    request_count: int = 0
    credit_header_available: bool = False
    credits_used: Optional[str] = None
    credits_remaining: Optional[str] = None

    def record_headers(self, headers: Dict[str, Any]) -> None:
        used, remaining = _extract_credit_headers(headers)
        if used is not None or remaining is not None:
            self.credit_header_available = True
            self.credits_used = used
            self.credits_remaining = remaining


def _pick_price(quote: Dict[str, Any]) -> Tuple[Optional[float], Optional[str]]:
    if quote.get("mid") is not None:
        return float(quote["mid"]), "mid"
    if quote.get("last") is not None:
        return float(quote["last"]), "last"
    bid = quote.get("bid")
    ask = quote.get("ask")
    if bid is not None and ask is not None:
        return (float(bid) + float(ask)) / 2, "bbo_mid"
    return None, None


def fetch_stock_live_quotes(symbols: List[str], client=None) -> QuoteResult:
    """Fetch live stock quotes via MarketData with partial failure handling."""
    result = QuoteResult()
    if not symbols:
        return result
    if client is None:
        from src.data.marketdata_client import MarketDataClient
        client = MarketDataClient()

    for sym in symbols:
        result.request_count += 1
        try:
            payload = client.get_stock_quote_with_meta(sym)
        except Exception as exc:
            logger.warning("MarketData stock quote exception for %s: %s", sym, exc)
            result.failed.append(sym)
            continue

        if not payload:
            logger.warning("MarketData returned no data for %s", sym)
            result.failed.append(sym)
            continue

        result.record_headers(payload.get("headers", {}))
        price, field = _pick_price(payload.get("quote", {}))
        if price is None:
            result.failed.append(sym)
            continue

        result.prices[sym] = price
        result.quote_meta[sym] = {
            "price_field": field,
            "raw_status": payload.get("raw", {}).get("s"),
            "updated": payload.get("raw", {}).get("updated"),
        }
        logger.info("[live] %s = $%.2f (%s)", sym, price, field)

    return result


def fetch_option_live_quotes(positions: List[dict], client=None) -> QuoteResult:
    """Fetch live option quotes via MarketData using OCC symbols."""
    result = QuoteResult()
    if not positions:
        return result
    if client is None:
        from src.data.marketdata_client import MarketDataClient
        client = MarketDataClient()

    for position in positions:
        result.request_count += 1
        key = (
            position["symbol"],
            position["expiration"],
            position["strike"],
            position["side"],
        )
        try:
            occ = build_occ_symbol(
                position["symbol"],
                position["expiration"],
                position["strike"],
                position["side"],
            )
        except Exception as exc:
            logger.warning("Failed to build OCC for %s: %s", key, exc)
            result.failed.append(key)
            continue

        try:
            payload = client.get_options_quote_with_meta(occ)
        except Exception as exc:
            logger.warning("MarketData option quote exception for %s: %s", occ, exc)
            result.failed.append(key)
            continue

        if not payload or payload.get("raw", {}).get("s") != "ok":
            result.failed.append(key)
            continue

        result.record_headers(payload.get("headers", {}))
        price, field = _pick_price(payload.get("quote", {}))
        if price is None:
            result.failed.append(key)
            continue

        result.prices[key] = price
        result.quote_meta[key] = {
            "occ": occ,
            "price_field": field,
            "updated": payload.get("raw", {}).get("updated"),
        }
        logger.info("[live] option %s = $%.2f (%s)", occ, price, field)

    return result
