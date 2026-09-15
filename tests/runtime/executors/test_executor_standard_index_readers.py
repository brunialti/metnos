"""Authority, privacy and behaviour gates for local image-index readers."""
from __future__ import annotations

import json
import shutil
import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


ROOT = Path(__file__).resolve().parents[3]
RUNTIME = ROOT / "runtime"
for path in (
    ROOT,
    RUNTIME,
    ROOT / "executors" / "find_images_indices",
    ROOT / "executors" / "find_persons_indices",
):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import find_images_indices as image_reader  # noqa: E402
import find_persons_indices as person_reader  # noqa: E402
from loader import Catalog, _load_dir_into_catalog  # noqa: E402
from prefilter import rank  # noqa: E402


def _sign_projected_record(record, monkeypatch: pytest.MonkeyPatch) -> None:
    import admitted_module_v1 as admitted

    manifest = Path(record.manifest_path)
    manifest_bytes = (
        f'name = "{record.name}"\n\n[code]\n'
        f'files = ["{record.code_files[0]}"]\n'
        f'digest = "{record.digest}"\n'
    ).encode("utf-8")
    manifest.write_bytes(manifest_bytes)
    private = Ed25519PrivateKey.generate()
    manifest.with_name("manifest.toml.sig").write_bytes(
        private.sign(manifest_bytes),
    )
    monkeypatch.setattr(
        admitted, "_projected_trusted_public_keys_v1",
        lambda: (private.public_key(),),
    )


@pytest.fixture
def signed_index_readers(tmp_path, monkeypatch):
    """Admit current candidate bytes without depending on their publication."""
    import agent_runtime
    import loader
    import sign
    from i18n_materializer import migrate_language_state_bytes
    from manifest_code_digest import prepare_manifest_digest_v1

    root = tmp_path / "signed-readers"
    keys = tmp_path / "signer-keys"
    keys.mkdir()
    private = Ed25519PrivateKey.generate()
    signer = "test-index-author"
    public_key = keys / f"{signer}_pub.bin"
    public_key.write_bytes(private.public_key().public_bytes_raw())
    public_key.chmod(0o644)
    names = ("find_images_indices", "find_persons_indices", "get_images_indices")
    for name in names:
        directory = root / name
        shutil.copytree(ROOT / "executors" / name, directory,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        path = directory / "manifest.toml"
        parsed = tomllib.loads(path.read_text())
        manifest = prepare_manifest_digest_v1(path.read_bytes(), {
            part: (directory / part).read_bytes() for part in parsed["code"]["files"]})
        state_path = directory / "manifest.lang_state.json"
        state = migrate_language_state_bytes(
            state_path.read_bytes(), manifest=tomllib.loads(manifest.decode()))
        path.write_bytes(manifest)
        state_path.write_bytes(state.state_bytes)
        path.with_suffix(".toml.sig").write_bytes(private.sign(manifest))
    monkeypatch.setattr(sign, "KEYS_DIR", keys)
    monkeypatch.setattr(loader, "list_trusted_publics", lambda: [(signer, private.public_key())])
    value = Catalog()
    _load_dir_into_catalog(root, value, True, is_synthesized=False)
    assert set(value.executors) == set(names)
    monkeypatch.setattr(agent_runtime, "load_catalog", lambda **_kwargs: value)
    return value


def _seed_private_index(base: Path, index_dir: Path) -> None:
    index_dir.mkdir(parents=True)
    entry = {
        "path": str(base / "photo.jpg"),
        "name": "photo.jpg",
        "description": "private portrait",
        "keywords": ["portrait"],
        "size": 123,
        "faces": [{
            "bbox": [1, 2, 30, 40],
            "embedding_face_idx": 0,
            "embedding_face": [0.1, 0.2, 0.3],
        }],
    }
    (index_dir / "entries.jsonl").write_text(
        json.dumps(entry) + "\n", encoding="utf-8",
    )
    np.save(index_dir / "embeddings_face.npy", np.ones((1, 512), dtype="float32"))
    (index_dir / "meta.json").write_text(json.dumps({
        "schema_version": 4,
        "version": 4,
        "base_path": str(base),
        "n_entries": 1,
    }), encoding="utf-8")


def test_reader_projection_never_exposes_biometric_embeddings(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("METNOS_INDEX_ROOT", str(tmp_path / "index"))
    base = tmp_path / "photos"
    base.mkdir()
    _seed_private_index(base, image_reader._index_dir(base))

    result = image_reader.invoke({
        "base_path": str(base),
        "min_face_pixels": 1,
    })

    assert result["ok"] is True
    assert result["entries"][0]["path"].endswith("photo.jpg")
    assert "embedding" not in json.dumps(result, sort_keys=True).lower()


def test_missing_index_is_read_only_and_names_the_builder(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    index_root = tmp_path / "index"
    monkeypatch.setenv("METNOS_INDEX_ROOT", str(index_root))
    base = tmp_path / "photos"
    base.mkdir()

    result = image_reader.invoke({"base_path": str(base), "query_text": "sea"})

    assert result["ok"] is False
    assert result["error_code"] == "image_index_missing"
    assert result["recommended_action"] == {
        "executor": "create_images_indices",
        "args": {"base_path": str(base)},
    }
    assert not index_root.exists()


def test_sandboxed_reader_sees_only_semantic_index_bind(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import agent_runtime
    import config
    import sandbox

    if not sandbox.bwrap_available():
        pytest.skip("bubblewrap unavailable")
    index_root = tmp_path / "index"
    monkeypatch.setenv("METNOS_INDEX_ROOT", str(index_root))
    monkeypatch.setattr(config, "PATH_INDEX_IMAGE", index_root / "image")
    base = tmp_path / "photos"
    base.mkdir()
    _seed_private_index(base, image_reader._index_dir(base))
    value = Catalog()
    _load_dir_into_catalog(ROOT / "executors", value, False, is_synthesized=False)
    executor = value.executors["find_images_indices"]

    result = agent_runtime.invoke_executor(
        executor,
        {"base_path": str(base), "min_face_pixels": 1},
        timeout_s=10,
        actor="host",
        channel="test",
    )

    if (not result.get("ok") and "bwrap:" in str(result.get("error"))
            and "Operation not permitted" in str(result.get("error"))):
        pytest.skip("kernel temporarily denied bubblewrap namespace creation")
    assert result["ok"] is True, result
    assert len(result["entries"]) == 1
    assert "embedding" not in json.dumps(result, sort_keys=True).lower()


def test_sandboxed_readers_reuse_logical_symlink_index_without_source_bind(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, signed_index_readers) -> None:
    """Regression turn 739f2212: materialized idx_dir is the lookup authority."""
    import agent_runtime
    import config
    import sandbox

    if not sandbox.bwrap_available():
        pytest.skip("bubblewrap unavailable")
    data = tmp_path / "data"
    target = tmp_path / "nas" / "photos"
    index_root = tmp_path / "index"
    data.mkdir()
    target.mkdir(parents=True)
    logical = data / "Immagini"
    logical.symlink_to(target, target_is_directory=True)
    monkeypatch.setenv("METNOS_USER_DATA", str(data))
    monkeypatch.setenv("METNOS_INDEX_ROOT", str(index_root))
    monkeypatch.setattr(config, "PATH_USER_DATA", data)
    monkeypatch.setattr(config, "PATH_INDEX_IMAGE", index_root / "image")
    _seed_private_index(target, image_reader._index_dir(logical))

    value = signed_index_readers
    cases = [
        ("find_images_indices", {"match_all": True}),
        ("find_persons_indices", {"name": "private"}),
        ("get_images_indices", {
            "base_path": str(target), "idx": "all",
        }),
    ]
    for name, args in cases:
        result = agent_runtime.invoke_executor(
            value.executors[name], args, timeout_s=15,
            actor="host", channel="test",
        )
        if (not result.get("ok") and "bwrap:" in str(result.get("error"))
                and "Operation not permitted" in str(result.get("error"))):
            pytest.skip("kernel temporarily denied bubblewrap namespace creation")
        assert result["ok"] is True, (name, result)
        if name == "get_images_indices":
            assert result["entries"][0]["exists"] is True
        else:
            assert result["entries"], (name, result)

    # The source and workspace link are outside every declared reader bind;
    # only the semantic index is needed to satisfy all three calls.
    assert logical.is_symlink()
    assert target.is_dir()


def test_sandboxed_person_reader_uses_read_only_registry_files(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import agent_runtime
    import config
    import persons_registry
    import sandbox

    if not sandbox.bwrap_available():
        pytest.skip("bubblewrap unavailable")
    data = tmp_path / "data"
    db = data / "persons.sqlite"
    registry = persons_registry.PersonsRegistry(db_path=db)
    try:
        embedding = np.zeros(persons_registry.EMBEDDING_DIM, dtype="float32")
        embedding[0] = 1.0
        registry.enroll(
            name="Ada", image_path=str(tmp_path / "ada.jpg"),
            face_box=(0, 0, 10, 10), embedding=embedding,
            sha256="a" * 64,
        )
    finally:
        registry.close()
    before = db.read_bytes()
    monkeypatch.setenv("METNOS_USER_DATA", str(data))
    monkeypatch.setattr(config, "PATH_USER_DATA", data)
    value = Catalog()
    _load_dir_into_catalog(ROOT / "executors", value, False, is_synthesized=False)

    result = agent_runtime.invoke_executor(
        value.executors["get_persons"], {}, timeout_s=10,
        actor="host", channel="test",
    )

    if (not result.get("ok") and "bwrap:" in str(result.get("error"))
            and "Operation not permitted" in str(result.get("error"))):
        pytest.skip("kernel temporarily denied bubblewrap namespace creation")
    assert result["ok"] is True, result
    assert result["entries"][0]["name"] == "Ada"
    assert db.read_bytes() == before


@pytest.mark.parametrize("invoke", [image_reader.invoke, person_reader.invoke])
def test_non_object_root_fails_before_resources(invoke) -> None:
    result = invoke([])
    assert result["ok"] is False
    assert result["error_class"] == "invalid_input"
    assert result["error_code"] == "args_not_object"


def test_person_alias_requires_a_search_criterion() -> None:
    result = person_reader.invoke({})
    assert result["ok"] is False
    assert result["error_code"] == "search_criterion_missing"


def test_person_alias_uses_the_parent_authenticated_engine_record(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from admitted_module_v1 import (
        ADMITTED_EXECUTORS_ENV_V1, code_digest_of_bytes_v1,
        encode_admitted_executor_records_v1,
    )

    directory = tmp_path / "find_images_indices"
    directory.mkdir()
    payload = (
        b"def invoke(args):\n"
        b"    return {'ok': True, 'entries': [], 'forwarded': args}\n"
    )
    code = directory / "find_images_indices.py"
    code.write_bytes(payload)
    record = SimpleNamespace(
        name="find_images_indices", manifest_path=directory / "manifest.toml",
        code_path=code, code_files=(code.name,),
        digest=code_digest_of_bytes_v1([payload]),
    )
    _sign_projected_record(record, monkeypatch)
    monkeypatch.setenv(
        ADMITTED_EXECUTORS_ENV_V1,
        encode_admitted_executor_records_v1([record]),
    )

    result = person_reader.invoke({"name": "Ada", "idx": "legacy"})

    assert result["ok"] is True
    assert result["forwarded"] == {"name": "Ada"}


def test_person_alias_refuses_changed_engine_before_execution(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from admitted_module_v1 import (
        ADMITTED_EXECUTORS_ENV_V1, code_digest_of_bytes_v1,
        encode_admitted_executor_records_v1,
    )

    directory = tmp_path / "find_images_indices"
    directory.mkdir()
    original = b"def invoke(args): return {'ok': True}\n"
    code = directory / "find_images_indices.py"
    code.write_bytes(original)
    record = SimpleNamespace(
        name="find_images_indices", manifest_path=directory / "manifest.toml",
        code_path=code, code_files=(code.name,),
        digest=code_digest_of_bytes_v1([original]),
    )
    _sign_projected_record(record, monkeypatch)
    monkeypatch.setenv(
        ADMITTED_EXECUTORS_ENV_V1,
        encode_admitted_executor_records_v1([record]),
    )
    marker = tmp_path / "executed"
    code.write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).touch()\n"
        "def invoke(args): return {'ok': True}\n",
        encoding="utf-8",
    )

    result = person_reader.invoke({"name": "Ada"})

    assert result["error_code"] == "executor_dependency_unavailable"
    assert not marker.exists()


def test_local_embedder_cannot_follow_remote_configuration(monkeypatch) -> None:
    import virt

    class Local:
        pass

    virt._cache.clear()
    monkeypatch.setattr(
        virt.tiers, "spec",
        lambda *_args, **_kwargs: {
            "provider": "http", "endpoint": "https://outside.example",
        },
    )
    monkeypatch.setattr("bge_embedding.BGEEmbeddingService", lambda _path=None: Local())

    assert isinstance(virt.get_local_embedder("text"), Local)
    assert all(key[0] != "emb" for key in virt._cache)
    virt._cache.clear()


@pytest.fixture(scope="module")
def catalog(standard_catalog):
    return standard_catalog


@pytest.mark.parametrize(
    ("name", "query"),
    [
        ("find_images_indices", "cerca le foto indicizzate scattate al mare"),
        ("find_images_indices", "show indexed pictures of a mountain landscape"),
        ("find_persons_indices", "mostrami i primi piani di una persona registrata"),
        ("find_persons_indices", "find close-up photos of an enrolled person"),
    ],
)
def test_natural_paraphrases_remain_routable(name: str, query: str, catalog) -> None:
    names = [item.name for item in rank(query, catalog, k=8, min_score=1)]
    assert name in names, (query, names)
