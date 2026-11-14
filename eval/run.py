#!/usr/bin/env python
"""CLI for the offline evaluation harness (NAU-15, spec 11).

The logic lives in `bench/run.py` so the server tests can import it as
`bench.run`; this file is the human entry point.

Pure-cache evaluation (no network, matrix must be complete for full numbers):

    uv run --project server python eval/run.py

Call plan only (prints how many calls a matrix run would cost; no calls):

    uv run --project server python eval/run.py --dry-run

NETWORK modes, reserved for an explicit human decision (they spend quota):

    uv run --project server python eval/run.py --generate   # matrix, resumable
    uv run --project server python eval/run.py --judge      # score cached answers
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bench.run import main

if __name__ == "__main__":
    raise SystemExit(main())
