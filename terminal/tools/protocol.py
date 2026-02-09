"""
FinanceTool protocol — defines the interface all data tools must implement.

Inspired by Dexter's tool pattern: registry + conditional loading + graceful degradation.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from enum import Enum


class ToolCategory(Enum):
    """Tool categories for organization and discovery."""
    MARKET_DATA = "market_data"      # Price, volume, quotes
    FUNDAMENTALS = "fundamentals"     # Financial statements, ratios
    COMPANY_INFO = "company_info"     # Profile, description, metadata
    MACRO = "macro"                   # Economic indicators, rates
    OPTIONS = "options"               # Options chains, Greeks
    NEWS = "news"                     # News, insider trading, earnings calendar


@dataclass
class ToolMetadata:
    """Metadata about a tool for registry and introspection."""
    name: str                         # Unique tool identifier
    category: ToolCategory            # Tool category
    description: str                  # Human-readable description
    provider: str                     # Data provider (FMP, FRED, Tradier, etc.)
    requires_api_key: bool = True     # Does this tool need API credentials?
    api_key_env_var: Optional[str] = None  # Which env var to check


class FinanceTool(ABC):
    """
    Abstract base class for all finance data tools.

    Design principles:
    1. Graceful degradation: tools check their own availability
    2. Uniform interface: all tools expose metadata + execute()
    3. Provider agnostic: FMP, FRED, Tradier all implement same protocol
    """

    @property
    @abstractmethod
    def metadata(self) -> ToolMetadata:
        """Return tool metadata for registry."""
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """
        Check if this tool can be used (API key present, service accessible).

        Returns:
            True if tool is ready to use, False otherwise
        """
        pass

    @abstractmethod
    def execute(self, **kwargs) -> Any:
        """
        Execute the tool with given parameters.

        Args:
            **kwargs: Tool-specific parameters

        Returns:
            Tool-specific data (dict, list, etc.)

        Raises:
            ValueError: If required parameters missing
            RuntimeError: If tool unavailable or execution fails
        """
        pass

    def __repr__(self) -> str:
        meta = self.metadata
        status = "✓" if self.is_available() else "✗"
        return f"<{meta.name} [{meta.provider}] {status}>"


class ToolExecutionError(Exception):
    """Raised when a tool execution fails."""
    pass


class ToolUnavailableError(Exception):
    """Raised when attempting to use an unavailable tool."""
    pass
