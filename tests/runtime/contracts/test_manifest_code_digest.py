"""The digest preparer is deterministic and owns no publication authority."""
from __future__ import annotations

import ast
import hashlib
import tomllib
from pathlib import Path

import pytest

from manifest_code_digest import (
    compute_code_digest, prepare_manifest_digest_v1,
)


def test_birth_preparation_and_historical_signer_share_one_digest(tmp_path: Path):
    code = b"VALUE = 1\n"
    helper = b"HELPER = 2\n"
    (tmp_path / "entry.py").write_bytes(code)
    (tmp_path / "helper.py").write_bytes(helper)
    manifest = (
        '[code]\nfiles = ["entry.py", "helper.py"]\n'
        'digest = "sha256:' + "0" * 64 + '"\n'
    ).encode("utf-8")

    prepared = prepare_manifest_digest_v1(
        manifest, {"entry.py": code, "helper.py": helper},
    )

    expected = "sha256:" + hashlib.sha256(code + helper).hexdigest()
    assert tomllib.loads(prepared.decode("utf-8"))["code"]["digest"] == expected
    assert compute_code_digest(tmp_path, ["entry.py", "helper.py"]) == expected
    assert prepared.replace(expected.encode(), b"sha256:" + b"0" * 64) == manifest


@pytest.mark.parametrize("payloads", [
    {"entry.py": b"VALUE = 1\n"},
    {"entry.py": b"VALUE = 1\n", "extra.py": b"EXTRA = 1\n"},
])
def test_candidate_payload_set_must_match_the_manifest_exactly(payloads):
    manifest = (
        '[code]\nfiles = ["entry.py", "helper.py"]\n'
        'digest = "sha256:' + "0" * 64 + '"\n'
    ).encode("utf-8")
    with pytest.raises(ValueError, match="manifest_code_payloads_invalid"):
        prepare_manifest_digest_v1(manifest, payloads)


def test_digest_preparer_has_no_signing_or_publication_import():
    import manifest_code_digest

    tree = ast.parse(Path(manifest_code_digest.__file__).read_bytes())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert imported.isdisjoint({
        "contract_store", "executor_birth_intent", "sign", "cryptography",
    })
