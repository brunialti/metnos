"""Shim temporaneo verso _legacy/aporia.py durante migrazione engine v2."""
import sys
import _legacy.aporia
sys.modules[__name__] = sys.modules['_legacy.aporia']
