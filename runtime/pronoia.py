"""Shim temporaneo verso _legacy/pronoia.py durante migrazione engine v2."""
import sys
import _legacy.pronoia
sys.modules[__name__] = sys.modules['_legacy.pronoia']
