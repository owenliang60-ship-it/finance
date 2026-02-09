# Tool Registry Quick Reference

## Import

```python
from terminal.tools import registry, ToolCategory
```

## Basic Operations

### List All Tools
```python
all_tools = registry.get_all_tools()
print(f"Total tools: {len(registry)}")
```

### Check Availability
```python
status = registry.check_availability()
for tool_name, info in status.items():
    print(f"{tool_name}: {'✓' if info['available'] else '✗'}")
```

### Filter by Category
```python
market_tools = registry.get_available_tools(category=ToolCategory.MARKET_DATA)
fundamental_tools = registry.get_available_tools(category=ToolCategory.FUNDAMENTALS)
```

### Execute a Tool
```python
# Get real-time quote
quote = registry.execute("get_quote", symbol="AAPL")

# Get historical prices
prices = registry.execute("get_historical_price", symbol="AAPL", years=5)

# Get company profile
profile = registry.execute("get_profile", symbol="AAPL")

# Get financial ratios
ratios = registry.execute("get_ratios", symbol="AAPL", limit=4)
```

## All Available Tools

### Market Data (3 tools)
- `get_quote` — Real-time quote
- `get_historical_price` — Historical daily OHLCV
- `get_screener_page` — Market screener (paginated)

### Company Info (2 tools)
- `get_profile` — Company profile
- `get_large_cap_stocks` — Large-cap stock list

### Fundamentals (5 tools)
- `get_ratios` — Financial ratios
- `get_income_statement` — Income statement
- `get_balance_sheet` — Balance sheet
- `get_cash_flow` — Cash flow statement
- `get_key_metrics` — Key metrics

## Error Handling

```python
from terminal.tools import ToolExecutionError

try:
    result = registry.execute("get_quote", symbol="AAPL")
except ToolExecutionError as e:
    print(f"Execution failed: {e}")
except KeyError as e:
    print(f"Tool not found: {e}")
except RuntimeError as e:
    print(f"Tool unavailable: {e}")
```

## Tool Categories

```python
from terminal.tools import ToolCategory

# Available categories:
ToolCategory.MARKET_DATA      # Price, volume, quotes
ToolCategory.FUNDAMENTALS     # Financial statements, ratios
ToolCategory.COMPANY_INFO     # Profile, description
ToolCategory.MACRO            # Economic indicators (future)
ToolCategory.OPTIONS          # Options chains, Greeks (future)
ToolCategory.NEWS             # News, insider trading (future)
```

## Adding Custom Tools

```python
from terminal.tools import FinanceTool, ToolCategory, ToolMetadata, registry

class MyTool(FinanceTool):
    @property
    def metadata(self):
        return ToolMetadata(
            name="my_tool",
            category=ToolCategory.MARKET_DATA,
            description="My custom tool",
            provider="MyProvider",
        )

    def is_available(self):
        return True  # Or check API key, etc.

    def execute(self, **kwargs):
        return {"result": "data"}

# Register it
registry.register(MyTool())
```

## Demo Script

```bash
python terminal/tools/demo.py
```

Shows registry status, available tools, and example execution.

---

**Full Documentation**: See `docs/tool_registry_phase1.md`
**Integration Guide**: See `terminal/tools/INTEGRATION_GUIDE.md`
