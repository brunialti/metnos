"""Local model grants expose artifacts, never the surrounding configuration."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import config
import sandbox
import virt
from virt import local_models


def test_image_reader_freezes_physical_alias_without_mounting_workspace(tmp_path, monkeypatch):
    from index_schema import corpus_digest

    data, physical = tmp_path / "data", tmp_path / "physical-photos"
    data.mkdir()
    physical.mkdir()
    logical = data / "Immagini"
    logical.symlink_to(physical, target_is_directory=True)
    monkeypatch.setenv("METNOS_USER_DATA", str(data))
    executor = SimpleNamespace(capabilities=[
        {"name": "fs:read", "hint": ["arg:base_path"]},
        {"name": "index:read", "hint": ["image"]},
    ], args_schema={"properties": {"base_path": {"type": "string"}}})
    expected = corpus_digest(physical)
    args = sandbox.resolve_filesystem_read_args(executor, {"base_path": str(physical)})
    assert args["base_path"] == str(logical)
    assert sandbox.filesystem_extras(executor, args) == [logical]
    # Bubblewrap exposes the selected logical path as a directory, not the
    # host symlink. Its digest must remain identical without the alias root.
    logical.unlink()
    logical.mkdir()
    assert corpus_digest(args["base_path"]) == expected
    executor.capabilities.pop()
    assert sandbox.resolve_filesystem_read_args(executor, {"base_path": str(physical)})["base_path"] == str(physical)


@pytest.fixture
def local_installation(tmp_path, monkeypatch):
    root = tmp_path / "installation"
    root.mkdir()
    monkeypatch.setattr(config, "PATH_ROOT", root)
    monkeypatch.delenv(local_models.PROJECTION_ENV, raising=False)
    monkeypatch.delenv("METNOS_CLIP_MODEL_DIR", raising=False)
    monkeypatch.delenv("METNOS_FACE_MODEL_DIR", raising=False)
    monkeypatch.setattr(virt.tiers, "spec", lambda _kind, role, defaults: defaults[role])
    return root


def _artifacts(root, *names):
    for name in names:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic local model artifact")


def _arguments(*hints, mode="read"):
    return sandbox._local_model_projection_args([
        {"name": f"metnos:{mode}", "hint": list(hints)},
    ])


def _bindings(args):
    return [tuple(args[i + 1:i + 3]) for i, value in enumerate(args)
            if value == "--ro-bind"]


def _environment(args):
    return {args[i + 1]: args[i + 2] for i, value in enumerate(args)
            if value == "--setenv"}


def test_only_declared_artifacts_are_projected_read_only(local_installation):
    text = local_installation / "models" / "embedding-bge"
    _artifacts(text, "tokenizer.json", "onnx/sentence_transformers_int8.onnx",
               "admin.key", "embedding_tiers.toml", "other.onnx")
    _artifacts(local_installation / "models" / "face", "det_10g.onnx")

    args = _arguments("embedding_text:local")

    assert _bindings(args) == [
        (str(text / relative), str(local_models.PROJECTION_ROOT / "text" / relative))
        for relative in ("onnx/sentence_transformers_int8.onnx", "tokenizer.json")
    ]
    assert "--bind" not in args
    assert args[args.index("--remount-ro") + 1] == str(local_models.PROJECTION_ROOT)
    for parent in (local_installation, text, text.parent):
        assert str(parent) not in args
    assert all("admin.key" not in value and "embedding_tiers.toml" not in value
               for value in args)


def test_tier_projection_omits_remote_secrets_and_honors_local_configuration(
        tmp_path, local_installation, monkeypatch):
    configured = tmp_path / "text-checkout"
    _artifacts(configured, "tokenizer.json", "onnx/sentence_transformers_int8.onnx")
    monkeypatch.setattr(virt.tiers, "spec", lambda *_args: {
        "provider": "bge", "model_dir": str(configured),
        "api_key": "fixture-secret", "base_url": "https://user:secret@example.test",
        "query_instruction": "not a BGE option",
    })

    args = _arguments("embedding_text:local")
    projected = _environment(args)[local_models.PROJECTION_ENV]

    assert json.loads(projected) == {"text": {
        "provider": "bge", "model_dir": str(local_models.PROJECTION_ROOT / "text"),
    }}
    assert "secret" not in " ".join(args)
    assert "query_instruction" not in projected
    assert all(Path(source).is_relative_to(configured) for source, _dest in _bindings(args))


def test_remote_tier_cannot_expand_local_authority(local_installation, monkeypatch):
    default = local_installation / "models" / "embedding-bge"
    _artifacts(default, "tokenizer.json")
    monkeypatch.setattr(virt.tiers, "spec", lambda *_args: {
        "provider": "http", "model_dir": "/unrelated/remote-cache",
        "endpoint": "https://user:secret@example.test", "api_key": "fixture-secret",
    })

    args = _arguments("embedding_text:local")

    assert _bindings(args) == [(str(default / "tokenizer.json"),
                               str(local_models.PROJECTION_ROOT / "text/tokenizer.json"))]
    assert "secret" not in " ".join(args)
    assert "remote-cache" not in " ".join(args)


@pytest.mark.parametrize("mode", ["write", "create", "cache"])
def test_model_hints_never_grant_write_access(mode, local_installation):
    _artifacts(local_installation / "models/embedding-bge", "tokenizer.json")

    assert not _bindings(_arguments("embedding_text:local", mode=mode))


def test_unknown_hints_and_inherited_projection_grant_nothing(
        local_installation, monkeypatch):
    monkeypatch.setenv(local_models.PROJECTION_ENV, '{"text":{"model_dir":"/"}}')
    monkeypatch.setenv("METNOS_FACE_MODEL_DIR", "/private")

    args = _arguments("embedding_unknown:local", "/private", "embedding_text:remote")

    assert not _bindings(args)
    assert _environment(args) == {
        local_models.PROJECTION_ENV: "{}",
        "METNOS_CLIP_MODEL_DIR": str(local_models.PROJECTION_ROOT / "image"),
        "METNOS_FACE_MODEL_DIR": str(local_models.PROJECTION_ROOT / "face"),
    }


def test_inherited_projection_cannot_choose_host_model_files(
        local_installation, monkeypatch):
    checkout = local_installation / "models/embedding-bge"
    _artifacts(checkout, "tokenizer.json")
    monkeypatch.setenv(local_models.PROJECTION_ENV, '{"text":{"model_dir":"/private"}}')

    args = _arguments("embedding_text:local")

    assert _bindings(args)[0][0] == str(checkout / "tokenizer.json")
    assert "/private" not in " ".join(args)


def test_missing_local_model_is_not_bootstrapped(local_installation):
    missing = local_installation / "models/embedding-bge"

    args = _arguments("embedding_text:local")

    assert not _bindings(args)
    assert not missing.exists()
    assert json.loads(_environment(args)[local_models.PROJECTION_ENV])["text"]["provider"] == "bge"


def test_image_and_face_overrides_are_independent_read_only_grants(
        tmp_path, local_installation, monkeypatch):
    face = tmp_path / "configured-face"
    image = tmp_path / "configured-siglip"
    _artifacts(face, "det_10g.onnx", "w600k_r50.onnx", "private.env")
    _artifacts(image, "text_model.onnx", "vision_model.onnx", "tokenizer.json")
    monkeypatch.setenv("METNOS_FACE_MODEL_DIR", str(face))
    monkeypatch.setenv("METNOS_CLIP_MODEL_DIR", str(image))

    args = _arguments("embedding_image:local", "embedding_face:local")

    assert len(_bindings(args)) == 5
    assert all(Path(source).parent in (face, image) for source, _dest in _bindings(args))
    assert "private.env" not in " ".join(args)
    assert set(json.loads(_environment(args)[local_models.PROJECTION_ENV])) == {"image"}
    assert all("embedding-bge" not in source for source, _dest in _bindings(args))


def test_external_image_and_face_config_survives_an_immutable_release(
        tmp_path, local_installation, monkeypatch):
    import face_embedding

    configured = tmp_path / "shared-models"
    face, image = configured / "face", configured / "image"
    _artifacts(face, "det_10g.onnx", "w600k_r50.onnx", "private.env")
    _artifacts(image, "text_model.onnx", "vision_model.onnx", "tokenizer.json")
    monkeypatch.setattr(virt.tiers, "spec", lambda _kind, role, defaults: {
        **defaults[role], "model_dir": str(configured / role), "api_key": "fixture-secret"})

    assert not (local_installation / "models").exists()
    assert face_embedding._default_model_dir() == face
    bindings = _bindings(_arguments("embedding_image:local", "embedding_face:local"))
    assert len(bindings) == 5
    assert all(Path(source).parent in (face, image) for source, _dest in bindings)
    assert not any("private.env" in source for source, _dest in bindings)
    # An existing installation-independent projection remains authoritative.
    monkeypatch.setenv("METNOS_FACE_MODEL_DIR", str(local_models.PROJECTION_ROOT / "face"))
    assert face_embedding._default_model_dir() == local_models.PROJECTION_ROOT / "face"


def test_face_asset_configuration_keeps_the_provider_role_closed():
    from virt.config_editor import ConfigEditError, _validate

    _validate("embedding", {"face": {"provider": "face", "model_dir": "/models/face"}})
    for role, provider in (("text", "face"), ("image", "face"), ("face", "http")):
        with pytest.raises(ConfigEditError, match=f"{role}.provider"):
            _validate("embedding", {role: {"provider": provider, "endpoint": "http://localhost:8080"}})


def test_cached_qwen_symlinks_project_exact_files_not_the_cache(
        tmp_path, local_installation, monkeypatch):
    checkout = tmp_path / "cache" / "snapshots" / "revision"
    blob = tmp_path / "cache" / "blobs" / "weights"
    _artifacts(blob.parent, blob.name)
    _artifacts(checkout, "config.json", "tokenizer.json", "1_Pooling/config.json")
    (checkout / "model.safetensors").symlink_to(blob)
    (checkout / "modules.json").write_text(json.dumps([
        {"path": ""}, {"path": "1_Pooling"},
    ]))
    monkeypatch.setattr(virt.tiers, "spec", lambda *_args: {
        "provider": "qwen", "model_dir": str(checkout),
        "query_instruction": "Retrieve matching descriptions.", "api_key": "fixture-secret",
    })

    args = _arguments("embedding_text:local")

    assert (str(blob), str(local_models.PROJECTION_ROOT / "text/model.safetensors")) in _bindings(args)
    assert (str(checkout / "1_Pooling/config.json"),
            str(local_models.PROJECTION_ROOT / "text/1_Pooling/config.json")) in _bindings(args)
    assert str(blob.parent) not in args
    assert str(checkout) not in args
    assert "fixture-secret" not in " ".join(args)
    assert json.loads(_environment(args)[local_models.PROJECTION_ENV])["text"]["query_instruction"] == "Retrieve matching descriptions."


def test_qwen_module_path_cannot_escape_projection(local_installation, monkeypatch):
    checkout = local_installation / "models/qwen"
    _artifacts(checkout, "config.json")
    (checkout / "modules.json").write_text('[{"path":"../../credentials"}]')
    monkeypatch.setattr(virt.tiers, "spec", lambda *_args: {
        "provider": "qwen", "model_dir": str(checkout),
    })

    with pytest.raises(sandbox.SandboxUnavailableError, match="local model projection"):
        _arguments("embedding_text:local")


def test_uninstalled_qwen_is_not_downloaded(local_installation, monkeypatch):
    monkeypatch.setattr(virt.tiers, "spec", lambda *_args: {"provider": "qwen"})
    monkeypatch.setattr("huggingface_hub.try_to_load_from_cache", lambda *_args: None)
    monkeypatch.setattr("huggingface_hub.snapshot_download", lambda *_args, **_kwargs: pytest.fail("must not download or prepare a snapshot"))

    args = _arguments("embedding_text:local")

    assert not _bindings(args)
    assert json.loads(_environment(args)[local_models.PROJECTION_ENV])["text"]["provider"] == "qwen"


def test_qwen_fingerprint_and_sandbox_share_existing_cache_resolution(
        tmp_path, local_installation, monkeypatch):
    from qwen_embedding import resolved_model_files

    checkout = tmp_path / "cache/snapshots/revision"
    _artifacts(checkout, "config.json", "model.safetensors", "tokenizer.json")
    monkeypatch.setattr(virt.tiers, "spec", lambda *_args: {"provider": "qwen"})
    monkeypatch.setattr("huggingface_hub.try_to_load_from_cache",
                        lambda *_args: str(checkout / "config.json"))

    sources = {source for source, _destination in _bindings(_arguments("embedding_text:local"))}

    assert sources == {str(path) for path in resolved_model_files()}


@pytest.mark.parametrize("factory", [virt.get_embedder, virt.get_local_embedder])
def test_factories_consume_projected_configuration_without_the_host_tiers(
        factory, monkeypatch):
    expected = str(local_models.PROJECTION_ROOT / "text")
    monkeypatch.setenv(local_models.PROJECTION_ENV, json.dumps({"text": {
        "provider": "bge", "model_dir": expected,
    }}))
    monkeypatch.setattr(virt.tiers, "spec", lambda *_args: pytest.fail("host configuration must not be read"))
    monkeypatch.setattr("bge_embedding.BGEEmbeddingService", lambda model_dir: model_dir)
    virt._cache.clear()
    try:
        assert factory("text") == expected
    finally:
        virt._cache.clear()


@pytest.mark.parametrize("raw", ["[]", "not json", '{"text":{"provider":"http","model_dir":"/"}}'])
def test_malformed_projection_fails_closed(raw, monkeypatch):
    monkeypatch.setenv(local_models.PROJECTION_ENV, raw)
    with pytest.raises(virt.EmbeddingUnavailableError, match="invalid local model projection"):
        local_models.projected_embedding_spec("text")


def test_local_factory_requires_a_declared_projected_role(monkeypatch):
    monkeypatch.setenv(local_models.PROJECTION_ENV, "{}")
    with pytest.raises(virt.EmbeddingUnavailableError, match="not declared"):
        local_models.local_embedding_spec("text")


def test_full_sandbox_includes_model_projection_without_network_authority(
        local_installation):
    _artifacts(local_installation / "models/embedding-bge", "tokenizer.json")

    args = sandbox._build_bwrap_args(Path(__file__), [
        {"name": "metnos:read", "hint": ["embedding_text:local"]},
    ])

    assert "--unshare-net" in args
    assert "--disable-userns" in args
    assert "--assert-userns-disabled" in args
    assert str(local_models.PROJECTION_ROOT / "text/tokenizer.json") in args


def test_real_sandbox_projects_models_read_only_and_hides_sibling_files(
        tmp_path, local_installation):
    if not sandbox.bwrap_available():
        pytest.skip("Bubblewrap is not installed")
    checkout = local_installation / "models/embedding-bge"
    _artifacts(checkout, "tokenizer.json", "onnx/sentence_transformers_int8.onnx",
               "private.env")
    projected = local_models.PROJECTION_ROOT / "text/tokenizer.json"
    code = tmp_path / "executor" / "probe.py"
    code.parent.mkdir()
    code.write_text("# Synthetic sandbox fixture.\n")
    script = (
        "import json, pathlib\n"
        "from virt.local_models import local_embedding_spec\n"
        f"assert not pathlib.Path({str(checkout)!r}).exists()\n"
        f"path = pathlib.Path({str(projected)!r})\n"
        "assert path.read_bytes() == b'synthetic local model artifact'\n"
        "assert not path.with_name('private.env').exists()\n"
        "assert local_embedding_spec('text')['model_dir'] == str(path.parent)\n"
        "try:\n"
        "    path.write_bytes(b'modified')\n"
        "except OSError:\n"
        "    pass\n"
        "else:\n"
        "    raise AssertionError('model artifact is writable')\n"
        "try:\n"
        "    path.with_name('new-file').touch()\n"
        "except OSError:\n"
        "    pass\n"
        "else:\n"
        "    raise AssertionError('model projection is writable')\n"
        "print(json.dumps({'visible': True, 'read_only': True, 'sibling_hidden': True}))\n"
    )
    executor = SimpleNamespace(code_path=code, capabilities=[
        {"name": "metnos:read", "hint": ["embedding_text:local"]},
    ])
    command = sandbox.wrap_command(executor, [sys.executable, "-c", script])
    process = subprocess.run(command, env={
        **os.environ, "PYTHONPATH": str(Path(sandbox.__file__).parent),
    }, capture_output=True, text=True, timeout=30)
    if process.returncode and "bwrap:" in process.stderr and any(
        value in process.stderr for value in ("Operation not permitted", "Permission denied")
    ):
        pytest.skip("host denied Bubblewrap namespace creation")
    assert process.returncode == 0, process.stderr
    assert json.loads(process.stdout) == {
        "visible": True, "read_only": True, "sibling_hidden": True,
    }
