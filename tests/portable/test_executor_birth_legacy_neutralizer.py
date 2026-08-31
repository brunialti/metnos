"""G7-E: the retirement is performed, re-read, and safe to repeat."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

import executor_birth_legacy_neutralizer as neutralizer


class _Step:
    __slots__ = ("legacy_id", "action", "locator")

    def __init__(self, legacy_id: str, action: str, locator: str) -> None:
        self.legacy_id = legacy_id
        self.action = action
        self.locator = locator


def _capability(root: Path):
    return neutralizer._TestOnlyNeutralizationCapabilityV1(root)


def _tree(tmp_path: Path) -> Path:
    root = tmp_path / "root"
    (root / "systemd").mkdir(parents=True)
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "legacy.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    return root


def test_a_unit_is_masked_and_an_entrypoint_is_moved_aside(tmp_path: Path) -> None:
    """Masking points the name at /dev/null; revoking renames, never deletes."""
    root = _tree(tmp_path)
    performed = neutralizer.neutralize_for_test_v1(_capability(root), [
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


def test_running_the_same_plan_again_is_idempotent(tmp_path: Path) -> None:
    """A resumed retirement recognises its own work instead of failing."""
    root = _tree(tmp_path)
    steps = [
        _Step("legacy-unit", "mask_user_unit", "systemd/metnos-old.service"),
        _Step("legacy-script", "revoke_repository_entrypoint", "scripts/legacy.sh"),
    ]
    first = neutralizer.neutralize_for_test_v1(_capability(root), steps)
    second = neutralizer.neutralize_for_test_v1(_capability(root), steps)

    assert [entry.repeated for entry in first] == [False, False]
    assert [entry.repeated for entry in second] == [True, True]
    assert (
        neutralizer.receipt_digest_v1(first)
        != neutralizer.receipt_digest_v1(second)
    ), "the receipt must say whether the work was done now or found done"


@pytest.mark.parametrize(("case", "code"), [
    ("occupied_unit", "neutralizer_mask_occupied"),
    ("occupied_retired", "neutralizer_entrypoint_occupied"),
    ("absolute_locator", "neutralizer_locator_invalid"),
    ("escape", "neutralizer_locator_escape"),
    ("unknown_action", "neutralizer_action_unknown"),
    ("missing_entrypoint", "neutralizer_entrypoint_invalid"),
])
def test_neutralization_denials(tmp_path: Path, case: str, code: str) -> None:
    """Every denial is one row of one table, not one apparatus each."""
    root = _tree(tmp_path)
    if case == "occupied_unit":
        (root / "systemd" / "taken.service").write_text("[Unit]\n", encoding="utf-8")
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
        neutralizer.neutralize_for_test_v1(_capability(root), [step])
    assert denied.value.code == code


def test_an_occupied_name_is_never_replaced(tmp_path: Path) -> None:
    """Refusing is the point: that file holds state nobody told us about."""
    root = _tree(tmp_path)
    taken = root / "systemd" / "taken.service"
    taken.write_text("[Unit]\n", encoding="utf-8")
    with pytest.raises(neutralizer.LegacyNeutralizerError):
        neutralizer.neutralize_for_test_v1(_capability(root), [
            _Step("legacy", "mask_system_unit", "systemd/taken.service"),
        ])
    assert taken.read_text(encoding="utf-8") == "[Unit]\n"


def test_a_look_alike_capability_does_not_open_the_door(tmp_path: Path) -> None:
    """The type is the credential; a shape with the same field is not."""
    root = _tree(tmp_path)

    class LookAlike:
        def __init__(self, root: Path) -> None:
            self.root = root

    with pytest.raises(neutralizer.LegacyNeutralizerError) as denied:
        neutralizer.neutralize_for_test_v1(LookAlike(root), [])
    assert denied.value.code == "neutralizer_capability_invalid"


def test_no_productive_neutralizer_is_exported() -> None:
    """G7's wrapper owns the authority; importing a name must not."""
    assert "neutralize_for_test_v1" not in neutralizer.__all__
    assert not any(
        name.startswith(("neutralize", "mask", "revoke"))
        for name in neutralizer.__all__
    )
