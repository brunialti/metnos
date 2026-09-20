"""Project administrator-installed vision assets into the existing launcher.

The optional system profile contains only the three already supported artifact
paths. It cannot add shell commands, launcher overrides or arbitrary environment
variables. Ordinary user model settings still choose providers and endpoints,
not host code. Existing trusted startup environment values keep precedence.
"""
from __future__ import annotations

import os
from pathlib import PurePosixPath
import stat
import tomllib

_LIMIT = 16 * 1024
_VARIABLES = {
    "model": "METNOS_VLM_MODEL",
    "mmproj": "METNOS_VLM_MMPROJ",
    "llama_bin": "METNOS_VLM_LLAMA_BIN",
}


class StartupProfileError(OSError):
    """The optional administrative profile exists but cannot be trusted."""


def _read_admin_file(path):
    """Read a bounded root-owned regular file through non-writable parents."""
    path = PurePosixPath(path)
    if not path.is_absolute() or ".." in path.parts:
        raise StartupProfileError("invalid vision startup profile path")
    try:
        os.lstat(path)
    except FileNotFoundError:
        return None
    descriptors = []
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        descriptor = os.open("/", flags | os.O_DIRECTORY)
        descriptors.append(descriptor)
        for index, component in enumerate(path.parts):
            if index:
                descriptor = os.open(
                    component, flags | (os.O_DIRECTORY if index < len(path.parts) - 1 else 0),
                    dir_fd=descriptor,
                )
                descriptors.append(descriptor)
            info = os.fstat(descriptor)
            is_file = index == len(path.parts) - 1
            valid_type = stat.S_ISREG(info.st_mode) if is_file else stat.S_ISDIR(info.st_mode)
            if not valid_type or info.st_uid != 0 or info.st_mode & 0o022:
                raise StartupProfileError("untrusted vision startup profile ownership")
        if info.st_size > _LIMIT:
            raise StartupProfileError("vision startup profile is too large")
        content = os.read(descriptor, _LIMIT + 1)
        if len(content) > _LIMIT:
            raise StartupProfileError("vision startup profile is too large")
        return content
    except FileNotFoundError:
        return None
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _profile_environment(content, role):
    try:
        profiles = tomllib.loads(content.decode("utf-8"))
        if not profiles or len(profiles) > 32:
            raise ValueError("invalid profile roles")
        for profile in profiles.values():
            if not isinstance(profile, dict) or set(profile) != set(_VARIABLES):
                raise ValueError("invalid profile fields")
            for value in profile.values():
                if (not isinstance(value, str) or not value.startswith("/")
                        or ".." in PurePosixPath(value).parts
                        or any(ord(char) < 32 or ord(char) == 127 for char in value)):
                    raise ValueError("invalid artifact path")
        selected = profiles.get(role, {})
        return {_VARIABLES[key]: value for key, value in selected.items()}
    except (UnicodeError, ValueError, TypeError) as exc:
        raise StartupProfileError("invalid vision startup profile") from exc


def vlm_startup_environment(role="default"):
    """Return a child-only environment; never mutate the HTTP/worker process."""
    environment = dict(os.environ)
    if all(environment.get(name) for name in _VARIABLES.values()):
        return environment
    from config import PATH_VLM_STARTUP_PROFILE
    content = _read_admin_file(PATH_VLM_STARTUP_PROFILE)
    if content is not None:
        for name, value in _profile_environment(content, role).items():
            if not environment.get(name):
                environment[name] = value
    return environment
