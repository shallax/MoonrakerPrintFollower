"""Fail a tag release if any required reusable-CI job was skipped or failed."""
from __future__ import annotations

import os


REQUIRED = (
    ("package", "MPF_RELEASE_PACKAGE_RESULT"),
    ("artifact scan", "MPF_RELEASE_SCAN_RESULT"),
    ("Cura gate", "MPF_RELEASE_GATE_RESULT"),
)


def main() -> int:
    results = [(name, os.environ.get(variable, "")) for name, variable in REQUIRED]
    incomplete = [(name, result or "missing") for name, result in results
                  if result != "success"]
    if incomplete:
        details = ", ".join(f"{name}={result}" for name, result in incomplete)
        print(f"::error::Release checks incomplete: {details}")
        return 1
    print("Release package, artifact scan and Cura gate all succeeded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
