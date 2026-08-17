"""Un'azione approvata torna DOVE appartiene (17/8/2026).

Difetto reale, turno a97056e1. «Installa LibreHardwareMonitor su pc-roberto»
ha chiesto conferma al PC — la scheda mostrava la versione e l'impronta prese
dal catalogo Windows — e alla conferma ha provato a installare **sul server**,
con il gestore di pacchetti di Linux:

    Could not open lock file /var/lib/dpkg/lock-frontend

La causa: la ripresa passa per `orchestration`, che invocava l'executor senza
destinazione. Nessuno se n'era accorto prima perche' il cancello di consenso
era stato usato solo per azioni che appartengono al server (gli invii), dove
la destinazione mancante coincide con quella giusta.

La destinazione la sa il RUNTIME, non l'executor: viene annotata una volta sul
callback e valgono entrambe le riprese, per qualunque dominio.

Run: `python3 -m pytest tests/runtime/engine/test_gate_resume_keeps_device.py -v`
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_RUNTIME = Path(__file__).resolve().parents[3] / "runtime"
if str(_RUNTIME) not in sys.path:
    sys.path.insert(0, str(_RUNTIME))


# ── L'annotazione: il callback impara dove e' girato il passo ─────────
def _run_con_passo(host, callback):
    passo = SimpleNamespace(
        tool="install_packages", step_idx=1, host=host,
        args={}, if_prev_entries_nonempty=False,
        result={"decision": "needs_inputs",
                "needs_inputs": {"on_complete": callback}},
    )
    return SimpleNamespace(steps=[passo], framework_hash="h")


def _annota(host, callback):
    """Chiama il codice VERO (`_inject_gate_resume_if_paused`), non una copia.

    Un test che riproduce la logica passerebbe anche cancellando il codice
    che dovrebbe proteggere: e' fiducia mal riposta, non copertura.
    """
    from engine.dispatch import _inject_gate_resume_if_paused
    from engine.types import Framework, StepSpec
    run = _run_con_passo(host, callback)
    framework = Framework(steps=[StepSpec(tool="install_packages", args={})])
    _inject_gate_resume_if_paused(run, "installa X", {}, framework=framework)
    return callback


def test_un_passo_girato_su_un_device_annota_la_destinazione():
    cb = _annota("PC-ROBERTO", {"type": "gate_dispatch"})
    assert cb["target_device"] == "PC-ROBERTO"


def test_un_passo_girato_sul_server_non_annota_niente():
    """Il server e' il default: annotarlo aggiungerebbe rumore senza
    cambiare nulla."""
    cb = _annota("server", {"type": "gate_dispatch"})
    assert "target_device" not in cb


def test_una_destinazione_gia_scritta_non_viene_sovrascritta():
    cb = _annota("PC-ROBERTO", {"type": "gate_dispatch",
                                "target_device": "ALTRO-PC"})
    assert cb["target_device"] == "ALTRO-PC"


# ── La ripresa: la destinazione arriva all'invocazione ────────────────
@pytest.fixture
def invocazioni(monkeypatch):
    """Cattura come `orchestration` invoca l'executor, senza eseguirlo."""
    import agent_runtime
    import loader
    visti = []

    finto = SimpleNamespace(name="install_packages", timeout_s=30)
    monkeypatch.setattr(loader, "load_catalog",
                        lambda **kw: SimpleNamespace(
                            executors={"install_packages": finto}))

    def finta_invoke(ex, args, **kw):
        visti.append({"executor": ex.name, "args": args,
                      "target_device": kw.get("target_device")})
        return {"ok": True, "final_message_hint": "fatto"}

    monkeypatch.setattr(agent_runtime, "invoke_executor", finta_invoke)
    return visti


def test_il_ramo_approvato_torna_sul_device(invocazioni):
    """Il caso del difetto: conferma data al PC, esecuzione sul PC."""
    import orchestration as O
    O._process_gate_dispatch(
        {"type": "gate_dispatch", "approve_value": "approve",
         "target_device": "PC-ROBERTO",
         "on_approve": {"tool": "install_packages",
                        "args": {"packages": ["X"]}}},
        {"decision": "approve"}, actor="host", channel="http")

    assert invocazioni, "il ramo approvato non e' stato eseguito"
    assert invocazioni[0]["target_device"] == "PC-ROBERTO"


def test_senza_device_la_ripresa_resta_sul_server(invocazioni):
    """Un'azione che appartiene al server continua a girare li': la
    destinazione assente vale «server», non un errore."""
    import orchestration as O
    O._process_gate_dispatch(
        {"type": "gate_dispatch", "approve_value": "approve",
         "on_approve": {"tool": "install_packages", "args": {}}},
        {"decision": "approve"}, actor="host", channel="http")

    assert invocazioni[0]["target_device"] is None


def test_un_rifiuto_non_esegue_niente(invocazioni):
    """Il cancello di consenso, ricontrollato qui perche' e' la ragione per
    cui esiste: su un «no» non parte nessuna invocazione."""
    import orchestration as O
    O._process_gate_dispatch(
        {"type": "gate_dispatch", "approve_value": "approve",
         "target_device": "PC-ROBERTO",
         "on_approve": {"tool": "install_packages",
                        "args": {"packages": ["X"]}}},
        {"decision": "reject"}, actor="host", channel="http")

    assert invocazioni == [], "un rifiuto ha eseguito il ramo"


def test_anche_la_ripresa_con_valori_torna_sul_device(invocazioni):
    """L'altra ripresa ha lo stesso difetto e la stessa cura: una
    disambiguazione nata su un device si conclude su quel device."""
    import orchestration as O
    O._process_resume_executor_with_values(
        {"type": "resume_executor_with_values",
         "executor": "install_packages", "args_base": {"packages": ["X"]},
         "target_device": "PC-ROBERTO"},
        {"scelta": "prima"}, actor="host", channel="http")

    assert invocazioni[0]["target_device"] == "PC-ROBERTO"
