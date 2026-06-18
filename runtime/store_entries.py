"""store_entries.py — skill builtin «store generico»: find/write/delete_entries.

Pelle sottile (inproc) sopra `store.Store` via il REGISTRO (`store.get_store`).
Builtin INPROC (non subprocess) perché il registro è in-processo. Regola
(Roberto): stessi nomi executor, backend diversi — il backend è scelto nel
registro, MAI da query. §2.6 (find→entries, write/delete→results), §7.9.

Dormienza (loader/routing_pool): se il registro è VUOTO questi 3 sono ESCLUSI
dal pool (nessun bersaglio → niente inquinamento del routing). Si «svegliano»
quando ≥1 store è registrato. Affinità RISTRETTA (store/archivio/raccolta), non
verbi generici → «trova le foto» non li tocca mai.
"""
from __future__ import annotations

import store as _store


def _resolve(name):
    """Store registrato o errore onesto §2.8 (l'unregistered diventa un
    risultato ok:False, non un'eccezione che rompe il turno)."""
    try:
        return _store.get_store(name), None
    except KeyError:
        return None, {
            "ok": False, "error_class": "missing_input",
            "error": (f"store «{name}» non registrato. Store disponibili: "
                      f"{_store.registered() or '(nessuno)'}."),
        }


def handle_find_entries(args, *, verbose: bool = False) -> dict:
    a = args or {}
    name = (a.get("store") or "").strip()
    if not name:
        return {"ok": False, "error_class": "invalid_args",
                "error": "manca 'store' (nome dello store da interrogare)",
                "entries": []}
    st, err = _resolve(name)
    if err:
        err["entries"] = []
        return err
    where = a.get("where") if isinstance(a.get("where"), dict) else None
    order = a.get("order")
    limit = a.get("max_results") or a.get("limit")
    rows = st.find(where=where, order=order,
                   limit=int(limit) if limit else None)
    return {"ok": True, "entries": rows, "metadata": {"count": len(rows)}}


def handle_write_entries(args, *, verbose: bool = False) -> dict:
    a = args or {}
    name = (a.get("store") or "").strip()
    if not name:
        return {"ok": False, "error_class": "invalid_args",
                "error": "manca 'store' (nome dello store su cui scrivere)",
                "results": []}
    entries = a.get("entries")
    if not isinstance(entries, list):
        return {"ok": False, "error_class": "invalid_args",
                "error": ("manca 'entries' (lista record): passa from_step=N "
                          "del producer da persistere"),
                "results": []}
    # set_fields (P3 redesign 18/6): override DETERMINISTICO di campi su OGNI
    # entry prima dell'upsert — es. FASE 3 "aggiorna lo store a posted":
    # write_entries(from_step=N, key=["id"], set_fields={"status":"posted"}).
    # Risolve un §2.8 silent failure: il proposer emetteva set_fields/fields ma
    # il handler li IGNORAVA → lo stato NON veniva mai aggiornato pur con ok:True
    # (record ri-scritti con lo status VECCHIO). Alias 'fields' accettato (il
    # modello usa entrambe le forme). §7.9 deterministico, no LLM.
    set_fields = a.get("set_fields")
    if not isinstance(set_fields, dict):
        set_fields = a.get("fields") if isinstance(a.get("fields"), dict) else None
    if set_fields:
        entries = [{**e, **set_fields} if isinstance(e, dict) else e
                   for e in entries]
    st, err = _resolve(name)
    if err:
        err["results"] = []
        return err
    # Valore iniziale dei campi assenti: deterministico da `store.insert_defaults`
    # (config di registrazione, applicato in Store.write) — NON dall'arg-filling
    # del proposer. Vedi store_bootstrap (github_issue_qa: status='new').
    key = a.get("key")
    if isinstance(key, str):
        key = [key]
    n = st.write(entries, key=key)
    return {"ok": True, "n_written": n,
            "results": [{"written": True} for _ in range(n)],
            "metadata": {"store": name, "n_written": n}}


def handle_delete_entries(args, *, verbose: bool = False) -> dict:
    a = args or {}
    name = (a.get("store") or "").strip()
    if not name:
        return {"ok": False, "error_class": "invalid_args",
                "error": "manca 'store' (nome dello store da cui eliminare)",
                "results": []}
    st, err = _resolve(name)
    if err:
        err["results"] = []
        return err
    where = a.get("where") if isinstance(a.get("where"), dict) else None
    n = st.delete(where=where)
    return {"ok": True, "n_deleted": n,
            "results": [{"deleted": True} for _ in range(n)],
            "metadata": {"store": name, "n_deleted": n}}


# ── Tool specs OpenAI-style (per Engine v2 validator + composer) ───────────
FIND_ENTRIES_TOOL = {
    "type": "function",
    "function": {
        "name": "find_entries",
        "description": (
            "SCOPO: legge record da uno STORE generico NOMINATO (archivio/"
            "raccolta dati interna, non file/mail/eventi). PATTERN: "
            "find_entries(store=\"spese\", where={\"mese\":\"06\"}, "
            "max_results=50). NON: file su disco -> find_files; mail -> "
            "read_messages; filtrare una lista GIÀ in memoria -> filter_entries. "
            "OUT: entries=[{...}]."),
        "parameters": {
            "type": "object",
            "required": ["store"],
            "properties": {
                "store": {"type": "string",
                          "description": "Nome dello store (archivio) da "
                                         "interrogare, es. \"spese\"."},
                "where": {"type": "object",
                          "description": "Filtro di uguaglianza {campo: valore}; "
                                         "valore lista = IN. Es. {\"stato\":"
                                         "\"aperto\"}."},
                "order": {"type": "array", "items": {"type": "string"},
                          "description": "Campi di ordinamento, es. [\"data\"]."},
                "max_results": {"type": "integer",
                                "description": "Cap risultati (§2.1)."},
            },
        },
    },
}

WRITE_ENTRIES_TOOL = {
    "type": "function",
    "function": {
        "name": "write_entries",
        "description": (
            "SCOPO: salva/aggiorna (UPSERT, crea-se-manca) record in uno STORE "
            "generico NOMINATO; aggiorna campi coi set_fields. PATTERN: producer "
            "allo step N poi write_entries(store=\"spese\", from_step=N, "
            "key=[\"id\"], set_fields={\"status\":\"posted\"}). NON: scrivere "
            "file -> write_files; inviare -> send_messages. Crea lo store e i "
            "record se mancano. OUT: results, n_written."),
        "parameters": {
            "type": "object",
            "required": ["store", "from_step"],
            "properties": {
                "store": {"type": "string",
                          "description": "Nome dello store su cui scrivere."},
                "from_step": {"type": "integer", "minimum": 1,
                              "description": "Step che ha prodotto i record da "
                                             "persistere (il runtime espande in "
                                             "entries)."},
                "key": {"type": "array", "items": {"type": "string"},
                        "description": "Campi-chiave per l'upsert (conflitto). "
                                       "Es. [\"id\"]. Assente -> insert puro."},
                "set_fields": {"type": "object",
                               "description": "Override {campo: valore} applicato "
                                              "a OGNI record prima dell'upsert "
                                              "(aggiorna lo stato). Es. "
                                              "{\"status\":\"posted\"}."},
            },
        },
    },
}

DELETE_ENTRIES_TOOL = {
    "type": "function",
    "function": {
        "name": "delete_entries",
        "description": (
            "SCOPO: elimina record da uno STORE generico NOMINATO. PATTERN: "
            "delete_entries(store=\"spese\", where={\"id\":\"x\"}). NON: file -> "
            "delete_files; mail -> move_messages(dst_folder=\"Trash\"). where "
            "assente/vuoto = svuota lo store. OUT: results, n_deleted."),
        "parameters": {
            "type": "object",
            "required": ["store"],
            "properties": {
                "store": {"type": "string",
                          "description": "Nome dello store da cui eliminare."},
                "where": {"type": "object",
                          "description": "Filtro {campo: valore} dei record da "
                                         "eliminare; assente = svuota."},
            },
        },
    },
}

# Affinità RISTRETTA store-specifica (IT+EN): solo query che nominano un
# archivio/raccolta generico arrivano qui — niente verbi generici.
_AFFINITY = ["store", "archivio", "archivi", "raccolta", "collezione",
             "registro dati", "database interno", "collection", "datastore",
             "memorizza nello store", "salva nell'archivio"]

BUILTIN_INPROC_SPECS = [
    {"name": "find_entries", "tool_spec": FIND_ENTRIES_TOOL,
     "affinity": _AFFINITY},
    {"name": "write_entries", "tool_spec": WRITE_ENTRIES_TOOL,
     "affinity": _AFFINITY},
    {"name": "delete_entries", "tool_spec": DELETE_ENTRIES_TOOL,
     "affinity": _AFFINITY},
]
