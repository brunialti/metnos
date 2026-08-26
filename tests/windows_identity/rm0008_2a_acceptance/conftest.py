"""Make the Windows cells able to reach their own helper module.

The certification runs under ``--import-mode=importlib``, which deliberately
leaves ``sys.path`` untouched: a test module is imported by location and its
directory is never added.  The helper that owns the real identities and the
independent ACL oracle lives beside these cells, so without this the whole
Windows side cannot even be collected.  A conftest is imported by location as
well, which is why it can state where its own package is.
"""
import sys
from pathlib import Path

_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
