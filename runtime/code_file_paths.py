"""Portable wire-name grammar for executor code bundles.

``[code].files`` has two roles in the current contract format.  Locally it is
an authoring locator, confined independently by ``allowed_code_roots``; a
server-only builtin may therefore contain ``..``.  For a device-capable
executor the same signed value also becomes a bundle key and a relative cache
path.  This module owns the stricter grammar required by that wire role.

Wire names deliberately use an ASCII subset.  Besides keeping Python and Rust
case-insensitive collision checks identical, that avoids filesystem-dependent
Unicode normalization at a signed cross-platform boundary.  A future mapping
from authoring locators to distinct wire names belongs to EXEC-BIND-001; it is
not inferred here.
"""
from __future__ import annotations

from collections.abc import Sequence
_WINDOWS_FORBIDDEN = frozenset('<>"|?*')
_WINDOWS_RESERVED = frozenset({"con", "prn", "aux", "nul"}) | frozenset(
    f"{prefix}{number}"
    for prefix in ("com", "lpt")
    for number in range(1, 10)
)


class PortableCodePathError(ValueError):
    """Stable validation failure at the signed bundle boundary."""

    __slots__ = ("code", "path")

    def __init__(self, code: str, path: str = "") -> None:
        self.code = code
        self.path = path
        super().__init__(f"{code}:{path}" if path else code)

    def __str__(self) -> str:
        return str(self.args[0])


def validate_portable_code_path(path: object) -> str:
    """Return one valid POSIX wire path or raise a stable coded error."""
    if not isinstance(path, str) or not path:
        raise PortableCodePathError("code_path_empty")
    if not path.isascii():
        raise PortableCodePathError("code_path_non_ascii", path)
    if path.startswith("/"):
        raise PortableCodePathError("code_path_absolute", path)
    if "\\" in path or ":" in path:
        raise PortableCodePathError("code_path_separator", path)

    for segment in path.split("/"):
        if not segment or segment in {".", ".."}:
            raise PortableCodePathError("code_path_segment", path)
        if segment.endswith((".", " ")):
            raise PortableCodePathError("code_path_trailing", path)
        if any(
            ord(character) < 32
            or ord(character) == 127
            or character in _WINDOWS_FORBIDDEN
            for character in segment
        ):
            raise PortableCodePathError("code_path_character", path)
        device_name = segment.split(".", 1)[0].lower()
        if device_name in _WINDOWS_RESERVED:
            raise PortableCodePathError("code_path_reserved", path)
    return path


def validate_portable_code_files(files: object) -> tuple[str, ...]:
    """Validate a complete ordered ``[code].files`` wire namespace.

    Collisions are checked over the whole POSIX relative path, not only the
    basename: a case-insensitive target filesystem must materialize every
    signed entry without overwriting another one.
    """
    if (
        not isinstance(files, Sequence)
        or isinstance(files, (str, bytes, bytearray))
        or not files
        or any(not isinstance(path, str) for path in files)
    ):
        raise PortableCodePathError("code_files_shape")
    validated = tuple(validate_portable_code_path(path) for path in files)
    folded: set[str] = set()
    for path in validated:
        key = path.lower()  # ASCII grammar: identical to casefold().
        if key in folded:
            raise PortableCodePathError("code_path_collision", path)
        folded.add(key)
    return validated


def manifest_requires_portable_code_files(manifest: object) -> bool:
    """Whether declared placement permits delivery to a remote device.

    Missing placement retains the Executor Standard's historical ``any``
    default.  Only an explicit, valid ``server`` scope opts out; malformed
    placement is rejected separately and must not weaken this gate.
    """
    if not isinstance(manifest, dict):
        return True
    placement = manifest.get("placement")
    return not (
        isinstance(placement, dict) and placement.get("scope") == "server"
    )
