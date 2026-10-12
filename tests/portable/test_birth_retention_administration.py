"""Coordinator protocol with real plans, signed journals and physical effects.

FileOwner/SqliteOwner are the existing recovery fixtures. Installed exclusion,
root custody, receipt-key acquisition and the complete census are explicit
seams: these tests do not certify installed owner or namespace coverage.
"""
from contextlib import contextmanager
from dataclasses import replace
from io import BytesIO
import json
import sqlite3
import sys
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from executor_birth_prepared_root import PreparedRootError
from executor_birth_retention import NodeState, RetentionError, RootKind
from install import birth_retention_administration as administration
from install.birth_retention_maintenance import Maintenance, OwnerObject
from test_birth_retention_maintenance import (
    FileOwner, Interrupted, NOW, OLD, RUN, SqliteOwner, interrupt_at, write_holds,
)


pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="native journal custody is Linux-only")


def _stdin(monkeypatch, document):
    raw = document if type(document) is bytes else json.dumps(document).encode("utf-8") + b"\n"
    monkeypatch.setattr(administration.sys, "stdin", SimpleNamespace(buffer=BytesIO(raw)))


def _forbidden(*args, **kwargs):
    pytest.fail("operation must not be reached")


@pytest.mark.parametrize(("stage", "raw"), [
    ("plan", b'{"public_sources":[],"extra":true}\n'),
    ("plan", b'{"public_sources":[],"public_sources":[]}\n'),
    ("plan", b"[]\n"),
    ("plan", b"\xff\n"),
    ("plan", {"public_sources": {}}),
    ("plan", {"public_sources": [["runtime/source.py", "!"]]}),
    ("plan", {"public_sources": [["../source.py", "Ynl0ZXM="]]}),
    ("collect", {"public_sources": [], "expected_inventory": "bad",
                 "max_objects": 1, "max_seconds": 1}),
    ("resume", {"public_sources": [], "max_objects": True, "max_seconds": 1}),
    ("resume", {"public_sources": [], "max_objects": 0, "max_seconds": 1}),
    ("resume", {"public_sources": [], "max_objects": 1, "max_seconds": float("nan")}),
    ("resume", {"public_sources": [], "max_objects": 1, "max_seconds": 301}),
    ("holds", {"holds": ["sha256:" + "1" * 64] * 2, "expected_version": None}),
])
def test_closed_invalid_input_is_rejected_before_exclusion(monkeypatch, stage, raw):
    from install import birth_retention_exclusion

    _stdin(monkeypatch, raw)
    monkeypatch.setattr(birth_retention_exclusion, "administrative_retention_exclusion_v1", _forbidden)
    monkeypatch.setattr(administration, "_maintenance_root", _forbidden)
    monkeypatch.setattr(administration, "_receipt_keys", _forbidden)
    with pytest.raises((RetentionError, PreparedRootError)):
        administration.run_retention(stage)


class _CoordinatorFixture:
    def __init__(self, tmp_path, monkeypatch, *, extra_files=0):
        import executor_birth_authority_files as authority_files
        from install import birth_retention_exclusion

        tmp_path.chmod(0o755)
        self.root = tmp_path / "retention-maintenance-v1"
        self.root.mkdir(mode=0o755)
        self.root.chmod(0o755)
        write_holds(self.root)
        self.owners = {"files": FileOwner(tmp_path / "file-owner"),
                       "database": SqliteOwner(tmp_path)}
        for index in range(extra_files):
            name = "extra-file-" + str(index)
            owner = FileOwner(tmp_path / name)
            owner.identity = replace(owner.identity, owner=name)
            self.owners[name] = owner
        self.private = Ed25519PrivateKey.generate()
        self.public = {"receipt-key": self.private.public_key()}
        self.events = []
        self.active = False
        self.fail_recovery = False
        self.context = SimpleNamespace(require_exclusion=self.full,
                                       require_held_exclusion=self.held)
        self.census = SimpleNamespace(owners=self.owners, inventory=self.inventory,
                                     observed_roots=lambda *args, **kwargs: frozenset(RootKind))

        native_holds = administration.read_holds
        native_no_pending = authority_files._require_no_retention_maintenance_at_v1

        def isolated_root(*, create=False):
            assert create is False
            return self.root

        def isolated_holds(root, *, root_owned):
            assert root == self.root and root_owned is True
            return native_holds(root, root_owned=False)

        def no_pending(root, *, root_owned):
            assert root == tmp_path and root_owned is True
            return native_no_pending(root, root_owned=False)

        fixture = self

        class ObservedMaintenance(Maintenance):
            def __init__(self, root, *, root_owned, require_exclusion):
                assert root == fixture.root and root_owned is True
                assert require_exclusion == fixture.context.require_held_exclusion
                super().__init__(root, root_owned=False, require_exclusion=require_exclusion)

            def begin(self, *args, **kwargs):
                assert fixture.events[-1] == "full"
                fixture.events.append("begin.enter")
                try:
                    return super().begin(*args, **kwargs)
                finally:
                    fixture.events.append("begin.exit")

            def resume(self, *args, **kwargs):
                assert fixture.events[-1] == "full"
                fixture.events.append("resume.enter")
                try:
                    return super().resume(*args, **kwargs)
                finally:
                    fixture.events.append("resume.exit")

            def finish(self, *args, **kwargs):
                assert fixture.events[-1] == "full"
                fixture.events.append("finish.enter")
                try:
                    return super().finish(*args, **kwargs)
                finally:
                    fixture.events.append("finish.exit")

        self.maintenance_type = ObservedMaintenance
        monkeypatch.setattr(authority_files, "DEFAULT_OWNERSHIP_ROOT_V1", tmp_path)
        monkeypatch.setattr(authority_files, "_require_no_retention_maintenance_at_v1", no_pending)
        monkeypatch.setattr(birth_retention_exclusion, "administrative_retention_exclusion_v1", self.exclusion)
        monkeypatch.setattr(administration, "_maintenance_root", isolated_root)
        monkeypatch.setattr(administration, "read_holds", isolated_holds)
        monkeypatch.setattr(administration, "Maintenance", ObservedMaintenance)
        monkeypatch.setattr(administration, "_load_inventory", self.load_inventory)
        monkeypatch.setattr(administration, "_receipt_keys", self.receipt_keys)

    def held(self):
        assert self.active, "exclusion must remain held"
        self.events.append("held")

    def full(self):
        assert self.active, "exclusion must remain held"
        self.events.append("full")

    @contextmanager
    def exclusion(self):
        assert not self.active
        self.active = True
        self.full()
        try:
            yield self.context
        finally:
            self.full()
            self.active = False

    def inventory(self, *, context=None):
        assert context is self.context
        self.held()
        result = []
        for owner in self.owners.values():
            version = owner.version(owner.identity)
            if version is not None:
                result.append(OwnerObject(owner.identity, version, NodeState.CLOSED, OLD, OLD))
        return tuple(result)

    def load_inventory(self, context, public_sources):
        assert context is self.context and public_sources == ()
        return self.census, self.recover

    def receipt_keys(self, *, signing):
        assert self.active
        self.events.append("keys.sign" if signing else "keys.verify")
        return ("receipt-key", self.private, self.public) if signing else (None, None, self.public)

    def recover(self):
        self.events.append("recovery")
        self.full()
        if self.fail_recovery:
            raise RetentionError("retention_inventory_incomplete", "isolated recovery readback")
        assert self.inventory(context=self.context) == ()
        assert all(owner.count() == 1 for owner in self.owners.values())


def test_stale_inventory_digest_never_acquires_signer_or_begins_journal(tmp_path, monkeypatch):
    fixture = _CoordinatorFixture(tmp_path, monkeypatch)
    _stdin(monkeypatch, {"public_sources": []})
    reviewed = administration.run_retention("plan")
    changed = fixture.owners["files"]
    changed.path.write_bytes(b"physical version changed after the reviewed plan")
    monkeypatch.setattr(administration, "_receipt_keys", _forbidden)
    monkeypatch.setattr(fixture.maintenance_type, "begin", _forbidden)
    monkeypatch.setattr(fixture.maintenance_type, "resume", _forbidden)
    monkeypatch.setattr(fixture.maintenance_type, "finish", _forbidden)
    _stdin(monkeypatch, {"public_sources": [], "expected_inventory": reviewed["inventory_digest"],
                         "max_objects": 10, "max_seconds": 20})
    with pytest.raises(RetentionError, match="retention_state_changed"):
        administration.run_retention("collect")
    assert changed.path.read_bytes() == b"physical version changed after the reviewed plan"
    assert all(owner.count() == 0 for owner in fixture.owners.values())
    assert {path.name for path in fixture.root.iterdir()} == {"holds.json"}


@pytest.mark.parametrize("extra_files", [0, 3])
def test_full_checks_stay_at_phase_boundaries_while_journal_uses_held_guard(
        tmp_path, monkeypatch, extra_files):
    fixture = _CoordinatorFixture(tmp_path, monkeypatch, extra_files=extra_files)
    _stdin(monkeypatch, {"public_sources": []})
    reviewed = administration.run_retention("plan")
    fixture.events.clear()
    _stdin(monkeypatch, {"public_sources": [], "expected_inventory": reviewed["inventory_digest"],
                         "max_objects": 10, "max_seconds": 20})
    outcome = administration.run_retention("collect")
    assert outcome["maintenance"]["completed"] == len(fixture.owners)
    assert outcome["recovery"]["deleted"] == len(fixture.owners)
    assert outcome["recovery"]["preserved"] == 0
    assert fixture.events.count("keys.sign") == 1
    # Same eight full boundaries for two or five objects; none per intent.
    assert fixture.events.count("full") == 8
    assert fixture.events.count("held") >= 3 * len(fixture.owners)
    for phase in ("begin", "resume", "finish"):
        start, end = fixture.events.index(phase + ".enter"), fixture.events.index(phase + ".exit")
        assert fixture.events[start - 1] == "full"
        assert fixture.events[end + 1] == "full"
        assert fixture.events[start:end].count("full") == (1 if phase == "finish" else 0)
    assert not (fixture.root / "active.json").exists()
    assert all(owner.version(owner.identity) is None and owner.count() == 1
               for owner in fixture.owners.values())


def _journal_intents(root):
    with sqlite3.connect(root / (RUN[7:] + ".sqlite")) as db:
        return db.execute("SELECT identity,owner_version,authentication FROM intents ORDER BY position").fetchall()


def test_resume_uses_original_signed_journal_and_keeps_barrier_until_recovery(tmp_path, monkeypatch):
    fixture = _CoordinatorFixture(tmp_path, monkeypatch)
    with fixture.exclusion():
        candidates = fixture.inventory(context=fixture.context)
        maintenance = Maintenance(fixture.root, root_owned=False, require_exclusion=fixture.held)
        maintenance.begin(candidates, run_id=RUN, observed_at=NOW, key_id="receipt-key",
                          private_key=fixture.private, public_keys=fixture.public, owners=fixture.owners)
        original = _journal_intents(fixture.root)
        with pytest.raises(Interrupted, match="after_effect"):
            maintenance.resume(fixture.owners, public_keys=fixture.public, max_objects=10,
                               max_seconds=20, crash=interrupt_at("after_effect"))
    assert sum(owner.count() for owner in fixture.owners.values()) == 1
    marker = (fixture.root / "active.json").read_bytes()
    monkeypatch.setattr(administration, "_projection", _forbidden)
    monkeypatch.setattr(fixture.maintenance_type, "begin", _forbidden)
    fixture.events.clear()
    fixture.fail_recovery = True
    document = {"public_sources": [], "max_objects": 10, "max_seconds": 20}
    _stdin(monkeypatch, document)
    with pytest.raises(RetentionError, match="isolated recovery readback"):
        administration.run_retention("resume")
    assert (fixture.root / "active.json").read_bytes() == marker
    assert _journal_intents(fixture.root) == original
    assert all(owner.version(owner.identity) is None and owner.count() == 1
               for owner in fixture.owners.values())
    fixture.fail_recovery = False
    _stdin(monkeypatch, document)
    outcome = administration.run_retention("resume")
    assert outcome["maintenance"]["completed"] == 0
    assert outcome["recovery"] == {"run_id": RUN, "deleted": len(fixture.owners), "preserved": 0}
    assert _journal_intents(fixture.root) == original
    assert fixture.events.count("keys.verify") == 2
    assert "keys.sign" not in fixture.events
    assert all(owner.count() == 1 for owner in fixture.owners.values())
    assert not (fixture.root / "active.json").exists()
    with sqlite3.connect(fixture.root / (RUN[7:] + ".sqlite")) as db:
        assert dict(db.execute("SELECT name,value FROM metadata"))["state"] == "complete"
