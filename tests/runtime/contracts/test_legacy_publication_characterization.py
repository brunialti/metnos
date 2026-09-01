from __future__ import annotations

from pathlib import Path

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
    import executor_birth_legacy_gate as gate
    import sign

    monkeypatch.setattr(gate, "closed_build_enforcement", lambda: False)

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


def test_legacy_registry_path_publisher_no_longer_exists() -> None:
    import i18n_pipeline

    assert not hasattr(i18n_pipeline, "_promote_contracts")
