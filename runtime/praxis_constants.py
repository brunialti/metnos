"""Shim temporaneo verso _legacy/praxis_constants.py durante migrazione engine v2."""
import sys
import _legacy.praxis_constants
sys.modules[__name__] = sys.modules['_legacy.praxis_constants']
