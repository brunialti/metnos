"""Shim temporaneo verso _legacy/praxis.py durante migrazione engine v2."""
import sys
import _legacy.praxis
sys.modules[__name__] = sys.modules['_legacy.praxis']
