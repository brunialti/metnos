"""Portable contract tests for the pure RM-0008 host-layout domain."""
from __future__ import annotations

import ast
import os
from pathlib import Path, PurePosixPath
import runpy

import pytest

import executor_birth_account_identity as identity
import executor_birth_host_layout as layout
import executor_birth_host_path_policy as path_policy


def _snapshot(
    *, supplementary_gids: tuple[int, ...] | None = None, **changes,
) -> identity.PosixAccountSnapshotV1:
    values = {
        "name": "metnos",
        "uid": 991,
        "gid": 992,
        "home": "/var/lib/metnos-service",
        "shell": "/usr/sbin/nologin",
    }
    values.update(changes)
    record = identity.PosixAccountRecordV1(**values)
    groups = (record.gid,) if supplementary_gids is None else supplementary_gids
    return identity.PosixAccountSnapshotV1(record, groups)


def _conforming_observation(
    spec: layout.HostLayoutSpecV1,
) -> layout.HostLayoutObservationV1:
    objects = tuple(
        layout.HostPathObservationV1(
            item.path,
            layout.HostNodeKindV1.directory,
            item.ownership.uid,
            item.ownership.gid,
            item.mode,
            False,
            False,
        )
        for item in spec.objects
    )
    return layout.HostLayoutObservationV1(spec.account, objects)


def _missing_observation(
    spec: layout.HostLayoutSpecV1,
) -> layout.HostLayoutObservationV1:
    objects = tuple(
        layout.HostPathObservationV1(item.path, layout.HostNodeKindV1.missing)
        for item in spec.objects
    )
    return layout.HostLayoutObservationV1(spec.account, objects)


def _attributes(kind, uid: int, gid: int, mode: int) -> tuple[object, ...]:
    return kind, uid, gid, mode, layout.PosixAclPolicyV1.absent


def test_account_policy_is_system_metnos_with_canonical_home_and_shell() -> None:
    spec = layout.build_host_layout_spec_v1(_snapshot())
    assert spec.account_policy == layout.HostAccountPolicyV1(
        layout.HostAccountKindV1.system,
        "metnos",
        "metnos",
        (),
        PurePosixPath("/var/lib/metnos-service"),
        PurePosixPath("/usr/sbin/nologin"),
    )


def test_canonical_tree_has_signed_paths_owners_modes_and_no_acls() -> None:
    spec = layout.build_host_layout_spec_v1(_snapshot())
    actual = {
        item.role: (
            item.path.as_posix(), item.ownership.kind,
            item.ownership.uid, item.ownership.gid, item.mode, item.posix_acl,
        )
        for item in spec.objects
    }
    root = _attributes(layout.HostOwnerKindV1.root, 0, 0, 0o755)
    bootstrap = _attributes(layout.HostOwnerKindV1.root, 0, 0, 0o700)
    service = _attributes(layout.HostOwnerKindV1.service, 991, 992, 0o700)
    expected_paths = {
        layout.HostPathRoleV1.ownership_parent: "/var/lib/metnos",
        layout.HostPathRoleV1.bootstrap_root: "/var/lib/metnos-host-provisioning-v1",
        layout.HostPathRoleV1.legacy_state_journal: (
            "/var/lib/metnos/executor-birth/legacy-state-adoption-v1"
        ),
        layout.HostPathRoleV1.service_home: "/var/lib/metnos-service",
        layout.HostPathRoleV1.ownership_root: "/var/lib/metnos/executor-birth",
        layout.HostPathRoleV1.preflight_attestations: (
            "/var/lib/metnos/executor-birth/preflight-attestations-v1"
        ),
        layout.HostPathRoleV1.cache_parent: "/var/lib/metnos-service/.cache",
        layout.HostPathRoleV1.config_parent: "/var/lib/metnos-service/.config",
        layout.HostPathRoleV1.local_parent: "/var/lib/metnos-service/.local",
        layout.HostPathRoleV1.share_parent: "/var/lib/metnos-service/.local/share",
        layout.HostPathRoleV1.state_parent: "/var/lib/metnos-service/.local/state",
        layout.HostPathRoleV1.cache: "/var/lib/metnos-service/.cache/metnos",
        layout.HostPathRoleV1.config: "/var/lib/metnos-service/.config/metnos",
        layout.HostPathRoleV1.data: "/var/lib/metnos-service/.local/share/metnos",
        layout.HostPathRoleV1.state: "/var/lib/metnos-service/.local/state/metnos",
        layout.HostPathRoleV1.workspace: "/var/lib/metnos-service/.local/share/metnos/workspace",
    }
    service_roles = {
        layout.HostPathRoleV1.cache, layout.HostPathRoleV1.config,
        layout.HostPathRoleV1.data, layout.HostPathRoleV1.state,
        layout.HostPathRoleV1.workspace,
    }
    private_root_roles = {
        layout.HostPathRoleV1.bootstrap_root,
        layout.HostPathRoleV1.legacy_state_journal,
    }
    assert set(actual) == set(expected_paths)
    for role, path in expected_paths.items():
        attributes = bootstrap if role in private_root_roles else root
        assert actual[role] == (path, *(service if role in service_roles else attributes))


@pytest.mark.parametrize(
    "changes,error",
    [
        ({"name": "other"}, "service account must be metnos"),
        ({"home": "/srv/metnos"}, "service account home"),
        ({"shell": "/bin/bash"}, "service account shell"),
        ({"uid": 0}, "must not use root"),
        ({"gid": 0}, "must not use root"),
    ],
)
def test_canonical_tree_rejects_wrong_service_identity(changes, error) -> None:
    with pytest.raises(ValueError, match=error):
        layout.build_host_layout_spec_v1(_snapshot(**changes))


@pytest.mark.parametrize("groups", [(), (992, 1001), (1001,)])
def test_canonical_tree_requires_only_the_primary_group(groups) -> None:
    with pytest.raises(ValueError, match="only its primary group"):
        layout.build_host_layout_spec_v1(_snapshot(supplementary_gids=groups))


def test_missing_tree_produces_deterministic_parent_first_plan() -> None:
    spec = layout.build_host_layout_spec_v1(_snapshot())
    first = layout.diff_host_layout_v1(spec, _missing_observation(spec))
    second = layout.diff_host_layout_v1(spec, _missing_observation(spec))
    expected_kinds = (
        layout.HostLayoutStepKindV1.create_directory,
        layout.HostLayoutStepKindV1.set_owner,
        layout.HostLayoutStepKindV1.remove_posix_acl,
        layout.HostLayoutStepKindV1.set_mode,
    )
    assert first == second
    assert not first.conflicts
    assert len(first.steps) == len(spec.objects) * len(expected_kinds)
    for offset, target in enumerate(spec.objects):
        group = first.steps[offset * 4:(offset + 1) * 4]
        assert tuple(step.kind for step in group) == expected_kinds
        assert all(step.target is target for step in group)


def test_xdg_leaves_are_derived_once_by_the_immutable_path_owner() -> None:
    snapshot = _snapshot()
    spec = layout.build_host_layout_spec_v1(snapshot)
    by_role = {item.role: item.path for item in spec.objects}
    xdg = identity.metnos_xdg_layout_v1(snapshot.record)
    assert layout.HOST_PATH_POLICY_V1 is path_policy.HOST_PATH_POLICY_V1
    assert by_role[layout.HostPathRoleV1.data] == PurePosixPath(xdg.data.as_posix())
    assert by_role[layout.HostPathRoleV1.state] == PurePosixPath(xdg.state.as_posix())
    assert by_role[layout.HostPathRoleV1.config] == PurePosixPath(xdg.config.as_posix())
    assert by_role[layout.HostPathRoleV1.cache] == PurePosixPath(xdg.cache.as_posix())
    assert by_role[layout.HostPathRoleV1.workspace] == PurePosixPath(xdg.workspace.as_posix())


def test_path_policy_is_the_account_independent_layout_source() -> None:
    spec = layout.build_host_layout_spec_v1(_snapshot())
    projected = tuple(
        (
            item.role, item.path, item.ownership.kind,
            item.mode, item.posix_acl,
        )
        for item in spec.objects
    )
    assert projected == tuple(
        (item.role, item.path, item.owner_kind, item.mode, item.posix_acl)
        for item in path_policy.HOST_PATH_POLICY_V1
    )


def test_trust_anchor_owner_is_typed_ordered_and_closed() -> None:
    assert path_policy.HOST_TRUST_ANCHORS_V1 == (
        PurePosixPath("/"), PurePosixPath("/var"), PurePosixPath("/var/lib"),
    )
    with pytest.raises(ValueError, match="host trust anchor"):
        path_policy._validate_trust_anchors_v1(
            (PurePosixPath("/var"), PurePosixPath("/")),
        )


def test_diff_repairs_owner_acl_then_final_mode() -> None:
    spec = layout.build_host_layout_spec_v1(_snapshot())
    objects = list(_conforming_observation(spec).objects)
    target = spec.objects[-1]
    objects[-1] = layout.HostPathObservationV1(
        target.path, layout.HostNodeKindV1.directory,
        44, 45, target.mode, True, True,
    )
    observed = layout.HostLayoutObservationV1(spec.account, tuple(objects))
    plan = layout.diff_host_layout_v1(spec, observed)
    assert tuple(step.kind for step in plan.steps) == (
        layout.HostLayoutStepKindV1.set_owner,
        layout.HostLayoutStepKindV1.remove_posix_acl,
        layout.HostLayoutStepKindV1.set_mode,
    )
    assert all(step.target is target for step in plan.steps)


def test_non_directory_is_a_conflict_and_never_a_replacement_step() -> None:
    spec = layout.build_host_layout_spec_v1(_snapshot())
    objects = list(_conforming_observation(spec).objects)
    target = spec.objects[-1]
    objects[-1] = layout.HostPathObservationV1(
        target.path, layout.HostNodeKindV1.other, 991, 992, 0o700, False, False,
    )
    plan = layout.diff_host_layout_v1(
        spec, layout.HostLayoutObservationV1(spec.account, tuple(objects)),
    )
    assert not plan.steps
    assert plan.conflicts == (
        layout.HostLayoutConflictV1(
            layout.HostLayoutConflictKindV1.not_directory,
            target,
            layout.HostNodeKindV1.other,
        ),
    )


def test_complete_observation_is_required_and_pinned_to_account() -> None:
    spec = layout.build_host_layout_spec_v1(_snapshot())
    complete = _conforming_observation(spec)
    incomplete = layout.HostLayoutObservationV1(spec.account, complete.objects[:-1])
    with pytest.raises(ValueError, match="cover exactly"):
        layout.diff_host_layout_v1(spec, incomplete)
    changed = layout.HostLayoutObservationV1(_snapshot(uid=993), complete.objects)
    with pytest.raises(identity.PosixAccountSnapshotChangedError):
        layout.diff_host_layout_v1(spec, changed)


def test_verified_second_observation_has_a_noop_plan() -> None:
    spec = layout.build_host_layout_spec_v1(_snapshot())
    assert not layout.diff_host_layout_v1(spec, _missing_observation(spec)).is_noop
    converged = _conforming_observation(spec)
    assert layout.diff_host_layout_v1(spec, converged).is_noop
    assert layout.verify_host_layout_v1(spec, converged) is converged


def test_verify_exposes_the_exact_nonconverged_plan() -> None:
    spec = layout.build_host_layout_spec_v1(_snapshot())
    observed = _missing_observation(spec)
    expected = layout.diff_host_layout_v1(spec, observed)
    with pytest.raises(layout.HostLayoutVerificationError) as captured:
        layout.verify_host_layout_v1(spec, observed)
    assert captured.value.plan == expected
    assert str(captured.value) == "host_layout_not_converged"


def test_import_is_pure_portable_and_within_source_limits(tmp_path: Path) -> None:
    before_environment = dict(os.environ)
    before_entries = tuple(tmp_path.iterdir())
    source_path = Path(layout.__file__)
    namespace = runpy.run_path(source_path)
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden_imports = {"os", "pwd", "subprocess", "systemd", "tempfile"}
    imports = {
        alias.name.split(".")[0]
        for node in ast.walk(tree) if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert not imports & forbidden_imports
    assert "open" not in namespace and "pwd" not in namespace
    assert dict(os.environ) == before_environment
    assert tuple(tmp_path.iterdir()) == before_entries
    assert len(source.splitlines()) <= 400
    sizes = [
        node.end_lineno - node.lineno + 1
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    assert max(sizes) <= 40
    assert layout.SERVICE_HOME_V1 == PurePosixPath("/var/lib/metnos-service")
    assert layout.HOST_PROVISIONING_ROOT_V1 == PurePosixPath(
        "/var/lib/metnos-host-provisioning-v1"
    )
    assert layout.PREFLIGHT_ATTESTATION_ROOT_V1 == PurePosixPath(
        "/var/lib/metnos/executor-birth/preflight-attestations-v1"
    )
