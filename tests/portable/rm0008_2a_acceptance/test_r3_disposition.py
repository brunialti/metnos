from __future__ import annotations

import hashlib
import inspect
import os
from dataclasses import fields
from pathlib import Path

import pytest

from ._support import (
    lock_role_binding,
    make_root,
    object_identity,
    open_session,
    private_role,
    role_binding,
    secure_fs,
    tree_snapshot,
    write_private,
    expected_directory_links,
)


CASES = (
    "complete-file-success",
    "empty-directory-success",
    "reject-root-components",
    "reject-absent",
    "reject-identity",
    "reject-kind",
    "reject-role",
    "reject-links",
    "reject-size",
    "reject-digest",
    "reject-nonempty-directory",
    "partial-pending-success",
    "reject-partial-oversize",
    "reject-foreign-pending",
)


REJECTION_CODES = {
    "reject-root-components": "birth_provisioning_io_unavailable",
    "reject-absent": "birth_provisioning_recovery_ambiguous",
    "reject-identity": "birth_provisioning_recovery_ambiguous",
    "reject-kind": "birth_provisioning_recovery_ambiguous",
    "reject-role": "birth_provisioning_acl_unsafe",
    "reject-links": "birth_provisioning_recovery_ambiguous",
    "reject-size": "birth_provisioning_recovery_ambiguous",
    "reject-digest": "birth_provisioning_recovery_ambiguous",
    "reject-nonempty-directory": "birth_provisioning_recovery_ambiguous",
    "reject-partial-oversize": "birth_provisioning_recovery_ambiguous",
    "reject-foreign-pending": "birth_provisioning_recovery_ambiguous",
}


def _expectation(
    module,
    *,
    components,
    identity,
    kind="regular_file",
    role=None,
    disposal_class="complete_file",
    links=1,
    expected_size=7,
    maximum_partial_size=None,
    digest="sha256:" + hashlib.sha256(b"payload").hexdigest(),
    inventory=None,
):
    return module._DisposalExpectation(
        components=components,
        identity=identity,
        kind=module._ObjectKind(kind),
        role=private_role(module) if role is None else role,
        disposal_class=module._DisposalClass(disposal_class),
        links=links,
        expected_size=expected_size,
        maximum_partial_size=maximum_partial_size,
        content_sha256=digest,
        inventory=inventory,
    )


@pytest.mark.parametrize("case", CASES, ids=CASES)
def test_dispose_transaction_object(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    assert set(REJECTION_CODES) == {
        item for item in CASES if item.startswith("reject-")
    }
    module = secure_fs()
    root = make_root(tmp_path / "birth")
    bindings = [lock_role_binding(module)]
    bindings.extend(
        (
            role_binding(
                module,
                ("absent.bin",),
                directory=False,
                role=private_role(module),
            ),
            role_binding(
                module,
                ("empty-directory",),
                directory=True,
                role=private_role(module),
            ),
            role_binding(
                module,
                ("empty-directory", "child.bin"),
                directory=False,
                role=private_role(module),
            ),
            role_binding(
                module,
                ("payload.bin",),
                directory=False,
                role=private_role(module),
            ),
            role_binding(
                module,
                ("payload.pending",),
                directory=False,
                role=private_role(module),
            ),
            role_binding(
                module,
                ("second-link.bin",),
                directory=False,
                role=private_role(module),
            ),
        )
    )
    session = open_session(root, role_bindings=tuple(bindings))
    try:
        with session.global_lock(exclusive=True, create=True):
            if case == "reject-root-components":
                assert {item.value for item in module._ObjectKind} == {
                    "regular_file",
                    "directory",
                }
                assert {item.value for item in module._DisposalClass} == {
                    "complete_file",
                    "partial_pending_file",
                    "empty_directory",
                }
                assert {item.value for item in module._BirthObjectRole} == {
                    "birth_confidential",
                    "birth_integrity_only",
                    "historical_private",
                    "historical_public",
                }
                assert tuple(item.name for item in fields(module._DisposalExpectation)) == (
                    "components",
                    "identity",
                    "kind",
                    "role",
                    "disposal_class",
                    "links",
                    "expected_size",
                    "maximum_partial_size",
                    "content_sha256",
                    "inventory",
                )
                assert tuple(item.name for item in fields(module._InventoryEntry)) == (
                    "name",
                    "identity",
                    "kind",
                    "role",
                    "links",
                    "size",
                )
                assert tuple(item.name for item in fields(module._DispositionResult)) == (
                    "identity",
                    "kind",
                    "removed",
                )
                signature = inspect.signature(
                    module._SecureRootSession.dispose_transaction_object
                )
                assert tuple(signature.parameters) == ("self", "expectation")
                assert all(
                    parameter.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
                    and parameter.default is inspect.Parameter.empty
                    for parameter in signature.parameters.values()
                )
                assert str(
                    signature.parameters["expectation"].annotation
                ).strip("'") == "_DisposalExpectation"
                assert str(signature.return_annotation).strip("'") == "_DispositionResult"

                identity = object_identity(root, module)
                immutable_records = (
                    _expectation(
                        module,
                        components=("complete.bin",),
                        identity=identity,
                    ),
                    module._InventoryEntry(
                        "complete.bin",
                        identity,
                        module._ObjectKind("regular_file"),
                        private_role(module),
                        1,
                        7,
                    ),
                    module._DispositionResult(
                        identity,
                        module._ObjectKind("regular_file"),
                        True,
                    ),
                )
                for record in immutable_records:
                    assert not hasattr(record, "__dict__")
                    assert record.__dataclass_params__.frozen
                    first_field = fields(record)[0].name
                    with pytest.raises((AttributeError, TypeError)):
                        setattr(record, first_field, None)
                invalid_values = (
                    {"components": (), "identity": identity},
                    {
                        "components": ("complete.bin",),
                        "identity": identity,
                        "digest": None,
                    },
                    {
                        "components": ("complete.bin",),
                        "identity": identity,
                        "maximum_partial_size": 7,
                    },
                    {
                        "components": ("complete.bin",),
                        "identity": identity,
                        "expected_size": None,
                    },
                    {
                        "components": ("complete.bin",),
                        "identity": identity,
                        "inventory": (),
                    },
                    {
                        "components": ("complete.bin",),
                        "identity": identity,
                        "digest": "sha256:" + "0" * 63,
                    },
                    {
                        "components": ("complete.bin",),
                        "identity": identity,
                        "digest": "sha256:" + "0" * 65,
                    },
                    {
                        "components": ("complete.bin",),
                        "identity": identity,
                        "digest": "sha256:" + "g" * 64,
                    },
                    {
                        "components": ("complete.bin",),
                        "identity": identity,
                        "digest": "sha256:" + "A" * 64,
                    },
                    {
                        "components": ("complete.bin",),
                        "identity": identity,
                        "digest": "0" * 64,
                    },
                    {
                        "components": ("partial.pending",),
                        "identity": identity,
                        "disposal_class": "partial_pending_file",
                        "expected_size": 7,
                        "maximum_partial_size": 7,
                        "digest": None,
                    },
                    {
                        "components": ("partial.pending",),
                        "identity": identity,
                        "disposal_class": "partial_pending_file",
                        "expected_size": None,
                        "maximum_partial_size": -1,
                        "digest": None,
                    },
                    {
                        "components": ("partial.pending",),
                        "identity": identity,
                        "disposal_class": "partial_pending_file",
                        "expected_size": None,
                        "maximum_partial_size": None,
                        "digest": None,
                    },
                    {
                        "components": ("partial.pending",),
                        "identity": identity,
                        "disposal_class": "partial_pending_file",
                        "expected_size": None,
                        "maximum_partial_size": 7,
                        "digest": "sha256:" + "0" * 64,
                    },
                    {
                        "components": ("partial.pending",),
                        "identity": identity,
                        "disposal_class": "partial_pending_file",
                        "expected_size": None,
                        "maximum_partial_size": 7,
                        "digest": None,
                        "inventory": (),
                    },
                    {
                        "components": ("empty",),
                        "identity": identity,
                        "kind": "directory",
                        "disposal_class": "empty_directory",
                        "links": expected_directory_links(),
                        "expected_size": None,
                        "digest": None,
                        "inventory": None,
                    },
                    {
                        "components": ("empty",),
                        "identity": identity,
                        "kind": "regular_file",
                        "disposal_class": "empty_directory",
                        "links": expected_directory_links(),
                        "expected_size": None,
                        "digest": None,
                        "inventory": (),
                    },
                    {
                        "components": ("empty",),
                        "identity": identity,
                        "kind": "directory",
                        "disposal_class": "empty_directory",
                        "links": expected_directory_links() + 1,
                        "expected_size": None,
                        "digest": None,
                        "inventory": (),
                    },
                    {
                        "components": ("empty",),
                        "identity": identity,
                        "kind": "directory",
                        "disposal_class": "empty_directory",
                        "links": expected_directory_links(),
                        "expected_size": 0,
                        "digest": None,
                        "inventory": (),
                    },
                    {
                        "components": ("empty",),
                        "identity": identity,
                        "kind": "directory",
                        "disposal_class": "empty_directory",
                        "links": expected_directory_links(),
                        "expected_size": None,
                        "maximum_partial_size": 0,
                        "digest": None,
                        "inventory": (),
                    },
                    {
                        "components": ("empty",),
                        "identity": identity,
                        "kind": "directory",
                        "disposal_class": "empty_directory",
                        "links": expected_directory_links(),
                        "expected_size": None,
                        "digest": "sha256:" + "0" * 64,
                        "inventory": (),
                    },
                )
                before = tree_snapshot(root)
                opened: list[object] = []
                if os.name == "posix":
                    real_open = os.open

                    def traced_open(path, flags, mode=0o777, *, dir_fd=None):
                        opened.append(path)
                        return real_open(path, flags, mode, dir_fd=dir_fd)

                    monkeypatch.setattr(os, "open", traced_open)
                else:
                    real_open_relative = module._win_open_relative_v1

                    def traced_open_relative(*args, **kwargs):
                        opened.append((args, kwargs))
                        return real_open_relative(*args, **kwargs)

                    monkeypatch.setattr(
                        module, "_win_open_relative_v1", traced_open_relative
                    )
                for values in invalid_values:
                    error = None
                    try:
                        expectation = _expectation(module, **values)
                    except module.BirthSecureFSError as caught:
                        error = caught
                    else:
                        with pytest.raises(module.BirthSecureFSError) as caught:
                            session.dispose_transaction_object(expectation)
                        error = caught.value
                    assert error.code == REJECTION_CODES[case]
                    assert opened == []
                    assert tree_snapshot(root) == before
                return
            if case == "reject-absent":
                expectation = _expectation(
                    module,
                    components=("absent.bin",),
                    identity=module._ObjectIdentity("0", "0"),
                )
                before = tree_snapshot(root)
                with pytest.raises(module.BirthSecureFSError) as caught:
                    session.dispose_transaction_object(expectation)
                assert caught.value.code == REJECTION_CODES[case]
                assert tree_snapshot(root) == before
                return

            if case in {"empty-directory-success", "reject-nonempty-directory"}:
                directory = session.create_directory_exclusive(
                    ("empty-directory",), role=private_role(module)
                )
                identity = object_identity(root / "empty-directory", module)
                if case == "reject-nonempty-directory":
                    session.create_file_exclusive(
                        ("empty-directory", "child.bin"),
                        b"child",
                        role=private_role(module),
                    )
                expectation = _expectation(
                    module,
                    components=("empty-directory",),
                    identity=identity,
                    kind="directory",
                    disposal_class="empty_directory",
                    links=expected_directory_links(),
                    expected_size=None,
                    digest=None,
                    inventory=(),
                )
                if case == "reject-nonempty-directory":
                    before = tree_snapshot(root)
                    with pytest.raises(module.BirthSecureFSError) as caught:
                        session.dispose_transaction_object(expectation)
                    assert caught.value.code == REJECTION_CODES[case]
                    assert directory.inventory() == ("child.bin",)
                    assert tree_snapshot(root) == before
                else:
                    if os.name != "posix":
                        result = session.dispose_transaction_object(expectation)
                        assert result.identity == identity and result.removed is True
                        assert not (root / "empty-directory").exists()
                        return
                    calls: list[tuple[object, object]] = []
                    real_rmdir = os.rmdir
                    real_inventory = module._posix_inventory
                    parent_before = session._inventory_state(())
                    expected_parent = tuple(
                        item
                        for item in parent_before
                        if item.name != "empty-directory"
                    )
                    post_parent = []
                    removed = False

                    def traced_rmdir(path, *, dir_fd=None):
                        nonlocal removed
                        calls.append((path, dir_fd))
                        result = real_rmdir(path, dir_fd=dir_fd)
                        removed = True
                        return result

                    def traced_inventory(fd, *args, **kwargs):
                        result = real_inventory(fd, *args, **kwargs)
                        value = os.fstat(fd)
                        root_value = root.stat()
                        if (
                            removed
                            and (value.st_dev, value.st_ino)
                            == (root_value.st_dev, root_value.st_ino)
                        ):
                            post_parent.append(result)
                        return result

                    monkeypatch.setattr(os, "rmdir", traced_rmdir)
                    monkeypatch.setattr(module, "_posix_inventory", traced_inventory)
                    result = session.dispose_transaction_object(expectation)
                    assert result.identity == identity and result.removed is True
                    assert not (root / "empty-directory").exists()
                    assert calls and calls[-1][0] == "empty-directory"
                    assert isinstance(calls[-1][1], int)
                    assert post_parent == [expected_parent, expected_parent]
                return

            payload = b"payload"
            name = (
                "payload.pending"
                if "partial" in case or case == "reject-foreign-pending"
                else "payload.bin"
            )
            if case == "reject-foreign-pending":
                session.create_file_exclusive(
                    (name,), payload, role=private_role(module)
                )
                (root / name).rename(tmp_path / "replaced-original.pending")
                write_private(root / name, payload)
                identity = object_identity(root / name, module)
            else:
                identity = session.create_file_exclusive(
                    (name,), payload, role=private_role(module)
                )
            values = {
                "components": (name,),
                "identity": identity,
                "expected_size": len(payload),
                "digest": "sha256:" + hashlib.sha256(payload).hexdigest(),
            }
            if case == "reject-identity":
                values["identity"] = module._ObjectIdentity("bad", "identity")
            elif case == "reject-kind":
                values.update(
                    kind="directory",
                    disposal_class="empty_directory",
                    links=expected_directory_links(),
                    expected_size=None,
                    digest=None,
                    inventory=(),
                )
            elif case == "reject-role":
                values["role"] = module._BirthObjectRole("birth_integrity_only")
            elif case == "reject-links":
                os.link(root / name, root / "second-link.bin")
                values["links"] = 1
            elif case == "reject-size":
                values["expected_size"] = len(payload) + 1
            elif case == "reject-digest":
                values["digest"] = "sha256:" + "0" * 64
            elif case in {"partial-pending-success", "reject-partial-oversize", "reject-foreign-pending"}:
                values.update(
                    disposal_class="partial_pending_file",
                    expected_size=None,
                    maximum_partial_size=(
                        len(payload) + 1
                        if case == "partial-pending-success"
                        else len(payload)
                    ),
                    digest=None,
                )
                if case == "reject-partial-oversize":
                    values["maximum_partial_size"] = len(payload) - 1
                if case == "reject-foreign-pending":
                    values["components"] = (name,)

            expectation = _expectation(module, **values)
            rejected = case.startswith("reject-")
            if rejected:
                before = tree_snapshot(root)
                with pytest.raises(module.BirthSecureFSError) as caught:
                    session.dispose_transaction_object(expectation)
                assert caught.value.code == REJECTION_CODES[case]
                assert (root / name).exists()
                assert tree_snapshot(root) == before
                if case == "reject-partial-oversize":
                    accepted = dict(values)
                    accepted["maximum_partial_size"] = len(payload) + 1
                    result = session.dispose_transaction_object(
                        _expectation(module, **accepted)
                    )
                    assert result.identity == identity
                    assert result.removed is True
                    assert not (root / name).exists()
                    writer_name = (
                        "_write_all_posix"
                        if os.name == "posix"
                        else "_win_write_all"
                    )
                    with monkeypatch.context() as failed_creation:
                        failed_creation.setattr(
                            module,
                            writer_name,
                            lambda *_: (_ for _ in ()).throw(
                                OSError(5, "injected incomplete pending write")
                            ),
                        )
                        with pytest.raises(module.BirthSecureFSError):
                            session.create_file_exclusive(
                                (name,),
                                b"incomplete",
                                role=private_role(module),
                            )
                    write_private(root / name, b"foreign")
                    foreign_identity = object_identity(root / name, module)
                    foreign_expectation = _expectation(
                        module,
                        components=(name,),
                        identity=foreign_identity,
                        disposal_class="partial_pending_file",
                        expected_size=None,
                        maximum_partial_size=len(b"foreign") + 1,
                        digest=None,
                    )
                    foreign_before = tree_snapshot(root)
                    with pytest.raises(module.BirthSecureFSError) as unregistered:
                        session.dispose_transaction_object(foreign_expectation)
                    assert (
                        unregistered.value.code
                        == "birth_provisioning_recovery_ambiguous"
                    )
                    assert tree_snapshot(root) == foreign_before
            else:
                if os.name != "posix":
                    result = session.dispose_transaction_object(expectation)
                    assert result.identity == identity
                    assert result.kind == module._ObjectKind("regular_file")
                    assert result.removed is True
                    assert not (root / name).exists()
                    return
                calls: list[tuple[object, object]] = []
                real_unlink = os.unlink
                real_inventory = module._posix_inventory
                parent_before = session._inventory_state(())
                expected_parent = tuple(
                    item for item in parent_before if item.name != name
                )
                post_parent = []
                removed = False

                def traced_unlink(path, *, dir_fd=None):
                    nonlocal removed
                    calls.append((path, dir_fd))
                    result = real_unlink(path, dir_fd=dir_fd)
                    removed = True
                    return result

                def traced_inventory(fd, *args, **kwargs):
                    result = real_inventory(fd, *args, **kwargs)
                    value = os.fstat(fd)
                    root_value = root.stat()
                    if (
                        removed
                        and (value.st_dev, value.st_ino)
                        == (root_value.st_dev, root_value.st_ino)
                    ):
                        post_parent.append(result)
                    return result

                monkeypatch.setattr(os, "unlink", traced_unlink)
                monkeypatch.setattr(module, "_posix_inventory", traced_inventory)
                result = session.dispose_transaction_object(expectation)
                assert result.identity == identity
                assert result.kind == module._ObjectKind("regular_file")
                assert result.removed is True
                assert not (root / name).exists()
                assert calls and calls[-1][0] == name
                assert isinstance(calls[-1][1], int)
                assert post_parent == [expected_parent, expected_parent]
    finally:
        session.close()
