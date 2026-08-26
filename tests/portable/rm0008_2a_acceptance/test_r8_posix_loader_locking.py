from __future__ import annotations

import importlib
import errno
import os
import select
from pathlib import Path

import pytest

from ._support import (
    make_root,
    mkdir_public,
    open_session,
    provision_approval,
    provision_keystore,
    provision_semantic,
)


CASES = (
    "keystore-global-exclusive",
    "approval-global-exclusive",
    "semantic-global-exclusive",
    "keystore-local-exclusive",
    "semantic-use-after-close",
)


def _provision_authorities(root: Path):
    set_id = "0" * 64
    authority_sets = root / "authority-sets"
    authority_set = authority_sets / set_id
    approval = authority_set / "approval"
    semantic = authority_set / "semantic"
    mkdir_public(authority_sets)
    mkdir_public(authority_set)
    provision_keystore(authority_set / "admission")
    mkdir_public(approval)
    provision_approval(approval / "authority.json")
    provision_semantic(semantic)
    (semantic / "semantic.json").rename(semantic / "authority.json")
    return {
        "keystore": ("authority-sets", set_id, "admission"),
        "approval": ("authority-sets", set_id, "approval", "authority.json"),
        "semantic_authority": (
            "authority-sets",
            set_id,
            "semantic",
            "authority.json",
        ),
        "semantic_public": ("authority-sets", set_id, "semantic", "public"),
        "semantic_evidence": ("authority-sets", set_id, "semantic", "evidence"),
    }


def _call_loader(kind: str, session, paths):
    if kind == "keystore":
        module = importlib.import_module("executor_birth_keystore")
        loaded = module._load_birth_keystore_in_session(paths["keystore"], session)
        assert loaded.config_revision == 1
        return loaded
    if kind == "approval":
        module = importlib.import_module("executor_birth_approval_authority")
        loaded = module._load_approval_authority_in_session(paths["approval"], session)
        assert loaded.revision == 1
        return loaded
    module = importlib.import_module("executor_birth_semantic_authority")
    loaded = module._load_semantic_authority_in_session(
        paths["semantic_authority"],
        paths["semantic_public"],
        paths["semantic_evidence"],
        session,
    )
    assert set(loaded.verifier_keys) == {"review-key"}
    return loaded


def _global_contender(root: Path, kind: str, paths, ready: int, result: int) -> None:
    try:
        import fcntl

        original_flock = fcntl.flock
        contention_observed = False

        def observed_flock(fd: int, operation: int) -> object:
            nonlocal contention_observed
            try:
                return original_flock(fd, operation)
            except OSError as exc:
                if (
                    not contention_observed
                    and operation & fcntl.LOCK_NB
                    and exc.errno in {errno.EACCES, errno.EAGAIN}
                ):
                    contention_observed = True
                    os.write(ready, b"R")
                raise

        fcntl.flock = observed_flock
        with open_session(root) as session:
            with session.global_lock(exclusive=False, create=False, timeout=2.0):
                _call_loader(kind, session, paths)
            os.write(result, b"O")
    finally:
        os._exit(0)


@pytest.mark.parametrize("case", CASES, ids=CASES)
def test_loader_lock_contract(tmp_path: Path, case: str) -> None:
    root = make_root(tmp_path / "birth")
    paths = _provision_authorities(root)
    with open_session(root) as initializer:
        with initializer.global_lock(exclusive=True, create=True):
            pass

    if case == "semantic-use-after-close":
        session = open_session(root)
        with session.global_lock(exclusive=False, create=False):
            authority = _call_loader("semantic", session, paths)
        session.close()
        module = importlib.import_module("executor_birth_semantic_review")
        with pytest.raises(module.SemanticReviewError) as caught:
            authority.inputs_for(None)
        assert caught.value.code == "semantic_review_unavailable"
        assert "authority-sets" not in str(caught.value)
        return

    if case == "keystore-local-exclusive":
        with open_session(root) as holder:
            with holder.global_lock(exclusive=False, create=False):
                with holder.local_lock(paths["keystore"], exclusive=True, create=False):
                    ready_r, ready_w = os.pipe()
                    result_r, result_w = os.pipe()
                    pid = os.fork()
                    if pid == 0:
                        os.close(ready_r)
                        os.close(result_r)
                        _global_contender(root, "keystore", paths, ready_w, result_w)
                    os.close(ready_w)
                    os.close(result_w)
                    assert select.select([ready_r], [], [], 2.0)[0] == [ready_r]
                    assert os.read(ready_r, 1) == b"R"
                    os.close(ready_r)
                    assert select.select([result_r], [], [], 0.15)[0] == []
                assert select.select([result_r], [], [], 2.0)[0] == [result_r]
                assert os.read(result_r, 1) == b"O"
                os.close(result_r)
                waited, status = os.waitpid(pid, 0)
                assert waited == pid and os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0
        return

    kind = case.split("-", 1)[0]
    ready_r, ready_w = os.pipe()
    result_r, result_w = os.pipe()
    with open_session(root) as holder:
        with holder.global_lock(exclusive=True, create=False):
            pid = os.fork()
            if pid == 0:
                os.close(ready_r)
                os.close(result_r)
                _global_contender(root, kind, paths, ready_w, result_w)
            os.close(ready_w)
            os.close(result_w)
            assert select.select([ready_r], [], [], 2.0)[0] == [ready_r]
            assert os.read(ready_r, 1) == b"R"
            os.close(ready_r)
            assert select.select([result_r], [], [], 0.15)[0] == []
        assert select.select([result_r], [], [], 2.0)[0] == [result_r]
        assert os.read(result_r, 1) == b"O"
        os.close(result_r)
        waited, status = os.waitpid(pid, 0)
        assert waited == pid and os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0
