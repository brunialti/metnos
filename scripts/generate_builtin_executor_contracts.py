#!/usr/bin/env python3
"""Generate canonical signed-manifest inputs for planner-visible builtins.

The generated manifests live outside ``executors/`` because their transport is
in-process.  They are nevertheless ordinary Executor Standard contracts: the
loader verifies their signature and code digest before catalog admission.
"""
from __future__ import annotations

import json
import os
import sys
import argparse
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime"
OUT = RUNTIME / "builtin_executor_contracts"
sys.path.insert(0, str(RUNTIME))


_META = {
    "admin": (
        "Esegue un comando shell di sistema privilegiato solo attraverso il vaglio.",
        "Runs one privileged system shell command only through the approval gate.",
        [("system:admin", ["mount", "systemctl", "chmod", "chown", "apt"])],
        "{ok: bool, decision?: str, argv?: Array<str>, error?: str}",
    ),
    "create_tasks": (
        "Crea un task schedulato o ricorrente.",
        "Creates a scheduled or recurring task.",
        [("metnos:write", ["scheduled_tasks"])],
        "{ok: bool, results?: Array<object>, task?: object, error?: str}",
    ),
    "list_tasks": (
        "Elenca i task schedulati visibili all'attore.",
        "Lists scheduled tasks visible to the actor.",
        [("metnos:read", ["scheduled_tasks"])],
        "{ok: bool, entries: Array<object>, error?: str}",
    ),
    "delete_tasks": (
        "Elimina task schedulati identificati per id o nome.",
        "Deletes scheduled tasks identified by id or name.",
        [("metnos:write", ["scheduled_tasks"])],
        "{ok: bool, results?: Array<object>, error?: str}",
    ),
    "read_tasks": (
        "Legge il dettaglio di un task schedulato noto.",
        "Reads details for one known scheduled task.",
        [("metnos:read", ["scheduled_tasks"])],
        "{ok: bool, entries?: Array<object>, task?: object, error?: str}",
    ),
    "set_tasks": (
        "Aggiorna stato o fire controllato di un task schedulato.",
        "Updates state or performs a controlled fire of a scheduled task.",
        [("metnos:write", ["scheduled_tasks"])],
        "{ok: bool, results?: Array<object>, task?: object, error?: str}",
    ),
    "read_tasks_history": (
        "Legge lo storico bounded delle esecuzioni di un task.",
        "Reads the bounded execution history of a task.",
        [("metnos:read", ["scheduled_tasks_history"])],
        "{ok: bool, history: Array<object>, used: int, cap_value: int, truncated: bool, error?: str}",
    ),
    "list_skills": (
        "Elenca le skill installate e il loro stato runtime.",
        "Lists installed skills and their runtime state.",
        [("metnos:read", ["skill_registry"])],
        "{ok: bool, entries: Array<object>, error?: str}",
    ),
    "set_skills": (
        "Abilita o disabilita una skill installata.",
        "Enables or disables an installed skill.",
        [("metnos:write", ["skill_registry"])],
        "{ok: bool, results?: Array<object>, error?: str}",
    ),
    "get_preferences": (
        "Elenca le preferenze personali impostate dall'utente.",
        "Lists the personal preferences the user has set.",
        [("metnos:read", ["user_prefs"])],
        "{ok: bool, entries: Array<object>, available: Array<object>, note?: str, error?: str}",
    ),
    "set_preferences": (
        "Imposta una preferenza personale su un valore ammesso.",
        "Sets one personal preference to an allowed value.",
        [("metnos:write", ["user_prefs"])],
        "{ok: bool, results?: Array<object>, ok_count?: int, error?: str}",
    ),
    "delete_preferences": (
        "Rimuove preferenze personali riportandole al valore predefinito.",
        "Removes personal preferences, restoring their default value.",
        [("metnos:write", ["user_prefs"])],
        "{ok: bool, results?: Array<object>, ok_count?: int, error?: str}",
    ),
    "find_entries": (
        "Interroga record in uno store dichiarato.",
        "Queries records in a declared store.",
        [("metnos:read", ["store_registry"])],
        "{ok: bool, entries: Array<object>, truncated?: bool, error?: str}",
    ),
    "write_entries": (
        "Scrive o aggiorna record in uno store dichiarato.",
        "Writes or updates records in a declared store.",
        [("metnos:write", ["store_registry"])],
        "{ok: bool, results: Array<object>, partial?: bool, error?: str}",
    ),
    "delete_entries": (
        "Elimina record da uno store dichiarato.",
        "Deletes records from a declared store.",
        [("metnos:write", ["store_registry"])],
        "{ok: bool, results?: Array<object>, error?: str}",
    ),
    "compare_entries": (
        "Confronta entries in memoria con una metrica semantica bounded.",
        "Compares in-memory entries with a bounded semantic metric.",
        [("compute:pure", ["semantic_similarity"])],
        "{ok: bool, entries: Array<object>, used?: int, error?: str}",
    ),
    "describe_images": (
        "Descrive immagini locali osservate senza modificarle.",
        "Describes observed local images without modifying them.",
        [("llm:local", ["vlm"]),
         ("fs:read", ["arg:reference_images", "arg:paths"])],
        "{ok: bool, entries: Array<object>, ok_count: int, fail_count: int, partial?: bool, error?: str}",
    ),
    "describe_entries": (
        "Sintetizza entries in memoria entro budget dichiarati.",
        "Summarizes in-memory entries within declared budgets.",
        [("llm:local", ["summarization"]), ("llm:online", ["summarization"])],
        "{ok: bool, content?: str, entries?: Array<object>, truncated?: bool, error?: str}",
    ),
    "classify_entries": (
        "Classifica entries in un insieme chiuso di classi.",
        "Classifies entries into a closed set of classes.",
        [("llm:local", ["classification"]), ("llm:online", ["classification"])],
        "{ok: bool, entries: Array<object>, partial?: bool, error?: str}",
    ),
    "extract_entries": (
        "Estrae record strutturati da entries gia' osservate.",
        "Extracts structured records from already observed entries.",
        [("llm:local", ["extraction"]), ("llm:online", ["extraction"])],
        "{ok: bool, entries: Array<object>, partial?: bool, truncated?: bool, error?: str}",
    ),
    "start_lre": (
        "avvia un piano LRE registrato per integrazioni che ne conoscono il profilo.",
        "starts a registered LRE plan for integrations that know its profile.",
        [("metnos:write", ["lre"])],
        "{ok: bool, decision?: 'accepted', workload_id?: str, revision_id?: str, state?: str, expected_sources?: int, plan_summary?: {stage_count: int, required_stage_count: int}, limits?: object, status_url?: str, final_message_hint?: str, error?: str}",
    ),
}


_ARG_EN = {
    "intent": "Natural-language description of the requested system operation.",
    "command_proposed": "Single proposed command argv string; no shell chaining.",
    "credentials_domain": "Optional credential binding domain resolved by runtime.",
    "actor_consent_token": "Opaque runtime-owned consent token.",
    "label": "Human-readable task label.", "when": "Schedule expression.",
    "query": "Natural-language query to run when the task fires.",
    "times": "Maximum number of task executions.",
    "grace_window_minutes": "Allowed late-fire window in minutes.",
    "id": "Known stable identifier.", "name": "Known stable name.",
    "enabled": "Whether the resource is enabled.",
    "fire_now": "Request one controlled immediate task fire.",
    "limit": "Maximum number of returned records.",
    "time_window": "Bounded time window.",
    "store": "Name of a declared runtime store.",
    "where": "Exact field-value filter.",
    "order": "Fields used for deterministic ordering.",
    "max_results": "Maximum number of returned results.",
    "from_step": "Previous producer step supplying entries.",
    "key": "Fields forming the upsert key.",
    "set_fields": "Constant fields applied to every written entry.",
    "reference": "Reference text or entry used for comparison.",
    "entries": "Input entries already available in memory.",
    "field": "Entry field used by the operation.",
    "top_n": "Maximum number of top-ranked entries.",
    "min_similarity": "Minimum accepted similarity score.",
    "reference_images": "Local image paths to describe.",
    "paths": "Alias for local image paths.",
    "style": "Requested summary style.", "context": "Optional summary context.",
    "group_by": "Optional grouping field.", "data_kind": "Input data kind hint.",
    "format": "Requested output format.", "tier": "Runtime model tier selector.",
    "max_tokens": "Maximum model output tokens.",
    "dimension": "Classification dimension.",
    "classes": "Closed list of allowed output classes.",
    "criterion": "Classification criterion.",
    "pre_filter": "Optional deterministic pre-filter.",
    "batch_size": "Maximum entries or independent sources per model batch.",
    "fields": "Fields to expose or extract from each entry.",
    "audit_fields": "Labeled source-level fields extracted deterministically without changing record count.",
    "relevance_entries": "Observed anchor records used for deterministic relevance filtering.",
    "relevance_fields": "Anchor-record fields used to derive relevance terms.",
    "relevance_terms": "Additional explicit terms used for relevance filtering.",
    "state_markers": "Optional normalized-state to observed source phrases mapping.",
    "instruction": "Narrow extraction instruction.",
    "max_per_text": "Maximum records extracted from one source text.",
    "max_total": "Maximum records across all sources.",
    "max_sources": "Maximum number of independent sources to process.",
    "drill_down": "Allow bounded follow-up extraction on nested content.",
    "profile": "Exact registered LRE profile selected for the requested result.",
}

# Override per-tool: lo stesso nome d'argomento puo' significare cose diverse
# in domini diversi (`key` e' la chiave di upsert in write_entries e il nome
# della preferenza in set_preferences). Consultato prima di `_ARG_EN`.
# Capitoli §2.5 scritti a mano, per i builtin in cui il confine con un
# fratello NON e' deducibile dallo scopo. La formula generica sotto
# («PATTERN: richiesta naturale tipizzata dal planner. NON: ampliare autorita
# o inventare input.») descrive il contratto d'esecuzione, non il dominio:
# applicata a due tool vicini li rende INDISTINGUIBILI per il planner, che e'
# esattamente il contrario di cio' che §2.5 chiede al capitolo NON.
# Misurato il 29/7: con la formula generica «togli la preferenza sul tono»
# sceglieva `set_preferences` con un valore inventato invece di
# `delete_preferences`, che pure era primo nel pool di routing.
_CHAPTERS = {
    "start_lre": (
        "PATTERN: start_lre(profile=\"images.questions.v1\", paths=[\"/percorso\"]). "
        "NON: scegliere se usare LRE; il runtime affida da sé i lavori lunghi "
        "ammessi. OUT: id, stato, sorgenti, URL.",
        "PATTERN: start_lre(profile=\"images.questions.v1\", paths=[\"/path\"]). "
        "NON: choosing whether to use LRE; the runtime submits eligible long work "
        "itself. OUT: id, state, sources, URL.",
    ),
    "get_preferences": (
        "PATTERN: get_preferences(). NON: non elenca i task schedulati "
        "(list_tasks), le skill installate (list_skills) o il profilo "
        "anagrafico (read_persons). OUT: entries=[{key, value, origin, "
        "applied}] piu' `available` con tutto cio' che si puo' impostare.",
        "PATTERN: get_preferences(). NON: it does not list scheduled tasks "
        "(list_tasks), installed skills (list_skills) or the person profile "
        "(read_persons). OUT: entries=[{key, value, origin, applied}] plus "
        "`available` with everything that can be set.",
    ),
    "set_preferences": (
        "PATTERN: set_preferences(key=\"reply_length\", value=\"breve\"). "
        "NON: per rimuovere o ripristinare il valore predefinito usa "
        "delete_preferences; non modifica sistema o skill. OUT: results=[{key, value, "
        "applied}].",
        "PATTERN: set_preferences(key=\"reply_length\", value=\"breve\"). "
        "NON: use delete_preferences to remove or restore a default; it does "
        "not alter system or skill settings. "
        "OUT: results=[{key, value, applied}].",
    ),
    "delete_preferences": (
        "PATTERN: delete_preferences(keys=[\"tone\"]) o "
        "delete_preferences(all=true). USA: rimuovere o ripristinare preferenze. "
        "NON: utenti o task. OUT: results=[{key, removed, previous}].",
        "PATTERN: delete_preferences(keys=[\"tone\"]) or "
        "delete_preferences(all=true). USE: remove or restore preferences. "
        "NON: users or tasks. OUT: results=[{key, removed, previous}].",
    ),
}


def _description(name: str, purpose_it: str, purpose_en: str) -> tuple:
    chapters = _CHAPTERS.get(name)
    if chapters:
        return (f"SCOPO: {purpose_it} {chapters[0]}",
                f"SCOPO: {purpose_en} {chapters[1]}")
    return (
        f"SCOPO: {purpose_it} PATTERN: richiesta naturale tipizzata dal "
        "planner. NON: ampliare autorita o inventare input. OUT: risultato "
        "JSON dichiarato.",
        f"SCOPO: {purpose_en} PATTERN: natural request typed by the planner. "
        "NON: enlarge authority or invent inputs. OUT: declared JSON result.",
    )


def _preference_arg_en() -> dict:
    """Testi EN degli argomenti delle preferenze.

    L'insieme chiuso di chiavi e valori e' derivato dal registro nella STESSA
    forma usata dal testo italiano: il planner vede gli stessi valori ammessi
    in entrambe le lingue, e una preferenza nuova nel registro compare in tutte
    e due rigenerando il contratto.
    """
    import user_preferences as _up
    return {
        "set_preferences": {
            "key": "Preference to set.",
            "value": ("Allowed canonical value for that preference — "
                      + _up.values_hint()),
        },
        "delete_preferences": {
            "keys": ("Preferences to remove, by exact name. Allowed: "
                     + _up.keys_hint()),
            "all": "true removes every preference the user has set.",
        },
    }


def _arg_en(tool_name: str, arg_name: str) -> str:
    per_tool = _preference_arg_en().get(tool_name) or {}
    static = {
        ("start_lre", "paths"): "Absolute local files or directories to include.",
    }
    return (per_tool.get(arg_name)
            or static.get((tool_name, arg_name))
            or _ARG_EN.get(arg_name)
            or f"Argument {arg_name}.")


# Moduli che dichiarano `BUILTIN_INPROC_SPECS`. Fonte unica per la raccolta
# delle spec e per il recupero dei termini di affinity.
_SPEC_MODULES = (
    "recurring_tasks", "skill_admin", "store_entries",
    "compare_entries", "describe_images", "user_preferences", "lre_submission",
)


# Signed maximums, not deployment grants. The central startup profile and
# scheduler may only lower these values. Builtins absent from this map retain
# the normative serial default.
_EXECUTION = {
    "extract_entries": {
        "effect": "read_only",
        "parallelism_class": 2,
        "resource_class": "llm",
        "concurrency_key": "none",
        "equivalence_gate": "verified",
    },
    "start_lre": {
        "effect": "create_only",
        "parallelism_class": 0,
        "resource_class": "local_io",
        "concurrency_key": "none",
        "equivalence_gate": "unverified",
    },
}

# Internal reasoning is an orthogonal catalog fact.  LLM-only builtins are
# inferred from their llm:* capability; agentic behavior must be explicit.
_INTELLIGENCE = {
    "extract_entries": "agentic",
}


def _q(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def _all_specs():
    specs = {}
    for module_name in _SPEC_MODULES:
        module = __import__(module_name)
        for entry in module.BUILTIN_INPROC_SPECS:
            specs[entry["name"]] = (entry["tool_spec"], Path(module.__file__))
    import classify_entries
    import describe_entries
    import extract_entries
    specs.update({
        "classify_entries": (classify_entries.CLASSIFY_ENTRIES_TOOL,
                             Path(classify_entries.__file__)),
        "describe_entries": (describe_entries.DESCRIBE_ENTRIES_TOOL,
                             Path(describe_entries.__file__)),
        "extract_entries": (extract_entries.EXTRACT_ENTRIES_TOOL,
                            Path(extract_entries.__file__)),
    })
    from system import admin
    admin_schema = admin.MANIFEST_VIRTUAL["args"]
    specs["admin"] = ({"function": {"parameters": admin_schema}}, Path(admin.__file__))
    return specs


def _render(name: str, tool_spec: dict, module_path: Path) -> str:
    purpose_it, purpose_en, caps, output = _META[name]
    if "error_class" not in output:
        output = output[:-1] + ", error_class?: str, error_code?: str}"
    fn = tool_spec.get("function") or {}
    args = fn.get("parameters") or {"type": "object", "properties": {}}
    required = list(args.get("required") or [])
    properties = args.get("properties") or {}
    manifest_dir = OUT / name
    code_rel = os.path.relpath(module_path.resolve(), manifest_dir.resolve())
    affinities = []
    for module_name in _SPEC_MODULES:
        module = sys.modules.get(module_name)
        for entry in getattr(module, "BUILTIN_INPROC_SPECS", []) if module else []:
            if entry.get("name") == name:
                affinities = list(entry.get("affinity") or [])
    lines = [
        '# Generated by scripts/generate_builtin_executor_contracts.py.',
        'manifest_format = "1.0"',
        'executor_standard = "metnos.executor/1.0"',
        f'name = {_q(name)}',
        'version = "1.0.0"',
        *([f'intelligence = {_q(_INTELLIGENCE[name])}']
          if name in _INTELLIGENCE else []),
        'author = "Metnos builtin maintainers"',
        f'affinity = {_q(affinities)}',
        'platforms = ["linux"]',
        'revertible = false',
        'lifecycle = "active"',
        '',
        '[description]',
        f'it = {_q(_description(name, purpose_it, purpose_en)[0])}',
        f'en = {_q(_description(name, purpose_it, purpose_en)[1])}',
        '',
        '[code]',
        f'files = [{_q(code_rel)}]',
        'digest = "sha256:' + ('0' * 64) + '"',
        '',
        '[args]',
        'type = "object"',
        f'required = {_q(required)}',
    ]
    for arg_name, raw_spec in properties.items():
        spec = dict(raw_spec) if isinstance(raw_spec, dict) else {"type": "string"}
        lines.extend(['', f'[args.properties.{arg_name}]'])
        for key in (
            "type", "default", "enum", "minimum", "maximum",
            "minItems", "maxItems",
        ):
            if key in spec:
                lines.append(f'{key} = {_q(spec[key])}')
        if "type" not in spec:
            lines.append('type = "string"')
        if arg_name in {"actor_consent_token"}:
            lines.append('runtime_resolved = true')
        lines.extend([
            '', f'[args.properties.{arg_name}.description]',
            f'it = {_q(str(spec.get("description") or f"Argomento {arg_name}."))}',
            f'en = {_q(_arg_en(name, arg_name))}',
        ])
        items = spec.get("items")
        if isinstance(items, dict) and isinstance(items.get("type"), str):
            lines.extend([
                '', f'[args.properties.{arg_name}.items]',
                f'type = {_q(items["type"])}',
            ])
    lines.extend(['', '[output]', f'schema_inline = {_q(output)}'])
    for cap_name, hints in caps:
        lines.extend(['', '[[capabilities]]', f'name = {_q(cap_name)}',
                      f'hint = {_q(hints)}'])
    execution = _EXECUTION.get(name)
    if execution:
        lines.extend([
            '', '[execution]',
            f'effect = {_q(execution["effect"])}',
            f'parallelism_class = {execution["parallelism_class"]}',
            f'resource_class = {_q(execution["resource_class"])}',
            f'concurrency_key = {_q(execution["concurrency_key"])}',
            f'equivalence_gate = {_q(execution["equivalence_gate"])}',
        ])
    lines.extend([
        '', '[placement]', 'scope = "server"', 'device_ok = false',
        '', '[[tests]]', 'name = "builtin_contract_domain_suite"',
        'reference = "tests/runtime/skills/test_builtin_executor_contracts.py"',
    ])
    if execution and execution["parallelism_class"] > 0:
        lines.extend([
            '', '[[tests]]',
            'name = "parallel_equivalence"',
            'reference = "tests/runtime/entries/test_extract_entries_parser.py::'
            'test_parallel_batches_are_equivalent_and_recompose_in_source_order"',
            'equivalence_runs = 2',
        ])
    lines.append('')
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate canonical manifests for in-process executors.",
    )
    parser.add_argument(
        "--sign",
        action="store_true",
        help=(
            "update code digests and sign generated contracts with the local "
            "author key; otherwise leave signing to sign.py sign-all"
        ),
    )
    args = parser.parse_args()
    specs = _all_specs()
    missing = sorted(set(_META) - set(specs))
    extra = sorted(set(specs) - set(_META))
    if missing or extra:
        raise SystemExit(f"contract inventory mismatch missing={missing} extra={extra}")
    for name in sorted(specs):
        directory = OUT / name
        directory.mkdir(parents=True, exist_ok=True)
        tool_spec, module_path = specs[name]
        rendered = _render(name, tool_spec, module_path)
        (directory / "manifest.toml").write_text(rendered, encoding="utf-8")
        from i18n_materializer import migrate_language_state_bytes
        state_path = directory / "manifest.lang_state.json"
        previous_state = state_path.read_bytes() if state_path.is_file() else b"{}"
        state_path.write_bytes(
            migrate_language_state_bytes(
                previous_state, manifest=tomllib.loads(rendered),
            ).state_bytes,
        )
    suffix = ""
    if args.sign:
        from sign import publish_authoring_update

        published = 0
        for name in sorted(specs):
            _digest, _signature, publication = publish_authoring_update(
                OUT / name,
            )
            published += int(publication is not None)
        suffix = (
            " and admitted them with the local author key"
            if published == 0
            else f" and published {published} immutable contract generations"
        )
    print(f"generated {len(specs)} builtin contracts under {OUT}{suffix}")


if __name__ == "__main__":
    main()
