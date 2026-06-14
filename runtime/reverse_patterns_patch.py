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

    # Sorgenti possibili di ID (in ordine di preferenza):
    # 1. `results._undo.ids` (lista canonica scritta dall'executor produttore).
    # 2. `results.results[*].<singular>_id` (contratto pattern documentato).
    # 3. `results.results[*].id` (fallback per skill imports che usano `id`
    #    semplice — vedi set_events google-workspace ADR 0123).
    undo_meta = results.get("_undo") if isinstance(results, dict) else None
    if isinstance(undo_meta, dict) and isinstance(undo_meta.get("ids"), list) and undo_meta["ids"]:
        ids = list(undo_meta["ids"])
        # Nessuno scope-grouping disponibile da _undo.ids: tutto in un gruppo.
        args = {ids_field: ids}
        return [{"executor": executor, "args": args}], None

    rows, err = _validate_undo_blob_with_fallback(results, id_field, scope_field)
    if err:
        return [], err

    groups = _group_by_scope(rows, scope_field)
    calls = []
    for scope_value, group_rows in groups.items():
        # Mantieni ordine di apparizione interno al gruppo (deterministico).
        ids = [r.get(id_field) or r.get("id") for r in group_rows]
        args = {ids_field: ids}
        if scope_value is not None:
            args[scope_field] = scope_value
        calls.append({"executor": executor, "args": args})
    return calls, None


def _validate_undo_blob_with_fallback(results, id_field, scope_field):
    """Variante che accetta `id` come fallback di `<singular>_id`."""
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
        rid = r.get(id_field) or r.get("id")
        if not rid:
            continue
        valid.append(r)
    if not valid:
        return [], f"no rows contained {id_field!r} or 'id'"
    return valid, None


# ---------------------------------------------------------------------------
# Registry hook (idempotente)
# ---------------------------------------------------------------------------

def _dispatch_call(call):
    """Invoca `delete_<objects>` caricando il suo modulo Python dal catalog.
    Cerca prima in <install_root>/executors/, poi nei skill root (ADR 0160:
    `skills/` new + `_imports/` legacy back-compat). Ritorna
    `(ok_count, fail_count)` dell'invocazione concreta."""
    import importlib.util
    name = call["executor"]
    args = call["args"]
    # §7.11: la install-root reale via config.PATH_EXECUTORS. Prima il bug
    # usava il placeholder letterale "<install_root>" (mai sostituito) → path
    # inesistente → undo di delete_<obj>_by_id no-op silenzioso sui builtin.
    import config as _C
    candidates = [
        _C.PATH_EXECUTORS / name / f"{name}.py",
    ]
    from skills_paths import skill_roots as _sr
    for base in _sr():
        for skill_dir in base.iterdir():
            cand = skill_dir / name / f"{name}.py"
            if cand.is_file():
                candidates.append(cand)
    code_path = next((p for p in candidates if p.is_file()), None)
    if code_path is None:
        return 0, len(args.get(next((k for k in args if k.endswith("_ids")), ""), []) or [1])
    spec = importlib.util.spec_from_file_location(f"_undo_{name}", str(code_path))
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
        obs = mod.invoke(args)
    except Exception:
        return 0, len(args.get(next((k for k in args if k.endswith("_ids")), ""), []) or [1])
    if isinstance(obs, dict):
        results = obs.get("results") or []
        ok = sum(1 for r in results if isinstance(r, dict) and r.get("status") in ("deleted", "ok"))
        fail = (obs.get("n_deleted") or len(results)) - ok if obs.get("ok") else len(args.get(next((k for k in args if k.endswith("_ids")), ""), []) or [1])
        return ok, max(0, fail)
    return 0, 1


def _make_pattern_callable(pattern_name_bound):
    """Costruisce la callable `(plan, results) -> dict` per uno specifico
    `pattern_name` registrato nel catalogo (`delete_events_by_id`,
    `delete_messages_by_id`, ...). Il nome e' chiuso in closure: cosi' la
    callable conosce sempre il proprio identificativo a build-time, senza
    dover ricostruirlo da `plan` (che e' tipicamente vuoto per gli executor
    skill-imported come set_events). Bug live turn 742b746d (11/5/2026 sera):
    la versione precedente leggeva `plan["reverse_pattern"]` come fonte e,
    quando assente (set_events registra `plan={}`), cadeva su
    "delete_unknown_by_id" -> "unknown object in pattern" -> ok_count=0.
    Build descriptors via `build_undo_calls` poi li INVOCA tramite
    `_dispatch_call` per chiudere il ciclo undo end-to-end. §2.8 no silent
    failure.
    """
    def _delete_by_id(plan, results):
        # Fallback robusto sul `plan` se override esplicito (es. multistage
        # reverse_pattern come list): rispettato in caso di varianti future.
        pattern_name = pattern_name_bound
        override = (plan or {}).get("reverse_pattern") or (plan or {}).get("_undo_pattern")
        if isinstance(override, str) and override.startswith("delete_") and override.endswith("_by_id"):
            pattern_name = override
        elif isinstance(override, list):
            cand = [n for n in override if isinstance(n, str)
                    and n.startswith("delete_") and n.endswith("_by_id")]
            if cand:
                pattern_name = cand[0]
        calls, err = build_undo_calls(pattern_name, results or {})
        if err:
            return {"ok": False, "error": err, "calls": [], "ok_count": 0, "fail_count": 0}
        ok_total = 0
        fail_total = 0
        for c in calls:
            ok_i, fail_i = _dispatch_call(c)
            ok_total += ok_i
            fail_total += fail_i
        return {
            "ok": ok_total > 0 and fail_total == 0,
            "ok_count": ok_total,
            "fail_count": fail_total,
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
    added = []
    for object_plural in _OBJECT_REGISTRY:
        key = f"delete_{object_plural}_by_id"
        existing = patterns_dict.get(key)
        if existing is None:
            # Una callable distinta per ogni key (closure su pattern_name)
            # cosi' la callable conosce sempre il proprio nome.
            patterns_dict[key] = _make_pattern_callable(key)
            added.append(key)
        # Else: gia' presente, idempotente (no-op).
    return added
