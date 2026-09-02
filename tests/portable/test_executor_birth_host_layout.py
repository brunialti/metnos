"""Portable contract tests for the pure RM-0008 host-layout domain."""
from __future__ import annotations

import ast
import os
from pathlib import Path, PurePosixPath
import runpy

import pytest

import executor_birth_account_identity as identity
import executor_birth_host_layout as layout


def _snapshot(**changes) -> identity.PosixAccountSnapshotV1:
    values = {
        "name": "metnos",
        "uid": 991,
        "gid": 992,
        "home": "/var/lib/metnos-service",
        "shell": "/usr/sbin/nologin",
    }
    values.update(changes)
    record = identity.PosixAccountRecordV1(**values)
    return identity.PosixAccountSnapshotV1(record, (992, 1001))


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


def test_account_policy_is_system_metnos_with_canonical_home_and_shell() -> None:
    spec = layout.build_host_layout_spec_v1(_snapshot())
    assert spec.account_policy == layout.HostAccountPolicyV1(
        layout.HostAccountKindV1.system,
        "metnos",
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
    root = (layout.HostOwnerKindV1.root, 0, 0, 0o755, layout.PosixAclPolicyV1.absent)
    service = (
        layout.HostOwnerKindV1.service, 991, 992, 0o700,
        layout.PosixAclPolicyV1.absent,
    )
    expected_paths = {
        layout.HostPathRoleV1.ownership_parent: "/var/lib/metnos",
        layout.HostPathRoleV1.service_home: "/var/lib/metnos-service",
        layout.HostPathRoleV1.ownership_root: "/var/lib/metnos/executor-birth",
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
    assert set(actual) == set(expected_paths)
    for role, path in expected_paths.items():
        assert actual[role] == (path, *(service if role in service_roles else root))


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


def test_xdg_leaves_are_derived_from_the_identity_owner(monkeypatch) -> None:
    snapshot = _snapshot()
    calls = []
    original = identity.metnos_xdg_layout_v1

    def derive(record):
        calls.append(record)
        return original(record)

    monkeypatch.setattr(layout, "metnos_xdg_layout_v1", derive)
    spec = layout.build_host_layout_spec_v1(snapshot)
    by_role = {item.role: item.path for item in spec.objects}
    xdg = original(snapshot.record)
    assert calls == [snapshot.record]
    assert by_role[layout.HostPathRoleV1.data] == PurePosixPath(xdg.data.as_posix())
    assert by_role[layout.HostPathRoleV1.state] == PurePosixPath(xdg.state.as_posix())
    assert by_role[layout.HostPathRoleV1.config] == PurePosixPath(xdg.config.as_posix())
    assert by_role[layout.HostPathRoleV1.cache] == PurePosixPath(xdg.cache.as_posix())
    assert by_role[layout.HostPathRoleV1.workspace] == PurePosixPath(xdg.workspace.as_posix())


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
