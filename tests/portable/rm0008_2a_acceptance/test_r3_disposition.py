from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from ._support import (
    make_root,
    object_identity,
    open_session,
    private_role,
    secure_fs,
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
    digest=hashlib.sha256(b"payload").hexdigest(),
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
def test_dispose_transaction_object(tmp_path: Path, case: str) -> None:
    module = secure_fs()
    root = make_root(tmp_path / "birth")
    session = open_session(root)
    try:
        with session.global_lock(exclusive=True, create=True):
            if case == "reject-root-components":
                expectation = _expectation(
                    module,
                    components=(),
                    identity=object_identity(root, module),
                )
                before = tuple(root.iterdir())
                with pytest.raises(module.BirthSecureFSError):
                    session.dispose_transaction_object(expectation)
                assert tuple(root.iterdir()) == before
                return
            if case == "reject-absent":
                expectation = _expectation(
                    module,
                    components=("absent.bin",),
                    identity=module._ObjectIdentity("0", "0"),
                )
                with pytest.raises(module.BirthSecureFSError) as caught:
                    session.dispose_transaction_object(expectation)
                assert caught.value.code == "birth_provisioning_recovery_ambiguous"
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
                    with pytest.raises(module.BirthSecureFSError):
                        session.dispose_transaction_object(expectation)
                    assert directory.inventory() == ("child.bin",)
                else:
                    result = session.dispose_transaction_object(expectation)
                    assert result.identity == identity and result.removed is True
                    assert not (root / "empty-directory").exists()
                return

            payload = b"payload"
            name = "payload.pending" if "partial" in case else "payload.bin"
            if case == "reject-foreign-pending":
                name = "foreign.pending"
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
                "digest": hashlib.sha256(payload).hexdigest(),
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
                values["digest"] = "0" * 64
            elif case in {"partial-pending-success", "reject-partial-oversize", "reject-foreign-pending"}:
                values.update(
                    disposal_class="partial_pending_file",
                    expected_size=None,
                    maximum_partial_size=len(payload),
                    digest=None,
                )
                if case == "reject-partial-oversize":
                    values["maximum_partial_size"] = len(payload) - 1
                if case == "reject-foreign-pending":
                    values["components"] = (name,)

            expectation = _expectation(module, **values)
            rejected = case.startswith("reject-")
            if rejected:
                with pytest.raises(module.BirthSecureFSError):
                    session.dispose_transaction_object(expectation)
                assert (root / name).exists()
            else:
                result = session.dispose_transaction_object(expectation)
                assert result.identity == identity
                assert result.kind == module._ObjectKind("regular_file")
                assert result.removed is True
                assert not (root / name).exists()
    finally:
        session.close()
