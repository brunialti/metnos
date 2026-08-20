"""test_device_shim_i18n — il device rende i messaggi user-facing (§7.13) invece
del codice grezzo (bug 7/7: get_files sul PC mostrava `ERR_PATH_NOT_FOUND
path=...`). Repertorio en+it bundleato con lo shim, generato dal DB i18n.

Guardia-drift: rigenera dal DB e confronta col file committato → un cambio non
allineato al DB rompe il baseline (come lo shim content-addressing).

La lingua si cambia SOLO con `monkeypatch.setenv`, mai assegnando
`os.environ`: questo file e' l'unico della suite che la muove, e un valore
lasciato dietro vale per tutti i test raccolti dopo. Misurato il 16/8/2026 —
usciva `en` e la suite dei manifest, che confronta stringhe italiane, falliva
su una trentina di executor tutti innocenti."""
import importlib.util
import json
import os
import sys
from pathlib import Path

_DEVSHIM = (Path(__file__).resolve().parents[3] / "runtime") / "device_shim"
# Entrambi servono per importare il generatore, ma runtime/ deve restare prima:
# altrimenti un test raccolto dopo questo file puo' importare per errore lo shim
# remoto come modulo server ``messages``.
sys.path.insert(0, str(_DEVSHIM))         # device_shim/ (gen_i18n)
sys.path.insert(0, str(_DEVSHIM.parent))  # runtime/ (i18n/messages server)

import gen_i18n  # noqa: E402


def _load_device_messages():
    """Carica device_shim/messages.py da PATH ESPLICITO (evita lo shadowing con
    runtime/messages.py, che `gen_i18n` mette per primo sul path)."""
    spec = importlib.util.spec_from_file_location(
        "_dsm_under_test", _DEVSHIM / "messages.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_json_allineato_al_db():
    """Il messages_i18n.json committato == rigenerato dal DB i18n (no drift)."""
    committed = json.loads((_DEVSHIM / "messages_i18n.json").read_text("utf-8"))
    fresh = gen_i18n.build_templates()
    assert committed == fresh, (
        "messages_i18n.json disallineato dal DB i18n → rigenera con "
        "`python3 runtime/device_shim/gen_i18n.py` e committa")


def test_device_rende_it_en(monkeypatch):
    _dsm = _load_device_messages()
    monkeypatch.setenv("METNOS_LANG", "it")
    assert _dsm.get("ERR_PATH_NOT_FOUND", path="/mnt/x.jpg") == "Percorso non trovato: /mnt/x.jpg."
    monkeypatch.setenv("METNOS_LANG", "en")
    assert _dsm.get("ERR_PATH_NOT_FOUND", path="X") == "Path not found: X."


def test_device_passthrough_su_ignoto(monkeypatch):
    _dsm = _load_device_messages()
    monkeypatch.setenv("METNOS_LANG", "it")
    # Codice non nel repertorio → passthrough onesto (codice + kwargs), MAI testo inventato.
    out = _dsm.get("ERR_CODICE_INESISTENTE_XYZ", a="1")
    assert out == "ERR_CODICE_INESISTENTE_XYZ a=1"


def test_copre_codici_comuni_executor(monkeypatch):
    """Sanity: i codici che get_files/backends emettono sono resi (non passthrough)."""
    _dsm = _load_device_messages()
    monkeypatch.setenv("METNOS_LANG", "it")
    for code in ("ERR_PATH_NOT_FOUND", "ERR_ARG_ENUM", "ERR_ARG_MISSING",
                 "ERR_ARG_NOT_LIST_OF", "ERR_ARG_MISSING_ONE_OF"):
        rendered = _dsm.get(code, arg="x", of="y", path="p", allowed="a", options="o")
        assert not rendered.startswith(code), f"{code} non reso (passthrough)"


def test_device_rende_errori_from_step_it_en(monkeypatch):
    _dsm = _load_device_messages()
    expected = {
        "it": "Il riferimento from_step=3 non esiste. Intervallo valido: 1..2.",
        "en": "The from_step=3 reference does not exist. Valid range: 1..2.",
    }
    for language, text in expected.items():
        monkeypatch.setenv("METNOS_LANG", language)
        assert _dsm.get("ERR_FROM_STEP_RANGE", step=3, maximum=2) == text


def test_device_rende_provider_termico_it_en(monkeypatch):
    _dsm = _load_device_messages()
    expected = {
        "it": "Nessun provider compatibile ha restituito una temperatura hardware utilizzabile.",
        "en": "No compatible provider returned a usable hardware temperature.",
    }
    for language, text in expected.items():
        monkeypatch.setenv("METNOS_LANG", language)
        assert _dsm.get("ERR_THERMAL_PROVIDER_UNAVAILABLE") == text
    for translations in _dsm._I18N.values():
        assert "ERR_THERMAL_PROVIDER_INACTIVE" not in translations


def test_message_may_use_code_as_a_placeholder(monkeypatch):
    """The lookup key is positional, so a `{code}` placeholder cannot collide."""
    _dsm = _load_device_messages()
    monkeypatch.setenv("METNOS_LANG", "en")
    rendered = _dsm.get(
        "ERR_CREATE_PROCESSES_START_FAILED",
        package="Vendor.Sensor",
        code="package_start_failed",
    )
    assert "package_start_failed" in rendered
