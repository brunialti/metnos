from __future__ import annotations

from pathlib import Path

import pytest

from ._support import (
    inventory_once_helper_name,
    make_root,
    mkdir_private,
    open_session,
    secure_fs,
    write_private,
)


CASES = (
    "add-between-scans",
    "remove-between-scans",
    "rename-between-scans",
    "replace-same-name-between-scans",
    "non-json-entry",
    "local-4096",
    "local-4097",
    "aggregate-4096",
    "aggregate-4097",
)


def _populate(directory: Path, count: int) -> None:
    for index in range(count):
        write_private(directory / f"entry-{index:04d}.bin", b"")


def _mutating_inventory(module, monkeypatch, root: Path, session, case: str) -> None:
    write_private(root / "anchor.bin", b"anchor")
    helper_name = inventory_once_helper_name()
    original = getattr(module, helper_name)
    calls = 0

    def barrier(*args, **kwargs):
        nonlocal calls
        result = original(*args, **kwargs)
        calls += 1
        if calls == 1:
            if case == "add-between-scans":
                write_private(root / "added.bin", b"added")
            elif case == "remove-between-scans":
                (root / "anchor.bin").unlink()
            elif case == "rename-between-scans":
                (root / "anchor.bin").rename(root / "renamed.bin")
            else:
                (root / "anchor.bin").unlink()
                write_private(root / "anchor.bin", b"replacement")
        return result

    monkeypatch.setattr(module, helper_name, barrier)
    with pytest.raises(module.BirthSecureFSError):
        with session.global_lock(exclusive=False, create=False):
            session._inventory_state(())
    assert calls == 2


@pytest.mark.parametrize("case", CASES, ids=CASES)
def test_inventory_closure_and_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    module = secure_fs()
    root = make_root(tmp_path / "birth")
    with open_session(root) as session:
        with session.global_lock(exclusive=True, create=True):
            pass
        if case.endswith("between-scans"):
            _mutating_inventory(module, monkeypatch, root, session, case)
            return
        if case == "non-json-entry":
            write_private(root / "opaque.bin", b"not-json")
            with session.global_lock(exclusive=False, create=False):
                entries = session._inventory_state(())
            entry = next(item for item in entries if item.name == "opaque.bin")
            assert entry.size == len(b"not-json")
            return
        if case.startswith("local-"):
            count = int(case.split("-")[1])
            _populate(root, count)
            if count == 4097:
                with pytest.raises(module.BirthSecureFSError) as caught:
                    with session.global_lock(exclusive=False, create=False):
                        session._inventory_state(())
                assert caught.value.code == "birth_provisioning_recovery_ambiguous"
            else:
                with session.global_lock(exclusive=False, create=False):
                    assert len(session._inventory_state(())) == 4096
            return

        left, right = root / "left", root / "right"
        mkdir_private(left)
        mkdir_private(right)
        _populate(left, 2048)
        _populate(right, 2048 if case == "aggregate-4096" else 2049)
        budget = module._InventoryBudgetV1()
        with session.global_lock(exclusive=False, create=False):
            first = session._inventory_state(("left",), budget=budget)
            assert len(first) == 2048
            if case == "aggregate-4097":
                with pytest.raises(module.BirthSecureFSError) as caught:
                    session._inventory_state(("right",), budget=budget)
                assert caught.value.code == "birth_provisioning_recovery_ambiguous"
            else:
                assert len(session._inventory_state(("right",), budget=budget)) == 2048
