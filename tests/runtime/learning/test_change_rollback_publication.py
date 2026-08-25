from __future__ import annotations

import contextlib
import shutil
from pathlib import Path
from types import SimpleNamespace

import change_rollback


def _create_intent(tmp_path: Path, monkeypatch):
    synth_root = tmp_path / "synth"
    executor_dir = synth_root / "sample_executor"
    executor_dir.mkdir(parents=True)
    (executor_dir / "manifest.toml").write_text(
        'name = "sample_executor"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(change_rollback.C, "PATH_SYNTH_EXECUTORS", synth_root)
    monkeypatch.setattr(change_rollback.C, "PATH_USER_DATA", tmp_path / "data")
    monkeypatch.setattr(change_rollback.C, "PATH_USER_STATE", tmp_path / "state")
    return SimpleNamespace(
        id="change-123",
        intent_target="sample_executor",
        applied_effect={"executor_name": "sample_executor"},
    ), executor_dir


def test_create_rollback_retires_store_contract_before_archiving_source(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import contract_store
    import manifest_inventory
    import sign

    intent, executor_dir = _create_intent(tmp_path, monkeypatch)
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

    def retire(path, **kwargs):
        assert boundary_held
        assert path.is_dir()
        retired.append((path, kwargs))

    monkeypatch.setattr(sign, "retire_executor_contract", retire)
    real_move = shutil.move

    def archive(source, destination):
        assert boundary_held
        return real_move(source, destination)

    monkeypatch.setattr(change_rollback.shutil, "move", archive)

    result = change_rollback._rollback_create_executor(intent)

    assert retired == [(executor_dir, {
        "actor": "change_rollback",
        "reason": "rollback create_executor change_intent=change-123",
    })]
    assert not executor_dir.exists()
    assert Path(result["archived_to"]).is_dir()
    assert boundary_held is False


def test_create_rollback_keeps_source_when_retirement_needs_retry(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import manifest_inventory
    import sign

    intent, executor_dir = _create_intent(tmp_path, monkeypatch)
    monkeypatch.setattr(
        manifest_inventory,
        "resolve_manifest_layout",
        lambda: manifest_inventory.ManifestLayout.STORE_ONLY,
    )
    monkeypatch.setattr(
        sign,
        "retire_executor_contract",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("registry unavailable after pointer commit"),
        ),
    )

    result = change_rollback._rollback_create_executor(intent)

    assert "requires retry" in result["error"]
    assert executor_dir.is_dir()
    assert not (tmp_path / "data" / "executors_archive").exists()
