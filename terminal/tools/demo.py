#!/usr/bin/env python3
"""
Tool Registry Demo — demonstrates the new tool system.

Usage:
    python terminal/tools/demo.py
"""
import sys
sys.path.insert(0, "/Users/owen/CC workspace/Finance")

from terminal.tools import registry, ToolCategory


def main():
    print("=" * 70)
    print("Finance Workspace — Tool Registry Demo")
    print("=" * 70)
    print()

    # Show registry status
    print(f"Registry Status: {registry}")
    print()

    # List all tools by category
    print("📊 Available Tools by Category:")
    print("-" * 70)

    for category in ToolCategory:
        tools = registry.get_available_tools(category=category)
        if tools:
            print(f"\n{category.value.upper()}:")
            for tool in tools:
                meta = tool.metadata
                print(f"  ✓ {meta.name:30} - {meta.description}")

    print()
    print("-" * 70)

    # Show availability status
    print("\n🔍 Detailed Availability Status:")
    print("-" * 70)

    status = registry.check_availability()
    for tool_name, info in status.items():
        status_icon = "✓" if info["available"] else "✗"
        print(
            f"{status_icon} {tool_name:30} "
            f"[{info['provider']:10}] "
            f"{info['category']}"
        )

    print()
    print("-" * 70)

    # Example: Execute a tool (if FMP available)
    available_tools = registry.get_available_tools()
    if available_tools:
        print("\n💡 Example Tool Execution:")
        print("-" * 70)

        # Try to get a quote for AAPL
        try:
            quote_tool = registry.get_tool("get_quote")
            if quote_tool and quote_tool.is_available():
                print("\nFetching AAPL quote...")
                quote = registry.execute("get_quote", symbol="AAPL")
                if quote:
                    print(f"  Symbol: {quote.get('symbol')}")
                    print(f"  Price: ${quote.get('price', 'N/A'):.2f}")
                    print(f"  Change: {quote.get('change', 'N/A')}")
                    print(f"  Volume: {quote.get('volume', 'N/A'):,}")
                else:
                    print("  No quote data returned")
            else:
                print("  Quote tool not available (missing FMP_API_KEY)")
        except Exception as e:
            print(f"  Error: {e}")
    else:
        print("\n⚠️  No tools available (FMP_API_KEY not set)")

    print()
    print("=" * 70)
    print(f"Total tools: {len(registry)} | Available: {len(available_tools)}")
    print("=" * 70)


if __name__ == "__main__":
    main()
