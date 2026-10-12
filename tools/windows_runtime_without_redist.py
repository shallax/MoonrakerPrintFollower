"""Release staging entry point for the packaged Windows DLL-free transformation."""

from mpf.toolhead.WindowsRuntimeWithoutRedist import (
    strip_cad_wheel,
    strip_python_helper,
)

__all__ = ("strip_cad_wheel", "strip_python_helper")
