"""I due meccanismi generali che l'installazione ha richiesto (17/8/2026).

Nessuno dei due nomina `install_packages`: sono proprieta' del runtime che
un dominio nuovo ha reso necessarie, non casi speciali per quel dominio.

1. **Autorita' sul server** (ADR 0209 D4, emendato): sul server un'azione
   sotto autorita' amministrativa la fa solo un amministratore dell'istanza.
   La regola e' guidata dalla capability `system:admin`, non da una lista di
   nomi. Il suo gemello — nessuno installa su un dispositivo che non possiede,
   l'amministratore compreso — era gia' strutturale, ed e' verificato qui che
   lo resti.

2. **Rimedio a un'operazione irreversibile** (ADR 0209 D3): dire soltanto
   «non annullabile» lascia la persona senza la strada; eseguire il rimedio da
   soli sarebbe peggio, perche' non e' cio' che ha chiesto. L'executor
   DICHIARA il rimedio nel manifest firmato, l'undo lo propone come domanda.

Run: `python3 -m pytest tests/runtime/executors/test_install_authority_and_undo.py -v`
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
_RUNTIME = ROOT / "runtime"
for p in (str(ROOT), str(_RUNTIME)):
    if p not in sys.path:
        sys.path.insert(0, p)


def _ex(name, caps):
    return SimpleNamespace(
        name=name,
        capabilities=[{"name": c} for c in caps],
        args_schema={"type": "object", "properties": {}},
    )


# ── 1. Autorita' sul server ───────────────────────────────────────────
@pytest.fixture
def registry(monkeypatch):
    """Un proprietario e un ospite, senza toccare il registro reale."""
    import devices as _devices
    import users as _users

    utenti = {"id-host": {"id": "id-host", "name": "proprietario",
                          "role": "host"},
              "id-ospite": {"id": "id-ospite", "name": "ospite",
                            "role": "guest"}}
    monkeypatch.setattr(_users, "get_user", lambda uid: utenti.get(uid))
    monkeypatch.setattr(_devices, "owner_id_for_actor", lambda a: a or "")
    return utenti


def test_sul_server_un_ospite_non_esegue_un_azione_amministrativa(registry):
    from invocation_scope import check_invocation_scope
    denial = check_invocation_scope(
        _ex("install_packages", ["system:admin", "code:exec"]),
        {"packages": ["x"]}, actor="id-ospite")
    assert denial, "un ospite non amministra l'istanza"


def test_sul_server_l_amministratore_esegue(registry):
    from invocation_scope import check_invocation_scope
    assert check_invocation_scope(
        _ex("install_packages", ["system:admin", "code:exec"]),
        {"packages": ["x"]}, actor="id-host") is None


def test_un_attore_sconosciuto_non_e_amministratore(registry):
    """Fail closed: non essere riconosciuti non e' un permesso."""
    from invocation_scope import check_invocation_scope
    assert check_invocation_scope(
        _ex("install_packages", ["system:admin"]),
        {"packages": ["x"]}, actor="tizio-mai-visto")


def test_la_lettura_resta_libera_per_tutti(registry):
    """La regola guarda `system:admin`, non il dominio: sapere CHE COSA e'
    installato non modifica niente e non chiede autorita'."""
    from invocation_scope import check_invocation_scope
    for attore in ("id-host", "id-ospite", "tizio-mai-visto"):
        assert check_invocation_scope(
            _ex("find_packages", ["system:read", "code:exec"]),
            {"packages": ["x"]}, actor=attore) is None, attore


def test_la_regola_e_guidata_dalla_capability_non_da_una_lista(registry):
    """Un executor qualunque che dichiari `system:admin` e' soggetto alla
    stessa regola: non c'e' nessun nome cablato."""
    from invocation_scope import check_invocation_scope
    assert check_invocation_scope(
        _ex("un-executor-che-non-esiste-ancora", ["system:admin"]),
        {}, actor="id-ospite")


def test_il_messaggio_dice_anche_dove_si_puo_fare(registry):
    """§2.8: un rifiuto che non indica la strada e' meta' informazione."""
    from invocation_scope import check_invocation_scope
    denial = check_invocation_scope(
        _ex("install_packages", ["system:admin"]),
        {"packages": ["x"]}, actor="id-ospite")
    assert "dispositiv" in denial.lower() or "device" in denial.lower()


def test_il_filtro_proprietario_dei_device_resta_senza_ramo_admin():
    """Il gemello della regola. Non si verifica con un test funzionale
    perche' e' un'ASSENZA: nel filtro dei device non deve comparire nessuna
    eccezione per l'amministratore, oggi ne' domani."""
    sorgente = (_RUNTIME / "agent_runtime.py").read_text(encoding="utf-8")
    blocco = sorgente[sorgente.index("_devs_for_actor = ["):]
    blocco = blocco[:400]
    assert "owner_user_id" in blocco
    for eccezione in ("role ==", "is_admin", '"admin"', "'admin'"):
        assert eccezione not in blocco, (
            f"perimetro device allargato per {eccezione}: essere "
            "amministratore dell'istanza non e' autorita' sulla macchina "
            "personale di un'altra persona (ADR 0209 D4, emendamento 17/8)")


# ── 2. Il rimedio dichiarato ──────────────────────────────────────────
sys.path.insert(0, str(ROOT / "executors" / "undo_last_turn"))
import undo_last_turn as _undo  # noqa: E402


def _rec(args):
    return {"op_id": "op-1", "turn_id": "t-1",
            "executor": "install_packages", "plan": {"args": args}}


def test_un_operazione_irreversibile_con_rimedio_produce_una_domanda():
    ex = SimpleNamespace(name="install_packages", revertible=False, undo={
        "remedy_executor": "install_packages",
        "remedy_args_from": ["packages", "scope"],
        "remedy_args_fixed": {"uninstall": True},
        "remedy_prompt_key": "MSG_UNDO_INSTALL_ASK_UNINSTALL",
    })
    out = _undo._remedy_question(ex, _rec({"packages": ["Foo.Bar"],
                                           "scope": "machine"}))

    assert out["decision"] == "needs_inputs"
    assert out["undone_count"] == 0, "non ha annullato niente, e lo dice"
    assert out["needs_inputs"]["dialog"][0]["schema"]["kind"] == "yes_no"


def test_il_rimedio_non_viene_eseguito_da_solo():
    """Il punto: si CHIEDE. Il payload descrive un'operazione da fare dopo
    una conferma, e non c'e' nessuna invocazione dentro questa funzione."""
    ex = SimpleNamespace(name="install_packages", revertible=False, undo={
        "remedy_executor": "install_packages",
        "remedy_args_from": ["packages"],
        "remedy_args_fixed": {"uninstall": True},
    })
    out = _undo._remedy_question(ex, _rec({"packages": ["Foo.Bar"]}))
    oc = out["needs_inputs"]["on_complete"]
    assert oc["type"] == "resume_executor_with_values"
    assert oc["args_base"]["uninstall"] is True
    assert oc["args_base"]["packages"] == ["Foo.Bar"]


def test_il_rimedio_agisce_su_cio_che_era_stato_fatto():
    """Si disinstalla quello che si era installato, non altro: gli argomenti
    vengono dall'operazione originale, non da una nuova interpretazione."""
    ex = SimpleNamespace(name="install_packages", revertible=False, undo={
        "remedy_executor": "install_packages",
        "remedy_args_from": ["packages", "scope"],
        "remedy_args_fixed": {"uninstall": True},
    })
    out = _undo._remedy_question(
        ex, _rec({"packages": ["A.Uno", "B.Due"], "scope": "user",
                  "actor_consent_token": "vecchio-token"}))
    base = out["needs_inputs"]["on_complete"]["args_base"]
    assert base["packages"] == ["A.Uno", "B.Due"]
    assert base["scope"] == "user"
    assert "actor_consent_token" not in base, (
        "un consenso vecchio non vale per un'operazione nuova")


def test_senza_dichiarazione_resta_il_vecchio_non_annullabile():
    """La domanda esiste solo dove un rimedio e' DICHIARATO: l'undo non
    inventa rimedi per gli executor che non ne hanno."""
    ex = SimpleNamespace(name="qualcosa", revertible=False, undo={})
    assert _undo._remedy_question(ex, _rec({"paths": ["/x"]})) is None


def test_senza_gli_argomenti_originali_non_si_chiede_niente():
    """Una domanda generica su un'azione che modifica una macchina non e' un
    consenso utilizzabile: meglio il vecchio «non annullabile»."""
    ex = SimpleNamespace(name="install_packages", revertible=False, undo={
        "remedy_executor": "install_packages",
        "remedy_args_from": ["packages"],
    })
    assert _undo._remedy_question(ex, _rec({})) is None


def test_il_meccanismo_non_nomina_nessun_dominio():
    """Il codice dell'undo non conosce pacchetti: legge una dichiarazione."""
    codice = (ROOT / "executors" / "undo_last_turn"
              / "undo_last_turn.py").read_text(encoding="utf-8")
    for dominio in ("install_packages", "winget", "package", "apt-get"):
        assert dominio not in codice, f"dominio cablato nell'undo: {dominio}"


def test_la_dichiarazione_di_install_packages_e_firmata():
    """La sezione `[undo]` vive nel manifest FIRMATO: un rimedio non
    dichiarato sotto firma sarebbe un'operazione proposta da chiunque."""
    import tomllib
    m = tomllib.loads((ROOT / "executors" / "install_packages"
                       / "manifest.toml").read_text(encoding="utf-8"))
    assert m["undo"]["remedy_executor"] == "install_packages"
    assert m["undo"]["remedy_args_fixed"]["uninstall"] is True
    assert m["revertible"] is False
    assert (ROOT / "executors" / "install_packages"
            / "manifest.toml.sig").is_file()
