# SPDX-License-Identifier: MIT
"""Archivio Monitor: JSONL del raccoglitore e indice derivato del lettore HTTP.

Nessuna funzione di questo modulo va chiamata nel percorso di una query LLM.
Il raccoglitore del pacchetto 3 consegnerà qui gli eventi già fuori dal turno.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sqlite3
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from audit_jsonl import append_bounded_jsonl
from monitor_events import count, identifier, normalize_event, timestamp
from monitor_metrics import METRIC_FIELDS
from monitor_requests import derive_request, process_alive

INDEX_VERSION = 1
MAX_LINE_BYTES = 4 * 1024 * 1024
MAX_SCAN_BYTES = 16 * 1024 * 1024
_SCHEMA = """
CREATE TABLE meta (revision INTEGER NOT NULL, epoch TEXT NOT NULL);
INSERT INTO meta VALUES (0, lower(hex(randomblob(16))));
CREATE TABLE segments (segment_id TEXT PRIMARY KEY, turn_id TEXT,
  parent_turn_id TEXT, owner_id TEXT, request_id TEXT, chain_complete INTEGER,
  payload TEXT NOT NULL);
CREATE INDEX segment_turn ON segments(turn_id, owner_id);
CREATE INDEX segment_parent ON segments(parent_turn_id, owner_id);
CREATE INDEX segment_request ON segments(request_id);
CREATE TABLE calls (call_id TEXT PRIMARY KEY, segment_id TEXT NOT NULL,
  provider TEXT, level TEXT, physical_model_key TEXT, started_at TEXT, payload TEXT NOT NULL);
CREATE INDEX call_segment ON calls(segment_id, started_at, call_id);
CREATE INDEX call_model ON calls(provider, physical_model_key, started_at);
CREATE TABLE requests (request_id TEXT NOT NULL, revision INTEGER NOT NULL,
  started_at TEXT, owner_id TEXT, channel TEXT, origin TEXT, status TEXT,
  payload TEXT, PRIMARY KEY(request_id, revision));
CREATE INDEX request_time ON requests(started_at, request_id, revision);
CREATE INDEX request_owner ON requests(owner_id, started_at);
CREATE INDEX request_status ON requests(status, started_at);
CREATE TABLE processes (epoch_key TEXT PRIMARY KEY, payload TEXT NOT NULL);
CREATE TABLE ingest (device INTEGER NOT NULL, inode INTEGER NOT NULL,
  position INTEGER NOT NULL, invalid_lines INTEGER NOT NULL,
  truncations INTEGER NOT NULL, skipping INTEGER NOT NULL, PRIMARY KEY(device, inode));
PRAGMA user_version = 1;
"""


def monitor_directory() -> Path:
    # Configurazione letta solo quando un componente apre l'archivio.
    import config as C
    return Path(C.PATH_USER_DATA) / "monitor"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)


def append_events(path: Path, events: Iterable[Mapping[str, Any]], *,
                  max_bytes: int = 8 * 1024 * 1024) -> Path:
    """Solo raccoglitore: valida e ruota senza cancellare lo storico."""
    return append_normalized_events(path, [normalize_event(row) for row in events], max_bytes=max_bytes)


def append_normalized_events(path: Path, events: Iterable[Mapping[str, Any]], *,
                             max_bytes: int = 8 * 1024 * 1024) -> Path:
    """Solo dopo normalize_event: evita di ripetere la normalizzazione nel writer."""
    return append_bounded_jsonl(path, events,
                                max_bytes=max_bytes, backup_count=None)


class MonitorIndex:
    """Un lettore/scrittore HTTP. Nessun thread o collegamento creato all'import."""

    def __init__(self, directory: Path | None = None, *, alive=process_alive):
        self.directory = Path(directory) if directory is not None else monitor_directory()
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = self.directory / "index.sqlite3"
        fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        os.close(fd)
        self.connection = sqlite3.connect(path, timeout=1.0)
        self.connection.row_factory = sqlite3.Row
        self.alive = alive
        try:
            version = self.connection.execute("PRAGMA user_version").fetchone()[0]
            if version == 0:
                self.connection.executescript("BEGIN;" + _SCHEMA + "COMMIT;")
            elif version != INDEX_VERSION:
                raise ValueError("unsupported_monitor_index_version")
            self.connection.execute("PRAGMA journal_mode=WAL")
        except Exception:
            self.connection.close()
            raise

    def close(self) -> None:
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _segment(self, segment_id: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT payload FROM segments WHERE segment_id=?",
                                      (segment_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def _apply(self, event: dict[str, Any]) -> str | None:
        if event["event"] == "process_started":
            self.connection.execute("INSERT OR IGNORE INTO processes VALUES (?,?)",
                                    (_json(event["process_epoch"]), _json(event)))
            return None
        segment_id = event["segment_id"]
        previous = self._segment(segment_id)
        if event["turn_id"]:
            duplicate = self.connection.execute(
                "SELECT segment_id FROM segments WHERE turn_id=? AND segment_id<>?",
                (event["turn_id"], segment_id)).fetchone()
            if duplicate:
                raise ValueError("turn_identity_conflict")
        segment = dict(previous or event)
        segment.pop("calls", None)
        if previous:
            for name in ("turn_id", "parent_turn_id", "owner_id", "job_ref"):
                if previous[name] is not None and event[name] is not None and (
                    previous[name] != event[name]
                ):
                    raise ValueError("segment_identity_conflict")
            finishing = event["event"] in {"segment_finished", "job_finished"}
            for name, value in event.items():
                if name in {"calls", "event", "schema_version"} or value is None:
                    continue
                if name in {"outcome", "active_ms", "finished_at", "wait_expires_at"}:
                    if not finishing:
                        continue
                    if previous["finished_at"] and event["finished_at"] < previous["finished_at"]:
                        continue
                if name in {"calls_started", "calls_closed", "dropped_calls", "dropped_events"}:
                    value = max(value, previous[name] or 0)
                    segment[name] = value
                elif previous.get(name) is None or finishing:
                    segment[name] = value
            if finishing:
                segment["event"] = event["event"]
        encoded = _json(segment)
        changed = previous is None or encoded != _json(previous)
        self.connection.execute(
            "INSERT INTO segments(segment_id,turn_id,parent_turn_id,owner_id,payload) VALUES (?,?,?,?,?) "
            "ON CONFLICT(segment_id) DO UPDATE SET turn_id=excluded.turn_id, "
            "parent_turn_id=excluded.parent_turn_id,owner_id=excluded.owner_id,payload=excluded.payload",
            (segment_id, segment["turn_id"], segment["parent_turn_id"], segment["owner_id"], encoded))
        for call in event["calls"]:
            old = self.connection.execute("SELECT segment_id,payload FROM calls WHERE call_id=?",
                                          (call["call_id"],)).fetchone()
            if old:
                if old["segment_id"] != segment_id:
                    raise ValueError("call_attribution_conflict")
                saved = json.loads(old["payload"])
                for name in ("provider", "level", "physical_model_key", "parent_call_id"):
                    if saved[name] and call[name] and saved[name] != call[name]:
                        raise ValueError("call_identity_conflict")
                merged = {name: value if value is not None else saved.get(name)
                          for name, value in call.items()}
                for field in METRIC_FIELDS:
                    if call[field] is None and saved[field] is not None:
                        merged[field] = saved[field]
                        merged["metric_sources"][field] = saved["metric_sources"][field]
                        merged["missing_reasons"].pop(field, None)
                if saved["status"] == "late" or call["status"] == "unknown":
                    merged["status"] = saved["status"]
                call = merged
            call_payload = _json(call)
            changed = changed or old is None or old["payload"] != call_payload
            self.connection.execute(
                "INSERT INTO calls VALUES (?,?,?,?,?,?,?) ON CONFLICT(call_id) DO UPDATE "
                "SET payload=excluded.payload, provider=excluded.provider,level=excluded.level, "
                "physical_model_key=excluded.physical_model_key,started_at=excluded.started_at",
                (call["call_id"], segment_id, call["provider"], call["level"],
                 call["physical_model_key"], call["started_at"], call_payload))
        if event["event"] in {"call_late", "segment_finished", "job_finished"} and segment["finished_at"]:
            observed = self.connection.execute(
                "SELECT COUNT(*) FROM calls WHERE segment_id=?", (segment_id,)).fetchone()[0]
            if observed > (segment["calls_closed"] or 0):
                segment["calls_closed"] = observed
                self.connection.execute("UPDATE segments SET payload=? WHERE segment_id=?",
                                        (_json(segment), segment_id))
                changed = True
        return segment_id if changed else None

    def _root(self, segment: dict[str, Any]) -> tuple[str, bool]:
        row, visited = segment, set()
        while row["parent_turn_id"]:
            if row["segment_id"] in visited:
                return "segment:" + min(visited), False
            visited.add(row["segment_id"])
            parent = self.connection.execute(
                "SELECT payload FROM segments WHERE turn_id=? AND owner_id IS ?",
                (row["parent_turn_id"], row["owner_id"])).fetchall()
            if len(parent) != 1:
                # La radice non è osservata. Non fondere con una richiesta di
                # un altro proprietario solo perché il riferimento coincide.
                return "segment:" + row["segment_id"], False
            row = json.loads(parent[0][0])
        root = row["turn_id"] or ("job:" + row["job_ref"] if row["job_ref"] else
                                  "segment:" + row["segment_id"])
        return root, bool(row["turn_id"] or row["job_ref"])

    def _refresh(self, changed: set[str], revision: int) -> None:
        pending, affected, roots = list(changed), set(), set()
        while pending:
            segment_id = pending.pop()
            if segment_id in affected:
                continue
            affected.add(segment_id)
            segment = self._segment(segment_id)
            old = self.connection.execute("SELECT request_id FROM segments WHERE segment_id=?",
                                          (segment_id,)).fetchone()[0]
            if old:
                roots.add(old)
            root, complete = self._root(segment)
            roots.add(root)
            self.connection.execute("UPDATE segments SET request_id=?,chain_complete=? WHERE segment_id=?",
                                    (root, int(complete), segment_id))
            if segment["turn_id"]:
                pending.extend(row[0] for row in self.connection.execute(
                    "SELECT segment_id FROM segments WHERE parent_turn_id=? AND owner_id IS ?",
                    (segment["turn_id"], segment["owner_id"])))
        for root in roots:
            self._snapshot(root, revision)

    def _snapshot(self, root: str, revision: int) -> bool:
        rows = self.connection.execute("SELECT payload,chain_complete FROM segments WHERE request_id=?",
                                       (root,)).fetchall()
        if rows and any(json.loads(row[0])["event"] != "unattributed" for row in rows):
            calls = self.connection.execute("SELECT c.payload FROM calls c JOIN segments s "
                                             "ON c.segment_id=s.segment_id WHERE s.request_id=?", (root,))
            segments = [json.loads(row[0]) for row in rows]
            parents = [row["parent_turn_id"] for row in segments if row["parent_turn_id"]]
            linear = len(parents) == len(set(parents))
            view = derive_request(root, segments,
                                  [json.loads(row[0]) for row in calls],
                                  chain_complete=linear and all(row[1] for row in rows), alive=self.alive)
        else:
            view = None
        payload = _json(view) if view else None
        previous = self.connection.execute("SELECT payload FROM requests WHERE request_id=? "
                                           "ORDER BY revision DESC LIMIT 1", (root,)).fetchone()
        if previous and previous[0] == payload:
            return False
        self.connection.execute("INSERT OR REPLACE INTO requests VALUES (?,?,?,?,?,?,?,?)",
                                (root, revision, view and view["started_at"], view and view["owner_id"],
                                 view and view["channel"], view and view["origin"], view and view["status"],
                                 payload))
        return True

    def _refresh_states(self) -> None:
        """Scadenze e processi morti possono cambiare senza una nuova riga JSONL."""
        roots = self.connection.execute(
            "SELECT r.request_id FROM requests r WHERE r.revision=(SELECT MAX(s.revision) "
            "FROM requests s WHERE s.request_id=r.request_id) "
            "AND r.status IN ('in_progress','unknown','awaiting_input')").fetchall()
        revision = self.connection.execute("SELECT revision FROM meta").fetchone()[0] + 1
        with self.connection:
            changed = [self._snapshot(row[0], revision) for row in roots]
            if any(changed):
                self.connection.execute("UPDATE meta SET revision=?", (revision,))

    def ingest(self, paths: Iterable[Path], *, max_events: int = 1000) -> dict[str, int]:
        """Legge solo righe complete. Posizione per inode: rinomina ≠ nuova acquisizione."""
        if type(max_events) is not int or not 1 <= max_events <= 10000:
            raise ValueError("invalid_ingest_limit")
        changed: set[str] = set()
        read = invalid = scanned_bytes = 0
        with self.connection:
            # Un solo commit del batch: RELEASE del primo SAVEPOINT altrimenti
            # sincronizza ogni riga e lascia eventi senza cursore dopo un errore.
            if not self.connection.in_transaction:
                # Prenota la scrittura prima di leggere il cursore: due pagine
                # concorrenti non devono aggiornare uno snapshot WAL superato.
                self.connection.execute("BEGIN IMMEDIATE")
            for path in paths:
                if read >= max_events or scanned_bytes >= MAX_SCAN_BYTES:
                    break
                fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
                with os.fdopen(fd, "rb") as source:
                    stat = os.fstat(source.fileno())
                    key = (stat.st_dev, stat.st_ino)
                    old = self.connection.execute("SELECT * FROM ingest WHERE device=? AND inode=?", key).fetchone()
                    position = old["position"] if old else 0
                    truncated = position > stat.st_size
                    skipping = bool(old["skipping"]) if old and not truncated else False
                    source.seek(0 if truncated else position)
                    bad = 0
                    while read < max_events and scanned_bytes < MAX_SCAN_BYTES:
                        position = source.tell()
                        line = source.readline(MAX_LINE_BYTES + 1)
                        scanned_bytes += len(line)
                        if not line:
                            break
                        if skipping or len(line) > MAX_LINE_BYTES:
                            if not skipping:
                                bad += 1
                                read += 1
                            skipping = not line.endswith(b"\n")
                            continue
                        if not line.endswith(b"\n"):
                            source.seek(position)
                            break
                        self.connection.execute("SAVEPOINT monitor_line")
                        try:
                            event = normalize_event(json.loads(line))
                            segment = self._apply(event)
                            if segment:
                                changed.add(segment)
                        except (ValueError, TypeError, UnicodeError, RecursionError):
                            self.connection.execute("ROLLBACK TO monitor_line")
                            bad += 1
                        self.connection.execute("RELEASE monitor_line")
                        read += 1
                    self.connection.execute("INSERT OR REPLACE INTO ingest VALUES (?,?,?,?,?,?)",
                                            (*key, source.tell(), (old["invalid_lines"] if old else 0) + bad,
                                             (old["truncations"] if old else 0) + int(truncated),
                                             int(skipping)))
                    invalid += bad
            if changed:
                revision = self.connection.execute("SELECT revision FROM meta").fetchone()[0] + 1
                self._refresh(changed, revision)
                self.connection.execute("UPDATE meta SET revision=?", (revision,))
        return {"lines_read": read, "invalid_lines": invalid, "segments_changed": len(changed)}

    def get_request(self, request_id: str) -> dict[str, Any] | None:
        request_id = identifier(request_id, required=True)
        self._refresh_states()
        row = self.connection.execute("SELECT payload FROM requests WHERE request_id=? "
                                      "ORDER BY revision DESC LIMIT 1", (request_id,)).fetchone()
        if not row or row[0] is None:
            return None
        view = json.loads(row[0])
        view["segments"] = [json.loads(row[0]) for row in self.connection.execute(
            "SELECT payload FROM segments WHERE request_id=?", (request_id,))]
        view["calls"] = [json.loads(row[0]) for row in self.connection.execute(
            "SELECT c.payload FROM calls c JOIN segments s ON c.segment_id=s.segment_id "
            "WHERE s.request_id=? ORDER BY c.started_at,c.call_id", (request_id,))]
        return view

    def list_requests(self, filters: Mapping[str, Any], *, limit: int = 50,
                      cursor: str | None = None) -> dict[str, Any]:
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("invalid_page_limit")
        return self._read_requests(filters, limit=limit, cursor=cursor)

    def _read_requests(self, filters: Mapping[str, Any], *, limit: int,
                       cursor: str | None = None) -> dict[str, Any]:
        self._refresh_states()
        allowed = {"from", "to", "owner_id", "channel", "origin", "status",
                   "provider", "level", "physical_model_key"}
        if set(filters) - allowed:
            raise ValueError("invalid_filter")
        values = {name: timestamp(filters.get(name), required=True) for name in ("from", "to")}
        delta = datetime.fromisoformat(values["to"]) - datetime.fromisoformat(values["from"])
        if not timedelta(0) < delta <= timedelta(days=90):
            raise ValueError("invalid_period")
        values.update({name: identifier(filters[name], required=True) for name in allowed - {"from", "to"}
                       if filters.get(name) is not None})
        digest = hashlib.sha256(_json(values).encode()).hexdigest()
        state = self.connection.execute("SELECT revision,epoch FROM meta").fetchone()
        revision, epoch = state[0], state[1]
        after = None
        if cursor:
            try:
                if len(cursor) > 2048:
                    raise ValueError("cursor_too_long")
                token = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
                if token["epoch"] != epoch or token["filters"] != digest or count(token["revision"]) is None or (
                    token["revision"] > revision
                ):
                    raise ValueError("cursor_context_mismatch")
                revision = token["revision"]
                after = (timestamp(token["started_at"], required=True),
                         identifier(token["request_id"], required=True))
            except (ValueError, TypeError, KeyError, UnicodeError) as exc:
                raise ValueError("invalid_cursor") from exc
        clauses = ["r.payload IS NOT NULL", "r.started_at>=?", "r.started_at<?"]
        parameters: list[Any] = [revision, revision, values["from"], values["to"]]
        model_filters = {"provider": 0, "level": 1, "physical_model_key": 2}
        for name in allowed - {"from", "to"} - model_filters.keys():
            if name in values:
                clauses.append(f"r.{name}=?")
                parameters.append(values[name])
        selected = [name for name in model_filters if name in values]
        if selected:
            clauses.append("EXISTS (SELECT 1 FROM json_each(r.payload,'$.models') m WHERE " +
                           " AND ".join(f"json_extract(m.value,'$[{model_filters[name]}]')=?"
                                        for name in selected) + ")")
            parameters.extend(values[name] for name in selected)
        if after:
            clauses.append("(r.started_at,r.request_id)<(?,?)")
            parameters.extend(after)
        parameters.append(limit + 1)
        rows = self.connection.execute(
            "SELECT r.payload FROM requests r WHERE r.revision<=? AND r.revision="
            "(SELECT MAX(s.revision) FROM requests s WHERE s.request_id=r.request_id "
            "AND s.revision<=?) AND " + " AND ".join(clauses) +
            " ORDER BY r.started_at DESC,r.request_id DESC LIMIT ?", parameters).fetchall()
        items = [json.loads(row[0]) for row in rows[:limit]]
        next_cursor = None
        if len(rows) > limit:
            last = items[-1]
            next_cursor = base64.urlsafe_b64encode(_json({"filters": digest, "revision": revision, "epoch": epoch,
                "started_at": last["started_at"], "request_id": last["request_id"]}).encode()).decode()
        return {"schema_version": "metnos.monitor/1", "filters": values,
                "items": items, "next_cursor": next_cursor, "revision": revision}

    def aggregate_source(self, filters: Mapping[str, Any], *, cap: int = 100_000) -> dict[str, Any]:
        """Intero filtro, indipendente dalla pagina; limite dichiarato al client."""
        if type(cap) is not int or not 1 <= cap <= 100_000:
            raise ValueError("invalid_aggregation_limit")
        page = self._read_requests(filters, limit=cap)
        values = page["filters"]
        # Le richieste sono selezionate per l'inizio della radice; le chiamate
        # per il proprio inizio, anche quando la radice precede il periodo.
        clauses = ["c.started_at>=?", "c.started_at<?"]
        parameters = [page["revision"], values["from"], values["to"]]
        scope = [name for name in ("owner_id", "channel", "origin", "status") if values.get(name)]
        if scope:
            clauses.append("r.payload IS NOT NULL")
            for name in scope:
                clauses.append(f"r.{name}=?")
                parameters.append(values[name])
        else:
            clauses.append("(r.payload IS NOT NULL OR json_extract(s.payload,'$.event')='unattributed')")
        for name in ("provider", "level", "physical_model_key"):
            if filters.get(name):
                clauses.append(f"c.{name}=?")
                parameters.append(filters[name])
        parameters.append(cap + 1)
        rows = self.connection.execute(
            "SELECT c.payload,s.request_id,json_extract(s.payload,'$.event') AS event "
            "FROM calls c JOIN segments s ON s.segment_id=c.segment_id "
            "LEFT JOIN requests r ON r.request_id=s.request_id AND r.revision="
            "(SELECT MAX(v.revision) FROM requests v WHERE v.request_id=s.request_id AND v.revision<=?) WHERE " +
            " AND ".join(clauses) + " ORDER BY c.started_at,c.call_id LIMIT ?", parameters).fetchall()
        calls = []
        for row in rows[:cap]:
            call = json.loads(row["payload"])
            call["request_id"] = row["request_id"]
            call["attribution"] = "unattributed" if row["event"] == "unattributed" else "request"
            calls.append(call)
        return {"requests": page["items"], "calls": calls, "filters": page["filters"],
                "truncated": bool(page["next_cursor"]) or len(rows) > cap,
                "revision": page["revision"]}

    def health(self, *, now: datetime | None = None) -> dict[str, Any]:
        row = self.connection.execute("SELECT COALESCE(SUM(invalid_lines),0), "
                                      "COALESCE(SUM(truncations),0) FROM ingest").fetchone()
        latest = self.connection.execute(
            "SELECT MAX(COALESCE(json_extract(payload,'$.finished_at'), "
            "json_extract(payload,'$.started_at'))) FROM segments").fetchone()[0]
        now = now or datetime.now(timezone.utc)
        collectors = []
        # Si leggono prima gli ultimi heartbeat: la storia dei processi
        # terminati non deve nascondere i raccoglitori correnti.
        paths = []
        for path in self.directory.glob("health-*.json"):
            try:
                paths.append((path.stat().st_mtime_ns, path))
            except OSError:
                continue
        for _, path in sorted(paths, reverse=True)[:256]:
            try:
                with path.open() as source:
                    payload = json.loads(source.read(64 * 1024))
                observed = timestamp(payload.get("observed_at"), required=True)
                alive = self.alive(payload.get("process_epoch"))
                payload["alive"] = alive
                payload["stale"] = alive is True and not payload.get("stopped") and (
                    now - datetime.fromisoformat(observed)).total_seconds() > 10
                collectors.append(payload)
            except (OSError, ValueError, TypeError, AttributeError):
                continue
        acquired = [item.get("latest_event_at") for item in collectors if item.get("latest_event_at")]
        history_truncated = len(paths) > 256
        return {"invalid_lines": row[0], "truncations": row[1],
                "latest_event_at": max(acquired) if acquired else latest,
                "revision": self.connection.execute("SELECT revision FROM meta").fetchone()[0],
                "collectors": collectors, "stale": any(item["stale"] for item in collectors),
                "collector_history_truncated": history_truncated,
                "available": any(item["alive"] is True and not item.get("stopped") for item in collectors),
                "dropped_events": (sum(count(item.get("dropped_events")) or 0 for item in collectors)
                                   if collectors and not history_truncated else None),
                "write_errors": (sum(count(item.get("write_errors")) or 0 for item in collectors)
                                 if collectors and not history_truncated else None)}
