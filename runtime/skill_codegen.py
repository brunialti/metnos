"""skill_codegen - Jinja codegen deterministico per Task C.

Trasforma un `ExecutorPlan` (output di skill_translator.translate_subcommand)
in tre file scritti su disco:
- <executor_dir>/<name>/manifest.toml         (Jinja template)
- <executor_dir>/<name>/<name>.py             (Jinja template)
- <executor_dir>/<name>/manifest.lang_state.json   (canonical i18n state)

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
import detection_lexicon as _detlex
import detection_lexicon_seed_codegen as _codegen_seed
import i18n as _i18n
from generated_executor_contract import (
    generated_contract_context,
    validate_generated_manifest_text,
)


_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

# Cache singleton (perf, 24/5/2026): Environment + FileSystemLoader rebuilt
# per ogni `render_manifest`/`render_executor_py` (2 ricreazioni per executor;
# 38 per skill da 19 plan). Loader holds template AST cache: la ricostruzione
# invalida quella cache ogni volta.
_JINJA_ENV: Environment | None = None


def _jinja_env() -> Environment:
    global _JINJA_ENV
    if _JINJA_ENV is None:
        env = Environment(
            loader=FileSystemLoader(str(_TEMPLATES_DIR)),
            undefined=StrictUndefined,
            keep_trailing_newline=True,
            autoescape=False,
        )
        env.filters["tojson"] = _tojson
        _JINJA_ENV = env
    return _JINJA_ENV


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
def _affinity_maps() -> tuple[dict, dict]:
    _codegen_seed.ensure_registered()
    return (
        _detlex.mapping("codegen.affinity.object"),
        _detlex.mapping("codegen.affinity.action"),
    )


# Compatibilita' per gli ispettori esistenti; il generatore rilegge il
# catalogo a ogni chiamata per vedere una lingua appena materializzata.
_AFFINITY_BY_OBJ, _AFFINITY_BY_VERB = _affinity_maps()


def _default_affinity(plan) -> list:
    """Affinity baseline QUALIFIED: combina verbo+oggetto in frasi (es.
    'cerca mail', 'find email') invece di bare nouns separati.

    Razionale §7.3 (bug F4 22/5/2026): `find_messages` con affinity
    `[mail, email, find, search]` matchava «mandami una mail» (send_messages
    intent) perche' bare 'mail' overlap query. Cartesian verbo×oggetto
    risolve: 'cerca mail' NON matcha 'mandami mail'.

    Cap 15. Fallback se uno dei due set e' vuoto: concat (back-compat).
    """
    affinity_by_obj, affinity_by_verb = _affinity_maps()
    objs = list(affinity_by_obj.get(plan.obj, []))
    verbs = list(affinity_by_verb.get(plan.verb, []))
    if not objs or not verbs:
        # Fallback: concat dedup capped 15.
        seen, out = set(), []
        for t in objs + verbs:
            if t not in seen:
                seen.add(t)
                out.append(t)
        return out[:15]
    # Cartesian qualified: verbo + " " + oggetto. Cap 15 = ~3 verbi × 5 obj.
    out: list[str] = []
    for v in verbs:
        for o in objs:
            phrase = f"{v} {o}"
            if phrase not in out:
                out.append(phrase)
            if len(out) >= 15:
                return out
    return out


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
    _codegen_seed.ensure_registered()
    # Identita' semantiche stabili; sorgente e resa vivono nel catalogo output.
    term_keys = (
        "MSG_CODEGEN_TERM_LIST", "MSG_CODEGEN_TERM_IDENTIFIER",
        "MSG_CODEGEN_TERM_START", "MSG_CODEGEN_TERM_END",
        "MSG_CODEGEN_TERM_DEFAULT", "MSG_CODEGEN_TERM_PLURAL",
        "MSG_CODEGEN_TERM_CAP", "MSG_CODEGEN_TERM_WINDOW",
        "MSG_CODEGEN_TERM_CALENDAR", "MSG_CODEGEN_TERM_STRING",
        "MSG_CODEGEN_TERM_RETURNED", "MSG_CODEGEN_TERM_MAX_ENTRIES",
        "MSG_CODEGEN_TERM_PURE_COMPUTE", "MSG_CODEGEN_TERM_SINGLE_ID",
    )
    out = it
    for key in term_keys:
        source = _i18n.get_for_language(key, "it")
        target = _i18n.get_for_language(key, "en")
        if not source.startswith("<missing:"):
            out = out.replace(source, target)
    return out


# ---------------------------------------------------------------------------
# Reverse_pattern -> vectorial coalesce hint
# ---------------------------------------------------------------------------


def _resource_input_group(plan) -> Optional[list[str]]:
    """Return the scalar/plural/entries group for a positional resource id.

    The translator creates this trio from an uppercase positional placeholder
    in the source CLI.  Deriving the group from ``ArgSpec`` keeps codegen
    independent from provider names and natural-language command examples.
    """
    args = list(plan.args)
    names = {arg.name for arg in args}
    for arg in args:
        if not getattr(arg, "positional", False) or arg.type != "string":
            continue
        plural = f"{arg.name}s"
        if plural in names and "entries" in names:
            return [arg.name, plural, "entries"]
    return None


def _vectorial_hint_from_plan(plan) -> Optional[dict]:
    """Prepare coalescing for a CLI accepting one positional resource id.

    The wrapper interface remains vectorial for both readers and mutators:
    callers may provide the scalar id, a list of ids, or upstream entries.
    The source CLI is invoked once per id and results are aggregated according
    to the executor output contract.

    1/6/2026: emetti il blocco SOLO se il plan ha davvero un arg id su cui
    iterare — `<obj>_id`/`<obj>_ids` (derivati da un positional MAIUSCOLO della
    `## Usage`) o `entries`. Skill flag-based (es. GitHub, dove l'id passa come
    `--number`/`--comment-id` via passthrough) NON hanno questi arg: in quel
    caso il coalesce inventerebbe nomi inesistenti e appenderebbe un positional
    che lo script non accetta (mismatch description-vs-code + chiamata rotta).
    """
    if plan.output_kind not in ("entries", "results"):
        return None
    group = _resource_input_group(plan)
    if group is None:
        return None
    singular, plural, _entries = group
    return {
        "entity": "rows",
        "singular": singular,
        "plural":   plural,
        "output_kind": plan.output_kind,
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
    # Set dei flag scalari `<x>_id` presenti: il loro auto-plurale `<x>_ids`
    # (aggiunto da skill_translator.build_args per §2.1) NON corrisponde a un
    # flag CLI reale quando lo script prende l'id come `--<x>-id` singolo (skill
    # flag-based, es. GitHub `--comment-id`). Emettere `--<x>-ids` produrrebbe un
    # argomento sconosciuto + codice non dichiarato (drift L6). 1/6/2026: in quel
    # caso saltiamo il plurale; la forma scalare passa via `--<x>-id`.
    singular_id_flags = {
        a.name for a in plan.args
        if a.name.endswith("_id") and a.name not in _NON_PASSTHROUGH
    }
    out = []
    for a in plan.args:
        if a.name in _NON_PASSTHROUGH:
            continue
        if getattr(a, "positional", False):
            continue
        if a.type == "array" and a.items_type == "object":
            # Es. events: list[dict] -> non e' passthrough, viene splittato in N call
            continue
        if (a.name.endswith("_ids") and a.type == "array"
                and a.name[:-1] in singular_id_flags):
            # Auto-plurale di un flag id scalare: niente flag CLI corrispondente.
            continue
        cli = kebab_known.get(a.name, a.name.replace("_", "-"))
        out.append({
            "name": a.name,
            "cli": cli,
            # Per calendar_id non vogliamo passare il default "primary" alla skill.
            "skip_default": a.default if a.name == "calendar_id" else None,
        })
    return out


def _positional_cli_args(plan) -> list[dict]:
    """Args declared positionally by the source skill examples, in order."""
    return [
        {"name": arg.name}
        for arg in plan.args
        if getattr(arg, "positional", False)
    ]


# ---------------------------------------------------------------------------
# ISO validations: args che vanno validati con _validate_iso_tz
# ---------------------------------------------------------------------------


def _iso_validations(plan) -> list:
    # ISO 8601-con-offset e' un vincolo dei soli eventi calendario (Google
    # Calendar esige timezone). Altri provider usano date-time piu' libere
    # (es. GitHub `since` accetta ISO senza offset stretto): NON imporre una
    # validazione che la description non dichiara, o L6 segnala drift
    # (1/6/2026). Limitiamo al dominio calendar.
    if plan.obj != "events":
        return []
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
# (il modello locale) a collegare la query naturale ("appuntamenti") al tool
# canonico. Senza questa specializzazione, la description "events via skill
# `calendar list`" e' troppo astratta e il PLANNER preferisce
# request_new_executor.
_SKILL_DOMAINS = (
    "calendar", "gmail", "drive", "sheets", "docs", "contacts",
)


def _domain_phrasing() -> dict:
    _codegen_seed.ensure_registered()
    out = {}
    for domain in _SKILL_DOMAINS:
        prefix = f"MSG_CODEGEN_DOMAIN_{domain.upper()}"
        out[domain] = {
            f"{field}_{lang}": _i18n.get_for_language(
                f"{prefix}_{field.upper()}", lang,
            )
            for field in ("noun", "service", "examples")
            for lang in ("it", "en")
        }
    return out


_SKILL_DOMAIN_PHRASING = _domain_phrasing()


def _action_description(verb: str, lang: str) -> str:
    key = f"MSG_CODEGEN_ACTION_{str(verb or '').upper()}"
    text = _i18n.get_for_language(key, lang)
    if text.startswith("<missing:"):
        return _i18n.get_for_language("MSG_CODEGEN_ACTION_FALLBACK", lang)
    return text


def _description_boilerplate(plan) -> tuple[str, str]:
    """Description boilerplate IT+EN seguendo §6 stile prescrittivo.

    Quando il `plan.skill_domain` e' in `_SKILL_DOMAIN_PHRASING`, la
    description usa il nome del servizio reale (Google Calendar, Gmail,
    ...) e l'esempio di query naturale. Help il PLANNER a routing
    deterministico senza fallback a request_new_executor.
    """
    _codegen_seed.ensure_registered()
    verb_desc_it = _action_description(plan.verb, "it")
    verb_desc_en = _action_description(plan.verb, "en")
    output_field = plan.output_kind

    # FORMATO A CAPITOLI (REGOLA UNIVERSALE §2.5): SCOPO/PATTERN/NON/OUT.
    # Era prosa colloquiale ("DEVI usarla per operazioni su X dello skill
    # backend. USO CORRETTO: name(...)"); ora pattern-oriented e stringato
    # (fix 2/6/2026). PATTERN = call literal con gli arg richiesti (o i primi).
    _args = list(getattr(plan, "args", None) or [])
    _req = [a for a in _args if getattr(a, "required", False)] or _args[:2]

    def _ph(a):
        t = getattr(a, "type", "string")
        return "N" if t in ("integer", "number") else '"..."'
    _sig = ", ".join(f"{a.name}={_ph(a)}" for a in _req)
    call = f"{plan.name}({_sig})"

    phrase = _domain_phrasing().get(plan.skill_domain or "")
    if phrase:
        it = _i18n.get_for_language(
            "MSG_CODEGEN_DESCRIPTION_PROVIDER", "it",
            verb=verb_desc_it, noun=phrase["noun_it"],
            service=phrase["service_it"], call=call,
            output_field=output_field,
        )
        en = _i18n.get_for_language(
            "MSG_CODEGEN_DESCRIPTION_PROVIDER", "en",
            verb=verb_desc_en, noun=phrase["noun_en"],
            service=phrase["service_en"], call=call,
            output_field=output_field,
        )
        return it, en

    # Fallback generico per domini non in mapping (skill nuova non Google).
    obj = plan.obj
    dom = plan.skill_domain or ""
    it = _i18n.get_for_language(
        "MSG_CODEGEN_DESCRIPTION_GENERIC", "it", verb=verb_desc_it,
        obj=obj, domain=dom, call=call, output_field=output_field,
    )
    en = _i18n.get_for_language(
        "MSG_CODEGEN_DESCRIPTION_GENERIC", "en", verb=verb_desc_en,
        obj=obj, domain=dom, call=call, output_field=output_field,
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
    def sample(arg):
        if arg.name == "repo":
            return "owner/repo"
        if arg.name in {"number", "comment_id", "workflow_id"}:
            return 1
        if arg.name == "target":
            return "issue:1"
        if arg.type == "integer":
            return 1
        if arg.type == "number":
            return 1.0
        if arg.type == "boolean":
            return True
        if arg.type == "array":
            return ["test"]
        return "test"

    valid_input = {arg.name: sample(arg) for arg in plan.args if arg.required}
    out = []
    # Test 1: unknown args fail before any provider process is started.
    out.append({
        "name": "rejects_unknown_args_offline",
        "input_toml": _toml_inline_value({"unknown_contract_arg": True}),
        "expect_toml": _toml_inline_value(
            {"ok": False, "error_class": "invalid_args"}),
        "env_toml": "",
    })
    # Test 2: provider subprocess esplicitamente fake, mai rete/credenziali.
    fake = ("skill_test_fakes.empty" if plan.output_kind == "entries"
            else "skill_test_fakes.success")
    out.append({
        "name": "happy_path_offline",
        "input_toml": _toml_inline_value(valid_input),
        "expect_toml": _toml_inline_value({"ok": True}),
        "env_toml": _toml_inline_value({"METNOS_SUBPROCESS_FAKE": fake}),
    })
    # Test 3: auth failure fake -> needs_inputs, no host token or provider call.
    out.append({
        "name": "auth_missing_offline_needs_inputs",
        "input_toml": _toml_inline_value(valid_input),
        "expect_toml": _toml_inline_value({"decision": "needs_inputs"}),
        "env_toml": _toml_inline_value({
            "METNOS_SUBPROCESS_FAKE": "skill_test_fakes.auth_required"}),
    })
    # Test 4: invalid args (specific al verbo)
    if plan.verb == "set":
        out.append({
            "name": "validates_missing_required",
            "input_toml": _toml_inline_value({}),
            "expect_toml": _toml_inline_value({"ok": False, "error_class": "invalid_args"}),
            "env_toml": "",
        })
    elif plan.verb == "delete":
        out.append({
            "name": "validates_missing_id",
            "input_toml": _toml_inline_value({}),
            "expect_toml": _toml_inline_value({"ok": False, "error_class": "invalid_args"}),
            "env_toml": "",
        })
    elif plan.output_kind == "entries":
        out.append({
            "name": "empty_result",
            "input_toml": _toml_inline_value(valid_input),
            "expect_toml": _toml_inline_value({"ok": True, "used": 0}),
            "env_toml": _toml_inline_value({
                "METNOS_SUBPROCESS_FAKE": "skill_test_fakes.empty"}),
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
            "  error_code?: str,\n"
            "  final_message_hint?: str\n"
            "}"
        )
    # Transformative: il code emette `n_<status_word>` (es. n_created/n_updated/
    # n_deleted/n_sent). Lo dichiariamo nello schema cosi' la verifica
    # description-vs-code (L6 stage 6) non flagga drift (1/6/2026).
    status_word = _STATUS_WORD_BY_VERB.get(plan.verb, "done")
    return (
        "{\n"
        "  ok: bool,\n"
        "  decision?: 'needs_inputs',\n"
        "  needs_inputs?: {title, dialog, fmt, on_complete},\n"
        "  results: Array<dict>,\n"
        f"  n_{status_word}: int,\n"
        "  used: int,\n"
        "  partial?: bool,\n"
        "  failures?: Array<{id, error, error_class}>,\n"
        "  error?: str,\n"
        "  error_class?: str,\n"
        "  error_code?: str,\n"
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


def _derive_skill_script(parsed_skill) -> str:
    """Deriva il path dello script API dalla `## Scripts` della SKILL.md.

    Pre-1/6/2026 era hardcoded `scripts/google_api.py` -> ogni skill non-Google
    (github, ...) generava executor che invocano uno script inesistente (bug
    funzionale silenzioso). §7.3: la sorgente unica e' `parsed_skill.scripts`.

    Euristica: fra gli script dichiarati, preferisci quello che assomiglia a
    un client API (`*_api.py`), poi il primo che NON e' di setup/OAuth, poi il
    primo in assoluto. Fallback storico `scripts/google_api.py` solo se la
    skill non dichiara alcuno script.
    """
    scripts = [str(s).strip() for s in (parsed_skill.scripts or []) if str(s).strip()]
    if not scripts:
        return "scripts/google_api.py"
    api_like = [s for s in scripts if Path(s).name.endswith("_api.py") or "api" in Path(s).stem.lower()]
    if api_like:
        return api_like[0]
    non_setup = [s for s in scripts if "setup" not in Path(s).stem.lower()
                 and "oauth" not in Path(s).stem.lower()]
    if non_setup:
        return non_setup[0]
    return scripts[0]


def build_context(plan, parsed_skill, *, description_it=None,
                  description_en=None, affinity=None,
                  skill_script: str | None = None) -> dict:
    """Costruisce il context jinja completo a partire dall'ExecutorPlan."""
    if skill_script is None:
        skill_script = _derive_skill_script(parsed_skill)
    if description_it is None or description_en is None:
        d_it, d_en = _description_boilerplate(plan)
        description_it = description_it or d_it
        description_en = description_en or d_en

    if affinity is None:
        affinity = _default_affinity(plan)

    vectorial_coalesce = _vectorial_hint_from_plan(plan)
    resource_input_group = _resource_input_group(plan)
    iso_validations = _iso_validations(plan)
    passthrough = _passthrough_flags(plan)
    positional = _positional_cli_args(plan)
    if vectorial_coalesce is not None:
        for arg in positional:
            arg["coalesced"] = (
                arg["name"] == vectorial_coalesce["singular"])

    # Drop degli arg auto-plurali `<x>_ids` orfani (1/6/2026): build_args
    # (§2.1) aggiunge il plurale per ogni flag id scalare `<x>_id`, ma se
    # l'executor e' flag-based single-call (niente _coalesce, l'id va come
    # `--<x>-id`) il plurale non e' ne' un flag CLI ne' iterabile: il code lo
    # ignora. Dichiararlo nel manifest crea drift description-vs-code (L6). Lo
    # rimuoviamo dagli arg dichiarati cosi' il manifest riflette cio' che il
    # code usa davvero. NB: con _coalesce attivo il plurale E' consumato →
    # nessun drop.
    _orphan_plurals: set = set()
    if vectorial_coalesce is None:
        _singular_id = {
            a.name for a in plan.args
            if a.name.endswith("_id") and a.name not in _NON_PASSTHROUGH
        }
        for a in plan.args:
            if (a.name.endswith("_ids") and a.type == "array"
                    and a.name[:-1] in _singular_id):
                _orphan_plurals.add(a.name)

    args_ctx = [_arg_to_ctx(a) for a in plan.args if a.name not in _orphan_plurals]

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
    rc_prompt_it = _i18n.get_for_language(
        "MSG_CODEGEN_OAUTH_PROMPT", "it", skill=parsed_skill.name,
    ) if has_oauth else ""
    rc_prompt_en = _i18n.get_for_language(
        "MSG_CODEGEN_OAUTH_PROMPT", "en", skill=parsed_skill.name,
    ) if has_oauth else ""

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
        # GitHub is a Metnos-owned builtin skill whose executors are authored
        # and maintained by us.  Its installation path reuses the skill bundle
        # substrate, but that path must never turn its origin into "imported".
        "builtin_handcrafted": parsed_skill.name == "github",
        **generated_contract_context(lifecycle="active"),
        "skill_domain": plan.skill_domain,
        "skill_action": plan.skill_action,
        "skill_script": skill_script,
        "affinity": affinity,
        "description_it": description_it,
        "description_en": description_en,
        "args": args_ctx,
        "allowed_args_py": repr([arg["name"] for arg in args_ctx]),
        "required_args_py": repr(tuple(
            arg["name"] for arg in args_ctx if arg.get("required"))),
        "requires_one_of": ([resource_input_group]
                            if resource_input_group is not None else []),
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
        # §2.1: azioni MUTANTI per-item (send/create/set/change) diventano
        # vettoriali via `entries` (from_step) + `<arg>_template` — un loop sul
        # singolo CLI, aggregato in results. delete usa gia' vectorial_coalesce
        # (id-loop); find/read ritornano gia' liste dall'API.
        "vectorial_entries": plan.verb in ("send", "create", "set", "change"),
        "iso_validations": iso_validations,
        "passthrough_flags": passthrough,
        "positional_cli_args": positional,
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
    rendered = tmpl.render(**context)
    validate_generated_manifest_text(rendered, expected_lifecycle="active")
    return rendered


def render_executor_py(context: dict) -> str:
    env = _jinja_env()
    tmpl = env.get_template("executor.py.j2")
    return tmpl.render(**context)


def _lang_state_for_manifest(manifest_text: str) -> str:
    """Build canonical state from every localized manifest surface."""
    import tomllib
    from i18n_materializer import migrate_language_state_bytes

    parsed = tomllib.loads(manifest_text)
    return migrate_language_state_bytes(
        b"{}", manifest=parsed,
    ).state_bytes.decode("utf-8")


def generate_executor_files(plan, parsed_skill, executor_dir, *,
                            description_it=None, description_en=None,
                            affinity=None, skill_script: str | None = None):
    """Genera 3 file in `executor_dir/<plan.name>/`. Crea la dir.

    Ritorna dict `{manifest_path, code_path, lang_state_path}`.

    DEVI: passare un ExecutorPlan + ParsedSkill (output di Task A/B).
    NON DEVI: scrivere in <install_root>/.
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
    lang_state_text = _lang_state_for_manifest(manifest_text)

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
