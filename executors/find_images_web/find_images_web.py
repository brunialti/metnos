"""Executor ``find_images_web`` per discovery testuale e ricerca inversa."""
from __future__ import annotations

import sys
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent.parent / "runtime"
if str(_RUNTIME) not in sys.path:
    sys.path.insert(0, str(_RUNTIME))

from executor_helpers import run_stdio  # noqa: E402
from backends.images.google_vision import find_images_web as _reverse  # noqa: E402
from backends.images.searxng import find_images_by_text as _text_search  # noqa: E402


def invoke(args: dict) -> dict:
    args = args or {}
    queries = args.get("queries")
    has_queries = bool(queries.strip()) if isinstance(queries, str) else bool(queries)
    has_sources = bool(args.get("paths") or args.get("urls") or args.get("from_step"))
    if has_queries and has_sources:
        from messages import get as _msg
        return {
            "ok": False,
            "error_code": "ERR_ARG_INVALID",
            "error": _msg("ERR_ARG_INVALID", arg="queries/paths/urls",
                          reason="mutually exclusive search modes"),
            "error_class": "invalid_args",
            "entries": [],
        }
    if has_queries:
        return _text_search(args)
    return _reverse(args)


def main():
    run_stdio(invoke)


if __name__ == "__main__":
    main()
