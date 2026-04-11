#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backtest.pipeline.runner import PipelineRunner


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    if len(argv) != 1:
        print("Usage: python scripts/run_pipeline.py <spec.yaml>")
        return 2

    spec_path = Path(argv[0]).resolve()
    runner = PipelineRunner(spec_path)
    result = runner.run()
    print(f"artifact_dir={result.artifact_dir}")
    print(f"spec_hash={result.spec_hash}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
