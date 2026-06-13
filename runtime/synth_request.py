"""Builtin tool `request_new_executor` — telos di non-rinuncia.

Quando il pianificatore non trova nel catalog un executor che copre la richiesta
utente, invece di rispondere "non ho il tool", chiama questo tool meta. Il
runtime intercetta la chiamata e attiva la cascata synt (compose → multistage
generate). Ritorna al LLM una observation con l'esito della sintesi.

Pattern parallelo a `scratchpad_read` (vedi `scratchpad.py`): il tool vive nel
runtime, niente manifest su disco, niente subprocess. La firma dell'executor
sintetizzato e l'installazione nel pool restano TODO della sessione UX —
oggi salviamo la proposal e torniamo l'esito al LLM.
"""
from __future__ import annotations

import json
import os
import time

from synt_multistage import run_full as multistage_run_full
from loader import SYNTHESIZED_EXECUTORS_DIR
from sign import sign_executor
from vocab import render_actions_pipe, render_objects_pipe, render_qualifiers_pipe
from messages import get as _msg

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
    """Scrive manifest.toml + <name>.py in SYNTHESIZED_EXECUTORS_DIR/<name>/
    e firma con sign_executor. Idempotente: se la cartella esiste viene
    sovrascritta (oggi e' OK perche' il flusso del turno produce un solo
    install per query).

    PRE-CHECK: il code_text DEVE compilare come Python valido. Se non
    compila, fallisce l'install: meglio rigettare il synth che installare
    un executor con SyntaxError che fallira' sempre a runtime
    (`feedback_no_silent_failure`).
    """
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

    out_dir = SYNTHESIZED_EXECUTORS_DIR / run.name
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
    lines = [
        f'# Manifest synthesized — Metnos synt multistage {time.strftime("%Y-%m-%d")}',
        '',
        'manifest_format = "1.0"',
        '',
        f'name        = "{run.name}"',
        'version     = "0.1.0"',
        f'author      = "synt-multistage <synt@metnos.com>"',
        f'affinity    = {_json.dumps(affinity, ensure_ascii=False)}',
        f'revertible  = {"true" if revertible else "false"}',
    ]
    if reverse_pattern:
        if isinstance(reverse_pattern, list):
            lines.append(f'reverse_pattern = {_json.dumps(reverse_pattern, ensure_ascii=False)}')
        else:
            lines.append(f'reverse_pattern = "{reverse_pattern}"')
    lines.extend([
        'lifecycle   = "active"',
        '',
        '[description]',
        f'{cur_lang} = {_json.dumps(description, ensure_ascii=False)}',
        '',
        '[code]',
        f'files  = ["{code_filename}"]',
        'digest = "sha256:placeholder"',  # sign_executor lo aggiorna
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

    (out_dir / "manifest.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Crea anche manifest.lang_state.json initial con sola entry per la lingua
    # corrente. Il daemon notturno tradurra' nelle altre lingue.
    import hashlib as _hashlib
    def _h(t: str) -> str:
        return "sha256:" + _hashlib.sha256(t.encode("utf-8")).hexdigest()
    lang_state = {
        "description": {
            cur_lang: {
                "version_hash": _h(description),
                "source_lang": None,
                "source_hash": None,
            },
        },
    }
    for arg_name, arg_def in (args_schema.get("properties") or {}).items():
        d = (arg_def or {}).get("description")
        if isinstance(d, str):
            lang_state[f"args.{arg_name}.description"] = {
                cur_lang: {
                    "version_hash": _h(d),
                    "source_lang": None,
                    "source_hash": None,
                },
            }
    (out_dir / "manifest.lang_state.json").write_text(
        _json.dumps(lang_state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    sign_executor(out_dir)
    # Fase 4 i18n (1/5/2026 sera): synt at-gen-time scrive description e
    # affinity in DB i18n. Lingua corrente, fetch_key EN canonical.
    # Sistema single-lang by default (vedi metnos_design_i18n_final.md punto 11).
    try:
        import i18n as _i18n
        cur_lang = _i18n.current_lang()
        desc = run.description if hasattr(run, "description") else None
        if desc:
            _i18n.set(f"{run.name}.description", cur_lang, desc)
        affinity = run.affinity_keywords if hasattr(run, "affinity_keywords") else None
        if affinity:
            import json as _json
            _i18n.set(f"{run.name}.affinity", cur_lang,
                       _json.dumps(affinity, ensure_ascii=False))
    except Exception:
        pass  # non bloccare l'install se DB non disponibile
    return out_dir


SYNTH_REQUEST_TOOL = {
    "type": "function",
    "function": {
        "name": "request_new_executor",
        "description": (
            "USA QUESTO TOOL quando nessuno degli executor disponibili copre la richiesta "
            "utente (es. comprimere/calcolare hash/estrarre da formato non coperto/validare). "
            "Il runtime lancera' la sintesi automatica di un nuovo executor. "
            "NON usare per richieste gia' coperte dai tool esistenti."
        ),
        "parameters": {
            "type": "object",
            "required": ["expected_name", "intent"],
            "properties": {
                "expected_name": {
                    "type": "string",
                    "description": (
                        "Nome canonico atteso dell'executor, formato "
                        "{action}_{object}[_{qualifier}]. Vocabolario azioni: "
                        + render_actions_pipe() + ". Oggetti: "
                        + render_objects_pipe() + ". Qualifier opzionale: "
                        + render_qualifiers_pipe() + "."
                    ),
                },
                "intent": {
                    "type": "string",
                    "description": (
                        "Una-due frasi descrittive di cosa l'executor deve fare, "
                        "incluso input atteso, output atteso, e cosa fa nei casi limite. "
                        "Esempio: 'Comprime un file con gzip e scrive l'archivio in un "
                        "percorso destinazione specifico. Input: paths (lista), dst (path). "
                        "Output: dst path effettivo. Errore se source mancante.'"
                    ),
                },
            },
        },
    },
}


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

    Lancia synt_multistage.run_full sincronamente (~150 s wall). Usa LLMRouter
    per i tier: stage 1-4 con `middle` (procedurale, gemma 4 26B), stage 5 con
    `wise` (creativo+procedurale, gemma 4 26B con think=true). Il provider
    del pianificatore (fast tier) NON e' adatto per la sintesi: qwen3:8b
    fatica con i 5 stage, specialmente stage 5 CODE.
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
        return {"ok": False, "error": "missing expected_name in request_new_executor args"}

    try:
        from loader import load_catalog as _load_catalog
        _cat = _load_catalog(verify=True)
    except Exception:
        _cat = None

    if _cat is not None and expected_name in _cat.executors:
        return {
            "ok": True,
            "synthesized": False,
            "already_in_catalog": True,
            "name": expected_name,
            "expected_name": expected_name,
            "message": (
                f"Executor `{expected_name}` esiste gia' nel catalog. "
                f"NON DEVI rifare la sintesi. CHIAMA `{expected_name}` "
                f"al prossimo step con gli args appropriati."
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
                "message": (
                    f"L'executor canonico per questo intent e' `{canonical}`, "
                    f"non `{expected_name}`. NON DEVI rifare la sintesi. "
                    f"CHIAMA `{canonical}` al prossimo step."
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
                    "message": (
                        f"L'intent (verb={verb_l7}, object={object_l7}) e' gia' "
                        f"coperto dallo skill imported `{primary}`. NON DEVI "
                        f"rifare la sintesi. CHIAMA `{primary}` al prossimo step "
                        f"con gli args appropriati."
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
        "web":  "login_session",  # sessione autenticata HTTP/cookie
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
            "message": (
                f"La query ha binding={_binding}: il tool nativo "
                f"`{_redirect_tool}` lo copre. NON DEVI sintetizzare un "
                f"nuovo executor. CHIAMA `{_redirect_tool}` al prossimo "
                f"step con gli args appropriati per il task originale."
            ),
        }

    PROPOSALS_DIR.mkdir(parents=True, exist_ok=True)

    # Synt usa SEMPRE middle+wise (non il tier del pianificatore). Vedi
    # `metnos_wise_tier_quality_floor` e direttiva 29/4/2026: synt non deve
    # usare fast tier. middle e' aliased da wise se non configurato.
    from llm_router import LLMRouter
    _router = LLMRouter()
    _middle_provider = _router.provider("middle")
    _wise_provider = _router.provider("wise")

    # PoC bench Asse B synt (13/5/2026 sera): env `METNOS_SYNT_BUDGET` permette
    # di iterare sul reasoning_budget di synt (default 1024 = legacy). Valori
    # supportati: "0" (no thinking), "<int>" flat. Determinismo §7.9: env-driven.
    _synt_budget_env = os.environ.get("METNOS_SYNT_BUDGET", "").strip()

    def _synt_budget_kwargs(default_budget: int = 1024) -> dict:
        """Costruisce kwargs `think`/`reasoning_budget` da `METNOS_SYNT_BUDGET`.
        - "0" -> think=False (no thinking budget, latenza minima).
        - "<int>" -> think=True + reasoning_budget=<int>.
        - "" (default) -> think=True + reasoning_budget=default_budget (legacy).
        """
        if _synt_budget_env == "0":
            return {"think": False}
        if _synt_budget_env.isdigit() and int(_synt_budget_env) > 0:
            return {"think": True, "reasoning_budget": int(_synt_budget_env)}
        return {"think": True, "reasoning_budget": default_budget}

    def _llm_middle(system, user, max_tokens=2500, **kwargs):
        t0 = time.time()
        # caller override > budget default (es. stage 6 passa think=False)
        _kw = {**_synt_budget_kwargs(1024), **kwargs}
        r = _middle_provider.chat(system, user, max_tokens=max_tokens,
                                  temperature=0.0, **_kw)
        return {
            "text": r.text or "",
            "in_tokens": r.in_tokens,
            "out_tokens": r.out_tokens,
            "latency_ms": int((time.time() - t0) * 1000),
        }

    def _llm_wise(system, user, max_tokens=5000, **kwargs):
        t0 = time.time()
        # caller override > budget default (es. stage 6 passa think=False)
        _kw = {**_synt_budget_kwargs(1024), **kwargs}
        r = _wise_provider.chat(system, user, max_tokens=max_tokens,
                                temperature=0.0, **_kw)
        return {
            "text": r.text or "",
            "in_tokens": r.in_tokens,
            "out_tokens": r.out_tokens,
            "latency_ms": int((time.time() - t0) * 1000),
        }

    if verbose:
        print(f"[synth_request] starting multistage for expected_name={expected_name!r} intent={intent!r}")

    if progress is not None:
        progress.start(f"Sto costruendo un nuovo strumento: <code>{expected_name}</code>")

    t_start = time.time()
    try:
        run = multistage_run_full(intent, _llm_middle, _llm_wise, progress=progress)
    except Exception as ex:
        if progress is not None:
            progress.update_free(f"sintesi interrotta: {type(ex).__name__}: {ex}")
        return {
            "ok": False,
            "error": f"multistage failed: {type(ex).__name__}: {ex}",
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
        n_tests = len((run.stages[2].output or {}).get("tests") or []) if len(run.stages) >= 3 and run.stages[2].success else None
        tests_part = f" · {n_tests} test verdi" if n_tests else ""

        # Auto-sign + install: scrive manifest+code in SYNTHESIZED_EXECUTORS_DIR
        # e firma con la chiave dell'autore. Il loader vede il nuovo executor al
        # prossimo load_catalog() invocato da agent_runtime.
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
                progress.update_free(f"<code>{run.name}</code> sintetizzato in {elapsed_s:.0f} s ma install fallito: {install_error}")
            else:
                progress.update_free(f"<code>{run.name}</code> pronto · {elapsed_s:.0f} s{tests_part}\n<i>installato e firmato, lo richiamo sui paths originali…</i>")

        return {
            "ok": install_error is None,
            "synthesized": True,
            "installed": install_error is None,
            "install_error": install_error,
            "proposed_name": run.name,
            "proposal_id": proposal_id,
            "elapsed_s": elapsed_s,
            "message": (
                (f"Executor `{run.name}` sintetizzato, firmato e installato "
                 f"in {elapsed_s} s. **AL PROSSIMO STEP CHIAMA `{run.name}` "
                 f"con gli args appropriati per il task originale dell'utente "
                 f"(\"{user_query}\")**: il tool e' ora nel catalog.")
                if install_error is None
                else _msg("MSG_SYNTH_FAILED",
                          reason=f"install error: {install_error}")
            ),
        }
    elif run.final_state == "rejected":
        if progress is not None:
            progress.update_free(f"sintesi rigettata: <i>{run.abandon_reason or 'verbo fuori vocab'}</i>")
        return {
            "ok": False,
            "synthesized": False,
            "rejected": True,
            "elapsed_s": elapsed_s,
            "reason": run.abandon_reason or "out-of-vocabulary",
            "message": _msg("MSG_SYNTH_REJECTED_VOCAB",
                            reason=run.abandon_reason or "verbo out-of-scope"),
        }
    else:  # abandoned
        if progress is not None:
            progress.update_free(f"sintesi interrotta · {elapsed_s:.0f} s\n<i>{run.abandon_reason or 'errore in uno stage'}</i>")
        return {
            "ok": False,
            "synthesized": False,
            "abandoned": True,
            "elapsed_s": elapsed_s,
            "reason": run.abandon_reason or "unknown",
            "message": _msg("MSG_SYNTH_FAILED",
                            reason=run.abandon_reason or "errore in uno stage"),
        }
