from __future__ import annotations

import sys
from pathlib import Path

import pytest

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")

import loader


def _write_executor(root: Path, name: str, *, lifecycle: str = "active") -> Path:
    target = root / name
    target.mkdir(parents=True, exist_ok=True)
    (target / "manifest.toml").write_text(
        f'''manifest_format = "1.0"
name = "{name}"
version = "0.1.0"
lifecycle = "{lifecycle}"
affinity = []

[description]
it = "test"

[code]
files = ["x.py"]
digest = "sha256:test"

[args]
type = "object"
required = []
''', encoding="utf-8")
    (target / "x.py").write_text(
        'if __name__ == "__main__":\n    pass\n', encoding="utf-8")
    return target


def test_proposed_manifest_cannot_bind_unsigned_code(tmp_path):
    _write_executor(tmp_path, "draft_with_code", lifecycle="proposed")
    loader.invalidate_catalog_cache()
    catalog = loader.load_catalog(
        executors_dir=tmp_path, verify=False, include_synth=False,
        include_verb_unique=False)
    assert catalog.get("draft_with_code") is None
    assert any(reason == "proposed_executor_must_not_bind_code"
               for _path, reason in catalog.rejected)


def test_verify_env_is_ignored_without_explicit_nonproduction_profile(
        tmp_path, monkeypatch):
    _write_executor(tmp_path, "signed_only")
    monkeypatch.setenv("METNOS_LOADER_VERIFY", "0")
    monkeypatch.delenv("METNOS_RUNTIME_PROFILE", raising=False)
    monkeypatch.setattr(
        loader, "verify_executor",
        lambda _path: (False, {"reason": "signature_checked"}),
    )
    loader.invalidate_catalog_cache()
    catalog = loader.load_catalog(
        executors_dir=tmp_path, verify=True, include_synth=False,
        include_verb_unique=False)
    assert catalog.get("signed_only") is None
    assert any(reason == "signature_checked"
               for _path, reason in catalog.rejected)


def test_verify_env_remains_available_to_explicit_e2e_profile(
        tmp_path, monkeypatch):
    _write_executor(tmp_path, "e2e_unsigned")
    monkeypatch.setenv("METNOS_LOADER_VERIFY", "0")
    monkeypatch.setenv("METNOS_RUNTIME_PROFILE", "e2e")
    monkeypatch.setattr(
        loader, "verify_executor",
        lambda _path: (_ for _ in ()).throw(AssertionError("must not run")),
    )
    loader.invalidate_catalog_cache()
    catalog = loader.load_catalog(
        executors_dir=tmp_path, verify=True, include_synth=False,
        include_verb_unique=False)
    assert catalog.get("e2e_unsigned") is not None


def test_imported_skill_is_skipped_when_enable_registry_is_unavailable(
        tmp_path, monkeypatch):
    _write_executor(tmp_path / "skills" / "sample", "sample_exec")
    import skill_registry
    monkeypatch.setattr(
        skill_registry, "is_skill_enabled",
        lambda _name: (_ for _ in ()).throw(RuntimeError("db locked")),
    )
    loader.invalidate_catalog_cache()
    catalog = loader.load_catalog(
        executors_dir=tmp_path, verify=False, include_synth=False,
        include_verb_unique=False)
    assert catalog.get("sample_exec") is None
    assert any("skill_enable_gate_unavailable" in reason
               for _path, reason in catalog.rejected)


def test_stage6_missing_verifier_never_self_approves(monkeypatch):
    import skill_admission
    monkeypatch.delenv("METNOS_STAGE6_VERIFY_FAKE", raising=False)
    monkeypatch.setitem(sys.modules, "synt_stage6_verify", None)
    with pytest.raises(RuntimeError, match="^semantic_verifier_unavailable$"):
        skill_admission._stage6_verify_callable()("manifest", "code")
