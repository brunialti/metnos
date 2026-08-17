"""Offline-only intent IR candidate 0.2.

The package is deliberately disconnected from the Metnos runtime.
"""

from .api import analyze, compile_ir

__all__ = ["analyze", "compile_ir"]
