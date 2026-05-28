"""Shim temporaneo verso _legacy/fast_path.py durante migrazione engine v2."""
import sys
import _legacy.fast_path
sys.modules[__name__] = sys.modules['_legacy.fast_path']
