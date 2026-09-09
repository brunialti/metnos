"""G7-E: the retirement is performed, re-read, and safe to repeat."""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

import pytest

import executor_birth_legacy_neutralizer as neutralizer


POSIX_ONLY = pytest.mark.skipif(
    sys.platform.startswith("win"),
    reason="masking is a systemd concept; the core denies on Windows first",
)


@pytest.fixture
def retired_successor(tmp_path):
    from executor_birth_legacy_retirement import LegacyRetirementStepV1

    root = _tree(tmp_path)
    step = LegacyRetirementStepV1
    steps = (
        step("a-script", "a", "script", "repository", "scripts/legacy.sh", "revoke_repository_entrypoint"),
        step("b-mask", "b", "system_unit", "system", "systemd/old.service", "mask_system_unit"),
        step("c-overlap", "c", "system_unit", "system", "systemd/http.service", "preserve_replaced_system_unit"),
    )
    for name in ("old.service", "http.service"):
        path = root / "systemd" / name
        path.write_bytes(b"legacy")
        path.chmod(0o644)
    script = root / "scripts/legacy.sh"
    script.chmod(0o644)
    expected = {steps[0].locator: (script.stat().st_size, "sha256:" + hashlib.sha256(script.read_bytes()).hexdigest())}
    previous = {("system", steps[2].locator): b"release N"}
    current = {("system", steps[2].locator): b"release N+1"}
    _apply(root, steps, previous)
    (root / steps[2].locator).write_bytes(b"release N")
    (root / steps[2].locator).chmod(0o644)
    return root, steps, previous, current, expected


def _observe_retired(fixture, **changes):
    root, steps, previous, current, expected = fixture
    options = dict(
        previous_steps=steps, previous_replacement_fragments=previous,
        replacement_fragments=current, expected_retired_files=expected,
    )
    options.update(changes)
    return neutralizer._observe_retired_core_v1(root, steps, **options)


def _read_only_snapshot(root):
    import stat

    result = {}
    for path in root.rglob("*"):
        info = path.lstat()
        payload = os.readlink(path) if stat.S_ISLNK(info.st_mode) else (
            path.read_bytes() if stat.S_ISREG(info.st_mode) else None
        )
        result[str(path.relative_to(root))] = (
            info.st_ino, info.st_mode, info.st_uid, info.st_gid, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns, payload,
        )
    return result


@POSIX_ONLY
@pytest.mark.parametrize("current", [b"release N", b"release N+1"])
def test_successor_observer_accepts_exact_previous_or_current_without_writes(
    retired_successor, monkeypatch, current,
):
    from executor_birth_legacy_retirement import plan_digest_v1

    root, steps, *_rest = retired_successor
    (root / steps[2].locator).write_bytes(current)
    before = _read_only_snapshot(root)
    for name in ("_neutralize_core_v1", "_mask_v1", "_revoke_v1", "_preserve_regular_name_v1"):
        monkeypatch.setattr(neutralizer, name, lambda *_a, **_k: pytest.fail("observer wrote"))
    assert _observe_retired(retired_successor) == plan_digest_v1(steps)
    assert _observe_retired(retired_successor) == plan_digest_v1(steps)
    assert _read_only_snapshot(root) == before


@POSIX_ONLY
@pytest.mark.parametrize("case", [
    "missing_mask", "wrong_mask", "missing_retired", "changed_retired", "original_recreated",
    "missing_current", "wrong_current", "current_mode", "current_link", "current_hardlink",
    "missing_preserved", "changed_preserved", "preserved_mode", "missing_record",
    "changed_record", "record_mode", "record_link", "record_hardlink", "half_mask_history",
])
def test_successor_observer_refuses_drift_without_repair(retired_successor, case):
    root, steps, *_rest = retired_successor
    script, mask, current = (root / item.locator for item in steps)
    retired = script.with_name(script.name + neutralizer.RETIRED_EXTENSION_V1)
    preserved = current.with_name(current.name + neutralizer.PRESERVED_EXTENSION_V1)
    record = preserved.with_name(preserved.name + ".receipt.json")
    if case.startswith("missing_"):
        {"missing_mask": mask, "missing_retired": retired, "missing_current": current,
         "missing_preserved": preserved, "missing_record": record}[case].unlink()
    elif case in {"changed_retired", "wrong_current", "changed_preserved", "changed_record"}:
        {"changed_retired": retired, "wrong_current": current,
         "changed_preserved": preserved, "changed_record": record}[case].write_bytes(b"changed")
    elif case == "original_recreated":
        script.write_bytes(b"again")
    elif case in {"current_mode", "record_mode", "preserved_mode"}:
        {"current_mode": current, "record_mode": record, "preserved_mode": preserved}[case].chmod(0o666)
    elif case == "wrong_mask":
        mask.unlink()
        mask.symlink_to("/wrong")
    elif case.endswith("_link"):
        target = current if case == "current_link" else record
        content = target.read_bytes()
        target.unlink()
        other = root / "other"
        other.write_bytes(content)
        target.symlink_to(other)
    elif case.endswith("_hardlink"):
        os.link(current if case == "current_hardlink" else record, root / "hardlink")
    else:
        mask.with_name(mask.name + neutralizer.PRESERVED_EXTENSION_V1).unlink()
    before = _read_only_snapshot(root)
    with pytest.raises((neutralizer.LegacyNeutralizerError, OSError)):
        _observe_retired(retired_successor)
    assert _read_only_snapshot(root) == before


@POSIX_ONLY
def test_successor_observer_rejects_changed_plan_and_unbound_inputs(retired_successor):
    _root, steps, *_rest = retired_successor
    for changed in (
        {"previous_steps": steps[:-1]}, {"expected_retired_files": {}},
        {"previous_replacement_fragments": {}}, {"replacement_fragments": {}},
    ):
        with pytest.raises(neutralizer.LegacyNeutralizerError):
            _observe_retired(retired_successor, **changed)


@POSIX_ONLY
def test_successor_observer_accepts_mask_created_from_absent_name(tmp_path):
    from executor_birth_legacy_retirement import LegacyRetirementStepV1

    root = _tree(tmp_path)
    steps = (LegacyRetirementStepV1(
        "absent", "a", "system_unit", "system", "systemd/absent.service", "mask_system_unit",
    ),)
    _apply(root, steps)
    assert _observe_retired((root, steps, {}, {}, {})).startswith("sha256:")


class _Step:
    __slots__ = ("legacy_id", "action", "locator", "scope")

    def __init__(
        self, legacy_id: str, action: str, locator: str,
        scope: str = "system",
    ) -> None:
        self.legacy_id = legacy_id
        self.action = action
        self.locator = locator
        self.scope = scope


def _capability(root: Path):
    return neutralizer._TestOnlyNeutralizationCapabilityV1(root)


def _tree(tmp_path: Path) -> Path:
    root = tmp_path / "root"
    (root / "systemd").mkdir(parents=True)
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "legacy.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    return root


def _apply(root: Path, steps, replacements=None):
    return neutralizer.neutralize_for_test_v1(
        _capability(root), steps,
        replacement_fragments=replacements or {},
    )


@POSIX_ONLY
def test_a_unit_is_masked_and_an_entrypoint_is_moved_aside(tmp_path: Path) -> None:
    """Masking points the name at /dev/null; revoking renames, never deletes."""
    root = _tree(tmp_path)
    performed = _apply(root, [
        _Step("legacy-unit", "mask_system_unit", "systemd/metnos-old.service"),
        _Step("legacy-script", "revoke_repository_entrypoint", "scripts/legacy.sh"),
    ])

    masked = root / "systemd" / "metnos-old.service"
    assert masked.is_symlink() and os.readlink(masked) == neutralizer.MASK_TARGET_V1
    retired = root / "scripts" / ("legacy.sh" + neutralizer.RETIRED_EXTENSION_V1)
    assert retired.is_file() and not (root / "scripts" / "legacy.sh").exists()
    # The entry point survives its retirement: it can still be inspected.
    assert retired.read_text(encoding="utf-8") == "#!/bin/sh\n"
    assert [entry.repeated for entry in performed] == [False, False]


@POSIX_ONLY
def test_running_the_same_plan_again_is_idempotent(tmp_path: Path) -> None:
    """A resumed retirement recognises its own work instead of failing."""
    root = _tree(tmp_path)
    steps = [
        _Step("legacy-unit", "mask_user_unit", "systemd/metnos-old.service"),
        _Step("legacy-script", "revoke_repository_entrypoint", "scripts/legacy.sh"),
    ]
    first = _apply(root, steps)
    second = _apply(root, steps)

    assert [entry.repeated for entry in first] == [False, False]
    assert [entry.repeated for entry in second] == [True, True]
    assert (
        neutralizer.receipt_digest_v1(first)
        != neutralizer.receipt_digest_v1(second)
    ), "the receipt must say whether the work was done now or found done"


@POSIX_ONLY
@pytest.mark.parametrize(("case", "code"), [
    ("occupied_unit_link", "neutralizer_mask_occupied"),
    ("occupied_retired", "neutralizer_entrypoint_occupied"),
    ("absolute_locator", "neutralizer_locator_invalid"),
    ("escape", "neutralizer_locator_escape"),
    ("unknown_action", "neutralizer_action_unknown"),
    ("missing_entrypoint", "neutralizer_entrypoint_invalid"),
])
@POSIX_ONLY
def test_neutralization_denials(tmp_path: Path, case: str, code: str) -> None:
    """Every denial is one row of one table, not one apparatus each."""
    root = _tree(tmp_path)
    if case == "occupied_unit_link":
        (root / "systemd" / "taken.service").symlink_to("elsewhere.service")
        step = _Step("legacy", "mask_system_unit", "systemd/taken.service")
    elif case == "occupied_retired":
        (root / "scripts" / ("legacy.sh" + neutralizer.RETIRED_EXTENSION_V1)).write_text(
            "older\n", encoding="utf-8",
        )
        step = _Step("legacy", "revoke_repository_entrypoint", "scripts/legacy.sh")
    elif case == "absolute_locator":
        step = _Step("legacy", "mask_system_unit", "/etc/systemd/system/x.service")
    elif case == "escape":
        (tmp_path / "outside").mkdir()
        (root / "away").symlink_to(tmp_path / "outside")
        step = _Step("legacy", "mask_system_unit", "away/x.service")
    elif case == "unknown_action":
        step = _Step("legacy", "delete_everything", "systemd/x.service")
    else:
        step = _Step("legacy", "revoke_repository_entrypoint", "scripts/absent.sh")

    with pytest.raises(neutralizer.LegacyNeutralizerError) as denied:
        _apply(root, [step])
    assert denied.value.code == code


@POSIX_ONLY
def test_an_occupied_legacy_unit_is_preserved_before_masking(
    tmp_path: Path,
) -> None:
    """The old bytes remain inspectable after their name becomes a mask."""
    root = _tree(tmp_path)
    taken = root / "systemd" / "taken.service"
    taken.write_text("[Unit]\n", encoding="utf-8")
    step = _Step("legacy", "mask_system_unit", "systemd/taken.service")

    first = _apply(root, [step])
    repeated = _apply(root, [step])

    preserved = taken.with_name(
        taken.name + neutralizer.PRESERVED_EXTENSION_V1,
    )
    assert taken.is_symlink() and os.readlink(taken) == neutralizer.MASK_TARGET_V1
    assert preserved.read_text(encoding="utf-8") == "[Unit]\n"
    assert first[0].repeated is False
    assert repeated[0].repeated is True


@POSIX_ONLY
@pytest.mark.parametrize(
    "stage", [
        "legacy_unit_record_published", "legacy_unit_preserved",
        "legacy_unit_masked",
    ],
)
def test_declared_masking_interruptions_converge(
    tmp_path: Path, stage: str,
) -> None:
    root = _tree(tmp_path)
    unit = root / "systemd" / "taken.service"
    unit.write_bytes(b"previous")
    step = _Step("legacy", "mask_user_unit", "systemd/taken.service")

    class Interrupted(Exception):
        pass

    def interrupt(observed: str) -> None:
        if observed == stage:
            raise Interrupted

    with pytest.raises(Interrupted):
        neutralizer.neutralize_for_test_v1(
            _capability(root), [step], replacement_fragments={},
            _crash_seam=interrupt,
        )
    resumed = _apply(root, [step])
    preserved = unit.with_name(
        unit.name + neutralizer.PRESERVED_EXTENSION_V1,
    )
    assert unit.is_symlink() and os.readlink(unit) == neutralizer.MASK_TARGET_V1
    assert preserved.read_bytes() == b"previous"
    assert resumed[0].repeated is (stage == "legacy_unit_masked")


@POSIX_ONLY
def test_the_replaced_system_unit_is_preserved_and_reread(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    unit = root / "systemd" / "metnos-http.service"
    previous = b"[Service]\nExecStart=/usr/bin/previous\n"
    replacement = b"[Service]\nExecStart=/usr/bin/current\n"
    unit.write_bytes(previous)
    step = _Step(
        "legacy-service-http-system", "preserve_replaced_system_unit",
        "systemd/metnos-http.service",
    )
    replacements = {("system", step.locator): replacement}

    first = _apply(root, [step], replacements)
    preserved = unit.with_name(
        unit.name + neutralizer.PRESERVED_EXTENSION_V1,
    )
    assert not unit.exists() and preserved.read_bytes() == previous
    assert first[0].content_hash == (
        "sha256:" + hashlib.sha256(previous).hexdigest()
    )
    assert (first[0].mode, first[0].uid, first[0].gid) == (
        preserved.stat().st_mode & 0o7777,
        preserved.stat().st_uid,
        preserved.stat().st_gid,
    )

    before_publication = _apply(root, [step], replacements)
    unit.write_bytes(replacement)
    completed = _apply(root, [step], replacements)
    assert before_publication[0].repeated is True
    assert completed[0].repeated is True
    assert preserved.read_bytes() == previous


@POSIX_ONLY
@pytest.mark.parametrize("state", ["occupied_history", "changed_final", "missing"])
def test_preservation_accepts_only_the_named_states(
    tmp_path: Path, state: str,
) -> None:
    root = _tree(tmp_path)
    unit = root / "systemd" / "metnos-http.service"
    preserved = unit.with_name(
        unit.name + neutralizer.PRESERVED_EXTENSION_V1,
    )
    replacement = b"[Service]\nExecStart=/usr/bin/current\n"
    step = _Step(
        "legacy-service-http-system", "preserve_replaced_system_unit",
        "systemd/metnos-http.service",
    )
    if state == "occupied_history":
        unit.write_bytes(b"previous")
        preserved.write_bytes(b"unrelated")
    elif state == "changed_final":
        unit.write_bytes(b"different")
        preserved.write_bytes(b"previous")
    with pytest.raises(neutralizer.LegacyNeutralizerError):
        _apply(root, [step], {("system", step.locator): replacement})


@POSIX_ONLY
def test_a_preservation_record_binds_the_historical_file(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    unit = root / "systemd" / "metnos-http.service"
    unit.write_bytes(b"previous")
    replacement = b"current"
    step = _Step(
        "legacy-service-http-system", "preserve_replaced_system_unit",
        "systemd/metnos-http.service",
    )
    _apply(root, [step], {("system", step.locator): replacement})
    preserved = unit.with_name(
        unit.name + neutralizer.PRESERVED_EXTENSION_V1,
    )
    preserved.write_bytes(b"changed")
    with pytest.raises(neutralizer.LegacyNeutralizerError) as denied:
        _apply(root, [step], {("system", step.locator): replacement})
    assert denied.value.code == "neutralizer_preservation_conflict"


@POSIX_ONLY
@pytest.mark.parametrize("stage", ["record_prefix", "record_published"])
def test_preservation_resumes_at_each_durable_record_boundary(
    tmp_path: Path, stage: str,
) -> None:
    root = _tree(tmp_path)
    unit = root / "systemd" / "metnos-http.service"
    unit.write_bytes(b"previous")
    replacement = b"current"
    step = _Step(
        "legacy-service-http-system", "preserve_replaced_system_unit",
        "systemd/metnos-http.service",
    )
    preserved = unit.with_name(
        unit.name + neutralizer.PRESERVED_EXTENSION_V1,
    )
    evidence = neutralizer._regular_file_evidence_v1(unit)
    encoded = neutralizer._preservation_record_v1(
        step.legacy_id, unit, preserved, evidence,
    )
    if stage == "record_prefix":
        record = unit.with_name(
            unit.name + neutralizer.PRESERVED_EXTENSION_V1 + ".receipt.json",
        )
        temporary = record.with_name("." + record.name + ".writing")
        temporary.write_bytes(encoded[: len(encoded) // 2])
    else:
        neutralizer._publish_preservation_record_v1(unit, encoded)

    result = _apply(root, [step], {("system", step.locator): replacement})
    assert result[0].repeated is False
    assert not unit.exists() and preserved.read_bytes() == b"previous"
    record = unit.with_name(
        unit.name + neutralizer.PRESERVED_EXTENSION_V1 + ".receipt.json",
    )
    assert record.read_bytes() == encoded


@POSIX_ONLY
@pytest.mark.parametrize(
    "stage", ["preservation_record_published", "replaced_system_unit_preserved"],
)
def test_declared_preservation_interruptions_converge(
    tmp_path: Path, stage: str,
) -> None:
    root = _tree(tmp_path)
    unit = root / "systemd" / "metnos-http.service"
    unit.write_bytes(b"previous")
    replacement = b"current"
    step = _Step(
        "legacy-service-http-system", "preserve_replaced_system_unit",
        "systemd/metnos-http.service",
    )

    class Interrupted(Exception):
        pass

    def interrupt(observed: str) -> None:
        if observed == stage:
            raise Interrupted

    with pytest.raises(Interrupted):
        neutralizer.neutralize_for_test_v1(
            _capability(root), [step],
            replacement_fragments={("system", step.locator): replacement},
            _crash_seam=interrupt,
        )
    resumed = _apply(
        root, [step], {("system", step.locator): replacement},
    )
    preserved = unit.with_name(
        unit.name + neutralizer.PRESERVED_EXTENSION_V1,
    )
    assert resumed[0].repeated is (stage == "replaced_system_unit_preserved")
    assert not unit.exists() and preserved.read_bytes() == b"previous"


def test_a_look_alike_capability_does_not_open_the_door(tmp_path: Path) -> None:
    """The type is the credential; a shape with the same field is not."""
    root = _tree(tmp_path)

    class LookAlike:
        def __init__(self, root: Path) -> None:
            self.root = root

    with pytest.raises(neutralizer.LegacyNeutralizerError) as denied:
        neutralizer.neutralize_for_test_v1(
            LookAlike(root), [], replacement_fragments={},
        )
    assert denied.value.code == "neutralizer_capability_invalid"


def test_no_productive_neutralizer_is_exported() -> None:
    """G7's wrapper owns the authority; importing a name must not."""
    assert "neutralize_for_test_v1" not in neutralizer.__all__
    assert not any(
        name.startswith(("neutralize", "mask", "revoke"))
        for name in neutralizer.__all__
    )


def test_windows_denies_before_resolving_any_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """The denial does not depend on how well an emulation held."""
    monkeypatch.setattr(neutralizer.sys, "platform", "win32")

    def _must_not_run(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the filesystem must not be consulted")

    monkeypatch.setattr(neutralizer.os, "symlink", _must_not_run)
    monkeypatch.setattr(neutralizer.os, "rename", _must_not_run)
    with pytest.raises(neutralizer.LegacyNeutralizerError) as denied:
        neutralizer.neutralize_for_test_v1(
            _capability(tmp_path), [], replacement_fragments={},
        )
    assert denied.value.code == "neutralizer_unsupported_platform"
