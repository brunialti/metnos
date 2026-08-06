"""Il piano GREZZO viaggia con l'observation (6/8/2026).

Un'observation registra il piano ESEGUITO, cioe' gia' passato dalla
GUARD_PIPELINE. Su un corpus fatto solo di piani sani non si puo' rispondere
alla domanda che conta prima di sostituire una guardia con una regola: «che
cosa ripara davvero, questa guardia?». Da qui la colonna `framework_raw_json`,
scritta col piano com'era PRIMA delle guardie.

Vuota per gli hit di cache: li' un piano grezzo non esiste, e dichiararlo
assente e' diverso dal dichiararlo identico (§2.8).

DB isolato per test: mai toccare il registro reale.
"""
from __future__ import annotations

import json
from pathlib import Path

import engine.autopath as AP
from engine.types import Intent, Framework, StepSpec


_INTENT = Intent(verb="find", object="files",
                 actions=[{"verb": "find", "object": "files"}])


def _fw(*tools):
    return Framework(steps=[StepSpec(tool=t, args={}) for t in tools],
                     final_message="ok")


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(AP, "_db_path", lambda: Path(tmp_path) / "autopath.sqlite")
    AP._DB_INIT_DONE = False


def _last(field):
    c = AP._conn()
    try:
        return c.execute(f"SELECT {field} FROM observations "
                         "ORDER BY id DESC LIMIT 1").fetchone()[0]
    finally:
        c.close()


def test_raw_plan_is_stored_when_given(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    raw = _fw("find_files").to_dict()
    AP.record_observation(turn_id="t1", intent=_INTENT,
                          framework=_fw("find_files", "describe_entries"),
                          query="trova i file", framework_raw=raw)
    stored = json.loads(_last("framework_raw_json"))
    assert [s["tool"] for s in stored["steps"]] == ["find_files"]
    assert [s["tool"] for s in json.loads(_last("framework_json"))["steps"]] \
        == ["find_files", "describe_entries"]


def test_cache_hit_declares_absence_not_identity(tmp_path, monkeypatch):
    """Senza piano grezzo la colonna resta VUOTA: assente, non «uguale al
    finale». Confonderli falserebbe qualunque conto su che cosa le guardie
    riparano."""
    _isolate(tmp_path, monkeypatch)
    AP.record_observation(turn_id="t2", intent=_INTENT,
                          framework=_fw("find_files"), query="trova i file")
    assert _last("framework_raw_json") == ""


def test_unserializable_raw_does_not_lose_the_observation(tmp_path, monkeypatch):
    """Il piano grezzo e' diagnostica: se non si serializza, l'observation si
    registra lo stesso. Non e' lui a decidere se un turno vale."""
    _isolate(tmp_path, monkeypatch)
    AP.record_observation(turn_id="t3", intent=_INTENT,
                          framework=_fw("find_files"), query="trova i file",
                          framework_raw={"steps": {object()}})
    assert _last("turn_id") == "t3"
    assert _last("framework_raw_json") == ""
