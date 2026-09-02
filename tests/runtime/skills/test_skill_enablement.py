from __future__ import annotations

import contextlib
import json
import os
import stat
import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import config as C
import contract_store
import loader
import manifest_inventory
import sign
import skill_admin
import skill_registry
from cli import skills_cli
from contract_store import ContractRetirement
from manifest_inventory import (
    ContractId,
    ManifestBootstrapError,
    ManifestInventory,
    ManifestOrigin,
    ManifestRef,
    ManifestStatus,
)


@pytest.fixture
def authoring_env(tmp_path: Path, monkeypatch):
    executors = tmp_path / "executors"
    runtime = tmp_path / "runtime"
    user_data = tmp_path / "data"
    user_executors = user_data / "executors"
    state = tmp_path / "state"
    paths = {
        "PATH_EXECUTORS": executors,
        "PATH_RUNTIME": runtime,
        "PATH_USER_DATA": user_data,
        "PATH_USER_STATE": state,
        "PATH_SYNTH_EXECUTORS": user_executors,
        "PATH_SKILLS_BUILTIN": executors / "skills",
        "PATH_SKILLS_USER": user_executors / "skills",
        "PATH_SKILLS_USER_LEGACY": user_executors / "_imports",
    }
    for name, value in paths.items():
        monkeypatch.setattr(C, name, value)
    monkeypatch.setattr(C, "DEFAULT_LANG", "it")
    return SimpleNamespace(**paths)


def _manifest(directory: Path, name: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "manifest.toml"
    path.write_text(
        f'name = "{name}"\nlifecycle = "active"\n',
        encoding="utf-8",
    )
    return path


def _skill(
    root: Path,
    skill_name: str,
    executor_name: str,
    *,
    lang: str = "any",
) -> None:
    skill = root / skill_name
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text(
        "---\n"
        f"name: {skill_name}\n"
        f"lang: {lang}\n"
        "auto_enable: false\n"
        "---\n",
        encoding="utf-8",
    )
    _manifest(skill / executor_name, executor_name)


def _state(path: Path) -> dict[str, bool]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_checked_enable_rejects_core_import_collision_without_state_change(
    authoring_env,
) -> None:
    _manifest(authoring_env.PATH_EXECUTORS / "core_exec", "shared_name")
    _skill(
        authoring_env.PATH_SKILLS_USER,
        "optional_bundle",
        "shared_name",
    )

    with pytest.raises(
        skill_registry.SkillEnablementError,
        match="contract_name_collision",
    ):
        skill_registry.set_skill_enabled_checked("optional_bundle", True)

    assert not skill_registry._state_file().exists()


def test_two_concurrent_enables_both_reject_an_installed_name_collision(
    authoring_env,
) -> None:
    _skill(authoring_env.PATH_SKILLS_USER, "bundle_a", "shared_name")
    _skill(authoring_env.PATH_SKILLS_USER, "bundle_b", "shared_name")
    barrier = threading.Barrier(2)
    successes: list[str] = []
    failures: list[tuple[str, str]] = []

    def enable(name: str) -> None:
        barrier.wait()
        try:
            skill_registry.set_skill_enabled_checked(name, True)
            successes.append(name)
        except skill_registry.SkillEnablementError as exc:
            failures.append((name, exc.code))

    threads = [
        threading.Thread(target=enable, args=(name,))
        for name in ("bundle_a", "bundle_b")
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert all(not thread.is_alive() for thread in threads)
    assert successes == []
    assert sorted(failures) == [
        ("bundle_a", "contract_name_collision"),
        ("bundle_b", "contract_name_collision"),
    ]
    assert not skill_registry._state_file().exists()


def test_valid_enable_changes_only_policy_and_locale_gate_stays_effective(
    authoring_env,
) -> None:
    _manifest(authoring_env.PATH_EXECUTORS / "core_exec", "shared_name")
    _skill(authoring_env.PATH_SKILLS_USER, "valid_bundle", "unique_name")
    _skill(
        authoring_env.PATH_SKILLS_USER,
        "other_locale",
        "locale_only",
        lang="en",
    )
    manifests_before = {
        path: path.read_bytes()
        for path in authoring_env.PATH_SKILLS_USER.rglob("manifest.toml")
    }

    assert skill_registry.set_skill_enabled_checked("valid_bundle", True) is True
    # Configured ON, but the locale predicate keeps the colliding EN-only
    # contract outside the IT candidate catalog.
    assert skill_registry.set_skill_enabled_checked("other_locale", True) is True

    assert _state(skill_registry._state_file()) == {
        "other_locale": True,
        "valid_bundle": True,
    }
    if os.name != "nt":
        assert stat.S_IMODE(skill_registry._state_file().stat().st_mode) == 0o600
    assert {
        path: path.read_bytes()
        for path in authoring_env.PATH_SKILLS_USER.rglob("manifest.toml")
    } == manifests_before


def test_checked_enable_rejects_link_state_without_touching_target(
    authoring_env,
) -> None:
    _skill(authoring_env.PATH_SKILLS_USER, "valid_bundle", "unique_name")
    state = skill_registry._state_file()
    state.parent.mkdir(parents=True, exist_ok=True)
    target = authoring_env.PATH_USER_DATA / "unrelated.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('{"untouched": true}\n', encoding="utf-8")
    try:
        state.symlink_to(target)
    except OSError as exc:  # pragma: no cover - depends on Windows privileges
        pytest.skip(f"symlink unavailable: {exc}")

    with pytest.raises(
        skill_registry.SkillEnablementError,
        match="skill_state_invalid",
    ):
        skill_registry.set_skill_enabled_checked("valid_bundle", True)

    assert target.read_text(encoding="utf-8") == '{"untouched": true}\n'


def test_missing_state_is_the_only_case_that_selects_defaults(
    authoring_env,
) -> None:
    assert skill_registry._load_state() == {}
    assert skill_registry.skill_state_cache_signature() == (
        "skill_state", "absent", 0, "",
    )

    state = skill_registry._state_file()
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text("not-json\n", encoding="utf-8")

    with pytest.raises(
        skill_registry.SkillEnablementError,
        match="^skill_state_invalid:",
    ):
        skill_registry._load_state()
    with pytest.raises(
        skill_registry.SkillEnablementError,
        match="^skill_state_invalid:",
    ):
        skill_registry.skill_state_cache_signature()
    with pytest.raises(
        skill_registry.SkillEnablementError,
        match="^skill_state_invalid:",
    ):
        skill_registry.list_skills()


@pytest.mark.skipif(
    not hasattr(os, "geteuid") or os.getuid() == 0,
    reason="requires distinct POSIX service and root identities",
)
def test_root_transition_reads_only_the_declared_service_owned_policy(
    authoring_env, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _skill(authoring_env.PATH_SKILLS_USER, "bundle", "unique_name")
    state = skill_registry._state_file()
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text('{"bundle": false}\n', encoding="utf-8")
    owner = (os.getuid(), os.getgid())
    monkeypatch.setattr(skill_registry.os, "geteuid", lambda: 0)

    assert skill_registry._is_skill_enabled_for_owner_v1(
        "bundle", owner,
    ) is False
    with pytest.raises(
        skill_registry.SkillEnablementError,
        match="foreign owner",
    ):
        skill_registry._is_skill_enabled_for_owner_v1(
            "bundle", (owner[0] + 1, owner[1]),
        )


def test_catalog_cache_signature_fails_closed_on_invalid_skill_state(
    authoring_env,
) -> None:
    state = skill_registry._state_file()
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text('{"bundle": false}\n', encoding="utf-8")
    valid = loader._catalog_cache_signature([])
    assert any(row[:2] == ("skill_state", "present") for row in valid)

    # An already populated cache cannot make a corrupt policy look absent.
    loader._CATALOG_CACHE["cached"] = (object(), valid)
    state.write_text("{", encoding="utf-8")
    with pytest.raises(
        skill_registry.SkillEnablementError,
        match="^skill_state_invalid:",
    ):
        loader._catalog_cache_signature([])


def test_catalog_cache_signature_rejects_redirected_skill_state(
    authoring_env,
) -> None:
    state = skill_registry._state_file()
    state.parent.mkdir(parents=True, exist_ok=True)
    target = authoring_env.PATH_USER_DATA / "redirected-state.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('{"bundle": true}\n', encoding="utf-8")
    try:
        state.symlink_to(target)
    except OSError as exc:  # pragma: no cover - Windows privilege dependent
        pytest.skip(f"symlink unavailable: {exc}")

    with pytest.raises(
        skill_registry.SkillEnablementError,
        match="^skill_state_invalid:",
    ):
        loader._catalog_cache_signature([])
    assert target.read_text(encoding="utf-8") == '{"bundle": true}\n'


def test_catalog_cache_signature_rejects_redirected_state_parent(
    authoring_env,
    monkeypatch,
) -> None:
    target_parent = authoring_env.PATH_USER_DATA / "redirected-state-parent"
    target_parent.mkdir(parents=True, exist_ok=True)
    (target_parent / "skill_enabled.json").write_text(
        '{"bundle": true}\n', encoding="utf-8",
    )
    redirected_parent = authoring_env.PATH_USER_DATA / "state-link"
    try:
        redirected_parent.symlink_to(target_parent, target_is_directory=True)
    except OSError as exc:  # pragma: no cover - Windows privilege dependent
        pytest.skip(f"symlink unavailable: {exc}")
    monkeypatch.setattr(C, "PATH_USER_STATE", redirected_parent)

    with pytest.raises(
        skill_registry.SkillEnablementError,
        match="^skill_state_parent_invalid:",
    ):
        loader._catalog_cache_signature([])
    assert (target_parent / "skill_enabled.json").read_text(
        encoding="utf-8",
    ) == '{"bundle": true}\n'


def test_catalog_cache_signature_rejects_unreadable_skill_state(
    authoring_env,
    monkeypatch,
) -> None:
    state = skill_registry._state_file()
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text('{"bundle": false}\n', encoding="utf-8")
    real_open = skill_registry.os.open

    def sharing_denied(path, flags, *args):
        if Path(path) == state:
            error = PermissionError("simulated sharing violation")
            error.winerror = 32
            raise error
        return real_open(path, flags, *args)

    monkeypatch.setattr(skill_registry.os, "open", sharing_denied)
    with pytest.raises(
        skill_registry.SkillEnablementError,
        match="^skill_state_invalid:",
    ):
        loader._catalog_cache_signature([])


def test_checked_enable_rejects_link_lock_without_touching_target(
    authoring_env,
) -> None:
    _skill(authoring_env.PATH_SKILLS_USER, "valid_bundle", "unique_name")
    lock = skill_registry._state_file().with_name("skill_enabled.json.lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    target = authoring_env.PATH_USER_DATA / "unrelated.lock"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"untouched")
    try:
        lock.symlink_to(target)
    except OSError as exc:  # pragma: no cover - depends on Windows privileges
        pytest.skip(f"symlink unavailable: {exc}")

    with pytest.raises(
        skill_registry.SkillEnablementError,
        match="skill_state_lock_invalid",
    ):
        skill_registry.set_skill_enabled_checked("valid_bundle", True)

    assert target.read_bytes() == b"untouched"


def _ref(
    root: Path,
    origin: ManifestOrigin,
    relative: str,
    *,
    skill_name: str | None = None,
    status: ManifestStatus = ManifestStatus.ADMITTED,
) -> ManifestRef:
    return ManifestRef(
        contract_id=ContractId(origin, relative),
        origin=origin,
        status=status,
        source_root=root,
        manifest_path=root / relative,
        manifest_relative=relative,
        allowed_code_roots=(root,),
        skill_name=skill_name,
    )


def _store_inventory_provider(refs):
    def provider(_sources, *, skill_enabled, **_kwargs):
        candidate = []
        for ref in refs:
            status = ref.status
            if ref.skill_name is not None:
                status = (
                    ManifestStatus.ADMITTED
                    if skill_enabled(ref.skill_name)
                    else ManifestStatus.DISABLED
                )
            candidate.append(replace(ref, status=status))
        return ManifestInventory(tuple(candidate), ())

    return provider


def test_authoring_second_observation_rechecks_name_uniqueness(
    tmp_path: Path,
    monkeypatch,
) -> None:
    first_ref = replace(
        _ref(tmp_path, ManifestOrigin.CORE, "first/manifest.toml"),
        name="first",
    )
    second_ref = replace(
        _ref(tmp_path, ManifestOrigin.CORE, "second/manifest.toml"),
        name="second",
    )
    observations = iter((
        ManifestInventory((first_ref, second_ref), ()),
        ManifestInventory((first_ref, replace(second_ref, name="first")), ()),
    ))
    monkeypatch.setattr(manifest_inventory, "default_manifest_sources", lambda: ())
    monkeypatch.setattr(
        manifest_inventory,
        "inventory_authoring_manifests",
        lambda *_args, **_kwargs: next(observations),
    )

    with pytest.raises(
        skill_registry.SkillEnablementError,
        match="contract_name_collision",
    ):
        skill_registry._authoring_candidate_preflight(lambda _name: True)


def test_store_preflight_authenticates_names_and_ignores_signed_tombstone(
    tmp_path: Path,
    monkeypatch,
) -> None:
    core = _ref(tmp_path, ManifestOrigin.CORE, "core/manifest.toml")
    optional = _ref(
        tmp_path,
        ManifestOrigin.USER_SKILL,
        "bundle/shared/manifest.toml",
        skill_name="bundle",
        status=ManifestStatus.DISABLED,
    )
    monkeypatch.setattr(manifest_inventory, "default_manifest_sources", lambda: ())
    monkeypatch.setattr(
        manifest_inventory,
        "inventory_store_manifests",
        _store_inventory_provider((core, optional)),
    )
    monkeypatch.setattr(sign, "list_trusted_publics", lambda: [("key", object())])
    revisions = {
        str(core.contract_id): SimpleNamespace(
            generation_id="a" * 64,
            parsed={"name": "shared"},
        ),
        str(optional.contract_id): ContractRetirement(
            contract_id=optional.contract_id,
            retirement_id="b" * 64,
            previous_generation_id="c" * 64,
            actor="test",
            reason="test",
            payload_bytes=b"{}",
            signature_bytes=b"sig",
            signature_hash="sha256:test",
            signed_by="key",
        ),
    }
    calls: list[str] = []

    def current(ref, *, trusted_publics):
        calls.append(str(ref.contract_id))
        return revisions[str(ref.contract_id)]

    monkeypatch.setattr(contract_store, "current_contract", current)
    monkeypatch.setattr(
        contract_store,
        "current_revision_id",
        lambda ref: skill_registry._revision_identity(
            revisions[str(ref.contract_id)]
        ),
    )

    # The disabled binding is still installed and therefore authenticated,
    # while its name is not candidate-visible.
    skill_registry._store_candidate_preflight(lambda _name: False)

    assert calls == [str(core.contract_id), str(optional.contract_id)]


def test_store_loader_rejects_published_collision_as_global_error(
    tmp_path: Path,
    monkeypatch,
) -> None:
    first = _ref(tmp_path, ManifestOrigin.CORE, "first/manifest.toml")
    second = _ref(tmp_path, ManifestOrigin.USER_SKILL, "s/second/manifest.toml")
    inventory = ManifestInventory((first, second), ())
    snapshots = {
        str(first.contract_id): SimpleNamespace(
            generation_id="a" * 64,
            parsed={"name": "duplicate"},
            source_manifest_dir=tmp_path,
            signed_by="key",
        ),
        str(second.contract_id): SimpleNamespace(
            generation_id="b" * 64,
            parsed={"name": "duplicate"},
            source_manifest_dir=tmp_path,
            signed_by="key",
        ),
    }
    monkeypatch.setattr(
        contract_store,
        "current_manifest",
        lambda ref, *, trusted_publics: snapshots[str(ref.contract_id)],
    )

    catalog = loader.Catalog()
    with pytest.raises(ManifestBootstrapError) as caught:
        loader._load_store_into_catalog(
            catalog,
            # Filtering a class of executors is a consumer view, not a waiver
            # of the global published-name invariant.
            include_synth=False,
            current_lang="it",
            inventory=inventory,
            trusted_publics=(("key", object()),),
            expected_revision_ids={
                str(first.contract_id): "a" * 64,
                str(second.contract_id): "b" * 64,
            },
        )

    assert caught.value.code == "published_name_collision"
    assert catalog.executors == {}


def test_cli_and_chat_admin_use_the_checked_operation(monkeypatch) -> None:
    calls: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        skill_registry,
        "set_skill_enabled_checked",
        lambda name, enabled: calls.append((name, enabled)),
    )

    assert skills_cli._cmd_enable(SimpleNamespace(skill="bundle")) == 0
    assert skill_admin.handle_set_skills({
        "name": "bundle", "enabled": False,
    })["ok"] is True
    assert calls == [("bundle", True), ("bundle", False)]


@pytest.mark.parametrize(
    "enabled",
    [
        "true", "false", "yes", "no", "attiva", "disabilita",
        "verdadero", 1, 0, None,
    ],
)
def test_chat_admin_rejects_non_boolean_enabled_without_state_change(
    monkeypatch, enabled,
) -> None:
    calls: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        skill_registry,
        "set_skill_enabled_checked",
        lambda name, value: calls.append((name, value)),
    )

    result = skill_admin.handle_set_skills({
        "name": "bundle", "enabled": enabled,
    })

    assert result == {
        "ok": False,
        "error": "param 'enabled' must be a JSON boolean",
    }
    assert calls == []


def test_enablement_holds_global_catalog_lock_through_state_commit(
    monkeypatch,
) -> None:
    events: list[str] = []

    @contextlib.contextmanager
    def catalog_lock(**_kwargs):
        events.append("catalog-enter")
        try:
            yield
        finally:
            events.append("catalog-exit")

    @contextlib.contextmanager
    def state_lock(**_kwargs):
        events.append("state-enter")
        try:
            yield
        finally:
            events.append("state-exit")

    monkeypatch.setattr(contract_store, "catalog_admission_lock", catalog_lock)
    monkeypatch.setattr(skill_registry, "_state_writer_lock", state_lock)
    monkeypatch.setattr(skill_registry, "_load_state", lambda **_kwargs: {})
    monkeypatch.setattr(
        skill_registry,
        "_skill_definitions",
        lambda: {"bundle": SimpleNamespace(auto_enable=False, lang="any")},
    )
    monkeypatch.setattr(
        skill_registry,
        "_candidate_preflight",
        lambda _policy: events.append("preflight"),
    )
    monkeypatch.setattr(
        skill_registry,
        "_save_state",
        lambda _state: events.append("commit"),
    )

    assert skill_registry.set_skill_enabled_checked("bundle", True) is True
    assert events == [
        "catalog-enter",
        "state-enter",
        "preflight",
        "commit",
        "state-exit",
        "catalog-exit",
    ]
