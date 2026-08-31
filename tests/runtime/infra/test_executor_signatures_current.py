"""Every shipped executor manifest must verify against a trusted key.

Nobody was checking this. On 31 August 2026 the repository carried 85 signed
executors and 84 of them verified: `find_packages` had its manifest and code
changed on the 30th without the signature being renewed, and the loader had
been silently rejecting it ever since — which cost roughly twenty red tests
that everyone was reading as "environment".

A stale signature is invisible by nature: the file is there, it has the right
name and the right size, and only a verification says it means nothing.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import sign


REPOSITORY = Path(__file__).resolve().parents[3]


def _signed_executors() -> list[Path]:
    root = REPOSITORY / "executors"
    return sorted(
        directory for directory in root.iterdir()
        if (directory / "manifest.toml").is_file()
        and (directory / "manifest.toml.sig").is_file()
    )


def test_at_least_one_executor_is_signed() -> None:
    """A pass with an empty inventory would be the worst outcome of all."""
    assert len(_signed_executors()) >= 50


@pytest.mark.parametrize(
    "directory", _signed_executors(), ids=lambda item: item.name,
)
def test_every_shipped_manifest_verifies(directory: Path) -> None:
    """The signature is renewed with the manifest, or it is a lie."""
    trusted = list(sign.list_trusted_publics())
    assert trusted, "no trusted signing key is configured"
    sign.verify_manifest_bytes(
        (directory / "manifest.toml").read_bytes(),
        (directory / "manifest.toml.sig").read_bytes(),
        trusted_publics=trusted,
    )


@pytest.mark.parametrize(
    "directory", _signed_executors(), ids=lambda item: item.name,
)
def test_every_declared_code_digest_matches_its_file(directory: Path) -> None:
    """A verified manifest that describes other code proves nothing.

    The signature covers the manifest, and the manifest names the code by
    digest. Checking only the first half would accept a correctly signed
    description of a file that is no longer there.
    """
    import hashlib
    import re

    manifest = (directory / "manifest.toml").read_text(encoding="utf-8")
    declared = re.search(r'digest\s*=\s*"(sha256:[0-9a-f]{64})"', manifest)
    if declared is None:
        pytest.skip("this manifest declares no code digest")
    files = re.search(r'files\s*=\s*\[([^\]]*)\]', manifest)
    assert files is not None, "a code digest without a file list"
    names = re.findall(r'"([^"]+)"', files.group(1))
    digest = hashlib.sha256()
    for name in names:
        digest.update((directory / name).read_bytes())
    assert f"sha256:{digest.hexdigest()}" == declared.group(1)
