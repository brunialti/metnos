"""skill_codegen - Jinja codegen deterministico per Task C.

Trasforma un `ExecutorPlan` (output di skill_translator.translate_subcommand)
in tre file scritti su disco:
- <executor_dir>/<name>/manifest.toml         (Jinja template)
- <executor_dir>/<name>/<name>.py             (Jinja template)
- <executor_dir>/<name>/manifest.lang_state.json   (placeholder)

Determinismo §7.9: zero LLM. Templates Jinja2 piloted da campi tipizzati
dell'ExecutorPlan. Description IT+EN puo' essere prodotta da
`skill_description_llm.py` (Task C.2) o boilerplate fallback (default).

Convenzione output naming (§2.6):
- read/find/get/list/filter -> entries
- set/delete/move/write/create/send -> results

Per ognuno la pipeline e':
1. Costruisci context jinja a partire dal plan + parsed_skill (provenance,
   skill_name dal parsed_skill, hints da reverse_pattern, ...).
2. Render manifest.toml.
3. Render <name>.py.
4. Scrivi i 3 file in <executor_dir>/<name>/.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional, Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined


_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


def _jinja_env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        autoescape=False,
    )
    env.filters["tojson"] = _tojson
    return env


def _tojson(value: Any) -> str:
    """Sostituisce il filtro tojson di default con json.dumps stabile.

    Per i valori `default` del manifest TOML, le stringhe vanno con virgolette
    doppie ("last-7d", "primary"), gli interi numerici (50), i booleani lower.
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


# ---------------------------------------------------------------------------
# Affinity generator (boilerplate; LLM stage 4 puo' raffinare).
# ---------------------------------------------------------------------------


# Affinity per object: include singolari + plurali IT/EN per matching prefilter
# token-based (case-insensitive, ma non lemmatize). Aggiungere singolare quando
# il termine plurale non lo deriva ovviamente (eventi/evento ok dal token match
# parziale? No: prefilter fa exact substring match sulla parola).
_AFFINITY_BY_OBJ = {
    "events":   ["appuntamento", "appuntamenti", "agenda", "evento",
                 "eventi", "calendario", "calendar", "riunione",
                 "riunioni", "incontro", "incontri", "meeting",
                 "scadenza", "scadenze", "deadline", "promemoria",
                 "reminder", "events", "meetings", "schedule"],
    "messages": ["mail", "email", "messaggio", "messaggi", "posta",
                 "newsletter", "newsletters", "messages", "emails",
                 "inbox", "lettera"],
    "files":    ["file", "documento", "documenti", "documents", "files",
                 "spreadsheet", "spreadsheets", "doc", "docs"],
    "contacts": ["contatto", "contatti", "rubrica", "contact", "contacts",
                 "address", "addresses", "persona"],
    "credentials": ["credenziale", "credenziali", "chiave", "chiavi",
                    "token", "credentials", "keys", "password"],
}


_AFFINITY_BY_VERB = {
    "read":   ["leggi", "read", "view", "open"],
    "find":   ["cerca", "find", "search"],
    "set":    ["crea", "imposta", "set", "create"],
    "delete": ["cancella", "elimina", "delete", "remove"],
    "send":   ["invia", "manda", "send"],
    "list":   ["elenca", "list", "enumera"],
    "get":    ["ottieni", "get"],
    "change": ["modifica", "update", "change"],
    "write":  ["scrivi", "write", "upload"],
}


def _default_affinity(plan) -> list:
    """Affinity baseline: combina termini per object + verbo. 8-15 termini."""
    base = list(_AFFINITY_BY_OBJ.get(plan.obj, []))
    base.extend(_AFFINITY_BY_VERB.get(plan.verb, []))
    seen = set()
    out = []
    for t in base:
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
    # Cap a 15
    return out[:15]


# ---------------------------------------------------------------------------
# Args list -> jinja-friendly dict
# ---------------------------------------------------------------------------


def _arg_to_ctx(arg) -> dict:
    return {
        "name": arg.name,
        "type": arg.type,
        "items_type": arg.items_type,
        "format": arg.format,
        "default": arg.default,
        "required": arg.required,
        "description": arg.description,
        # Per ora description_it/en uguali (LLM stage 4 li raffinera').
        "description_it": arg.description,
        "description_en": _en_fallback(arg.description),
    }


def _en_fallback(it: str) -> str:
    """Fallback rudimentale IT->EN per descrizioni args.

    Tabella di sostituzione di alcuni termini ricorrenti. Determinismo
    §7.9: NON e' traduzione, e' boilerplate che funziona finche' lo stage 4
    LLM non produce description vera (gap §5.9).
    """
    if not it:
        return ""
    # Lookup tabella semplice: sostituzioni 1:1 case-insensitive.
    subs = {
        "Lista": "List",
        "Identificatore": "Identifier",
        "Inizio": "Start",
        "Fine": "End",
        "Default": "Default",
        "Versione plurale": "Plural form",
        "Cap superiore esplicito": "Explicit upper cap",
        "Finestra temporale": "Time window",
        "del calendario": "calendar",
        "Stringa": "String",
        "ritornate": "returned",
        "max entries": "max entries",
        "Pure compute": "Pure compute",
        "Identificatore singolo": "Single identifier",
    }
    out = it
    for it_word, en_word in subs.items():
        out = out.replace(it_word, en_word)
    return out


# ---------------------------------------------------------------------------
# Reverse_pattern -> vectorial coalesce hint
# ---------------------------------------------------------------------------


def _vectorial_hint_from_plan(plan) -> Optional[dict]:
    """Per delete/send executor che iterano N volte uno script che accetta
    UN id per chiamata, prepara il blocco vettoriale (_coalesce_<entity>).

    Ritorna None se l'executor non e' iterativo (output entries o N=1 fisso).
    """
    if plan.output_kind != "results":
        return None
    if plan.verb not in ("delete", "send"):
        return None
    # Mapping singolare/plurale standard dal vocabolario chiuso.
    obj_singular = {
        "events":   "event",
        "messages": "message",
        "files":    "file",
        "contacts": "contact",
    }.get(plan.obj, plan.obj.rstrip("s"))
    return {
        "entity": "rows",
        "singular": f"{obj_singular}_id",
        "plural":   f"{obj_singular}_ids",
    }


# ---------------------------------------------------------------------------
# Passthrough flags: args che vanno passati alla CLI skill come --flag value
# ---------------------------------------------------------------------------


_NON_PASSTHROUGH = frozenset({
    "events", "messages", "files",
    "event_id", "event_ids", "message_id", "message_ids",
    "file_id", "file_ids", "contact_id", "contact_ids",
    "entries", "top_k", "time_window", "start", "end",
    "summary",  # Trattato a parte in template
    "rows", "ids",
})


def _passthrough_flags(plan) -> list:
    """Trova flag scalari del manifest che devono diventare `--cli value` nel
    subprocess. Convenzione: name snake_case Metnos -> cli `--name-cli`
    (kebab-case se conosciuto, altrimenti snake_case).
    """
    kebab_known = {
        "html_link":     "html-link",
        "raw_query":     "raw-query",
        "add_labels":    "add-labels",
        "remove_labels": "remove-labels",
        "calendar_id":   "calendar",  # Skill usa --calendar
        "sheet_name":    "sheet-name",
        "export_mime":   "export-mime",
    }
    out = []
    for a in plan.args:
        if a.name in _NON_PASSTHROUGH:
            continue
        if a.type == "array" and a.items_type == "object":
            # Es. events: list[dict] -> non e' passthrough, viene splittato in N call
            continue
        cli = kebab_known.get(a.name, a.name.replace("_", "-"))
        out.append({
            "name": a.name,
            "cli": cli,
            # Per calendar_id non vogliamo passare il default "primary" alla skill.
            "skip_default": a.default if a.name == "calendar_id" else None,
        })
    return out


# ---------------------------------------------------------------------------
# ISO validations: args che vanno validati con _validate_iso_tz
# ---------------------------------------------------------------------------


def _iso_validations(plan) -> list:
    out = []
    for a in plan.args:
        if a.format == "date-time" and a.name not in ("start", "end"):
            # Start/end gia' coperti da _resolve_window quando time_window e' presente.
            out.append(a.name)
        elif a.name in ("start", "end") and plan.skill_action != "list":
            # set_events/delete_events: validazione esplicita ISO oltre a time_window.
            out.append(a.name)
    return out


# ---------------------------------------------------------------------------
# Description boilerplate (Task C.2 LLM puo' sostituire).
# ---------------------------------------------------------------------------


# Mapping skill domain -> phrasing user-facing (IT+EN). Help LLM PLANNER
# (Gemma 4 26B) a collegare la query naturale ("appuntamenti") al tool
# canonico. Senza questa specializzazione, la description "events via skill
# `calendar list`" e' troppo astratta e il PLANNER preferisce
# request_new_executor.
_SKILL_DOMAIN_PHRASING = {
    "calendar": {
        "noun_it": "appuntamenti del calendario",
        "noun_en": "calendar appointments",
        "service_it": "Google Calendar",
        "service_en": "Google Calendar",
        "examples_it": "appuntamenti, agenda, eventi, riunioni",
        "examples_en": "appointments, schedule, events, meetings",
    },
    "gmail": {
        "noun_it": "messaggi email",
        "noun_en": "email messages",
        "service_it": "Gmail",
        "service_en": "Gmail",
        "examples_it": "email, mail, posta, messaggi",
        "examples_en": "email, mail, messages",
    },
    "drive": {
        "noun_it": "file su cloud",
        "noun_en": "cloud files",
        "service_it": "Google Drive",
        "service_en": "Google Drive",
        "examples_it": "documenti su Drive, cartelle, file condivisi",
        "examples_en": "Drive documents, folders, shared files",
    },
    "sheets": {
        "noun_it": "fogli di calcolo",
        "noun_en": "spreadsheets",
        "service_it": "Google Sheets",
        "service_en": "Google Sheets",
        "examples_it": "spreadsheet, foglio elettronico, tabelle",
        "examples_en": "spreadsheets, sheets, tables",
    },
    "docs": {
        "noun_it": "documenti di testo",
        "noun_en": "text documents",
        "service_it": "Google Docs",
        "service_en": "Google Docs",
        "examples_it": "documento Docs, testo formattato",
        "examples_en": "Docs document, formatted text",
    },
    "contacts": {
        "noun_it": "contatti rubrica",
        "noun_en": "address book contacts",
        "service_it": "Google Contacts",
        "service_en": "Google Contacts",
        "examples_it": "contatti, rubrica, email di persone",
        "examples_en": "contacts, address book, people emails",
    },
}


def _description_boilerplate(plan) -> tuple[str, str]:
    """Description boilerplate IT+EN seguendo §6 stile prescrittivo.

    Quando il `plan.skill_domain` e' in `_SKILL_DOMAIN_PHRASING`, la
    description usa il nome del servizio reale (Google Calendar, Gmail,
    ...) e l'esempio di query naturale. Help il PLANNER a routing
    deterministico senza fallback a request_new_executor.
    """
    verb_desc_it = {
        "read":   "Legge",
        "find":   "Cerca",
        "set":    "Crea o aggiorna",
        "delete": "Cancella",
        "send":   "Invia",
        "list":   "Elenca",
        "get":    "Ottiene",
        "change": "Modifica",
        "write":  "Scrive",
    }.get(plan.verb, "Esegue operazione su")
    verb_desc_en = {
        "read":   "Reads",
        "find":   "Searches",
        "set":    "Creates or updates",
        "delete": "Deletes",
        "send":   "Sends",
        "list":   "Lists",
        "get":    "Gets",
        "change": "Modifies",
        "write":  "Writes",
    }.get(plan.verb, "Performs operation on")
    output_field = plan.output_kind

    phrase = _SKILL_DOMAIN_PHRASING.get(plan.skill_domain or "")
    if phrase:
        it = (
            f"{verb_desc_it} {phrase['noun_it']} di {phrase['service_it']} "
            f"via OAuth. DEVI usarla per query su {phrase['examples_it']}. "
            f"NON DEVI usarla per altri provider (es. iCloud, Outlook, IMAP). "
            f"Vettoriale (§2.1): ritorna `{output_field}: list`. "
            f"USO CORRETTO: {plan.name}(...). "
            f"ERRORE: invocarla senza credenziali (richiedera' setup OAuth)."
        )
        en = (
            f"{verb_desc_en} {phrase['noun_en']} of {phrase['service_en']} "
            f"via OAuth. MUST be used for queries about "
            f"{phrase['examples_en']}. MUST NOT be used for other providers "
            f"(e.g. iCloud, Outlook, IMAP). Vectorial (§2.1): returns "
            f"`{output_field}: list`. CORRECT USE: {plan.name}(...). "
            f"ERROR: invoking without credentials (will require OAuth setup)."
        )
        return it, en

    # Fallback generico per domini non in mapping (skill nuova non Google).
    obj = plan.obj
    it = (
        f"{verb_desc_it} {obj} via skill `{plan.skill_domain} {plan.skill_action}`. "
        f"Vettoriale (§2.1): ritorna `{output_field}: list`. "
        f"DEVI usarla per operazioni su {obj} dello skill backend. "
        f"USO CORRETTO: {plan.name}(...)."
    )
    en = (
        f"{verb_desc_en} {obj} via skill `{plan.skill_domain} {plan.skill_action}`. "
        f"Vectorial (§2.1): returns `{output_field}: list`. "
        f"MUST be used for operations on {obj} from the skill backend. "
        f"CORRECT USE: {plan.name}(...)."
    )
    return it, en


# ---------------------------------------------------------------------------
# Tests boilerplate
# ---------------------------------------------------------------------------


def _toml_inline_value(v) -> str:
    """Serializza un valore python come literal TOML inline (semplificato)."""
    if v is None:
        return '""'
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        return '"' + v.replace('"', '\\"') + '"'
    if isinstance(v, list):
        return "[" + ", ".join(_toml_inline_value(x) for x in v) + "]"
    if isinstance(v, dict):
        parts = [f"{k} = {_toml_inline_value(val)}" for k, val in v.items()]
        return "{ " + ", ".join(parts) + " }"
    return '""'


def _tests_for_plan(plan) -> list:
    """Costruisce 4-6 test in stile §3 (caso felice, lista vuota, args invalidi,
    edge dominio = auth missing).
    """
    out = []
    # Test 1: validates_invalid_args (args non dict o args essenziali mancanti)
    out.append({
        "name": "validates_args_must_be_object",
        "input_toml": _toml_inline_value({"_force_invalid_type": True}),
        "expect_toml": _toml_inline_value({"ok": True}),  # placeholder
    })
    # Test 2: happy path mocked (richiede _force_empty per non chiamare il subprocess)
    happy_input = {"_force_empty": True} if plan.output_kind == "entries" else \
        ({"summary": "Test", "start": "2026-03-01T10:00:00Z",
          "end": "2026-03-01T11:00:00Z"} if plan.skill_action == "create"
         else {"event_id": "test-id-1"})
    out.append({
        "name": "happy_path_mocked",
        "input_toml": _toml_inline_value(happy_input),
        "expect_toml": _toml_inline_value({"ok": True}),
    })
    # Test 3: auth missing -> needs_inputs
    out.append({
        "name": "auth_missing_returns_needs_inputs",
        "input_toml": _toml_inline_value({"_force_error": "auth_required"}),
        "expect_toml": _toml_inline_value({"decision": "needs_inputs"}),
    })
    # Test 4: invalid args (specific al verbo)
    if plan.verb == "set":
        out.append({
            "name": "validates_missing_required",
            "input_toml": _toml_inline_value({}),
            "expect_toml": _toml_inline_value({"ok": False, "error_class": "invalid_args"}),
        })
    elif plan.verb == "delete":
        out.append({
            "name": "validates_missing_id",
            "input_toml": _toml_inline_value({}),
            "expect_toml": _toml_inline_value({"ok": False, "error_class": "invalid_args"}),
        })
    elif plan.output_kind == "entries":
        out.append({
            "name": "empty_result",
            "input_toml": _toml_inline_value({"_force_empty": True}),
            "expect_toml": _toml_inline_value({"ok": True, "used": 0}),
        })
    return out


# ---------------------------------------------------------------------------
# Output schema inline (TOML triple-quoted)
# ---------------------------------------------------------------------------


def _output_schema_inline(plan) -> str:
    if plan.output_kind == "entries":
        return (
            "{\n"
            "  ok: bool,\n"
            "  decision?: 'needs_inputs',\n"
            "  needs_inputs?: {title, dialog, fmt, on_complete},\n"
            f"  entries: Array<{{kind: '{plan.output_record_kind or 'record'}', id: str, ...}}>,\n"
            "  used: int,\n"
            "  available_total?: int,\n"
            "  truncated?: bool,\n"
            "  truncated_what?: str,\n"
            "  cap_field?: str,\n"
            "  cap_value?: int,\n"
            "  error?: str,\n"
            "  error_class?: str,\n"
            "  final_message_hint?: str\n"
            "}"
        )
    return (
        "{\n"
        "  ok: bool,\n"
        "  decision?: 'needs_inputs',\n"
        "  needs_inputs?: {title, dialog, fmt, on_complete},\n"
        "  results: Array<dict>,\n"
        "  used: int,\n"
        "  partial?: bool,\n"
        "  failures?: Array<{id, error, error_class}>,\n"
        "  error?: str,\n"
        "  error_class?: str,\n"
        "  _undo?: {pattern, ids},\n"
        "  final_message_hint?: str\n"
        "}"
    )


# ---------------------------------------------------------------------------
# Extra record fields (es. calendar_id che la skill non emette)
# ---------------------------------------------------------------------------


def _extra_record_fields(plan) -> str:
    """Ritorna una stringa Python valida per dict literal."""
    if plan.obj == "events":
        return '{"calendar_id": args.get("calendar_id") or "primary"}'
    return "None"


# ---------------------------------------------------------------------------
# Helpers status word
# ---------------------------------------------------------------------------


_STATUS_WORD_BY_VERB = {
    "set":    "created",
    "delete": "deleted",
    "send":   "sent",
    "write":  "written",
    "create": "created",
    "change": "updated",
    "move":   "moved",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


_PROVIDERS_PATH = Path(__file__).resolve().parent / "skill_oauth_providers.json"


def _load_oauth_provider_table() -> dict:
    """Carica `runtime/skill_oauth_providers.json`. Determinismo: la
    tabella e' un file dati committato col core, non un'API esterna.
    Ritorna `{}` su missing/parse-error per non bloccare il codegen
    di skill non-OAuth."""
    if not _PROVIDERS_PATH.is_file():
        return {}
    try:
        import json as _json
        return _json.loads(_PROVIDERS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _infer_oauth_provider(parsed_skill) -> dict:
    """Inferenza non-LLM del provider OAuth della skill.

    Ordine di lookup:
      1. provider table[skill_name]                 (match diretto)
      2. provider table per required_credential_files (es. google_token.json
         -> google-workspace).
    Ritorna `{scopes_options, mirror_paths, client_secret_install_path}`
    quando trovato; `{}` se la skill non e' un provider OAuth riconosciuto.
    """
    table = _load_oauth_provider_table()
    providers = table.get("providers") or {}
    skill_name = (getattr(parsed_skill, "name", "") or "").lower()
    if skill_name in providers:
        return dict(providers[skill_name])
    hints = (table.get("_inference_hints") or {}).get("required_credential_files") or {}
    for rcf in getattr(parsed_skill, "required_credential_files", None) or []:
        path = rcf.get("path", "")
        name = Path(path).name
        if name in hints:
            target = hints[name]
            if target in providers:
                return dict(providers[target])
    return {}


def build_context(plan, parsed_skill, *, description_it=None,
                  description_en=None, affinity=None,
                  skill_script: str = "scripts/google_api.py") -> dict:
    """Costruisce il context jinja completo a partire dall'ExecutorPlan."""
    if description_it is None or description_en is None:
        d_it, d_en = _description_boilerplate(plan)
        description_it = description_it or d_it
        description_en = description_en or d_en

    if affinity is None:
        affinity = _default_affinity(plan)

    vectorial_coalesce = _vectorial_hint_from_plan(plan)
    iso_validations = _iso_validations(plan)
    passthrough = _passthrough_flags(plan)

    args_ctx = [_arg_to_ctx(a) for a in plan.args]

    # has_oauth_setup: la skill richiede credenziali OAuth (presente in
    # required_credential_files del parsed_skill).
    has_oauth = bool(parsed_skill.required_credential_files)

    # oauth_provider config: scopes_options, mirror_paths, client_secret_install_path.
    # Inferenza dal lookup `runtime/skill_oauth_providers.json`. Vuoto se la skill
    # non e' un provider riconosciuto: il manifest non avra' [oauth_provider] e
    # l'executor usera' i defaults di _needs_inputs_oauth_setup (vuoti).
    oauth_provider_cfg = _infer_oauth_provider(parsed_skill) if has_oauth else {}

    # needs_time_window: l'executor accetta time_window come arg.
    needs_tw = any(a.name == "time_window" for a in plan.args)
    tw_default = next(
        (a.default for a in plan.args if a.name == "time_window"),
        "last-7d",
    )

    has_top_k = any(a.name == "top_k" for a in plan.args)
    top_k_default = next(
        (a.default for a in plan.args if a.name == "top_k"),
        50,
    )

    capabilities_ctx = [{"name": c.name, "hint": c.hint} for c in plan.capabilities]

    # required_credentials: presente se la skill ha credential files.
    rc_binding = parsed_skill.name if has_oauth else ""
    rc_fields = []
    for rcf in parsed_skill.required_credential_files:
        path = rcf.get("path", "")
        stem = Path(path).stem if path else ""
        if stem:
            rc_fields.append(f"{stem}_json")
    rc_form_kind = "oauth_browser_flow" if has_oauth else ""
    rc_prompt_it = (
        f"Per usare {parsed_skill.name} servono credenziali OAuth. Vuoi configurarle ora?"
        if has_oauth else ""
    )
    rc_prompt_en = (
        f"Using {parsed_skill.name} requires OAuth credentials. Configure now?"
        if has_oauth else ""
    )

    # truncated_what
    truncated_what = plan.obj  # "events", "messages", ...

    # empty_default per stdout parse
    empty_default = "[]" if plan.output_kind == "entries" else "{}"

    # record_kind
    record_kind = plan.output_record_kind or plan.obj.rstrip("s")

    # status_word
    status_word = _STATUS_WORD_BY_VERB.get(plan.verb, "done")

    return {
        "name": plan.name,
        "skill_name": parsed_skill.name,
        "skill_domain": plan.skill_domain,
        "skill_action": plan.skill_action,
        "skill_script": skill_script,
        "affinity": affinity,
        "description_it": description_it,
        "description_en": description_en,
        "args": args_ctx,
        "output_kind": plan.output_kind,
        "output_schema_inline": _output_schema_inline(plan),
        "capabilities": capabilities_ctx,
        "reversible": plan.reversible,
        "reverse_pattern": plan.reverse_pattern,
        "provenance": plan.provenance,
        "tests": _tests_for_plan(plan),
        "has_oauth_setup": has_oauth,
        "has_api_key_setup": False,  # default; caller puo' override
        "needs_time_window": needs_tw,
        "time_window_default": tw_default if isinstance(tw_default, str) else "last-7d",
        "has_top_k": has_top_k,
        "top_k_default": int(top_k_default) if top_k_default is not None else 50,
        "vectorial_coalesce": vectorial_coalesce,
        "iso_validations": iso_validations,
        "passthrough_flags": passthrough,
        "status_word": status_word,
        "truncated_what": truncated_what,
        "empty_default": empty_default,
        "record_kind": record_kind,
        "extra_record_fields_py": _extra_record_fields(plan),
        "required_credentials_binding": rc_binding,
        "required_credentials_fields": rc_fields,
        "required_credentials_form_kind": rc_form_kind,
        "required_credentials_prompt_it": rc_prompt_it,
        "required_credentials_prompt_en": rc_prompt_en,
        "oauth_provider": oauth_provider_cfg,
    }


def render_manifest(context: dict) -> str:
    env = _jinja_env()
    tmpl = env.get_template("manifest.toml.j2")
    return tmpl.render(**context)


def render_executor_py(context: dict) -> str:
    env = _jinja_env()
    tmpl = env.get_template("executor.py.j2")
    return tmpl.render(**context)


def _lang_state_placeholder() -> str:
    """Companion JSON `manifest.lang_state.json` (ADR 0092 multilingua).
    Tutte le `version_hash` sono placeholder finche' il signer non
    riscrive (Task E).
    """
    return json.dumps({
        "description": {
            "it": {
                "version_hash": "sha256:PLACEHOLDER_NOT_SIGNED_IN_WORKTREE",
                "source_lang": None,
                "source_hash": None,
            },
            "en": {
                "source_lang": "it",
                "source_hash": "sha256:PLACEHOLDER_NOT_SIGNED_IN_WORKTREE",
                "version_hash": "sha256:PLACEHOLDER_NOT_SIGNED_IN_WORKTREE",
            },
        },
    }, indent=2)


def generate_executor_files(plan, parsed_skill, executor_dir, *,
                            description_it=None, description_en=None,
                            affinity=None, skill_script: str = "scripts/google_api.py"):
    """Genera 3 file in `executor_dir/<plan.name>/`. Crea la dir.

    Ritorna dict `{manifest_path, code_path, lang_state_path}`.

    DEVI: passare un ExecutorPlan + ParsedSkill (output di Task A/B).
    NON DEVI: scrivere in /opt/myclaw/.
    """
    out_dir = Path(executor_dir) / plan.name
    out_dir.mkdir(parents=True, exist_ok=True)

    ctx = build_context(
        plan, parsed_skill,
        description_it=description_it,
        description_en=description_en,
        affinity=affinity,
        skill_script=skill_script,
    )

    manifest_text = render_manifest(ctx)
    code_text = render_executor_py(ctx)
    lang_state_text = _lang_state_placeholder()

    manifest_path = out_dir / "manifest.toml"
    code_path = out_dir / f"{plan.name}.py"
    lang_state_path = out_dir / "manifest.lang_state.json"

    manifest_path.write_text(manifest_text, encoding="utf-8")
    code_path.write_text(code_text, encoding="utf-8")
    lang_state_path.write_text(lang_state_text, encoding="utf-8")

    return {
        "manifest_path": str(manifest_path),
        "code_path": str(code_path),
        "lang_state_path": str(lang_state_path),
        "context": ctx,
    }
