"""Shim temporaneo verso _legacy/multi_tool_paths.py durante migrazione engine v2."""
import sys
import _legacy.multi_tool_paths
sys.modules[__name__] = sys.modules['_legacy.multi_tool_paths']
