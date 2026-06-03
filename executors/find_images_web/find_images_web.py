"""Executor `find_images_web` — reverse image search via Google Cloud
Vision Web Detection. Thin wrapper sul backend.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent.parent / "runtime"
if str(_RUNTIME) not in sys.path:
    sys.path.insert(0, str(_RUNTIME))

from messages import get as _msg  # noqa: E402
from backends.images.google_vision import find_images_web as _backend  # noqa: E402


def invoke(args: dict) -> dict:
    return _backend(args)


def main():
    try:
        args = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        sys.stdout.write(json.dumps({"ok": False,
                                      "error": _msg("ERR_JSON_INVALID")}))
        return
    sys.stdout.write(json.dumps(invoke(args), ensure_ascii=False))


if __name__ == "__main__":
    main()
