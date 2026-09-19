# Partial cloud test overlays can import production modules

An isolated `/tmp` overlay placed modified scripts before Finance on PYTHONPATH, but imports from the baseline repository reordered sys.path. Python's dynamic namespace package lookup subsequently imported production `crypto_daily_rankings.py` and `daily_scan_all.py`, giving 11 false test failures despite the overlay containing new versions. Traceback paths identified the error.

The test-only remedy was to create `scripts/__init__.py` and `scripts/quant/__init__.py` within that temporary overlay using `pkgutil.extend_path`, freezing overlay-first package lookup before imports could reorder sys.path. The actual cloud Python 3.10 suite then passed all 123 relevant tests. No production files were changed. Future overlay verification must assert source paths, or use a complete isolated checkout.
