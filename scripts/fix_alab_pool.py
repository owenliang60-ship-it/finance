#!/usr/bin/env python3
"""One-time fix: set ALAB in_pool=1 and backfill fundamentals."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from terminal.company_store import get_store
from src.data.pool_manager import ensure_in_pool


def main():
    symbol = "ALAB"

    # 1. Ensure in pool (universe.json + company.db)
    info = ensure_in_pool(symbol)
    if not info:
        print(f"FAILED: could not add {symbol} to pool")
        return

    # 2. Verify pool membership via universe.json
    from src.data.pool_manager import get_symbols
    in_pool = symbol in get_symbols()
    print(f"{symbol} in pool (universe.json) = {in_pool}")

    # 3. Backfill fundamentals
    from src.data.fundamental_fetcher import update_all_fundamentals
    print(f"Backfilling fundamentals for {symbol}...")
    update_all_fundamentals([symbol])
    print("Done.")


if __name__ == "__main__":
    main()
