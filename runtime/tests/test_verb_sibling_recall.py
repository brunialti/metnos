"""META-fix recall verbi-sibling di lifecycle (§7.3, 3/6/2026).

Il vocabolario context-free _VERB_TO_CANONICAL e' 1:1 e LOSSY: "metti/salva" ->
"write" soltanto, quindi i producer create_* perdevano il verb-boost e venivano
ESCLUSI da rank_with_intent (parts[0] != verb -> continue). Il planner sceglieva
write e inventava un path (bug spreadsheet 2-3/6). Fix: _VERB_ALSO_CANONICAL
surfaccia i sibling di lifecycle (write<->create); il primario resta preferito,
il downstream (write=UPSERT, SCOPO, L6) decide. Auto-limitante: il sibling
compare solo se quel producer esiste per l'oggetto.
"""
import sys
from pathlib import Path

_RT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_RT))
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


def _names(q, verb, obj, k=8):
    r = P.rank_with_intent(q, _CAT, {"verb": verb, "object": obj}, k=k) or []
    return [e.name for e in r]


def test_write_intent_surfaces_create_sibling_but_primary_first():
    n = _names("metti i dati in uno spreadsheet", "write", "files")
    assert "write_files_spreadsheet" in n and "create_files_spreadsheet" in n
    assert n.index("write_files_spreadsheet") < n.index("create_files_spreadsheet")


def test_create_intent_surfaces_write_sibling():
    n = _names("crea un foglio coi dati", "create", "files")
    assert "create_files_spreadsheet" in n and "write_files_spreadsheet" in n


def test_monosemic_verb_not_polluted():
    # delete non ha sibling -> nessun create_*/write_* nel pool.
    n = _names("cancella i file temporanei", "delete", "files")
    assert n and all(not x.startswith(("create_", "write_")) for x in n)


def test_sibling_only_if_producer_exists():
    # "salva l'evento": write_events NON esiste -> sibling create_events surfacea.
    n = _names("salva questo evento", "write", "events")
    assert "create_events" in n


def test_bow_path_also_surfaces_both():
    r = P.rank("metti i dati in uno spreadsheet xlsx", _CAT, k=6) or []
    n = [getattr(e, "name", e) for e in r]
    assert "create_files_spreadsheet" in n and "write_files_spreadsheet" in n
