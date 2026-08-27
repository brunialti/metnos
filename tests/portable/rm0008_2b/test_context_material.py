"""The inert context material: described, never applied by this increment.

The framing is the one the runtime already owns, so the normative vector of
section 9.5 is pinned here.  What the provisioner adds is the closed catalogue
and the honest ``enforcement_state`` of every component.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from install import birth_authority_provisioner as provisioning
from install.birth_authority_provisioner import BirthProvisioningError

from . import support

pytestmark = pytest.mark.skipif(
    os.name == "nt", reason="the Windows profile is certified by its own job"
)

GOLDEN_COMPONENT_DIGESTS = {
    "standard": "sha256:3848d69a2e2d89bc24d1e87304c16ad27fb158862fd7b79565dc849d6f36a952",
    "linter": "sha256:f58f0037431e9965eaf697617687ca66443fd59829575b22cd821575d47151e8",
    "vocabulary": "sha256:6aa47c6c49534271cb419cda28cb0ec06eb9aee2b8a99afbfdaba1fe35a1b980",
    "authority_registry": "sha256:87886c06ed696b9d52f4c327b9bf06177df1050a08e2c95ff50c5dd0ee2e0e3e",
    "sandbox_registry": "sha256:55ed0376bddf12d793c9262c902d8cf324fbcc978de62b1d692ea25e53b40f7c",
    "property_catalog": "sha256:417a5714e9c61749baf3e7527b3fe26404b7ae57ca8d57ea26165018738f06b3",
    "runner": "sha256:d68adf14a533e57809f0759effb7f9ac4f23747b80e070c48afb91d9a3ea48ad",
    "review_policy": "sha256:eee40cfa270d09e3ff0eee90ddd02783a6972a7cb820af90d5f322fd9abe5ca2",
    "template_allowlist": "sha256:7fccbc7ae834cdb499b2457314bc96ded3e21535fedbd07273fe1d2de4a57e87",
    "primitive_allowlist": "sha256:b2db20dfbdf40669c399f093974f6ad729bc2070683134eb8de187e73e57b324",
    "dependency_allowlist": "sha256:0cf3749d74b739baa81bac198b9917cea915e4ad47e6c016df64a9527df7444a",
}
GOLDEN_CONTEXT_ID = (
    "sha256:4bf49733b5fe2295b90df04cc906bc7ffece72a79cd956f5a3dd3aa0f5c04710"
)
GOLDEN_CONTEXT_EPOCH = (
    "sha256:9eac9b907a5d5a1f799c6eb09751eadcd5bc6a5de0959013001d56f4dc29d86c"
)

REGISTRY = {"author": {"key_ids": ["one"]}}


def _prepare(tmp_path: Path, monkeypatch, registry=None):
    support.stage_runtime_sources(tmp_path, monkeypatch)
    return provisioning._prepare_installed_admission_context_v1(
        REGISTRY if registry is None else registry
    )


def test_the_normative_vector_of_the_framing_still_holds():
    """Section 9.5: the framing is the runtime's own and must not drift."""
    import executor_birth_context as context

    material = context.AdmissionContextMaterial(**{
        name: context.ComponentMaterial(
            version="1", files=(),
            configuration={"enforcement_state": "prepared_only", "fixture": "v1"},
        )
        for name in context._COMPONENT_NAMES
    })
    built = context.build_admission_context(material)
    assert {
        name: getattr(built.context, name).digest
        for name in context._COMPONENT_NAMES
    } == GOLDEN_COMPONENT_DIGESTS
    assert built.pin.admission_context_id == GOLDEN_CONTEXT_ID
    assert built.pin.context_epoch == GOLDEN_CONTEXT_EPOCH


def test_the_catalogue_covers_the_eleven_closed_names():
    import executor_birth_context as context

    names = tuple(item[0] for item in provisioning._CONTEXT_CATALOG_V1)
    assert names == context._COMPONENT_NAMES
    assert len(set(names)) == len(names)
    states = {item[3] for item in provisioning._CONTEXT_CATALOG_V1}
    assert states <= {"productive", "prepared_only"}
    assert "prepared_only" in states


def test_every_catalogued_file_exists_in_the_distribution():
    import importlib

    runtime_config = importlib.import_module("config")
    for _, _, files, _ in provisioning._CONTEXT_CATALOG_V1:
        for name in files:
            assert (Path(runtime_config.PATH_RUNTIME) / name).is_file(), name


def test_the_material_document_has_the_closed_schema(tmp_path: Path, monkeypatch):
    prepared = _prepare(tmp_path, monkeypatch)
    document = json.loads(prepared.document)
    assert set(document) == {
        "schema_version", "state", "components",
        "prepared_admission_context_id", "prepared_context_epoch",
    }
    assert document["schema_version"] == 1
    assert document["state"] == "prepared_not_active"
    assert set(document["components"]) == set(GOLDEN_COMPONENT_DIGESTS)
    for name, component in document["components"].items():
        assert set(component) == {
            "version", "files", "configuration", "component_digest",
        }
        assert component["component_digest"].startswith("sha256:")
        assert component["configuration"]["enforcement_state"] in {
            "productive", "prepared_only",
        }
        for record in component["files"]:
            assert set(record) == {"label", "size", "sha256"}
            assert "/" not in record["label"] and record["size"] > 0
    assert document["prepared_admission_context_id"] == (
        prepared.prepared_admission_context_id
    )
    assert document["prepared_context_epoch"] == prepared.prepared_context_epoch
    assert prepared.document == support.canonical_json(document)


def test_the_document_carries_no_path_and_no_code(tmp_path: Path, monkeypatch):
    prepared = _prepare(tmp_path, monkeypatch)
    text = prepared.document.decode("utf-8")
    assert "/tmp" not in text and str(tmp_path) not in text
    assert "import " not in text and "def " not in text


def test_the_material_is_stable_and_follows_its_sources(
    tmp_path: Path, monkeypatch,
):
    first = _prepare(tmp_path / "a", monkeypatch)
    again = _prepare(tmp_path / "b", monkeypatch)
    assert again.document == first.document
    assert again.source_inventory_sha256 == first.source_inventory_sha256

    stage = support.stage_runtime_sources(tmp_path / "c", monkeypatch)
    (stage / "vocab.py").write_bytes(
        (stage / "vocab.py").read_bytes() + b"\n# changed\n"
    )
    os.chmod(stage / "vocab.py", 0o644)
    changed = provisioning._prepare_installed_admission_context_v1(REGISTRY)
    assert changed.prepared_admission_context_id != (
        first.prepared_admission_context_id
    )
    assert changed.prepared_context_epoch != first.prepared_context_epoch
    assert changed.source_inventory_sha256 != first.source_inventory_sha256


def test_the_material_follows_the_prepared_authorities(
    tmp_path: Path, monkeypatch,
):
    first = _prepare(tmp_path / "a", monkeypatch)
    other = _prepare(
        tmp_path / "b", monkeypatch, registry={"author": {"key_ids": ["two"]}},
    )
    assert other.prepared_admission_context_id != (
        first.prepared_admission_context_id
    )
    document = json.loads(other.document)
    registry = document["components"]["authority_registry"]["configuration"]
    assert registry["registry"] == {"author": {"key_ids": ["two"]}}


def test_a_catalogue_entry_that_moves_changes_the_identity(
    tmp_path: Path, monkeypatch,
):
    """Section 9.2: adding or removing an entry must change the digests."""
    first = _prepare(tmp_path, monkeypatch)
    original = provisioning._CONTEXT_CATALOG_V1
    shortened = tuple(
        (name, version, files[:-1] if name == "standard" else files, state)
        for name, version, files, state in original
    )
    monkeypatch.setattr(provisioning, "_CONTEXT_CATALOG_V1", shortened)
    fewer = provisioning._prepare_installed_admission_context_v1(REGISTRY)
    assert fewer.prepared_admission_context_id != (
        first.prepared_admission_context_id
    )

    promoted = tuple(
        (name, version, files, "productive" if name == "linter" else state)
        for name, version, files, state in original
    )
    monkeypatch.setattr(provisioning, "_CONTEXT_CATALOG_V1", promoted)
    changed = provisioning._prepare_installed_admission_context_v1(REGISTRY)
    assert changed.prepared_admission_context_id != (
        first.prepared_admission_context_id
    )


def test_the_factory_takes_no_catalogue_from_the_caller():
    import inspect

    signature = inspect.signature(
        provisioning._prepare_installed_admission_context_v1
    )
    assert list(signature.parameters) == ["authority_registry"]


def test_an_absent_distribution_is_an_incomplete_catalogue(
    tmp_path: Path, monkeypatch,
):
    import importlib

    runtime_config = importlib.import_module("config")
    monkeypatch.setattr(runtime_config, "PATH_RUNTIME", tmp_path / "absent")
    with pytest.raises(BirthProvisioningError) as error:
        provisioning._prepare_installed_admission_context_v1(REGISTRY)
    assert error.value.code == "birth_context_catalog_incomplete"


def test_a_writable_distribution_is_refused(tmp_path: Path, monkeypatch):
    stage = support.stage_runtime_sources(tmp_path, monkeypatch)
    os.chmod(stage, 0o777)
    with pytest.raises(BirthProvisioningError) as error:
        provisioning._prepare_installed_admission_context_v1(REGISTRY)
    assert error.value.code == "birth_provisioning_acl_unsafe"
