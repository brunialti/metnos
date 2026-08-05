"""test_device_shim_i18n — il device rende i messaggi user-facing (§7.13) invece
del codice grezzo (bug 7/7: get_files sul PC mostrava `ERR_PATH_NOT_FOUND
path=...`). Repertorio en+it bundleato con lo shim, generato dal DB i18n.

Guardia-drift: rigenera dal DB e confronta col file committato → un cambio non
allineato al DB rompe il baseline (come lo shim content-addressing)."""
import importlib.util
import json
import os
import sys
from pathlib import Path

_DEVSHIM = (Path(__file__).resolve().parents[3] / "runtime") / "device_shim"
sys.path.insert(0, str(_DEVSHIM.parent))  # runtime/ (per i18n dentro gen_i18n)
sys.path.insert(0, str(_DEVSHIM))         # device_shim/

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


def test_device_rende_it_en():
    _dsm = _load_device_messages()
    os.environ["METNOS_LANG"] = "it"
    assert _dsm.get("ERR_PATH_NOT_FOUND", path="/mnt/x.jpg") == "Percorso non trovato: /mnt/x.jpg."
    os.environ["METNOS_LANG"] = "en"
    assert _dsm.get("ERR_PATH_NOT_FOUND", path="X") == "Path not found: X."


def test_device_passthrough_su_ignoto():
    _dsm = _load_device_messages()
    os.environ["METNOS_LANG"] = "it"
    # Codice non nel repertorio → passthrough onesto (codice + kwargs), MAI testo inventato.
    out = _dsm.get("ERR_CODICE_INESISTENTE_XYZ", a="1")
    assert out == "ERR_CODICE_INESISTENTE_XYZ a=1"


def test_copre_codici_comuni_executor():
    """Sanity: i codici che get_files/backends emettono sono resi (non passthrough)."""
    _dsm = _load_device_messages()
    os.environ["METNOS_LANG"] = "it"
    for code in ("ERR_PATH_NOT_FOUND", "ERR_ARG_ENUM", "ERR_ARG_MISSING",
                 "ERR_ARG_NOT_LIST_OF", "ERR_ARG_MISSING_ONE_OF"):
        rendered = _dsm.get(code, arg="x", of="y", path="p", allowed="a", options="o")
        assert not rendered.startswith(code), f"{code} non reso (passthrough)"
