from __future__ import annotations

import os
import errno
from pathlib import Path

import pytest

import manifest_inventory as inventory_module
from manifest_inventory import (
    ContractId,
    ManifestBootstrapError,
    ManifestLayout,
    ManifestOrigin,
    ManifestSource,
    ManifestStatus,
    default_manifest_sources,
    inventory_authoring_manifests,
    inventory_manifests,
    manifest_ref_for_source_path,
    resolve_manifest_layout,
)
from contract_store import encode_binding


@pytest.mark.parametrize("failure", ["io", "corrupt", "skill"])
def test_store_failure_keeps_closed_diagnostics_through_loader(tmp_path, monkeypatch, failure):
    import contract_store
    import loader
    from skill_registry import SkillEnablementError

    secret = "private-path-secret-never-publish"
    contract_id = ContractId(ManifestOrigin.USER_SKILL, "sample/read_files/manifest.toml")
    store_root = tmp_path / "store"
    directory = store_root / contract_id.storage_key
    directory.mkdir(parents=True)
    binding = directory / "binding.json"
    binding.write_bytes(encode_binding(contract_id))
    source = ManifestSource(
        ManifestOrigin.USER_SKILL, tmp_path / "authoring",
        min_depth=2, max_depth=2, skill_scoped=True,
    )
    enabled = lambda _name: True
    if failure == "io":
        def read(*_args, **_kwargs):
            try:
                raise OSError(errno.EMFILE, secret)
            except OSError as cause:
                raise contract_store.ContractStoreError("binding_invalid", secret) from cause
        monkeypatch.setattr(contract_store, "_read_regular_file", read)
        expected = ("binding_invalid", "binding_invalid", errno.EMFILE)
    elif failure == "corrupt":
        binding.write_bytes(b"not-json")
        expected = ("binding_invalid", "binding_invalid", None)
    else:
        def enabled(_name):
            try:
                raise PermissionError(errno.EACCES, secret)
            except PermissionError as cause:
                raise SkillEnablementError("skill_state_invalid", secret) from cause
        expected = ("skill_status_error", "skill_state_invalid", errno.EACCES)
    inventory = inventory_module.inventory_store_manifests(
        (source,), store_root=store_root, skill_enabled=enabled,
    )
    assert len(inventory.problems) == 1
    with pytest.raises(ManifestBootstrapError) as raised:
        loader._load_store_into_catalog(
            loader.Catalog(), include_synth=True, current_lang="en", inventory=inventory,
        )
    assert raised.value.code == "store_inventory_invalid"
    assert raised.value.inventory_diagnostics == (expected,)
    assert secret not in repr(raised.value.inventory_diagnostics)


def _manifest(directory: Path, name: str, *, lifecycle: str = "active") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "manifest.toml"
    path.write_text(
        f'name="{name}"\nlifecycle="{lifecycle}"\n',
        encoding="utf-8",
    )
    return path


def test_inventory_classifies_every_topology_without_granting_authority(
    tmp_path: Path,
) -> None:
    core = tmp_path / "core"
    builtin = tmp_path / "builtin"
    builtin_skills = tmp_path / "builtin-skills"
    user = tmp_path / "user"
    user_skills = tmp_path / "user-skills"
    legacy = tmp_path / "legacy"
    retired = tmp_path / "retired"
    imported = _manifest(user_skills / "calendar" / "read_events", "read_events")
    before = imported.read_bytes()
    _manifest(core / "find_files", "find_files")
    _manifest(builtin / "list_tasks", "list_tasks")
    _manifest(builtin_skills / "media" / "read_media", "read_media")
    _manifest(user / "custom_task", "custom_task")
    _manifest(legacy / "mail" / "read_mail", "read_mail")
    _manifest(retired / "reply_messages", "reply_messages")

    inventory = inventory_manifests((
        ManifestSource(ManifestOrigin.CORE, core),
        ManifestSource(ManifestOrigin.BUILTIN, builtin),
        ManifestSource(
            ManifestOrigin.BUILTIN_SKILL, builtin_skills,
            min_depth=2, max_depth=2, skill_scoped=True,
            allowed_code_roots=(builtin_skills,),
        ),
        ManifestSource(ManifestOrigin.USER, user),
        ManifestSource(
            ManifestOrigin.USER_SKILL, user_skills,
            min_depth=2, max_depth=2, skill_scoped=True,
            allowed_code_roots=(user_skills,),
        ),
        ManifestSource(
            ManifestOrigin.LEGACY_IMPORT, legacy,
            min_depth=2, max_depth=2, skill_scoped=True,
            allowed_code_roots=(legacy,),
        ),
        ManifestSource(
            ManifestOrigin.RETIRED, retired,
            default_status=ManifestStatus.RETIRED,
        ),
    ), skill_enabled=lambda name: name != "calendar")

    view = {(item.origin, item.name): item.status for item in inventory.manifests}
    assert view == {
        (ManifestOrigin.CORE, "find_files"): ManifestStatus.ADMITTED,
        (ManifestOrigin.BUILTIN, "list_tasks"): ManifestStatus.ADMITTED,
        (ManifestOrigin.BUILTIN_SKILL, "read_media"): ManifestStatus.ADMITTED,
        (ManifestOrigin.USER, "custom_task"): ManifestStatus.ADMITTED,
        (ManifestOrigin.USER_SKILL, "read_events"): ManifestStatus.DISABLED,
        (ManifestOrigin.LEGACY_IMPORT, "read_mail"): ManifestStatus.ADMITTED,
        (ManifestOrigin.RETIRED, "reply_messages"): ManifestStatus.RETIRED,
    }
    assert imported.read_bytes() == before
    skill_roots = {
        item.name: item.allowed_code_roots
        for item in inventory.manifests if item.skill_name is not None
    }
    assert skill_roots == {
        "read_media": (builtin_skills.resolve() / "media",),
        "read_events": (user_skills.resolve() / "calendar",),
        "read_mail": (legacy.resolve() / "mail",),
    }


def test_inventory_reports_symlinks_aliases_collisions_and_parse_errors(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    original = _manifest(first / "a", "same_name")
    _manifest(second / "b", "same_name")
    alias_dir = first / "alias"
    alias_dir.mkdir()
    os.link(original, alias_dir / "manifest.toml")
    link_dir = first / "linked"
    link_dir.mkdir()
    (link_dir / "manifest.toml").symlink_to(original)
    broken = first / "broken"
    broken.mkdir()
    (broken / "manifest.toml").write_text("not = [valid", encoding="utf-8")

    inventory = inventory_manifests((
        ManifestSource(ManifestOrigin.CORE, first),
        ManifestSource(ManifestOrigin.BUILTIN, second),
    ))

    codes = {problem.code for problem in inventory.problems}
    assert {"alias", "symlink", "parse_error", "name_collision"} <= codes
    assert [item.name for item in inventory.manifests] == ["same_name", "same_name"]


def test_inventory_order_and_contract_storage_keys_are_portable(tmp_path: Path) -> None:
    root = tmp_path / "root"
    _manifest(root / "zeta", "zeta")
    _manifest(root / "alpha", "alpha")

    first = inventory_manifests((ManifestSource(ManifestOrigin.CORE, root),))
    second = inventory_manifests((ManifestSource(ManifestOrigin.CORE, root),))

    assert [item.name for item in first.manifests] == ["alpha", "zeta"]
    assert first == second
    for item in first.manifests:
        key = item.contract_id.storage_key
        assert len(key) == 64
        assert key.isascii() and key.isalnum()
        assert ":" not in key


def test_installed_census_includes_disabled_and_excludes_retired(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    sources = (
        ManifestSource(ManifestOrigin.CORE, root / "core"),
        ManifestSource(
            ManifestOrigin.USER_SKILL,
            root / "skills",
            min_depth=2,
            max_depth=2,
            skill_scoped=True,
        ),
        ManifestSource(
            ManifestOrigin.RETIRED,
            root / "retired",
            default_status=ManifestStatus.RETIRED,
        ),
    )
    _manifest(root / "core" / "active", "active")
    _manifest(root / "skills" / "disabled-skill" / "disabled", "disabled")
    _manifest(root / "retired" / "old", "old")

    inventory = inventory_authoring_manifests(
        sources,
        skill_enabled=lambda _name: False,
    )

    assert [ref.name for ref in inventory.installed()] == ["active", "disabled"]
    assert [ref.name for ref in inventory.admitted()] == ["active"]


@pytest.mark.parametrize("relative", (
    "../manifest.toml",
    "/alpha/manifest.toml",
    "alpha\\manifest.toml",
    "alpha//manifest.toml",
))
def test_contract_id_rejects_noncanonical_paths(relative: str) -> None:
    try:
        ContractId(ManifestOrigin.CORE, relative)
    except ValueError as exc:
        assert "canonical" in str(exc)
    else:  # pragma: no cover - assertion made explicit for readable failures
        raise AssertionError("noncanonical contract id accepted")


def test_explicit_empty_sources_never_fall_back_to_real_installation(
    monkeypatch,
) -> None:
    def forbidden_defaults():
        raise AssertionError("default roots must not be opened")

    monkeypatch.setattr(inventory_module, "default_manifest_sources", forbidden_defaults)

    assert inventory_manifests(()).manifests == ()


def test_default_code_roots_are_narrow_and_cover_shared_runtime_code() -> None:
    sources = {source.origin: source for source in default_manifest_sources()}

    assert sources[ManifestOrigin.CORE].allowed_code_roots == (
        inventory_module._C.PATH_EXECUTORS,
        inventory_module._C.PATH_RUNTIME,
    )
    assert inventory_module._C.PATH_ROOT not in (
        sources[ManifestOrigin.CORE].allowed_code_roots
    )
    assert sources[ManifestOrigin.BUILTIN].allowed_code_roots == (
        inventory_module._C.PATH_RUNTIME,
    )


def test_store_inventory_is_structural_and_never_opens_authoring(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "authoring"
    manifest_path = source_root / "sample" / "manifest.toml"
    manifest_path.mkdir(parents=True)  # unreadable-as-a-file sentinel
    source = ManifestSource(
        ManifestOrigin.CORE,
        source_root,
        allowed_code_roots=(source_root,),
    )
    contract_id = ContractId(ManifestOrigin.CORE, "sample/manifest.toml")
    store_root = tmp_path / "state" / "contract-publications" / "v1"
    contract_dir = store_root / contract_id.storage_key
    contract_dir.mkdir(parents=True)
    (contract_dir / "binding.json").write_bytes(encode_binding(contract_id))

    inventory = inventory_manifests(
        (source,),
        store_root=store_root,
        active_marker=tmp_path / "state" / "contract-publications.ACTIVE",
    )

    assert not inventory.problems
    assert len(inventory.manifests) == 1
    ref = inventory.manifests[0]
    assert ref.manifest_path == manifest_path
    assert ref.manifest_hash is None
    assert ref.name is None
    assert ref.lifecycle is None
    assert manifest_ref_for_source_path(inventory, manifest_path) == ref


def test_explicit_authoring_inventory_remains_available_after_store_cutover(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "authoring"
    manifest_path = _manifest(source_root / "sample", "sample")
    store_root = tmp_path / "state" / "contract-publications" / "v1"
    store_root.mkdir(parents=True)

    inventory = inventory_authoring_manifests((
        ManifestSource(ManifestOrigin.CORE, source_root),
    ))

    assert not inventory.problems
    assert inventory.manifests[0].manifest_path == manifest_path
    assert inventory.manifests[0].name == "sample"


def test_marker_or_production_root_forbids_legacy_fallback(tmp_path: Path) -> None:
    state = tmp_path / "state"
    store_root = state / "contract-publications" / "v1"
    marker = state / "contract-publications.ACTIVE"

    assert resolve_manifest_layout(
        store_root=store_root, active_marker=marker,
    ) is ManifestLayout.AUTHORING

    marker.parent.mkdir(parents=True)
    marker.write_bytes(b"v1\n")
    with pytest.raises(ManifestBootstrapError, match="store_root_missing"):
        resolve_manifest_layout(store_root=store_root, active_marker=marker)

    marker.unlink()
    store_root.parent.mkdir(parents=True)
    with pytest.raises(ManifestBootstrapError, match="store_version_missing"):
        resolve_manifest_layout(store_root=store_root, active_marker=marker)

    store_root.mkdir()
    marker.write_bytes(b"v1\n")
    with pytest.raises(
        ManifestBootstrapError, match="production_store_incomplete",
    ):
        resolve_manifest_layout(store_root=store_root, active_marker=marker)

    (store_root / ("a" * 64)).mkdir()
    assert resolve_manifest_layout(
        store_root=store_root, active_marker=marker,
    ) is ManifestLayout.STORE_ONLY


@pytest.mark.parametrize("marker_present", (False, True))
@pytest.mark.parametrize(
    "incomplete_layout",
    ("empty_v1", "container_debris", "v1_debris"),
)
def test_incomplete_store_never_resolves_as_store_only(
    tmp_path: Path,
    marker_present: bool,
    incomplete_layout: str,
) -> None:
    state = tmp_path / "state"
    container = state / "contract-publications"
    store_root = container / "v1"
    marker = state / "contract-publications.ACTIVE"
    store_root.mkdir(parents=True)
    if incomplete_layout == "container_debris":
        (store_root / ("a" * 64)).mkdir()
        (container / "unexpected").write_bytes(b"debris")
    elif incomplete_layout == "v1_debris":
        (store_root / "not-a-contract").write_bytes(b"debris")
    if marker_present:
        marker.write_bytes(b"v1\n")

    with pytest.raises(ManifestBootstrapError) as caught:
        resolve_manifest_layout(store_root=store_root, active_marker=marker)

    assert caught.value.code == "production_store_incomplete"


def test_shadow_root_does_not_activate_store_mode(tmp_path: Path) -> None:
    state = tmp_path / "state"
    (state / "contract-publications-shadow" / "nonce" / "v1").mkdir(
        parents=True,
    )

    assert resolve_manifest_layout(
        store_root=state / "contract-publications" / "v1",
        active_marker=state / "contract-publications.ACTIVE",
    ) is ManifestLayout.AUTHORING


def test_malformed_marker_fails_closed_even_with_a_complete_version_root(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    store_root = state / "contract-publications" / "v1"
    store_root.mkdir(parents=True)
    marker = state / "contract-publications.ACTIVE"
    marker.write_bytes(b"v2\n")

    with pytest.raises(ManifestBootstrapError, match="active_marker_invalid"):
        resolve_manifest_layout(store_root=store_root, active_marker=marker)


def test_link_like_root_counts_as_present_and_never_reactivates_authoring(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    state.mkdir()
    outside = tmp_path / "outside"
    (outside / "v1").mkdir(parents=True)
    production = state / "contract-publications"
    production.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ManifestBootstrapError, match="store_root_invalid"):
        resolve_manifest_layout(
            store_root=production / "v1",
            active_marker=state / "contract-publications.ACTIVE",
        )
