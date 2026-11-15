# Task 1.3 — REST boundary and symbol rules

Verified against Binance official documentation on **2026-10-20 (Asia/Singapore)**. This is a code contract, not demo protocol evidence. No authenticated request or trade was made for this task.

## Client contract

```python
client = BinanceClient(env='demo', credentials=None, transport=None,
                       limiter=None, clock=time.time, allow_demo_writes=False)
info = client.request('GET', Endpoints.EXCHANGE_INFO)
# For authenticated reads or explicitly approved future demo probes:
client.sync_clock()
result = client.request(method, path, params=None, signed=False, protective=False)
```

`transport` implements `requests.Session.request`. Reservations and response-state updates hold the shared limiter lock; HTTP runs outside it. Concurrent submissions use separate clients/transports sharing one limiter. The action layer enforces the 5+5 lanes. Tests inject transport, clock, and limiter; they use no network. Default clients share a limiter per environment within a process. The lifecycle owner must persist and restore ban/pause state across cron processes in stage 2. This client alone is not the live scheduler or durable action ledger.

Production non-GET calls always raise `NOT_SENT`, even if `allow_demo_writes=True`. Demo writes need the explicit flag. Paths and methods have an allowlist. Redirects are disabled. All private paths require signing. `load_credentials(path)` accepts only a caller-owned regular file with mode `0600`; symlinks are refused. Credentials have redacted repr. Do not log the transport object, signed URL/body, headers, or local variables.

Return value is the decoded JSON dictionary/list. It means the API acknowledged the request; the owner must validate endpoint response fields and order identity before recording ACKED. It does not establish a fill or zero position.

`ExchangeError` exposes `outcome`, `code`, `http_status`, `retry_after`:

| Outcome | Caller action |
|---|---|
| `NOT_SENT` | Local gate blocked transport. Keep the durable action unsent. |
| `REJECTED` | HTTP 4xx / negative API code, except timeout/disconnect codes. Record the code; classify at action layer. |
| `UNKNOWN` | Timeout, connection failure, 408, any 5xx, -1001/-1006/-1007, malformed JSON or unexpected reply. Query by identity before deciding anything. |

There is **no automatic retry**, including no mutation retry. Server text and transport exception text are excluded from outward errors. The conservative classification keeps every 5xx UNKNOWN even where Binance documents some definite failure variants.

## Endpoint map

Constants live in `binance.Endpoints`. The following paths were read from the [official trading reference](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/trade).

| Constant | Method / path | Relevant request fields |
|---|---|---|
| `ORDER` | GET/POST/DELETE `/fapi/v1/order` | `symbol`; new: `side,type,quantity,newClientOrderId,reduceOnly`; query/cancel: `orderId` or `origClientOrderId` |
| `OPEN_ORDERS` | GET `/fapi/v1/openOrders` | optional `symbol` |
| `ALGO_ORDER` | GET/POST/DELETE `/fapi/v1/algoOrder` | new: `algoType=CONDITIONAL,symbol,side,type=STOP_MARKET,quantity,triggerPrice,workingType,reduceOnly,clientAlgoId`; query/cancel: `algoId` or `clientAlgoId` |
| `OPEN_ALGO_ORDERS` | GET `/fapi/v1/openAlgoOrders` | optional `symbol` |
| `POSITION_RISK` | GET `/fapi/v3/positionRisk` | optional `symbol` |
| `LEVERAGE` | POST `/fapi/v1/leverage` | `symbol,leverage` |
| `MARGIN_TYPE` | POST `/fapi/v1/marginType` | `symbol,marginType=CROSSED` |
| `USER_TRADES` | GET `/fapi/v1/userTrades` | `symbol,orderId,startTime,endTime,fromId,limit` as appropriate |

Algo query preserves **`algoStatus`** and **`actualOrderId`** separately. A triggered parent's status does not prove its child filled. Stage 2 must query the child via `ORDER`, reconcile its trades, then decide residual action. Empty `actualOrderId` is not proof of zero exposure. Quantity-based protection must not also send `closePosition=true`. G1–G5 must still establish actual demo behavior.

Account paths come from the [official account reference](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/account):

| Constant | GET path | Fields used later |
|---|---|---|
| `ACCOUNT` | `/fapi/v3/account` | wallet, unrealized P&L, available balance |
| `POSITION_MODE` | `/fapi/v1/positionSide/dual` | `dualSidePosition` |
| `MULTI_ASSETS_MODE` | `/fapi/v1/multiAssetsMargin` | `multiAssetsMargin` |
| `SYMBOL_CONFIG` | `/fapi/v1/symbolConfig` | `symbol,marginType,leverage` |
| `INCOME` | `/fapi/v1/income` | `incomeType,tranId,time,asset,income` |

Public paths come from the [official market reference](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data):

| Constant | GET path | Parameters |
|---|---|---|
| `TIME` | `/fapi/v1/time` | none |
| `EXCHANGE_INFO` | `/fapi/v1/exchangeInfo` | none |
| `KLINES` | `/fapi/v1/klines` | `symbol,interval,startTime,endTime,limit` |
| `TICKER_24HR` | `/fapi/v1/ticker/24hr` | optional `symbol` |
| `MARK_PRICE` | `/fapi/v1/premiumIndex` | optional `symbol` |

The price adapter uses v1. The official v2 ticker page warns its weight header is inaccurate; v2 is not exposed by this client.

Key permission inspection belongs to stage 3; no unverified wallet/security endpoint is exposed here.

## Timing and rate gates

The [official general reference](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/general-info) supplies base URLs, HMAC signing, weight headers, 429/418 handling and timing semantics. Project-specific policies below implement the approved plan:

- Demo base `https://demo-fapi.binance.com`; production `https://fapi.binance.com`.
- Connection/read timeouts `(5, 10)` seconds; `recvWindow=5000`. Client owns signing fields. Boolean parameters become lowercase strings.
- `sync_clock()` uses a round-trip midpoint; sample RTT above one second is rejected. A sample is fresh for 60 seconds. Timestamp uses the measured offset. Absolute offset above 1000 ms blocks new mutations; necessary queries and protection remain possible.
- First fetch `EXCHANGE_INFO` to load the one-minute REQUEST_WEIGHT limit. Unknown/malformed limits block other routes. TIME and EXCHANGE_INFO may bootstrap.
- Every response reads `X-MBX-USED-WEIGHT-1M`. The header is the exchange's own count for this IP, so it replaces the reservation of the request it answers (2026-11-15; before, the larger of the two was kept, which made the flat 40-unit private reservation exhaust the soft limit after a few symbols). Reservations of requests still in flight stay. Missing/malformed headers cannot erase reservations. Minute rollover preserves a newly received header.
- Normal requests stop at 75% of the actual limit (including predicted cost). `protective=True` permits necessary recovery queries/protection above that soft gate; the lifecycle owner must not use it for exposure increases.
- Public weights follow the documented route/limit schedule. Private calls conservatively reserve 40 units each. This deliberately overcounts cheap/private writes; actual headers can only raise the local estimate. Tune only after demo evidence and without weakening the soft gate.
- 429 sets shared cooldown from numeric `Retry-After`, or 60 seconds when missing/invalid. 418 also latches `new_orders_paused`; without a deadline it blocks indefinitely pending manual reconciliation. Protection does not bypass an exchange ban/cooldown. No sleeping or retries are hidden inside the client.

## Decimal rules

`SymbolRules.from_exchange_info(symbol_row)` requires complete PRICE_FILTER, LOT_SIZE, MARKET_LOT_SIZE, MIN_NOTIONAL and PERCENT_PRICE filters, status and integer deliveryDate. Missing, duplicate, nonfinite, nonpositive or contradictory bounds fail closed. Symbols whose filters disable bounds with zero are deliberately rejected pending an explicit compatibility decision.

`round_quantity(q, market=True)` floors to a common LOT_SIZE/MARKET_LOT_SIZE step. `round_price(p, up=False)` floors to tick; `up=True` is available when a future protection policy explicitly requires ceiling. Always call validation after rounding; flooring below minimum is not valid.

`validate_order(q, mark_price=..., now_ms=..., price=None, market=True, reduce_only=False, side=None)` enforces applicable quantity and price filters. MARKET notional uses mark price; LIMIT needs `market=False` and a price. With side supplied, PERCENT_PRICE follows BUY upper / SELL lower bounds. Without side it uses the stricter two-sided band. Trigger-price policy remains the protection module's responsibility. Reduce-only skips new-entry eligibility and minimum notional but retains size/price checks. These semantics follow the [official filter definitions](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/common-definition).

New-entry eligibility requires TRADING, PERPETUAL, USDT quote and delivery strictly after the supplied timestamp. No rounding helper silently raises quantity to meet a minimum.

## Evidence and remaining gate

Offline tests cover an independent RFC 4231 signature vector, signing bytes, permission checks, write gates, unknown outcomes/no retry, header budget, rollover, shared cooldown/ban, clock skew, parent/child preservation and Decimal boundaries. They do not establish real endpoint support, protection replacement behavior, simultaneous reduce-only semantics or account permissions. Those remain Task 1.4 / stage 3 gates.

Position zero proof (2026-10-20): `GET /fapi/v2/positionRisk` may return explicit zero rows and is separately allowed as `POSITION_RISK_V2`. `v3/positionRisk` returns only symbols with positions or open orders. A missing row is never zero proof; probe preflight and cleanup require explicit V2 rows for tested symbols. Source: official Trade page, Position Information V2 / V3.
