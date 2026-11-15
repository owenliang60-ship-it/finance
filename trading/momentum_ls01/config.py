"""Fixed parameters from the implementation plan §4. Change only with a plan revision."""
from decimal import Decimal

STRATEGY_ID = 'momentum-ls-01'
RULE_VERSION = '2026-10-19.v2'
ID_PREFIX = 'mls1-'

ENTRY_WINDOW_S = 300            # first wave must start within 12:00:00–12:05:00 SGT
CONVERGE_S = 600                # one deadline for the whole run, from the first request
POLL_S = 2
NOT_ACCEPTED_AFTER_S = (10, 30, 120)   # three "order does not exist" replies at these ages
LEVERAGE = 2
MARGIN_TYPE = 'CROSSED'
STOP_MULTIPLE = Decimal(2)      # short stop = entryPrice x 2, rounded up to tick
MARGIN_BUFFER = Decimal('1.10')
BASIS_TOLERANCE = Decimal('0.0001')    # relative; calibrate with G4 production evidence
PROTECT_RETRY_S = 3600          # after a refused replacement while an old stop still covers the short
GUARD_FAIL_LIMIT = 5
HEARTBEAT_STALE_S = 180
WATCHDOG_REPEAT_S = 1800
BACKUP_KEEP = 30

# Exchange error codes. Source: official USD-M error page (read 2026-10-20); the
# production probe (gate 1b) must confirm the ones the system branches on.
ORDER_NOT_FOUND = -2013
RATE_LIMIT_CODE = -1003
LOCK_WAIT_S = 90                # the minute guard starts in the same second as the rebalance
TRADE_LOOKBACK_S = 6 * 86400    # userTrades accepts at most a 7-day window
WOULD_TRIGGER = -2021
# Signature, key, IP, permission, clock and account-mode failures hit every symbol alike.
COMMON_CAUSE_CODES = frozenset({-1002, -1021, -1022, -2014, -2015, -4061})

# Client order id prefixes the exchange uses for fills it creates itself.
FORCED_PREFIXES = (('autoclose-', 'liquidation'), ('adl_autoclose', 'adl'), ('settlement_autoclose-', 'delist'))
TRADING_INCOME = frozenset({'REALIZED_PNL', 'FUNDING_FEE', 'COMMISSION', 'INSURANCE_CLEAR'})
MINOR_DEVIATIONS = frozenset({'below_min'})
