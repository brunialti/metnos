"""Deterministic executor-code digest preparation without signing authority.

This module can read candidate code and transform manifest bytes. It owns no
key, lock, publication primitive or Birth capability. The historical signer
and Birth candidate preparation share these functions, so the derived
``[code].digest`` cannot drift apart.
"""
from __future__ import annotations

import hashlib
import tomllib
from collections.abc import Mapping, Sequence
from pathlib import Path

import tomlkit


def code_digest_of_payloads(files: Sequence[str], payloads: Mapping[str, bytes]) -> str:
    """Digest the exact declared files, in declared order."""
    if (not isinstance(files, Sequence) or isinstance(files, (str, bytes))
            or not files or any(not isinstance(name, str) or not name for name in files)):
        raise ValueError("manifest_code_files_invalid")
    if len(files) != len(set(files)) or set(files) != set(payloads):
        raise ValueError("manifest_code_payloads_invalid")
    digest = hashlib.sha256()
    for name in files:
        payload = payloads[name]
        if not isinstance(payload, bytes):
            raise ValueError("manifest_code_payloads_invalid")
        digest.update(payload)
    return f"sha256:{digest.hexdigest()}"


def compute_code_digest(manifest_dir: Path, code_files: Sequence[str]) -> str:
    """Read and digest files with the historical signer semantics."""
    digest = hashlib.sha256()
    for name in code_files:
        with (Path(manifest_dir) / name).open("rb") as stream:
            for chunk in iter(lambda: stream.read(8192), b""):
                digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def update_digest_in_text(manifest_text: str, new_digest: str) -> str:
    """Set the derived code digest, including a candidate's first preparation."""
    document = tomlkit.parse(manifest_text)
    code = document.get("code")
    if not isinstance(code, Mapping):
        raise ValueError("manifest_code_files_invalid")
    # TOML structure, not a text pattern, owns the field. Other tables and
    # comments may also contain a digest and must remain untouched.
    if code.get("digest") == new_digest:
        return manifest_text
    code["digest"] = new_digest
    return tomlkit.dumps(document)


def prepare_manifest_digest_v1(
    manifest_bytes: bytes, code_payloads: Mapping[str, bytes],
) -> bytes:
    """Return manifest bytes whose digest derives only from the owned payloads."""
    try:
        text = manifest_bytes.decode("utf-8")
        manifest = tomllib.loads(text)
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ValueError("manifest_code_candidate_invalid") from exc
    code = manifest.get("code")
    files = code.get("files") if isinstance(code, dict) else None
    digest = code_digest_of_payloads(files, code_payloads)
    prepared = update_digest_in_text(text, digest).encode("utf-8")
    tomllib.loads(prepared.decode("utf-8"))
    return prepared


__all__ = [
    "code_digest_of_payloads", "compute_code_digest",
    "prepare_manifest_digest_v1", "update_digest_in_text",
]
