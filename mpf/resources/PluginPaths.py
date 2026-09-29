"""Locate packaged files independently of the caller's directory.

Cura installs this package under its plugin ID, not necessarily as ``mpf``.
The root comes from this package's own location; callers supply literal paths
relative to it. Resource contract tests validate every declared file against
both the source tree and the built package.
"""
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def plugin_path(*parts: str) -> str:
    """Return a local filename beneath the installed plugin root."""
    return str(PLUGIN_ROOT.joinpath(*parts))
