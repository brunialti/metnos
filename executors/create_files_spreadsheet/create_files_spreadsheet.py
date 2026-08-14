#!/usr/bin/env python3
"""create_files_spreadsheet — dispatcher canonical Google Sheets create (24/5/2026).

Tool UNICO per creare un nuovo spreadsheet Google Sheets. Dispatcher
sottile che instrada al backend giusto in base a `client` (default
`google_workspace`).

Architettura: dispatcher sottile + backend in `runtime/backends/files/`.

§2.3 reverse_pattern='delete_files_by_id': il backend ritorna `_undo`
embed con `ids=[spreadsheet_id]` + scope `client=google_workspace`;
il runtime instrada via `reverse_patterns_patch::delete_files_by_id`.

Contratto:
    stdin: JSON {title, sheet_name?,
                 client?: 'google_workspace' (default)}
    stdout: JSON {ok, n_created, spreadsheet_id, web_view_url, title,
                  results, used, _undo}
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.environ.get("METNOS_RUNTIME") or next(
    str(p / "runtime") for p in Path(__file__).resolve().parents
    if (p / "runtime" / "config.py").is_file()))
from messages import get as _msg  # noqa: E402
from executor_helpers import run_stdio  # noqa: E402
from backends.files import local  # noqa: E402

# §10.3 self-hosted default: `local` (.xlsx/.csv) e' il backend canonico,
# allegabile a un'email. `google_workspace` (Google Sheets online) e' opt-in.
_HANDLERS = {
    "local": local,
}

_COLLISION_RETRY_LIMIT = 100
_TITLE_FILENAME_LIMIT = 120

_SOURCE_COLUMN_ALIASES = {
    "domini": ("dominio", "domain", "domains"),
    "domains": ("dominio", "domain", "domini"),
    "origini": ("origine", "origin", "origins", "source", "sources"),
    "origins": ("origine", "origin", "origini", "source", "sources"),
}


def _materialize_source_column_aliases(args: dict) -> dict:
    """Populate requested plural source headers from canonical entry keys.

    This adapter deliberately lives in the executor, not only in the server
    backend: a remote device pulls executor code by digest while its bundled
    runtime may be older.  Materializing the exact requested key keeps both
    local and remote sinks lossless without mutating caller-owned entries.
    """
    entries = args.get("entries")
    columns = args.get("columns")
    if not isinstance(entries, list) or not isinstance(columns, list):
        return args
    requested = [column for column in columns
                 if isinstance(column, str)
                 and column.strip().casefold() in _SOURCE_COLUMN_ALIASES]
    if not requested:
        return args

    changed = False
    normalized_entries = []
    for entry in entries:
        if not isinstance(entry, dict):
            normalized_entries.append(entry)
            continue
        normalized = dict(entry)
        for column in requested:
            current = normalized.get(column)
            if current not in (None, "", []):
                continue
            aliases = _SOURCE_COLUMN_ALIASES[column.strip().casefold()]
            source = next((normalized.get(alias) for alias in aliases
                           if normalized.get(alias) not in (None, "", [])),
                          None)
            if source is not None:
                normalized[column] = source
                changed = True
        normalized_entries.append(normalized)
    if not changed:
        return args
    return {**args, "entries": normalized_entries}


def _backend(client: str):
    backend = _HANDLERS.get(client)
    if backend is None and client == "google_workspace":
        try:
            from backends.files import google_workspace as backend  # lazy, server-only
        except ImportError:
            return None
        _HANDLERS[client] = backend
    return backend


def _short_turn_id(args: dict) -> str:
    """Return the filesystem-safe turn fragment shown by the chat UI."""
    raw = args.get("_turn_id") or os.environ.get("METNOS_TURN_ID") or ""
    if not isinstance(raw, str):
        raw = str(raw)
    return re.sub(r"[^A-Za-z0-9_-]", "_", raw.strip())[:8].strip("_-")


def _collision_title(args: dict, turn_id: str, ordinal: int) -> str:
    """Build ``<title>_<short-turn>[_N]`` without truncating the suffix."""
    raw_title = args.get("title")
    title = raw_title.strip() if isinstance(raw_title, str) else ""
    title = title or "spreadsheet"
    suffix = f"_{turn_id}" if ordinal == 1 else f"_{turn_id}_{ordinal}"
    room = max(1, _TITLE_FILENAME_LIMIT - len(suffix))
    return f"{title[:room].rstrip() or 'spreadsheet'[:room]}{suffix}"


def _create_local_collision_safe(backend, args: dict) -> dict:
    """Create an implicit-title spreadsheet without failing on name reuse.

    An explicit ``path``/``spreadsheet_id`` remains create-only and fail-closed:
    silently moving an explicitly placed artifact would violate caller intent.
    For the ordinary title-derived destination, retry an ``ERR_DST_EXISTS``
    with the short runtime turn id.  The bounded ordinal also handles a replay
    of the same turn and the create race remains safe because the backend uses
    exclusive file creation.
    """
    result = backend.create_spreadsheet(args)
    if result.get("error_code") != "ERR_DST_EXISTS":
        return result
    if args.get("path") or args.get("spreadsheet_id"):
        return result
    turn_id = _short_turn_id(args)
    if not turn_id:
        return result

    for ordinal in range(1, _COLLISION_RETRY_LIMIT + 1):
        retry_args = {
            **args,
            "title": _collision_title(args, turn_id, ordinal),
        }
        result = backend.create_spreadsheet(retry_args)
        if result.get("error_code") != "ERR_DST_EXISTS":
            return result
    return result


def invoke(args):
    if not isinstance(args, dict):
        return {"ok": False, "error": _msg("ERR_ARGS_NOT_OBJECT"),
                "error_class": "invalid_args",
                "results": [], "used": 0, "n_created": 0}
    client = args.get("client") or "local"
    backend = _backend(client)
    if backend is None:
        return {"ok": False,
                "error": _msg("ERR_NOT_APPLICABLE", what=f"client '{client}'"),
                "error_class": "invalid_args",
                "results": [], "used": 0, "n_created": 0}
    normalized = _materialize_source_column_aliases(args)
    if client == "local":
        return _create_local_collision_safe(backend, normalized)
    return backend.create_spreadsheet(normalized)


def main():
    run_stdio(invoke, error_extra={"error_class": "invalid_args", "results": [], "used": 0, "n_created": 0})


if __name__ == "__main__":
    main()
