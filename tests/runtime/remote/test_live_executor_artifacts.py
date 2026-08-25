from __future__ import annotations

import base64
import hashlib
import json
import tomllib
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import agent_server
import invocations
import loader
from contract_store import publish_signed_source
from i18n_materializer import encode_language_state, manifest_language_selectors
from manifest_inventory import ManifestOrigin, ManifestSource, inventory_authoring_manifests
from sign import sign_manifest_bytes


def _hash_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _language_state(manifest: Mapping[str, Any]) -> bytes:
    state = {
        "schema_version": 1,
        "selectors": {
            selector: {
                language: {
                    "version_hash": _hash_text(text),
                    "source_lang": None,
                    "source_hash": None,
                }
                for language, text in table.items()
            }
            for selector, table in manifest_language_selectors(manifest).items()
        },
    }
    return encode_language_state(state, manifest=manifest)


def _live_executor(tmp_path: Path, monkeypatch):
    source_root = tmp_path / "authoring"
    directory = source_root / "get_processes"
    directory.mkdir(parents=True)
    code = directory / "get_processes.py"
    code_bytes = (
        b"def invoke(args):\n"
        b"    return {'ok': True, 'results': []}\n"
        b"def reverse(plan, results):\n"
        b"    return {'ok': True}\n"
        b"if __name__ == \"__main__\":\n"
        b"    pass\n"
    )
    code.write_bytes(code_bytes)
    digest = "sha256:" + hashlib.sha256(code_bytes).hexdigest()
    manifest = directory / "manifest.toml"
    manifest.write_text(
        f'''manifest_format = "1.0"
executor_standard = "metnos.executor/1.0"
name = "get_processes"
version = "1.0.0"
lifecycle = "active"
platforms = ["windows"]
affinity = []
revertible = true
reverse_pattern = "module.reverse"

[description]
it = "SCOPO: legge una prova. PATTERN: get_processes(). NON: modificare dati. OUT: results=[]."
en = "SCOPO: reads a probe. PATTERN: get_processes(). NON: modify data. OUT: results=[]."

[code]
files = ["get_processes.py"]
digest = "{digest}"

[output]
schema_inline = "{{ ok: bool, results: list }}"

[[capabilities]]
name = "compute:pure"
hint = []

[[tests]]
name = "sample"
input = {{}}
expect = {{ ok = true }}

[[managed_dependencies]]
key = "hardware_sensor_provider"
package_id = "Vendor.SensorProvider"
mode = "provider"
interface = "hardware_sensors_v1"
domains_arg = "sensor_domains"
sensor_types_arg = "sensor_types"
assembly = "SensorProvider.dll"
entry_type = "Vendor.SensorProvider.EntryPoint"

[args]
type = "object"
required = []

[args.properties.sensor_domains]
type = "array"

[args.properties.sensor_domains.description]
it = "Domini dei sensori richiesti."
en = "Requested sensor domains."

[args.properties.sensor_types]
type = "array"

[args.properties.sensor_types.description]
it = "Tipi di sensore richiesti."
en = "Requested sensor types."
''',
        encoding="utf-8",
    )
    parsed = tomllib.loads(manifest.read_text(encoding="utf-8"))
    (directory / "manifest.lang_state.json").write_bytes(
        _language_state(parsed)
    )
    private = Ed25519PrivateKey.generate()
    (directory / "manifest.toml.sig").write_bytes(
        sign_manifest_bytes(manifest.read_bytes(), private_key=private)
    )
    inventory = inventory_authoring_manifests((ManifestSource(
        ManifestOrigin.CORE,
        source_root,
        allowed_code_roots=(source_root,),
    ),))
    assert not inventory.problems
    ref = inventory.manifests[0]
    trusted = (("author", private.public_key()),)

    shadow = tmp_path / "shadow-v1"
    result = publish_signed_source(
        ref,
        expected_generation_id=None,
        trusted_publics=trusted,
        store_root=shadow,
    )
    state = tmp_path / "state"
    production = state / "contract-publications"
    production.mkdir(parents=True)
    shadow.rename(production / "v1")

    # Sentinel: every attempted authoring-manifest read now fails, while the
    # structural parent and its code remain available to the live generation.
    manifest.unlink()
    manifest.mkdir()

    monkeypatch.setattr(loader._C, "PATH_USER_STATE", state)
    monkeypatch.setattr(loader._C, "PATH_EXECUTORS", source_root)
    monkeypatch.setattr(loader, "list_trusted_publics", lambda: list(trusted))
    monkeypatch.setattr(
        invocations, "list_trusted_publics", lambda: list(trusted)
    )
    monkeypatch.setattr(
        invocations, "load_public", lambda _name: private.public_key()
    )
    monkeypatch.setattr(invocations, "load_private", lambda _name: private)
    loader.invalidate_catalog_cache()
    return manifest, code_bytes, result.current_generation_id, private


def test_store_artifact_hashes_reverse_and_provider_ignore_authoring_manifest(
    tmp_path: Path,
    monkeypatch,
) -> None:
    sentinel, code_bytes, generation, private = _live_executor(
        tmp_path, monkeypatch
    )

    artifact = invocations.load_executor_artifact("get_processes")
    assert sentinel.is_dir()
    assert artifact.generation_id == generation
    assert "generations" in artifact.manifest_path.parts
    assert artifact.code_files == (("get_processes.py", code_bytes),)
    assert invocations.executor_shas("get_processes") == (
        hashlib.sha256(artifact.manifest_bytes).hexdigest(),
        hashlib.sha256(code_bytes).hexdigest(),
    )

    device = SimpleNamespace(profile_json=json.dumps({
        "protocol_capabilities": [invocations.REMOTE_REVERSE_CAPABILITY],
    }))
    invocations._validate_operation(
        artifact,
        {"plan": {}, "results": {}},
        "reverse",
        device,
    )

    grants = invocations._managed_provider_grants(
        artifact,
        {
            "sensor_domains": ["cpu"],
            "sensor_types": ["temperature"],
        },
        "inv-store-sentinel",
    )
    assert len(grants) == 1
    grant = grants[0]
    assert grant["manifest_sha256"] == artifact.manifest_sha256
    private.public_key().verify(
        invocations._b64u_decode(grant["server_sig"]),
        invocations.managed_provider_grant_body(grant),
    )


def test_store_bundle_serves_only_the_reverified_snapshot_bytes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    sentinel, code_bytes, _generation, _private = _live_executor(
        tmp_path, monkeypatch
    )
    payload = agent_server._executor_bundle_payload("get_processes")
    manifest_bytes = base64.b64decode(payload["manifest_toml"])
    served_code = base64.b64decode(payload["files"]["get_processes.py"])
    assert sentinel.is_dir()
    assert served_code == code_bytes
    manifest = tomllib.loads(manifest_bytes.decode("utf-8"))
    assert manifest["code"]["digest"] == (
        "sha256:" + hashlib.sha256(served_code).hexdigest()
    )
    private_public = _private.public_key()
    private_public.verify(base64.b64decode(payload["manifest_sig"]), manifest_bytes)


def test_store_artifact_fails_closed_on_code_drift(
    tmp_path: Path,
    monkeypatch,
) -> None:
    sentinel, _code_bytes, _generation, _private = _live_executor(
        tmp_path, monkeypatch
    )
    (sentinel.parent / "get_processes.py").write_bytes(b"changed after publish\n")
    loader.invalidate_catalog_cache()

    with pytest.raises(invocations.InvocationError):
        invocations.load_executor_artifact("get_processes")
