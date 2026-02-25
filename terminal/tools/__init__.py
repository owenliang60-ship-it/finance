"""
Terminal tools — data tool registry for Finance workspace.

Provides a unified interface to all data sources (FMP, FRED, Tradier, etc.)
via the ToolRegistry pattern.

Usage:
    from terminal.tools import registry

    # List available tools
    tools = registry.get_available_tools()

    # Execute a tool
    quote = registry.execute("get_quote", symbol="AAPL")

    # Check availability
    status = registry.check_availability()
"""
import logging
from terminal.tools.registry import get_registry
from terminal.tools.fmp_tools import create_fmp_tools

logger = logging.getLogger(__name__)

# Initialize global registry
registry = get_registry()

# Auto-register tools
_fmp_tools_loaded = False
_fred_tools_loaded = False
_marketdata_tools_loaded = False


def _load_fmp_tools():
    """Load FMP tools into registry (conditional on API key)."""
    global _fmp_tools_loaded
    if _fmp_tools_loaded:
        return

    try:
        tools = create_fmp_tools()
        for tool in tools:
            registry.register(tool)

        available_count = sum(1 for t in tools if t.is_available())
        total_count = len(tools)

        if available_count > 0:
            logger.info(
                f"Loaded {available_count}/{total_count} FMP tools "
                f"(provider: FMP)"
            )
        else:
            logger.warning(
                f"FMP tools registered but unavailable "
                f"(missing FMP_API_KEY or import failed)"
            )

        _fmp_tools_loaded = True

    except Exception as e:
        logger.error(f"Failed to load FMP tools: {e}")


def _load_fred_tools():
    """Load FRED tools into registry (conditional on API key)."""
    global _fred_tools_loaded
    if _fred_tools_loaded:
        return

    try:
        from terminal.tools.fred_tools import create_fred_tools

        tools = create_fred_tools()
        for tool in tools:
            registry.register(tool)

        available_count = sum(1 for t in tools if t.is_available())
        total_count = len(tools)

        if available_count > 0:
            logger.info(
                f"Loaded {available_count}/{total_count} FRED tools "
                f"(provider: FRED - free macro data)"
            )
        else:
            logger.info(
                f"FRED tools registered but unavailable "
                f"(missing FRED_API_KEY - get free key at https://fred.stlouisfed.org/)"
            )

        _fred_tools_loaded = True

    except Exception as e:
        logger.error(f"Failed to load FRED tools: {e}")


def _load_marketdata_tools():
    """Load MarketData.app tools into registry (conditional on API key)."""
    global _marketdata_tools_loaded
    if _marketdata_tools_loaded:
        return

    try:
        from terminal.tools.marketdata_tools import create_marketdata_tools

        tools = create_marketdata_tools()
        for tool in tools:
            registry.register(tool)

        available_count = sum(1 for t in tools if t.is_available())
        total_count = len(tools)

        if available_count > 0:
            logger.info(
                f"Loaded {available_count}/{total_count} MarketData tools "
                f"(provider: MarketData.app - options data)"
            )
        else:
            logger.info(
                f"MarketData tools registered but unavailable "
                f"(missing MARKETDATA_API_KEY)"
            )

        _marketdata_tools_loaded = True

    except Exception as e:
        logger.error(f"Failed to load MarketData tools: {e}")


# Auto-load on import
_load_fmp_tools()
_load_fred_tools()
_load_marketdata_tools()


# Re-export key components for convenience
from terminal.tools.protocol import (
    FinanceTool,
    ToolCategory,
    ToolMetadata,
    ToolExecutionError,
    ToolUnavailableError,
)
from terminal.tools.registry import ToolRegistry

__all__ = [
    "registry",
    "FinanceTool",
    "ToolCategory",
    "ToolMetadata",
    "ToolRegistry",
    "ToolExecutionError",
    "ToolUnavailableError",
]
