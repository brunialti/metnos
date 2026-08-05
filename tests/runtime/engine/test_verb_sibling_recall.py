"""Il verbo dell'intent e' una CONGETTURA: il pool si chiude sulla classe.

L'estrattore proietta la richiesta su UN verbo canonico, e la proiezione e' 1:1
quindi lossy: «metti/salva» finisce su `write` anche quando l'operazione giusta
e' `create` (bug spreadsheet 2-3/6/2026), «togli / azzera / rimetti al valore
predefinito» finisce su `set` anche quando e' `delete` (E2E preferenze
29/7/2026). Chiudere il pool sul verbo esatto fa sparire tool che esistono.

Sul lato che CAMBIA STATO il cancello si chiude quindi sulla classe e il verbo
esatto resta il punteggio piu' alto: recall dalla classe, precisione dal verbo.
Sul lato di sola lettura il verbo resta un cancello — li' allargare peggiora
(misurato: 9 cambi del primo classificato, tutti regressioni).

Vedi `prefilter.implements_intent_verb` per la misura completa. La tabella di
coppie scritta a mano che stava qui e' stata rimossa: il concetto la sostituisce.
"""
import sys
from pathlib import Path

_RT = (Path(__file__).resolve().parents[3] / "runtime")
import prefilter as P  # noqa: E402
from loader import Executor  # noqa: E402


def _ex(name, aff=None):
    return Executor(name=name, version="1", description=name,
                    affinity=aff or [name.split("_")[0]], args_schema={},
                    capabilities=[], tests=[], code_path=None,
                    manifest_path=Path("/x"), signed_by="t")


_CAT = [_ex("write_files_spreadsheet", ["scrivi", "salva", "foglio", "spreadsheet"]),
        _ex("create_files_spreadsheet", ["crea", "nuovo", "foglio", "spreadsheet"]),
        _ex("write_files", ["scrivi", "salva"]),
        _ex("create_events", ["crea", "evento", "fissa"]),
        _ex("delete_files", ["cancella", "elimina"]),
        _ex("read_files", ["leggi"]),
        _ex("send_messages", ["invia", "manda", "mail"])]


def _names(q, verb, obj, k=8, catalog=None):
    r = P.rank_with_intent(q, catalog or _CAT, {"verb": verb, "object": obj},
                           k=k) or []
    return [e.name for e in r]


# --- il predicato, in isolamento ------------------------------------------

def test_predicate_is_reflexive_and_class_bound():
    assert P.implements_intent_verb("write", "write")
    # Lato che muta: la classe apre, il verbo esatto restera' preferito.
    assert P.implements_intent_verb("create", "write")
    assert P.implements_intent_verb("delete", "set")
    assert P.implements_intent_verb("set", "delete")
    # Un verbo di sola lettura non realizza un intento che muta.
    assert not P.implements_intent_verb("read", "delete")


def test_predicate_keeps_read_only_intents_strict():
    """Sul lato di sola lettura il verbo resta un cancello."""
    assert not P.implements_intent_verb("get", "read")
    assert not P.implements_intent_verb("sort", "find")
    assert not P.implements_intent_verb("delete", "read")


# --- effetto sul pool ------------------------------------------------------

def test_write_intent_surfaces_create_but_primary_first():
    n = _names("metti i dati in uno spreadsheet", "write", "files")
    assert "write_files_spreadsheet" in n and "create_files_spreadsheet" in n
    assert n.index("write_files_spreadsheet") < n.index("create_files_spreadsheet")


def test_create_intent_surfaces_write():
    n = _names("crea un foglio coi dati", "create", "files")
    assert "create_files_spreadsheet" in n and "write_files_spreadsheet" in n


def test_set_intent_surfaces_delete_but_primary_first():
    """«togli la preferenza sul tono» -> l'estrattore dice `set`.

    Senza la classe il pool era tutto e solo `set_*` e il planner rispondeva
    «non esiste un tool per togliere una preferenza» pur avendone uno.
    """
    cat = _CAT + [_ex("set_preferences", ["imposta", "preferenza"]),
                  _ex("delete_preferences", ["togli", "preferenza"]),
                  _ex("get_preferences", ["preferenza", "elenco"])]
    n = _names("togli la preferenza sul tono", "set", "preferences",
               k=12, catalog=cat)
    assert "set_preferences" in n and "delete_preferences" in n
    assert n.index("set_preferences") < n.index("delete_preferences")


def test_producer_intent_not_widened_to_mutating_tools():
    """Un intento di sola lettura non tira dentro i tool che mutano."""
    n = _names("leggi i file di configurazione", "read", "files")
    assert n and all(not x.startswith(("delete_", "send_")) for x in n)


def test_sibling_only_if_producer_exists():
    # "salva l'evento": write_events NON esiste -> create_events surfacea.
    n = _names("salva questo evento", "write", "events")
    assert "create_events" in n


def test_bow_path_also_surfaces_both():
    r = P.rank("metti i dati in uno spreadsheet xlsx", _CAT, k=6) or []
    n = [getattr(e, "name", e) for e in r]
    assert "create_files_spreadsheet" in n and "write_files_spreadsheet" in n
