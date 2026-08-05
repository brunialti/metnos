"""Canonical technical registry for broad filesystem content kinds.

Natural-language recognition deliberately lives in ``detection_lexicon``.
This module contains only the stable mapping from a canonical kind to the
filename extensions which implement it. Keeping the two concerns separate
lets a new UI language add surface forms without changing executor code, and
lets every filesystem executor expand the same kind in the same way.
"""
from __future__ import annotations

from collections.abc import Iterable
import re


FILE_KIND_EXTENSIONS: dict[str, tuple[str, ...]] = {
    "image": (
        ".arw", ".avif", ".bmp", ".cr2", ".cr3", ".dng", ".gif",
        ".heic", ".heif", ".ico", ".jpeg", ".jpg", ".jxl", ".nef",
        ".orf", ".pef", ".png", ".raf", ".rw2", ".srw", ".svg",
        ".tif", ".tiff", ".webp",
    ),
    "video": (
        ".3gp", ".avi", ".flv", ".m2ts", ".m4v", ".mkv", ".mov",
        ".mp4", ".mpeg", ".mpg", ".mts", ".webm", ".wmv",
    ),
    "audio": (
        ".aac", ".aiff", ".alac", ".flac", ".m4a", ".mp3", ".ogg",
        ".opus", ".wav", ".wma",
    ),
    "document": (
        ".csv", ".doc", ".docx", ".epub", ".md", ".ods", ".odt",
        ".pdf", ".ppt", ".pptx", ".rtf", ".tsv", ".txt", ".xls",
        ".xlsx",
    ),
    "archive": (
        ".7z", ".bz2", ".gz", ".rar", ".tar", ".tbz2", ".tgz",
        ".txz", ".xz", ".zip",
    ),
}


def extensions_for_kinds(kinds: Iterable[str]) -> tuple[str, ...]:
    """Return a deterministic, deduplicated extension union."""

    extensions = {
        extension
        for kind in kinds
        for extension in FILE_KIND_EXTENSIONS.get(str(kind), ())
    }
    return tuple(sorted(extensions))


def globs_for_kinds(kinds: Iterable[str]) -> list[str]:
    """Translate canonical kinds to case-neutral filename globs."""

    return [f"*{extension}" for extension in extensions_for_kinds(kinds)]


def kinds_for_globs(globs: Iterable[str]) -> set[str]:
    """Infer canonical content kinds from simple extension globs.

    This is the inverse semantic projection used by planning guards.  It does
    not interpret natural language and deliberately ignores broad or complex
    patterns: only forms such as ``*.jpg`` or ``.jpg`` carry an unambiguous
    kind in the closed technical registry.
    """
    extension_to_kind = {
        extension.casefold(): kind
        for kind, extensions in FILE_KIND_EXTENSIONS.items()
        for extension in extensions
    }
    out: set[str] = set()
    for raw in globs:
        if not isinstance(raw, str):
            continue
        value = raw.strip().casefold()
        match = re.fullmatch(r"(?:\*)?(\.[a-z0-9]+)", value)
        if match and match.group(1) in extension_to_kind:
            out.add(extension_to_kind[match.group(1)])
    return out


def canonical_objects_for_kinds(kinds: Iterable[str]) -> set[str]:
    """Project technical file kinds into canonical naming-grammar objects."""
    out: set[str] = set()
    for kind in kinds:
        canonical = str(kind).casefold()
        if canonical not in FILE_KIND_EXTENSIONS:
            continue
        out.add(canonical)
        out.add(canonical + "s")
    return out


__all__ = [
    "FILE_KIND_EXTENSIONS",
    "canonical_objects_for_kinds",
    "extensions_for_kinds",
    "globs_for_kinds",
    "kinds_for_globs",
]
