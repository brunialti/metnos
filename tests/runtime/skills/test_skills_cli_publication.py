from __future__ import annotations

import contextlib
import shutil
from pathlib import Path
from types import SimpleNamespace

from cli import skills_cli


def test_import_admission_reports_legacy_signing(monkeypatch, tmp_path: Path) -> None:
    import sign

    monkeypatch.delenv("METNOS_SKILLS_NO_SIGN", raising=False)
    monkeypatch.setattr(
        sign,
        "publish_authoring_update",
        lambda _path: ("sha256:legacy", tmp_path / "manifest.toml.sig", None),
    )

    assert skills_cli._try_publish_authoring_update(tmp_path) == "signed"


def test_import_admission_reports_store_publication(monkeypatch, tmp_path: Path) -> None:
    import sign

    monkeypatch.delenv("METNOS_SKILLS_NO_SIGN", raising=False)
    monkeypatch.setattr(
        sign,
        "publish_authoring_update",
        lambda _path: (
            "sha256:store",
            tmp_path / "manifest.toml.sig",
            SimpleNamespace(current_generation_id="sha256:generation"),
        ),
    )

    assert skills_cli._try_publish_authoring_update(tmp_path) == "published"


def test_import_admission_keeps_failure_explicit(monkeypatch, tmp_path: Path) -> None:
    import sign

    monkeypatch.delenv("METNOS_SKILLS_NO_SIGN", raising=False)
    monkeypatch.setattr(
        sign,
        "publish_authoring_update",
        lambda _path: (_ for _ in ()).throw(RuntimeError("commit ambiguous")),
    )

    assert skills_cli._try_publish_authoring_update(tmp_path) == (
        "sign_failed: commit ambiguous"
    )


def test_import_reactivates_only_after_authenticated_retirement(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import contract_store
    import sign

    monkeypatch.delenv("METNOS_SKILLS_NO_SIGN", raising=False)
    monkeypatch.setattr(
        sign,
        "publish_authoring_update",
        lambda _path: (_ for _ in ()).throw(
            contract_store.ContractStoreError(
                "contract_retired", "sha256:" + "1" * 64,
            )
        ),
    )
    calls = []
    monkeypatch.setattr(
        sign,
        "reactivate_executor_contract",
        lambda path, **kwargs: calls.append((path, kwargs)),
    )

    assert skills_cli._try_publish_authoring_update(tmp_path) == "reactivated"
    assert calls == [(str(tmp_path), {
        "actor": "skills_cli",
        "reason": "reinstall imported executor contract",
    })]


def _skill_contracts(tmp_path: Path) -> Path:
    skill_dir = tmp_path / "imported" / "sample_skill"
    for name in ("first_executor", "second_executor"):
        directory = skill_dir / name
        directory.mkdir(parents=True)
        (directory / "manifest.toml").write_text(f'name = "{name}"\n')
    return skill_dir


def test_store_uninstall_retires_every_contract_before_removing_source(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import contract_store
    import manifest_inventory
    import sign

    skill_dir = _skill_contracts(tmp_path)
    monkeypatch.setattr(
        skills_cli, "_resolve_existing_skill_dir", lambda _name: skill_dir,
    )
    monkeypatch.setattr(skills_cli, "_skills_dir", lambda: tmp_path / "sources")
    monkeypatch.setattr(
        manifest_inventory,
        "resolve_manifest_layout",
        lambda: manifest_inventory.ManifestLayout.STORE_ONLY,
    )
    retired = []
    boundary_held = False

    @contextlib.contextmanager
    def boundary():
        nonlocal boundary_held
        assert not boundary_held
        boundary_held = True
        try:
            yield
        finally:
            boundary_held = False

    monkeypatch.setattr(contract_store, "catalog_admission_lock", boundary)

    real_rmtree = shutil.rmtree

    def remove_tree(path):
        assert boundary_held
        real_rmtree(path)

    monkeypatch.setattr(skills_cli.shutil, "rmtree", remove_tree)

    def retire(path, **kwargs):
        assert boundary_held
        retired.append((path, kwargs))

    monkeypatch.setattr(
        sign,
        "retire_executor_contract",
        retire,
    )

    result = skills_cli._cmd_uninstall(SimpleNamespace(
        skill="sample_skill", purge_source=False,
    ))

    assert result == 0
    assert [path.name for path, _kwargs in retired] == [
        "first_executor", "second_executor",
    ]
    assert all(
        kwargs == {
            "actor": "skills_cli",
            "reason": "uninstall imported skill=sample_skill",
        }
        for _path, kwargs in retired
    )
    assert not skill_dir.exists()
    assert boundary_held is False


def test_store_uninstall_keeps_all_sources_when_any_retirement_fails(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import manifest_inventory
    import sign

    skill_dir = _skill_contracts(tmp_path)
    monkeypatch.setattr(
        skills_cli, "_resolve_existing_skill_dir", lambda _name: skill_dir,
    )
    monkeypatch.setattr(
        manifest_inventory,
        "resolve_manifest_layout",
        lambda: manifest_inventory.ManifestLayout.STORE_ONLY,
    )
    attempts = []

    def retire(path, **_kwargs):
        attempts.append(path.name)
        if path.name == "second_executor":
            raise RuntimeError("registry unavailable after commit")

    monkeypatch.setattr(sign, "retire_executor_contract", retire)

    result = skills_cli._cmd_uninstall(SimpleNamespace(
        skill="sample_skill", purge_source=False,
    ))

    assert result == 2
    assert attempts == ["first_executor", "second_executor"]
    assert skill_dir.is_dir()
    assert all((skill_dir / name / "manifest.toml").is_file() for name in attempts)
