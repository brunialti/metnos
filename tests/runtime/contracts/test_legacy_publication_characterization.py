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


@pytest.mark.xfail(
    strict=True,
    reason="RM-0007 M1 must parse the same manifest bytes whose signature passed",
)
def test_verifier_rejects_manifest_swapped_between_signature_and_parse(
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
    original_read_text = Path.read_text

    def read_text(path: Path, *args, **kwargs):
        if path == manifest:
            return swapped
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)

    ok, _details = sign.verify_executor(executor)

    assert not ok


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
