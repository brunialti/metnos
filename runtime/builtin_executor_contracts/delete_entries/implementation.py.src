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
import detection_lexicon as _detlex
import detection_lexicon_seed_codegen as _codegen_seed
import i18n as _i18n


def _msg(key: str, **kwargs) -> str:
    _codegen_seed.ensure_registered()
    return _i18n.get(key, **kwargs)


def _store_affinity() -> list[str]:
    _codegen_seed.ensure_registered()
    return _detlex.forms("codegen.store_entries_affinity")


def _resolve(name):
    """Store registrato o errore onesto §2.8 (l'unregistered diventa un
    risultato ok:False, non un'eccezione che rompe il turno)."""
    try:
        return _store.get_store(name), None
    except KeyError:
        available = _store.registered() or _msg("MSG_STORE_NONE")
        return None, {
            "ok": False, "error_class": "missing_input",
            "error": _msg(
                "ERR_STORE_NOT_REGISTERED", name=name, available=available,
            ),
        }


def handle_find_entries(args, *, verbose: bool = False) -> dict:
    a = args or {}
    name = (a.get("store") or "").strip()
    if not name:
        return {"ok": False, "error_class": "invalid_args",
                "error": _msg("ERR_STORE_REQUIRED_FIND"),
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
                "error": _msg("ERR_STORE_REQUIRED_WRITE"),
                "results": []}
    entries = a.get("entries")
    if not isinstance(entries, list):
        return {"ok": False, "error_class": "invalid_args",
                "error": _msg("ERR_STORE_ENTRIES_REQUIRED"),
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
    # §2.4 confine NL->determinismo: una key che NON e' colonna dello schema
    # ma ha un SINONIMO documentato che lo e' viene rimappata (bug live 6/7:
    # find_issues_github porta `number` E `issue_number`, il proposer sceglie
    # `number`, la colonna e' `issue_number` -> «no such column»). Mappa
    # CHIUSA (field_synonyms §7.9); nessun match -> errore onesto a valle.
    if isinstance(key, list):
        try:
            from field_synonyms import FIELD_SYNONYMS
            cols = set(getattr(st.schema, "columns", {}) or {})
            key = [next((s for s in FIELD_SYNONYMS.get(k, []) if s in cols),
                        k) if (cols and k not in cols) else k
                   for k in key]
            # Su sqlite l'upsert ON CONFLICT deve matchare ESATTAMENTE una
            # PK/UNIQUE: una key che e' SOTTOINSIEME proprio della PK (es.
            # ["issue_number"] con PK (repo, issue_number)) fallirebbe
            # sempre. Stesso intento di dedup -> completa alla PK. Key con
            # campi FUORI PK: lasciata intatta (errore onesto a valle).
            pk = tuple(getattr(st.schema, "primary_key", ()) or ())
            if pk and key and set(key) < set(pk):
                key = list(pk)
        except Exception:
            pass
    # Fix bug live 3/7 (§2.8): "prima" genuino, va calcolato PRIMA del write
    # (dopo, ogni riga appena scritta risulterebbe sempre "gia' presente").
    # was_new[i] = True se entries[i] era ASSENTE dallo store, False se era
    # gia' presente (l'upsert la aggiorna, non la crea). Un pipeline che
    # conta n_written come "nuovi" mente su un upsert-noop: n_new e' il dato
    # onesto per "quante ne ho inserite/scoperte ORA" (vedi manifest).
    try:
        was_new = st.check_new(entries, key=key)
    except Exception as ex:  # §2.8: errore SQL onesto, con le colonne valide
        return {"ok": False, "error_class": "wrong_args",
                "error": _msg(
                    "ERR_STORE_WRONG_COLUMNS", error=ex, name=name,
                    columns=", ".join(
                        getattr(st.schema, "columns", {}) or []),
                ),
                "results": []}
    if set_fields:
        # VALORE-INIZIALE non regredisce (§7.9, bug live task github 6/7):
        # un set_fields IDENTICO all'insert_default dello store (es.
        # status='new' con insert_defaults {'status':'new'}) e' il valore di
        # NASCITA — applicarlo alle righe ESISTENTI le regrediva (la #53
        # 'posted' tornava 'new' a ogni fire del task → ri-triage infinito).
        # Le chiavi iniziali valgono solo per le righe NUOVE; ogni ALTRO
        # set_fields (es. status='posted') resta upsert pieno (contratto P3).
        _init = dict(getattr(st, "insert_defaults", None) or {})
        _initial_keys = {k for k, v in set_fields.items()
                         if k in _init and _init[k] == v}
        entries = [
            ({**e, **(set_fields if is_new else {
                k: v for k, v in set_fields.items()
                if k not in _initial_keys})}
             if isinstance(e, dict) else e)
            for e, is_new in zip(entries, was_new)]
    try:
        n = st.write(entries, key=key)
    except Exception as ex:  # §2.8: errore SQL onesto, con le colonne valide
        return {"ok": False, "error_class": "wrong_args",
                "error": _msg(
                    "ERR_STORE_WRONG_COLUMNS", error=ex, name=name,
                    columns=", ".join(
                        getattr(st.schema, "columns", {}) or []),
                ),
                "results": []}
    n_new = sum(1 for w in was_new if w)
    return {"ok": True, "n_written": n, "n_new": n_new, "n_updated": n - n_new,
            "results": [{"written": True, "was_new": w} for w in was_new],
            "metadata": {"store": name, "n_written": n, "n_new": n_new}}


def handle_delete_entries(args, *, verbose: bool = False) -> dict:
    a = args or {}
    name = (a.get("store") or "").strip()
    if not name:
        return {"ok": False, "error_class": "invalid_args",
                "error": _msg("ERR_STORE_REQUIRED_DELETE"),
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
        "description": _msg("MSG_STORE_FIND_DESCRIPTION"),
        "parameters": {
            "type": "object",
            "required": ["store"],
            "properties": {
                "store": {"type": "string",
                          "description": _msg("MSG_STORE_ARG_NAME_FIND")},
                "where": {"type": "object",
                          "description": _msg("MSG_STORE_ARG_WHERE_FIND")},
                "order": {"type": "array", "items": {"type": "string"},
                          "description": _msg("MSG_STORE_ARG_ORDER")},
                "max_results": {"type": "integer",
                                "description": _msg("MSG_STORE_ARG_MAX")},
            },
        },
    },
}

WRITE_ENTRIES_TOOL = {
    "type": "function",
    "function": {
        "name": "write_entries",
        "description": _msg("MSG_STORE_WRITE_DESCRIPTION"),
        "parameters": {
            "type": "object",
            "required": ["store", "from_step"],
            "properties": {
                "store": {"type": "string",
                          "description": _msg("MSG_STORE_ARG_NAME_WRITE")},
                "from_step": {"type": "integer", "minimum": 1,
                              "description": _msg("MSG_STORE_ARG_FROM_STEP")},
                "key": {"type": "array", "items": {"type": "string"},
                        "description": _msg("MSG_STORE_ARG_KEY")},
                "set_fields": {"type": "object",
                               "description": _msg("MSG_STORE_ARG_SET_FIELDS")},
            },
        },
    },
}

DELETE_ENTRIES_TOOL = {
    "type": "function",
    "function": {
        "name": "delete_entries",
        "description": _msg("MSG_STORE_DELETE_DESCRIPTION"),
        "parameters": {
            "type": "object",
            "required": ["store"],
            "properties": {
                "store": {"type": "string",
                          "description": _msg("MSG_STORE_ARG_NAME_DELETE")},
                "where": {"type": "object",
                          "description": _msg("MSG_STORE_ARG_WHERE_DELETE")},
            },
        },
    },
}

# Affinità RISTRETTA store-specifica (IT+EN): solo query che nominano un
# archivio/raccolta generico arrivano qui — niente verbi generici.
_AFFINITY = _store_affinity()

BUILTIN_INPROC_SPECS = [
    {"name": "find_entries", "tool_spec": FIND_ENTRIES_TOOL,
     "affinity": _AFFINITY},
    {"name": "write_entries", "tool_spec": WRITE_ENTRIES_TOOL,
     "affinity": _AFFINITY},
    {"name": "delete_entries", "tool_spec": DELETE_ENTRIES_TOOL,
     "affinity": _AFFINITY},
]
