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
import os
import re
from pathlib import Path

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


def server_roots(args: dict, workspace: str) -> dict:
    """A bare drive root given to a tool that runs on this server becomes the
    person's working folder.

    A POSIX server has no drive: «C:\\» there is what the planner writes when
    the person named no place («la mia cartella di lavoro», 10-13/11/2026: it
    answered «path not found: C:\\»). A drive root followed by a path is left
    alone: that is a real place, on some device, and the tool says so.
    """
    def change(value: str) -> str:
        drive = value.strip().rstrip("\\/")
        bare = len(drive) == 2 and bool(_WINDOWS_ROOT.match(drive + os.sep))
        return workspace if bare else value

    return _walk(args, False, change)


def anchor_args(args: dict, profile_json: str | None) -> dict:
    """Replace a named user folder with the device's real one; the rest is unchanged."""
    return anchor_folders(args, _known_folders(profile_json))


def anchor_folders(args: dict, folders: dict[str, str], keep=None) -> dict:
    """Same translation from folders already known; ``keep(name)`` spares a name."""
    from path_alias import _user_dir_aliases

    aliases = _user_dir_aliases() if folders else None
    if not aliases:
        return args

    def change(value: str) -> str:
        if keep is not None and _relative(value) and keep(_first(value)):
            return value
        return _anchored(value, folders, aliases)

    return _walk(args, False, change)


def has_named_folder(args: dict) -> bool:
    """A relative path argument starts with a named user folder («Documenti/...»)."""
    from path_alias import _user_dir_aliases

    aliases = _user_dir_aliases() or {}
    found: list[str] = []

    def note(value: str) -> str:
        if _relative(value) and _first(value).casefold() in aliases:
            found.append(value)
        return value

    _walk(args, False, note)
    return bool(found)


# The operating system's own names for the folders a client reports
# (`client-rs/src/runner.rs::known_folders`) and their XDG user-dirs keys.
_XDG_KEYS = {"Desktop": "XDG_DESKTOP_DIR", "Documents": "XDG_DOCUMENTS_DIR",
             "Downloads": "XDG_DOWNLOAD_DIR", "Music": "XDG_MUSIC_DIR",
             "Pictures": "XDG_PICTURES_DIR", "Public": "XDG_PUBLICSHARE_DIR",
             "Templates": "XDG_TEMPLATES_DIR", "Videos": "XDG_VIDEOS_DIR"}


def local_known_folders(home: Path) -> dict[str, str]:
    """This machine's folders for ``home``, under the names a client reports.

    The XDG user-dirs file records localized folders («Documenti»); without an
    entry the default name counts. Only existing folders other than the home
    itself are reported, as absolute normalized paths.
    """
    declared: dict[str, str] = {}
    try:
        text = (home / ".config" / "user-dirs.dirs").read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        text = ""
    for line in text.splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and len(value) >= 2 and value[0] == value[-1] == '"':
            value = value[1:-1]
            declared[key] = str(home) + value[5:] if value.startswith("$HOME") else value
    folders: dict[str, str] = {}
    for name, key in _XDG_KEYS.items():
        path = os.path.normpath(declared.get(key) or str(home / name))
        if os.path.isabs(path) and path != os.path.normpath(str(home)) and os.path.isdir(path):
            folders[name] = path
    return folders


def _first(value: str) -> str:
    return re.split(r"[\\/]+", value.strip())[0]


def relative_paths(args: dict) -> list[str]:
    """Path arguments still relative, which the client cannot sandbox."""
    found: list[str] = []

    def note(value: str) -> str:
        if _relative(value):
            found.append(value)
        return value

    _walk(args, False, note)
    return found
