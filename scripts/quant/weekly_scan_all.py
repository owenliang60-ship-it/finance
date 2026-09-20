#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Installed in Quant/scanners; implementation is versioned in sibling Finance."""
from pathlib import Path
import sys

DEFAULT_RESULT_DIR = 'weekly_report'


def main() -> int:
    scanner_dir = Path(__file__).resolve().parent
    finance_dir = scanner_dir.parent.parent / 'Finance'
    sys.path.insert(0, str(finance_dir))
    from scripts.crypto_weekly_report import run
    run(scanner_dir, scanner_dir.parent / 'results' / DEFAULT_RESULT_DIR)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
