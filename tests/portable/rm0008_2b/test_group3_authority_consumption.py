"""The installed authority registries are really consumed, not merely present.

Group 2 declared this one not proven: the set existed on disk, but nothing
showed that the running gate took its authorities from there.  These cells
change one identity in the installed set and observe the change arrive at the
core, which is the only evidence that means anything.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from . import support

pytestmark = pytest.mark.skipif(
    os.name == "nt", reason=support.POSIX_SCENARIO_ONLY_V1
)


def _prepared(tmp_path: Path, monkeypatch) -> Path:
    base = support.make_config(
        tmp_path, author=Ed25519PrivateKey.generate(), operator=True,
    )
    support.provision(monkeypatch, base)
    support.use_config(monkeypatch, base)
    return base


def test_the_sealed_authorities_come_from_the_installed_set(
    tmp_path: Path, monkeypatch,
):
    """Every identity the core will use is the one written under the root."""
    import executor_birth_prepared_root as door
    from executor_birth_keystore import raw_public_key

    base = _prepared(tmp_path, monkeypatch)
    sealed = door.load_sealed_authorities_v1()

    installed = support.installed_set(base)
    admission = json.loads((installed / "admission" / "keystore.json").read_bytes())
    assert sealed.admission.active_key_id == admission["active_key_id"]

    producers = sorted(item.name for item in (installed / "producers").iterdir())
    assert sorted(sealed.producers) == producers

    approval = json.loads((installed / "approval" / "authority.json").read_bytes())
    assert sealed.approval.revision == approval["revision"]
    assert sorted(sealed.approval.keys) == sorted(approval["keys"])
    assert sorted(sealed.approval.actors) == sorted(approval["actors"])

    # The author store is a separate root, and its active key travels too.
    author_public = raw_public_key(
        sealed.author.verifier_keys[sealed.author.active_key_id]
    )
    assert len(author_public) == 32


def test_what_the_operator_installs_is_what_the_gate_holds(
    tmp_path: Path, monkeypatch,
):
    """Consumption, demonstrated: change the input, and the core changes.

    Two roots are provisioned with different approval actors.  If the gate took
    its authorities from anywhere but the installed set, the two would agree.
    """
    import executor_birth_prepared_root as door

    def provisioned(name: str, actors) -> object:
        base = support.make_config(
            (tmp_path / name), author=Ed25519PrivateKey.generate(),
        )
        keys = {"review.pub": support.public_bytes(Ed25519PrivateKey.generate())}
        support.install_operator_input(
            base,
            approval=support.approval_document(actors=actors),
            semantic=support.semantic_document(tuple(keys)),
            keys=keys,
        )
        support.provision(monkeypatch, base)
        support.use_config(monkeypatch, base)
        return door.load_sealed_authorities_v1()

    one = provisioned("one", {
        "operator": {"key_ids": ["operator-key"], "scopes": ["birth"]},
    })
    two = provisioned("two", {
        "operator": {"key_ids": ["operator-key"], "scopes": ["birth"]},
        "reviewer": {"key_ids": ["operator-key"], "scopes": ["birth"]},
    })

    assert sorted(one.approval.actors) == ["operator"]
    assert sorted(two.approval.actors) == ["operator", "reviewer"]


def test_editing_an_installed_registry_stops_the_gate(
    tmp_path: Path, monkeypatch,
):
    """Stronger than reading it: the bytes are authenticated before use.

    The set document records the digest of every installed registry, so an edit
    after provisioning is refused as a mismatch instead of quietly becoming the
    new authority.
    """
    import executor_birth_prepared_root as door
    from executor_birth_prepared_set import PreparedSetError

    base = _prepared(tmp_path, monkeypatch)
    assert door.load_sealed_authorities_v1().approval.revision == 1

    document = support.installed_set(base) / "approval" / "authority.json"
    value = json.loads(document.read_bytes())
    value["revision"] = 2
    support.write(document, support.canonical_json(value), 0o644)

    with pytest.raises(PreparedSetError, match="birth_prepared_set_mismatch"):
        door.load_sealed_authorities_v1()


def test_a_registry_that_cannot_be_read_stops_the_gate(
    tmp_path: Path, monkeypatch,
):
    """A corrupt registry is a refusal, never an empty authority."""
    import executor_birth_prepared_root as door
    from executor_birth_prepared_set import PreparedSetError

    base = _prepared(tmp_path, monkeypatch)
    document = support.installed_set(base) / "approval" / "authority.json"
    support.write(document, b'{"schema_version": 1}', 0o644)

    with pytest.raises((PreparedSetError, door.PreparedRootError)):
        door.load_sealed_authorities_v1()
