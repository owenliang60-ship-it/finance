"""Installed in Quant/scanners; implementation is versioned in sibling Finance."""
from pathlib import Path
import sys


def main():
    scanner_dir = Path(__file__).resolve().parent
    finance_dir = scanner_dir.parent.parent / 'Finance'
    sys.path.insert(0, str(finance_dir))
    from scripts.crypto_daily_rankings import run
    run(scanner_dir, scanner_dir.parent / 'results' / 'daily_rankings')


if __name__ == '__main__':
    main()
