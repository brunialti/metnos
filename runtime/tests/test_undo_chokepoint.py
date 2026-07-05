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

import pytest

_RUNTIME = Path(__file__).resolve().parent.parent
if str(_RUNTIME) not in sys.path:
    sys.path.insert(0, str(_RUNTIME))


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
    assert calls["delete_dirs"]["if_empty_only"] is True
    # write → delete dei creati (solo created=true)
    out2 = build_remote_reverse_calls(
        "delete_created_paths", {},
        {"results": [{"path": "/x/new.txt", "created": True},
                     {"path": "/x/già.txt", "created": False}]})
    assert out2["calls"][0]["executor"] == "delete_files"
    assert out2["calls"][0]["args"]["paths"] == ["/x/new.txt"]
    # blob → non remotabile, dichiarato
    out3 = build_remote_reverse_calls("restore_blob_backup", {}, {})
    assert out3["calls"] == [] and out3["unsupported"] == ["restore_blob_backup"]
    # rename: template LETTERALE per-file (niente {name})
    out4 = build_remote_reverse_calls(
        ["swap_src_dst"], {},
        {"results": [{"src": "/a/vecchio.txt", "dst": "/b/nuovo.txt"}]})
    a4 = out4["calls"][0]["args"]
    assert a4["entries"] == [{"src": "/b/nuovo.txt"}]
    assert a4["dst_template"] == "/a/vecchio.txt"


# 6. IMAP swap NON diventa una chiamata move_files (dominio diverso).

def test_imap_pairs_not_remotable_as_fs_move():
    from reverse_patterns import build_remote_reverse_calls
    out = build_remote_reverse_calls(
        ["swap_src_dst"], {},
        {"results": [{"account": "a@b.c", "src": "INBOX", "dst": "Junk",
                      "uid": "77"}]})
    assert out["calls"] == []
