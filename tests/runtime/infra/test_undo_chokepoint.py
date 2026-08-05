"""C7 Area-2 CP4 — undo: scrittore al choke-point + reverse device-aware.

Copre la REGRESSIONE af6c7b8 (4/7): la cancellazione del planner legacy aveva
rimosso l'UNICO scrittore del log undo — da allora ogni mutazione era
silenziosamente non annullabile (§2.8). Ora lo scrittore vive in
`agent_runtime.invoke_executor` (choke-point: engine + device + futuri).
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")


@pytest.fixture()
def undo_log_isolated(tmp_path, monkeypatch):
    """UndoLog rediretto su file temp nel processo del runtime."""
    import agent_runtime
    import undo as undo_mod
    log_path = tmp_path / "undo.jsonl"

    class _IsolatedLog(undo_mod.UndoLog):
        def __init__(self, path=None):
            super().__init__(path or log_path)

    monkeypatch.setattr(agent_runtime, "UndoLog", _IsolatedLog)
    return log_path


def _records(log_path):
    if not log_path.exists():
        return []
    return [json.loads(l) for l in log_path.read_text().splitlines() if l.strip()]


def _find_executor(name):
    from loader import load_catalog
    return next(e for e in load_catalog() if getattr(e, "name", "") == name)


# 1. REGRESSIONE af6c7b8: una mutazione reale via invoke_executor scrive
#    pending+done nel log (prima del fix: log muto).

def test_write_files_logs_pending_and_done(undo_log_isolated, tmp_path):
    import agent_runtime
    ex = _find_executor("write_files")
    dest = tmp_path / "out" / "nota.txt"
    turn = uuid.uuid4().hex[:12]
    obs = agent_runtime.invoke_executor(
        ex, {"entries": [{"path": str(dest), "content": "ciao"}],
             "client": "local"},
        timeout_s=60, turn_id=turn, actor="host", channel="test")
    assert obs.get("ok") is True, obs
    assert dest.read_text() == "ciao"
    recs = _records(undo_log_isolated)
    kinds = [r["type"] for r in recs]
    assert kinds == ["pending", "done"], recs
    assert recs[0]["executor"] == "write_files"
    assert recs[0]["turn_id"] == turn
    assert recs[0]["device"] == ""  # esecuzione locale


# 2. Round-trip COMPLETO server-side: write → undo_last_turn → file rimosso
#    (il reverse delete_created_paths gira davvero).

def test_undo_last_turn_reverses_local_write(undo_log_isolated, tmp_path):
    import agent_runtime
    ex = _find_executor("write_files")
    dest = tmp_path / "da_annullare.txt"
    obs = agent_runtime.invoke_executor(
        ex, {"entries": [{"path": str(dest), "content": "x"}],
             "client": "local"},
        timeout_s=60, turn_id=uuid.uuid4().hex[:12],
        actor="host", channel="test")
    assert obs.get("ok") is True and dest.exists()
    sys.path.insert(0, str(_RUNTIME.parent / "executors" / "undo_last_turn"))
    try:
        import undo_last_turn as ult
    finally:
        sys.path.pop(0)
    out = ult.invoke({"log_path": str(undo_log_isolated)})
    assert out.get("ok") is True, out
    assert out.get("undone_count") == 1, out
    assert not dest.exists(), "il reverse non ha rimosso il file creato"


def test_undo_broker_reverses_through_real_subprocess_sandbox_boundary(
        tmp_path, monkeypatch):
    """Il percorso live non deve vedere un /tmp privato e fingere un no-op."""
    import agent_runtime

    user_data = tmp_path / "user-data"
    user_data.mkdir()
    target = tmp_path / "created-by-forward.txt"
    target.write_text("payload", encoding="utf-8")
    log_path = user_data / "undo.jsonl"
    records = [
        {
            "type": "pending", "device": "", "op_id": "op-broker",
            "turn_id": "turn-broker", "ts": 1.0, "actor": "broker-test",
            "channel": "test", "executor": "write_files", "args": {},
            "plan": {},
        },
        {
            "type": "done", "op_id": "op-broker", "ts": 2.0,
            "results": {
                "ok": True,
                "results": [{"path": str(target), "created": True}],
            },
        },
    ]
    log_path.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )
    monkeypatch.setenv("METNOS_USER_DATA", str(user_data))
    ex = _find_executor("undo_last_turn")

    result = agent_runtime.invoke_executor(
        ex, {"log_path": "/tmp/forged-undo.jsonl"}, timeout_s=60,
        actor="broker-test", channel="test",
    )

    assert result.get("ok") is True, result
    assert result.get("undone_count") == 1, result
    assert not target.exists()
    assert "undone" in log_path.read_text(encoding="utf-8")


def test_undo_selects_local_spreadsheet_branch_just_in_time(
        undo_log_isolated, tmp_path):
    import agent_runtime
    ex = _find_executor("create_files_spreadsheet")
    destination = tmp_path / "created.xlsx"
    obs = agent_runtime.invoke_executor(
        ex,
        {"path": str(destination), "values": [["name"], ["Ada"]],
         "client": "local"},
        timeout_s=60,
        turn_id=uuid.uuid4().hex[:12],
        actor="host",
        channel="test",
    )
    assert obs.get("ok") is True and destination.exists()
    sys.path.insert(0, str(
        _RUNTIME.parent / "executors" / "undo_last_turn"))
    try:
        import undo_last_turn as ult
        out = ult.invoke({"log_path": str(undo_log_isolated)})
    finally:
        sys.path.pop(0)
    assert out.get("ok") is True, out
    assert out.get("undone_count") == 1, out
    assert not destination.exists()


def test_unknown_manifest_pattern_falls_back_to_module_reverse(
        tmp_path, monkeypatch):
    """Un pattern legacy ignoto non deve oscurare un reverse() funzionante."""
    sys.path.insert(0, str(_RUNTIME.parent / "executors" / "undo_last_turn"))
    try:
        import undo_last_turn as ult
    finally:
        sys.path.pop(0)
    code = tmp_path / "legacy_reverse.py"
    code.write_text(
        "def reverse(plan, results):\n"
        "    return {'ok': True, 'ok_count': 1, 'fail_count': 0}\n",
        encoding="utf-8",
    )
    log_path = tmp_path / "undo.jsonl"
    from undo import UndoLog
    log = UndoLog(log_path)
    log.append_pending("op-legacy", "turn-legacy", "legacy_delete", {},
                       plan={}, actor="host")
    log.append_done("op-legacy", {"ok": True, "results": [{}]})
    ex = SimpleNamespace(
        name="legacy_delete", revertible=True,
        reverse_pattern="restore_legacy", code_path=code,
    )
    monkeypatch.setattr(
        ult, "load_catalog", lambda: SimpleNamespace(get=lambda name: ex))

    out = ult.invoke({"log_path": str(log_path), "_actor": "host"})

    assert out["ok"] is True
    assert out["undone_count"] == 1


def test_known_pattern_failure_never_falls_back_to_module_reverse(
        tmp_path, monkeypatch):
    """Un reverse noto ma fallito resta fallito: niente secondo effetto."""
    sys.path.insert(0, str(_RUNTIME.parent / "executors" / "undo_last_turn"))
    try:
        import undo_last_turn as ult
    finally:
        sys.path.pop(0)
    marker = tmp_path / "module_was_called"
    code = tmp_path / "must_not_run.py"
    code.write_text(
        "from pathlib import Path\n"
        f"def reverse(plan, results):\n    Path({str(marker)!r}).touch()\n"
        "    return {'ok': True, 'ok_count': 1, 'fail_count': 0}\n",
        encoding="utf-8",
    )
    log_path = tmp_path / "undo.jsonl"
    from undo import UndoLog
    log = UndoLog(log_path)
    log.append_pending("op-known", "turn-known", "known_delete", {},
                       plan={}, actor="host")
    log.append_done("op-known", {
        "ok": True,
        "results": [{"path": str(tmp_path / "missing"),
                     "restore_mode": "overwrite"}],
    })
    ex = SimpleNamespace(
        name="known_delete", revertible=True,
        reverse_pattern="restore_blob_backup", code_path=code,
    )
    monkeypatch.setattr(
        ult, "load_catalog", lambda: SimpleNamespace(get=lambda name: ex))

    out = ult.invoke({"log_path": str(log_path), "_actor": "host"})

    assert out["ok"] is False
    assert out["undone_count"] == 0
    assert not marker.exists()


def test_calendar_delete_by_id_pattern_is_registered():
    from reverse_patterns import PATTERNS

    assert "delete_calendars_by_id" in PATTERNS


# 3. Executor NON revertibile → nessun record (niente rumore nel log).

def test_non_revertible_writes_nothing(undo_log_isolated, tmp_path):
    import agent_runtime
    ex = _find_executor("list_dirs")
    d = tmp_path / "dir"
    d.mkdir()
    obs = agent_runtime.invoke_executor(
        ex, {"path": str(d)}, timeout_s=60,
        turn_id="t1", actor="host", channel="test")
    assert obs.get("ok") is True, obs
    assert _records(undo_log_isolated) == []


# 4. Helper device: il campo `device` finisce nel record pending.

def test_pending_device_field(undo_log_isolated):
    import agent_runtime

    class _Ex:
        name = "move_files"
        revertible = True

    op = agent_runtime._undo_pending(
        _Ex(), {"entries": []}, turn_id="t9", actor="host",
        channel="http", device="dev-abc123")
    assert op
    recs = _records(undo_log_isolated)
    assert recs[0]["device"] == "dev-abc123"


# 5. Builder deterministico delle chiamate-reverse remote.

def test_build_remote_reverse_calls():
    from reverse_patterns import build_remote_reverse_calls
    # move → swap
    out = build_remote_reverse_calls(
        ["swap_src_dst", "delete_created_dirs"], {},
        {"results": [{"src": "C:/a/f.txt", "dst": "C:/b/f.txt"}],
         "dirs_created": ["C:/b"]})
    assert out["unsupported"] == []
    calls = {c["executor"]: c["args"] for c in out["calls"]}
    assert calls["move_files"]["entries"] == [{"src": "C:/b/f.txt"}]
    assert calls["move_files"]["dst_template"] == "C:/a/{name}"
    assert calls["delete_dirs"]["paths"] == ["C:/b"]
    assert calls["delete_dirs"].get("force", False) is False
    # write → delete dei creati (solo created=true)
    out2 = build_remote_reverse_calls(
        "delete_created_paths", {},
        {"results": [{"path": "/x/new.txt", "created": True},
                     {"path": "/x/già.txt", "created": False}]})
    assert out2["calls"][0]["executor"] == "delete_files"
    assert out2["calls"][0]["args"]["paths"] == ["/x/new.txt"]
    # blob (ADR 0183 D3 + batch/copy 6/7): restore = COPIA device-locale
    # blob→path, BATCH entries + template {dst} (blob dedup serve N path).
    out3 = build_remote_reverse_calls(
        "restore_blob_backup", {},
        {"results": [{"path": "C:/docs/f.txt",
                      "blob_path": "C:/hist/no_turn/blob/ab.bin",
                      "blob_sha256": "ab"}]})
    a3 = out3["calls"][0]["args"]
    assert out3["calls"][0]["executor"] == "move_files"
    assert a3["entries"] == [{"src": "C:/hist/no_turn/blob/ab.bin",
                              "dst": "C:/docs/f.txt"}]
    assert a3["dst_template"] == "{dst}"
    assert a3["copy"] is True
    assert out3["unsupported"] == []
    # righe senza blob → dichiarato, mai silenzio
    out3b = build_remote_reverse_calls(
        "restore_blob_backup", {}, {"results": [{"path": "/x.txt"}]})
    assert out3b["calls"] == []
    assert out3b["unsupported"] == ["restore_blob_backup:righe-senza-blob"]
    # Lo scope provider emesso dal backend deve sopravvivere fino alla call
    # JIT di undo; senza, delete_files ricadeva erroneamente su `local`.
    from reverse_patterns_patch import build_undo_calls
    scoped, scoped_error = build_undo_calls(
        "delete_files_by_id",
        {"_undo": {"ids": ["drive-1"],
                   "scope": {"client": "google_workspace"}}},
    )
    assert scoped_error is None
    assert scoped == [{
        "executor": "delete_files",
        "args": {"file_ids": ["drive-1"], "client": "google_workspace"},
    }]
    # rename: template LETTERALE per-file (niente {name})
    out4 = build_remote_reverse_calls(
        ["swap_src_dst"], {},
        {"results": [{"src": "/a/vecchio.txt", "dst": "/b/nuovo.txt"}]})
    a4 = out4["calls"][0]["args"]
    assert a4["entries"] == [{"src": "/b/nuovo.txt"}]
    assert a4["dst_template"] == "/a/vecchio.txt"


def test_runtime_reverse_selection_is_bounded_by_manifest():
    from types import SimpleNamespace
    undo_executor = _RUNTIME.parent / "executors" / "undo_last_turn"
    sys.path.insert(0, str(undo_executor))
    try:
        import undo_last_turn as ult
    finally:
        sys.path.pop(0)

    executor = SimpleNamespace(reverse_pattern=[
        "delete_created_paths", "delete_files_by_id"])
    local = {"results": {"_undo": {
        "reverse_pattern": "delete_created_paths"}}}
    remote = {"results": {"_undo": {
        "reverse_pattern": "delete_files_by_id"}}}
    forged = {"results": {"_undo": {
        "reverse_pattern": "restore_blob_backup"}}}

    assert ult._effective_reverse_pattern(executor, local) == (
        "delete_created_paths", None)
    assert ult._effective_reverse_pattern(executor, remote) == (
        "delete_files_by_id", None)
    selected, error = ult._effective_reverse_pattern(executor, forged)
    assert selected is None
    assert "outside manifest ceiling" in error


def test_drive_trash_roundtrip_uses_jit_pattern_and_marks_undone(
        tmp_path, monkeypatch):
    sys.path.insert(0, str(_RUNTIME.parent / "executors" / "undo_last_turn"))
    try:
        import undo_last_turn as ult
    finally:
        sys.path.pop(0)
    from undo import UndoLog
    from backends.files import google_workspace

    log_path = tmp_path / "undo.jsonl"
    log = UndoLog(log_path)
    log.append_pending(
        "op-drive-trash", "turn-drive-trash", "delete_files",
        {"client": "google_workspace", "file_ids": ["drive-id-000001"]},
        plan={}, actor="host",
    )
    log.append_done("op-drive-trash", {
        "ok": True,
        "results": [{
            "ok": True, "id": "drive-id-000001", "status": "trashed",
        }],
        "_undo": {
            "reverse_pattern": "restore_trashed_files",
            "ids": ["drive-id-000001"],
            "scope": {"client": "google_workspace"},
        },
    })
    executor = SimpleNamespace(
        name="delete_files", revertible=True,
        reverse_pattern=["restore_blob_backup", "restore_trashed_files"],
        code_path=_RUNTIME.parent / "executors/delete_files/delete_files.py",
    )
    monkeypatch.setattr(
        ult, "load_catalog", lambda: SimpleNamespace(
            get=lambda name: executor if name == "delete_files" else None),
    )
    calls = []
    monkeypatch.setattr(
        google_workspace, "restore_trashed",
        lambda args: calls.append(dict(args)) or {
            "ok": True, "ok_count": 1, "fail_count": 0,
            "results": [{"id": args["ids"][0], "status": "restored"}],
            "failed": [],
        },
    )

    result = ult.invoke({"log_path": str(log_path), "_actor": "host"})

    assert result["ok"] is True
    assert result["undone_count"] == 1
    assert calls == [{"ids": ["drive-id-000001"]}]
    assert any(record.get("type") == "undone"
               for record in _records(log_path))


# 6. IMAP swap NON diventa una chiamata move_files (dominio diverso).

def test_imap_pairs_not_remotable_as_fs_move():
    from reverse_patterns import build_remote_reverse_calls
    out = build_remote_reverse_calls(
        ["swap_src_dst"], {},
        {"results": [{"account": "a@b.c", "src": "INBOX", "dst": "Junk",
                      "uid": "77"}]})
    assert out["calls"] == []
