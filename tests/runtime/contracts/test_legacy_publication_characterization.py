from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from i18n_pipeline import _promote_contracts
from i18n_registry import ResourceRecord


def _manifest_text(*, code_file: str, digest: str) -> str:
    return (
        'name="sample"\nversion="1.0.0"\n'
        '[description]\nen="SCOPO: read. PATTERN: sample(). NON: write. OUT: {ok}."\n'
        'it="SCOPO: legge. PATTERN: sample(). NON: scrive. OUT: {ok}."\n\n'
        '[args]\ntype="object"\n'
        '[output]\nschema_inline="{ok: bool}"\n'
        f'[code]\nfiles=["{code_file}"]\ndigest="{digest}"\n'
    )


def test_verifier_parses_the_same_manifest_bytes_whose_signature_passed(
    tmp_path: Path, monkeypatch,
) -> None:
    import sign

    executor = tmp_path / "sample"
    executor.mkdir()
    first_code = executor / "first.py"
    second_code = executor / "second.py"
    first_code.write_text("print('first')\n", encoding="utf-8")
    second_code.write_text("print('second')\n", encoding="utf-8")
    first_digest = sign.compute_code_digest(executor, [first_code.name])
    second_digest = sign.compute_code_digest(executor, [second_code.name])
    manifest = executor / "manifest.toml"
    manifest.write_text(
        _manifest_text(code_file=first_code.name, digest=first_digest),
        encoding="utf-8",
    )
    monkeypatch.setattr(sign, "KEYS_DIR", tmp_path / "keys")
    sign.generate_keypair("author")
    sign.sign_executor(executor)
    swapped = _manifest_text(code_file=second_code.name, digest=second_digest)
    original_read_bytes = Path.read_bytes
    swapped_on_disk = False

    def read_bytes(path: Path, *args, **kwargs):
        nonlocal swapped_on_disk
        value = original_read_bytes(path, *args, **kwargs)
        if path == manifest and not swapped_on_disk:
            swapped_on_disk = True
            manifest.write_text(swapped, encoding="utf-8")
        return value

    monkeypatch.setattr(Path, "read_bytes", read_bytes)

    ok, details = sign.verify_executor(executor)

    assert ok
    assert details["digest"] == first_digest
    assert manifest.read_text(encoding="utf-8") == swapped


@pytest.mark.xfail(
    strict=True,
    reason="RM-0007 M3 must resolve destinations only through ContractId inventory",
)
def test_legacy_publisher_rejects_registry_supplied_manifest_path(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    manifest = outside / "manifest.toml"
    original = _manifest_text(code_file="sample.py", digest="sha256:" + "0" * 64)
    manifest.write_text(original, encoding="utf-8")
    translation = "SCOPO: aggiornato. PATTERN: sample(). NON: scrive. OUT: {ok}."
    encoded = json.dumps(
        translation, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    artifact = tmp_path / "candidate.json"
    artifact.write_text(json.dumps({
        "schema": "metnos.localization-candidate/1",
        "resource_id": "contract:sample:description",
        "layer": "contract",
        "source_lang": "en",
        "target_lang": "nl",
        "source_hash": "0" * 64,
        "translation": translation,
    }), encoding="utf-8")
    record = ResourceRecord(
        resource_id="contract:sample:description",
        layer="contract",
        source_lang="en",
        target_lang="nl",
        source_hash="0" * 64,
        status="translated",
        attempts=1,
        translation_hash=hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        quality="reviewed",
        artifact_path=str(artifact),
        last_error=None,
        metadata={
            "manifest_path": str(manifest),
            "selector": "description",
            "executor": "sample",
        },
    )

    admitted, errors = _promote_contracts([record], "nl", lambda _path: None)

    assert admitted == 0
    assert record.resource_id in errors
    assert manifest.read_text(encoding="utf-8") == original
