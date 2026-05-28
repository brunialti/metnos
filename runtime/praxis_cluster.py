"""Shim temporaneo verso _legacy/praxis_cluster.py durante migrazione engine v2."""
import sys
import _legacy.praxis_cluster
sys.modules[__name__] = sys.modules['_legacy.praxis_cluster']
