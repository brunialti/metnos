from pathlib import Path
from types import SimpleNamespace

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from admitted_module_v1 import code_digest_of_bytes_v1
import reverse_patterns_patch as patch


_PRIVATE = Ed25519PrivateKey.generate()


class _Catalog:
    def __init__(self, executor=None):
        self.executor = executor

    def get(self, name):
        return self.executor if name == "delete_events" else None


def _call():
    return {
        "executor": "delete_events",
        "args": {"event_ids": ["a", "b"]},
    }


def _published(tmp_path: Path):
    directory = tmp_path / "delete_events"
    directory.mkdir(parents=True)
    payload = (
        b"def invoke(args):\n"
        b" return {'ok': True, 'n_deleted': 2, 'results': "
        b"[{'status': 'deleted'}, {'ok': True}]}\n"
    )
    code = directory / "delete_events.py"
    code.write_bytes(payload)
    manifest = directory / "manifest.toml"
    digest = code_digest_of_bytes_v1([payload])
    manifest_bytes = (
        b'name = "delete_events"\n\n[code]\nfiles = ["delete_events.py"]\n'
        + f'digest = "{digest}"\n'.encode("utf-8")
    )
    manifest.write_bytes(manifest_bytes)
    manifest.with_name("manifest.toml.sig").write_bytes(
        _PRIVATE.sign(manifest_bytes),
    )
    return SimpleNamespace(
        name="delete_events",
        manifest_path=manifest,
        code_path=code,
        digest=digest,
        code_files=(code.name,),
    )


def _fresh_catalog(monkeypatch, executor):
    monkeypatch.setattr(
        "admitted_module_v1._invalidate_catalog_cache_at_start_v1", lambda: None,
    )
    monkeypatch.setattr(
        "admitted_module_v1._load_catalog_at_start_v1",
        lambda: _Catalog(executor),
    )
    monkeypatch.setattr(
        "admitted_module_v1._trusted_public_keys_v1",
        lambda: (_PRIVATE.public_key(),),
    )


def test_dispatch_crosses_the_real_authenticated_door(
        tmp_path: Path, monkeypatch):
    executor = _published(tmp_path)
    _fresh_catalog(monkeypatch, executor)

    assert patch._dispatch_call(_call(), _Catalog(executor)) == (2, 0)


def test_dispatch_fails_closed_without_the_callers_verified_catalog(
        tmp_path: Path, monkeypatch):
    executor = _published(tmp_path)
    _fresh_catalog(monkeypatch, executor)

    assert patch._dispatch_call(_call(), None) == (0, 2)
    assert patch._dispatch_call(_call(), _Catalog()) == (0, 2)


def test_dispatch_fails_closed_if_catalog_snapshot_changed(
        tmp_path: Path, monkeypatch):
    selected = _published(tmp_path / "selected")
    current = _published(tmp_path / "current")
    _fresh_catalog(monkeypatch, current)

    assert patch._dispatch_call(_call(), _Catalog(selected)) == (0, 2)
