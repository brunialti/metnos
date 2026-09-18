"""Read-only preflight for legacy, in-place image-index maintenance tools."""
from __future__ import annotations

import json
from pathlib import Path


def legacy_image_paths(directory: Path) -> list[str]:
    """Reject durable/negative generations before models, caches or writes.

    Old maintenance tools cannot update atomic generations or negative
    records. Use create_images_indices to produce a new generation instead.
    """
    metadata = json.loads((directory / "meta.json").read_text(encoding="utf-8"))
    if not isinstance(metadata, dict):
        raise ValueError("image_backfill_metadata_invalid")
    negative_count = metadata.get("n_not_indexed", 0)
    if type(negative_count) is not int or negative_count < 0:
        raise ValueError("image_backfill_metadata_invalid")
    if ("active_generation" in metadata or "generation_files" in metadata
            or negative_count > 0 or directory.parent.name == ".generations"):
        raise ValueError("image_backfill_requires_create_images_indices")
    paths = []
    with (directory / "entries.jsonl").open("r", encoding="utf-8") as entries:
        for line in entries:
            if not line.strip():
                continue
            entry = json.loads(line)
            if not isinstance(entry, dict):
                raise ValueError("image_backfill_entry_invalid")
            if entry.get("indexing_status") == "not_indexed":
                raise ValueError("image_backfill_requires_create_images_indices")
            path = entry.get("path")
            if isinstance(path, str) and path:
                paths.append(path)
    return paths
