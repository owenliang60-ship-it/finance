# Tool Registry System — Phase 1 Complete

**Date**: 2026-02-08
**Status**: ✓ Complete (all tests passing)
**Files Created**: 5 files, 706 lines total

---

## Overview

The tool registry system provides a unified, provider-agnostic interface to all finance data sources (FMP, FRED, Tradier, etc.). Built following the Dexter pattern: protocol-first, graceful degradation, conditional loading.

## Architecture

```
terminal/tools/
├── __init__.py          # Global registry + auto-loading (60 lines)
├── protocol.py          # FinanceTool ABC + metadata (96 lines)
├── registry.py          # ToolRegistry class (197 lines)
├── fmp_tools.py         # 10 FMP tool wrappers (353 lines)
└── demo.py              # Demo script (118 lines)
```

## Key Components

### 1. Protocol (`protocol.py`)

**FinanceTool** abstract base class:
- `metadata` property → ToolMetadata (name, category, provider, API key info)
- `is_available()` → checks if tool can be used (API key present, etc.)
- `execute(**kwargs)` → executes the tool with given parameters

**ToolCategory** enum:
- MARKET_DATA (price, volume, quotes)
- FUNDAMENTALS (financial statements, ratios)
- COMPANY_INFO (profile, description)
- MACRO (economic indicators)
- OPTIONS (chains, Greeks)
- NEWS (news, insider trading)

**Custom Exceptions**:
- `ToolExecutionError` — tool execution failed
- `ToolUnavailableError` — tool not available (missing API key, etc.)

### 2. Registry (`registry.py`)

**ToolRegistry** class:
- `register(tool)` — add a tool to registry
- `get_tool(name)` — retrieve tool by name
- `get_available_tools(category=None)` — filter by availability + category
- `execute(tool_name, **kwargs)` — execute a tool by name
- `check_availability()` — get status of all tools

**Global singleton**: `get_registry()` → global instance

### 3. FMP Tools (`fmp_tools.py`)

**BaseFMPTool**:
- Checks `FMP_API_KEY` env var
- Wraps `fmp_client` from `src/data/fmp_client.py`
- Handles common error logic

**10 Tool Implementations**:

| Tool Name | Category | Description |
|-----------|----------|-------------|
| get_historical_price | MARKET_DATA | Historical daily OHLCV (5 years) |
| get_quote | MARKET_DATA | Real-time quote |
| get_screener_page | MARKET_DATA | Market screener (paginated) |
| get_profile | COMPANY_INFO | Company profile |
| get_large_cap_stocks | COMPANY_INFO | Large-cap stock list |
| get_ratios | FUNDAMENTALS | Financial ratios |
| get_income_statement | FUNDAMENTALS | Income statement |
| get_balance_sheet | FUNDAMENTALS | Balance sheet |
| get_cash_flow | FUNDAMENTALS | Cash flow statement |
| get_key_metrics | FUNDAMENTALS | Key metrics |

### 4. Initialization (`__init__.py`)

- Auto-loads FMP tools on import
- Creates global `registry` singleton
- Re-exports key classes for convenience

## Usage

### Basic Usage

```python
from terminal.tools import registry

# List available tools
tools = registry.get_available_tools()

# Execute a tool
quote = registry.execute("get_quote", symbol="AAPL")

# Filter by category
from terminal.tools import ToolCategory
market_tools = registry.get_available_tools(category=ToolCategory.MARKET_DATA)
```

### Adding New Tools

```python
from terminal.tools.protocol import FinanceTool, ToolCategory, ToolMetadata

class MyNewTool(FinanceTool):
    @property
    def metadata(self):
        return ToolMetadata(
            name="my_new_tool",
            category=ToolCategory.MACRO,
            description="Does something useful",
            provider="MyProvider",
            requires_api_key=True,
            api_key_env_var="MY_API_KEY",
        )

    def is_available(self):
        return os.getenv("MY_API_KEY") is not None

    def execute(self, **kwargs):
        # Implementation here
        pass

# Register it
from terminal.tools import registry
registry.register(MyNewTool())
```

## Test Coverage

**18 tests** in `tests/test_tool_registry.py`:

- Protocol compliance
- Registry operations (register, get, list, filter)
- FMP tool creation and metadata
- Availability checking (with/without API key)
- Tool execution (success + error cases)
- Global registry initialization

**All tests passing**: 26/26 total (18 new + 8 existing)

## Demo

```bash
python terminal/tools/demo.py
```

Shows:
- Registry status (X/10 available)
- Tools by category
- Detailed availability status
- Example tool execution (if FMP_API_KEY set)

## Graceful Degradation

The system is designed to work even when data sources are unavailable:

1. **Tools check their own availability** via `is_available()`
2. **Registry filters unavailable tools** in `get_available_tools()`
3. **Clear error messages** when attempting to use unavailable tools
4. **No crashes** — system continues to function with available tools

## Next Steps (Phase 2)

**Task #8**: Integrate registry into `pipeline.py`
- Replace direct `fmp_client` calls with `registry.execute()`
- This will enable seamless addition of FRED, Tradier tools later
- Backward compatibility: existing pipeline logic unchanged

## Design Principles

1. **Protocol-first**: Define interface before implementation
2. **Wrap, don't rewrite**: Use existing `fmp_client` methods
3. **Conditional loading**: Tools check API keys, gracefully skip if missing
4. **Provider agnostic**: Easy to add FRED, Tradier, etc. later
5. **Backward compatible**: Existing code continues to work

## File Summary

| File | Lines | Purpose |
|------|-------|---------|
| terminal/tools/protocol.py | 96 | Protocol definition |
| terminal/tools/registry.py | 197 | Registry implementation |
| terminal/tools/fmp_tools.py | 353 | FMP tool wrappers |
| terminal/tools/__init__.py | 60 | Initialization |
| terminal/tools/demo.py | 118 | Demo script |
| tests/test_tool_registry.py | 282 | Test suite |
| **TOTAL** | **1106** | **Phase 1 complete** |

---

**Built by**: tools-architect
**Reviewed**: All tests passing
**Ready for**: Phase 2 (pipeline integration)
