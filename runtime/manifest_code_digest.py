"""Deterministic executor-code digest preparation without signing authority.

This module can read candidate code and transform manifest bytes. It owns no
key, lock, publication primitive or Birth capability. The historical signer
and Birth candidate preparation share these functions, so the derived
``[code].digest`` cannot drift apart.
"""
from __future__ import annotations

import hashlib
import re
import tomllib
from collections.abc import Mapping, Sequence
from pathlib import Path


_DIGEST_RE = re.compile(r'(digest\s*=\s*")sha256:[^"]*(")')


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
    """Replace only the derived digest field, preserving all other text."""
    if not _DIGEST_RE.search(manifest_text):
        raise ValueError("manifest non contiene una riga 'digest = \"sha256:...\"'")
    return _DIGEST_RE.sub(rf'\g<1>{new_digest}\g<2>', manifest_text)


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
