"""Canonical technical registry for broad filesystem content kinds.

Natural-language recognition deliberately lives in ``detection_lexicon``.
This module contains only the stable mapping from a canonical kind to the
filename extensions which implement it. Keeping the two concerns separate
lets a new UI language add surface forms without changing executor code, and
lets every filesystem executor expand the same kind in the same way.
"""
from __future__ import annotations

from collections.abc import Iterable


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


__all__ = ["FILE_KIND_EXTENSIONS", "extensions_for_kinds", "globs_for_kinds"]
