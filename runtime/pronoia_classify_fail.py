"""Shim temporaneo verso _legacy/pronoia_classify_fail.py durante migrazione engine v2."""
import sys
import _legacy.pronoia_classify_fail
sys.modules[__name__] = sys.modules['_legacy.pronoia_classify_fail']
