"""Native Sites audit history, keeping operational topology and closure witnesses."""
from dataclasses import replace
from datetime import timedelta

from executor_birth_retention import NodeState, RetentionError, RootKind
from install.birth_retention_jsonl import JournalEntry, _JsonlOwner
from install.birth_retention_maintenance import OwnerObject, _digest
from install.birth_retention_rotated_jsonl import _RotatedJournalOwner
from install.birth_retention_sqlite import _iso, _utc


_CLOSURE = frozenset({"session_close", "session_reap"})
_TOPOLOGY = frozenset({"session_open", "allowlist_change", "credential_origin_approval"})
_EVENTS = _CLOSURE | _TOPOLOGY | frozenset({
    "allowlist_auto_allow", "collection_backtrack", "collection_control_rejected",
    "collection_decision", "collection_navigation_rejected", "collection_observation",
    "collection_replay_observed", "cookie_observation", "goal_facet_unchanged",
    "landing_recovery", "login_backtrack", "login_phase", "login_user_action",
    "overlay_dismiss", "overlay_navigating_control", "session_reuse",
    "site_action", "stealth_denied_by_ceiling", "task_mandate_denied",
    "credential_use", "factor_resolution", "login_attempt", "origin_mismatch",
    "captcha_attempt", "act", "kill_switch",
})


def _persistent(record):
    # verified_site_topology considers the domain on *every* owner-bound event,
    # including events outside its three explicit branches. Preserve their order.
    return bool(record.get("owner_user_id")) or record["event"] in _TOPOLOGY


class _SitesJournal(_JsonlOwner):
    name = "sites_audit"
    node_type = "audit_segment"
    projection = None

    def _record_key(self, record):
        if (record.get("event") not in _EVENTS
                or any(type(record.get(field)) is not str
                       for field in ("ts", "owner", "session_id", "domain"))):
            raise ValueError("unknown Sites audit schema")
        _utc(record["ts"])
        # These fields would extend the native session-local audit contract.
        if any(field in record for field in ("job_id", "turn_id", "receipt_id")):
            raise ValueError("unreconciled Sites audit reference")
        return _digest(record)

    def _entries(self, custody, info, lines, records):
        entries = tuple(JournalEntry(OwnerObject(
            self.identity(key), version, NodeState.OPEN,
            _iso(_utc(items[0]["ts"])), None, roots=(RootKind.OPEN_AUDIT,),
        ), items) for key, (items, version) in self._groups(custody, info, lines, records).items())
        if self.projection is None:
            return entries
        result = []
        for entry in entries:
            projected = self.projection.get(entry.object.identity)
            if projected is None or projected.version != entry.object.version:
                raise RetentionError("retention_owner_changed", "Sites audit projection changed")
            result.append(JournalEntry(projected, entry.records))
        return tuple(result)


class _SitesAuditOwner(_RotatedJournalOwner):
    journal_type = _SitesJournal

    def _project(self, entries):
        sessions = {}
        for entry in entries:
            record = entry.records[0]
            sid = record["session_id"]
            if sid:
                sessions.setdefault(sid, []).append(entry)
        closed = {}
        bodies = {}
        for sid, history in sessions.items():
            for field in ("owner", "domain"):
                if len({entry.records[0][field] for entry in history if entry.records[0][field]}) > 1:
                    raise RetentionError("retention_inventory_incomplete", "ambiguous Sites session")
            markers = [entry for entry in history if entry.records[0]["event"] in _CLOSURE]
            if markers:
                end = max(_utc(entry.records[0]["ts"]) for entry in markers)
                # An event after closure may mean reuse or an incomplete history.
                if all(_utc(entry.records[0]["ts"]) <= end for entry in history):
                    closed[sid] = (end, tuple(entry.object.identity for entry in markers))
            bodies[sid] = any(not _persistent(entry.records[0])
                              and entry.records[0]["event"] not in _CLOSURE for entry in history)
        objects = []
        for entry in entries:
            obj, record = entry.object, entry.records[0]
            sid = record["session_id"]
            closure = closed.get(sid)
            marker = record["event"] in _CLOSURE
            if closure and not _persistent(record) and not (marker and bodies[sid]):
                end, witnesses = closure
                obj = replace(obj, state=NodeState.CLOSED, roots=(),
                              eligible_after=_iso(end + timedelta(days=90)),
                              references=() if marker else witnesses)
            objects.append(obj)
        return self.link_copies(objects)

    def inventory(self):
        return self._project(self.scan())

    def delete(self, identity, expected_version):
        # Two inventories: collect closed history first, its closure witnesses
        # only after every body is gone. Arbitrary receipt order remains safe.
        journal = self._dispatch(identity)
        journal.projection = {obj.identity: obj for obj in self._project(self.scan(recovery=True))}
        journal.delete(identity, expected_version)
