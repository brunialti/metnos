# SPDX-License-Identifier: MIT
"""Named user folders of a device, resolved before an invocation is signed.

The planner writes what the person said («Documenti», «Immagini/2024»). A
sandboxed client cannot anchor a relative path before starting the executor,
so it refuses it. The client reports where its own folders are, OneDrive
redirection included, and the words come from the reviewed path aliases:
neither side keeps a list of the other's names.
"""
from __future__ import annotations

import json
import re

_WINDOWS_ROOT = re.compile(r"^[A-Za-z]:[\\/]")
_PATH_KEYS = frozenset({"path", "paths", "base_path", "root", "dir", "dirs",
                        "directory", "directories", "src", "dst"})
_PATH_SUFFIXES = ("_path", "_paths", "_dir", "_directory")


def _path_key(key: str) -> bool:
    """Same reading of an argument name as the client's sandbox gate."""
    key = key.casefold().replace("-", "_")
    return key in _PATH_KEYS or key.endswith(_PATH_SUFFIXES)


def _relative(value: str) -> bool:
    text = value.split("{", 1)[0].strip()
    return bool(text) and "://" not in text and not (
        text.startswith(("/", "\\", "~")) or _WINDOWS_ROOT.match(text))


def _known_folders(profile_json: str | None) -> dict[str, str]:
    try:
        profile = json.loads(profile_json or "{}")
    except (TypeError, ValueError):
        return {}
    folders = profile.get("known_folders") if isinstance(profile, dict) else None
    if not isinstance(folders, dict):
        return {}
    return {name: path for name, path in folders.items()
            if isinstance(name, str) and isinstance(path, str) and path}


def _anchored(value: str, folders: dict[str, str], aliases: dict[str, list[str]]) -> str:
    if not _relative(value):
        return value
    first, *rest = re.split(r"[\\/]+", value.strip())
    folder = next((folders[name] for name in aliases.get(first.casefold(), ())
                   if name in folders), None)
    if folder is None:
        return value
    separator = "\\" if _WINDOWS_ROOT.match(folder) else "/"
    return separator.join([folder.rstrip("\\/"), *(part for part in rest if part)])


def _walk(value, is_path: bool, change):
    if isinstance(value, str):
        return change(value) if is_path else value
    if isinstance(value, list):
        return [_walk(item, is_path, change) for item in value]
    if isinstance(value, dict):
        # On a nested object the child's own key decides: `content` next to
        # `path` is not a path because it lives in a collection of files.
        return {key: _walk(item, _path_key(str(key)), change) for key, item in value.items()}
    return value


def anchor_args(args: dict, profile_json: str | None) -> dict:
    """Replace a named user folder with the device's real one; the rest is unchanged."""
    from path_alias import _user_dir_aliases

    folders = _known_folders(profile_json)
    aliases = _user_dir_aliases() if folders else None
    if not aliases:
        return args
    return _walk(args, False, lambda value: _anchored(value, folders, aliases))


def relative_paths(args: dict) -> list[str]:
    """Path arguments still relative, which the client cannot sandbox."""
    found: list[str] = []

    def note(value: str) -> str:
        if _relative(value):
            found.append(value)
        return value

    _walk(args, False, note)
    return found
