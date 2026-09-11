#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Unified daily crypto scan.

Runs the remaining scanners serially to avoid API contention:
1. PMARP
2. RVOL
3. BTC NUPL
4. Crypto dual Top10
"""

import importlib
import sys
import time
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

SCANNERS = [
    ("PMARP", "binance_pmarp_scanner"),
    ("RVOL", "binance_rvol_scanner"),
    ("BTC NUPL", "btc_nupl_scanner"),
    ("Crypto 双榜", "binance_beta_scanner"),
]


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def run_scanner(index: int, total: int, label: str, module_name: str) -> bool:
    log(f"\n[{index}/{total}] 运行 {label} 扫描器...")
    try:
        module = importlib.import_module(module_name)
        module.main()
        return True
    except Exception as exc:
        log(f"{label}扫描器错误: {exc}")
        return False


def main() -> int:
    log("=" * 60)
    log("每日统一扫描开始")
    log("=" * 60)

    start_time = time.time()
    failures = []
    total = len(SCANNERS)

    for index, (label, module_name) in enumerate(SCANNERS, start=1):
        ok = run_scanner(index, total, label, module_name)
        if not ok:
            failures.append(label)
        if index < total:
            time.sleep(2)

    elapsed = time.time() - start_time
    log("\n" + "=" * 60)
    if failures:
        log(f"扫描完成但存在失败: {', '.join(failures)}，总耗时 {elapsed/60:.1f} 分钟")
        log("=" * 60)
        return 1

    log(f"全部扫描完成，总耗时 {elapsed/60:.1f} 分钟")
    log("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
