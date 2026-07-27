#!/usr/bin/env python3
"""Run the optional Playwright-based VOZ crawler."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from voz_crawler_core.playwright_crawler import main


if __name__ == "__main__":
    raise SystemExit(main())
