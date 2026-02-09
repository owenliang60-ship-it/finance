# Tool Registry Integration Guide

This guide shows how to migrate from direct `fmp_client` calls to the tool registry pattern.

## Current Pattern (Before)

```python
# In pipeline.py or any other module
from src.data.fmp_client import fmp_client

# Direct calls to fmp_client
quote = fmp_client.get_quote("AAPL")
profile = fmp_client.get_profile("AAPL")
prices = fmp_client.get_historical_price("AAPL", years=5)
```

## New Pattern (After)

```python
# In pipeline.py or any other module
from terminal.tools import registry

# Execute via registry
quote = registry.execute("get_quote", symbol="AAPL")
profile = registry.execute("get_profile", symbol="AAPL")
prices = registry.execute("get_historical_price", symbol="AAPL", years=5)
```

## Migration Steps for Task #8

### Step 1: Import Changes

Replace:
```python
from src.data.fmp_client import fmp_client
```

With:
```python
from terminal.tools import registry
```

### Step 2: Method Call Mapping

| Old `fmp_client` Method | New `registry.execute()` Call |
|------------------------|-------------------------------|
| `fmp_client.get_quote(symbol)` | `registry.execute("get_quote", symbol=symbol)` |
| `fmp_client.get_profile(symbol)` | `registry.execute("get_profile", symbol=symbol)` |
| `fmp_client.get_historical_price(symbol, years)` | `registry.execute("get_historical_price", symbol=symbol, years=years)` |
| `fmp_client.get_ratios(symbol, limit)` | `registry.execute("get_ratios", symbol=symbol, limit=limit)` |
| `fmp_client.get_income_statement(symbol, period, limit)` | `registry.execute("get_income_statement", symbol=symbol, period=period, limit=limit)` |
| `fmp_client.get_balance_sheet(symbol, period, limit)` | `registry.execute("get_balance_sheet", symbol=symbol, period=period, limit=limit)` |
| `fmp_client.get_cash_flow(symbol, period, limit)` | `registry.execute("get_cash_flow", symbol=symbol, period=period, limit=limit)` |
| `fmp_client.get_key_metrics(symbol, limit)` | `registry.execute("get_key_metrics", symbol=symbol, limit=limit)` |
| `fmp_client.get_large_cap_stocks(threshold)` | `registry.execute("get_large_cap_stocks", market_cap_threshold=threshold)` |
| `fmp_client.get_screener_page(offset, limit, volume)` | `registry.execute("get_screener_page", offset=offset, limit=limit, volume_more_than=volume)` |

### Step 3: Error Handling (Optional Enhancement)

Old pattern (no explicit error handling):
```python
quote = fmp_client.get_quote("AAPL")
if not quote:
    # Handle None return
    pass
```

New pattern (with registry error handling):
```python
from terminal.tools import ToolExecutionError

try:
    quote = registry.execute("get_quote", symbol="AAPL")
except ToolExecutionError as e:
    # Handle execution error (API failure, timeout, etc.)
    logger.error(f"Failed to get quote: {e}")
    quote = None
except KeyError as e:
    # Handle tool not found (shouldn't happen with known tools)
    logger.error(f"Tool not found: {e}")
    quote = None
```

## Example: pipeline.py Migration

### Before (Lines ~150-200)

```python
def collect_data_package(symbol: str) -> DataPackage:
    """Collect all data for a symbol."""
    pkg = DataPackage(symbol=symbol)

    # Get quote
    quote = fmp_client.get_quote(symbol)
    if quote:
        pkg.price = {
            "latest_close": quote.get("price"),
            "change_percent": quote.get("changesPercentage"),
        }

    # Get profile
    profile = fmp_client.get_profile(symbol)
    if profile:
        pkg.profile = profile

    # Get fundamentals
    pkg.ratios = fmp_client.get_ratios(symbol, limit=4)
    pkg.income = fmp_client.get_income_statement(symbol, period="quarter", limit=8)

    return pkg
```

### After (Lines ~150-200)

```python
from terminal.tools import registry

def collect_data_package(symbol: str) -> DataPackage:
    """Collect all data for a symbol."""
    pkg = DataPackage(symbol=symbol)

    # Get quote
    quote = registry.execute("get_quote", symbol=symbol)
    if quote:
        pkg.price = {
            "latest_close": quote.get("price"),
            "change_percent": quote.get("changesPercentage"),
        }

    # Get profile
    profile = registry.execute("get_profile", symbol=symbol)
    if profile:
        pkg.profile = profile

    # Get fundamentals
    pkg.ratios = registry.execute("get_ratios", symbol=symbol, limit=4)
    pkg.income = registry.execute("get_income_statement", symbol=symbol, period="quarter", limit=8)

    return pkg
```

## Benefits of Migration

1. **Provider Independence**: Later can add FRED, Tradier tools without changing pipeline code
2. **Unified Interface**: All data sources use same `registry.execute()` pattern
3. **Availability Checking**: Can check if tools are available before attempting execution
4. **Better Error Messages**: Registry provides clearer error messages when tools fail
5. **Testing**: Easier to mock the registry in tests vs. mocking fmp_client

## Backward Compatibility Note

The migration is **non-breaking**:
- `fmp_client` still exists and works
- Existing code continues to function
- Can migrate incrementally (one module at a time)
- All existing tests still pass

## Future Extensions (After Phase 1)

Once pipeline.py is migrated, we can easily add:

### FRED Tools (Macro Data)
```python
# In terminal/tools/fred_tools.py
class GetGDPTool(FinanceTool):
    # ... implementation ...

# Usage (after registration)
gdp = registry.execute("get_gdp", series_id="GDP")
```

### Tradier Tools (Options Data)
```python
# In terminal/tools/tradier_tools.py
class GetOptionChainTool(FinanceTool):
    # ... implementation ...

# Usage (after registration)
chain = registry.execute("get_option_chain", symbol="AAPL")
```

## Testing After Migration

Run the existing test suite to verify backward compatibility:
```bash
pytest tests/ -v
```

All tests should still pass (currently 26/26 passing).

---

**Questions?** Contact tools-architect or see `docs/tool_registry_phase1.md` for full documentation.
