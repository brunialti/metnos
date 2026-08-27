"""Load ``tests/runtime/conftest.py`` by path, under a name of its own.

The bare name ``conftest`` belongs to whichever suite pytest collected first,
so importing it that way makes a test depend on which OTHER tests are running.
With ``tests/portable`` in the same session it resolved to that directory's
conftest: one module failed to import outright, another lost every attribute
it expected.  Naming the file removes the ambiguity for good.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_PATH = Path(__file__).resolve().parents[1] / "conftest.py"
_spec = importlib.util.spec_from_file_location("_metnos_runtime_conftest", _PATH)
module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(module)
