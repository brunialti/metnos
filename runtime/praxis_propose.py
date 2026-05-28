"""Shim temporaneo verso _legacy/praxis_propose.py durante migrazione engine v2."""
import sys
import _legacy.praxis_propose
sys.modules[__name__] = sys.modules['_legacy.praxis_propose']
