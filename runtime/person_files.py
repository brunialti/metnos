# SPDX-License-Identifier: MIT
"""The working folder of each person on this server.

Roberto, 13/11/2026: one folder holding only the person's own files. It is
what «la mia cartella di lavoro» means and where a path without a folder
goes. Until then both meant the service's data directory, where the person's
files were mixed with the service's own state (a convention of 22/5/2026,
born when the service ran as the person).

Nothing is moved to build it. The folders the person already reached by name
through a link in the old place (the photo archive on the NAS) get the same
link here; the old link stays where it is, because the image index is named
after it (`index_schema.canonical_corpus_path`) and the photo links of past
conversations resolve through it.
"""
from __future__ import annotations

import os
from pathlib import Path

import config as C

# Set by the service for the process of a tool: the folder of the person the
# tool is working for (`path_alias.workspace_default` reads it).
ENV = "METNOS_PERSON_FILES"
_DIRECTORY = "persons"
_UNKNOWN = "_unknown"


def _name(owner: object) -> str:
    """A folder name for an account identifier: nothing that could leave the
    parent folder."""
    text = "".join(char for char in str(owner or "") if char.isalnum() or char in "-_")
    return text[:64] or _UNKNOWN


def root(owner: object) -> Path:
    """The working folder of the account ``owner``."""
    return Path(C.PATH_WORKSPACE) / _DIRECTORY / _name(owner)


def owner_of(actor: object) -> str:
    """The account a turn's actor works for; an unknown actor has a folder of
    its own and never the host's."""
    import devices

    owner = devices.owner_id_for_actor(str(actor or "") or "host")
    return _UNKNOWN if owner == devices.NO_OWNER else str(owner)


def for_actor(actor: object) -> Path:
    return root(owner_of(actor))


def for_args(args: object) -> Path:
    """The folder of the person a tool's arguments were authenticated for
    (`_actor` is written by the runtime, never by a plan)."""
    return for_actor(args.get("_actor") if isinstance(args, dict) else None)


def _legacy() -> Path:
    """Where relative paths went before: the service's data directory."""
    return Path(C.PATH_USER_DATA)


def ensure(folder: Path, *, host: bool = False) -> Path:
    """Create the folder on first use. For the host account, the links that
    stood in the old place are repeated here, pointing to the same targets:
    they are the mounts that person already reached by name."""
    folder = Path(folder)
    if not folder.is_dir():
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    if host:
        try:
            names = sorted(os.listdir(_legacy()))
        except OSError:
            names = []
        for name in names:
            old = _legacy() / name
            new = folder / name
            try:
                if old.is_symlink() and not os.path.lexists(new):
                    os.symlink(os.readlink(old), new)
            except OSError:
                continue          # a link that cannot be made is a folder not reached by name
    return folder


def ensure_for_actor(actor: object) -> Path:
    import devices

    owner = owner_of(actor)
    try:
        host = owner == devices.host_user_id()
    except Exception:  # noqa: BLE001 - without a registry nobody is the host
        host = False
    return ensure(root(owner), host=host)


def absolute(value: str, folder: Path) -> str:
    """A path without a root, as the person means it: inside their folder. A
    name that is a link there (a mounted archive) becomes the real place, so
    that permissions are checked on what is actually read."""
    text = str(value).strip()
    target = Path(folder) / text
    try:
        first = Path(folder) / Path(text).parts[0] if Path(text).parts else None
        if first is not None and first.is_symlink():
            return str(first.resolve() / Path(*Path(text).parts[1:]))
    except (OSError, ValueError, IndexError):
        pass
    return os.path.normpath(str(target))
