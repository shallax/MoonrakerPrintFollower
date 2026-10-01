#!/usr/bin/env python3
"""Compatibility entry point for the shared native Windows/macOS driver."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from native.dev import main  # noqa: E402

if __name__ == '__main__':
    raise SystemExit(main())
