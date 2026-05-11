"""reverse_patterns_patch.py — 5° pattern `delete_<object>_by_id` (§2.3).

Estensione cumulativa di `runtime/reverse_patterns.py` da integrare al
Task D admission dell'importer skill. Mantenuto in worktree separato per
non sporcare il file canonico fino a merge.

Contratto del pattern
---------------------
- Si applica ad executor produttori (es. `set_events`, `set_contacts`,
  `send_messages`) che producono record con un identificativo logico
  remoto (event_id, contact_id, message_id, ...).
- Il manifest dichiara nel TOML:
      reverse_pattern = "delete_events_by_id"
  (sostituire `events` con l'oggetto plurale §2.2 del produttore).
- Il pattern legge `results.results[]` cercando:
      `<object_singular>_id`  -> e.g. `event_id`, `contact_id`
  + (opzionale) campo `scope_id`  -> e.g. `calendar_id`, `address_book_id`
- L'undo costruisce una chiamata al gemello `delete_<objects>` passando:
      args = {
        "<object_singular>_ids": [id1, id2, ...],
        "<scope_field>": scope_id_value,
      }
  e group-by sullo scope_id se eterogeneo (multiple call).

Schema atteso `results`
-----------------------
Esempio per `set_events` -> `delete_events_by_id`:
  results = {
    "results": [
      {"event_id": "abc", "calendar_id": "primary", ...},
      {"event_id": "def", "calendar_id": "work@example.com", ...},
    ]
  }
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# Catalogo: object plurale -> (singolare, scope_field, delete_verb_template)
# ---------------------------------------------------------------------------
# Estensibile: ogni nuova famiglia (es. files_remote, tasks, contacts)
# aggiunge una riga qui invece di un nuovo pattern.
_OBJECT_REGISTRY = {
    "events":   {"singular": "event",   "scope": "calendar_id"},
    "messages": {"singular": "message", "scope": "folder"},
    "contacts": {"singular": "contact", "scope": "address_book_id"},
    "files":    {"singular": "file",    "scope": "drive_id"},
}


# ---------------------------------------------------------------------------
# Helper deterministici (§7.9)
# ---------------------------------------------------------------------------

def _object_from_pattern_name(name):
    """`delete_events_by_id` -> `events`. None se non matcha schema."""
    if not isinstance(name, str):
        return None
    if not name.startswith("delete_") or not name.endswith("_by_id"):
        return None
    middle = name[len("delete_"):-len("_by_id")]
    return middle or None


def _registry_for(object_plural):
    """Lookup nel registry; None se sconosciuto (escalation a Roberto §2.2)."""
    return _OBJECT_REGISTRY.get(object_plural)


def _validate_undo_blob(results, id_field, scope_field):
    """Valida che `results.results[]` abbia almeno un record con `id_field`.

    Ritorna `(rows, error)`:
      - `rows`: lista di dict validi (ognuno con `id_field` non vuoto).
      - `error`: str se mancano dati critici (es. lista vuota o nessun id).
    """
    if not isinstance(results, dict):
        return [], "results must be a dict"
    rows = results.get("results")
    if not isinstance(rows, list):
        return [], "results.results must be a list"
    if not rows:
        return [], "empty results list, nothing to undo"

    valid = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        rid = r.get(id_field)
        if not rid:
            continue
        valid.append(r)
    if not valid:
        return [], f"no rows contained {id_field!r}"
    return valid, None


def _group_by_scope(rows, scope_field):
    """Raggruppa righe per `scope_field`. None come chiave se mancante.

    Ritorna dict ordinato deterministicamente per chiave (None per ultimo).
    """
    groups = {}
    for r in rows:
        key = r.get(scope_field)
        groups.setdefault(key, []).append(r)
    # Ordinamento deterministico: None alla fine, resto alfabetico.
    ordered = {}
    for k in sorted([k for k in groups if k is not None], key=str):
        ordered[k] = groups[k]
    if None in groups:
        ordered[None] = groups[None]
    return ordered


# ---------------------------------------------------------------------------
# Builder degli args di undo
# ---------------------------------------------------------------------------

def build_undo_calls(pattern_name, results):
    """Costruisce le chiamate `delete_<objects>(...)` di undo.

    Ritorna `(calls, error)`:
      - `calls`: lista di dict `{"executor": str, "args": dict}`. Una sola
        entry se lo scope_id e' uniforme (o assente); multiple se eterogeneo.
      - `error`: str se la patch non puo' generare l'undo (validazione).

    Esempio:
      pattern_name = "delete_events_by_id"
      results = {"results": [
        {"event_id": "a", "calendar_id": "primary"},
        {"event_id": "b", "calendar_id": "primary"},
      ]}
      -> calls = [{
           "executor": "delete_events",
           "args": {"event_ids": ["a", "b"], "calendar_id": "primary"},
         }]
    """
    object_plural = _object_from_pattern_name(pattern_name)
    if object_plural is None:
        return [], f"invalid pattern name: {pattern_name!r}"
    spec = _registry_for(object_plural)
    if spec is None:
        return [], f"unknown object in pattern: {object_plural!r}"

    singular = spec["singular"]
    scope_field = spec["scope"]
    id_field = f"{singular}_id"
    ids_field = f"{singular}_ids"
    executor = f"delete_{object_plural}"

    rows, err = _validate_undo_blob(results, id_field, scope_field)
    if err:
        return [], err

    groups = _group_by_scope(rows, scope_field)
    calls = []
    for scope_value, group_rows in groups.items():
        # Mantieni ordine di apparizione interno al gruppo (deterministico).
        ids = [r[id_field] for r in group_rows]
        args = {ids_field: ids}
        if scope_value is not None:
            args[scope_field] = scope_value
        calls.append({"executor": executor, "args": args})
    return calls, None


# ---------------------------------------------------------------------------
# Registry hook (idempotente)
# ---------------------------------------------------------------------------

def _make_pattern_callable():
    """Costruisce la callable signature `(plan, results) -> dict` che
    `apply_pattern` invoca. Estrae il nome del pattern dal `plan` (campo
    `reverse_pattern` o `_undo_pattern`), poi delega a `build_undo_calls`.
    Non esegue la chiamata: lascia al runtime undo_last_turn il dispatch
    effettivo (rispetta la separazione catalogo / dispatcher).
    """
    def _delete_by_id(plan, results):
        pattern_name = (
            (plan or {}).get("reverse_pattern")
            or (plan or {}).get("_undo_pattern")
            or "delete_unknown_by_id"
        )
        if isinstance(pattern_name, list):
            # Multistage: trova quello con suffisso _by_id.
            cand = [n for n in pattern_name if isinstance(n, str)
                    and n.startswith("delete_") and n.endswith("_by_id")]
            pattern_name = cand[0] if cand else "delete_unknown_by_id"
        calls, err = build_undo_calls(pattern_name, results or {})
        if err:
            return {"ok": False, "error": err, "calls": []}
        return {
            "ok": True,
            "ok_count": sum(len(c["args"].get(
                f"{_OBJECT_REGISTRY[_object_from_pattern_name(pattern_name)]['singular']}_ids",
                [])) for c in calls),
            "fail_count": 0,
            "calls": calls,
        }
    return _delete_by_id


def register_delete_by_id_pattern(reverse_patterns):
    """Aggiunge le entry `delete_<object>_by_id` al registry `PATTERNS`.

    `reverse_patterns` e' il modulo `runtime.reverse_patterns` gia' caricato
    (o un namespace test-double con attributo `PATTERNS: dict`).

    Idempotente: re-invocare non sostituisce le entry gia' presenti se la
    callable e' identica (stessa identita') e non solleva errore (rispetta
    §7.1 no shim, ma anche §2.8 no silent fail su patch ripetute).
    """
    patterns_dict = getattr(reverse_patterns, "PATTERNS", None)
    if not isinstance(patterns_dict, dict):
        raise TypeError(
            "reverse_patterns module must expose PATTERNS: dict[str, callable]"
        )
    callable_impl = _make_pattern_callable()
    added = []
    for object_plural in _OBJECT_REGISTRY:
        key = f"delete_{object_plural}_by_id"
        existing = patterns_dict.get(key)
        if existing is None:
            patterns_dict[key] = callable_impl
            added.append(key)
        # Else: gia' presente, idempotente (no-op).
    return added
