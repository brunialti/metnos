from __future__ import annotations

import importlib
import os
from pathlib import Path

import pytest

from ._support import (
    chown_other_uid,
    provision_approval,
    provision_keystore,
    provision_semantic,
    restore_owner,
    tree_snapshot,
)


CASES = (
    "keystore-external-local-only",
    "approval-public-other-uid",
    "semantic-public-other-uid",
    "keystore-legacy-no-mutation",
    "approval-legacy-no-mutation",
    "semantic-legacy-no-mutation",
)


@pytest.mark.parametrize("case", CASES, ids=CASES)
def test_posix_legacy_loader_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    keystore_root = tmp_path / "external-keystore"
    approval_file = tmp_path / "approval-authority.json"
    semantic_root = tmp_path / "semantic"
    provision_keystore(keystore_root)
    provision_approval(approval_file)
    semantic_value = provision_semantic(semantic_root)

    if case == "keystore-external-local-only":
        opened: list[str] = []
        real_open = os.open

        def traced_open(path, flags, mode=0o777, *, dir_fd=None):
            opened.append(os.fspath(path))
            return real_open(path, flags, mode, dir_fd=dir_fd)

        monkeypatch.setattr(os, "open", traced_open)
        loaded = importlib.import_module("executor_birth_keystore").load_birth_keystore(
            keystore_root
        )
        assert loaded.config_revision == 1
        assert all("provisioning-v1.lock" not in path for path in opened)
        assert not (tmp_path / "provisioning-v1.lock").exists()
        return

    if case == "approval-public-other-uid":
        chown_other_uid(iter((approval_file,)))
        try:
            loaded = importlib.import_module(
                "executor_birth_approval_authority"
            ).load_approval_authority(approval_file)
            assert loaded.revision == 1
        finally:
            restore_owner(iter((approval_file,)))
        return

    if case == "semantic-public-other-uid":
        owned_elsewhere = (
            semantic_root / "public" / "review.pub",
            semantic_root / "evidence",
        )
        chown_other_uid(iter(owned_elsewhere))
        try:
            loaded = importlib.import_module(
                "executor_birth_semantic_authority"
            ).load_semantic_authority(semantic_value, semantic_root)
            assert set(loaded.verifier_keys) == {"review-key"}
        finally:
            restore_owner(iter(owned_elsewhere))
        return

    if case == "keystore-legacy-no-mutation":
        target = keystore_root
        call = lambda: importlib.import_module(
            "executor_birth_keystore"
        ).load_birth_keystore(keystore_root)
    elif case == "approval-legacy-no-mutation":
        target = tmp_path
        call = lambda: importlib.import_module(
            "executor_birth_approval_authority"
        ).load_approval_authority(approval_file)
    else:
        target = semantic_root
        call = lambda: importlib.import_module(
            "executor_birth_semantic_authority"
        ).load_semantic_authority(semantic_value, semantic_root)
    before = tree_snapshot(target)
    result = call()
    assert result is not None
    assert tree_snapshot(target) == before
