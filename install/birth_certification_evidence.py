"""Administrator-owned F5 evidence chronology; never an activation authority.

The external observation owner supplies real harness results and review proofs.
This module preserves them and detects incomplete or inconsistent chronology.
Only the subsequent certifier can authenticate the full installation/frontier
and sign a qualification. Ordinary Birth does not open this database.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import wraps
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat

from executor_birth_authority_files import (
    DEFAULT_OWNERSHIP_ROOT_V1, OwnershipAuthorityError, _directory_metadata,
    _managed_authority_platform_supported_v1, _root_owned_chain,
)
from executor_birth_canonical import decode_canonical_ascii_v1, encode_canonical_ascii_v1
from install.birth_ownership_authority_provisioner import _provisioning_lock, _sync_directory


DIRECTORY_V1 = "certification-evidence-v1"
MAX_RECORD_BYTES = 1024 * 1024
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
MAX_EVENTS = 10_000
_DOMAIN = b"metnos.executor-birth.certification-evidence/v1\0"
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_BASE = {"installation_id", "head_id", "source_id", "catalog_id", "harness_id"}
_FIELDS = {
    "census": {"scope_id", "sources", "review", "findings"},
    "defect_opened": {"finding_id", "evidence"},
    "defect_closed": {"finding_id", "opening", "repair", "verification", "review"},
    "profile": _BASE | {"manifest", "cases"},
    "cycle_started": {"profile"},
    "cycle_finished": {"start", "results", "turns"},
    "cycle_interrupted": {"start"},
}
_DDL = (
    "CREATE TABLE artifacts (digest TEXT PRIMARY KEY, body BLOB NOT NULL)",
    "CREATE TABLE events (sequence INTEGER PRIMARY KEY, body BLOB NOT NULL, digest TEXT NOT NULL UNIQUE)",
    *(f"CREATE TRIGGER {table}_{operation.lower()} BEFORE {operation} ON {table} "
      "BEGIN SELECT RAISE(ABORT, 'immutable certification evidence'); END"
      for table in ("artifacts", "events") for operation in ("UPDATE", "DELETE")),
)


class EvidenceError(ValueError):
    """A closed administrative diagnostic; not a user-facing message."""

    def __init__(self, detail: str):
        self.code, self.detail = "birth_certification_evidence_invalid", detail
        super().__init__(f"{self.code}: {detail}")


def _hash(raw: bytes, *, domain: bytes = b"") -> str:
    return "sha256:" + hashlib.sha256(domain + raw).hexdigest()


def _identifier(value: object) -> str:
    if type(value) is not str or not value or len(value.encode("utf-8")) > 256 or value.strip() != value or "\0" in value:
        raise EvidenceError("identifier")
    return value


def _digest(value: object) -> str:
    if type(value) is not str or _DIGEST.fullmatch(value) is None:
        raise EvidenceError("digest")
    return value


def _json(raw: bytes):
    try:
        return decode_canonical_ascii_v1(raw, maximum=MAX_RECORD_BYTES)
    except (ValueError, TypeError, RecursionError) as exc:
        raise EvidenceError("canonical document") from exc


@dataclass(frozen=True)
class EvidenceFrontierV1:
    """An observed ledger frontier, explicitly not a signed qualification."""

    head: str | None
    event_count: int
    census_scope: str | None
    open_findings: tuple[str, ...]
    profile: str | None
    consecutive_successes: tuple[str, ...]
    pending_cycle: str | None


@dataclass
class _State:
    head: str | None = None
    count: int = 0
    scope: str | None = None
    findings: dict[str, str | None] = field(default_factory=dict)
    profile: str | None = None
    cases: dict[str, dict] = field(default_factory=dict)
    pending: str | None = None
    successes: tuple[str, ...] = ()
    turns: set[str] = field(default_factory=set)


def _transaction(method):
    @wraps(method)
    def operation(self, *args, **kwargs):
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            return method(self, *args, **kwargs)
        except BaseException:
            self._connection.rollback()
            self._state = _Evidence(self._connection)._state
            raise
    return operation


class _Evidence:
    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection
        self._state = _State()
        for table in ("artifacts", "events"):
            if connection.execute(f"SELECT coalesce(sum(length(body)),0) FROM {table}").fetchone()[0] > MAX_ARTIFACT_BYTES:
                raise EvidenceError("storage budget")
        rows = connection.execute("SELECT sequence, body, digest FROM events ORDER BY sequence LIMIT ?", (MAX_EVENTS + 1,)).fetchall()
        if len(rows) > MAX_EVENTS:
            raise EvidenceError("event limit")
        for sequence, raw, digest in rows:
            if sequence != self._state.count + 1 or type(raw) is not bytes or digest != _hash(raw, domain=_DOMAIN):
                raise EvidenceError("event identity")
            self._advance(_json(raw), digest)

    @property
    def frontier(self) -> EvidenceFrontierV1:
        state = self._state
        return EvidenceFrontierV1(state.head, state.count, state.scope,
                                  tuple(sorted(key for key, value in state.findings.items() if value is not None)),
                                  state.profile, state.successes, state.pending)

    def _artifact(self, digest: str) -> bytes:
        _digest(digest)
        row = self._connection.execute("SELECT body FROM artifacts WHERE digest=?", (digest,)).fetchone()
        if row is None or type(row[0]) is not bytes or not row[0] or len(row[0]) > MAX_RECORD_BYTES or _hash(row[0]) != digest:
            raise EvidenceError("artifact missing or changed")
        return row[0]

    def _put(self, raw: bytes) -> str:
        if type(raw) is not bytes or not raw or len(raw) > MAX_RECORD_BYTES:
            raise EvidenceError("artifact size")
        digest = _hash(raw)
        self._connection.execute("INSERT OR IGNORE INTO artifacts VALUES (?, ?)", (digest, raw))
        if self._artifact(digest) != raw:
            raise EvidenceError("artifact collision")
        total = self._connection.execute("SELECT coalesce(sum(length(body)),0) FROM artifacts").fetchone()[0]
        if total > MAX_ARTIFACT_BYTES:
            raise EvidenceError("artifact budget")
        return digest

    def _advance(self, event: dict, digest: str) -> None:
        state = self._state
        if (type(event) is not dict or set(event) != {"schema_version", "sequence", "previous", "kind", "payload"}
                or type(event["schema_version"]) is not int or event["schema_version"] != 1
                or type(event["sequence"]) is not int or event["sequence"] != state.count + 1
                or event["previous"] != state.head or type(event["kind"]) is not str
                or event["kind"] not in _FIELDS):
            raise EvidenceError("event sequence or schema")
        kind, payload = event["kind"], event["payload"]
        if type(payload) is not dict or set(payload) != _FIELDS[kind]:
            raise EvidenceError("payload schema")
        if state.scope is None and kind != "census":
            raise EvidenceError("census required")
        if state.pending is not None and kind not in {"cycle_finished", "cycle_interrupted"}:
            raise EvidenceError("cycle still open")
        if kind == "census":
            if state.count or type(payload["sources"]) is not list or not payload["sources"] or type(payload["findings"]) is not dict:
                raise EvidenceError("initial census")
            _digest(payload["scope_id"])
            if len(set(map(_digest, payload["sources"]))) != len(payload["sources"]):
                raise EvidenceError("duplicate census source")
            for reference in (*payload["sources"], payload["review"], *payload["findings"].values()):
                self._artifact(reference)
            for finding in payload["findings"]:
                _identifier(finding)
            state.scope = payload["scope_id"]
            state.findings = dict.fromkeys(payload["findings"], digest)
        elif kind == "defect_opened":
            finding = _identifier(payload["finding_id"])
            if state.findings.get(finding) is not None:
                raise EvidenceError("finding already open")
            self._artifact(payload["evidence"])
            state.findings[finding] = digest
        elif kind == "defect_closed":
            finding = _identifier(payload["finding_id"])
            if state.findings.get(finding) is None or payload["opening"] != state.findings[finding]:
                raise EvidenceError("stale finding closure")
            for reference in (payload["repair"], payload["verification"], payload["review"]):
                self._artifact(reference)
            state.findings[finding] = None
        elif kind == "profile":
            for name in _BASE:
                _digest(payload[name])
            manifest = _json(self._artifact(payload["manifest"]))
            cases = _json(self._artifact(payload["cases"]))
            if type(manifest) is not dict or type(cases) is not list or not cases:
                raise EvidenceError("profile documents")
            selected = {}
            for case in cases:
                if type(case) is not dict:
                    raise EvidenceError("case schema")
                case_id = _identifier(case.get("case_id"))
                probes = case.get("postcondition_probes")
                if case_id in selected or type(probes) is not list or not probes or len(set(map(_identifier, probes))) != len(probes):
                    raise EvidenceError("case identity or probes")
                selected[case_id] = case
            # Match the existing external oracle's exact matrix framing.
            matrix = "".join(json.dumps(case, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for case in cases).encode("utf-8")
            if manifest.get("case_matrix_sha256") != hashlib.sha256(matrix).hexdigest():
                raise EvidenceError("profile matrix binding")
            state.profile, state.cases, state.successes = digest, selected, ()
        elif kind == "cycle_started":
            if state.profile is None or payload["profile"] != state.profile:
                raise EvidenceError("cycle profile")
            state.pending = digest
        else:
            if state.pending is None or payload["start"] != state.pending:
                raise EvidenceError("cycle start binding")
            success = kind == "cycle_finished" and self._results_pass(payload)
            state.successes = (*state.successes[-1:], state.pending) if success else ()
            state.pending = None
        state.count, state.head = event["sequence"], digest

    def _results_pass(self, payload: dict) -> bool:
        results = _json(self._artifact(payload["results"]))
        turns = _json(self._artifact(payload["turns"]))
        if type(results) is not list or type(turns) is not dict or set(turns) != set(self._state.cases):
            raise EvidenceError("cycle coverage")
        by_case = {}
        observed_turns = set()
        for result in results:
            if type(result) is not dict:
                raise EvidenceError("result schema")
            case_id = _identifier(result.get("case_id"))
            if case_id not in self._state.cases or case_id in by_case or result.get("verdict") not in ("pass", "fail", "error"):
                raise EvidenceError("result identity or verdict")
            by_case[case_id] = result
            references = turns[case_id]
            if type(references) is not dict:
                raise EvidenceError("turn evidence")
            for turn, reference in references.items():
                _identifier(turn)
                if turn in observed_turns or turn in self._state.turns:
                    raise EvidenceError("turn reused")
                observed_turns.add(turn)
                self._artifact(reference)
            if result["verdict"] == "pass":
                probes = result.get("probe_results")
                if not references or type(probes) is not list or not probes or result.get("failure_reasons") != []:
                    raise EvidenceError("passing result without evidence")
                names = []
                for probe in probes:
                    if type(probe) is not dict or probe.get("passed") is not True:
                        raise EvidenceError("passing result with failed probe")
                    names.append(_identifier(probe.get("name")))
                if len(set(names)) != len(names) or set(names) != set(self._state.cases[case_id]["postcondition_probes"]):
                    raise EvidenceError("probe coverage")
        if set(by_case) != set(self._state.cases):
            raise EvidenceError("incomplete cycle")
        self._state.turns.update(observed_turns)
        return all(result["verdict"] == "pass" for result in results)

    def _append(self, kind: str, payload: dict) -> EvidenceFrontierV1:
        state = self._state
        if state.count >= MAX_EVENTS:
            raise EvidenceError("event limit")
        event = dict(schema_version=1, sequence=state.count + 1, previous=state.head, kind=kind, payload=payload)
        raw = encode_canonical_ascii_v1(event)
        if len(raw) > MAX_RECORD_BYTES:
            raise EvidenceError("event size")
        digest = _hash(raw, domain=_DOMAIN)
        self._advance(event, digest)
        self._connection.execute("INSERT INTO events VALUES (?, ?, ?)", (state.count, raw, digest))
        self._connection.commit()
        # Reconstruct through the cold reader before reporting a committed act.
        self._state = _Evidence(self._connection)._state
        return self.frontier

    @_transaction
    def census(self, *, scope_id: str, sources: tuple[bytes, ...], review: bytes,
               findings: dict[str, bytes]) -> EvidenceFrontierV1:
        return self._append("census", dict(scope_id=scope_id,
            sources=[self._put(raw) for raw in sources], review=self._put(review),
            findings={key: self._put(raw) for key, raw in findings.items()}))

    @_transaction
    def open_defect(self, finding_id: str, evidence: bytes) -> EvidenceFrontierV1:
        return self._append("defect_opened", dict(finding_id=finding_id, evidence=self._put(evidence)))

    @_transaction
    def close_defect(self, finding_id: str, *, opening: str, repair: bytes,
                     verification: bytes, review: bytes) -> EvidenceFrontierV1:
        return self._append("defect_closed", dict(finding_id=finding_id, opening=opening,
            repair=self._put(repair), verification=self._put(verification), review=self._put(review)))

    @_transaction
    def profile(self, *, base: dict[str, str], manifest: bytes, cases: bytes) -> EvidenceFrontierV1:
        return self._append("profile", {**base, "manifest": self._put(manifest), "cases": self._put(cases)})

    @_transaction
    def start_cycle(self) -> EvidenceFrontierV1:
        return self._append("cycle_started", dict(profile=self._state.profile))

    @_transaction
    def finish_cycle(self, *, start: str, results: bytes,
                     turns: dict[str, dict[str, bytes]]) -> EvidenceFrontierV1:
        references = {case: {turn: self._put(raw) for turn, raw in entries.items()} for case, entries in turns.items()}
        return self._append("cycle_finished", dict(start=start, results=self._put(results),
            turns=self._put(encode_canonical_ascii_v1(references))))


def _open_database(path: Path, *, root_owned: bool) -> sqlite3.Connection:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(path, flags | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        fd = os.open(path, flags)
    try:
        metadata = os.fstat(fd)
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1
                or stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_size > 2 * MAX_ARTIFACT_BYTES
                or (root_owned and (metadata.st_uid != 0 or metadata.st_gid != 0))):
            raise EvidenceError("unsafe database")
        # Do not checkpoint or rewrite an unsupported existing WAL store just
        # by opening it. This owner creates DELETE-journal databases only.
        if metadata.st_size and os.pread(fd, 2, 18) != b"\x01\x01":
            raise EvidenceError("database journal format")
        os.fsync(fd)
    finally:
        os.close(fd)
    _sync_directory(path.parent)
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA synchronous=FULL")
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        objects = dict(connection.execute("SELECT name, sql FROM sqlite_master WHERE sql IS NOT NULL"))
        if version == 0 and not objects:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                for statement in _DDL:
                    connection.execute(statement)
                connection.execute("PRAGMA user_version=1")
        elif version != 1 or set(objects.values()) != set(_DDL):
            raise EvidenceError("database schema")
        if connection.execute("PRAGMA journal_mode").fetchone()[0] != "delete":
            raise EvidenceError("database journal mode")
        if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise EvidenceError("database integrity")
        return connection
    except BaseException:
        connection.close()
        raise


@contextmanager
def _evidence_at_v1(root: Path, *, root_owned: bool):
    """One native persistence path; the parameterized seam is for isolation."""
    _directory_metadata(root, root_owned=root_owned)
    with _provisioning_lock(root, root_owned=root_owned):
        directory = root / DIRECTORY_V1
        if not directory.exists():
            directory.mkdir(mode=0o755)
            directory.chmod(0o755)
            _sync_directory(root)
        _directory_metadata(directory, root_owned=root_owned)
        if {path.name for path in directory.iterdir()} - {"evidence.sqlite", "evidence.sqlite-journal"}:
            raise EvidenceError("database inventory")
        journal = directory / "evidence.sqlite-journal"
        if journal.is_symlink():
            raise EvidenceError("unsafe recovery journal")
        if journal.exists():
            metadata = journal.lstat()
            if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1
                    or stat.S_IMODE(metadata.st_mode) != 0o600
                    or (root_owned and (metadata.st_uid != 0 or metadata.st_gid != 0))):
                raise EvidenceError("unsafe recovery journal")
        connection = _open_database(directory / "evidence.sqlite", root_owned=root_owned)
        try:
            evidence = _Evidence(connection)
            if evidence.frontier.pending_cycle is not None:
                evidence._append("cycle_interrupted", dict(start=evidence.frontier.pending_cycle))
            yield evidence
        finally:
            connection.rollback()
            connection.close()


@contextmanager
def administrative_evidence_v1():
    """Open only the fixed native administrative root, never a caller's path."""
    if not _managed_authority_platform_supported_v1():
        raise OwnershipAuthorityError("birth_certification_authority_platform_unsupported")
    if getattr(os, "geteuid", lambda: -1)() != 0:
        raise OwnershipAuthorityError("birth_certification_authority_root_required")
    _root_owned_chain(DEFAULT_OWNERSHIP_ROOT_V1)
    with _evidence_at_v1(DEFAULT_OWNERSHIP_ROOT_V1, root_owned=True) as evidence:
        yield evidence


__all__ = ["EvidenceError", "EvidenceFrontierV1", "administrative_evidence_v1"]
