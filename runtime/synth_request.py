"""Builtin tool `request_new_executor` — telos di non-rinuncia.

Quando il pianificatore non trova nel catalog un executor che copre la richiesta
utente, invece di rispondere "non ho il tool", chiama questo tool meta. Il
runtime intercetta la chiamata e attiva la cascata synt (compose → multistage
generate). Ritorna al LLM una observation con l'esito della sintesi.

Pattern parallelo a `scratchpad_read` (vedi `scratchpad.py`): il tool vive nel
runtime, niente manifest su disco, niente subprocess. Un risultato generato
viene salvato e firmato come candidato; l'attivazione resta subordinata ai gate
dell'Executor Standard e non viene inferita dal solo esito della generazione.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from synt_multistage import run_full as multistage_run_full
from loader import SYNTHESIZED_EXECUTORS_DIR
from executor_birth_synth import (
    SynthBirthData, require_synth_birth_service, submit_synth_multistage,
)
from vocab import render_actions_pipe, render_objects_pipe, render_qualifiers_pipe
from messages import get as _msg
from generated_executor_contract import (
    generated_contract_context,
    validate_generated_manifest_text,
)
from manifest_inventory import ContractId, ManifestOrigin

from logging_setup import get_logger
import config as _C  # §7.11
log = get_logger(__name__)

PROPOSALS_DIR = _C.PATH_USER_DATA / "synt_proposals"


def _toml_value(v):
    """Serializza un valore Python come TOML letterale.
    Usa = per dict (NON : come JSON). Ricorre per dict/list."""
    import json as _json
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if v is None:
        return '""'
    if isinstance(v, str):
        return _json.dumps(v, ensure_ascii=False)
    if isinstance(v, list):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    if isinstance(v, dict):
        parts = [f"{k} = {_toml_value(vv)}" for k, vv in v.items()]
        return "{ " + ", ".join(parts) + " }"
    return _json.dumps(str(v), ensure_ascii=False)


def _validate_birth_tests(executor_dir):
    """Esegue il test_runner.py sul manifest dell'executor sintetizzato.
    Ritorna None se tutti i test passano, una stringa di errore se almeno uno
    fallisce o se il runner stesso esplode (es. import error nel code).
    """
    import subprocess
    from pathlib import Path
    manifest_path = Path(executor_dir) / "manifest.toml"
    if not manifest_path.exists():
        return f"manifest non trovato in {executor_dir}"
    try:
        result = subprocess.run(
            ["python3", str(_C.PATH_RUNTIME / "test_runner.py"), str(manifest_path)],
            capture_output=True, text=True, timeout=60,
        )
    except subprocess.TimeoutExpired:
        return "test_runner timeout (>60s)"
    except Exception as ex:
        return f"test_runner exception: {type(ex).__name__}: {ex}"
    out = (result.stdout or "") + (result.stderr or "")
    # Il runner stampa 'X/Y passati' come summary; se X != Y, almeno uno fallito.
    import re
    m = re.search(r"(\d+)/(\d+)\s+passati", out)
    if m:
        passed, total = int(m.group(1)), int(m.group(2))
        if passed < total:
            # Estrai i nomi dei test falliti per feedback al synt
            failed = re.findall(r"\s+X\s+(\w+)", out)
            return f"{passed}/{total} passati; falliti: {failed[:5]}"
        return None
    if result.returncode != 0:
        return f"test_runner exit {result.returncode}: {out[:300]}"
    return None  # nessun summary trovato, ma exit 0 — assumi ok


def _install_synthesized(run, intent, user_query):
    """Scrive e firma un candidato in SYNTHESIZED_EXECUTORS_DIR/<name>/.

    Il lifecycle resta ``synthesized``: il composer non lo espone finche' un
    successivo gate di ammissione non completa traduzioni, contratto, autorita'
    e prove richieste dallo standard. Idempotente: se la cartella esiste viene
    sovrascritta (oggi e' OK perche' il flusso del turno produce un solo
    install per query).

    PRE-CHECK: il code_text DEVE compilare come Python valido. Se non
    compila, fallisce l'install: meglio rigettare il synth che installare
    un executor con SyntaxError che fallira' sempre a runtime
    (`feedback_no_silent_failure`).
    """
    require_synth_birth_service()
    if not run.name or not run.code_text:
        raise RuntimeError("run senza name o code_text, niente da installare")
    # Validazione sintassi Python: compile() solleva SyntaxError se il
    # codice e' malformato. Stop prima di toccare il filesystem.
    try:
        compile(run.code_text, f"{run.name}.py", "exec")
    except SyntaxError as e:
        raise RuntimeError(
            f"synth code_text per '{run.name}' contiene SyntaxError "
            f"a riga {e.lineno}: {e.msg}. Install rifiutato."
        )

    s1 = (run.stages[0].output or {}) if len(run.stages) >= 1 else {}
    s2 = (run.stages[1].output or {}) if len(run.stages) >= 2 else {}
    s4 = (run.stages[3].output or {}) if len(run.stages) >= 4 else {}

    import tempfile
    # Candidate bytes live outside authoring.  Only commit_birth_snapshot may
    # create/replace SYNTHESIZED_EXECUTORS_DIR/<name>.
    staging_root = Path(tempfile.mkdtemp(prefix="metnos-synt-birth-"))
    out_dir = staging_root / run.name
    out_dir.mkdir(parents=True, exist_ok=True)

    code_filename = f"{run.name}.py"
    (out_dir / code_filename).write_text(run.code_text, encoding="utf-8")

    description = s4.get("description") or intent or run.name
    affinity = s4.get("affinity") or []
    revertible = bool(s1.get("revertible") or False)
    reverse_pattern = s2.get("reverse_pattern")
    capabilities = s2.get("capabilities") or []
    args_schema = {
        "type": "object",
        "required": s2.get("args_required") or [],
        "properties": s2.get("args_properties") or {},
    }

    # Render manifest.toml a mano (toml stdlib non scrive). Stile coerente
    # coi seed: chiavi base + [code] + [args].
    # ADR 0092 Phase 4 (5/5/2026): description e args.properties.<arg>.description
    # sono scritti come table multilingua `[description] <lang> = "..."`
    # dove <lang> = METNOS_LANG corrente (default 'it'). Il daemon notturno
    # `align_manifest_descriptions()` traduce nelle altre lingue.
    import json as _json
    import os as _os
    cur_lang = _os.environ.get("METNOS_LANG", "it")
    _generated_contract = generated_contract_context(lifecycle="synthesized")
    lines = [
        f'# Manifest synthesized — Metnos synt multistage {time.strftime("%Y-%m-%d")}',
        '',
        *_generated_contract["generated_header_toml"].splitlines(),
        '',
        f'name        = "{run.name}"',
        'version     = "0.1.0"',
        'author      = "synt-multistage <synt@metnos.com>"',
        f'affinity    = {_json.dumps(affinity, ensure_ascii=False)}',
        f'revertible  = {"true" if revertible else "false"}',
    ]
    if reverse_pattern:
        if isinstance(reverse_pattern, list):
            lines.append(f'reverse_pattern = {_json.dumps(reverse_pattern, ensure_ascii=False)}')
        else:
            lines.append(f'reverse_pattern = "{reverse_pattern}"')
    lines.extend([
        *_generated_contract["execution_policy_toml"].splitlines(),
        '',
        '[description]',
        f'{cur_lang} = {_json.dumps(description, ensure_ascii=False)}',
        '',
        '[code]',
        f'files  = ["{code_filename}"]',
        'digest = "sha256:placeholder"',  # publication boundary lo aggiorna
        '',
        '[args]',
        'type     = "object"',
        f'required = {_json.dumps(args_schema["required"], ensure_ascii=False)}',
    ])
    # args.properties.<name> + sub-table args.properties.<name>.description
    for arg_name, arg_def in (args_schema.get("properties") or {}).items():
        lines.append('')
        lines.append(f'[args.properties.{arg_name}]')
        # Stampa tutti i campi tranne `description` (che diventa sotto-tabella).
        arg_desc = None
        for k, v in (arg_def or {}).items():
            if k == "description":
                arg_desc = v
                continue
            lines.append(f'{k} = {_toml_value(v)}')
        if arg_desc is not None:
            lines.append('')
            lines.append(f'[args.properties.{arg_name}.description]')
            lines.append(f'{cur_lang} = {_json.dumps(arg_desc, ensure_ascii=False)}')
    if capabilities:
        for cap in capabilities:
            lines.append('')
            lines.append('[[capabilities]]')
            if isinstance(cap, dict):
                for k, v in cap.items():
                    lines.append(f'{k} = {_toml_value(v)}')
            elif isinstance(cap, str):
                lines.append(f'name = {_toml_value(cap)}')

    # Il prompt di code generation impone questo envelope. La forma resta
    # volutamente larga nel candidato; una futura promozione deve verificarla
    # contro il comportamento effettivo prima di attivare l'executor.
    lines.extend([
        '',
        '[output]',
        'schema_inline = """',
        '{',
        '  ok: bool,',
        '  ok_count?: int,',
        '  fail_count?: int,',
        '  entries?: Array<dict>,',
        '  results?: Array<dict>,',
        '  failed?: Array<dict>,',
        '  error?: str,',
        '  error_class?: str',
        '}',
        '"""',
        '',
        '[presentation]',
        'default_view = "list"',
        '',
        '[presentation.list]',
        'mode = "table"',
        'columns = [{ key = "item", source = ["id", "name", "title", "subject", "path", "url", "$entry"], cell_max = 160 }]',
        'max_rows = 200',
        'max_chars = 16000',
        'overflow = "notice"',
    ])

    stage_tests = ((run.stages[2].output or {}).get("tests") or []) \
        if len(run.stages) >= 3 else []
    for test in stage_tests:
        if not isinstance(test, dict):
            continue
        lines.extend([
            '',
            '[[tests]]',
            f'name = {_toml_value(test.get("name") or "generated_case")}',
            f'input = {_toml_value(test.get("input") or {})}',
            f'expect = {_toml_value(test.get("expect") or {})}',
        ])
        if test.get("setup"):
            lines.append(f'setup = {_toml_value(test["setup"])}')
        if test.get("teardown"):
            lines.append(f'teardown = {_toml_value(test["teardown"])}')

    _manifest_text = "\n".join(lines) + "\n"
    validate_generated_manifest_text(
        _manifest_text, expected_lifecycle="synthesized")
    (out_dir / "manifest.toml").write_text(_manifest_text, encoding="utf-8")

    # Derive the companion from the completed JSON-Schema tree.  The shared
    # enumerator covers nested properties/items without generator-specific
    # selector rules.
    import tomllib
    from i18n_materializer import migrate_language_state_bytes
    parsed_manifest = tomllib.loads(_manifest_text)
    (out_dir / "manifest.lang_state.json").write_bytes(
        migrate_language_state_bytes(
            b"{}", manifest=parsed_manifest,
        ).state_bytes,
    )

    import shutil
    try:
        birth = submit_synth_multistage(SynthBirthData(
            candidate_root=out_dir,
            contract_id=ContractId(ManifestOrigin.USER, f"{run.name}/manifest.toml"),
            reason=f"synt multistage: {intent}",
        ))
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)
    if birth.publication is None:
        raise RuntimeError(birth.error_code or "synth_birth_rejected")
    # (Rimosso 21/6 il blocco i18n at-gen-time: leggeva run.description/
    # run.affinity_keywords — attributi INESISTENTI su MultistageRun → sempre
    # no-op, codice morto. Gli executor synth portano description/affinity nel
    # MANIFEST in lingua utente, non nel DB i18n — vedi memoria
    # i18n-scope-by-executor-class.)
    return SYNTHESIZED_EXECUTORS_DIR / run.name


def build_synth_request_tool() -> dict:
    """Costruisce lo schema tool nella lingua del contesto corrente.

    Il nome del tool, gli argomenti e il vocabolario canonico restano stabili;
    la prosa che orienta il modello proviene dal catalogo i18n. Il builder va
    chiamato nel contesto del turno, cosi' una nuova lingua non richiede branch
    nel codice.
    """
    return {
        "type": "function",
        "function": {
            "name": "request_new_executor",
            "description": _msg("PROMPT_SYNTH_TOOL_DESCRIPTION"),
            "parameters": {
                "type": "object",
                "required": ["expected_name", "intent"],
                "properties": {
                    "expected_name": {
                        "type": "string",
                        "description": _msg(
                            "PROMPT_SYNTH_EXPECTED_NAME",
                            actions=render_actions_pipe(),
                            objects=render_objects_pipe(),
                            qualifiers=render_qualifiers_pipe(),
                        ),
                    },
                    "intent": {
                        "type": "string",
                        "description": _msg("PROMPT_SYNTH_INTENT"),
                    },
                },
            },
        },
    }


# Compatibilita' per importatori esterni. Il runtime deve preferire il builder
# quando prepara un catalogo per un turno.
SYNTH_REQUEST_TOOL = build_synth_request_tool()


def _find_canonical_alias(expected_name, catalog):
    """Se `expected_name` non esiste nel catalog ma esiste un alias
    `<producer_verb>_<object>[_qualifier]` per lo stesso object, ritornalo.

    Caso d'uso (4/5/2026): PLANNER chiama `request_new_executor(expected_name=
    "list_processes")` ma l'executor canonico e' `get_processes`. Stesso
    object "processes", verbo producer diverso. Senza redirect, synt parte
    e ricostruisce un duplicato.

    Returns: nome canonico in catalog, oppure None.
    """
    try:
        from vocab import PRODUCER_VERBS, OBJECTS
    except Exception:
        return None
    if not expected_name or not catalog:
        return None
    parts = expected_name.split("_", 2)
    if len(parts) < 2:
        return None
    verb = parts[0]
    # Solo verbi producer hanno alias semanticamente equivalenti.
    # Per non-producer (move/delete/send/write/compute/change/...) la
    # richiesta e' azione, non lookup: nessun alias possibile.
    if verb not in PRODUCER_VERBS:
        return None
    rest = "_".join(parts[1:])
    obj_qual = rest.split("_", 1)
    obj = obj_qual[0]
    qualifier = obj_qual[1] if len(obj_qual) > 1 else None
    if obj not in OBJECTS:
        return None
    cat_names = catalog.executors if hasattr(catalog, "executors") else catalog
    for pv in ("get", "find", "read", "list"):
        if pv == verb:
            continue
        candidate = f"{pv}_{obj}_{qualifier}" if qualifier else f"{pv}_{obj}"
        if candidate in cat_names:
            return candidate
        if qualifier:
            candidate_no_q = f"{pv}_{obj}"
            if candidate_no_q in cat_names:
                return candidate_no_q
    return None


def handle_synth_request(args, *, user_query, progress=None, verbose=False, current_steps=None):
    """Gestisce la chiamata a request_new_executor.

    Lancia synt_multistage.run_full sincronamente (~150 s wall). Usa i tier
    logici `middle` per gli stadi procedurali, `creative` per la descrizione,
    `wise` per il codice e per la verifica semantica. Provider,
    modello e policy sono quelli configurati dall'istanza; il tier fast del
    planner non e' adatto alla sintesi.
    Salva la proposal in PROPOSALS_DIR e ritorna una observation strutturata
    per il LLM.

    `progress` (opzionale): istanza di runtime.progress.Progress. Se passato,
    apre il canale visivo (start/update/finish) per UX su Telegram/HTML.

    Pre-call short-circuit (4/5/2026, ADR 0076): evita la cascata synt quando
    l'executor canonico esiste gia'. Due casi:
    - **already_in_catalog**: `expected_name` matcha un executor presente.
      Ritorna immediato con observation che istruisce il PLANNER a chiamarlo.
    - **redirected**: `expected_name` non matcha ma esiste un alias
      `<producer_verb>_<object>` per lo stesso object (es. `list_processes`
      → `get_processes`). Ritorna immediato con il nome canonico.
    Risparmio: ~150 s per chiamata. Risolve il loop ricorrente
    `request_new_executor(expected_name="list_processes")`.
    """
    expected_name = (args or {}).get("expected_name") or ""
    intent = (args or {}).get("intent") or user_query
    if not expected_name:
        return {"ok": False, "error": _msg("ERR_SYNTH_EXPECTED_NAME_REQUIRED")}

    try:
        from loader import (
            VISIBILITY_COMPOSER as _VISIBILITY_COMPOSER,
            filter_for_visibility as _filter_for_visibility,
            load_catalog as _load_catalog,
        )
        _raw_cat = _load_catalog(verify=True)
        _cat = _filter_for_visibility(_raw_cat, _VISIBILITY_COMPOSER)
    except Exception:
        _raw_cat = None
        _cat = None

    # Un candidato gia' generato non e' un tool disponibile e non deve essere
    # rigenerato o presentato al planner come tale. Rimane una singola unita'
    # in attesa dei gate di promozione.
    _existing_candidate = (
        _raw_cat.executors.get(expected_name)
        if _raw_cat is not None else None
    )
    if _existing_candidate is not None and _existing_candidate.lifecycle in {
            "proposed", "synthesized"}:
        # Store publication can become current before a final registry
        # callback reports failure. Re-entering the same publisher on the
        # existing candidate is idempotent and repairs that tail; merely
        # returning "already exists" would strand the reconciliation.
        try:
            from manifest_inventory import ManifestLayout, resolve_manifest_layout
            if resolve_manifest_layout() is ManifestLayout.STORE_ONLY:
                authoring_path = _existing_candidate.authoring_manifest_path
                if authoring_path is None:
                    raise RuntimeError("candidate authoring provenance missing")
                birth = submit_synth_multistage(SynthBirthData(
                    candidate_root=authoring_path.parent,
                    contract_id=ContractId(
                        ManifestOrigin.USER, f"{expected_name}/manifest.toml",
                    ),
                    reason=f"replay synthesized candidate {expected_name}",
                ))
                if birth.publication is None:
                    raise RuntimeError(birth.error_code or "synth_birth_rejected")
        except Exception as exc:
            return {
                "ok": False,
                "error": f"candidate publication requires retry: {exc}",
                "proposed_name": expected_name,
            }
        return {
            "ok": True,
            "synthesized": True,
            "installed": False,
            "candidate_created": True,
            "candidate_existing": True,
            "planner_visible": False,
            "lifecycle": _existing_candidate.lifecycle,
            "proposed_name": expected_name,
            "message": _msg("MSG_SYNTH_CANDIDATE_CREATED", name=expected_name),
        }

    if _cat is not None and expected_name in _cat.executors:
        return {
            "ok": True,
            "synthesized": False,
            "already_in_catalog": True,
            "name": expected_name,
            "expected_name": expected_name,
            "message": _msg(
                "MSG_SYNTH_ALREADY_AVAILABLE", name=expected_name,
            ),
        }

    if _cat is not None:
        canonical = _find_canonical_alias(expected_name, _cat)
        if canonical:
            return {
                "ok": True,
                "synthesized": False,
                "redirected": True,
                "name": canonical,
                "expected_name": expected_name,
                "message": _msg(
                    "MSG_SYNTH_CANONICAL_REDIRECT",
                    canonical=canonical, expected=expected_name,
                ),
            }

    # ── L7 admission: anti-synth quando intent matcha imported skill ──
    # (ADR 0125 / 0114 5° gate, 12/5/2026). Bug live 11/5: PLANNER chiede
    # synth di `read_appointments`/`read_calendar` mentre `read_events`
    # (imported da google-workspace via skill_importer ADR 0123) gia'
    # copre l'intent (verb=read, object=events tramite sinonimo
    # appointments/calendar → events). Determinismo §7.9: lookup tabellare
    # via `vocab.lookup_imported_for_intent`, no LLM.
    #
    # Pattern: parse `expected_name` come `<verb>_<object>[_qualifier]`,
    # risolve object_token via sinonimi IT+EN canonicalizzati, cerca
    # imported con stesso (verb, canonical_object). Match → reject con
    # `error="duplicates_imported_skill_<name>"`.
    try:
        from vocab import lookup_imported_for_intent
        parts = expected_name.split("_", 2)
        if len(parts) >= 2:
            verb_l7 = parts[0]
            object_l7 = parts[1]
            imported_hits = lookup_imported_for_intent(verb_l7, object_l7)
            if imported_hits:
                primary = imported_hits[0]
                return {
                    "ok": True,
                    "synthesized": False,
                    "redirected": True,
                    "l7_admission": True,
                    "name": primary,
                    "expected_name": expected_name,
                    "error": f"duplicates_imported_skill_{primary}",
                    "imported_alternatives": imported_hits,
                    "message": _msg(
                        "MSG_SYNTH_IMPORTED_REDIRECT",
                        verb=verb_l7, object=object_l7, name=primary,
                    ),
                }
    except Exception as _e:
        # L7 deve essere best-effort: errori nella tabella non bloccano la
        # cascata synt (fallback al comportamento legacy). Log a debug.
        log.debug("L7 admission skip per errore: %s", _e)

    # ── Binding short-circuit (24/5/2026, ADR 0076 extension) ───────────
    # Quando la query ha un `binding` (cifs/ssh/web) riconosciuto, esistono
    # tool builtin nativi che lo coprono — synthesis e' improprio (verbi
    # tipo "mount", "ssh", "login" sono fuori dal vocab chiuso §2.2 e
    # verrebbero rejected). Ridirigi al builtin appropriato prima di
    # iniziare la cascata.
    #
    # Razionale §7.3: il binding e' la single source of truth per la
    # selezione del canale di esecuzione. La cascata synth e' riservata
    # a intent realmente fuori dal sistema (es. nuova classe di problema).
    try:
        from agent_runtime import detect_binding as _detect_binding
        _binding = _detect_binding(user_query or "")
    except Exception:
        _binding = "generic"
    _BINDING_TO_BUILTIN = {
        "cifs": "admin",         # mount via sudoer
        "ssh":  "admin",          # comandi remoti via sudoer
        "web":  "login_urls",  # autenticazione HTTP/cookie del dominio urls
    }
    _redirect_tool = _BINDING_TO_BUILTIN.get(_binding)
    if _redirect_tool:
        return {
            "ok": True,
            "synthesized": False,
            "redirected": True,
            "binding_short_circuit": True,
            "binding": _binding,
            "name": _redirect_tool,
            "expected_name": expected_name,
            "message": _msg(
                "MSG_SYNTH_BINDING_REDIRECT",
                binding=_binding, name=_redirect_tool,
            ),
        }

    PROPOSALS_DIR.mkdir(parents=True, exist_ok=True)

    # Synt selects workload contracts only.  Provider/model/decoding policy
    # are resolved centrally for each logical tier.
    from llm_router import LLMRouter
    from llm_workloads import tier_for
    _router = LLMRouter()
    _providers = {
        workload: _router.provider(tier_for(workload))
        for workload in (
            "synt.procedural", "synt.description", "synt.multistage",
            "synt.semantic_verify",
        )
    }

    def _invoke(workload, system, user, max_tokens, **kwargs):
        t0 = time.time()
        r = _providers[workload].chat(
            system, user, max_tokens=max_tokens, **kwargs)
        return {
            "text": r.text or "",
            "in_tokens": r.in_tokens,
            "out_tokens": r.out_tokens,
            "latency_ms": int((time.time() - t0) * 1000),
        }

    def _llm_procedural(system, user, max_tokens=2500, **kwargs):
        return _invoke(
            "synt.procedural", system, user, max_tokens, **kwargs)

    def _llm_creative(system, user, max_tokens=2500, **kwargs):
        return _invoke(
            "synt.description", system, user, max_tokens, **kwargs)

    def _llm_wise(system, user, max_tokens=5000, **kwargs):
        return _invoke(
            "synt.multistage", system, user, max_tokens, **kwargs)

    def _llm_fidelity(system, user, max_tokens=2500, **kwargs):
        return _invoke(
            "synt.semantic_verify", system, user, max_tokens, **kwargs)

    if verbose:
        print(f"[synth_request] starting multistage for expected_name={expected_name!r} intent={intent!r}")

    if progress is not None:
        progress.start(_msg("MSG_SYNTH_PROGRESS_START", name=expected_name))

    t_start = time.time()
    try:
        run = multistage_run_full(
            intent, _llm_procedural, _llm_wise,
            llm_call_creative=_llm_creative,
            llm_call_fidelity=_llm_fidelity,
            progress=progress,
        )
    except Exception as ex:
        if progress is not None:
            progress.update_free(_msg(
                "MSG_SYNTH_PROGRESS_INTERRUPTED",
                reason=f"{type(ex).__name__}: {ex}",
            ))
        return {
            "ok": False,
            "error": _msg(
                "ERR_SYNTH_MULTISTAGE_FAILED",
                reason=f"{type(ex).__name__}: {ex}",
            ),
            "expected_name": expected_name,
        }
    elapsed_s = round(time.time() - t_start, 1)

    proposal_id = f"{int(t_start)}_{(run.name or expected_name).replace('/', '_')}"
    proposal_path = PROPOSALS_DIR / f"{proposal_id}.json"

    # ADR 0122: enrich con path shape + ETA index lookup. Niente LLM,
    # tutto deterministico (path_shape_hash sui chosen_tool produttivi
    # del turno corrente fino a ora). Se l'index e' freddo, i campi
    # restano None e l'evaluator interpreta come "non disponibile".
    path_hash = ""
    path_steps_list: list[str] = []
    path_eta_p50_ms: int | None = None
    path_eta_p95_ms: int | None = None
    path_call_count_60d: int | None = None
    try:
        from path_shape import path_shape_hash as _ps_hash, steps_to_tools
        path_steps_list = steps_to_tools(current_steps or [])
        path_hash = _ps_hash(current_steps or [])
    except Exception:
        path_hash = ""
    if path_hash:
        try:
            from proposals_eta_index import lookup as _eta_lookup, count_shape_calls
            rec = _eta_lookup(path_hash)
            if rec:
                path_eta_p50_ms = rec.get("p50_ms")
                path_eta_p95_ms = rec.get("p95_ms")
            path_call_count_60d = count_shape_calls(
                path_hash, since_ts=time.time() - 60 * 86400,
            )
        except Exception:
            pass

    proposal_doc = {
        "id": proposal_id,
        "expected_name": expected_name,
        "intent": intent,
        "user_query": user_query,
        "ts_start": t_start,
        "elapsed_s": elapsed_s,
        "final_state": run.final_state,
        "name": run.name,
        "abandon_reason": run.abandon_reason,
        # ADR 0122: instrumentation forward
        "path_hash": path_hash,
        "path_steps": path_steps_list,
        "path_n_steps": len(path_steps_list),
        "path_eta_p50_ms": path_eta_p50_ms,
        "path_eta_p95_ms": path_eta_p95_ms,
        "path_call_count_60d": path_call_count_60d,
        "stages": [
            {
                "stage": s.stage,
                "success": s.success,
                "latency_ms": s.latency_ms,
                "error": s.error,
                "output": s.output if s.success else None,
            }
            for s in run.stages
        ],
    }
    try:
        proposal_path.write_text(json.dumps(proposal_doc, ensure_ascii=False, indent=2))
    except Exception as ex:
        if verbose:
            print(f"[synth_request] failed to persist proposal: {ex}")

    if run.final_state == "synthesized":
        # Scrive manifest+code nel pool dei candidati e firma l'unita'. La firma
        # non implica attivazione: il lifecycle synthesized resta fuori dal
        # catalogo del composer.
        install_error = None
        try:
            _install_synthesized(run, intent, user_query)
        except Exception as ex:
            install_error = f"{type(ex).__name__}: {ex}"
            if verbose:
                print(f"[synth_request] install fallito: {install_error}")

        # Test-driven validation: dopo install, esegue i birth test del manifest.
        # Se uno o piu' test falliscono, rifiuta l'install (rimuove la dir) e
        # ritorna error con dettaglio dei fallimenti. Cosi' il pianificatore
        # riceve un'observation onesta invece di chiamare un executor broken.
        # `feedback_no_silent_failure`: meglio dichiarare il fallimento di
        # generazione che installare un broken.
        if install_error is None:
            test_error = _validate_birth_tests(SYNTHESIZED_EXECUTORS_DIR / run.name)
            if test_error:
                # Rimuovi l'install fallito per non lasciare un executor broken in catalog.
                import shutil
                try:
                    shutil.rmtree(SYNTHESIZED_EXECUTORS_DIR / run.name)
                except Exception as _e:  # silent swallow (auto-fixed)
                    log.warning("silent exception in %s: %s", __name__, _e)
                install_error = f"birth tests failed: {test_error}"
                if verbose:
                    print(f"[synth_request] test fallito: {install_error}")

        if progress is not None:
            if install_error:
                progress.update_free(_msg(
                    "MSG_SYNTH_PROGRESS_INSTALL_FAILED",
                    name=run.name, seconds=f"{elapsed_s:.0f}",
                    reason=install_error,
                ))
            else:
                progress.update_free(_msg(
                    "MSG_SYNTH_CANDIDATE_CREATED", name=run.name,
                ))

        return {
            "ok": install_error is None,
            "synthesized": True,
            "installed": False,
            "candidate_created": install_error is None,
            "planner_visible": False,
            "lifecycle": "synthesized" if install_error is None else None,
            "install_error": install_error,
            "proposed_name": run.name,
            "proposal_id": proposal_id,
            "elapsed_s": elapsed_s,
            "message": (
                _msg("MSG_SYNTH_CANDIDATE_CREATED", name=run.name)
                if install_error is None
                else _msg("MSG_SYNTH_FAILED",
                          reason=f"install error: {install_error}")
            ),
        }
    elif run.final_state == "rejected":
        if progress is not None:
            progress.update_free(_msg(
                "MSG_SYNTH_PROGRESS_REJECTED",
                reason=(run.abandon_reason
                        or _msg("MSG_SYNTH_REASON_OUT_OF_VOCAB")),
            ))
        return {
            "ok": False,
            "synthesized": False,
            "rejected": True,
            "elapsed_s": elapsed_s,
            "reason": run.abandon_reason or "out-of-vocabulary",
            "message": _msg("MSG_SYNTH_REJECTED_VOCAB",
                            reason=(run.abandon_reason
                                    or _msg("MSG_SYNTH_REASON_OUT_OF_VOCAB"))),
        }
    else:  # abandoned
        if progress is not None:
            progress.update_free(_msg(
                "MSG_SYNTH_PROGRESS_ABANDONED",
                seconds=f"{elapsed_s:.0f}",
                reason=(run.abandon_reason
                        or _msg("MSG_SYNTH_REASON_STAGE_ERROR")),
            ))
        return {
            "ok": False,
            "synthesized": False,
            "abandoned": True,
            "elapsed_s": elapsed_s,
            "reason": run.abandon_reason or "unknown",
            "message": _msg("MSG_SYNTH_FAILED",
                            reason=(run.abandon_reason
                                    or _msg("MSG_SYNTH_REASON_STAGE_ERROR"))),
        }
