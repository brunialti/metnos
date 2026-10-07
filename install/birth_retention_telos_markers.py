"""Physical Telos acceptance markers, read through the native consumer format.

Only the consumer's pre-dispatch invalid-input branch proves absence of work.
Successful, failed, redirected and candidate results require the enclosing
Birth/job inventory before they can close. A filename alone is not closure.
"""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import os
import re
import time

from executor_birth_canonical import _pairs_v1
from executor_birth_retention import NodeState, RetentionError, RootKind
from install.birth_retention_files import _PrivateFiles
from install.birth_retention_jsonl import _invalid_constant, _time
from install.birth_retention_maintenance import ObjectIdentity, OwnerObject
from install.birth_retention_sqlite import _iso, _utc


_PENDING = frozenset({"synt_pending", "change_pending", "pipeline_pending"})
_DIRECTORIES = _PENDING | {"synt_processed"}
_SIGNATURE = r"[A-Za-z0-9_-]{1,128}"
_PENDING_NAME = re.compile(rf"({_SIGNATURE})\.json")
_PROCESSED_NAME = re.compile(rf"({_SIGNATURE})\.(invalid|success|candidate|noop|failed)(\.result)?\.json")
_INVALID_RESULT = {"error": "missing expected_name or intent"}


class _TelosMarkerOwner(_PrivateFiles):
    name = "telos_acceptance_markers"

    def __init__(self, *, root, require_exclusion, owner):
        super().__init__(root=root, require_exclusion=require_exclusion, owner=owner,
                         private_directory=False, file_modes=frozenset({0o600, 0o640, 0o644}),
                         copied_timestamps=True)

    @staticmethod
    def _parts(local_id):
        if type(local_id) is not str:
            raise RetentionError("retention_owner_invalid", "Telos marker identity")
        parts = local_id.split("/")
        if len(parts) != 2 or parts[0] not in _DIRECTORIES:
            raise RetentionError("retention_owner_invalid", "Telos marker identity")
        match = (_PENDING_NAME if parts[0] in _PENDING else _PROCESSED_NAME).fullmatch(parts[1])
        if match is None:
            raise RetentionError("retention_inventory_incomplete", "unknown Telos marker")
        return parts[0], parts[1], match

    def identity(self, local_id):
        self._parts(local_id)
        return ObjectIdentity(self.name, self.root.as_uri(), "proposal", local_id)

    def _identity(self, identity):
        if identity != self.identity(identity.local_id):
            raise RetentionError("retention_owner_invalid", "foreign Telos marker")
        return self._parts(identity.local_id)

    def _read(self, directory, custody, identity):
        category, name, match = self._identity(identity)
        observed = self._read_file(directory, custody, name, identity,
                                   include_payload=True, max_bytes=128 << 20)
        if observed is None:
            return None
        try:
            payload = json.loads(observed[4], object_pairs_hook=_pairs_v1,
                                 parse_constant=_invalid_constant)
            if type(payload) is not dict:
                raise ValueError("marker payload")
            result = category == "synt_processed" and match.group(3) is not None
            timestamp = observed[3]
            if not result:
                if payload.get("sig") != match.group(1) or type(payload.get("prop_id")) is not str:
                    raise ValueError("marker binding")
                timestamp = max(timestamp, _time(payload.get("ts")))
            invalid = category == "synt_processed" and match.group(2) == "invalid"
            closed = invalid and (payload == _INVALID_RESULT if result else (
                not payload.get("expected_name") or not payload.get("intent")))
            # Result files can exist without the renamed marker after native
            # partial writes or collection. Their own invalid result still
            # records the same pre-dispatch branch; no other status suffices.
            obj = OwnerObject(identity, observed[1], NodeState.CLOSED if closed else NodeState.OPEN,
                              _iso(timestamp), _iso(timestamp + timedelta(days=90)) if closed else None,
                              roots=() if closed else (RootKind.OPEN_REVISION,))
            return obj, match.group(1), payload
        except (ValueError, TypeError, RecursionError) as exc:
            raise RetentionError("retention_owner_state_invalid", "Telos marker payload") from exc

    def scan(self):
        entries, deadline = {}, time.monotonic() + 15
        try:
            with self._directory() as (root, _):
                categories = self._names(root)
                if not set(categories) <= _DIRECTORIES:
                    raise RetentionError("retention_inventory_incomplete", "unknown Telos marker directory")
                for category in categories:
                    with self._directory(category) as (directory, custody):
                        names = self._names(directory)
                        for name in names:
                            if len(entries) >= 100_000 or time.monotonic() > deadline:
                                raise RetentionError("retention_inventory_incomplete", "Telos marker budget")
                            identity = self.identity(f"{category}/{name}")
                            entry = self._read(directory, custody, identity)
                            if entry is None:
                                raise RetentionError("retention_owner_changed", "Telos marker disappeared")
                            entries[identity] = entry
                        if self._names(directory) != names:
                            raise RetentionError("retention_owner_changed", "Telos marker directory changed")
                if self._names(root) != categories:
                    raise RetentionError("retention_owner_changed", "Telos marker namespaces changed")
        except FileNotFoundError:
            if self.root.exists() or entries:
                raise RetentionError("retention_owner_changed", "Telos marker directory disappeared") from None
        if time.monotonic() > deadline:
            raise RetentionError("retention_inventory_incomplete", "Telos marker budget")
        return entries

    def inventory(self):
        entries = self.scan()
        groups, refs = {}, {identity: set() for identity in entries}
        for identity, (_, signature, _) in entries.items():
            groups.setdefault(signature, set()).add(identity)
        for identities in groups.values():
            hub = min(identities, key=lambda identity: identity.key.node_id)
            for identity in identities - {hub}:
                refs[identity].add(hub)
                refs[hub].add(identity)
        if entries != self.scan():
            raise RetentionError("retention_owner_changed", "Telos marker inventory changed")
        return tuple(replace(entry[0], references=tuple(sorted(refs[identity], key=lambda item: item.key.node_id)))
                     for identity, entry in sorted(entries.items(), key=lambda item: item[0].key.node_id))

    def version(self, identity):
        category, _, _ = self._identity(identity)
        try:
            with self._directory(category) as (directory, custody):
                entry = self._read(directory, custody, identity)
                if entry is None:
                    os.fsync(directory)
                return None if entry is None else entry[0].version
        except FileNotFoundError:
            return None

    def delete(self, identity, expected_version):
        category, name, _ = self._identity(identity)
        try:
            with self._directory(category) as (directory, custody):
                entry = self._read(directory, custody, identity)
                if entry is not None:
                    obj = entry[0]
                    if obj.version != expected_version:
                        raise RetentionError("retention_owner_changed", "Telos marker version")
                    if obj.state is not NodeState.CLOSED or _utc(obj.eligible_after) >= datetime.now(timezone.utc):
                        raise RetentionError("retention_owner_state_invalid", "Telos marker not closed or expired")
                    self.require_exclusion()
                    os.unlink(name, dir_fd=directory)
                os.fsync(directory)
        except FileNotFoundError:
            with self._directory() as (root, _):
                os.fsync(root)
