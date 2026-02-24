"""
ToolRegistry — central registry for all finance data tools.

Provides:
- Tool registration and discovery
- Category-based filtering
- Availability checking
- Graceful degradation (skip unavailable tools)
"""
import logging
from typing import Any, Dict, List, Optional
from terminal.tools.protocol import FinanceTool, ToolCategory, ToolMetadata

logger = logging.getLogger(__name__)


class ToolRegistry:
    """
    Central registry for finance data tools.

    Usage:
        registry = ToolRegistry()
        registry.register(MyTool())

        # Get available tools
        tools = registry.get_available_tools(category=ToolCategory.MARKET_DATA)

        # Execute a tool
        result = registry.execute("get_historical_price", symbol="AAPL")
    """

    def __init__(self):
        self._tools: Dict[str, FinanceTool] = {}
        self._loaded = False

    def register(self, tool: FinanceTool) -> None:
        """
        Register a tool in the registry.

        Args:
            tool: FinanceTool instance to register

        Raises:
            ValueError: If tool with same name already registered
        """
        name = tool.metadata.name
        if name in self._tools:
            raise ValueError(f"Tool '{name}' already registered")

        self._tools[name] = tool
        status = "available" if tool.is_available() else "unavailable"
        logger.debug(f"Registered tool: {name} ({status})")

    def get_tool(self, name: str) -> Optional[FinanceTool]:
        """
        Get a tool by name.

        Args:
            name: Tool name

        Returns:
            FinanceTool instance or None if not found
        """
        return self._tools.get(name)

    def get_all_tools(self) -> List[FinanceTool]:
        """Return all registered tools."""
        return list(self._tools.values())

    def get_available_tools(
        self, category: Optional[ToolCategory] = None
    ) -> List[FinanceTool]:
        """
        Get all available tools, optionally filtered by category.

        Args:
            category: Optional category filter

        Returns:
            List of available FinanceTool instances
        """
        tools = self._tools.values()

        if category:
            tools = [t for t in tools if t.metadata.category == category]

        return [t for t in tools if t.is_available()]

    def list_tools(self, category: Optional[ToolCategory] = None) -> List[ToolMetadata]:
        """
        List metadata for all tools (available or not).

        Args:
            category: Optional category filter

        Returns:
            List of ToolMetadata
        """
        tools = self._tools.values()

        if category:
            tools = [t for t in tools if t.metadata.category == category]

        return [t.metadata for t in tools]

    def execute(self, tool_name: str, **kwargs) -> Any:
        """
        Execute a tool by name.

        Args:
            tool_name: Name of the tool to execute
            **kwargs: Tool-specific parameters

        Returns:
            Tool execution result

        Raises:
            KeyError: If tool not found
            RuntimeError: If tool unavailable or execution fails
        """
        tool = self._tools.get(tool_name)
        if not tool:
            available = ", ".join(self._tools.keys())
            raise KeyError(
                f"Tool '{tool_name}' not found. Available: {available}"
            )

        if not tool.is_available():
            raise RuntimeError(
                f"Tool '{tool_name}' is not available "
                f"(provider: {tool.metadata.provider})"
            )

        try:
            return tool.execute(**kwargs)
        except Exception as e:
            logger.error(f"Tool '{tool_name}' execution failed: {e}")
            raise RuntimeError(f"Tool execution failed: {e}") from e

    def check_availability(self) -> Dict[str, Dict[str, Any]]:
        """
        Check availability status of all registered tools.

        Returns:
            Dict mapping tool name to status info
        """
        status = {}
        for name, tool in self._tools.items():
            meta = tool.metadata
            status[name] = {
                "available": tool.is_available(),
                "category": meta.category.value,
                "provider": meta.provider,
                "requires_api_key": meta.requires_api_key,
            }
        return status

    def __len__(self) -> int:
        """Return number of registered tools."""
        return len(self._tools)

    def __repr__(self) -> str:
        total = len(self._tools)
        available = sum(1 for t in self._tools.values() if t.is_available())
        return f"<ToolRegistry: {available}/{total} available>"


# Global singleton registry
_global_registry: Optional[ToolRegistry] = None


def get_registry() -> ToolRegistry:
    """
    Get the global tool registry (singleton).

    Returns:
        Global ToolRegistry instance
    """
    global _global_registry
    if _global_registry is None:
        _global_registry = ToolRegistry()
    return _global_registry


def reset_registry() -> None:
    """Reset the global registry (useful for testing)."""
    global _global_registry
    _global_registry = None
