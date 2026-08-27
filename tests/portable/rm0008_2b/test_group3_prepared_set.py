"""Group 3 reads back what group 2 prepared, and never trusts the marker.

Only the claim of this reader is exercised here: the set on disk must agree
with itself.  What group 2 already certified — the primitive, the journal, the
canonical documents — is not re-tested.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from executor_birth_prepared_set import (
    PREPARED_STATE_V1, PreparedSetError, load_prepared_set_v1,
)

from . import support

pytestmark = pytest.mark.skipif(
    os.name == "nt", reason=support.WINDOWS_BLOCKER_V1
)


def _prepared(tmp_path: Path, monkeypatch):
    base = support.make_config(
        tmp_path, author=Ed25519PrivateKey.generate(), operator=True,
    )
    support.provision(monkeypatch, base)
    return base


def _read(monkeypatch, base: Path):
    session = support.open_layout(monkeypatch, base).birth_session
    with session:
        with session.global_lock(exclusive=False, create=False):
            return load_prepared_set_v1(session)


def _rewrite(path: Path, mutate) -> None:
    document = json.loads(path.read_bytes())
    mutate(document)
    support.write(path, support.canonical_json(document), 0o644)


def test_a_prepared_set_reads_back_and_stays_inactive(
    tmp_path: Path, monkeypatch,
):
    base = _prepared(tmp_path, monkeypatch)
    observed = _read(monkeypatch, base)

    marker = json.loads(support.installed_marker(base).read_bytes())
    document = json.loads(
        (support.installed_set(base) / "set.json").read_bytes()
    )
    assert observed.set_id == marker["set_id"] == document["set_id"]
    assert observed.state == PREPARED_STATE_V1
    assert observed.author_active_key_id == document["author_active_key_id"]
    assert observed.admission_active_key_id == document["admission_active_key_id"]
    assert len(observed.producer_keys) == 11
    assert observed.prepared_admission_context_id.startswith("sha256:")
    with pytest.raises(TypeError):
        observed.producer_keys["x"] = None


@pytest.mark.parametrize("case", [
    "marker-set-id", "marker-digest", "marker-state", "set-author-key",
    "set-context-digest",
])
def test_a_set_that_disagrees_with_itself_is_refused(
    tmp_path: Path, monkeypatch, case: str,
):
    base = _prepared(tmp_path, monkeypatch)
    marker = support.installed_marker(base)
    document = support.installed_set(base) / "set.json"

    if case == "marker-set-id":
        _rewrite(marker, lambda item: item.update(set_id="0" * 64))
    elif case == "marker-digest":
        _rewrite(marker, lambda item: item.update(set_json_sha256="0" * 64))
    elif case == "marker-state":
        _rewrite(marker, lambda item: item.update(state="active"))
    elif case == "set-author-key":
        _rewrite(document, lambda item: item.update(author_active_key_id="other"))
    else:
        _rewrite(
            document, lambda item: item.update(context_material_sha256="0" * 64)
        )

    with pytest.raises(PreparedSetError) as error:
        _read(monkeypatch, base)
    assert error.value.code in {
        "birth_prepared_set_invalid",
        "birth_prepared_set_mismatch",
        "birth_prepared_set_unavailable",
    }
    assert error.value.__cause__ is None


def test_the_reader_opens_nothing_of_its_own():
    """The authority to reach the filesystem stays where it was granted."""
    import inspect

    import executor_birth_prepared_set as module

    source = inspect.getsource(module)
    for forbidden in (
        "_adopt_authenticated_root", "_open_win_root", "_open_posix_root",
        "_open_legacy_root_session", "create_file_exclusive",
        "create_directory_exclusive", "rename_no_replace",
        "dispose_transaction_object", "birth_authority_provisioner",
    ):
        assert forbidden not in source, forbidden


def test_the_runtime_door_is_read_only(tmp_path: Path, monkeypatch):
    """The runtime opens its own root, and only to read it."""
    import executor_birth_prepared_root as door

    base = _prepared(tmp_path, monkeypatch)
    support.use_config(monkeypatch, base)
    observed = door.read_prepared_set_v1()
    expected = _read(monkeypatch, base)
    assert observed == expected

    session = door.open_prepared_root_session_v1()
    with session:
        with pytest.raises(Exception):
            session.create_directory_exclusive(
                ("author-root-v1",), role=None,
            )


def test_an_absent_prepared_root_is_named_not_guessed(tmp_path: Path, monkeypatch):
    import executor_birth_prepared_root as door

    base = support.make_config(tmp_path)
    support.use_config(monkeypatch, base)
    import shutil

    shutil.rmtree(base / "birth")
    with pytest.raises(door.PreparedRootError) as error:
        door.read_prepared_set_v1()
    assert error.value.code.startswith("birth_provisioning_")
    assert error.value.__cause__ is None


def test_the_material_is_rebuilt_from_the_distribution_not_trusted(
    tmp_path: Path, monkeypatch,
):
    """Section 9.4: the runtime does not take the recorded description on faith."""
    import executor_birth_prepared_root as door
    from executor_birth_prepared_set import PreparedSetError

    base = _prepared(tmp_path, monkeypatch)
    support.use_config(monkeypatch, base)
    assert door.read_prepared_set_v1().set_id

    # One byte of the installed distribution changes: the description recorded
    # in the set no longer matches what the code would produce today.
    import importlib

    runtime_config = importlib.import_module("config")
    changed = Path(runtime_config.PATH_RUNTIME) / "vocab.py"
    changed.write_bytes(changed.read_bytes() + b"\n# changed\n")
    support.apply_profile(changed, directory=False, private=False)

    with pytest.raises(PreparedSetError) as error:
        door.read_prepared_set_v1()
    assert error.value.code == "birth_prepared_set_mismatch"
