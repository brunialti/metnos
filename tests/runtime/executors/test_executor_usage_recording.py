"""L'uso reale degli executor torna a lasciare traccia (6/8/2026).

Il gancio per-invocazione fu cancellato il 4/7 con `af6c7b87` insieme al
planner legacy e mai ricablato in engine v3: per un mese `last_used_at` e'
rimasto vuoto su 124 executor su 193, mentre `apply_executor_ager` decideva le
deprecazioni e `change_observer` i rollback su numeri fermi. Il gancio vive ora
nell'UNICO punto attraversato da subprocess, remoto, builtin in-process e onda
parallela (`ExecutorScheduler.invoke`, ADR 0196).

Quattro proprieta', tutte verificate qui: si registra, l'esito negativo resta
scritto, la suite non inquina la misura, e un contatore non fa mai fallire
l'invocazione che sta contando.

DB isolato per test: mai toccare quello reale.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

import executor_aging
from executor_metadata import DEFAULT_EXECUTION_POLICY
from executor_scheduler import ExecutorScheduler


@dataclass
class _Executor:
    """Un'unita' firmata su disco: `code_path` e' cio' che la distingue dagli
    slot interni che lo scheduler ammette (Tutor mode/sonde/compositore)."""

    name: str = "find_files"
    code_path: Path = Path("/opt/metnos/executors/find_files/find_files.py")
    execution_policy: dict = field(
        default_factory=lambda: dict(DEFAULT_EXECUTION_POLICY))


@dataclass
class _SlotInterno:
    """Come il Tutor lo passa: nome e politica, nessuna unita' su disco."""

    name: str = "tutor_mode"
    execution_policy: dict = field(
        default_factory=lambda: dict(DEFAULT_EXECUTION_POLICY))


@pytest.fixture
def db_isolato(tmp_path, monkeypatch):
    """DB in tmp_path: mai il registro reale."""
    monkeypatch.setattr(executor_aging, "DB_PATH", tmp_path / "executor_stats.db")
    return tmp_path


def _fuori_dalla_suite(monkeypatch) -> None:
    """Il gancio per contratto non registra sotto pytest, e pytest rimette
    `PYTEST_CURRENT_TEST` a ogni fase: per misurare il gancio bisogna uscire
    dalla suite QUI, nel corpo del test, non in una fixture."""
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)


def test_una_invocazione_lascia_traccia(db_isolato, monkeypatch) -> None:
    _fuori_dalla_suite(monkeypatch)
    scheduler = ExecutorScheduler(max_workers=2, max_in_flight=2)

    assert scheduler.invoke(_Executor(), lambda: {"ok": True}) == {"ok": True}

    riga = executor_aging.lookup("find_files")
    assert riga is not None, "l'invocazione non e' stata registrata"
    assert riga.total_calls == 1
    assert riga.last_call_ok is True
    assert riga.last_used_at


def test_l_esito_negativo_resta_scritto(db_isolato, monkeypatch) -> None:
    """`change_observer` fa rollback su `last_call_ok=False`: un fallimento
    scritto come successo sarebbe un esito dichiarato e non corrispondente
    (§2.8)."""
    _fuori_dalla_suite(monkeypatch)
    scheduler = ExecutorScheduler(max_workers=2, max_in_flight=2)

    scheduler.invoke(_Executor(), lambda: {"ok": True})
    scheduler.invoke(_Executor(), lambda: {"ok": False, "error": "x"})

    riga = executor_aging.lookup("find_files")
    assert riga.total_calls == 2
    assert riga.last_call_ok is False


def test_un_executor_che_esplode_conta_come_fallito(db_isolato, monkeypatch) -> None:
    _fuori_dalla_suite(monkeypatch)
    scheduler = ExecutorScheduler(max_workers=2, max_in_flight=2)

    def _esplode() -> dict:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        scheduler.invoke(_Executor(), _esplode)

    riga = executor_aging.lookup("find_files")
    assert riga.total_calls == 1
    assert riga.last_call_ok is False


def test_gli_slot_interni_non_sono_executor(db_isolato, monkeypatch) -> None:
    """Il Tutor passa allo scheduler mode/sonde/compositore per lo slot LLM:
    hanno un nome e una politica, non un ciclo di vita. Una riga qui li
    farebbe comparire nel registro degli executor e nell'aging, che itera su
    tutte le righe che trova."""
    _fuori_dalla_suite(monkeypatch)
    scheduler = ExecutorScheduler(max_workers=2, max_in_flight=2)

    scheduler.invoke(_SlotInterno(), lambda: {"ok": True})

    assert executor_aging.lookup("tutor_mode") is None
    assert executor_aging.all_stats() == []


def test_la_suite_non_inquina_la_misura_di_esercizio(db_isolato, monkeypatch) -> None:
    """La suite invoca executor veri migliaia di volte con casi costruiti a
    mano. Sommarli al traffico reale falserebbe l'unica misura su cui un
    executor viene deprecato — stesso motivo di `engine/guard_stats`."""
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "qualche::test")
    scheduler = ExecutorScheduler(max_workers=2, max_in_flight=2)

    scheduler.invoke(_Executor(), lambda: {"ok": True})

    assert executor_aging.lookup("find_files") is None


def test_un_db_illeggibile_non_ferma_l_invocazione(db_isolato, monkeypatch) -> None:
    """Un contatore non deve far fallire l'invocazione che sta contando."""
    _fuori_dalla_suite(monkeypatch)
    monkeypatch.setattr(
        executor_aging, "DB_PATH", Path("/proc/non-scrivibile/executor_stats.db"))
    scheduler = ExecutorScheduler(max_workers=2, max_in_flight=2)

    assert scheduler.invoke(
        _Executor(), lambda: {"ok": True, "entries": []})["ok"] is True
