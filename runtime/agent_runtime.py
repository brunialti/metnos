#!/usr/bin/env python3
"""
agent_runtime.py — il loop pianificatore (Metnos v1.1 POC).

Decisioni di design applicate (sessione 26/4/2026):
    D1  mode-parametrizzato:
            local  = multistep ReAct (default)
            online = single-shot (rimandato; PoC non ha provider online)
            hybrid = router (rimandato)
    D2  pre-filtrato (bag-of-words v1.1, MiniLM rimandato).
    D3  turno = una richiesta utente.
    D4  vaglio probabilistico, qui stub always-approve. In multistep gira fra step.
    D7  sequenziale.
    D8  in-memory + JSONL append-only.
    D9  niente retry, cap step + cap chiamate (configurabili, default 5/2).
    M1  config + router (in PoC mode hardcoded a local).

Aggiornato dopo ciclo finale POC (26/4):
    - tool-use NATIVO via *Provider.chat_with_tools (no prompt-based JSON parsing)
    - data piping con sintassi {{stepN.field}} (opzione A confermata nel ciclo 12)
    - default planner = llamacpp + modello locale su :8080 (ADR 0146, supersedes
      il default storico qwen3:8b di ADR 0044)
"""
import functools
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import uuid
from llm_provider import OllamaProvider, ProviderError, make_provider_from_spec
from loader import load_catalog, filter_for_visibility, VISIBILITY_COMPOSER
from messages import get as msg
from mnestoma import Mnestoma, build_desired_signature
from prefilter import rank, rank_adaptive
from scratchpad import Scratchpad, SCRATCHPAD_READ_TOOL
from synt import Synt, make_request as synt_make_request
from synth_request import SYNTH_REQUEST_TOOL, handle_synth_request
import location_request as _location_request
import prompt_loader  # ADR 0092: prompt LLM in runtime/prompts/<lang>/
import detection_lexicon as _detlex  # lessici NL traducibili (gemello i18n)
from config import DEFAULT_LANG, DEFAULT_TIMEZONE

# Executor che PRODUCONO un file deliverable (consegna su ogni canale §7.3).
_FILE_PRODUCER_PREFIXES = ("create_", "write_", "render_", "compress_")


def _derive_file_attachments(tool, res: dict) -> list:
    """Sintetizza `attachments` dai path di file scritti da uno step
    file-producer, quando l'executor non li dichiara. Generale (tutta la
    famiglia create_/write_/render_/compress_); kind dedotto dal mime
    (image/* -> gallery, altro -> download chip/documento). §2.6/§7.3."""
    import mimetypes
    from pathlib import Path as _P
    # Segnale di CREAZIONE indipendente dal nome (10/7): un result con
    # `_undo.reverse_pattern=delete_created_paths` dichiara «ho creato questi
    # file» — vale anche per i tool fuori prefisso (get_images_google_photos
    # SCARICA foto: il picker completava senza gallery né indicazione del dove).
    _undo = res.get("_undo") if isinstance(res, dict) else None
    created = []
    if isinstance(_undo, dict) and _undo.get("reverse_pattern") == "delete_created_paths":
        created = [p for p in (_undo.get("paths") or []) if isinstance(p, str)]
    if not isinstance(tool, str) or (
            not tool.startswith(_FILE_PRODUCER_PREFIXES) and not created):
        return []
    paths = list(created)
    for k in ("path", "output_path", "dst"):
        v = res.get(k)
        if isinstance(v, str):
            paths.append(v)
    for k in ("paths", "files"):
        v = res.get(k)
        if isinstance(v, list):
            paths += [x for x in v if isinstance(x, str)]
    for r in (res.get("results") or []):
        if isinstance(r, dict):
            p = r.get("path") or r.get("local_path")
            if isinstance(p, str):
                paths.append(p)
    out, seen = [], set()
    for p in paths:
        if p in seen:
            continue
        seen.add(p)
        try:
            if not _P(p).is_file():
                continue
        except OSError:
            continue
        mime = mimetypes.guess_type(p)[0] or "application/octet-stream"
        out.append({"kind": "image" if mime.startswith("image/") else "file",
                    "path": p, "basename": _P(p).name, "mime": mime})
    return out
from fast_path import try_fast_path

LOCATION_REQUEST_TOOL = {
    "type": "function",
    "function": {
        "name": "request_location_from_user",
        "description": (
            "USA QUESTO TOOL quando hai gia' chiamato get_location come precursor "
            "di una query LOCATION-RELATIVE (con marker prossimita' tipo 'vicino a "
            "me', 'qui', 'intorno', 'near me', 'nearby') e get_location ha ritornato "
            "ok:false con error tipo 'no location received yet'. Il tool rinegozia "
            "con l'utente via canale (Telegram: bottoni 'Invia posizione'/'Annulla' "
            "+ campo testo per indirizzo/CAP/citta'). Il TURNO TERMINA SILENZIOSAMENTE "
            "subito dopo: ritornera' un'observation con awaiting:true e il runtime "
            "soppimera' il final_answer. Quando l'utente risponde, il daemon "
            "rilancera' un nuovo turno con la query originale e get_location avra' "
            "la posizione fresca. NON usare per query con luogo esplicito ('a Roma', "
            "'in Via X') — quelle vanno a find_places diretto senza get_location."
        ),
        "parameters": {
            "type": "object",
            "required": ["goal"],
            "properties": {
                "goal": {
                    "type": "string",
                    "description": (
                        "Verbo+oggetto della query corrente in italiano breve, da "
                        "mostrare all'utente nel prompt 'Mi serve la tua posizione "
                        "per <goal>'. Es. 'trovare la farmacia piu' vicina', "
                        "'cercare ristoranti nei dintorni'."
                    ),
                },
            },
        },
    },
}
from describe_entries import DESCRIBE_ENTRIES_TOOL, handle_describe_entries
from classify_entries import CLASSIFY_ENTRIES_TOOL, handle_classify_entries
from extract_entries import EXTRACT_ENTRIES_TOOL, handle_extract_entries
from store_entries import (
    FIND_ENTRIES_TOOL, WRITE_ENTRIES_TOOL, DELETE_ENTRIES_TOOL,
    handle_find_entries, handle_write_entries, handle_delete_entries)
from compare_entries import COMPARE_ENTRIES_TOOL, handle_compare_entries
from describe_images import handle_describe_images
from recurring_tasks import (
    CREATE_TASKS_TOOL, LIST_TASKS_TOOL,
    DELETE_TASKS_TOOL, READ_TASKS_TOOL,
    SET_TASKS_TOOL, READ_TASKS_HISTORY_TOOL,
    handle_create_tasks, handle_list_tasks,
    handle_delete_tasks, handle_read_tasks,
    handle_set_tasks, handle_read_tasks_history,
)
from skill_admin import (
    handle_list_skills, handle_set_skills,
)
from test_runner import check_hints
from undo import UndoLog
from vaglio import judge, guard_check
import config as _C  # §7.11

TURN_LOG_DIR = _C.PATH_USER_DATA / "turns"
DEFAULT_CAP_STEPS = 30
DEFAULT_CAP_SAME_EXECUTOR = 10
# Cap per-turn per executor non-action (find/get/list/read/classify/filter):
# chiamate >= soglia (anche non consecutive) forzano final_answer (the design guide
# §4.4 estesa, 8/5/2026 notte). Configurabile via env. I verbi action
# (write/move/delete/send/create/set/change) hanno guardie proprie a monte
# (cyclic-call, duplicate, vaglio).
DEFAULT_CAP_MAX_PER_TURN = int(os.environ.get("METNOS_CAP_MAX_PER_TURN", "3") or "3")
SCRATCHPAD_THRESHOLD_BYTES = 4096  # observation oltre questa dimensione vanno in scratchpad


# Scrubbing credenziali nel turn log (ADR 0082, 4/5/2026).
# I pattern si applicano DOPO che il PLANNER ha gia' processato la query
# (le credenziali restano in RAM per il turn). Output jsonl pulito.
# Separatore OBBLIGATORIO ([:=] o spazio) fra chiave e valore: con [:=]?
# opzionale, "C:\\Users\\rober\\..." matchava «User»+«s\\rober\\...» → i path
# Windows nei turn record diventavano `C:\\User<REDACTED:cred>` (falso
# positivo, 6/7). Una coppia user/password REALE ha sempre un separatore.
_CRED_RE = re.compile(
    r"(\bp(?:wd|assword|sw|ass)\s*(?:[:=]\s*|\s+))(\S+)", re.IGNORECASE
)
_USER_RE = re.compile(
    r"(\bu(?:sername|ser|name|tente)\s*(?:[:=]\s*|\s+))(\S+)", re.IGNORECASE
)
_OTP_RE = re.compile(
    r"(\b(?:otp|2fa|one[- ]time code|verification code|"
    r"codice(?: otp| 2fa| di verifica)?)\s*(?:[:=]\s*|\s+))(\S+)",
    re.IGNORECASE,
)


# --- Think budget modulation per planner step (19/5/2026) ------------------
# Pattern A+B (manifest [planning] complexity + verb-of-name fallback).
# Bench del modello locale 19/5/2026 + euristica Roberto:
#  - Exec deterministico: think=False, budget=0.
#  - Binary contestuale: think=True, budget=64-128.
#  - Tool calling pool ≤5: think=True, budget=256.
#  - Tool calling pool 6-15: think=True, budget=512.
#  - Tool calling pool >15: think=True, budget=768.
#  - Code generation (synt stage 5): think=True, budget=1024+.
# NOTA: skip think (False) NON e' implementabile per tool-calling: bench
# 19/5 ha dimostrato che il planner sceglie tool sbagliato senza reasoning
# → loop_break. Quindi la modulazione e' SOLO sul budget.
# Vedi [[feedback_thinking_budget_heuristic]] e
# [[metnos_todo_high_think_per_model]] (validazione cross-model pendente).

# Verbi a bassa complessita' decisionale: tool-calling diretto.
# Le 3 categorie (low/medium/high) derivano dal verbo del name se il
# manifest non dichiara [planning] complexity (Pattern B).
_VERB_COMPLEXITY = {
    # Low: producer/snapshot deterministici, scelta tool ovvia se prefilter ok.
    "get": "low", "read": "low", "list": "low", "find": "low",
    # Medium: filtri/computi/comparazioni (richiedono valutazione predicato).
    "filter": "medium", "sort": "medium", "group": "medium",
    "classify": "medium", "describe": "medium", "compare": "medium",
    "compute": "medium", "order": "medium",
    # High: mutating + extract + creare = rischio + creativita'.
    "write": "high", "move": "high", "delete": "high", "create": "high",
    "send": "high", "change": "high", "extract": "high", "share": "high",
    "set": "high",
}

# Budget tokens per (complexity, pool_size_bucket).
# Euristica Roberto 19/5: tool-call (pattern matching NL→template) ha bisogno
# di thinking ma non troppo. Il budget cresce un po' (ma non troppo) col
# pool size. Scaling moderato — bench 19/5 ha mostrato che budget aggressivi
# bassi causano scelta tool sbagliato (request_new_executor invece di
# write_files allo step 3).
_BUDGET_TABLE = {
    # (complexity, pool_bucket): budget
    ("low",    "small"):  256,   # pool ≤5
    ("low",    "medium"): 320,   # pool 6-15 (+25%)
    ("low",    "large"):  384,   # pool >15 (+50% vs small)
    ("medium", "small"):  320,
    ("medium", "medium"): 384,
    ("medium", "large"):  512,
    ("high",   "small"):  384,
    ("high",   "medium"): 512,
    ("high",   "large"):  640,
}


def _infer_complexity_from_name(name: str) -> str:
    """Pattern B: fallback automatico dal verbo del name. Producer
    (get/read/find/list) → low; filtri/compute → medium; mutating → high.
    """
    verb = name.split("_", 1)[0]
    return _VERB_COMPLEXITY.get(verb, "medium")


def _pool_size_bucket(n: int) -> str:
    if n <= 5:
        return "small"
    if n <= 15:
        return "medium"
    return "large"


def _decide_reasoning_budget(candidates, tools_for_step, step_num, loop_start_step):
    """Determina il reasoning_budget per questo planner call basato su:
    (1) step_num: step 1 forza complexity=medium (cascata multi-step composto
        non e' nota dal prefilter; budget basato solo su top-1 sottostima);
    (2) step 2+: complexity dal prefilter top-1 (manifest [planning] o
        inferred via verb);
    (3) dimensione del pool tools_for_step (small/medium/large).

    Ritorna l'int reasoning_budget per LlamaCppProvider.

    Bench 19/5: step 1 con complexity=low (inferred da producer top-1)
    causava planner cascade sbagliata su query multi-step "scarica e salva"
    (sceglieva read_urls_html invece della pipeline corretta). Step 1 ottiene
    sempre medium + 50% boost. Sostituisce formula dyn legacy step1=768/step2+=256.
    """
    pool_bucket = _pool_size_bucket(len(tools_for_step or []))

    is_step1 = (step_num == loop_start_step)
    if is_step1:
        # Step 1: complexity forzata medium (decisione di cascata multi-step).
        complexity = "medium"
    elif not candidates:
        complexity = "medium"
    else:
        top1 = candidates[0]
        complexity = getattr(top1, "complexity", "") or _infer_complexity_from_name(top1.name)

    base = _BUDGET_TABLE.get((complexity, pool_bucket), 384)

    # Step 1 boost: pool grande, history vuota, decisione di cascata.
    if is_step1:
        base = int(base * 1.5)

    return base


def _scrub_credentials(text: str) -> tuple[str, int]:
    """Sostituisce match di password/username inline con `<REDACTED:cred>`.

    Ritorna (testo_pulito, n_match). Idempotente: re-applicare e' no-op
    perche' `<REDACTED:cred>` non matcha gli stessi pattern.
    """
    if not isinstance(text, str) or not text:
        return text, 0
    n_matches = 0
    def _r(m):
        nonlocal n_matches
        n_matches += 1
        return m.group(1) + "<REDACTED:cred>"
    cleaned = _CRED_RE.sub(_r, text)
    cleaned = _USER_RE.sub(_r, cleaned)
    cleaned = _OTP_RE.sub(_r, cleaned)
    return cleaned, n_matches


# Anti thinking-leak (ADR 0102, 7/5/2026). Il modello locale con think=true a volte
# emette il proprio reasoning interno nel canale `text` invece che nel
# canale `thinking` separato — il final_message dell'utente si riempie di
# righe tipo "Wait, I'll check...", "Actually, I should...", "Let me think".
# Lo scrubber e' deterministico (regex su righe standalone, §7.9):
# rimuove SOLO righe il cui inizio e' un trigger di reasoning, preservando
# substring legittime in mezzo a paragrafi reali (§2.8 no silent failure).
_THINKING_LEAK_RE = re.compile(
    r"^\s*(?:"
    r"Wait\b|Actually\b|Let me\b|I'll\b|I will\b|Hmm\b|"
    r"Looking at\b|One detail:|Final Answer(?:\s+construction)?:|"
    r"Wait,?\s+I(?:'|)ll\b|Wait,?\s+I should\b|"
    r"Now I'll\b|Actually,?\s+I'll\b|So,?\s+the answer\b|Let me think\b|"
    r"I should\b|Rule:\s|Given\b"
    r").*$",
    re.IGNORECASE,
)

# Pattern italiani — meta-permission e self-talk del modello locale con think=true.
# Caso live federvolley (7/5/2026): "(posso provare a cercarli se mi dai il
# via libera)" e "ti suggerisco queste alternative" come list intro.
# Politica chirurgica (the design guide §2.8 / §7.9): rimuoviamo SOLO righe in
# parentesi che chiedono permesso, oppure righe standalone che aprono con
# meta-permission ("se vuoi", "se mi dai il via libera", ...). Mantieni
# substring legittime in mezzo a contenuto reale.

# (a) Riga interamente fra parentesi che chiede permesso.
_LEAK_IT_PAREN_PERMISSION_RE = re.compile(
    r"^\s*\(\s*(?:"
    r"posso provare|posso cercare|posso aiutarti|posso suggerirti|"
    r"posso farlo|posso fare|posso recuperare|posso scaricare|"
    r"se mi dai il via libera|se vuoi|se preferisci|fammi sapere|"
    r"dimmi se|vuoi che (?:lo )?faccia|se ti serve|se hai bisogno"
    r")[^)]*\)\s*\.?\s*$",
    re.IGNORECASE,
)

# (b) Riga standalone che APRE con meta-permission/meta-discourse e
# termina nello stesso periodo (no continuazione su altre frasi).
# Pattern: la riga inizia con uno dei trigger e finisce con `.`/`?`/`!`
# o EOL — l'intera riga e' una richiesta di permesso unica. Se prosegue
# con altri contenuti (es. "se vuoi posso aiutarti, ma prima ..."), NON
# scattare per evitare di mutilare contenuto utile.
_LEAK_IT_STANDALONE_RE = re.compile(
    r"^\s*(?:"
    r"se mi dai il via libera|se vuoi posso|fammi sapere se|"
    r"dimmi se vuoi|vuoi che (?:lo )?faccia|"
    r"posso provare a|posso cercare|posso aiutarti|posso suggerirti"
    r")\b[^.?!,;]*[.?!]?\s*$",
    re.IGNORECASE,
)


# Pattern di leak runtime-internal (§2.8 guard): messaggi destinati al
# PLANNER LLM (system messages del runtime) che il LLM a volte copia
# nel final_answer.message. Detection deterministica §7.9.
_RUNTIME_INTERNAL_LEAK_RE = re.compile(
    r"(DUPLICATE_CALL:|FORMULA LA FINAL_ANSWER|"
    r"FORMULATE (?:THE )?FINAL_ANSWER|"
    r"^validation failed:|^vaglio rifiuta:|"
    r"consecutive_blocked|auto_final_on_duplicate|"
    r"cap_same_executor|VECTORIAL_VIOLATION|"
    r"synth_request_blocked_by|requires one of \[|"
    # Synth rejection messages (turn live 25/5/2026 bk93uc961):
    # «request_new_executor rejected: candidate '...' copre la query
    # (jaccard 1.00). Riusalo invece di sintetizzare.» — system msg
    # destinato al PLANNER, non all'utente.
    r"request_new_executor rejected|jaccard \d|"
    r"Riusalo invece di sintetiz|"
    r"Reuse it instead of synthesiz|"
    r"candidate '[^']+' copre la query|"
    r"candidate '[^']+' covers the query)",
    re.IGNORECASE | re.MULTILINE,
)


def _has_runtime_internal_leak(text: str) -> bool:
    if not text or not isinstance(text, str):
        return False
    return bool(_RUNTIME_INTERNAL_LEAK_RE.search(text))


# Set di `step.error` "meta" del runtime: indicano che il step e' stato
# bloccato dal guard (duplicate_call, cap_same_executor, ecc.), NON che
# l'executor stesso abbia prodotto un error semantico. Quando cerchiamo
# il "vero" error per il final_message dobbiamo SKIPPARLI e risalire al
# primo step con error sostanziale.
_META_STEP_ERRORS = frozenset({
    "duplicate_call_blocked",
    "auto_final_on_duplicate",
    "auto_final_on_duplicate_fail",
    "anti_vectorial_blocked",
    "inline_data_rejected",
    "malformed_reference",
})


def _is_meta_step(s) -> bool:
    """True se lo step e' un guard runtime, non un fail executor reale."""
    if s is None:
        return False
    _step_err = (getattr(s, "error", "") or "").strip()
    if _step_err in _META_STEP_ERRORS:
        return True
    if _step_err.startswith("cap_same_executor"):
        return True
    _obs = getattr(s, "result", None)
    if isinstance(_obs, dict) and _obs.get("_duplicate") is True:
        return True
    return False


def _detect_unfulfilled_mutating_intent(log) -> str:
    """Detection §4.3 + §2.8 (25/5/2026): intent utente mutating ma
    nessuno step l'ha eseguito con successo → l'azione e' pendente,
    il final_message attuale e' fuorviante.

    Due fonti di intent mutating (entrambe deterministiche §7.9):
      1. `log.intent_verb` da intent_extractor LLM (se verb in
         DESTRUCTIVE_VERBS, l'intent e' mutating per costruzione).
      2. step CHIAMATI con `chosen_tool` che inizia con verbo mutating
         (il PLANNER ha mappato la query a un tool mutating).

    Verb pendente se:
      - intent.verb in DESTRUCTIVE_VERBS, nessuno step con quel verb ok=True
        OPPURE
      - ALCUN step mutating chiamato senza ok=True+count>0.
    """
    try:
        from vocab import DESTRUCTIVE_VERBS
    except Exception:
        return ""

    intent_verb = (getattr(log, "intent_verb", "") or "").strip()
    intent_is_mutating = intent_verb in DESTRUCTIVE_VERBS

    # §dominio + compound rule (ea1ba7e): un `send` SENZA destinatario
    # esplicito ("mandami il riassunto", "mandami in chat") NON e' outbound —
    # e' una richiesta di risposta in chat, gia' soddisfatta da
    # describe_entries. Riusa lo STESSO predicato del compound_decomposer
    # (single source of truth con il routing send->chat) cosi' la honesty
    # guard §2.8 non marca "send non completata" un turno che ha
    # correttamente risposto in chat (bug live 1/6/2026: la decomposizione
    # find->classify->filter->describe era giusta, ma il final_message
    # veniva sovrascritto con "L'azione send non e' stata completata").
    if intent_verb == "send":
        try:
            from compound_decomposer import _send_has_explicit_recipient
            if not _send_has_explicit_recipient(
                    getattr(log, "user_query", "") or ""):
                intent_is_mutating = False
        except Exception:
            pass

    # Cerca step chiamati con verbo mutating + esito. `ok=True` (qualunque
    # ok_count, anche 0) significa azione TENTATA correttamente — count=0
    # e' un esito LEGITTIMO (es. filter ha selezionato 0 spam, move ha 0
    # target da spostare, l'esito e' onesto). Solo `ok=False` o nessuno
    # step del verbo richiesto → pending.
    pending_from_steps = ""
    intent_verb_executed = False
    for s in getattr(log, "steps", []) or []:
        if not s.chosen_tool:
            continue
        step_verb = s.chosen_tool.split("_", 1)[0]
        if step_verb not in DESTRUCTIVE_VERBS:
            continue
        _obs = s.result if isinstance(s.result, dict) else None
        # §2.8 esito PARZIALE = azione AVVENUTA (bug live 8025922, 6/7:
        # delete di 456 con il solo desktop.ini rifiutato → ok=False ma
        # ok_count=455; dichiarare «non completata» era falso — la notice
        # MSG_MUTATE_PARTIAL a valle riporta già il fallito). Stesso
        # criterio "mutated" di _undo_done.
        _fulfilled = isinstance(_obs, dict) and (
            _obs.get("ok") is True or bool(_obs.get("ok_count"))
            or bool(_obs.get("results")))
        if _fulfilled:
            if intent_is_mutating and step_verb == intent_verb:
                return ""
            if not intent_is_mutating:
                return ""
            intent_verb_executed = True
            continue
        # ok=False o ok=None → step non completato successo
        pending_from_steps = step_verb

    if intent_is_mutating and not intent_verb_executed:
        return intent_verb
    if pending_from_steps:
        return pending_from_steps
    return ""


def _compose_honest_from_last_error(log) -> str:
    """Compose messaggio onesto user-facing dall'ultimo step ok=False.

    Salta step "meta" del runtime (duplicate_call_blocked, cap_same, ecc.)
    e risale al VERO step con error semantico. Riusa il path priority
    error-first dell'invariante TurnLog.write (`MSG_VALIDATION_LOOP_FINAL`
    o `MSG_FINAL_FALLBACK_FROM_ERROR`). Fallback MSG_FINAL_FALLBACK_GENERIC.
    """
    for _s in reversed(getattr(log, "steps", []) or []):
        if _is_meta_step(_s):
            continue
        _obs = _s.result if isinstance(_s.result, dict) else {}
        if not _obs:
            continue
        if _s.chosen_tool == "final_answer":
            continue
        if _obs.get("ok") is False:
            # §2.8: `final_message_hint` = messaggio user-facing ESPLICITO
            # dell'executor (gia' i18n, es. "Nessuna persona 'X' nel registro").
            # Ha priorita' sull'`error` grezzo (spesso un codice tipo
            # "unknown_name") e sul fallback generico.
            _hint = _obs.get("final_message_hint")
            if isinstance(_hint, str) and _hint.strip():
                return _hint.strip()
            _err = _obs.get("error") or ""
            _failed = _obs.get("failed") or []
            if not _err and isinstance(_failed, list) and _failed:
                _err = ", ".join(
                    str((f or {}).get("error", "")).strip()
                    for f in _failed
                    if isinstance(f, dict) and f.get("error")
                )
            _vfails = _obs.get("validation_failures") or []
            if _vfails and isinstance(_vfails, list):
                try:
                    return msg(
                        "MSG_VALIDATION_LOOP_FINAL",
                        tool=_s.chosen_tool or "",
                        fails="; ".join(str(v) for v in _vfails),
                    )
                except Exception:
                    pass
            if _err:
                # Scrub leak runtime-internal dall'error stesso §2.8:
                # se l'error contiene marker runtime (request_new_executor
                # rejected, DUPLICATE_CALL, jaccard, ...) emettere generic
                # fallback invece di propagare il leak nel template.
                if _has_runtime_internal_leak(str(_err)):
                    continue  # cerca step precedente
                try:
                    return msg(
                        "MSG_FINAL_FALLBACK_FROM_ERROR",
                        tool=_s.chosen_tool or "",
                        error=str(_err).strip(),
                    )
                except Exception:
                    return f"{_s.chosen_tool}: {_err}"
    try:
        return msg("MSG_FINAL_FALLBACK_GENERIC")
    except Exception:
        return "Non sono riuscito a produrre un esito. Riformula la richiesta."


def _scrub_thinking_leak(text):
    """Rimuove pattern di reasoning leak da response PLANNER.

    Il modello locale con think=true a volte emette thinking nel canale text invece
    che nel canale thinking separato. Pattern rimossi:

    - EN: righe (standalone) che iniziano con marker di reasoning interno
      tipo "Wait, ", "Actually, ", "Let me ", "I'll check", "Hmm, ",
      "Looking at", "Final Answer:", "One detail:", "Rule: ".
    - IT (chirurgico, ADR estensione 7/5/2026): righe ENTIRE in parentesi
      che chiedono permesso ("(posso provare a ... se mi dai il via libera)")
      e righe standalone meta-permission ("se vuoi posso ...", "fammi
      sapere se ..."). NON tocca "Riassumendo: ..." o "Ti suggerisco ..."
      in mezzo a contenuto perche' possono essere legittimi (es. l'utente
      ha chiesto un riassunto).

    Sostringhe in mezzo a paragrafi legittimi sono PRESERVATE (es. il
    documento citato dice "Wait, this is important" rimane).

    Idempotente: re-applicare e' no-op (le righe leak sono gia' rimosse).
    Ritorna stringa pulita; input non-stringa o vuoto torna invariato.
    """
    if not text or not isinstance(text, str):
        return text
    cleaned = []
    for line in text.split("\n"):
        if _THINKING_LEAK_RE.match(line):
            continue
        if _LEAK_IT_PAREN_PERMISSION_RE.match(line):
            continue
        if _LEAK_IT_STANDALONE_RE.match(line):
            continue
        cleaned.append(line)
    out = "\n".join(cleaned).strip()
    # Collapse multi-blank-lines residue dopo rimozioni.
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out


# Markup HTML document-level: se compare nel final_message e' SEMPRE un leak
# (observation/fetch grezzo trapelato), mai contenuto legittimo. Il messaggio
# canonico e' markdown per i canali (autolink `<url>` e inline-code restano
# intatti finche' non c'e' un marker di documento).
_RE_HTML_DOC_MARKER = re.compile(
    r"<!DOCTYPE|<html[\s>]|</html>|<head[\s>]|<body[\s>]|<script[\s>]|<style[\s>]",
    re.IGNORECASE,
)
_RE_HTML_SCRIPT_STYLE = re.compile(
    r"<(script|style)\b[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
_RE_HTML_ANY_TAG = re.compile(r"<[^>]+>")


def _scrub_raw_html_leak(text):
    """Backstop (§2.8/§7.9) applicato al final_message degli answer-turn (dopo
    i formatter): se contiene markup HTML document-level (fetch/observation
    grezza trapelata, es. bug "azione schedulata invia messaggio errato"),
    rimuove script/style + TUTTI i tag e normalizza. Le sorgenti note
    (terminator_metis, format_search_results, observation builder) sanificano
    a monte; questo e' la rete di sicurezza finale. CONSERVATIVO: agisce solo
    in presenza di un marker di documento, cosi' markdown e autolink `<url>`
    legittimi restano intatti. Idempotente."""
    if not text or not isinstance(text, str):
        return text
    if not _RE_HTML_DOC_MARKER.search(text):
        return text
    import html as _html
    cleaned = _RE_HTML_SCRIPT_STYLE.sub(" ", text)
    cleaned = _RE_HTML_ANY_TAG.sub(" ", cleaned)
    cleaned = _html.unescape(cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _scrub_args_recursive(node, total: list[int]) -> object:
    """Scrub ricorsivo su dict/list/str. Mutates total[0] con il count."""
    if isinstance(node, str):
        cleaned, n = _scrub_credentials(node)
        total[0] += n
        return cleaned
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            # Anche scrubbing diretto dei value se la chiave dice "password"
            if isinstance(k, str) and k.lower() in (
                "password", "pwd", "psw", "pass", "otp", "one_time_code",
                "verification_code", "value_ref",
            ) and isinstance(v, str) and v:
                total[0] += 1
                out[k] = "<REDACTED:cred>"
            else:
                out[k] = _scrub_args_recursive(v, total)
        return out
    if isinstance(node, list):
        return [_scrub_args_recursive(x, total) for x in node]
    return node


# ── Estrazione credenziali dalla query (Strato 1 — ADR 0089, 4/5/2026) ──
# Quando l'utente scrive "monta share \\\\nas\\Public user roberto pwd hunter2"
# il runtime estrae user/pwd e li salva cifrati prima che la query raggiunga
# il PLANNER. La query passata al pianificatore ha le creds rimpiazzate da
# `<REDACTED:cred:domain>` cosi' il LLM non le vede mai. Il dominio viene
# derivato deterministicamente dal contesto della query (host CIFS, URL web,
# host SSH) — codice deterministico > LLM (the design guide §7.9).
#
# Pattern coperti: etichette traducibili da detection_lexicon, in qualunque
# ordine, con punteggiatura/connettori liberi e valori anche fra virgolette.
#
# Riconoscimento del dominio:
#   - share CIFS:  "//192.0.2.20/Public" / "\\\\host.local\\share" → cifs_<host>
#   - URL/host web: "https://webmail.example.com" → host esatto
#   - ssh:          "ssh roberto@host.local"        → ssh_<host>
#   - hint testuale: "share|smb|cifs|nas" → cifs ; "login|portale|sito" → web ;
#                    "ssh" → ssh.
#   - fallback: "generic" se nessun host derivabile (caso degenere).

# Pattern di estrazione: cattura coppie user/pwd in una passata sola.
# Usiamo finditer per ricavare gli offset esatti (per scrubbing offsets).
# Le keyword sono ordinate per lunghezza (LONGEST FIRST) per evitare match
# parziali tipo "user" che taglia "username" ⇒ value="name:carlo".
_CREDENTIAL_LABEL_FALLBACK = {
    "username": ["username", "user id", "userid", "utente", "nome utente",
                 "user", "usr", "email", "e-mail", "login"],
    "password": ["password", "passwd", "passphrase", "pwd", "psw", "pass"],
}
_CREDENTIAL_CONNECTOR_FALLBACK = ["e", "con", "and", "with"]
_CREDENTIAL_VALUE = r'(?:"[^"\r\n]+"|\'[^\'\r\n]+\'|[^\s,;]+)'


def _forms_pattern(forms: list[str]) -> str:
    """Alternativa regex senza capture, longest-first e whitespace flessibile."""
    escaped = []
    for form in sorted(set(forms), key=len, reverse=True):
        escaped.append(re.escape(form).replace(r"\ ", r"\s+"))
    return r"(?<!\w)(?:" + "|".join(escaped) + r")(?!\w)"


@functools.lru_cache(maxsize=8)
def _credential_pair_patterns(lang: str) -> tuple[re.Pattern, re.Pattern]:
    labels = _detlex.mapping("credentials.field_label")
    users = labels.get("username") or _CREDENTIAL_LABEL_FALLBACK["username"]
    passwords = labels.get("password") or _CREDENTIAL_LABEL_FALLBACK["password"]
    connectors = (_detlex.forms("credentials.pair_connector")
                  or _CREDENTIAL_CONNECTOR_FALLBACK)
    user_pattern = _forms_pattern(users)
    password_pattern = _forms_pattern(passwords)
    connector_pattern = _forms_pattern(connectors)
    separator = (
        rf"(?:\s*[,;/|]\s*|\s+{connector_pattern}\s+|\s+)"
    )
    user_then_password = re.compile(
        rf"({user_pattern})\s*[:=]?\s*({_CREDENTIAL_VALUE})"
        rf"(?:{separator})*"
        rf"({password_pattern})\s*[:=]?\s*({_CREDENTIAL_VALUE})",
        re.IGNORECASE,
    )
    password_then_user = re.compile(
        rf"({password_pattern})\s*[:=]?\s*({_CREDENTIAL_VALUE})"
        rf"(?:{separator})*"
        rf"({user_pattern})\s*[:=]?\s*({_CREDENTIAL_VALUE})",
        re.IGNORECASE,
    )
    return user_then_password, password_then_user


def _clean_credential_value(value: str) -> str:
    value = value.strip()
    if (len(value) >= 2 and value[0] == value[-1]
            and value[0] in ("'", '"')):
        return value[1:-1]
    return value.rstrip(",;.")

# Riconoscimento host CIFS: //host/share oppure \\host\share (Windows-style).
# Tolleriamo doppia barra invertita escapata in stringa Telegram.
_CIFS_SHARE_RE = re.compile(
    r"(?:\\\\|//)([A-Za-z0-9._-]+)(?:\\|/)([A-Za-z0-9._/\\-]+)"
)
# URL / host web
_URL_HOST_RE = re.compile(
    r"https?://([A-Za-z0-9._-]+)(/[^\s]*)?", re.IGNORECASE
)
# host bare (FQDN o IP) — usato solo quando hint = ssh/login/sito
_BARE_HOST_RE = re.compile(
    r"\b((?:\d{1,3}\.){3}\d{1,3}|(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,})\b"
)
# Hint del binding: parole-spia per CIFS/web/ssh.
# Prioritizziamo discriminatori inequivocabili (ssh come comando, https://, //)
# rispetto a parole "ambigue" (nas, share) che possono comparire anche in
# query che parlano di accesso ssh a un NAS.
_BINDING_STRONG = (
    ("ssh",  (r"\bssh\s", r"\bscp\s", r"\bsftp\s", r"\bssh\b$")),
    ("web",  (r"https?://",)),
    ("cifs", (r"//\S+/", r"\\\\\S+",)),
)
_BINDING_WEAK = (
    ("cifs", ("share", "smb", "cifs", "nas", "monta", "mount", "samba")),
    ("ssh",  ("ssh", "scp", "sftp")),
    ("web",  ("login", "sito", "portale", "registro", "banca",
              "browser", "webmail")),
)


def detect_binding(query: str) -> str:
    """Ritorna 'cifs' | 'ssh' | 'web' | 'generic' in base ad hint linguistici.

    Priorita' a discriminatori inequivocabili (regex) prima dei keyword.
    Public: importato anche da `synth_request` per il binding short-circuit
    (un binding coperto da tool builtin invalida la cascata synth).
    """
    qlc = query.lower()
    for binding, patterns in _BINDING_STRONG:
        for p in patterns:
            if re.search(p, qlc):
                return binding
    for binding, kws in _BINDING_WEAK:
        if any(k in qlc for k in kws):
            return binding
    return "generic"


def _detect_host(query: str, binding: str) -> tuple[str, dict]:
    """Ritorna (host, context) deterministicamente. context include share path
    quando applicabile per uso da parte del sudoer al fire time.
    """
    ctx: dict = {"binding": binding}
    if binding == "cifs":
        m = _CIFS_SHARE_RE.search(query)
        if m:
            host = m.group(1).lower()
            ctx["host"] = host
            ctx["share"] = m.group(2).replace("\\", "/")
            return host, ctx
    if binding == "web":
        m = _URL_HOST_RE.search(query)
        if m:
            host = m.group(1).lower()
            ctx["host"] = host
            return host, ctx
    # Fallback bare host (vale anche per ssh)
    m = _BARE_HOST_RE.search(query)
    if m:
        host = m.group(1).lower()
        ctx["host"] = host
        return host, ctx
    return "", ctx


def extract_credentials(query: str) -> list[dict]:
    """Estrae coppie user/pwd dalla query con regex deterministici (ADR 0089).

    Ritorna lista (puo' essere vuota) di dict con shape:
        {
          "domain":   "cifs_192.0.2.20",   # chiave canonica per credentials.store
          "username": "roberto",
          "password": "hunter2",
          "context":  {"binding": "cifs", "host": "...", "share": "..."},
          "scrub_spans": [(start, end), ...],   # offsets nel testo originale
        }

    Pattern accettati: "user X pwd Y", "utente X password Y", "user=X pass=Y",
    "username:X password:Y", "nome utente X passw Y". Case-insensitive.
    Il dominio e' derivato dall'host nella query (CIFS share, URL web, host
    bare) + binding inferito dalle parole-spia (share/cifs/nas → cifs,
    login/portale → web, ssh → ssh, fallback "generic").

    Non solleva eccezioni: query senza match → lista vuota.
    """
    if not isinstance(query, str) or not query.strip():
        return []
    user_then_password, password_then_user = _credential_pair_patterns(
        _detlex.current_lang())
    user_matches = list(user_then_password.finditer(query))
    password_matches = list(password_then_user.finditer(query))
    if not user_matches and not password_matches:
        return []

    binding = detect_binding(query)
    host, ctx = _detect_host(query, binding)
    # Una coppia credenziale + FQDN senza indicatori CIFS/SSH e' un binding
    # web naturale ("credenziali di telepass.com"), non ``host_*``.
    if binding == "generic" and host:
        binding = "web"
        ctx["binding"] = binding
    domain_prefix = binding if binding != "generic" else "host"
    if binding == "web" and host:
        domain = host
    elif host:
        domain = f"{domain_prefix}_{host}"
    else:
        domain = f"{domain_prefix}_unknown"

    out: list[dict] = []
    seen_spans: set[tuple[int, int]] = set()

    def _add(user: str, pwd: str, spans: list[tuple[int, int]]) -> None:
        # Skip vuoti / placeholder gia' redacted
        if not user or not pwd:
            return
        if user.startswith("<REDACTED") or pwd.startswith("<REDACTED"):
            return
        for s in spans:
            if s in seen_spans:
                return
        for s in spans:
            seen_spans.add(s)
        out.append({
            "domain": domain,
            "username": user,
            "password": pwd,
            "context": dict(ctx),
            "scrub_spans": list(spans),
        })

    for m in user_matches:
        # group 2 = user value, group 4 = pwd value
        user_val = _clean_credential_value(m.group(2))
        pwd_val = _clean_credential_value(m.group(4))
        # Scrub spans: solo i VALUE, non le keyword (per leggibilita').
        spans = [(m.start(2), m.end(2)), (m.start(4), m.end(4))]
        _add(user_val, pwd_val, spans)
    for m in password_matches:
        pwd_val = _clean_credential_value(m.group(2))
        user_val = _clean_credential_value(m.group(4))
        spans = [(m.start(2), m.end(2)), (m.start(4), m.end(4))]
        _add(user_val, pwd_val, spans)

    return out


def _redact_spans(text: str, spans: list[tuple[int, int]], domain: str) -> str:
    """Sostituisce i tratti span con `<REDACTED:cred:domain>`. Preserva offset
    riducendo gli span man mano. Lavora su una copia, niente mutazione in-place.
    """
    if not spans:
        return text
    placeholder = f"<REDACTED:cred:{domain}>"
    # Ordina per start desc cosi' le sostituzioni successive non spostano
    # gli span ancora da processare.
    sorted_spans = sorted(set(spans), key=lambda s: s[0], reverse=True)
    out = text
    for start, end in sorted_spans:
        if 0 <= start < end <= len(out):
            out = out[:start] + placeholder + out[end:]
    return out


def apply_credentials_extraction(query: str) -> tuple[str, list[dict]]:
    """Strato 1 del flow UX credenziali (ADR 0089).

    1. Estrae credenziali dalla query con `extract_credentials`.
    2. Per ciascuna: salva cifrate via `credentials.store` (ADR 0082).
    3. Sostituisce i value nel testo con `<REDACTED:cred:domain>`.
    4. Ritorna (query_redacted, list_di_creds_metadata) — la metadata
       contiene solo domain + context (NON username/password) ed e'
       sicura da iniettare nel context del PLANNER.
    """
    creds = extract_credentials(query or "")
    if not creds:
        return query, []
    try:
        import credentials  # type: ignore
    except ImportError:
        return query, []
    redacted = query
    safe_meta: list[dict] = []
    # Aggrega tutti gli scrub span (l'estrazione produce piu' record
    # con stesso domain — gli span vanno comunque rimpiazzati tutti).
    all_spans: list[tuple[int, int]] = []
    for c in creds:
        for s in c.get("scrub_spans") or []:
            all_spans.append(tuple(s))
        try:
            credentials.store(
                c["domain"],
                {
                    "username": c["username"],
                    "password": c["password"],
                    # Authority is selected in a secret-free form before the
                    # binding can be used unattended.
                    "scopes": [],
                    **{k: v for k, v in (c.get("context") or {}).items()
                       if k in ("binding", "host", "share", "workgroup", "port")},
                },
            )
        except (ValueError, OSError, FileNotFoundError):
            # Storage fallito: log lo stato ma scrubbamo lo stesso il testo
            # (priorita': non leakare la pwd anche se non riusciamo a salvarla).
            continue
        safe_meta.append({
            "domain": c["domain"],
            "context": dict(c.get("context") or {}),
            "mandate_pending": True,
        })
    if all_spans:
        # Per il redact uso il primo dominio come tag, ma ogni run cattura
        # un solo dominio per query nella pratica (un solo host).
        primary_domain = creds[0]["domain"]
        redacted = _redact_spans(query, all_spans, primary_domain)
    return redacted, safe_meta


def _is_credentials_store_only_intent(intent) -> bool:
    """True solo per un intento semantico puro ``set credentials``.

    La decisione usa l'intent canonico, non forme testuali: le frasi naturali
    restano aperte e un compound ``salva e fai login`` continua nel motore.
    """
    actions = list(getattr(intent, "actions", None) or [])
    if not actions:
        actions = [{"verb": getattr(intent, "verb", ""),
                    "object": getattr(intent, "object", "")}]
    return bool(actions) and all(
        isinstance(action, dict)
        and (action.get("verb") or "").lower() == "set"
        and (action.get("object") or "").lower() == "credentials"
        for action in actions
    )



# --- Prompt + tools rendering ---------------------------------------------
# PLANNER prompt è in runtime/prompts/<METNOS_LANG>/planner.j2 (ADR 0092).
# Caricato via prompt_loader.get("planner", **vars) in run_turn().

# Inietta il vocabolario centralizzato (vocab.py) nel prompt — single source.
from vocab import (
    render_actions_inline as _vocab_actions,
    render_objects_inline as _vocab_objects,
    render_qualifiers_inline as _vocab_qualifiers,
)
from logging_setup import get_logger
log = get_logger(__name__)
# Alias modulo (immutabile, mai shadowed). Usato dai gestori silent-swallow
# dentro run_turn(), dove `log` e' shadowed dall'istanza TurnLog locale.
_LOG = log


def _render_project_paths_block() -> str:
    """Carica `runtime/project_paths.json` e ritorna un blocco testuale per
    il prompt PLANNER. Formato: una riga per progetto con name → code_root.

    Bug fix 4/5/2026 (ADR 0079): l'utente puo' chiamare un progetto col suo
    nome ('metnos', 'giorgio2', ...) come oggetto della query. Senza questo
    blocco il PLANNER interpretava il nome come pattern di filename e
    chiamava find_files(pattern="*metnos*") invece di usare il code_root
    canonico. Letto a load-time del modulo: gli aggiornamenti al JSON si
    rifletteranno al prossimo restart del runtime/daemon.
    """
    cfg_path = Path(__file__).resolve().parent / "project_paths.json"
    if not cfg_path.exists():
        return '  (nessun progetto configurato in runtime/project_paths.json)'
    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as ex:
        log.warning("project_paths.json read failed: %s", ex)
        return '  (errore lettura runtime/project_paths.json)'
    if not isinstance(data, dict) or not data:
        return '  (nessun progetto configurato)'
    lines = []
    for proj, meta in data.items():
        if not isinstance(meta, dict):
            continue
        # Supporto sia codebase (code_root) sia collezioni dati (data_root):
        # se entrambi mancano, "?". Il PLANNER vede comunque description.
        root = meta.get("code_root") or meta.get("data_root") or "?"
        kind = "codebase" if meta.get("code_root") else "collezione"
        desc = meta.get("description") or ""
        lines.append(f'  "{proj}" = {kind} in {root}'
                      + (f' ({desc})' if desc else ''))
    return "\n".join(lines) if lines else '  (nessun progetto configurato)'


def _render_users_known_block() -> str:
    """Carica `users.list_users()` e ritorna un blocco testuale per il prompt
    PLANNER. Formato: una riga per user con name (role, owner, autonomy,
    canali verificati). Multi-user (4/5/2026, ADR 0083): permette al
    pianificatore di risolvere "manda a Lucia" → `to_user="lucia"` invece
    di indovinare chat_id letterali. Letto a load-time: aggiornare l'elenco
    richiede restart del runtime/daemon (mantiene il prompt deterministico
    durante la pianificazione di un turno).
    """
    try:
        import users as _users
        _users.init_db()
        rows = _users.list_users()
    except Exception as ex:
        log.warning("users.list_users failed: %s", ex)
        return '  (servizio utenti non disponibile)'
    if not rows:
        return '  (nessun utente registrato)'
    out = []
    for u in rows:
        try:
            chans = _users.list_channels(u["id"])
        except Exception:
            chans = []
        verified = [c["channel"] for c in chans if c.get("verified_at")]
        pending = [c["channel"] for c in chans
                   if not c.get("verified_at") and c.get("pairing_token")]
        suffix_chans = []
        if verified:
            suffix_chans.append(", ".join(f"{c} OK" for c in verified))
        if pending:
            suffix_chans.append(", ".join(f"{c} pending" for c in pending))
        suffix = " — " + "; ".join(suffix_chans) if suffix_chans else ""
        owner = ""
        if u.get("owner_user_id"):
            o = _users.get_user(u["owner_user_id"])
            if o:
                owner = f", owner={o['name']}"
        out.append(
            f'  - {u["name"]} ({u["role"]}{owner}, '
            f'autonomy={u["autonomy_level"]}){suffix}'
        )
    return "\n".join(out)


def _render_telos_block(lang: str) -> str:
    """Wrap thin di `telos_loader.render_planner_block(lang)`. Fase 4
    wire-in (22/5/2026): se TELOS.md mancante o vuoto ritorna stringa
    vuota; il template `_footer.j2` ha `{% if telos_block %}` quindi
    nessuna sezione spuria appare. Hot-reload via `telos_loader` cache
    su mtime (idempotente, thread-safe).
    """
    try:
        import telos_loader
        return telos_loader.render_planner_block(lang)
    except Exception as ex:
        log.warning("telos_loader.render_planner_block failed: %s", ex)
        return ""


def _render_rejected_pipelines_block(user_query: str, lang: str) -> str:
    """Negative examples per il planner (E.2/E.3, 22/5/2026).

    Strato 1 (1 ✗): wording soft "evita queste pipeline".
    Strato 2 (>=2 ✗ consecutive): HARD CONSTRAINT con escalation —
    "rifiuto definitivo, considera request_new_executor o consult_frontier
    o final_answer onesto 'non ho strumenti adatti'".

    Strato 3 (UI escalation post-3 ✗, task #30, 24/5/2026): handled
    upstream da `_orchestrate_strato3_escalation` in `run_turn` (early
    exit prima del PLANNER loop). Dialog 4-choice (synth/frontier/
    reformulate/abandon) via `orchestrate_needs_inputs`.
    """
    if not user_query:
        return ""
    try:
        from turn_feedback import (
            rejected_pipelines_for_query, count_consecutive_errors_for_query,
        )
        rejected = rejected_pipelines_for_query(user_query)
        consec_errors = count_consecutive_errors_for_query(user_query)
    except Exception as ex:
        log.warning("rejected_pipelines_for_query failed: %s", ex)
        return ""
    if not rejected:
        return ""
    hard = consec_errors >= 2
    if lang == "en":
        if hard:
            header = ("RUNTIME HARD CONSTRAINT — USER REJECTED ALL PIPELINES "
                       "ATTEMPTED ({n} consecutive ✗)").format(n=consec_errors)
            body_suffix = [
                "",
                "DO: try a STRUCTURALLY DIFFERENT approach (different executor "
                "family, or request_new_executor for synthesis, or "
                "consult_frontier for high-stakes reasoning).",
                "DO NOT: produce yet another minor variation of the rejected "
                "pipelines — the user wants a different SHAPE of answer.",
                "OK: if no viable alternative exists, emit final_answer "
                "explicitly stating: \"I don't have a suitable tool for X; "
                "I can try synthesizing one with request_new_executor.\"",
                "ERROR: silently re-run a similar pipeline.",
            ]
        else:
            header = ("PIPELINES ALREADY REJECTED BY USER FOR THIS QUERY "
                       "(do not repeat!)")
            body_suffix = [
                "",
                "DO: pick a DIFFERENT pipeline. DO NOT: replicate those listed.",
                "OK: use an alternative executor, read metadata directly, "
                "or reshape the steps.",
                "ERROR: rebuild the same sequence the user already rejected.",
            ]
        lines = [
            "══════════════════════════════════════════════════════════════════════",
            header,
            "══════════════════════════════════════════════════════════════════════",
            "",
        ]
        for p in rejected[:5]:
            lines.append(f"- {' → '.join(p)}")
        lines += body_suffix
        return "\n".join(lines)
    # IT
    if hard:
        header = ("VINCOLO HARD RUNTIME — UTENTE HA RIFIUTATO TUTTE LE "
                   "PIPELINE TENTATE ({n} ✗ consecutive)").format(n=consec_errors)
        body_suffix = [
            "",
            "DEVI: provare un approccio STRUTTURALMENTE DIVERSO (executor di "
            "famiglia diversa, o request_new_executor per sintesi, o "
            "consult_frontier per ragionamento ad alto rischio).",
            "NON DEVI: produrre l'ennesima variante minore delle pipeline "
            "rifiutate — l'utente vuole una FORMA diversa di risposta.",
            "OK: se nessuna alternativa praticabile esiste, emetti final_answer "
            "esplicitando: \"non ho uno strumento adatto per X; posso "
            "provare a sintetizzarne uno con request_new_executor.\"",
            "ERRORE: rilanciare silenziosamente una pipeline simile.",
        ]
    else:
        header = ("PIPELINE GIA' RIFIUTATE DALL'UTENTE PER QUESTA QUERY "
                   "(non ripetere!)")
        body_suffix = [
            "",
            "DEVI: scegliere una pipeline DIVERSA. NON DEVI: ripetere quelle "
            "elencate sopra.",
            "OK: usare un executor alternativo, leggere direttamente metadata, "
            "o riformulare gli step.",
            "ERRORE: ricostruire la stessa sequence che l'utente ha gia' "
            "bocciato.",
        ]
    lines = [
        "══════════════════════════════════════════════════════════════════════",
        header,
        "══════════════════════════════════════════════════════════════════════",
        "",
    ]
    for p in rejected[:5]:
        lines.append(f"- {' → '.join(p)}")
    lines += body_suffix
    return "\n".join(lines)




_OBS_HISTORY_CHAR_CAP = 8000


def _check_top_k_affinity_jaccard(
    query: str, candidates: list, *, threshold: float = 0.3,
) -> tuple[str, float] | None:
    """Soft-gate B.5 (19/5/2026 v4, #10 prompt PLANNER re-eng):
    ritorna `(tool_name, jaccard_score)` del primo candidate del top-K che
    ha jaccard affinity vs query >= threshold. None se nessuno passa la soglia.

    Usato per rifiutare `request_new_executor` quando il top-K contiene gia'
    un tool con affinity adeguata. Determinismo §7.9.

    Tokenizzazione: lower + split su non-alfanumerico, filtra stop-word
    minimali IT+EN. Affinity del tool: union di `name` (split su _) + termini
    da `affinity` (lista).
    """
    import re as _re
    if not query or not candidates:
        return None
    _stop = {"il","la","i","gli","le","un","una","di","da","del","della","dei",
             "delle","a","al","alla","ai","alle","in","con","su","per","tra",
             "fra","e","o","ma","che","mi","ci","ti","si","ho","ha","hai",
             "the","a","an","of","to","in","is","it","for","on","with","and",
             "or","but","this","that"}
    q_tokens = {t for t in _re.split(r"[^\w]+", query.lower()) if t and t not in _stop and len(t) >= 3}
    if not q_tokens:
        return None
    # B.5 STRONG MATCH (19/5/2026 v4): se la query contiene un verbo che
    # canonicalizza a un verbo Metnos §2.2 via vocab synonyms (es. "salva"→
    # "write", "cancella"→"delete"), E il top-K ha un tool con nome che
    # inizia con quel verbo canonico, → match forte (score=1.0) immediato.
    # Questo evita che query con molti tokens irrilevanti (es. "salva nota:
    # spesa supermercato 35€") diluiscano la jaccard sotto threshold.
    try:
        from prefilter import detect_canonical_verbs_all, tokenize  # type: ignore
        _qtoks_for_verb = tokenize(query)
        _canonical_verbs = detect_canonical_verbs_all(_qtoks_for_verb) or []
    except Exception:
        _canonical_verbs = []
    if _canonical_verbs:
        for ex in candidates:
            name = getattr(ex, "name", "") or ""
            if not name or name == "request_new_executor":
                continue
            for cv in _canonical_verbs:
                if name.startswith(cv + "_"):
                    return (name, 1.0)
    for ex in candidates:
        name = getattr(ex, "name", "") or ""
        if not name or name == "request_new_executor":
            continue
        aff_terms = getattr(ex, "affinity", None) or []
        if isinstance(aff_terms, dict):
            aff_terms = aff_terms.get("it", []) + aff_terms.get("en", [])
        tool_tokens = set()
        tool_tokens.update(t for t in name.split("_") if len(t) >= 3)
        for term in aff_terms:
            if isinstance(term, str):
                tool_tokens.update(t for t in _re.split(r"[^\w]+", term.lower())
                                    if t and len(t) >= 3 and t not in _stop)
        if not tool_tokens:
            continue
        inter = q_tokens & tool_tokens
        if not inter:
            continue
        # Overlap coefficient: inter / min(|q|, |t|). Insensibile all'asimmetria
        # tool con affinity ricca vs query breve (caso live 11/5 "appuntamenti
        # domani": Jaccard penalizza, overlap coefficient cattura coverage).
        score = len(inter) / max(1, min(len(q_tokens), len(tool_tokens)))
        if score >= threshold:
            return (name, score)
    return None


def _trim_obs_for_history(obs: dict | str, *, cap: int = _OBS_HISTORY_CHAR_CAP) -> str:
    """Serializza una observation in JSON pronto per `history_for_llm` con cap
    sui caratteri. Se sfora, prova prima a rimuovere `body_preview` dalle
    entries (campo pesante, recuperabile da scratchpad_read); se ancora sfora,
    riduce il numero di entries preservando struttura JSON valida; come ultima
    risorsa, fallback al troncamento "stupido" di stringa con marker.

    Niente perdita silenziosa: se rimuove campi/entries lo dichiara via campo
    `_obs_trimmed` con dettaglio."""
    if not isinstance(obs, dict):
        s = json.dumps(obs, ensure_ascii=False) if not isinstance(obs, str) else obs
        return s if len(s) <= cap else s[:cap] + "...[troncato]"

    s = json.dumps(obs, ensure_ascii=False)
    if len(s) <= cap:
        return s

    entries = obs.get("entries")
    if isinstance(entries, list) and entries:
        # Tentativo 1: rimuovi body_preview
        slim_entries = [
            {k: v for k, v in e.items() if k != "body_preview"} if isinstance(e, dict) else e
            for e in entries
        ]
        slim = dict(obs)
        slim["entries"] = slim_entries
        slim["_obs_trimmed"] = "body_preview rimosso (recuperabile via scratchpad_read)"
        s2 = json.dumps(slim, ensure_ascii=False)
        if len(s2) <= cap:
            return s2

        # Tentativo 2: riduci numero di entries proporzionalmente
        n = len(slim_entries)
        keep = max(1, int(n * cap / max(len(s2), 1)))
        slim["entries"] = slim_entries[:keep]
        slim["_obs_trimmed"] = (
            f"body_preview rimosso + entries ridotte a {keep}/{n} "
            f"(piene via scratchpad_read)"
        )
        s3 = json.dumps(slim, ensure_ascii=False)
        if len(s3) <= cap:
            return s3

    # Fallback: troncamento stringa con marker
    return s[:cap] + "...[troncato]"


_FROM_STEP_DESC = (
    "Numero dello step precedente (in questo turno) che ha prodotto la "
    "lista da consumare. Es. se al passo 1 hai chiamato find_files / "
    "read_messages / find_dirs, qui passi from_step=1: il runtime "
    "espande automaticamente le entries dallo scratchpad. NON passare "
    "entries inline: lo schema non lo prevede e il modello non ha visibilita' "
    "sui dati prodotti dagli step precedenti."
)


_WEEKDAY_IT = ["lunedi'", "martedi'", "mercoledi'", "giovedi'",
                "venerdi'", "sabato", "domenica"]
_WEEKDAY_EN = ["Monday", "Tuesday", "Wednesday", "Thursday",
                "Friday", "Saturday", "Sunday"]


def _render_now_vars() -> dict:
    """Restituisce dict con riferimenti temporali correnti per il prompt
    planner footer (Roberto 12/5/2026): today_iso/now_hhmm/weekday_*/tz.
    Iniettato in compose() per evitare step get_now ridondante quando il
    planner deve solo risolvere una data relativa banale. §7.9 deterministico.
    Per orari precisi al secondo o explicit time-of-day request, il planner
    invoca comunque get_now (hint in _footer.j2).
    """
    from datetime import datetime
    try:
        import zoneinfo
        tz = zoneinfo.ZoneInfo(DEFAULT_TIMEZONE)
        now = datetime.now(tz)
    except Exception:
        now = datetime.now()
    wd = now.weekday()
    return {
        "today_iso": now.strftime("%Y-%m-%d"),
        "now_hhmm": now.strftime("%H:%M"),
        "weekday_it": _WEEKDAY_IT[wd],
        "weekday_en": _WEEKDAY_EN[wd],
        "tz": DEFAULT_TIMEZONE,
    }


def planner_facing_schema(schema):
    """Trasforma lo `args_schema` di un executor nello schema esposto al
    pianificatore LLM. Fix strutturale (30/4/2026) per il disallineamento
    prompt↔schema sotto schema-guided decoding (Ollama/modello locale).

    Regola unica: se l'executor consuma una lista prodotta da uno step
    upstream (rilevato dalla presenza di `entries` in `properties` o in
    `required` del manifest), lo schema esposto al modello rimuove
    `entries` (che NON puo' essere inventato dall'LLM, e' un dato di
    runtime) e inietta `from_step: integer >=1` come argomento di
    riferimento allo step. Altri campi del manifest (e.g. `by`, `op`,
    `dst_template`) restano invariati.

    Razionale: sotto schema-guided decoding il modello segue lo schema,
    non il prompt. Esporre `entries: array` come richiesto forza l'LLM a
    inventare contenuto plausibile o passare `[]`. Esporre `from_step`
    come unico riferimento alla lista upstream rende il vincolo
    strutturale, non un consiglio nel prompt. Vedi §2.4 di the design guide
    (robustezza al confine NL→determinismo) e ADR-from_step.

    Il `validate_args` accetta sia `from_step` che `entries` (vedi
    special-case linea 387), quindi:
    - manifest che gia' usano `from_step` (filter_entries, get_files,
      move_files): nessuna trasformazione necessaria.
    - manifest che usano `entries` (sort_entries, compute_entries,
      classify_entries): vengono trasformati qui in modo consistente.
    """
    if not isinstance(schema, dict):
        return schema
    props = dict(schema.get("properties") or {})
    required = list(schema.get("required") or [])
    has_entries = "entries" in props or "entries" in required
    if not has_entries:
        return schema
    # Rimuovi entries dalla vista del modello: non puo' inventarle.
    props.pop("entries", None)
    # Inietta from_step (idempotente).
    if "from_step" not in props:
        props["from_step"] = {
            "type": "integer",
            "minimum": 1,
            "description": _FROM_STEP_DESC,
        }
    # Sostituisci entries con from_step in required, deduplicando.
    new_required = []
    for r in required:
        target = "from_step" if r == "entries" else r
        if target not in new_required:
            new_required.append(target)
    if "from_step" not in new_required:
        new_required.append("from_step")
    out = dict(schema)
    out["properties"] = props
    out["required"] = new_required
    return out


def render_tools_for_provider(executors):
    """Converte la lista di Executor in tools format Ollama/OpenAI.

    Applica `planner_facing_schema` per garantire che lo schema esposto
    al modello sia coerente con la convenzione `from_step` (vedi
    docstring). Trasformazione centralizzata: i singoli manifest non
    devono ricordare la convenzione, la pipeline rendering la impone.

    Slim (#H0 19/5/2026 sera): la description + args_schema sono compressi
    deterministicamente (prima frase + boundary §2.5; args desc 1
    frase corta; rimozione description per arg auto-descrittivi con
    default). Disable via env METNOS_TOOL_SCHEMA_FULL=1. Vedi
    `runtime/tool_schema_slim.py`.
    """
    from tool_schema_slim import (
        slim_description, slim_args_schema, is_slim_enabled,
    )
    apply_slim = is_slim_enabled()
    tools = []
    for ex in executors:
        desc = ex.description
        params = planner_facing_schema(ex.args_schema) or {"type": "object"}
        if apply_slim:
            desc = slim_description(desc)
            params = slim_args_schema(params)
        tools.append({
            "type": "function",
            "function": {
                "name": ex.name,
                "description": desc,
                "parameters": params,
            },
        })
    return tools


# --- Validazione args (subset JSON Schema v1.1) ----------------------------

# Marker auto-referenziali di RIFIUTO/meta-commento del modello (IT+EN, il
# modello locale risponde in-lang). Frasi distintive che non compaiono mai come valore
# legittimo di un argomento (titolo, path, id, query). Usate da validate_args
# per intercettare i rifiuti che il PLANNER trapela DENTRO un arg.
# _LLM_REFUSAL_MARKERS migrato a detection_lexicon (concept substring
# `llm.refusal_marker`); vedi detection_lexicon_seed.


def validate_args(args, schema):
    failures = []
    schema = schema or {}
    props = schema.get("properties") or {}
    required = schema.get("required") or []
    for r in required:
        if r not in args:
            # `from_step` viene consumato da resolve_from_step e sostituito da
            # `entries` (popolato dallo scratchpad). Se entries e' presente,
            # il required e' soddisfatto. Stesso pattern per altri arg che
            # un futuro resolver potrebbe iniettare.
            if r == "from_step" and "entries" in args:
                continue
            failures.append(f"missing required arg '{r}'")
    # `requires_one_of`: lista di liste, ciascuna sotto-lista esprime un
    # disgiuntivo "ALMENO uno fra X, Y, Z deve essere non-vuoto". Schema
    # dichiarativo §7.3 universale: ogni executor puo' imporre vincoli
    # tipo «paths OR urls OR from_step» senza prompt teaching ad-hoc nella
    # description. Un arg e' considerato "fornito" se: presente nel dict
    # E non-vuoto (string non-blank, list non-empty, dict non-empty).
    for group in (schema.get("requires_one_of") or []):
        if not isinstance(group, list) or not group:
            continue
        provided = False
        for k in group:
            if k == "from_step":
                # `from_step` viene risolto a `entries` upstream prima
                # dell'invocazione: vale se uno dei due e' presente non-vuoto.
                if (args.get("from_step") is not None
                        and args.get("from_step") != 0):
                    provided = True
                    break
                if args.get("entries"):
                    provided = True
                    break
                continue
            v = args.get(k)
            if v is None:
                continue
            if isinstance(v, str) and v.strip():
                provided = True
                break
            if isinstance(v, (list, dict)) and len(v) > 0:
                provided = True
                break
            if not isinstance(v, (str, list, dict)) and v:
                provided = True
                break
        if not provided:
            failures.append(
                f"requires one of {group} (none provided non-empty)"
            )
    # Placeholder value detection §7.3 (25/5/2026): property con
    # `forbid_placeholder_values: true` rifiuta valori sintetici tipo
    # "msg_1", "mail_2", "id_3" emessi dal PLANNER LLM quando inventa
    # IDs invece di usare from_step. Pattern deterministico §7.9.
    import re as _re_ph_val
    _PLACEHOLDER_VALUE_RE = _re_ph_val.compile(
        r"^(msg|mail|email|file|item|id|entry|element|placeholder|uid|"
        r"row|record|doc|document|message)[-_]?\d+$",
        _re_ph_val.IGNORECASE,
    )
    for name, value in (args or {}).items():
        if name not in props:
            continue
        spec = props[name]
        if spec.get("forbid_placeholder_values") and isinstance(value, list):
            _ph_hits = [v for v in value if isinstance(v, str)
                        and _PLACEHOLDER_VALUE_RE.match(v.strip())]
            if _ph_hits:
                failures.append(
                    f"arg '{name}' contiene valori placeholder sintetici "
                    f"({_ph_hits[:3]}). Usa `from_step=N` per ottenere "
                    f"gli ID reali dallo step producer (§4.1)."
                )
                continue
        # ``from_step`` e' un riferimento strutturale del runtime, consumato
        # prima dell'invocazione. Resta valido come intero anche nei vecchi
        # manifest che lo includevano per errore in uno schema array condiviso.
        if name == "from_step" and isinstance(value, int) \
                and not isinstance(value, bool):
            continue
        declared_type = spec.get("type")
        expected_types = (
            declared_type if isinstance(declared_type, list)
            else [declared_type] if isinstance(declared_type, str)
            else []
        )

        def _matches(expected):
            return {
                "array": lambda: isinstance(value, list),
                "boolean": lambda: isinstance(value, bool),
                "integer": lambda: isinstance(value, int) and not isinstance(value, bool),
                "null": lambda: value is None,
                "number": lambda: isinstance(value, (int, float)) and not isinstance(value, bool),
                "object": lambda: isinstance(value, dict),
                "string": lambda: isinstance(value, str),
            }.get(expected, lambda: True)()

        if expected_types and not any(_matches(item) for item in expected_types):
            expected_label = " | ".join(expected_types)
            failures.append(
                f"arg '{name}' deve essere {expected_label}, e' "
                f"{type(value).__name__}"
            )
        if "enum" in spec and value not in spec["enum"]:
            failures.append(f"arg '{name}' deve essere in {spec['enum']}, e' {value!r}")
    # LLM refusal / meta-text leak detection §7.3 (2/6/2026): il PLANNER LLM,
    # se gli si chiede di riempire un arg che non sa valorizzare (es. uno
    # spreadsheet_id Drive per un foglio ancora da creare), a volte emette un
    # RIFIUTO in linguaggio naturale COME VALORE dell'arg ("Non posso
    # eseguire... Come modello linguistico, non ho accesso..."): garbage che
    # non deve mai raggiungere l'executor (§2.8). Nessun valore legittimo e' un
    # rifiuto auto-referenziale del modello. Universale (ogni arg), §7.9.
    def _is_refusal(v):
        if not isinstance(v, str):
            return False
        lo = v.lower()
        return _detlex.match("llm.refusal_marker", lo)
    for name, value in (args or {}).items():
        if _is_refusal(value) or (
            isinstance(value, list) and any(_is_refusal(x) for x in value)
        ):
            failures.append(
                f"arg '{name}' contiene un rifiuto/meta-testo del modello, non "
                f"un valore reale: il PLANNER ha generato un rifiuto al posto "
                f"dell'argomento. Step malformato."
            )
    return failures


# --- Data piping {{stepN.field}} ------------------------------------------

_REF_RE = re.compile(r"^\{\{step(\d+)\.([a-zA-Z0-9_.]+)\}\}$")


_ACTION_VERBS_PRED = {
    "move", "delete", "send", "write", "extract", "create",
    "compress", "compute", "set", "render", "change",
}

# Keyword imperative bilingue (IT+EN) per discriminare query d'azione
# ("uccidi processo X", "kill -9 firefox") da query di stato puro
# ("stato sistema", "uptime"). Usate da Level 3 ADR 0111 e dal safety
# net in TurnLog.write() per decidere se il `final_message` LLM va
# preservato (azione richiesta) o sostituito dal blocco deterministico.
# _HEALTH_IMPERATIVE_KEYWORDS migrato a detection_lexicon (concept substring
# `health.imperative`); vedi detection_lexicon_seed.


# Executor transformative single-shot: dopo ok:True con _undo registrato,
# il runtime forza final_answer per evitare che il planner LLM oscilli e
# crei duplicati. ADR 0123 + bug live turn c627784c (11/5/2026 sera).
# Estendere solo per executor che creano UNA singola entita' remota per
# invocazione (set_*, send_*, write_* su provider esterni).
def _detect_binary_missing_in_obs(obs) -> dict | None:
    """Cerca `error_class="binary_missing"` nei `results` di un'observation.

    Ritorna il primo record con suggested_install (dict raw), None se
    nessun result ha questa shape. Pattern §7.3 install-on-demand
    (17/5/2026): usato sia da hook T1 (save pending) sia da rule planner
    (suggerire admin install).
    """
    if not isinstance(obs, dict):
        return None
    for r in (obs.get("results") or []):
        if isinstance(r, dict) and r.get("error_class") == "binary_missing":
            if r.get("suggested_install"):
                return r
    return None


def _is_auto_final_transformative(tool_name: str) -> bool:
    """True se il tool e' mutating §2.2 (verbo `_MUTATING_VERBS` prefix).
    Derivazione dinamica §7.3: ogni nuovo executor con verbo mutating eredita
    auto-final senza touch della lista. Sostituisce il vecchio set hardcoded
    `_AUTO_FINAL_TRANSFORMATIVE` (17/5/2026)."""
    if not tool_name or "_" not in tool_name:
        return False
    verb = tool_name.split("_", 1)[0]
    return verb in _MUTATING_VERBS


# P3 (12/5/2026) — Detection congiunzioni multi-step nella user_query.
# Trigger: turn b1d9c236 «fissa appuntamento il prossimo mercoledi mattina
# dopo le 9 per un ora se c'è posto E MANDAMI UNA EMAIL DI CONFERMA» →
# set_events ok → _AUTO_FINAL_TRANSFORMATIVE chiuse il turno → la parte
# «e mandami email di conferma» mai eseguita.
# Soluzione: regex IT+EN word-boundary su congiunzioni seguite da verbo
# d'azione esplicito. NON usiamo " e " generico per evitare falsi positivi
# («fissa o mercoledi o giovedi» = alternativa, «email e telefono» = lista
# noun). §7.9 deterministico, niente LLM.
# Congiunzioni STRUTTURALI di continuita' multi-step. Solo marker linguistici
# universali (no verbi enumerati): `_query_has_continuation` rileva multi-step
# anche via classe semantica (>=2 verbi canonici distinti — derivato da
# prefilter._VERB_TO_CANONICAL vocab IT+EN). §7.3 NON hardcoded enumerazione.
# _MULTISTEP_CONJUNCTIONS_RE migrato a detection_lexicon (concept regex
# `query.multistep`); vedi detection_lexicon_seed.


def _query_has_continuation(query: str) -> bool:
    """True se la query e' multi-step esplicito. Detection a 2 strati:

    1. Congiunzione strutturale di continuita' (regex universale IT+EN).
    2. Classe semantica: 2+ verbi canonici distinti nel query (derivato da
       prefilter._VERB_TO_CANONICAL, gia' contiene sinonimi IT+EN per le
       azioni di vocab.ACTIONS). §7.3 generale, non enumerativa.

    Esempi positivi:
      - «fissa appuntamento ... e mandami email di conferma» (set+send)
      - «book meeting friday and send confirmation» (set+send + «and»)
      - «mandami l'email e fissa l'appuntamento» (send+set inverso)
    Esempi negativi:
      - «fissa o mercoledi o giovedi» (alternativa, 1 verbo solo)
      - «email e telefono di X» (lista nominale, 0 verbi)
      - «fissa appuntamento mercoledi alle 9» (1 verbo solo)
    Determinismo §7.9: lookup O(len(query)) + token scan vocab, niente LLM.
    """
    if not query or not isinstance(query, str):
        return False
    if _detlex.search("query.multistep", query):
        return True
    # Multi-verb detection via vocab classes. Per le congiunzioni semplici
    # ``e``/``and`` richiediamo almeno un verbo su CIASCUN lato: token nominali
    # polisemici come ``email`` non devono trasformare «dimmi email e telefono»
    # in due azioni. I marker forti (e poi/and then/inoltre/...) sono gia'
    # gestiti dal detection lexicon sopra.
    try:
        from prefilter import tokenize, detect_canonical_verbs_all
        import re as _re
        for conjunction in ("e", "and"):
            for match in _re.finditer(
                    rf"(?<!\w){_re.escape(conjunction)}(?!\w)",
                    query, flags=_re.IGNORECASE):
                left = detect_canonical_verbs_all(tokenize(query[:match.start()]))
                right = detect_canonical_verbs_all(tokenize(query[match.end():]))
                if left and right:
                    return True
        return False
    except Exception:
        return False


def _format_send_messages_detail(obs: dict) -> str:
    """Costruisce il `detail` di MSG_TRANSFORMATIVE_AUTO_FINAL per send_messages.

    Pattern: «a <recipient_id> «<subject>»». Helper §7.9 (no LLM). Idem
    `_format_..._detail` per altri executor che non espongono htmlLink/id ma
    hanno una shape `results[0]` con campi user-facing standardizzati.
    """
    if not isinstance(obs, dict):
        return ""
    r0 = (obs.get("results") or [{}])[0]
    if not isinstance(r0, dict):
        return ""
    to = r0.get("recipient_id") or r0.get("target") or ""
    if isinstance(to, list):
        to = ", ".join(str(x) for x in to)
    subj = r0.get("subject") or ""
    if to and subj:
        return f"a {to} «{subj}»"
    if to:
        return f"a {to}"
    return subj


_MUTATING_VERBS = frozenset({
    # Sottoinsieme di ACTIONS §2.2 con side-effect remoto:
    # send, create, delete, set, write, move, share, change, render.
    # NON include: read, find, get, list, filter, sort, group, classify,
    # describe, compute, compare, order, extract, compress.
    "send", "create", "delete", "set", "write", "move", "share",
    "change", "render",
})


def _all_query_verbs_satisfied(query: str, executed_tools: list[str]) -> bool:
    """True se TUTTI i verbi MUTATING della query sono coperti da almeno
    uno degli `executed_tools` (prefisso `<verb>_`). Verbi read-only/
    suggestion (find/get/read/list/filter/describe/...) sono opzionali per
    la chiusura pipeline. Determinismo §7.9.

    Bug live turn 7f7381d2dba24bdc (14/5/2026, propose+choose+notify): dopo
    send_messages ok=True il PLANNER rifaceva find_events_empty perche'
    auto_final_transformative era inibito da `_query_has_continuation` (la
    query ha 2 verbi: `proponi`→describe + `mandami`→send). Pipeline e'
    completa quando il verbo MUTATING (`send`) e' stato eseguito; il
    `describe` di «proponi» e' P5 suggestion-semantics, copertura
    soddisfatta dal `find_events_empty + get_inputs` upstream.

    `executed_tools` deve essere la lista dei tool name (chosen_name) degli
    step ok=True inclusi i `resumed_from_prior_turn` (lo scratchpad
    ricostruito dal callback resume_planner). Caller responsabile.
    """
    if not query or not isinstance(query, str) or not executed_tools:
        return False
    try:
        from prefilter import tokenize, detect_canonical_verbs_all
        verbs = detect_canonical_verbs_all(tokenize(query))
        mutating = [v for v in verbs if v in _MUTATING_VERBS]
        if not mutating:
            return False
        executed_verbs = {
            t.split("_", 1)[0] for t in executed_tools if isinstance(t, str) and "_" in t
        }
        return all(v in executed_verbs for v in mutating)
    except Exception:
        return False


# P6 (12/5/2026) — Notify-continuation detection per multi-pipeline.
# Trigger: turn 35431172 «proponi N orari ... E MANDAMI EMAIL con la
# scelta». Atteso: pipeline propose-and-notify (variant a/b) con
# send_messages come step finale dopo get_inputs (e create_events
# eventualmente in mezzo).
# Pattern: marker "notify" (mandami/inviami/notificami/avvisami + email/
# notifica/conferma) + congiunzione («e», «and», ", e") che precede.
# §7.9 deterministico, niente LLM.
# La regex e' DISTINTA da `_MULTISTEP_CONJUNCTIONS_RE`: qui catturiamo
# il VERBO di notifica esplicito + il MEZZO (email/notifica/messaggio/
# telegram/conferma), non solo la congiunzione strutturale.
_NOTIFY_CONTINUATION_RE = re.compile(
    r"("
    # IT verbi notify con enclitici tipici mi/ci/gli — token interi via \b.
    r"\b(?:mandami|inviami|spediscimi|notificami|avvisami|scrivimi)\b|"
    r"\bfammi\s+sapere\b|"
    # Forma "e/poi + verbo + (article)? + (mezzo)": multi-step strutturale
    # con marker esplicito del mezzo. NB: \b iniziale prima della cong.
    r"\b(?:e|and|poi)\s+(?:mi\s+)?(?:mandi|invii|spedisci|notifichi|avvisi|"
    r"invia|manda|notifica|avvisa)\s+(?:una\s+|un\s+|la\s+)?"
    r"(?:email|mail|messaggio|notifica|conferma|sms|telegram|whatsapp)\b|"
    # EN verbi notify
    r"\b(?:email|notify|alert|message|text|ping)\s+me\b|"
    r"\bsend\s+me\s+(?:a\s+|an\s+)?(?:email|message|text|notification|notify)\b|"
    r"\blet\s+me\s+know\b|"
    # Marker del MEZZO di notifica esplicito: «via email», «via telegram».
    r"\bvia\s+(?:email|mail|telegram|sms|whatsapp|notifica|notification|message)\b|"
    # Coda «+ invia conferma», «and send confirmation»: cong NON-word (+/,)
    # OPPURE word (e/and/poi). Senza \b sul prefix per ammettere `+`/`,`.
    # Lookbehind senza fixed-width: usiamo char-class al posto di alternation.
    r"(?:[\s\+,]|\b)(?:e|and|poi|\+|,)\s+(?:invia|manda|notifica|send|notify)\s+"
    r"(?:una\s+|un\s+|la\s+|a\s+|an\s+|the\s+)?"
    r"(?:conferma|confirmation|notifica|notification|messaggio|email|mail)\b|"
    # Verbo notify standalone dopo cong NON-word/word (end-of-clause):
    # «+ notifica», «and notify» a fine richiesta. Implica «notify the user».
    r"(?:[\s\+,]|\b)(?:e|and|poi|\+|,)\s+(?:notifica|notify)(?=\s*[.!?]|\s*$)|"
    # Cong NON-word + <noun_medium> [<noun_conferma>]: «+ email conferma»,
    # «, email confirmation», «+ telegram avviso». Forma ellittica del
    # verbo notify (verbo sottinteso, mezzo+oggetto espliciti). Solo per
    # congiunzioni NON-word (+/,) che marcano gia' lo step separato; un
    # verbo coniugato «e/and/poi» da solo NON triggera questa branch per
    # evitare falsi positivi (es. «cerca email» — congiunzione word senza
    # ellissi verbale).
    r"(?:\s*[\+,])\s+"
    r"(?:una\s+|un\s+|la\s+|a\s+|an\s+|the\s+)?"
    r"(?:email|mail|telegram|sms|whatsapp|notifica|notification|messaggio|message)\s*"
    r"(?:di\s+|of\s+)?"
    r"(?:conferma|confirmation|riassunto|summary|notifica|notification|"
    r"avviso|alert|update|aggiornamento)?\b"
    r")",
    re.IGNORECASE,
)


def _query_has_notify_continuation(query: str) -> bool:
    """True se la query contiene una continuation di notifica esplicita
    diretta all'utente.

    Esempi positivi:
      - «... e mandami email con la scelta»
      - «... e notificami il risultato»
      - «... and email me the choice»
      - «... and let me know»
      - «... con conferma via email»
    Esempi negativi (devono ritornare False):
      - «cerca email» (verbo search, non notify)
      - «leggi le email di oggi» (verbo read)
      - «email di Mario» (sostantivo, no verbo notify)
    Determinismo §7.9: regex O(len(query)), niente LLM.
    """
    if not query or not isinstance(query, str):
        return False
    return bool(_NOTIFY_CONTINUATION_RE.search(query))


# P4 (12/5/2026) — Availability marker detection per check_availability.
# Trigger: turn b1d9c236 «... SE C'È POSTO» → planner ha chiamato set_events
# direttamente senza read_events. Il workflow (check_availability) di
# calendar.j2 era ignorato.
# Defense in depth: il runtime intercetta set_events quando la query ha
# un availability marker e read_events NON e' nei step precedenti.
# Soluzione (c): post-hoc reject + hint, lascia che il planner ri-pianifichi.
# Determinismo §7.9: regex deterministico, niente LLM.
_AVAILABILITY_MARKERS_RE = re.compile(
    r"\b("
    # IT
    r"se\s+c['’]?[eè]\s+(un\s+)?(posto|buco|slot|spazio|tempo)|"
    r"se\s+(la\s+finestra|lo\s+slot)\s+[eè]['\s]*libera|"
    r"se\s+sono\s+libero|se\s+sei\s+libero|"
    r"se\s+non\s+ho\s+(altro|impegni|gi[aà])|"
    r"verifica\s+(la\s+)?disponibilit[aà]|controlla\s+(la\s+)?disponibilit[aà]|"
    r"se\s+disponibile|"
    # EN
    r"if\s+(it['’]?s\s+)?available|if\s+(i\s+am|i['’]?m)\s+free|"
    r"if\s+there['’]?s\s+(a\s+)?(slot|opening|space|time)|"
    r"if\s+free|check\s+availability|"
    r"if\s+(the\s+)?(slot|window)\s+is\s+free"
    r")\b",
    re.IGNORECASE,
)


# Tool che CREANO eventi calendar: derivati dal catalog al call-time
# (verb in classe trasformativa + object="events"). §7.3 NON hardcoded.
# Cache LRU al boot per evitare scan ad ogni gate check.
def _calendar_write_tools() -> frozenset:
    """Set di executor name che scrivono sul calendario.
    Derivato dal catalog (`verb in {set, create} AND object == "events"`).
    Cache modulo-level invalidata solo a reload manuale (no overhead per-call).
    """
    cached = getattr(_calendar_write_tools, "_cached", None)
    if cached is not None:
        return cached
    try:
        from loader import load_catalog
        from vocab import canonical_object
        names = set()
        for ex in load_catalog():
            name = ex.name
            if "_" not in name:
                continue
            verb, _, obj_raw = name.partition("_")
            if verb not in ("set", "create"):
                continue
            # canonical_object riconosce sinonimi (events/eventi/appuntamenti).
            # Per executor naming il suffisso e' gia' canonico, ma normalizziamo
            # per robustezza (es. send_messages_google_workspace).
            obj_canon = canonical_object(obj_raw.split("_")[0])
            if obj_canon == "events":
                names.add(name)
        result = frozenset(names)
    except Exception:
        # Fallback graceful se catalog non disponibile (test/boot iniziale):
        # almeno create_events e' guaranteed-canonical (post ADR 0128, era set_events).
        result = frozenset({"create_events"})
    _calendar_write_tools._cached = result
    return result



def _query_requires_availability_check(query: str) -> bool:
    """True se la query richiede availability check pre-set_events.

    Esempi positivi:
      - «se c'è posto», «se sono libero», «se non ho altro»
      - «verifica disponibilità», «if there's a slot», «if free»
    Esempi negativi:
      - «fissa appuntamento mercoledi alle 9» (no marker)
      - «book meeting friday» (no marker)
    Determinismo §7.9: regex lookup O(len(query)), niente LLM.
    """
    if not query or not isinstance(query, str):
        return False
    return bool(_AVAILABILITY_MARKERS_RE.search(query))


# P5 (12/5/2026) — Propose-intent detection per gate suggestion vs destructive.
# Trigger: turn a0b96f6f (12/5/2026 09:07) → query «proponi 3 orari per
# appuntamento la prossima settimana mattina» → planner ha chiamato
# set_events con summary="Appuntamento Proposto 1" e finestra 8:00-12:00
# lunedi-sabato (whole-week blob destructive). Atteso: read_events + final
# testuale con N slot computati. NESSUN set_events.
#
# Regex SEMANTICA UNIVERSALE: cattura verbi di suggerimento IT+EN con
# eventuali enclitici (mi/ti/ci/gli) tramite quantifier, NON enumerazione
# enclitica esaustiva. Cattura anche le formulazioni interrogative tipiche
# («che ne dici», «what about», «quali sono N ... liberi»). Determinismo
# §7.9: regex compilata, niente LLM nel runtime gate.
#
# Pattern espliciti per «quali sono N X liberi/disponibili» perche' la
# costruzione «quali sono i miei impegni» (read events) NON deve triggerare
# il gate (la query e' read, non suggerimento di nuovo slot).
_PROPOSE_INTENT_RE = re.compile(
    r"(?:\b|^)("
    # IT — verbi suggestion con eventuali enclitici (mi/ti/ci/gli/mela/...)
    # Forma generale: stem + opzionale enclitico. Compatto via quantifier.
    # Stem + opzionale enclitico (mi/ti/ci/gli/cela/...) — quantifier-based,
    # NON enumerazione esaustiva. La forma `\w{1,5}?` cattura enclitici e
    # desinenze di coniugazione (-armi -arci -ami -ate -ano -ebbe ...).
    r"propon[a-z]{1,5}|propor[a-z]{2,7}|"
    r"suggeris[a-z]{1,5}|sugger[a-z]{2,7}|"
    r"raccomand[a-z]{1,6}|"
    # IT — formulazioni interrogative tipiche di richiesta suggerimento.
    # Pattern «che [ne] dici», «cosa [ne] pensi», «che dici», «consigliami N»
    r"che(?:\s+ne)?\s+dici|cosa(?:\s+ne)?\s+(?:dici|pensi)|"
    r"consigli[a-z]{1,5}|"
    # IT — «quali (sono|fasce|orari|slot|...) ... liber[ie]/disponibil[ie]/...»
    # Pattern semantico: parola interrogativa «quali» seguita entro la frase
    # da un marker di disponibilita'/vacuita'. La distanza max 0-6 tokens.
    # NB 22/5/2026: rimosso `aperte?` dal pattern — falso positivo su query
    # sysinfo «quali porte TCP aperte» (network info, NON calendar). I marker
    # canonici disponibilita' calendar sono `liber[ie]|disponibil[ie]|vuot[ei]`.
    r"quali\s+(?:\w+\s+){0,6}(?:liber[ie]|disponibil[ie]|vuoti?|vuote)|"
    # IT — «N alternative/opzioni/slot/orari/fasce/mattine/proposte».
    # Forma con numero (3/2/...) + sostantivo proposta-like. Cattura
    # «dammi 3 alternative», «cerca 3 slot 9-11», «alcune proposte»,
    # «2 mercoledi liberi», «qualche slot». Indipendente dal verbo
    # principale (cerca/dammi/voglio/etc.: il SOSTANTIVO + il NUMERO
    # bastano a inferire "richiesta di N opzioni" semanticamente).
    # Lista sostantivi: alternative/opzioni/proposte sono universali
    # proposal-noun; slot/orari/fasce/mattine/pomeriggi/giorni-settimana
    # sono dominio calendar (parte di `_OBJECT_HINTS["events"]`).
    r"(?:\d+|alcun[ie]|qualche|alcune|alcuni|some)\s+"
    r"(?:opzion[ie]|alternativ[ae]|propost[ae]|slot|slots|orari[oi]?|"
    r"fasce?|mattine?|pomeriggi|finestre?|"
    r"mercoled[ìi]|luned[ìi]|marted[ìi]|"
    r"gioved[ìi]|venerd[ìi]|sabat[oi]|domenic[ah]e?)|"
    # EN — verbs (gerund/3rd, infinitive)
    r"propose|proposes|proposing|"
    r"suggest|suggests|suggesting|"
    r"recommend|recommends|recommending|"
    # EN — interrogative
    r"what\s+about|how\s+about|"
    # EN — «what slots/times/X (are) free/available/open»: marker dispon-
    # bilita' su sostantivo plurale. Stessa logica di «quali» IT.
    r"what\s+(?:\w+\s+){0,4}(?:are\s+|is\s+)?(?:free|available|open)|"
    r"which\s+(?:\w+\s+){0,4}(?:are\s+|is\s+)?(?:free|available|open)|"
    # EN — «any free X», «any open X» — domanda «c'e' / ce ne sono?»
    # Restringo al dominio calendar via lista nomi temporal: slot/time/window.
    r"any\s+(?:free|available|open)\s+(?:slots?|times?|windows?|mornings?|afternoons?|days?|appointments?|meetings?)|"
    # EN — «N options/alternatives/slots/morning times/...» (with optional
    # preceding politeness verb: give me / I'd like / I want / can you).
    # Lista nomi RISTRETTA al dominio proposal/calendar:
    # options/alternatives/proposals = universal proposal-noun;
    # slots/times/mornings/afternoons/openings = calendar dominio.
    # Esclude generici (emails/files/messages) per evitare falsi positivi.
    r"(?:\d+|some|a\s+few|several|any)\s+"
    r"(?:morning\s+|afternoon\s+|free\s+|available\s+|open\s+|"
    r"alternative\s+|proposed?\s+)?"
    r"(?:options?|alternatives?|proposals?|slots?|times?|"
    r"mornings?|afternoons?|openings?|windows?)"
    r")(?:\b|$)",
    re.IGNORECASE,
)


def _query_is_propose_intent(query: str) -> bool:
    """True se la query e' propose-intent (richiesta di suggerimento/proposta
    di alternative), NON di creazione/modifica destrutiva.

    Esempi positivi:
      - «proponi 3 orari per appuntamento»
      - «suggeriscimi 2 mercoledi liberi»
      - «raccomandami una mattina libera»
      - «che ne dici di lunedi 9-10»
      - «quali sono le mattine libere prossima settimana»
      - «propose 3 morning times», «suggest a meeting time»
      - «what are 3 free slots tomorrow», «give me 3 options»

    Esempi negativi (devono ritornare False):
      - «fissa appuntamento mercoledi alle 9»  (set destructive)
      - «book a meeting friday»  (set destructive)
      - «quali sono i miei impegni domani»  (read events, no «liberi/disponibili»)
      - «crea evento lunedi»  (create destructive)

    Determinismo §7.9: regex O(len(query)), niente LLM.
    """
    if not query or not isinstance(query, str):
        return False
    return bool(_PROPOSE_INTENT_RE.search(query))


def _has_prior_read_events_ok(steps) -> bool:
    """True se uno dei step precedenti e' read_events con ok=True.

    Usato dai gate P4/P5 per non bloccare set_events quando il check
    availability/read gia' fatto. §7.9 lookup deterministico.
    """
    for s in steps:
        tool = getattr(s, "chosen_tool", None)
        if tool != "read_events":
            continue
        res = getattr(s, "result", None)
        if isinstance(res, dict) and res.get("ok") is True:
            return True
    return False


# Bug 12/5/2026 resume PLANNER dopo dialog pick (propose+notify continuation).
# Euristica deterministica §7.9 per detectare MID-pipeline get_inputs: il
# PLANNER ha emesso get_inputs ma la query contiene una continuation
# (notify/create/move/...) che richiede un altro step dopo il pick.
# _RESUME_AFTER_DIALOG_HINTS_IT/EN migrato a detection_lexicon (concept
# substring `dialog.resume_hint`); vedi detection_lexicon_seed.


def _should_resume_planner_after_dialog(query: str, route_info,
                                          history_for_refs) -> bool:
    """True se il get_inputs corrente e' MID-pipeline e il turno deve
    riprendere dopo il pick dell'utente (bug 12/5/2026 propose+notify).

    Sources di evidenza (OR):
      1. route_info ha uno dei marker `multi_pipeline_*` (propose+notify
         o notify-only) — il rank ha gia' classificato la query come
         multi-pipeline.
      2. Hint linguistici di continuation (mandami/manda/invia/notify/
         create/...): notify e action-verb residuo dopo il dialog pick.

    Determinismo §7.9: nessun LLM. Restituisce bool.

    DEVI: ritornare True solo se la query contiene una continuation
    legittima oltre il dialog.
    NON DEVI: triggerare resume per query single-step (es. solo
    «proponi 3 orari» senza «e mandami»).
    OK: «proponi 3 orari e mandami email» → True.
    ERRORE: «mostrami 3 orari» → False (solo display, no action verb).
    """
    if not isinstance(query, str) or not query.strip():
        return False
    # Source 1: route_info marker (set in run_turn quando la query e'
    # propose+notify o notify-only).
    if isinstance(route_info, dict):
        if (route_info.get("multi_pipeline_propose_notify")
                or route_info.get("multi_pipeline_notify_only")):
            return True
    # Source 2: hint linguistici espliciti.
    if _detlex.match("dialog.resume_hint", query):
        return True
    return False


def _inject_get_inputs_choice_for_propose(*, entries: list,
                                            object_canonical: str
                                            ) -> dict | None:
    """Costruisce args deterministici per `get_inputs(kind=choice)` quando
    siamo in pipeline propose+notify dopo `find_events_empty` (ADR 0129
    extended, 14/5/2026 sera).

    Determinismo §7.9: nessun LLM, lookup tabellare per object_canonical
    (oggi solo `events`; estendere quando emerge altro pattern).
    """
    if not entries:
        return None
    if object_canonical == "events":
        return {
            "title": "Scegli l'orario per l'appuntamento",
            "entries": entries,
            "dialog": [{
                "var": "scelta",
                "prompt": "Quale orario preferisci per l'appuntamento?",
                "schema": {
                    "kind": "choice",
                    "display_template": "{when_human}",
                    "value_field": "start",
                },
            }],
        }
    return None


def _snapshot_scratchpad(history_for_refs) -> list:
    """Snapshot serializzabile JSON dello scratchpad per il resume callback.

    Filtra step troppo pesanti (entries molto lunghe): truncation a max 50
    entries per step + max 10KB per observation totale (heuristica safe).
    Determinismo §7.9.
    """
    out: list = []
    for h in (history_for_refs or []):
        if not isinstance(h, dict):
            continue
        obs = h.get("observation") or {}
        # Trim entries lunghe: il PLANNER continuation vede sintesi, non
        # blob full. Se servono dettagli il PLANNER puo' ri-leggere.
        if isinstance(obs, dict):
            obs_trimmed = dict(obs)
            entries = obs_trimmed.get("entries")
            if isinstance(entries, list) and len(entries) > 50:
                obs_trimmed["entries"] = entries[:50]
                obs_trimmed["_entries_truncated"] = True
                obs_trimmed["_entries_total"] = len(entries)
        else:
            obs_trimmed = obs
        out.append({
            "step": int(h.get("step") or 0),
            "tool": h.get("tool") or "",
            "args": h.get("args") or {},
            "observation": obs_trimmed,
        })
    return out


_AUTO_FINAL_SKIP_TOOLS = frozenset({
    "scratchpad_read", "filter_entries", "classify_entries", "describe_entries",
})


# Verbi non-action (read-only/discovery/pure-compute) soggetti al cap
# per-turn rinforzato (8/5/2026 notte). Identificati dal prefisso del
# tool name (azione_oggetto). I verbi action restano protetti dalle
# guardie pre-esistenti (vaglio, cyclic, duplicate).
_NON_ACTION_VERB_PREFIXES = frozenset({
    "find", "get", "list", "read", "classify", "filter",
    "describe", "compute", "compare", "sort", "group",
})


def _is_non_action_tool(tool_name: str) -> bool:
    """True se il tool e' un verbo non-action (cf. `_NON_ACTION_VERB_PREFIXES`).

    Estrae il prefisso azione dal nome `azione_oggetto[_qualifier]`.
    Ritorna False per `final_answer`, scratchpad_read e tool senza
    underscore (sicurezza per nomi atipici).
    """
    if not tool_name or "_" not in tool_name:
        return False
    if tool_name == "scratchpad_read":
        return True  # scratchpad_read e' read-only data-piping
    verb = tool_name.split("_", 1)[0]
    return verb in _NON_ACTION_VERB_PREFIXES


def _tokenize_for_dup(value):
    """Estrae token-set ordinato da uno scalare/lista per Jaccard.

    Strip whitespace, lowercase, split su whitespace + punteggiatura
    semplice. Numeri, path e identificatori opachi restano interi.
    """
    if value is None:
        return frozenset()
    if isinstance(value, (list, tuple)):
        out = set()
        for v in value:
            out |= _tokenize_for_dup(v)
        return frozenset(out)
    if isinstance(value, dict):
        out = set()
        for v in value.values():
            out |= _tokenize_for_dup(v)
        return frozenset(out)
    s = str(value).strip().lower()
    if not s:
        return frozenset()
    # Split su whitespace e punteggiatura "soft" — preserva path e URL.
    tokens = re.split(r"[\s,;|]+", s)
    return frozenset(t for t in tokens if t)


# Campi-arg "semantici" (testo libero) per dedup/Jaccard cross-call: una sola
# definizione condivisa da _normalize_args_for_dup + _args_jaccard (erano 2 copie).
_SEMANTIC_FIELDS = frozenset({"topic", "query", "pattern", "q", "search", "text"})


def _normalize_args_for_dup(args):
    """Normalizza dict args per confronto duplicate-near-identical.

    Ritorna dict con:
      - Chiavi ordinate.
      - Stringhe whitespace-stripped + lowercase.
      - Liste di stringhe ordinate.
      - Campi `topic`/`query`/`pattern`/`q` ridotti a token-set ordinato
        (sorted tuple) per Jaccard cross-call.
    Argomenti mancanti / None / liste vuote: rimossi.
    """
    if not isinstance(args, dict):
        return {}
    out = {}
    for k in sorted(args.keys()):
        v = args[k]
        if v is None:
            continue
        if isinstance(v, str):
            v2 = v.strip().lower()
            if not v2:
                continue
            if k in _SEMANTIC_FIELDS:
                out[k] = tuple(sorted(_tokenize_for_dup(v2)))
            else:
                out[k] = v2
        elif isinstance(v, (list, tuple)):
            if not v:
                continue
            if k in _SEMANTIC_FIELDS:
                out[k] = tuple(sorted(_tokenize_for_dup(v)))
            else:
                # Lista di stringhe → strip+lowercase+ordinata; altri tipi → repr stabile.
                norm = []
                for item in v:
                    if isinstance(item, str):
                        norm.append(item.strip().lower())
                    else:
                        norm.append(repr(item))
                out[k] = tuple(sorted(norm))
        elif isinstance(v, dict):
            sub = _normalize_args_for_dup(v)
            if sub:
                out[k] = tuple(sorted(sub.items()))
        else:
            out[k] = v
    return out


def _args_jaccard(a_norm, b_norm) -> float:
    """Jaccard token-set fra due args normalizzati su campi semantici.

    Se nessuno dei due ha campi semantici (topic/query/pattern/...),
    fall-back: ritorna 1.0 sse i dict normalizzati sono uguali, 0.0 altrimenti.
    """
    a_tokens = set()
    b_tokens = set()
    for k in _SEMANTIC_FIELDS:
        if k in a_norm and isinstance(a_norm[k], tuple):
            a_tokens |= set(a_norm[k])
        if k in b_norm and isinstance(b_norm[k], tuple):
            b_tokens |= set(b_norm[k])
    if not a_tokens and not b_tokens:
        return 1.0 if a_norm == b_norm else 0.0
    if not a_tokens or not b_tokens:
        return 0.0
    inter = a_tokens & b_tokens
    union = a_tokens | b_tokens
    return len(inter) / len(union) if union else 0.0


_AUTO_FINAL_PREFER_READ_OVER_DISCOVERY = (
    # find_urls e' discovery (URL+title+snippet); read_urls_html ha
    # contenuto reale. Quando entrambi sono presenti e il read ha text
    # non-banale, preferisci il read come fonte di final_message.
    {"find_urls"},
    {"read_urls_html", "read_urls_pdf", "get_urls_text"},
)


def _compose_final_message_from_obs(lp_tool, lp_obs):
    """Compose final_message dal `last_productive` (tool, obs).

    Estrazione del detail dalla observation con precedenza:
      detail_md > summary > final_message_hint > message > results/entries.
    Ritorna (final_message, ok_count, n_above_threshold).
    Riusato da auto_final_on_duplicate e cap_max_per_turn (8/5/2026 notte).

    §2.8 ext (25/5/2026): se l'obs ha 0 elementi prodotti (ok_count=0,
    entries=[], results=[]) E ha `errors` non vuoti, NON usare il
    template "completato" — riporta gli errori reali. Caso live turn
    2a5f2711: find_images_web con `urls=path_locale` → entries=[] +
    errors=[{...}] ma ok=True → final disonesto "completato (0 elementi)".
    """
    # Detection processor con 0 entries (§2.8 honesty, 25/5/2026):
    # se il last_productive e' un PROCESSOR_VERB (filter/classify/sort/
    # group/compute/compare/describe) che ritorna entries=[] dopo
    # filtraggio, il template "completato (0 elementi)" sarebbe vero ma
    # fuorviante (l'utente ha chiesto un'azione mutating su un subset
    # che e' risultato vuoto). Emetti final dedicato. Caso live turn
    # e2ec23eb: filter_entries(where=relevance,where_in=[junk,low]) →
    # 0 entries (nessuna mail spam) → auto_final pescava read_messages
    # come last_productive e mostrava "17 elementi" fuorviante.
    if isinstance(lp_obs, dict) and lp_tool:
        try:
            from vocab import PROCESSOR_VERBS as _PROC_VERBS_LP
        except Exception:
            _PROC_VERBS_LP = frozenset()
        _verb_lp = lp_tool.split("_", 1)[0]
        if _verb_lp in _PROC_VERBS_LP:
            _entries_lp = lp_obs.get("entries") or []
            _results_lp = lp_obs.get("results") or []
            if not _entries_lp and not _results_lp:
                try:
                    return msg(
                        "MSG_PROCESSOR_EMPTY",
                        tool=lp_tool,
                    ), 0, 0
                except Exception:
                    return (
                        f"Nessun risultato da `{lp_tool}`. Il filtro non "
                        f"ha selezionato elementi corrispondenti."
                    ), 0, 0

    # Detection mascheramento 0-elementi + errors (§2.8 honesty).
    if isinstance(lp_obs, dict):
        _ok_cnt = lp_obs.get("ok_count")
        _entries = lp_obs.get("entries") or []
        _results = lp_obs.get("results") or []
        _errors = lp_obs.get("errors") or []
        _zero_work = (
            (_ok_cnt == 0 or _ok_cnt is None)
            and not _entries and not _results
        )
        if _zero_work and isinstance(_errors, list) and _errors:
            _err_msgs = []
            _seen = set()
            for _e in _errors[:3]:
                if not isinstance(_e, dict):
                    continue
                _emsg = (_e.get("error") or _e.get("error_class")
                         or _e.get("reason") or "")
                _emsg = str(_emsg).strip()
                if _emsg and _emsg not in _seen:
                    _seen.add(_emsg)
                    _err_msgs.append(_emsg)
            _err_text = "; ".join(_err_msgs) if _err_msgs else "errore"
            try:
                _msg_template = msg(
                    "MSG_FINAL_FALLBACK_FROM_ERROR",
                    tool=lp_tool or "", error=_err_text,
                )
            except Exception:
                _msg_template = f"{lp_tool}: nessun risultato. {_err_text}"
            return _msg_template, 0, 0

    ok_count, n_above_threshold = _extract_auto_final_count(lp_obs)
    # 15/5/2026: detail_md autoritativo → usalo come final PURO senza wrap
    # "{tool}: completato (...)". Per executor che producono markdown
    # ricco (read_tasks_history, lifecycle_summary, ecc.) il wrap aggiunge
    # rumore inutile ("read_tasks_history: completato (? elementi). ...")
    if isinstance(lp_obs, dict):
        md = lp_obs.get("detail_md")
        if isinstance(md, str) and md.strip():
            return md.strip(), ok_count, n_above_threshold
    explicit_detail_md = (
        lp_obs.get("detail_md") if isinstance(lp_obs, dict) else None
    )
    explicit_summary = (
        lp_obs.get("summary") if isinstance(lp_obs, dict) else None
    )
    explicit_hint = (
        lp_obs.get("final_message_hint") if isinstance(lp_obs, dict) else None
    )
    explicit_message = (
        lp_obs.get("message") if isinstance(lp_obs, dict) else None
    )
    detail = None
    if explicit_detail_md and isinstance(explicit_detail_md, str):
        detail = explicit_detail_md.strip()[:1500]
    elif explicit_summary and isinstance(explicit_summary, str):
        detail = explicit_summary.strip()[:400]
    elif explicit_hint and isinstance(explicit_hint, str):
        detail = explicit_hint.strip()[:600]
    elif explicit_message and isinstance(explicit_message, str):
        detail = explicit_message.strip()[:400]
    else:
        results = (lp_obs.get("results") or []) if isinstance(lp_obs, dict) else []
        entries_list = (lp_obs.get("entries") or []) if isinstance(lp_obs, dict) else []
        summary_bits = []
        for r in results[:5]:
            if not isinstance(r, dict):
                continue
            if r.get("to") and r.get("subject"):
                summary_bits.append(f"a {','.join(r['to']) if isinstance(r['to'], list) else r['to']} «{r['subject']}»")
            elif r.get("path"):
                summary_bits.append(r["path"])
            elif r.get("dst"):
                d = r["dst"]; folder = d.get("folder") if isinstance(d, dict) else d
                summary_bits.append(f"→ {folder}")
        if not summary_bits:
            for e in entries_list[:3]:
                if not isinstance(e, dict):
                    continue
                for k in ("name", "subject", "path", "title", "url",
                          "signature", "kind"):
                    v = e.get(k)
                    if v:
                        summary_bits.append(str(v)[:80])
                        break
        detail = "; ".join(summary_bits) if summary_bits else None
    if ok_count is None and explicit_message and isinstance(explicit_message, str):
        final_message = f"{lp_tool}: {explicit_message.strip()}"
    else:
        count_str = _format_auto_final_count(ok_count, n_above_threshold)
        final_message = msg(
            "MSG_AUTO_FINAL_COMPLETED",
            tool=lp_tool, count_str=count_str,
            detail=(detail if detail else msg("MSG_AUTO_FINAL_NO_DETAIL")),
        )
    return final_message, ok_count, n_above_threshold


# Vectorial enforcement helpers (ADR 0130, 12/5/2026).
# Bug live turn `8f8080c0` (12/5/2026, 13min): find_events_empty x9 consecutivi
# con args che variavano solo `time_windows` -> DUPLICATE_CALL non scattava (args
# diff), cap_same a 10 troppo permissivo per executor vettoriali. Anti-pattern
# §2.1: un executor che accetta args plurali (paths/urls/time_windows/...) DEVE
# essere chiamato UNA volta con N args, NON N volte. Detection deterministica via
# manifest introspection (`args_schema.properties[arg].type == "array"`), zero
# whitelist hardcoded §7.3. Cap_same custom 2 per executor vettoriali (vs 10
# default), perche' la chiamata seguente alla prima ok=True su un vettoriale e'
# sempre un retry del LLM che non aggiunge lavoro utile (segno di confusione su
# §2.1, non di esplorazione legittima).
_VECTORIAL_CAP_SAME = 2  # cap_same per executor vettoriali (vs DEFAULT_CAP_SAME_EXECUTOR=10)

@functools.lru_cache(maxsize=512)
def _executor_has_plural_args(executor_name: str, schema_signature: str) -> bool:
    """True se l'executor accetta almeno un arg plurale (lista) nel suo schema.

    Detection introspettiva del `args_schema.properties`: cerca proprieta' con
    `type=="array"` (JSON Schema). Determinismo §7.9: niente LLM, niente
    whitelist hardcoded. Si applica a TUTTI gli executor vettoriali §2.1.

    `schema_signature` e' una stringa stabile derivata dallo schema (sorted
    properties + type), usata come chiave di cache: invalida automaticamente
    al re-firma dell'executor (manifest re-loaded).

    DEVI: passare il signature dal caller (vedi `_vectorial_schema_signature`).
    NON DEVI: ispezionare l'oggetto Executor in cache (non hashable).
    """
    # Parser leggero del signature: "name:type;name:type;..." con type "array"
    # come marker per detection. Se non c'e' "array" nel signature, nessun
    # plural arg presente.
    return ":array" in schema_signature or ";array" in schema_signature


def _vectorial_schema_signature(args_schema: dict | None) -> str:
    """Stringa stabile derivata da `args_schema.properties` per cache key.

    Format: "name1:type1;name2:type2;..." (sorted by name). Tipi normalizzati a
    lowercase (json schema usa lowercase). Lascia "" se schema vuoto o malformed.
    Riusa solo type del top-level (no nested items.type analysis, sufficient
    per detection plural).
    """
    if not isinstance(args_schema, dict):
        return ""
    props = args_schema.get("properties") or {}
    if not isinstance(props, dict) or not props:
        return ""
    parts = []
    for name in sorted(props.keys()):
        prop = props.get(name) or {}
        t = (prop.get("type") if isinstance(prop, dict) else None) or ""
        parts.append(f"{name}:{str(t).lower()}")
    return ";".join(parts)


def _cap_same_for_executor(executor, default_cap: int) -> int:
    """Cap_same dedicato per `executor`: 2 se vettoriale (plural args), default
    altrimenti. ADR 0130 §2.1: executor vettoriali devono essere chiamati una
    volta con N args, non N volte. La soglia bassa previene il thrashing del
    LLM che varia gli args sperando in un risultato diverso.
    """
    if executor is None:
        return default_cap
    sig = _vectorial_schema_signature(getattr(executor, "args_schema", None))
    if _executor_has_plural_args(executor.name, sig):
        return _VECTORIAL_CAP_SAME
    return default_cap


# Bug live turn `eb837329` (11/5/2026): final_message su loop_break era una
# stringa hardcoded ("file extension, precise path") che parlava di file
# anche per query su events/messages/urls. Soluzione: hint parametrico per
# OBJECT dell'intent, tabella deterministica in `i18n.sqlite` (ADR 0104).
# Determinismo §7.9: mapping OBJECT -> chiave i18n, niente LLM.
_LOOP_BREAK_HINT_OBJECTS = frozenset({
    "files", "dirs", "messages", "events", "urls",
    "images", "processes", "contacts", "credentials",
})


def _loop_break_hint(intent_object: str | None) -> str:
    """Ritorna il hint user-facing parametrico sull'object dell'intent.

    Mappa `intent.object` (lowercase) -> chiave `MSG_LOOP_BREAK_HINT_<OBJ>`
    in `i18n.sqlite`. Fallback `MSG_LOOP_BREAK_HINT_GENERIC` quando l'object
    e' None, vuoto o non in `_LOOP_BREAK_HINT_OBJECTS`. Riusa `messages.get`
    (alias `msg`) per il fallback chain `current_lang -> en -> it`.
    """
    obj = (intent_object or "").strip().lower()
    if obj in _LOOP_BREAK_HINT_OBJECTS:
        text = msg(f"MSG_LOOP_BREAK_HINT_{obj.upper()}")
        # `<missing:KEY>` indica che la chiave non esiste in DB: fallback a generic.
        if not text.startswith("<missing:"):
            return text
    return msg("MSG_LOOP_BREAK_HINT_GENERIC")


def _intent_object_from_route(route_info) -> str | None:
    """Estrae `intent.object` da `route_info`. Robusto a None/missing.

    Riusato dai 6 emit-site di `MSG_LOOP_BREAK` in `run_turn` (5 guard
    branches pre-execute + 1 post-execute fail) per costruire il hint
    object-aware (`_loop_break_hint`).
    """
    if not route_info:
        return None
    intent = route_info.get("intent") if isinstance(route_info, dict) else None
    if not intent:
        return None
    return intent.get("object") if isinstance(intent, dict) else None


def _resolve_auto_final_from_steps(steps):
    """Risolve il `last_productive` per `auto_final_on_duplicate`.

    Walk back fra `steps` saltando data-piping helpers e LLM-narrators
    (`describe_entries`). Ritorna `(lp_tool, lp_obs)` del primo step ok
    productive trovato, oppure dell'ultimo step se nessun candidato.
    Ritorna `(None, {})` se la lista e' vuota.

    Preferenza speciale (turn live federvolley 7/5/2026): quando il
    last_productive e' un executor di "discovery" (find_urls) ma in
    history c'e' un executor di "lettura" (read_urls_html/_pdf/get_urls_text)
    con `text`/`entries` non-vuoti, preferisci il READ — il discovery e'
    metadati, il read e' contenuto.

    L'estrazione esiste per testabilita' (cf. test_auto_final_on_duplicate.py).
    """
    discovery_tools, read_tools = _AUTO_FINAL_PREFER_READ_OVER_DISCOVERY

    last_productive = None
    for prev in reversed(steps):
        res = getattr(prev, "result", None)
        if not isinstance(res, dict) or not res.get("ok"):
            continue
        if getattr(prev, "chosen_tool", None) in _AUTO_FINAL_SKIP_TOOLS:
            continue
        last_productive = prev
        break

    # Override discovery → read se applicabile (cf. ADR 0098)
    if last_productive is not None:
        lp_tool_check = getattr(last_productive, "chosen_tool", None)
        if lp_tool_check in discovery_tools:
            for prev in reversed(steps):
                res = getattr(prev, "result", None)
                if not isinstance(res, dict) or not res.get("ok"):
                    continue
                if getattr(prev, "chosen_tool", None) not in read_tools:
                    continue
                # Verifica che il read abbia contenuto non-banale
                if _read_obs_has_content(res):
                    last_productive = prev
                    break

    if last_productive is None and steps:
        last_productive = steps[-1]
    if last_productive is None:
        return (None, {})
    lp_tool = getattr(last_productive, "chosen_tool", None)
    lp_obs = getattr(last_productive, "result", None) or {}
    return (lp_tool, lp_obs if isinstance(lp_obs, dict) else {})


def _read_obs_has_content(obs: dict) -> bool:
    """Heuristic: l'observation di un read_urls_* ha contenuto utile?

    True se almeno una entry ha `text`/`body` con >= 200 char, oppure
    `summary`/`detail_md` non-vuoto, oppure `final_message_hint`
    descrittivo.
    """
    if not isinstance(obs, dict):
        return False
    for k in ("summary", "detail_md", "final_message_hint"):
        v = obs.get(k)
        if isinstance(v, str) and len(v.strip()) >= 80:
            return True
    entries = obs.get("entries") or obs.get("results") or []
    if isinstance(entries, list):
        for e in entries[:5]:
            if not isinstance(e, dict):
                continue
            for k in ("text", "body", "content"):
                v = e.get(k)
                if isinstance(v, str) and len(v.strip()) >= 200:
                    return True
    return False


def _extract_auto_final_count(lp_obs: dict) -> tuple[int | None, int | None]:
    """Estrae `(ok_count, n_above_threshold)` da un'observation productive.
    `n_above_threshold` solo se strettamente maggiore di `ok_count`.
    """
    if not isinstance(lp_obs, dict):
        return (None, None)
    ok_count = lp_obs.get("ok_count")
    if ok_count is None:
        for k in ("entries", "matches", "results", "files", "paths"):
            v = lp_obs.get(k)
            if isinstance(v, list):
                ok_count = len(v); break
    if ok_count is None:
        for k in ("n_entries", "item_count"):
            v = lp_obs.get(k)
            if isinstance(v, int):
                ok_count = v; break
    nat = lp_obs.get("n_above_threshold")
    if isinstance(nat, int) and isinstance(ok_count, int) and nat > ok_count:
        return (ok_count, nat)
    return (ok_count if isinstance(ok_count, int) else None, None)


def _format_auto_final_count(ok_count: int | None,
                             n_above_threshold: int | None) -> str:
    if not isinstance(ok_count, int):
        return msg("MSG_AUTO_FINAL_COUNT_UNKNOWN")
    if isinstance(n_above_threshold, int):
        return msg("MSG_AUTO_FINAL_COUNT_THRESHOLD",
                   n=ok_count, total=n_above_threshold)
    return msg("MSG_AUTO_FINAL_COUNT_PLAIN", n=ok_count)


def _predict_remaining_path(intent: dict | None, current_tool: str) -> list[str]:
    """Previsione euristica dei prossimi step dato intent + tool corrente.

    Conservative: ritorna al massimo 2 elementi (incertezza alta nei
    multi-step ReAct). Usata SOLO per il rendering del breadcrumb live
    (badge "futuri" muti); il path reale puo' divergere senza danni.

    Logica:
      - current_tool == "final_answer" → []
      - intent.verb in ACTION_VERBS (move/delete/send/...) → ["final_answer"]
        (l'azione di solito chiude il turno, niente describe dopo)
      - current_tool == "describe_entries" → ["final_answer"]
      - producer (read/find/list/get) + intent NON azione → ["describe_entries", "final_answer"]
      - default → ["final_answer"]
    """
    if current_tool == "final_answer":
        return []
    if current_tool == "describe_entries":
        return ["final_answer"]
    verb = (intent or {}).get("verb")
    if verb in _ACTION_VERBS_PRED:
        return ["final_answer"]
    if verb in ("read", "find", "list", "get"):
        return ["describe_entries", "final_answer"]
    return ["final_answer"]


def _lookup_field(obj, dotted):
    cur = obj
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None, f"campo '{part}' non trovato"
    return cur, None


def _consumer_match_arg(consumer_schema: dict | None, prev_entries: list) -> str | None:
    """Layer 4 (5/5/2026): rileva l'arg consumer naturale per una lista
    di entries, basandosi sulla convenzione I/O Metnos (plurale↔singolare).

    Esempio: find_urls produce entries=[{url, title, ...}], read_urls_html
    consuma `urls`. Match: arg `urls` → singolare `url` → presente in
    entries[0] → estrai entries[*].url.

    Caso degenere `prev_entries=[]`: non possiamo ispezionare entries[0],
    quindi prendiamo come consumer arg il primo array required dello schema
    (esclusi entries/from_step). Il caller iniettera' lista vuota — l'executor
    decide se ok_count=0 o errore di dominio.

    Ritorna il nome dell'arg consumer (string) o None se nessun match.
    Esclude `entries` stesso (target di fallback gestito dal caller).
    """
    if not isinstance(consumer_schema, dict) or not isinstance(prev_entries, list):
        return None
    props = consumer_schema.get("properties") or {}
    if not isinstance(props, dict):
        return None
    required = consumer_schema.get("required") or []
    if not isinstance(required, list):
        required = []

    # Caso degenere: lista vuota. Prendi il primo array required, escludendo
    # `entries`/`from_step`. Senza required, ritorna None → fallback `entries`.
    if not prev_entries:
        for arg_name in required:
            if arg_name in ("entries", "from_step"):
                continue
            spec = props.get(arg_name)
            if isinstance(spec, dict):
                t = spec.get("type")
                if t and t != "array":
                    continue
            return arg_name
        return None

    if not isinstance(prev_entries[0], dict):
        return None
    sample_keys = set(prev_entries[0].keys())
    # Ranking: prima l'arg required (semantica piu' forte), poi alfabetico stabile.
    candidates = []
    for arg_name, spec in props.items():
        if arg_name == "entries":
            continue  # gestito dal fallback
        if arg_name == "from_step":
            continue
        # Solo arg di tipo array: l'auto-espansione consegna una lista.
        if isinstance(spec, dict):
            t = spec.get("type")
            if t and t != "array":
                continue
        # `from_entries_key` (25/5/2026) §7.3: dichiarazione esplicita di
        # quale campo delle entries usare quando from_step espande in
        # quest'arg. Risolve l'ambiguita' quando il singular naïve di
        # arg_name punta a un campo non utile (es. move_messages.message_ids
        # → singular "message_id" matcha RFC822 header invece dello UID
        # IMAP usato dal backend). Manifest property opt-in.
        from_key = (spec.get("from_entries_key")
                    if isinstance(spec, dict) else None)
        if isinstance(from_key, str) and from_key in sample_keys:
            priority = 0 if arg_name in required else 1
            # boost priorita' per dichiarazione esplicita
            candidates.append((priority - 1, arg_name, from_key))
            continue
        # Singolare = arg.rstrip('s'). Match esatto contro un campo di entries[0].
        singular = arg_name[:-1] if arg_name.endswith("s") and len(arg_name) > 1 else arg_name
        if singular in sample_keys:
            priority = 0 if arg_name in required else 1
            candidates.append((priority, arg_name, singular))
    if not candidates:
        return None
    # Sort: prima i required (priority=0), tie-break alfabetico.
    candidates.sort(key=lambda t: (t[0], t[1]))
    return candidates[0][1]  # nome arg consumer


def _expand_nested_from_step(args: dict, history: list) -> tuple[dict, list]:
    """Espande pattern `from_step:N` ANNIDATI dentro liste args (8/5/2026).

    Caso d'uso: `paths_filter: ["from_step:2"]` — il PLANNER vuole passare
    i path dello step 2 come filtro, ma `resolve_from_step` standard
    riconosce solo top-level `from_step: int`. Senza questa espansione,
    il valore literal "from_step:2" finisce nella lista paths_filter →
    intersezione vuota → 0 entries (bug live bob al mare 8/5).

    Sostituisce ogni stringa `"from_step:N"` o `"from_step=N"` dentro
    valori lista degli args con la lista dei path estratti dallo step N.

    Per `paths_filter` (semantica: lista path assoluti) estrae
    `entry["path"]` da entries dello step N.
    Per altri arg lista, estrae il singolare se matcha (urls, ids, ecc).

    Idempotente: gli args senza pattern restano invariati. Errors append-only.
    """
    import re as _re
    PATTERN = _re.compile(r"^\s*from_step\s*[:=]\s*(\d+)\s*$", _re.IGNORECASE)
    errors: list[str] = []
    if not isinstance(args, dict):
        return args, errors
    new_args = dict(args)
    for arg_name, val in list(args.items()):
        if not isinstance(val, list):
            continue
        if not any(isinstance(v, str) and PATTERN.match(v) for v in val):
            continue
        # Trovato almeno un placeholder. Espandi.
        expanded: list = []
        for v in val:
            if isinstance(v, str) and (m := PATTERN.match(v)):
                step_n = int(m.group(1))
                if step_n < 1 or step_n > len(history):
                    errors.append(
                        f"{arg_name}: from_step={step_n} fuori range "
                        f"(history len={len(history)})"
                    )
                    continue
                step_obs = history[step_n - 1].get("observation") or {}
                step_entries = step_obs.get("entries") if isinstance(step_obs, dict) else None
                if not isinstance(step_entries, list):
                    errors.append(
                        f"{arg_name}: step {step_n} non ha entries (lista)"
                    )
                    continue
                # Mappa entry → scalare per arg_name. paths_filter → path.
                # Generico: se arg termina in 's', il singolare e' la chiave
                # da estrarre (paths_filter → ricava 'path' speciale).
                if arg_name == "paths_filter":
                    singular = "path"
                elif arg_name.endswith("s") and len(arg_name) > 1:
                    singular = arg_name[:-1]
                else:
                    singular = arg_name
                for e in step_entries:
                    if isinstance(e, dict) and singular in e:
                        sv = e[singular]
                        if sv is not None:
                            expanded.append(sv)
            else:
                expanded.append(v)
        new_args[arg_name] = expanded
    return new_args, errors


def resolve_from_step(args, history, consumer_schema=None):
    """Espande l'arg shortcut `from_step: int` in `entries: <list>` (o nell'arg
    consumer naturale) consultando lo scratchpad/observation dello step indicato.

    Pattern preferito (29/4/2026, refactor F1) per passare al tool una lista
    prodotta da uno step precedente: `from_step: 2` invece di `entries:
    "{{step2.entries}}"`. Lo schema-guided decoding emette un int, niente
    possibilita' di inventare dict — F1 risolto strutturalmente.

    Comportamento:
    - Se args ha `from_step: int`, recupera `history[N-1].observation`,
      cerca il primo campo lista canonico (entries/matches/items/results/
      files/paths).
    - Se `consumer_schema` e' fornito (Layer 4, 5/5/2026): tenta di mappare
      le entries sull'arg consumer naturale (es. read_urls_html consuma
      `urls` ↔ entries[*].url). Estrae i valori scalari e li passa
      sotto quell'arg. Fallback: inietta sotto `entries`.
    - Se args non ha from_step, no-op.
    - Errori (step inesistente, step senza lista, type sbagliato): list di
      stringhe istruttive.
    """
    errors = []
    if not isinstance(args, dict):
        return args, errors
    if "from_step" not in args:
        return args, errors
    fs = args.get("from_step")
    if isinstance(fs, str) and fs.isdigit():
        fs = int(fs)
    if not isinstance(fs, int):
        errors.append(
            f"from_step: deve essere un intero, ricevuto {type(fs).__name__} ({fs!r}). "
            f"Usa il numero dello step precedente che ha prodotto la lista (es. from_step=1)."
        )
        return args, errors
    # Args alternativi che identificano gia' il target senza bisogno di
    # from_step (10/5/2026 fix bug live: PLANNER spesso passa from_step
    # SUPERFLUO accanto a un name/names/all/paths/urls esplicito; non ha
    # senso bloccare l'esecuzione se l'utente ha gia' detto cosa fare).
    # 15/5/2026 estesa con event_ids/event_id/entries/to/to_user dopo
    # bug live: "cancella gli eventi con id X, Y, Z" → LLM emette
    # delete_events(from_step=1, event_ids=[...]) → from_step=1 al primo
    # step inesistente blocca pur con event_ids espliciti.
    _ALT_TARGET_KEYS = ("name", "names", "all", "paths", "urls", "ids",
                          "messages", "patterns",
                          "event_ids", "event_id", "entries",
                          "to", "to_user")
    _has_alt = any(
        k in args and args[k] not in (None, "", [], {})
        for k in _ALT_TARGET_KEYS
    )
    # SAFETY (17/5/2026): se l'utente ha gia' specificato un target esplicito
    # (event_id/paths/ids/...), from_step e' ridondante O contraddittorio.
    # Prima del fix, from_step espandeva SEMPRE prev_list in `entries`,
    # sovrascrivendo silenziosamente l'event_id esplicito. Bug live 16/5/2026:
    # "cancella evento abc-123" → PLANNER fa read_events(next-7d) +
    # delete_events(event_id="abc-123", from_step=1) → runtime ignora
    # event_id e cancella TUTTI i 9 eventi della lista step1. 15 eventi reali
    # bruciati in 4 turn di test. Fix §7.3: target esplicito vince SEMPRE
    # sul from_step (intent utente prevale su pipe pattern del PLANNER).
    if _has_alt:
        new_args = dict(args)
        new_args.pop("from_step", None)
        return new_args, errors
    if fs < 1 or fs > len(history):
        if _has_alt:
            new_args = dict(args)
            new_args.pop("from_step", None)
            return new_args, errors
        errors.append(
            f"from_step={fs}: step inesistente. Validi: 1..{len(history)}. "
            f"Indica lo step che nel turno corrente ha gia' prodotto una lista."
        )
        return args, errors
    step_obs = history[fs - 1].get("observation", {})
    src_tool = history[fs - 1].get("tool", "?")
    if not isinstance(step_obs, dict):
        if _has_alt:
            new_args = dict(args)
            new_args.pop("from_step", None)
            return new_args, errors
        errors.append(f"from_step={fs}: step '{src_tool}' senza observation valida")
        return args, errors
    list_field = None
    for k in ("entries", "matches", "items", "results", "files", "paths"):
        v = step_obs.get(k)
        if isinstance(v, list):
            list_field = k
            break
    if list_field is None:
        if _has_alt:
            new_args = dict(args)
            new_args.pop("from_step", None)
            return new_args, errors
        errors.append(
            f"from_step={fs}: step '{src_tool}' non ha prodotto una lista "
            f"(cerco entries/matches/items/results/files/paths). "
            f"Scegli uno step diverso o passa entries inline."
        )
        return args, errors
    prev_list = step_obs[list_field]
    new_args = dict(args)
    new_args.pop("from_step", None)
    # Layer 4 (5/5/2026): consumer-arg auto-espansione. Se lo schema consumer
    # ha un arg matchabile sul singolare di un campo di entries[0] e l'arg
    # consumer non e' gia' presente in args, estrae i valori scalari.
    consumer_arg = _consumer_match_arg(consumer_schema, prev_list)
    if consumer_arg and consumer_arg not in new_args:
        # Match key: prima `from_entries_key` (dichiarazione esplicita),
        # fallback singular naïve.
        match_key = None
        if isinstance(consumer_schema, dict):
            _spec = (consumer_schema.get("properties") or {}).get(consumer_arg)
            if isinstance(_spec, dict):
                fek = _spec.get("from_entries_key")
                if isinstance(fek, str) and fek:
                    match_key = fek
        if not match_key:
            match_key = (consumer_arg[:-1]
                         if consumer_arg.endswith("s") and len(consumer_arg) > 1
                         else consumer_arg)
        values = []
        for e in prev_list:
            if isinstance(e, dict) and match_key in e:
                v = e[match_key]
                if v is not None:
                    values.append(v)
        new_args[consumer_arg] = values
        return new_args, errors
    # Fallback storico: inietta sotto `entries` (target standard universale).
    new_args["entries"] = prev_list
    # Injection metadata upstream per executor che possono usare available_total
    # senza materializzare l'intera lista (es. compute_entries op=count su
    # find_files truncated: usa available_total invece di len(entries) capped).
    # Keys passate solo se presenti nello step_obs (no rumore).
    src_meta = step_obs.get("metadata") if isinstance(step_obs.get("metadata"), dict) else {}
    avail_total = step_obs.get("available_total") or src_meta.get("available_total")
    if isinstance(avail_total, int) and avail_total > len(prev_list):
        new_args["_from_step_total_hint"] = avail_total
        new_args["_from_step_truncated"] = True
    # Layer 5 (15/5/2026): secondary list reference. Pattern §7.3 per
    # executor bi-lista (filter_lists, filter_entries+overlap):
    #   `with_step=N`  → entries_b  (pattern canonical filter_lists)
    #   `overlap_step=N` → overlap_entries (alias filter_entries overlap)
    # Permette pipeline tipo:
    #   step A: filter_entries(from_step=read, where_starts_with="HLT")
    #   step B: filter_entries(from_step=read, where_starts_with="MNM")
    #   step C: filter_lists(op="overlap", from_step=A, with_step=B)
    for _ref_arg, _inject_arg in (
        ("with_step", "entries_b"),
        ("overlap_step", "overlap_entries"),
    ):
        _ref = new_args.pop(_ref_arg, None)
        if isinstance(_ref, str) and _ref.isdigit():
            _ref = int(_ref)
        if isinstance(_ref, int):
            if 1 <= _ref <= len(history):
                _other_obs = history[_ref - 1].get("observation", {})
                if isinstance(_other_obs, dict):
                    for k in ("entries", "matches", "items", "results"):
                        v = _other_obs.get(k)
                        if isinstance(v, list):
                            new_args[_inject_arg] = v
                            break
            else:
                errors.append(
                    f"{_ref_arg}={_ref}: step inesistente. "
                    f"Validi: 1..{len(history)}"
                )
    return new_args, errors


def resolve_references(args, history):
    errors = []

    def walk(node):
        if isinstance(node, str):
            m = _REF_RE.match(node.strip())
            if not m:
                return node
            step_num = int(m.group(1))
            field = m.group(2)
            if step_num < 1 or step_num > len(history):
                errors.append(f"reference {node}: step {step_num} non esiste")
                return node
            obs = history[step_num - 1]["observation"]
            value, err = _lookup_field(obs, field)
            if err:
                errors.append(f"reference {node}: {err}")
                return node
            return value
        if isinstance(node, dict):
            return {k: walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(x) for x in node]
        return node

    return walk(args), errors


_INLINE_LIST_THRESHOLD = 3  # >= N dict in lista inline => richiedi reference (default)
# Nomi di arg riservati al "data passing" fra step: per questi la soglia e' 1
# (qualunque dict inline e' rifiutato — devono arrivare via {{stepN.<field>}}).
# Coerente con i nomi che `scratchpad._summarize_structured` cerca come
# `chosen_key` e con `ref_hint` nel synthetic handle.
_REFERENCE_ARG_NAMES = frozenset({"entries", "items", "matches", "results", "files", "paths"})

_MALFORMED_REF_RE = re.compile(r"\{\{?step\d+\.[^{}]*[|?][^{}]*\}\}?|(?<!\{)\{step\d+\.[^{}]*\}(?!\})")


def check_inline_data(args):
    """Rifiuta args che contengono liste di dict inline non banali.

    Il pianificatore deve usare {{stepN.field}} per riusare output di step
    precedenti, mai re-incollare inline (fragile a quoting/escape e a token
    waste). Sotto la soglia (N<3) accettiamo dati costruiti ad hoc dal modello.
    """
    if not isinstance(args, dict):
        return None
    for k, v in args.items():
        if not isinstance(v, list):
            continue
        if not v or not isinstance(v[0], dict):
            continue
        # Per gli arg "data passing" canonici (entries, items, matches, ...)
        # qualunque dict inline e' rifiutato: devono arrivare via reference.
        # Per altri arg, soglia di tolleranza per dati costruiti ad hoc.
        threshold = 1 if k in _REFERENCE_ARG_NAMES else _INLINE_LIST_THRESHOLD
        if len(v) < threshold:
            continue
        return (
            f"INLINE_DATA_REJECTED: hai passato '{k}' come {len(v)} dict inline. "
            f"Per riusare l'output di un passo precedente DEVI usare la reference "
            f"'{{{{stepN.{k}}}}}' (es. '{{{{step1.{k}}}}}'). "
            f"Esempio corretto: {{ \"{k}\": \"{{{{step1.{k}}}}}\" }}. "
            f"Riformula la chiamata."
        )
    return None


def check_malformed_reference(args):
    """Rileva placeholder malformati (graffa singola, pipe, ternario).

    L'unica sintassi valida e' `{{stepN.field}}` puro. Tutto il resto e' un
    template engine che il runtime non interpreta: il LLM ha confuso la
    sintassi con Jinja o simili. Errore istruttivo, no loop.
    """
    if not isinstance(args, dict):
        return None
    def _scan(node, key_path=""):
        if isinstance(node, str):
            m = _MALFORMED_REF_RE.search(node)
            if m:
                return (
                    f"MALFORMED_REFERENCE: arg '{key_path}' contiene un placeholder non valido: "
                    f"'{m.group(0)}'. La SOLA sintassi ammessa e' `{{{{stepN.field}}}}` puro "
                    f"(doppie graffe, niente pipe `|`, niente ternario `?`, niente espressioni). "
                    f"Per filtrare/derivare dati usa uno step intermedio (es. chiama filter_entries "
                    f"come step a se') e poi referenzia il suo output."
                )
        elif isinstance(node, dict):
            for k, v in node.items():
                err = _scan(v, f"{key_path}.{k}" if key_path else k)
                if err:
                    return err
        elif isinstance(node, list):
            for i, v in enumerate(node):
                err = _scan(v, f"{key_path}[{i}]")
                if err:
                    return err
        return None
    return _scan(args)


def extract_step_refs(args) -> set[int]:
    """Ritorna l'insieme degli step numerici referenziati in args via {{stepN.field}}."""
    refs: set[int] = set()

    def walk(node):
        if isinstance(node, str):
            m = _REF_RE.match(node.strip())
            if m:
                refs.add(int(m.group(1)))
        elif isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(args)
    return refs


# --- Anti-allucinazione final_message (Bug B, 5/5/2026) ---------------------
#
# Caso live turn 23be1548: PLANNER ha emesso final_message "Sto effettuando
# una ricerca... ti aggiornerò non appena disponibili" — falso, nessun BG worker
# in coda. Self-check deterministico (regex, no LLM, §7.9): se il messaggio
# contiene verbi di promessa futura E nessuno step ok ha registrato un'azione,
# prepende notice "azione NON registrata". Notice additiva, non sostitutiva.

_HALLUCINATION_RE = re.compile(
    r"\b("
    # Forme "ti X-ò" (futuro semplice 1pps), con e senza accento finale
    r"ti (informer[oò'`]|aggiorner[oò'`]|far[oò'`] sapere|dir[oò'`]|"
    r"segnaler[oò'`]|comunicher[oò'`]|contatter[oò'`]|risponder[oò'`])"
    # Forme "sto X-ndo" (gerundio progressivo) tranne quando seguite da
    # una conferma esplicita di azione registrata.
    r"|sto (cercando|effettuando|controllando|monitorando|verificando|raccogliendo)"
    # Forme "appena X" (futuro condizionato a evento)
    r"|appena (avr[oò'`]|trovo|trovato|trovi|disponibili|disponibile|ricever[oò'`])"
    r")",
    re.IGNORECASE,
)

# Tool che REGISTRANO un'azione futura concreta. Se il PLANNER promette
# follow-up e ne ha chiamato uno con ok=true, la promessa e' supportata.
# Aggiungere qui ogni nuovo executor con effetto persistente registrato.
_REGISTERED_FUTURE_TOOLS = frozenset({
    "create_tasks",              # task ricorrente nel scheduler builtin
    "send_messages",             # mail/telegram in uscita
    "write_files",               # file scritti localmente
    "create_dirs",               # directory create
    "set_signatures",            # safety policy aggiornata
    "create_indices_image",      # indice persistente costruito
    "move_files",                # file spostati
    "move_messages",             # mail spostate
    "delete_files",              # file cancellati
    "delete_messages",           # mail cancellate
    "admin",                     # azioni privilegiate eseguite (mount, kill, ...)
})


# Pattern §2.8 honesty (17/5/2026): detection final_message «non trovato/
# not found» mentre uno step mutating ha realmente effettuato N>0 modifiche.
# Trigger originale: bug live 16/5/2026 in cui delete_events con
# event_id="abc-123"+from_step=1 ha cancellato 9 eventi reali della lista
# step1, e il LLM ha emesso final «evento abc-123 non trovato» — falso
# silent failure §2.8. Anche se fix #1 (`_resolve_from_step` SAFETY) previene
# il bug a monte, questo check resta come safety net per altre forme di
# divergenza tra obs ok/n_done e claim del LLM nel final.
_FALSE_NOT_FOUND_RE = re.compile(
    r"(non (?:e'|è) stato trovat[oai]|non trovat[oai]|"
    r"non (?:esiste|esistono|risulta|risultano)|"
    r"not found|does not exist|n[oa]t (?:been )?found)",
    re.IGNORECASE,
)
# SoT in pipeline_effects.py (condiviso con engine/dispatch, 12/6/2026).
from pipeline_effects import (  # noqa: E402
    MUTATING_TOOL_PREFIXES as _MUTATING_TOOL_PREFIXES,
)


def _detect_false_not_found(final_message: str | None, steps: list) -> dict | None:
    """Ritorna info-dict se il final_message dichiara «non trovato»
    contraddicendo uno step mutating ok=True con ok_count>=1.

    Returns:
        None se nessuna contraddizione, altrimenti
        {tool, ok_count} del primo step mutating contraddetto.
    """
    if not final_message:
        return None
    if not _FALSE_NOT_FOUND_RE.search(final_message):
        return None
    for s in steps or []:
        tool = getattr(s, "chosen_tool", None) or (
            s.get("chosen_tool") if isinstance(s, dict) else None
        )
        if not tool or not any(tool.startswith(p) for p in _MUTATING_TOOL_PREFIXES):
            continue
        result = getattr(s, "result", None)
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except Exception:
                continue
        if not isinstance(result, dict):
            continue
        if result.get("ok") is not True:
            continue
        ok_count = (result.get("ok_count")
                     or result.get("n_created")
                     or (len(result["results"])
                          if isinstance(result.get("results"), list)
                          else 0))
        try:
            ok_count = int(ok_count or 0)
        except (TypeError, ValueError):
            ok_count = 0
        if ok_count >= 1:
            return {"tool": tool, "ok_count": ok_count}
    return None


def _detect_unbacked_promise(final_message: str | None, steps: list) -> bool:
    """Ritorna True se il `final_message` contiene una promessa di azione
    futura ma nessuno step ok ha chiamato un tool che registra azioni.

    Determinismo §7.9: regex + lookup, niente LLM nel critical path.
    """
    if not final_message:
        return False
    if not _HALLUCINATION_RE.search(final_message):
        return False
    for s in steps or []:
        tool = getattr(s, "chosen_tool", None) or (
            s.get("chosen_tool") if isinstance(s, dict) else None
        )
        result = getattr(s, "result", None)
        if result is None and isinstance(s, dict):
            result = s.get("result")
        if (tool in _REGISTERED_FUTURE_TOOLS
                and isinstance(result, dict) and result.get("ok")):
            return False
    return True


# --- Anti-falso-successo su pipeline vuota (§2.8, 12/6/2026) ----------------
#
# Caso live: query schedulata maintenance github su 0 issue aperte — la
# pipeline find->read->filter->classify->write gira su 0 entries, write
# ok_count=0, MA il final_message narra «analizzato, salvato bozze pronte,
# notificato» (falso successo). Predicato deterministico §7.9: conta gli
# effetti REALI del turno dai result degli step; usato sia per la notice
# nel final (TurnLog.write) sia per la soppressione del push schedulato
# (recurring_tasks._scheduled_push_is_noop).
# Implementazione condivisa in `pipeline_effects.py` (12/6/2026): la stessa
# contabilità alimenta il criterio di EFFICACIA del fastpath L0
# (engine/dispatch._maybe_record_fastpath → ineffective_mutations).
from pipeline_effects import pipeline_effect_counts  # noqa: E402


# Claim di esito POSITIVO nel final (IT+EN). Negazioni escluse via
# lookbehind («non ho trovato» non matcha). Pattern conservativo: meglio
# un falso-negativo (nessuna notice) che marcare un final onesto.
_FALSE_SUCCESS_RE = re.compile(
    r"(?<!non )(?<!not )\b(?:"
    r"ho\s+(?:analizzat|trovat|salvat|inviat|creat|classificat|preparat|"
    r"scritt|spostat|cancellat|aggiornat|notificat|registrat)\w*"
    r"|(?:bozz\w+|rispost\w+|notific\w+)\s+(?:salvat|pront|inviat|creat)\w*"
    r"|(?:e'|è|sono)\s+stat[oaie]\s+(?:salvat|inviat|creat|notificat|"
    r"preparat|analizzat|classificat)\w*"
    r"|i\s+have\s+(?:analyz|found|saved|sent|creat|classifi|prepar|notifi)\w*"
    r"|(?:drafts?|replies|notifications?)\s+(?:saved|sent|ready|created)"
    r")",
    re.IGNORECASE,
)


def _detect_false_success(final_message: str | None, counts: dict | None) -> bool:
    """True se il final CLAIMA un esito positivo ma la pipeline e' VUOTA
    (0 items, 0 mutations, 0 failures su step contabili). §7.9 regex+conteggi.
    """
    if not final_message or not isinstance(counts, dict):
        return False
    if counts.get("failures") or counts.get("countable", 0) == 0:
        return False
    if counts.get("items", 0) > 0 or counts.get("mutations", 0) > 0:
        return False
    return bool(_FALSE_SUCCESS_RE.search(final_message))


# Claim di MUTAZIONE su un oggetto REALE (file/foglio/documento/evento/mail/...):
# distinto dal claim di lettura/sintesi. «ho creato il foglio» richiede una
# mutazione vera; «ho creato un riepilogo/elenco» (testo) NON e' una mutazione e
# NON deve matchare → object list stretta per evitare falsi positivi.
# NB (23/6): fra articolo e oggetto sono ammesse 0-2 parole (aggettivi):
# «creato un NUOVO foglio», «creato il MIO file di calcolo». Il bound {0,2}
# evita falsi match che scavalcano clausole. Bug live turno eventi: il synth
# diceva «Ho creato un nuovo foglio» su 0 mutazioni reali e il regex (che
# pretendeva il sostantivo subito dopo l'articolo) lo mancava -> §2.8 bucato.
_MUT_GAP = r"(?:\w+\s+){0,2}"  # 0-2 parole opzionali (aggettivi) fra art. e oggetto
_MUTATION_CLAIM_RE = re.compile(
    r"(?<!non )(?<!not )\b(?:"
    r"(?:creat|generat|salvat|scritt|prepar)\w*\s+(?:(?:il|lo|la|un|uno|una|"
    r"the|a|an)\s+)?" + _MUT_GAP + r"(?:foglio|file|document\w*|spreadsheet|"
    r"sheet|calendari\w*|event\w*|cartell\w*|folder|tabell\w*|csv|xlsx)"
    r"|(?:inviat|spedit|mandat|sent)\w*\s+(?:(?:il|la|un|the|a|an)\s+)?"
    + _MUT_GAP + r"(?:mail|email|messaggi\w*|message)"
    r"|(?:spostat|cancellat|eliminat|delet|mov)\w*\s+(?:(?:il|la|i|le|the)\s+)?"
    + _MUT_GAP + r"(?:file|mail|email|messaggi\w*|event\w*)"
    r"|(?:created|saved|wrote|generated|prepared)\s+(?:(?:the|a|an)\s+)?"
    + _MUT_GAP + r"(?:file|spreadsheet|sheet|document|calendar|event|folder|"
    r"table|csv)"
    r")", re.IGNORECASE)


_DEGENERATE_FINAL_RE = re.compile(
    r"\A[\(\[\s]*\d+(?:[.,]\d+)?\s*"
    r"(?:elementi|entries|elements|voci|risultati|results|item|items)?\s*[\)\]\s]*\Z",
    re.IGNORECASE)


def _is_degenerate_final(final_message: str | None) -> bool:
    """True se il final_message è DEGENERE: vuoto o un nudo conteggio/placeholder
    («0», «3», «(2 elementi)») che NON è una risposta in linguaggio naturale
    (§2.8). Sintomo di un template-render andato a vuoto (es. `${stepN.@count}`
    come intero messaggio, o un piano monco che lascia il conteggio scoperto).
    Mai mostrabile all'utente come esito. Deterministico §7.9."""
    if final_message is None:
        return True
    s = final_message.strip()
    if not s:
        return True
    return bool(_DEGENERATE_FINAL_RE.match(s))


def _detect_false_mutation(final_message: str | None, counts: dict | None) -> bool:
    """True se il final CLAIMA una MUTAZIONE su un oggetto reale (creato il
    foglio/inviato la mail/...) ma `counts.mutations==0` e nessuna mutazione e'
    stata tentata. Leggere/trovare elementi NON realizza una mutazione → il
    claim e' falso (§2.8). Indipendente da `items` (a differenza di
    _detect_false_success, che copre la pipeline TOTALMENTE vuota). §7.9."""
    if not final_message or not isinstance(counts, dict):
        return False
    if counts.get("mutations", 0) > 0 or counts.get("mutating_attempted"):
        return False
    # Negazione esplicita dell'azione («non ho creato», «non sono riuscito a
    # inviare», «couldn't create») → il final e' gia' onesto, non toccarlo.
    if re.search(r"non\s+(?:ho|sono\s+riuscit\w+\s+a|sono\s+stat\w+\s+in\s+grado"
                 r"\s+di)\s*\w*\s*(?:creat|inviat|spedit|salvat|generat|scritt|"
                 r"spostat|cancellat|prepar)"
                 r"|(?:couldn'?t|could\s+not|was\s+not\s+able\s+to|did\s*n'?t)"
                 r"\s+\w*\s*(?:creat|sen[dt]|sav|writ|generat|mov|delet|prepar)",
                 final_message, re.IGNORECASE):
        return False
    return bool(_MUTATION_CLAIM_RE.search(final_message))


def _offer_defer_dialog(*, query, device_id, device_name, actor, channel,
                        conversation_id, sender_id):
    """Fase 7 A.1: dialog yes_no «eseguo appena {device} torna online?».

    Riusa l'infrastruttura dialog_pending/gate: form web (INLINE_FORM),
    bottoni Telegram, submit → orchestration `defer_turn` → deferred_turns.
    Ritorna {final_message, expandable_caps} o None (fail-open → errore
    onesto classico)."""
    try:
        import uuid as _uuid
        import dialog_pending as _dp
        from messages import get as _m
        dialog_id = _uuid.uuid4().hex[:16]
        prompt = _m("MSG_DEFER_OFFER", device=device_name)
        state = {
            "dialog_id": dialog_id,
            "title": _m("MSG_DEFER_TITLE"),
            "dialog": [{
                "var": "decision", "prompt": prompt,
                "schema": {"kind": "choice", "choices": [
                    {"label": _m("MSG_BTN_APPROVE"), "value": "approve"},
                    {"label": _m("MSG_BTN_REJECT"), "value": "reject"},
                ]},
            }],
            "fmt": "form" if channel == "http" else "dialogue",
            "fmt_arg": "auto", "values_collected": {}, "step_index": 0,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "actor": actor, "channel": channel,
            "timeout_s": 3600, "completed": False, "cancelled": False,
            "on_complete": {
                "type": "defer_turn", "original_query": query,
                "device_id": device_id, "device_name": device_name,
                "conversation_id": conversation_id,
            },
        }
        _dp.save_pending(sender_id, dialog_id, state)
        final = prompt
        if state["fmt"] == "form":
            final += f"\n\nINLINE_FORM:/agent/dialog/{dialog_id}/form"
        return {"final_message": final, "expandable_caps": [{
            "kind": "get_inputs_response", "dialog_id": dialog_id,
            "step_total": 1, "fmt": state["fmt"],
            "sender_for_state": sender_id,
        }]}
    except Exception as _de:  # noqa: BLE001 — fail-open
        log = get_logger(__name__)
        log.warning("A.1 offer_defer fallita (fail-open): %r", _de)
        return None


def _undo_pending(executor, args, *, turn_id, actor, channel, device=""):
    """Scrittore del log undo al CHOKE-POINT di invocazione (§2.3/§4.5).

    Era nel loop del PLANNER legacy: la sua cancellazione (af6c7b8, 4/7) aveva
    lasciato l'undo SENZA scrittore — ogni mutazione era diventata non
    annullabile in silenzio (§2.8). Qui copre TUTTI i path (engine, device,
    futuri). `device` = id remoto quando l'op gira su un device: l'undo deve
    ribaltare sullo STESSO host (§2.9). Fail-open: l'undo non blocca il turno.
    Ritorna op_id o None."""
    if not getattr(executor, "revertible", False):
        return None
    if executor.name == "undo_last_turn":
        return None  # §4.5: mai undo dell'undo
    try:
        import uuid as _uuid
        _op = _uuid.uuid4().hex
        UndoLog().append_pending(
            _op, turn_id or "", executor.name, args, plan={},
            actor=actor or "host", channel=channel or "", device=device)
        return _op
    except Exception as _ue:
        log.warning("[undo] append_pending fallita (fail-open): %r", _ue)
        return None


def _undo_done(op_id, obs):
    """Chiude il record undo quando l'op ha REALMENTE mutato qualcosa.

    Bug live 981ddc9f (6/7): delete di 489 con 1 rifiuto (desktop.ini system
    file) → ok=False MA ok_count=488 e results[] pieni di blob. Gating su
    `ok` puro lasciava l'op ORFANA: 488 file cancellati e non annullabili
    (§2.8). Esito PARZIALE = comunque done (il reverse ribalta i results
    presenti); pending senza done resta = crashed/0-effetto."""
    if not op_id or not isinstance(obs, dict):
        return
    mutated = bool(obs.get("ok")) or bool(obs.get("ok_count")) \
        or bool(obs.get("results"))
    if not mutated:
        return
    try:
        UndoLog().append_done(op_id, obs)
    except Exception as _ue:
        log.warning("[undo] append_done fallita (fail-open): %r", _ue)


def _stringify_spreadsheet_floats(value):
    """Rende canonici i soli valori-cella float destinati a un device."""
    if isinstance(value, float):
        return format(value, ".15g")
    if isinstance(value, list):
        return [_stringify_spreadsheet_floats(item) for item in value]
    if isinstance(value, tuple):
        return [_stringify_spreadsheet_floats(item) for item in value]
    if isinstance(value, dict):
        return {key: _stringify_spreadsheet_floats(item)
                for key, item in value.items()}
    return value


def _invoke_executor_impl(executor, args, timeout_s=30, *, autonomy="supervised",
                          turn_id=None, actor=None, channel=None,
                          target_device=None):
    """Invoca un executor, opzionalmente in sandbox bubblewrap.

    Se `bwrap` e' installato e `METNOS_SANDBOX` non e' disabilitato,
    il comando viene wrappato; altrimenti gira come subprocess Python
    diretto (la pseudo-sandbox del runtime resta attiva: filtro path/host
    + Vaglio).

    `actor` / `channel` (12/5/2026): propagati come `METNOS_ACTOR` /
    `METNOS_CHANNEL` nell'env del subprocess. Servono a `get_inputs` per
    derivare un `sender_id` stabile (`<channel>:<actor>`) che e' chiave
    di storage per `dialog_pending`. Senza questa propagazione gli
    executor defaultano a `actor="host"`/`channel=""` e il consumer HTTP
    cerca lo state con un sender_id diverso da quello con cui e' stato
    salvato (bug live 12/5/2026: pipeline find_events_empty → get_inputs
    → send_messages perdeva il dialog state).
    """
    # --- Placement remoto (ADR 0034, design doc executor remoti §10/§14) ---
    # [placement] scope="device" nel manifest → l'executor NON gira qui:
    # invocazione firmata al device via coda (remote_exec). Default (nessun
    # [placement] o scope any/server) = esecuzione locale invariata.
    # Un-gate chat-driven placement (ADR 0034): il blocco parte se il manifest è
    # scope="device" OPPURE se il turno ha risolto un PC bersaglio dalla chat
    # (`target_device`) E l'executor dichiara `[placement] device_ok=true`.
    # Un target device NON impacchettabile (es. get_now con destinazione
    # appiccicosa) gira LOCALE, non fallisce. Senza target e senza scope=device →
    # esecuzione locale invariata (prod-safe §7.1).
    _plc = getattr(executor, "placement", None) or {}
    _plc_scope = (_plc.get("scope") or "").strip().lower()
    # F2 + rilievo #4 (2026-07-04): eleggibilità al device PURO MANIFEST-DRIVEN
    # (`[placement] device_ok = true`). La whitelist DEVICE_ELIGIBLE è stata
    # RIMOSSA (transizione finita: i 3 executor read-only dichiarano device_ok):
    # non c'è più fallback che possa mascherare un manifest incompleto. Un nuovo
    # executor remoto si dichiara nel manifest (single source of truth = catalogo).
    # Data-locality (co-location consumer↔producer, 7/7/2026): l'engine marca
    # gli step device_ok che consumano from_step da un producer girato sul
    # SERVER → restano sul server (i loro entries/path sono dati locali, non
    # esistono sul device). Il marker e' rimosso qui, prima di raggiungere
    # l'executor. NON tocca scope="device" (device-only per costruzione).
    _colocate_server = bool(args.pop("_colocate_server", False))
    # Pin-server provider-backed (10/7, bug B1): un'invocazione il cui backend
    # è una skill provider (client=google_workspace, suffisso _google_photos,
    # dispatcher gw...) gira SOLO sul server — le credenziali/CLI della skill
    # vivono lì, sul device il modulo non esiste per costruzione (C7). Senza
    # questo pin la destinazione APPICCICOSA mandava «cerca X su google drive»
    # sul PC → ERR_NOT_APPLICABLE. Stessa semantica del precedente stabilito:
    # target non impacchettabile → gira locale, non fallisce.
    import sandbox as _sandbox  # lazy: evita import circolare a module-load
    _skill_names = _sandbox.invocation_skills(executor, args)
    _device_ok = (bool(target_device) and bool(_plc.get("device_ok"))
                  and not _colocate_server and not _skill_names)
    if _plc_scope == "device" or _device_ok:
        import devices as _devices
        import placement as _placement
        import remote_exec as _remote
        # target_device = NOME device dalla chat → choose_placement L1.c lo abbina
        # (con gate connessione + piattaforma). Senza, resta la logica scope.
        # F3 (review 2026-07-04): filtra i device per PROPRIETARIO anche QUI (non
        # solo nella risoluzione chat), altrimenti un executor scope="device"
        # senza target esplicito vedrebbe i device di TUTTI gli utenti.
        # A3 (2026-07-04): owner ora e' un vero users.id → risolvi l'actor con il
        # resolver centrale (actor puo' essere 'host'/device_id/user).
        _who = _devices.owner_id_for_actor(actor)
        _devs_for_actor = [
            d for d in _devices.list_devices()
            if (getattr(d, "owner_user_id", "host") or "host") == _who]
        _intent = {"device": target_device} if target_device else None
        try:
            _target = _placement.choose_placement(
                _plc, _intent, _devs_for_actor,
                platforms=getattr(executor, "platforms", None),
                executor_name=executor.name)
        except _placement.PlacementError as e:
            from messages import get as _pmsg
            return {"ok": False, "error": _pmsg(e.code, **e.fmt),
                    "error_class": "placement"}
        if _target != _placement.SERVER:
            # Il wire firmato device vieta i float JSON: non hanno una forma
            # canonica byte-identica Rust/Python. Per un foglio sono VALORI di
            # cella, quindi al solo confine remoto li serializziamo come
            # decimali testuali stabili (0.95 -> "0.95"). Gli arg strutturali
            # degli altri executor restano intatti e continuano a fallire
            # chiusi se tentano di inviare float non canonici.
            remote_args = (_stringify_spreadsheet_floats(args)
                           if executor.name == "create_files_spreadsheet"
                           else args)
            _undo_op = _undo_pending(executor, remote_args, turn_id=turn_id,
                                     actor=actor, channel=channel,
                                     device=str(_target))
            from executor_scheduler import assigned_worker_environment
            _obs = _remote.invoke_remote(
                executor, remote_args, _target, timeout_s=timeout_s,
                turn_id=turn_id,
                env_injections=assigned_worker_environment(executor) or None,
                actor=actor or "", channel=channel or "")
            # Marca l'esecuzione REALE sul device: il tag/campo del turno si
            # basa su questo (mai un tag ottimistico su un'operazione locale).
            if isinstance(_obs, dict) and target_device:
                _obs.setdefault("_ran_on_device", target_device)
            _undo_done(_undo_op, _obs)
            return _obs

    _undo_op = _undo_pending(executor, args, turn_id=turn_id,
                             actor=actor, channel=channel, device="")

    # Isolamento multi-utente dell'undo (7/7/2026): garantisce `_actor` a
    # undo_last_turn QUI al choke-point — il fast-path «annulla» non passa
    # dall'injection dell'engine e arriverebbe senza identita' (un guest
    # filtrerebbe come 'host' e potrebbe ribaltare op altrui).
    if executor.name == "undo_last_turn" and "_actor" not in args:
        args = {**args, "_actor": actor or "host"}

    payload = json.dumps(args)
    base_cmd = [sys.executable, str(executor.code_path)]
    # Extras skill-backed (10/7, bug B2): senza, dal 9/7 (bubblewrap installato)
    # il token OAuth era INVISIBILE alla sandbox e i dispatcher `metnos:*`
    # senza rete → ogni op Google chiedeva il setup OAuth in loop. Bind della
    # SOLA home skill (RW: il refresh riscrive il token) + rete.
    _extra_rw, _force_net = _sandbox.skill_extras(_skill_names)
    # Local IMAP authority is capability-derived and account-scoped.  The
    # resolver returns only read-only mail credential files; it never exposes
    # the shared web-credential vault directory to an executor.
    _extra_ro, _mail_net = _sandbox.mail_extras(executor, args)
    # Dynamic filesystem inputs remain exact and capability-derived: only
    # signed ``fs:read`` hints such as ``arg:reference_images`` can add them.
    _extra_ro.extend(_sandbox.filesystem_extras(executor, args))
    # Reversible destructive executors write content-addressed backup blobs to
    # one runtime-managed directory.  Mount only this turn's leaf, derived
    # from the signed reverse pattern; never expose the shared history root.
    _extra_rw.extend(_sandbox.undo_history_extras(
        executor, turn_id=turn_id))
    _force_net = _force_net or _mail_net
    # I gate costruiti dentro executor di dominio devono persistere oltre il
    # /tmp privato di bwrap. Bind per-sender soltanto: mai l'intero archivio,
    # che puo' contenere input sensibili altrui.
    _extra_rw.extend(_sandbox.dialog_extras(
        executor, actor=actor or "host", channel=channel or ""))
    cmd = _sandbox.wrap_command(executor, base_cmd, autonomy=autonomy,
                                extra_ro=_extra_ro, extra_rw=_extra_rw,
                                force_net=_force_net)
    # PYTHONPATH augmentato: gli executor (specie quelli sintetizzati) importano
    # moduli runtime (mail_client, messages, platform_policy, ...) per nome.
    # Senza questo, il subprocess vede solo stdlib e fallisce con
    # ModuleNotFoundError. Vedi caso live 29/4/2026 sera (move_messages errore in
    # esecuzione anche dopo birth tests verdi).
    env = os.environ.copy()
    # Executor generated under the central execution contract receive one
    # runtime-owned item-worker budget. Legacy/handcrafted manifests without
    # [execution] keep their exact historical internal-concurrency behavior.
    from executor_scheduler import assigned_worker_environment
    env.update(assigned_worker_environment(executor))
    runtime_path = str(Path(__file__).resolve().parent)
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        runtime_path if not existing_pp
        else f"{runtime_path}{os.pathsep}{existing_pp}"
    )
    # METNOS_RUNTIME = path canonico della dir runtime/ usata da QUESTO daemon.
    # Gli executor (canonical e synthesized) la leggono per bootstrap sys.path
    # senza assunzioni di depth o location filesystem. ADR 0148 universal pattern.
    env["METNOS_RUNTIME"] = runtime_path
    # Esponi METNOS_TURN_ID al subprocess: gli executor revertibili lo usano
    # per nominare i blob backup deterministicamente
    # (`<HISTORY>/<turn_id>/blob/<sha256>.bin`).
    if turn_id:
        env["METNOS_TURN_ID"] = turn_id
    if actor:
        env["METNOS_ACTOR"] = actor
    if channel:
        env["METNOS_CHANNEL"] = channel
    # Unattended authority is task-scoped.  Propagate only the opaque task
    # identity; each domain reloads and validates its own persisted envelope.
    env.pop("METNOS_TASK_NAME", None)
    try:
        from treated_issues_guard import scheduled_task_name
        _scheduled_task = scheduled_task_name()
    except Exception:
        _scheduled_task = ""
    if _scheduled_task:
        env["METNOS_TASK_NAME"] = _scheduled_task
    _t_start = time.perf_counter()
    result = subprocess.run(
        cmd, input=payload, capture_output=True, text=True, timeout=timeout_s,
        env=env,
    )
    _elapsed_ms = int((time.perf_counter() - _t_start) * 1000)
    try:
        parsed_result = json.loads(result.stdout)
    except json.JSONDecodeError:
        parsed_result = {"ok": False,
                          "error": f"non-JSON output: {result.stdout!r}; "
                                   f"stderr: {result.stderr!r}",
                          "error_class": "non_json"}
    # Audit log per skill imports (mini-version Fase C, ADR 0140).
    # No-op per builtin handcrafted (provenance vuoto). Fail-silent.
    try:
        if getattr(executor, "is_imported", False):
            from skill_audit import audit_skill_invocation
            audit_skill_invocation(
                executor_name=executor.name,
                provenance=getattr(executor, "provenance", {}),
                args=args,
                result=parsed_result,
                elapsed_ms=_elapsed_ms,
                error_class=(parsed_result.get("error_class")
                              if isinstance(parsed_result, dict) else None),
            )
    except Exception:
        pass
    _undo_done(_undo_op, parsed_result)
    return parsed_result


def invoke_executor(executor, args, timeout_s=30, *, autonomy="supervised",
                    turn_id=None, actor=None, channel=None, target_device=None):
    """Universal scheduled choke-point for local and remote executors.

    The scheduler is synchronous and serial-first by default, so this wrapper
    adds bounded backpressure and metrics without changing planner ordering,
    arguments, outputs, placement, undo or sandbox behavior.
    """
    from executor_scheduler import concurrency_identity_for, invoke_scheduled

    return invoke_scheduled(
        executor,
        lambda: _invoke_executor_impl(
            executor, args, timeout_s=timeout_s, autonomy=autonomy,
            turn_id=turn_id, actor=actor, channel=channel,
            target_device=target_device,
        ),
        concurrency_identity=concurrency_identity_for(
            executor, args, target_device=target_device),
    )


def submit_executor(executor, args, timeout_s=30, *, autonomy="supervised",
                    turn_id=None, actor=None, channel=None,
                    target_device=None):
    """Submit one admitted executor call to the single central pool.

    This is deliberately the asynchronous twin of :func:`invoke_executor`:
    same implementation, arguments, placement and result contract.  Admission
    remains owned by ``ExecutorScheduler``; an unverified/class-0 executor is
    executed synchronously and returned as an already-completed Future.
    """
    from executor_scheduler import concurrency_identity_for, submit_scheduled

    return submit_scheduled(
        executor,
        lambda: _invoke_executor_impl(
            executor, args, timeout_s=timeout_s, autonomy=autonomy,
            turn_id=turn_id, actor=actor, channel=channel,
            target_device=target_device,
        ),
        concurrency_identity=concurrency_identity_for(
            executor, args, target_device=target_device),
    )


# --- Step + Turn log -------------------------------------------------------

@dataclass
class StepLog:
    step_num: int
    llm_text: str = ""
    llm_thinking: str = ""
    llm_in_tokens: int = 0
    llm_out_tokens: int = 0
    llm_latency_ms: int = 0
    chosen_tool: str = ""
    raw_args: dict = field(default_factory=dict)
    resolved_args: dict = field(default_factory=dict)
    validation_failures: list = field(default_factory=list)
    scope_violation: str | None = None
    vaglio_approved: bool = False
    result: dict = field(default_factory=dict)
    error: str | None = None
    # Telemetria fine (ADR 0080, 4/5/2026): le 4 sotto-componenti del
    # tempo di turno fuori dal PLANNER LLM. Default None: campi nuovi sui
    # turn JSONL, opzionali per non rompere la deserializzazione storica.
    intent_ms: int | None = None     # intent extractor (LLM, tipicamente solo step 1)
    vaglio_ms: int | None = None     # judge() (LLM o stub)
    exec_ms: int | None = None       # invoke_executor (sandbox + I/O)
    rerank_ms: int | None = None     # re_rank_for_step (post-step ok)
    prefilter_ms: int | None = None  # rank_adaptive (tipicamente solo step 1)
    # ADR 0099: True quando lo step e' iniettato deterministicamente dal
    # runtime (URL detection -> read_urls_html primo step), non scelto dal
    # PLANNER. Visibile in TurnLog JSONL per telemetria.
    seed_step: bool = False
    # the design guide §4.4 estesa (8/5/2026 notte): marker per cap_max_per_turn
    # rinforzato. Settato a "max_calls_per_turn" quando un tool non-action
    # viene chiamato >= DEFAULT_CAP_MAX_PER_TURN volte nel turno con args
    # near-identical (Jaccard >= 0.7).
    loop_break_total: str | None = None
    # ADR 0149 (18/5/2026): canonical_query emessa dal PLANNER come
    # by-product del tool_call JSON (solo se METNOS_CANONICAL_QUERY=1).
    # Persistita per telemetria e riusata da mnestoma.canonical_query_log.
    canonical_query: str = ""


# Pentade ADR 0161 ext: pattern strutturale per intent count.
# Detection deterministico §7.9 (no LLM): marker quantificatori interrogativi
# universali IT+EN. Usato da TurnLog._collect_truncation_notices e
# _expandable_caps per sopprimere rumore quando l'utente vuole solo un numero.
# _COUNT_QUANTIFIER_MARKERS migrato a detection_lexicon (concept substring
# `count.quantifier`, spazi significativi); vedi detection_lexicon_seed.


def _is_count_intent(intent_verb: str, user_query: str) -> bool:
    """Vero se l'intent e' un count: verb=compute (canonical §2.2) o pattern
    testuale quantificatore. Pattern §7.3 universale, no per-domain."""
    if (intent_verb or "").lower() == "compute":
        return True
    q = f" {(user_query or '').lower()} "
    return _detlex.match("count.quantifier", q)


@dataclass
class TurnLog:
    ts_start: float
    ts_end: float = 0.0
    user_query: str = ""
    turn_id: str = ""
    mode: str = ""
    candidates: list = field(default_factory=list)
    steps: list = field(default_factory=list)
    final_message: str = ""
    final_kind: str = ""
    # Lista di proposte di cap expand emerse dal turno: ogni elemento e'
    # {step_num, executor, args, used, available_total, suggested_args}.
    # Popolata in write() per i daemon channel che gestiscono dialog
    # stateful di conferma (the design guide 2.11 fase 2).
    expandable_caps: list = field(default_factory=list)
    # Attachments emessi dall ultimo step che ne ha prodotti (es.
    # find_images_indices). Lista di dict {kind, path, score, basename, caption}.
    # Il path crudo NON viene mai serializzato verso il client: i channel
    # adapter lo sostituiscono con URL signed (HTTP) o upload binario
    # (Telegram sendMediaGroup).
    attachments: list = field(default_factory=list)
    # Pending location request (regola §2-quater): metadata per il daemon che
    # deve renderizzare il prompt UI (Telegram bottoni / web form / CLI prompt).
    # Forma: {pending_id, goal, chat_id, original_query, expires_in_s}.
    pending_location: dict | None = None
    # Scrubbing credenziali nel turn log (ADR 0082, 4/5/2026): se la query
    # utente contiene pattern tipo `pwd:foo` / `user:bar`, prima di
    # serializzare il jsonl rimpiazziamo il valore con `<REDACTED:cred>`.
    # In RAM la credenziale resta finche' il turno e' attivo (per non
    # spezzare i tool che la consumano), ma su disco non si scrive mai.
    redacted: bool = False
    n_redacted_fields: int = 0

    # Anti-allucinazione final_message (Bug B, 5/5/2026): True se il
    # messaggio finale conteneva promesse di azione futura non registrate
    # da alcuno step ok. Notice additiva applicata in write(); il campo
    # serve da telemetria per metriche future (frequenza allucinazioni
    # PLANNER per turno).
    unbacked_promise_detected: bool = False

    # Conteggio deterministico degli effetti reali del turno (§2.8,
    # 12/6/2026): popolato in write() via `pipeline_effect_counts(steps)`.
    # None = nessuno step contabile. Consumato da (1) anti-falso-successo
    # nel final e (2) recurring_tasks per sopprimere il push schedulato
    # sui run a vuoto (0 items, 0 mutazioni).
    effect_counts: dict | None = None
    # True se la notice anti-falso-successo e' stata applicata (telemetria).
    false_success_detected: bool = False

    # Identita' utente + canale del turno (6/5/2026): necessari a write()
    # per orchestrare get_inputs sul cap-expand (ADR 0091 generalizzato).
    # Settati da run_turn ai parametri ricevuti.
    actor: str = "host"
    channel: str = ""
    # Destinazione risolta del turno (ADR 0034, chat-driven placement): nome del
    # device su cui è stato instradato, o None = server. Campo STRUTTURATO per
    # l'UI (chip «destinazione») oltre al marcatore 📍 già nel final_message.
    target_device: str | None = None
    # Verbo intent estratto dall'intent_extractor (25/5/2026): usato in
    # write() per detection mutating-intent-unfulfilled quando il PLANNER
    # non chiama mai un tool col verbo richiesto (es. utente «cancella X»
    # ma PLANNER fa solo read/list).
    intent_verb: str = ""
    # Conversation linking (8/5/2026): persisted nel JSONL per permettere
    # al chat HTTP di ricaricare la storia della conversazione dopo un tab
    # close. Sender HTTP setta dal body POST `conversation_id`. Telegram lo
    # ricava da `chat_id`. Vuoto = turn standalone (nessun grouping).
    conversation_id: str = ""

    # ADR 0149 (18/5/2026): idempotency flag per la registrazione di
    # canonical_query in mnestoma.canonical_query_log. Set True dopo la
    # prima write() che ha avuto chance di registrare → write() successive
    # nello stesso turno (es. cap-expand dialog) non duplicano.
    _canonical_recorded: bool = False

    # Mappa fallback executor → nome dell'arg che controlla il cap di output.
    # Coverage di tutti gli executor del catalog corrente (30/4/2026 sera) che
    # hanno un cap esplicito sui risultati. Da migrare a campo `cap_field` nel
    # manifest (estensione the design guide 2.7+2.11) per evitare hardcoding.
    # max_depth/max_batch_size esclusi: non sono cap di output, sono limiti
    # operativi (profondita' ricorsione, batch IMAP interno).
    _CAP_FIELD_FALLBACK = {
        "read_messages":      "max_total",
        "find_files":         "max_results",
        "list_dirs":          "max_results",
        "find_places":        "max_results",
        "filter_texts_lines": "max_results",
        "read_files":         "max_bytes",
        "read_files_csv":     "max_rows",
        "read_files_xlsx":    "max_rows",
    }

    # Counter di successo per verbo mutating (§2.6). Primo presente vince.
    # SoT in pipeline_effects.MUTATE_COUNT_KEYS (condiviso, 12/6/2026).
    from pipeline_effects import MUTATE_COUNT_KEYS as _MUTATE_SUCCESS_KEYS

    def _enforce_mutating_honesty(self):
        """§2.8 (mai negoziabile): un final che CLAIMA un esito mutating deve
        riflettere il result. Caso: lo step mutating ha ok=True ma success=0 E
        ha `not_found`/`failed` non vuoti (l'utente ha nominato target che NON
        esistono) → il final ottimistico del proposer ("...e' stato cancellato")
        e' DISONESTO. Sostituiscilo con un messaggio onesto deterministico.

        Complementare a `_detect_unfulfilled_mutating_intent` (che gestisce
        ok=False / nessuno step). Trigger STRETTO su not_found/failed: il caso
        legittimo "0 match" (cancella spam → 0 da spostare) NON viene toccato.
        §7.9 zero LLM, generale per delete/move/send/...
        """
        if not self.steps:
            return
        # §2.8 TIMEOUT su step MUTANTE (bug live 1ba8e2c4, 6/7: delete di
        # massa sul device uccisa a metà dalla deadline → esecuzione PARZIALE
        # reale, results/blob-mapping persi, undo orfano): l'esito è INCERTO
        # — mai dichiarare «nessuna operazione eseguita». Chiave nel catalogo
        # seed §7.13 (guard: test_seed_i18n_gate_keys.py).
        from pipeline_effects import MUTATING_TOOL_PREFIXES as _MUT_PREF
        for s in reversed(self.steps):
            res = s.result if isinstance(s.result, dict) else {}
            if (res.get("error_class") in ("timeout", "remote_timeout")
                    and not res.get("ok", False)
                    and any((s.chosen_tool or "").startswith(p)
                            for p in _MUT_PREF)):
                dev = (res.get("_ran_on_device")
                       or (res.get("_remote") or {}).get("device_id", "")[:12]
                       or (res.get("device_id") or "")[:12] or "server")
                self.final_message = msg("MSG_MUTATE_TIMEOUT_UNCERTAIN",
                                         tool=s.chosen_tool, device=dev)
                return
        mut = None
        top_level_failures = []
        for s in reversed(self.steps):
            res = s.result if isinstance(s.result, dict) else {}
            if not res:
                continue
            _is_mut_tool = any((s.chosen_tool or "").startswith(p)
                               for p in _MUT_PREF)
            # Alcuni confini (firma wire, placement, sandbox) falliscono prima
            # che l'executor possa costruire `failed[]`/`results`. Sono comunque
            # fallimenti mutanti reali e non devono sparire dietro il successo
            # di uno step precedente della stessa pipeline.
            if (_is_mut_tool and res.get("ok") is False
                    and not res.get("failed") and not res.get("not_found")
                    and (res.get("error") or res.get("error_code"))):
                top_level_failures.append({
                    "path": s.chosen_tool or "?",
                    "error": str(res.get("error")
                                 or res.get("error_code") or ""),
                })
            has_signals = (
                any(k in res for k in self._MUTATE_SUCCESS_KEYS)
                or "not_found" in res or "failed" in res)
            # Un mutante è giudicabile qui se ritorna `results` (trasformativi
            # §2.6: delete/move/write). CASO 2ter (7/7): un VERBO mutante con
            # effetto PARZIALE reale (conteggio>0) + falliti ma SENZA lista
            # `results` (send/share multi-account) — prima sfuggiva e i falliti
            # restavano MASCHERATI (§2.8). Il gancio è STRETTO al parziale:
            # un fallimento PIENO (conteggio 0/assente) NON si tocca qui, resta
            # alla via error_class che lo traduce via i18n (ERR_<CLASS>) — la
            # mia intercettazione grezza lo avrebbe reso «?: <classe tecnica>».
            _positive_count = any(
                isinstance(res.get(k), int) and res.get(k) > 0
                for k in self._MUTATE_SUCCESS_KEYS)
            _partial_no_results = (
                _is_mut_tool and _positive_count and "results" not in res
                and ("failed" in res or "not_found" in res))
            if ("results" in res and has_signals) or _partial_no_results:
                mut = res
                break
            # primo result puramente di lettura (entries, no segnali) → il turno
            # non e' mutating in coda: non intervenire. Un read con failed[]
            # (account SSL-fail) NON è mutante: lo gestisce _collect_failure_notices.
            if "entries" in res and not has_signals:
                return
        if mut is None:
            if not top_level_failures:
                return
            mut = {"results": [], "failed": top_level_failures}
        elif top_level_failures:
            mut = dict(mut)
            existing_failed = mut.get("failed") or []
            if not isinstance(existing_failed, list):
                existing_failed = [existing_failed]
            mut["failed"] = existing_failed + top_level_failures
        not_found = mut.get("not_found") or []
        failed = mut.get("failed") or []
        not_found = list(not_found) if isinstance(not_found, list) else [not_found]
        failed = failed if isinstance(failed, list) else [failed]
        if not not_found and not failed:
            return  # esito pieno: nessun claim da correggere
        # Split failed[] per error_code strutturato: *_NOT_FOUND = target
        # inesistente («non trovato» legittimo); ERR_REFUSE_MOVE = SKIP di
        # PROTEZIONE deliberato (system file, §2.9/platform_policy — non è un
        # fallimento: presentarlo come «fallito» allarmava, Roberto 6/7 sera);
        # il RESTO = fallimento reale su un target che ESISTE.
        real_failed, protected = [], []
        for it in failed:
            code = (it.get("error_code") or "") if isinstance(it, dict) else ""
            if code.endswith("_NOT_FOUND"):
                not_found.append(it)
            elif code == "ERR_REFUSE_MOVE":
                protected.append(it)
            else:
                real_failed.append(it)
        success = None
        for k in self._MUTATE_SUCCESS_KEYS:
            if isinstance(mut.get(k), int):
                success = mut[k]
                break
        if success is None:
            results = mut.get("results") or []
            success = sum(1 for r in results
                          if not isinstance(r, dict) or r.get("ok", True))

        def _ids(lst):
            out = []
            for it in lst:
                if isinstance(it, dict):
                    # `to`/`account`/`recipient`: label dei mutanti non-fs
                    # (send/share multi-account) — senza, il destinatario
                    # fallito compariva come «?» (2ter, 7/7).
                    out.append(str(it.get("id") or it.get("event_id")
                                   or it.get("path") or it.get("to")
                                   or it.get("recipient") or it.get("account")
                                   or it.get("error") or it))
                else:
                    out.append(str(it))
            return ", ".join(out[:8])

        def _describe_failure(it):
            """«label»: motivo. Ri-renderizza via i18n quando l'item porta
            error_code + parametri strutturati (i failed[] REMOTI arrivano col
            fallback grezzo code+kv del device: qui si ri-umanizza, §7.13);
            altrimenti usa l'error text così com'è."""
            if not isinstance(it, dict):
                return str(it)
            label = str(it.get("path") or it.get("src") or it.get("id")
                        or it.get("event_id") or it.get("uid")
                        or it.get("to") or it.get("recipient")
                        or it.get("account") or "?")
            reason = ""
            code = it.get("error_code") or ""
            if code:
                params = {k: v for k, v in it.items()
                          if isinstance(v, (str, int, float))}
                try:
                    cand = msg(code, **params)
                except Exception:
                    cand = ""
                if cand and "{" not in cand and not cand.startswith("<missing:"):
                    reason = cand
            if not reason:
                reason = str(it.get("error") or code or "")
            if label != "?" and label in reason:
                return reason
            return f"«{label}»: {reason}" if reason else f"«{label}»"

        def _failures_detail(lst, cap=3):
            parts = [_describe_failure(it) for it in lst[:cap]]
            if len(lst) > cap:
                parts.append(f"+{len(lst) - cap}")
            return "; ".join(parts)

        # §7.13: messaggi risolti via i18n DB nella lingua dell'istanza. Le
        # chiavi vivono nel catalogo seed (install/data/i18n_seed.sqlite,
        # IT+EN), NON in-linea nel sorgente. Guard di presenza:
        # runtime/tests/test_seed_i18n_gate_keys.py.
        _prot_note = ""
        if protected:
            _prot_note = msg("MSG_MUTATE_PROTECTED_SKIPPED",
                             n=len(protected),
                             detail=_ids(protected))
        if success == 0:
            if real_failed:
                detail = _failures_detail(real_failed)
                if not_found:
                    detail += " | " + msg(
                        "MSG_MUTATE_PARTIAL", n=len(not_found),
                        detail=_ids(not_found))
                self.final_message = msg("MSG_MUTATE_FAILED_NONE_DONE",
                                         detail=detail)
            elif not_found:
                self.final_message = msg("MSG_MUTATE_NONE_DONE",
                                         detail=_ids(not_found))
            elif protected:
                # SOLO skip di protezione (es. delete su una dir che
                # contiene solo desktop.ini): esito informativo, non allarme.
                self.final_message = msg("MSG_MUTATE_FAILED_NONE_DONE",
                                         detail=_prot_note)
                return
            if _prot_note and _prot_note not in (self.final_message or ""):
                self.final_message = ((self.final_message or "").rstrip()
                                      + "\n\n" + _prot_note).strip()
        else:
            parts = [p for p in (_ids(not_found),
                                 _failures_detail(real_failed)) if p]
            if parts:
                notice = msg("MSG_MUTATE_PARTIAL",
                             n=len(not_found) + len(real_failed),
                             detail="; ".join(parts))
                if notice not in (self.final_message or ""):
                    self.final_message = ((self.final_message or "").rstrip()
                                          + "\n\n" + notice).strip()
            if _prot_note and _prot_note not in (self.final_message or ""):
                self.final_message = ((self.final_message or "").rstrip()
                                      + "\n\n" + _prot_note).strip()

    def _collect_failure_notices(self):
        """§2.8 (mai silent failure): rende VISIBILI i fallimenti per-item/account
        (`failed[]`/`fail_count`) di uno step producer. Senza questo, account IMAP
        falliti (es. SSL 'bad record mac') → il conteggio cade a 0 e il final
        dichiara 'Hai 0 mail' mentre in realtà N account NON sono stati controllati
        (bug 1/6). Generale per qualunque executor con `failed[]`."""
        notices, seen = [], set()
        for s in self.steps:
            res = s.result if isinstance(s.result, dict) else {}
            failed = res.get("failed")
            if not (isinstance(failed, list) and failed):
                continue
            # Solo PRODUCER (read/find/get/list) con failed[] per-item (es.
            # read_messages account SSL-fail). I fallimenti dei verbi MUTATING
            # (send/write/move/delete) hanno semantica diversa ("non inviato",
            # non "non controllato") e sono resi onesti dal blocco humanize
            # error_class (§2.8) — qui li si skippa per non oscurarlo con un
            # avviso da-producer fuorviante (bug no_verified_channel 8/6).
            _verb = (s.chosen_tool or "").split("_")[0]
            if _verb not in ("read", "find", "get", "list"):
                continue
            # Skip i risultati MUTATING (§2.6: hanno `results`): gestiti da
            # _enforce_mutating_honesty → evita doppio-avviso.
            if "results" in res:
                continue
            labels = []
            for f in failed[:6]:
                if isinstance(f, dict):
                    lab = (f.get("account") or f.get("url") or f.get("path")
                           or f.get("message_id") or f.get("id") or f.get("uid")
                           or f.get("to") or f.get("index"))
                    text = str(lab) if lab is not None else "?"
                    if len(text) > 160:
                        text = f"{text[:96]}…{text[-63:]}"
                    labels.append(text)
            n = len(failed)
            key = (s.chosen_tool, n, tuple(labels))
            if key in seen:
                continue
            seen.add(key)
            # §7.13: chiave nel catalogo seed, risolta via msg() nella lingua
            # istanza (guard: test_seed_i18n_gate_keys.py).
            notices.append(msg("MSG_PARTIAL_ITEM_FAILURE", n=n,
                               labels=", ".join(labels) or "?"))
        return notices

    def _collect_expandable_caps(self):
        """Per ogni step truncated dove conosciamo (a) il cap_field e (b)
        l'available_total > used, costruisce una proposta di re-run con
        cap esteso. Convenzione: l'executor puo' esporre `cap_field` e
        `cap_value` direttamente nel result; altrimenti fallback su mappa
        interna per nome executor.

        Eccezione (ADR 0090, get_inputs): se uno step ha emesso
        `expandable_caps` con kind specifici (es. `get_inputs_response`),
        questi vengono propagati senza richiedere il pattern truncated.
        Sono proposte non di cap-expand, ma di dialogo strutturato.
        """
        proposals = []
        seen = set()
        # Pentade ADR 0161 ext: skip cap-expand per query count.
        # Query con quantificatore → utente vuole il numero, dialog e' rumore.
        if _is_count_intent(getattr(self, "intent_verb", ""),
                             getattr(self, "user_query", "")):
            return proposals
        # ── Pass 1: propaga expandable_caps custom (ADR 0090) ──
        for s in self.steps:
            res = s.result if isinstance(s.result, dict) else {}
            custom = res.get("expandable_caps") or []
            if not isinstance(custom, list):
                continue
            for item in custom:
                if isinstance(item, dict) and item.get("kind"):
                    enriched = dict(item)
                    enriched.setdefault("step_num", s.step_num)
                    enriched.setdefault("executor", s.chosen_tool)
                    proposals.append(enriched)
        # 10/5/2026 fix UX: skip cap-expand per output narrativi/non-azionabili.
        # describe_entries produce TESTO sintetizzato (LLM): allargare il cap
        # non aiuta l'utente, riallarga solo il contesto LLM. Stesso per
        # find_urls quando used >= 10 (un umano non legge 5000 link).
        _NARRATIVE_NO_CAP_EXPAND = {"describe", "URL"}

        # 15/5/2026 fix UX query aggregate: se l'ULTIMO step e' `final_answer`
        # synthetic E un producer precedente espone `metadata.total_count`
        # E quel numero compare nel final_message → l'utente ha gia' la
        # risposta aggregata, offrire "allargo a 2000?" e' rumore.
        # Se invece il numero NON compare (list-style: "Ho trovato N",
        # ma N riflette `used` non `total_count`), cap_expand resta
        # utile. §7.3 general-purpose: discrimina aggregate vs list
        # via signal autoritativo (numero stesso), non keyword.
        _final_close = bool(
            self.steps and self.steps[-1].chosen_tool == "final_answer"
        )
        if _final_close:
            _fm = (self.final_message or "").replace(".", "").replace(",", "")
            for s in self.steps:
                res = s.result if isinstance(s.result, dict) else {}
                md = res.get("metadata")
                if not isinstance(md, dict):
                    continue
                tc = md.get("total_count")
                if isinstance(tc, int) and tc > 0 and str(tc) in _fm:
                    return proposals  # numero aggregato gia' nel final

        for s in self.steps:
            res = s.result if isinstance(s.result, dict) else {}
            if not res.get("truncated"):
                continue
            # Skip explicit user-set cap (truncated_intentional, ADR 0062).
            if res.get("truncated_intentional"):
                continue
            # Skip producer a RANKING (output_policy modi G/W/TG): il top-K E'
            # la risposta, available_total e' solo informazione → "allargo?" e'
            # rumore E lascia un dialog pendente che mangia la query successiva
            # (bug 31/5: ricerche foto/web). §7.9 deterministico, generale
            # (copre find_images_indices/find_urls e futuri ranked producer).
            try:
                from output_policy import (
                    resolve as _op_resolve, RANKED_MODES as _RANKED)
                if _op_resolve(getattr(self, "intent_verb", ""),
                               s.chosen_tool,
                               getattr(self, "user_query", "")
                               ).get("mode") in _RANKED:
                    continue
            except Exception as _e:
                log.debug("output_policy ranked-skip noop: %r", _e)
            used = res.get("used") or res.get("ok_count") or res.get("count")
            available = res.get("available_total")
            if not available or not used or available <= used:
                continue
            tw = res.get("truncated_what") or ""
            if tw in _NARRATIVE_NO_CAP_EXPAND and used >= 10:
                continue
            if s.chosen_tool == "describe_entries":
                continue
            # Qualifier `_empty` (ADR 0127): l'executor ritorna ESATTAMENTE
            # quanto chiesto dall'utente (es. find_events_empty max_results=3).
            # Cap intenzionale per semantica del verbo, no overflow inquiry.
            # §7.3 detection generale (qualifier suffix), no whitelist hardcoded.
            if s.chosen_tool and s.chosen_tool.endswith("_empty"):
                continue
            cap_field = res.get("cap_field") or self._CAP_FIELD_FALLBACK.get(s.chosen_tool)
            if not cap_field:
                continue
            cap_current = res.get("cap_value") or used  # used coincide col cap usato
            # suggested = available_total + 10% buffer, arrotondato per eccesso
            # a una potenza "umana" (200, 500, 1000, 2000, 5000, ...).
            target = int(available * 1.1)
            for step_size in (200, 500, 1000, 2000, 5000, 10000):
                if step_size >= target:
                    suggested_cap = step_size; break
            else:
                suggested_cap = target
            key = (s.chosen_tool, cap_field, cap_current)
            if key in seen:
                continue
            seen.add(key)
            new_args = dict(s.raw_args or {})
            new_args[cap_field] = suggested_cap
            proposals.append({
                "kind": "cap_expand",
                "step_num": s.step_num,
                "executor": s.chosen_tool,
                "cap_field": cap_field,
                "cap_current": cap_current,
                "cap_suggested": suggested_cap,
                "used": used,
                "available_total": available,
                "args_original": s.raw_args,
                "args_suggested": new_args,
            })
        return proposals

    def _orchestrate_cap_expand_dialog(self, proposal: dict) -> None:
        """Sintetizza un get_inputs (1 step yes_no) per chiedere conferma
        d'allargamento cap, in luogo della vecchia stringa "rispondi sì".

        Side-effect: sostituisce `self.final_message` con il
        `final_message_hint` del get_inputs orchestrato e
        `self.expandable_caps` con la entry `get_inputs_response`. Il
        canale (Telegram daemon o HTTP) consume gia' `get_inputs_response`
        come dialogo persistente in `dialog_pending` (mode 0600), quindi
        sopravvive al restart del daemon.

        Idempotente: se l'orchestration fallisce (es. dialog_pending
        write error), preserva il final_message originale e logga il warning.
        """
        executor = proposal.get("executor") or ""
        cap_field = proposal.get("cap_field") or ""
        cap_suggested = proposal.get("cap_suggested")
        available_total = proposal.get("available_total")
        used = proposal.get("used")
        args_suggested = proposal.get("args_suggested") or {}
        if not executor or not cap_field or cap_suggested is None:
            return  # proposta malformata: lascia stato com'e'

        # Etichetta leggibile per la preview ("processi", "foto", "righe")
        # — dal `truncated_what` se l'executor lo dichiara, altrimenti
        # fallback i18n.
        preview_label = msg("MSG_TRUNCATED_DEFAULT_WHAT")
        for s in self.steps:
            if s.chosen_tool == executor:
                res = s.result if isinstance(s.result, dict) else {}
                tw = res.get("truncated_what")
                if isinstance(tw, str) and tw:
                    preview_label = tw
                break

        prompt = msg("MSG_CAP_EXPAND_ASK", used=used, label=preview_label,
                     available_total=available_total,
                     cap_suggested=cap_suggested)
        title = msg("MSG_CAP_EXPAND_TITLE")
        dialog = [{
            "var": "confirm",
            "prompt": prompt,
            "schema": {"kind": "yes_no"},
        }]
        on_complete = {
            "type": "expand_cap_and_resume",
            "executor": executor,
            "cap_field": cap_field,
            "cap_suggested": cap_suggested,
            "args_suggested": args_suggested,
            "preview_label": preview_label,
        }

        sender_id = (
            f"{self.channel}:{self.actor}" if self.channel
            else (self.actor or "host")
        )
        try:
            # runtime/ già su sys.path (agent_runtime VIVE in runtime/).
            import orchestration as _orch
            res = _orch.invoke_get_inputs_internal(
                sender_id=sender_id,
                title=title,
                description=None,
                dialog=dialog,
                fmt="auto",
                on_complete=on_complete,
                actor=self.actor or "host",
                channel=self.channel or None,
                timeout_s=600,
            )
        except (ImportError, OSError, RuntimeError, ValueError, TypeError):
            log.exception("cap-expand orchestration fallita; lascio prompt vuoto")
            return

        if not res.get("ok"):
            log.warning("cap-expand orchestration ko: %s", res.get("error"))
            return

        hint = res.get("final_message_hint") or ""
        if hint:
            self.final_message = ((self.final_message or "").rstrip()
                                  + "\n\n" + hint).strip()
        # Sostituisci expandable_caps con la entry get_inputs_response cosi'
        # i channel adapter prendono il cammino gia' rodato (dialog_pending +
        # process_completion_callback) invece del bespoke cap_pending.
        new_caps = []
        for c in res.get("expandable_caps") or []:
            if isinstance(c, dict):
                enriched = dict(c)
                enriched.setdefault("step_num", proposal.get("step_num"))
                enriched.setdefault("executor", executor)
                new_caps.append(enriched)
        if new_caps:
            self.expandable_caps = new_caps

    def _append_images_results_if_any(self) -> None:
        """Se in history c'e' uno step find_images_indices/find_persons_indices
        ok con entries, accoda al final_message la lista path reali (max 15).
        Previene hallucination LLM (PLANNER inventa path "IMG_001.jpg..."
        invece di leggere entries reali): l'append deterministico ancorato
        ai path effettivi sostituisce/integra il messaggio LLM."""
        import os
        seen_paths: set = set()
        all_entries: list = []
        for s in self.steps:
            if s.chosen_tool not in ("find_images_indices", "find_persons_indices"):
                continue
            res = s.result if isinstance(s.result, dict) else {}
            if not res.get("ok"):
                continue
            for e in res.get("entries") or []:
                if not isinstance(e, dict):
                    continue
                p = e.get("path")
                if not p or p in seen_paths:
                    continue
                seen_paths.add(p)
                all_entries.append(e)
        if not all_entries:
            return
        max_show = 15
        n_total = len(all_entries)
        sample = all_entries[:max_show]
        # Se il LLM ha gia' incluso path corretti, non duplicare (idempotente).
        existing = self.final_message or ""
        already_in_msg = sum(
            1 for e in sample
            if os.path.basename(e.get("path", "")) in existing
        )
        if already_in_msg >= len(sample) // 2 and already_in_msg > 0:
            return  # gia' presente in modo significativo, skip
        # Solo basename nel testo: caption VLM visibile in gallery viewer
        # (hover tooltip + overlay HTML), NON duplicata nel final testuale
        # (Roberto 15/5/2026: troppo verbose).
        lines = ["", "", "**Risultati:**"]
        for e in sample:
            basename = os.path.basename(e.get("path", ""))
            lines.append(f"- `{basename}`")
        if n_total > max_show:
            lines.append(f"_... e altre {n_total - max_show} foto._")
        self.final_message = (existing.rstrip() + "\n".join(lines))

    def _append_search_results_if_any(self) -> None:
        """Se in history esistono step `find_urls` ok con entries,
        AGGREGA tutti i risultati, dedup per URL, ordina per score desc,
        appende al final_message la lista markdown cliccabile via
        `output_format.format_search_results`.

        UX fix 10/5/2026 «risposta educata ma inutile» + «PLANNER pesca
        lo step sbagliato»: aggregazione cross-step + dedup garantisce
        che le URL piu' rilevanti emergano anche se PLANNER ha invocato
        find_urls multiple volte con varianti (alcune buone, alcune
        rumorose).
        """
        all_entries_by_url: dict[str, dict] = {}
        all_docs_by_url: dict[str, dict] = {}
        for s in self.steps:
            if s.chosen_tool != "find_urls":
                continue
            res = s.result if isinstance(s.result, dict) else {}
            if not res.get("ok"):
                continue
            for e in res.get("entries") or []:
                if not isinstance(e, dict):
                    continue
                url = e.get("url")
                if not isinstance(url, str) or not url:
                    continue
                # Mantieni la entry con score piu' alto se duplicata.
                prev = all_entries_by_url.get(url)
                cur_score = e.get("score") or 0
                prev_score = (prev or {}).get("score") or 0
                if prev is None or cur_score > prev_score:
                    all_entries_by_url[url] = e
            for d in res.get("discovered_documents") or []:
                if not isinstance(d, dict):
                    continue
                url = d.get("url")
                if not isinstance(url, str) or not url:
                    continue
                if url not in all_docs_by_url:
                    all_docs_by_url[url] = d

        if not all_entries_by_url and not all_docs_by_url:
            return

        # Restrizione 20/5/2026: se nel turno c'e' un read_urls_html
        # andato a buon fine, mostriamo come fonti SOLO gli URL su cui
        # l'estrazione testo e' davvero stata fatta. I link di find_urls
        # rimasti fuori erano candidati grezzi (titolo + snippet), non
        # fonti del riassunto. Comportamento default = "riassunto + fonti
        # effettive"; lista cliccabile grezza si vede solo nel branch
        # list-intent (vedi describe_entries interceptor).
        _fetched_urls: set[str] = set()
        for s in self.steps:
            if s.chosen_tool != "read_urls_html":
                continue
            res = s.result if isinstance(s.result, dict) else {}
            if not res.get("ok"):
                continue
            for e in res.get("entries") or []:
                if not isinstance(e, dict):
                    continue
                url = e.get("url")
                body = e.get("body_text")
                if (isinstance(url, str) and url
                        and isinstance(body, str)
                        and len(body.strip()) >= 100):
                    _fetched_urls.add(url)
        if _fetched_urls:
            # Ristretto: solo gli URL effettivamente processati.
            all_entries_by_url = {
                u: e for u, e in all_entries_by_url.items()
                if u in _fetched_urls
            }
            all_docs_by_url = {
                u: d for u, d in all_docs_by_url.items()
                if u in _fetched_urls
            }
            if not all_entries_by_url and not all_docs_by_url:
                return

        # Noise tail elimination (Roberto, 10/5/2026):
        # 1) Score-relative threshold STRETTO 30% del top (era 5% troppo
        #    morbido — su top 35 lasciava entrare migliaia con score 1-5).
        #    Top tipico BM25 web 30-50 -> threshold 9-15; entries 0-9
        #    droppate. LLM rerank top 1 -> threshold 0.3.
        # 2) Hard cap 30 entries finali post-threshold. Un umano non
        #    legge oltre 30 link. Cap_expand cap_field=top_k esposto
        #    al PLANNER per allargare on-demand.
        scores = [
            e.get("score") for e in all_entries_by_url.values()
            if isinstance(e.get("score"), (int, float))
        ]
        if scores:
            top_score = max(scores)
            if top_score > 0:
                threshold = top_score * 0.30
                all_entries_by_url = {
                    u: e for u, e in all_entries_by_url.items()
                    if isinstance(e.get("score"), (int, float))
                    and e["score"] >= threshold
                }

        # Drop entries da motori di ricerca (privacy/terms/help/...) —
        # mai utili come "risultato" anche se BFS li ha pescati come
        # cross-link. Stesso whitelist di find_urls._is_search_engine_home
        # (host esatti, NON match parziale per evitare false positive).
        _SEARCH_ENGINE_HOSTS_DROP = {
            "google.com", "google.it", "google.fr", "google.de", "google.es",
            "google.co.uk", "google.ch", "google.at", "google.nl", "google.be",
            "www.google.com", "www.google.it", "www.google.fr",
            "www.google.de", "www.google.es", "www.google.co.uk",
            "www.google.ch", "www.google.at", "www.google.nl",
            "policies.google.com", "support.google.com",
            "bing.com", "www.bing.com",
            "duckduckgo.com", "www.duckduckgo.com",
            "search.brave.com", "brave.com",
            "yandex.com", "yandex.ru",
            "yahoo.com", "search.yahoo.com",
            "ecosia.org", "www.ecosia.org",
            "qwant.com", "www.qwant.com",
            "startpage.com", "www.startpage.com",
        }
        from urllib.parse import urlparse as _urlparse

        def _is_search_engine_url(url: str) -> bool:
            try:
                host = (_urlparse(url).hostname or "").lower()
            except Exception:
                return False
            return host in _SEARCH_ENGINE_HOSTS_DROP

        # Sort entries: score desc, poi URL stabile. Drop score==0 se
        # almeno UNA entry ha score>0 (filtro rumore cross-step).
        # Drop sempre URL da motori di ricerca (cross-link policy/help).
        entries_list = [
            e for e in all_entries_by_url.values()
            if not _is_search_engine_url(e.get("url") or "")
        ]
        any_positive = any(
            isinstance(e.get("score"), (int, float)) and e.get("score", 0) > 0
            for e in entries_list
        )
        if any_positive:
            entries_list = [
                e for e in entries_list
                if isinstance(e.get("score"), (int, float)) and e["score"] > 0
            ]
        entries_list.sort(
            key=lambda e: (-(e.get("score") or 0), e.get("url") or "")
        )
        # 11/5/2026: dedup avanzato per titolo+score. Siti con accessibility
        # options (?textMode=0/1/2, ?contrastMode=0/1/2) generano URL
        # multipli con stesso content+title+score. Idem footer/sidebar
        # uniform: ogni pagina del sito espone lo stesso indirizzo come
        # entry-titolo separata. Tenere SOLO la prima entry per ogni
        # (title_normalized, score_rounded) pair.
        seen_title_score: set[tuple[str, float]] = set()
        deduped_entries: list[dict] = []
        for e in entries_list:
            t = (e.get("title") or "").strip().lower()
            s = round(float(e.get("score") or 0.0), 2)
            key = (t, s)
            if t and key in seen_title_score:
                continue
            if t:
                seen_title_score.add(key)
            deduped_entries.append(e)
        entries_list = deduped_entries

        # Split entries vs documenti per evitare duplicati nelle due
        # sezioni del rendering. «Risultati» mostra solo pagine HTML
        # (is_document=False); «Documenti scoperti» mostra solo file
        # binari (PDF/DOCX/XLSX, is_document=True). Lo stesso URL non
        # appare mai in entrambi.
        html_entries: list[dict] = []
        doc_entries: list[dict] = []
        for e in entries_list:
            if e.get("is_document"):
                doc_entries.append(e)
            else:
                html_entries.append(e)
        # Aggrega i docs da all_docs_by_url (find_urls expone subset doc
        # separatamente, ma potrebbe contenere docs non in entries — es.
        # da BFS cross-domain). Unione + dedup per URL.
        for d in all_docs_by_url.values():
            if _is_search_engine_url(d.get("url") or ""):
                continue
            u = d.get("url")
            if u and not any(de.get("url") == u for de in doc_entries):
                doc_entries.append(d)
        # Sort docs per score desc.
        doc_entries.sort(
            key=lambda d: (-(d.get("score") or 0), d.get("url") or "")
        )
        # Hard cap 30 per sezione: nessuno legge piu' di 30 link.
        _HARD_CAP = 30
        entries = html_entries[:_HARD_CAP]
        docs = doc_entries[:_HARD_CAP]
        try:
            from output_format import format_search_results
        except Exception:
            return
        block = format_search_results(
            entries,
            query=self.user_query or "",
            discovered_documents=docs,
            max_show=20,
        )
        if not block:
            return
        # Idempotenza: se gia' presente nel messaggio, non duplicare.
        first_url = ""
        for e in entries:
            u = e.get("url") if isinstance(e, dict) else ""
            if isinstance(u, str) and u:
                first_url = u
                break
        if first_url and first_url in (self.final_message or ""):
            return

        # Loop_break/error: il messaggio runtime "Mi sono bloccato" e'
        # confondente quando AVEMMO comunque dei risultati. Sostituisce
        # con un'intro morbida che dichiara onestamente i limiti senza
        # negare l'utilita' della lista. Idempotente.
        if self.final_kind in ("loop_break", "error") and entries:
            try:
                from messages import get as _msg_local
                soft = _msg_local("MSG_SEARCH_PARTIAL_OR_INTERRUPTED",
                                    n=len(entries))
            except Exception:
                soft = (
                    f"Ricerca interrotta prima di poter convergere su una "
                    f"risposta diretta, ma sono stati raccolti {len(entries)} "
                    f"risultati pertinenti."
                )
            self.final_message = soft
            self.final_kind = "answer"
        self.final_message = (
            (self.final_message or "").rstrip() + "\n\n" + block
        ).strip()

    def _prepend_health_block_if_any(self) -> None:
        """Se uno step ok ha prodotto un dict `health`, prepend il blocco
        salute formattato al final_message. Si appoggia al formatter
        condiviso di runtime/orchestration.py (`_fmt_health_block` +
        `_fmt_entries_block`). Idempotente: se il blocco e' gia' nel
        messaggio non duplica.
        """
        health = None
        entries = None
        health_host = ""
        for s in self.steps:
            res = s.result if isinstance(s.result, dict) else {}
            h = res.get("health")
            if isinstance(h, dict):
                health = h
                # Host-aware (5/7): se lo step è girato su un device, il
                # titolo dice il DEVICE, non «server» (§2.8).
                health_host = str(res.get("_ran_on_device") or "")
                # Stesso step: prendi anche le entries (lista processi).
                e = res.get("entries")
                if isinstance(e, list):
                    entries = e
                break
        if not health:
            return
        # FOCUS per sezione (9/7, Roberto): domanda SPECIFICA («qual è l'ip»,
        # «che gpu ha») → solo la sezione pertinente, dettagliata. Lessico
        # `health.section_focus` (detection_lexicon, mapping IT+EN). Nessun
        # match → blocco-status completo (comportamento storico).
        _focus: set = set()
        try:
            _fmap = _detlex.mapping("health.section_focus") or {}
            _ql = (self.user_query or "").lower()
            for _sec, _forms in _fmap.items():
                if _detlex.match_any(_forms, _ql):
                    _focus.add(_sec)
        except Exception:  # noqa: BLE001 — best-effort, fallback full block
            _focus = set()
        try:
            # runtime/ già su sys.path (agent_runtime VIVE in runtime/).
            from orchestration import _fmt_health_block, _fmt_entries_block
            block = _fmt_health_block(health, host=health_host,
                                      sections=_focus or None)
            if entries and not _focus:
                # Top 10 processi col detail cpu%/mem% (non solo nomi nudi).
                # Cap 10 perche' health gia' occupa righe; per piu' c'e'
                # cap-expand.
                proc_block = _fmt_entries_block(entries, cap=10)
                if proc_block:
                    block = block + "\n\n**" + msg("MSG_HEALTH_TOP_PROCESSES") + "**\n" + proc_block
        except (ImportError, KeyError, AttributeError):
            return
        if not block or "📊 **Stato" in (self.final_message or "") \
                or "Stato server" in (self.final_message or ""):
            return  # gia' presente o formatter non funzionante
        # ADR 0111 Level 3 safety net: per query di stato puro (nessuna
        # keyword imperativa nella user_query), il `final_message` LLM
        # tende a duplicare/allucinare i dati (esempi: uptime sbagliato,
        # RAM/dischi inventati). Il blocco deterministico prepended e'
        # gia' la risposta completa: zerare il final_message LLM evita
        # il doppio output. Se c'e' una keyword imperativa, l'LLM puo'
        # avere aggiunto una conferma di azione legittima → preserva.
        # Funziona indipendentemente da `is_multistep`/`chosen_mode`,
        # quindi copre anche i path single-step / fast-path / scratchpad.
        _q = (self.user_query or "").lower()
        # ECCEZIONE (10/7, Roberto «usare il più possibile le info disponibili»):
        # se il final È il summary di un describe_entries ESEGUITO (sintesi LLM
        # sui DATI, non un template allucinato — es. «descrivi i processi»),
        # buttarlo perde il riassunto: conserva e prependi il blocco sopra.
        _describe_sum = next(
            (r.get("summary") for s in reversed(self.steps)
             for r in [s.result if isinstance(s.result, dict) else {}]
             if s.chosen_tool == "describe_entries"
             and isinstance(r.get("summary"), str) and r["summary"].strip()),
            None)
        _final_is_describe = bool(
            _describe_sum and _describe_sum.strip()[:80] in
            (self.final_message or ""))
        # A health block can be one clause of a multidomain pipeline (for
        # example file inventory + RAM/CPU/disk).  The health-only fallback
        # must not erase the planner's summary of the other producers merely
        # because their wording does not match the small imperative lexicon.
        _has_non_health_steps = any(
            (getattr(s, "chosen_tool", "") or "")
            not in {"get_processes", "final_answer"}
            for s in self.steps
        )
        if (not _detlex.match("health.imperative", _q)
                and not _final_is_describe
                and not _has_non_health_steps):
            self.final_message = ""
        self.final_message = (block + "\n\n" + (self.final_message or "")).strip()

    def _collect_truncation_notices(self):
        """Scansiona le observation degli step per estrarre cap/truncation
        non dichiarati. Convenzione: un executor che colpisce un cap aggiunge
        all'observation `truncated: true`, opzionalmente `available_total`
        (cardinalita' reale prima del cap), `used: int`, e `truncated_what`
        (nome leggibile della unita': 'email', 'file', 'risultati', ...).
        Vedi feedback_truncation_visibility.

        Pentade ADR 0161 ext: skip notice se intent.verb=compute o query
        contiene marker count (pattern §7.3 universale, no per-tool).
        Per query count l'utente vuole SOLO il numero, notice e' rumore.
        """
        notices = []
        seen = set()
        # Skip per query count (verb=compute o pattern testuale)
        if _is_count_intent(getattr(self, "intent_verb", ""),
                             getattr(self, "user_query", "")):
            return notices
        from vocab import PROCESSOR_VERBS as _PROC_VERBS
        for s in self.steps:
            res = s.result if isinstance(s.result, dict) else {}
            if not res.get("truncated"):
                continue
            # Qualifier `_empty` (ADR 0127): l'executor ritorna ESATTAMENTE
            # quanto chiesto dall'utente (es. find_events_empty max_results=3).
            # "Truncated" qui significa "ho rispettato il tuo cap", non "ho
            # tagliato risultati validi". Notice e' rumore. §7.3 detection
            # generale via suffix qualifier, parallelo a cap-expand suppression.
            if s.chosen_tool and s.chosen_tool.endswith("_empty"):
                continue
            # Producer a RANKING (output_policy G/W/TG): il top-K E' la
            # risposta, available_total e' il pool scoreggiato (non risultati
            # "tagliati") → il notice "Hai N, troppi, considero K" e'
            # fuorviante. Parallelo alla soppressione cap_expand (§2.7).
            try:
                from output_policy import (
                    resolve as _op_resolve, RANKED_MODES as _RANKED)
                if _op_resolve(getattr(self, "intent_verb", ""),
                               s.chosen_tool,
                               getattr(self, "user_query", "")
                               ).get("mode") in _RANKED:
                    continue
            except Exception as _e:
                log.debug("output_policy ranked-notice skip noop: %r", _e)
            # Solo i PRODUCER (read/find/list/get) emettono notice di
            # truncation user-facing: rappresentano l'evento "dato sorgente
            # > cap". I PROCESSOR (vocab.PROCESSOR_VERBS) trasformano una
            # lista gia' presente nello scratchpad; il loro `truncated:True`
            # e' metadata per il pattern cap_expand §2.11 (PLANNER puo'
            # rilanciare con cap maggiore), non un secondo evento sul
            # dato sorgente — quello e' gia' stato annunciato dal producer
            # upstream.
            if s.chosen_tool:
                _verb = s.chosen_tool.split("_", 1)[0]
                if _verb in _PROC_VERBS:
                    continue
            what = (res.get("truncated_what") or s.chosen_tool
                    or msg("MSG_TRUNCATED_DEFAULT_WHAT"))
            # §7.3 (5/7): un executor eseguito sul DEVICE risolve _msg con lo
            # shim SENZA DB i18n → i campi payload a forma di chiave (es.
            # truncated_what='MSG_OBJECT_PROCESSES') arrivano CRUDI. Il server
            # ha il DB: risolvi qui ogni valore chiave-forma, per costruzione.
            if isinstance(what, str) and re.fullmatch(r"(?:MSG|ERR|WARN)_[A-Z0-9_]+", what):
                what = msg(what)
            if what == "input_sources":
                # extract_entries ha capato le SORGENTI in INPUT (non l'output):
                # i campi corretti sono available_INPUT_total + cap_value (50),
                # non available_total (= record estratti, es. 2). Senza, il
                # notice diceva «Hai 2 input_sources, ne considero 2» (nonsenso,
                # bug live 22/6). Parola generica, niente gergo «input_sources».
                available = res.get("available_input_total")
                used = res.get("cap_value") or res.get("used")
                what = msg("MSG_TRUNCATED_DEFAULT_WHAT")
            else:
                used = res.get("used") or res.get("ok_count") or res.get("count")
                available = res.get("available_total")
            # Se extract_entries tocca il token cap, la cardinalità reale è
            # ignota: available_total e used descrivono entrambi i record già
            # salvati. Evita «hai 759, ne considero 759» e dichiara il limite
            # per sorgente con il template no-total già localizzato.
            if (s.chosen_tool == "extract_entries"
                    and res.get("output_truncated_sources")):
                used = res.get("cap_value") or used
                key = (what, used, "extract_output")
                if key not in seen:
                    seen.add(key)
                    notices.append(msg(
                        "MSG_TRUNCATED_NO_TOTAL", used=used, what=what))
                continue
            key = (what, used, available)
            if key in seen:
                continue
            seen.add(key)
            if available and used:
                notices.append(msg("MSG_TRUNCATED_GENERIC",
                                   available=available, what=what, used=used))
            elif used:
                notices.append(msg("MSG_TRUNCATED_NO_TOTAL", used=used, what=what))
        return notices

    def write(self):
        # Shape FSM ADR 0154: l'ultimo step di un turno terminale deve
        # avere `chosen_tool` settato. Step generati da percorsi che NON
        # passano dal tool_call dispatch (LLM ritorna testo senza
        # tool_calls, ProviderError, error LLM) lasciano `chosen_tool=""`
        # (default StepLog), generando shape "E?" che fallisce la regex
        # `^E+F?$`. Normalizzazione idempotente, una sola riga: tutti i
        # call-site terminali ereditano il fix.
        if self.steps and self.final_kind in (
            "answer", "ask", "error", "loop_break"
        ):
            if not self.steps[-1].chosen_tool:
                self.steps[-1].chosen_tool = "final_answer"
        # Effetti reali del turno (§2.8, 12/6/2026): calcolati per OGNI
        # final_kind cosi' i consumer (notice falso-successo qui sotto,
        # gate push schedulato in recurring_tasks) leggono lo stesso dato.
        self.effect_counts = pipeline_effect_counts(self.steps)
        # Anti thinking-leak (ADR 0102, 7/5/2026): rimuovi righe di
        # reasoning interno emesse erroneamente dal PLANNER nel canale
        # text. Applicato PRIMA di qualsiasi prepend (truncation/health/
        # hallucination) e PRIMA dell'append di elapsed, cosi' nessuna
        # riga aggiunta dal runtime viene scartata. Idempotente.
        if self.final_kind == "answer" and self.final_message:
            self.final_message = _scrub_thinking_leak(self.final_message)
            # §2.8 raw-HTML leak guard: HTML document-level mai user-facing
            # (fetch grezzo trapelato). Backstop universale dopo i formatter.
            self.final_message = _scrub_raw_html_leak(self.final_message)
            # §2.8 universal leak guard: marker runtime-internal in
            # final_message (DUPLICATE_CALL, "validation failed:", etc.)
            # sono gergo destinato al PLANNER, non all'utente. Detection
            # deterministica §7.9 + sintesi onesta dal last error.
            # Applicato dopo _scrub_thinking_leak per coprire OGNI path
            # (auto_final_on_duplicate_fail, final_answer synth grammar,
            # _compose_final_message_from_obs branch).
            if _has_runtime_internal_leak(self.final_message):
                self.final_message = _compose_honest_from_last_error(self)
            # §4.3 mutating intent honesty: se la user_query contiene un
            # verbo mutating (move/delete/send/write/create/share) ma
            # nessuno step ok=True ha quel verbo → l'azione non e' stata
            # eseguita. Final_message attuale (es. "read_messages:
            # completato (7 elementi)" da auto-final) e' fuorviante.
            # Sostituisci con dichiarazione esplicita di incompletezza.
            _mutating_pending = _detect_unfulfilled_mutating_intent(self)
            if _mutating_pending:
                # §2.8: se lo step mutante FALLITO porta un messaggio
                # user-facing ESPLICITO (`final_message_hint`, es. delete_persons
                # "Nessuna persona 'X' nel registro."), mostralo — NON mascherarlo
                # col generico "azione non completata" (che maschera la causa
                # reale e azionabile, come faceva "Pipeline malformata").
                _mut_hint = ""
                for _s in reversed(getattr(self, "steps", []) or []):
                    _o = _s.result if isinstance(_s.result, dict) else None
                    if isinstance(_o, dict) and _o.get("ok") is False:
                        _h = _o.get("final_message_hint")
                        if isinstance(_h, str) and _h.strip():
                            _mut_hint = _h.strip()
                            break
                if _mut_hint:
                    self.final_message = _mut_hint
                else:
                    try:
                        self.final_message = msg(
                            "MSG_MUTATING_INTENT_UNFULFILLED",
                            verb=_mutating_pending,
                        )
                    except Exception:
                        self.final_message = (
                            f"L'azione `{_mutating_pending}` richiesta non e' "
                            f"stata completata. Riformula la richiesta con "
                            f"maggiori dettagli o specifica i target da "
                            f"modificare."
                        )
        # 10/5/2026: append lista risultati formattati quando in history
        # c'e' uno step `find_urls` ok con entries. Indipendente da
        # final_kind: anche su loop_break/error l'utente vede i risultati
        # parziali raccolti — prima il PLANNER andava in loop e l'utente
        # non vedeva NIENTE dei link gia' trovati.
        if self.final_kind in ("answer", "loop_break", "error"):
            self._append_search_results_if_any()
            # _append_images_results_if_any rimosso 15/5/2026: lista basename
            # nel testo era ridondante con gallery_url. L'utente vede thumb
            # + caption hover nella gallery, niente serve nel testo.
        # Prepend di eventuali notice di truncation prima della final answer.
        # Una sola volta, idempotente: se la stringa e' gia' presente non duplica.
        # Skip quando l'ultimo step e' `final_answer` synthetic (ADR 0133 ext):
        # il LLM ha ricevuto la describe_entries (con info `truncated`) e ha
        # gia' formulato un final consapevole — prependere ridonda e copre il
        # messaggio utile (bug live 15/5/2026 mail run 1: prepend mascherava
        # la sintesi LLM del riassunto mail).
        if self.final_kind == "answer":
            # §2.8: un final mutating non puo' claimare un esito non avvenuto.
            self._enforce_mutating_honesty()
            # §2.8: rendi visibili i fallimenti per-item/account (no silent "Hai 0").
            for _fn in self._collect_failure_notices():
                if _fn and _fn not in (self.final_message or ""):
                    self.final_message = ((self.final_message or "").rstrip()
                                          + "\n\n" + _fn).strip()
            _llm_synth_final = bool(
                self.steps and self.steps[-1].chosen_tool == "final_answer"
            )
            if not _llm_synth_final:
                for notice in self._collect_truncation_notices():
                    if notice and notice not in (self.final_message or ""):
                        self.final_message = (notice + "\n\n" + (self.final_message or "")).strip()
            # Bug C estensione (6/5/2026): se uno step ha prodotto una sezione
            # `health` (get_processes(include_health=true)), prepend il blocco
            # salute al final_message. describe_entries non sa leggere health
            # perche' lavora solo su entries; senza questo, lo "stato server"
            # mostra solo i nomi dei processi e basta. Idempotente.
            self._prepend_health_block_if_any()
            # Auto cap expand proposal (the design guide 2.11 fase 2).
            # Popola `expandable_caps` cosi' il channel daemon puo' salvare
            # dialog state e riconoscere la risposta di conferma utente.
            self.expandable_caps = self._collect_expandable_caps()
            if self.expandable_caps:
                p = self.expandable_caps[0]
                # Skip prompt per kind custom gia' renderizzati (ADR 0090):
                # `get_inputs_response` ha gia' la carta UX (Step N/M) emessa
                # dall'executor; `admin_approval` ha la carta vaglio.
                if p.get("kind") in ("get_inputs_response", "admin_approval"):
                    pass
                elif p.get("kind") == "cap_expand" and "available_total" in p and "cap_field" in p:
                    # Feedback utente (3/6): NON aprire un dialog yes_no bloccante a
                    # fine query — obbligava l'utente a rispondere a OGNI troncamento.
                    # La notifica di troncamento §2.7 ("Hai N, considero K") e' gia'
                    # nel final_message (sopra): informa senza forzare. Se vuole il
                    # resto l'utente lo chiede in linguaggio naturale ("dammi tutte").
                    # §2.11 reso NON-blocking. Rimuovi il cap_expand dalle
                    # expandable_caps cosi' il channel non crea un dialog pendente.
                    self.expandable_caps = [
                        c for c in self.expandable_caps
                        if c.get("kind") != "cap_expand"]
            # Anti-allucinazione (Bug B, 5/5/2026): notice additiva quando il
            # final_message contiene promesse di azione futura ma nessuno step
            # ok ha registrato l'azione. §2.8 No silent failure: la falsa
            # sicurezza sull'utente e' colpo mortale. Notice prepended (non
            # rewrite) per preservare l'eventuale info utile del messaggio.
            if _detect_unbacked_promise(self.final_message, self.steps):
                self.unbacked_promise_detected = True
                _hallucination_notice = msg("MSG_HALLUCINATION_NOTICE")
                if _hallucination_notice not in (self.final_message or ""):
                    self.final_message = (
                        _hallucination_notice + "\n\n"
                        + (self.final_message or "")
                    ).strip()
            # Anti-falso-silent-failure §2.8 (17/5/2026): se il LLM dice
            # «non trovato» mentre uno step mutating ha realmente modificato
            # N>=1 record (bug live 16/5/2026: 15 eventi calendar cancellati
            # mentre Metnos dichiarava «evento non trovato»), prepend notice
            # autoritativa con il contatore reale. La sovrascrittura non e'
            # destructive (preserva il messaggio LLM per audit) ma chiarisce
            # all'utente lo stato reale del sistema.
            _ff = _detect_false_not_found(self.final_message, self.steps)
            if _ff:
                _ff_notice = (
                    f"⚠ ATTENZIONE: {_ff['tool']} ha modificato "
                    f"{_ff['ok_count']} elementi (ok=True). Il messaggio "
                    f"sotto del LLM contraddice questo fatto — la modifica "
                    f"E' STATA EFFETTUATA."
                )
                if _ff_notice not in (self.final_message or ""):
                    self.final_message = (
                        _ff_notice + "\n\n"
                        + (self.final_message or "")
                    ).strip()
            # Anti-falso-successo §2.8 (12/6/2026): il final NARRA un esito
            # positivo («analizzato, salvato bozze pronte, notificato») ma
            # TUTTI gli step contabili hanno prodotto/processato 0 elementi
            # (caso live: maintenance github schedulata su 0 issue aperte,
            # store a 0 righe). Notice additiva deterministica §7.9, simmetrica
            # a _detect_false_not_found; preserva il messaggio LLM per audit.
            if _detect_false_success(self.final_message, self.effect_counts):
                self.false_success_detected = True
                _fs_notice = msg("MSG_FALSE_SUCCESS_NOTICE")
                # SOSTITUISCE (non antepone) il narrato LLM falso: l'utente
                # deve vedere SOLO la verità (0 risultati), non «ho creato il
                # foglio con i dati» DOPO «nessuna azione eseguita» (messaggio
                # contraddittorio, turn 36a40c35/e591854e). Il testo LLM resta
                # nel log dello step per audit.
                self.final_message = _fs_notice
            # Claim di MUTAZIONE (creato il foglio / inviato la mail / ...) ma
            # 0 mutazioni reali — anche con items>0 (es. ha LETTO mail ma NON
            # creato il foglio): il synth mente sull'azione. Sostituisci con la
            # verità (§2.8, bug live 21/6 fatture Anthropic).
            elif _detect_false_mutation(self.final_message, self.effect_counts):
                self.false_success_detected = True
                self.final_message = msg("MSG_FALSE_MUTATION_NOTICE")
            # Final DEGENERE §2.8 (23/6, banco #1): un final_message nudo-conteggio
            # («0») o vuoto NON è un esito mostrabile. Sintomo: template-render a
            # vuoto su un piano monco (decomposer droppa create_files_spreadsheet
            # → resta scoperto `${stepN.@count}`). Sostituisci con la verità: se
            # era dichiarata una mutazione mai eseguita → notice falsa-mutazione;
            # altrimenti il conteggio onesto degli elementi prodotti / no-results.
            # NB: NON intercettare i turni con step in errore — quelli hanno il
            # loro fallback onesto (error_class → messaggio user-friendly più
            # sotto in write()); un final vuoto + step fallito deve menzionare
            # l'errore, non «nessun risultato» (test_final_message_invariant).
            elif (_is_degenerate_final(self.final_message)
                  and not (self.effect_counts or {}).get("failures")):
                self.false_success_detected = True
                _ec = self.effect_counts or {}
                # §2.8: un builtin che dichiara il SUO esito nel result
                # (`final_message_hint`/`message` i18n, es. undo_last_turn
                # «Nessuna operazione reversibile da annullare») vince sui generici — bug live
                # f9cb0033 6/7: l'undo onesto diventava «Nessun risultato
                # trovato» (falso: non era una ricerca).
                _exec_msg = ""
                for _s in reversed(self.steps):
                    _r = _s.result if isinstance(_s.result, dict) else {}
                    _declared = (_r.get("final_message_hint")
                                 or _r.get("message"))
                    if isinstance(_declared, str) and _declared.strip():
                        _exec_msg = _declared.strip()
                        break
                if _exec_msg:
                    self.final_message = _exec_msg
                elif not _ec.get("mutating_attempted") and _ec.get("items", 0) == 0:
                    # niente prodotto, niente mutato → no-results onesto
                    self.final_message = msg("MSG_NO_RESULTS")
                else:
                    # qualcosa è stato letto/prodotto ma il messaggio è degenere:
                    # render onesto del conteggio + nota se un'azione dichiarata
                    # (mutazione) non è avvenuta. Singolare/plurale corretto.
                    # §7.13: chiavi risolte via i18n DB (lingua istanza), definite
                    # nel catalogo seed — mai testo in-linea nel sorgente. Guard:
                    # runtime/tests/test_seed_i18n_gate_keys.py.
                    _n = _ec.get("items", 0)
                    _muts = _ec.get("mutations", 0)
                    if _muts:
                        # Mutazione RIUSCITA (es. piano da recovery, che non
                        # porta final_message template): dire «nessun'altra
                        # azione completata» suonerebbe come un fallimento.
                        # Esito onesto col conteggio reale (§2.8).
                        self.final_message = msg(
                            "MSG_DEGENERATE_FINAL_MUTATIONS", n=_muts)
                    elif _n == 1:
                        self.final_message = msg("MSG_DEGENERATE_FINAL_ITEM_ONE")
                    else:
                        self.final_message = msg("MSG_DEGENERATE_FINAL_ITEMS", n=_n)
        # A.2 (fase 7): avvisi fuori-turno pendenti per QUESTO destinatario
        # (es. op remota completata DOPO il timeout del suo turno) — anteposti
        # DOPO tutte le riscritture del final (honesty/degenerate/false-success
        # ASSEGNANO final_message: prima, la notice andava persa). Best-effort.
        try:
            import user_notices as _un
            for _nt in _un.drain(self.channel or "", self.actor or "host"):
                if _nt not in (self.final_message or ""):
                    self.final_message = (
                        _nt + "\n\n" + (self.final_message or "")).strip()
        except Exception as _ne:
            log.debug("user_notices drain noop: %r", _ne)
        # Propaga attachments dall ultimo step che ne ha prodotti (use
        # case realistico: un solo find_images_indices per turno).
        for s_step in reversed(self.steps):
            res = s_step.result if isinstance(s_step.result, dict) else {}
            atts = res.get("attachments")
            if isinstance(atts, list) and atts:
                self.attachments = atts
                break
        # Fallback §7.3: nessun executor ha dichiarato attachments ma uno step
        # file-producer (spreadsheet/doc/zip/...) ha scritto un file su disco →
        # sintetizza gli attachments cosi' il deliverable arriva all'utente su
        # OGNI canale (HTTP download + Telegram documento). Bug live 5303699e.
        if not self.attachments:
            for s_step in reversed(self.steps):
                res = s_step.result if isinstance(s_step.result, dict) else {}
                if not res.get("ok"):
                    continue
                derived = _derive_file_attachments(s_step.chosen_tool, res)
                if derived:
                    self.attachments = derived
                    break

        # Universal §7.3: se reference_images allegate via drag&drop,
        # PREPEND le foto input agli attachments cosi' la chat mostra
        # anche le N foto di reference (utile per reverse image search:
        # vedere visivamente input vs simili web).
        upload_step = next((s for s in self.steps if s.chosen_tool == "@uploaded"), None)
        if upload_step is not None and isinstance(upload_step.result, dict):
            up_entries = upload_step.result.get("entries") or []
            input_atts = []
            for e in up_entries:
                if not isinstance(e, dict):
                    continue
                p = e.get("path") or e.get("reference_image")
                if not isinstance(p, str) or not p:
                    continue
                from pathlib import Path as _P
                input_atts.append({
                    "kind": "image",
                    "path": p,
                    "basename": _P(p).name,
                    "caption": "input",
                })
            if input_atts:
                # Prepend: input prima, web/local risultati dopo
                self.attachments = input_atts + list(self.attachments or [])

        # Invariante §2.8 (no silent failure): un turno terminale che parla
        # all'utente non puo' avere final_message vuoto. Indipendente dal
        # path che ha settato `final_kind`. Sintesi best-effort dal contesto.
        # Ordine cruciale: l'error report ha PRIORITA' sul compose_from_obs
        # perche' su `ok=False` quest'ultimo emette "completato (0 elementi)"
        # — disonesto §2.8 (un fail non e' un completamento).
        # Strato:
        #   1. Ultimo step con `ok=False` E error informativo →
        #      MSG_FINAL_FALLBACK_FROM_ERROR (tool + error reale).
        #   2. Ultimo step con obs strutturata utile (ok=True/None) →
        #      `_compose_final_message_from_obs` (path auto-final ufficiale).
        #   3. Fallback generico MSG_FINAL_FALLBACK_GENERIC.
        # `needs_inputs` ha dialog UX dedicata: non rientra qui.
        def _humanize_error_class(raw: str) -> str:
            """Traduce error_class technical (es. `no_verified_channel`) in
            testo user-facing via i18n key `ERR_<UPPERCASE>`. Fallback al
            raw string se la chiave non esiste. Generale §7.3: ogni
            executor che ritorna un error_class registrato come ERR_
            i18n diventa automaticamente user-friendly senza modifiche.
            """
            if not raw or not isinstance(raw, str):
                return raw or ""
            # Strip prefisso colon-separated tipo "channel_not_paired:telegram"
            _key_part = raw.split(":", 1)[0].strip()
            if not _key_part or not _key_part.replace("_", "").isalnum():
                return raw
            _i18n_key = f"ERR_{_key_part.upper()}"
            try:
                _human = msg(_i18n_key)
            except Exception:
                return raw
            # `msg()` ritorna `<missing:KEY>` se assente: distingui
            if _human and not _human.startswith("<missing:"):
                return _human
            return raw

        def _extract_error(obs: dict) -> str:
            if not isinstance(obs, dict):
                return ""
            _e = obs.get("error")
            if isinstance(_e, str) and _e.strip():
                return _humanize_error_class(_e.strip())
            _failed = obs.get("failed") or []
            if isinstance(_failed, list):
                parts = [
                    _humanize_error_class(str((f or {}).get("error", "")).strip())
                    for f in _failed
                    if isinstance(f, dict) and f.get("error")
                ]
                # dedup conservando ordine (stessa error_class su piu' target)
                seen = set()
                deduped = []
                for p in parts:
                    if p and p not in seen:
                        seen.add(p)
                        deduped.append(p)
                if deduped:
                    return " ".join(deduped)
            return ""

        if (self.final_kind in ("answer", "ask", "error", "loop_break")
                and not (self.final_message or "").strip()):
            _fallback = ""
            # (1) priorita': ultimo step ok=False con error → onestamente
            #     reporta il fail. Non degradare a "completato (0 elementi)".
            for _s in reversed(self.steps):
                _obs = _s.result if isinstance(_s.result, dict) else {}
                if not _obs:
                    continue
                if _s.chosen_tool == "final_answer":
                    continue
                if _obs.get("ok") is False:
                    _err = _extract_error(_obs)
                    if _err:
                        try:
                            _fallback = msg(
                                "MSG_FINAL_FALLBACK_FROM_ERROR",
                                tool=_s.chosen_tool or "",
                                error=_err,
                            )
                        except Exception:
                            _fallback = f"{_s.chosen_tool}: {_err}"
                        break
            # (2) successo silente: usa compose_from_obs
            if not _fallback:
                for _s in reversed(self.steps):
                    _obs = _s.result if isinstance(_s.result, dict) else None
                    if not _obs:
                        continue
                    if _s.chosen_tool == "final_answer":
                        continue
                    if _obs.get("ok") is False:
                        continue
                    try:
                        _fm, _, _ = _compose_final_message_from_obs(
                            _s.chosen_tool or "", _obs)
                    except Exception:
                        _fm = ""
                    if _fm and _fm.strip():
                        _fallback = _fm.strip()
                        break
            # (3) ultimo livello: fallback generico i18n
            if not _fallback:
                try:
                    _fallback = msg("MSG_FINAL_FALLBACK_GENERIC")
                except Exception:
                    _fallback = ""
            if _fallback:
                self.final_message = _fallback
        # Footer "elapsed: Xs · chiuso HH:MM:SS" rimosso 7/5/2026 notte
        # (Roberto: ridondante con il badge meta della UI HTTP, valore
        # gia' presente nel jsonl come ts_end-ts_start per telemetria).
        #
        # ADR 0149 (18/5/2026): log canonical_query del PRIMO step
        # planner verso mnestoma.canonical_query_log per la futura
        # promozione fast-path L1. Solo first step (mapping canonical →
        # first_chosen_tool). Idempotente per turn via _canonical_recorded.
        # Off-thread di fallimento: never raise → telemetria mai-bloccante.
        if not self._canonical_recorded and self.steps and self.final_kind:
            first = self.steps[0]
            cq = ""
            try:
                # raw_args può essere dict o stringa JSON. canonical_query
                # è attributo dello step settato da agent_runtime.
                cq = getattr(first, "canonical_query", "") or ""
                if not cq and isinstance(first.raw_args, dict):
                    cq = str(first.raw_args.get("_canonical_query", "") or "")
                # Fallback (19/5 v5): step 1 e' spesso un seed_step
                # (ADR 0099) iniettato deterministicamente senza chiamata
                # al PLANNER LLM, quindi canonical_query=None. In quel
                # caso leggiamo la canonical_query dal primo step LLM
                # successivo (tipicamente step 2). Senza questo, le
                # query con URL esplicito non popolano canonical_query_log.
                if (not cq and getattr(first, "seed_step", False)
                        and len(self.steps) >= 2):
                    for s in self.steps[1:]:
                        cq_alt = getattr(s, "canonical_query", "") or ""
                        if cq_alt:
                            cq = cq_alt
                            break
            except Exception:
                cq = ""
            tool = getattr(first, "chosen_tool", "") or ""
            if cq and tool:
                try:
                    from mnestoma import Mnestoma
                    raw = (first.raw_args if isinstance(first.raw_args, dict)
                           else {})
                    # args_shape: template (placeholders); args_observed:
                    # valori reali. Fase 14 v5: differenziamo per riusare i
                    # valori al second pass del matcher senza re-extraction.
                    # Per ora `raw_args` contiene gia' i valori reali del
                    # primo step: lo passiamo sia come shape (back-compat)
                    # che come args_observed.
                    resolved = (first.resolved_args
                                if isinstance(first.resolved_args, dict)
                                else raw)
                    # Strip args query-derived (ADR 0150 v7, generalizzato):
                    # qualunque arg che `args_extractor.regex_extract`
                    # saprebbe ri-derivare dal query corrente NON va
                    # memoizzato letterale — verrebbe applicato a una
                    # query con intent diverso. Single source of truth =
                    # args_extractor + schema dell'executor del primo step.
                    # General + lang-independent §7.3.
                    resolved_clean = dict(resolved)
                    try:
                        from args_extractor import regex_extract as _are
                        from loader import load_catalog as _ld
                        _ex0 = (getattr(_ld(), "executors", {})
                                 .get(tool))
                        _sch0 = (getattr(_ex0, "args_schema", None)
                                  if _ex0 else None) or {}
                        if _sch0:
                            _qd = set(
                                _are(self.user_query or "", _sch0).keys()
                            )
                            resolved_clean = {
                                k: v for k, v in resolved.items()
                                if k not in _qd
                            }
                    except Exception:
                        pass
                    _mn = Mnestoma()
                    _mn.record_canonical_query(
                        cq, tool, raw,
                        ok=(self.final_kind == "answer"),
                        args_observed=resolved_clean,
                    )
                except Exception:
                    pass
            self._canonical_recorded = True

        TURN_LOG_DIR.mkdir(parents=True, exist_ok=True)
        path = TURN_LOG_DIR / f"{time.strftime('%Y-%m-%d')}.jsonl"
        # Scrubbing credenziali prima della serializzazione (ADR 0082):
        # passiamo da asdict (snapshot) e ri-iniettiamo le entry pulite.
        record = asdict(self)
        n_redacted_total = [0]
        cleaned_query, _n = _scrub_credentials(record.get("user_query", "") or "")
        n_redacted_total[0] += _n
        record["user_query"] = cleaned_query
        steps_clean = []
        for s in record.get("steps", []):
            if isinstance(s, dict) and isinstance(s.get("raw_args"), dict):
                s["raw_args"] = _scrub_args_recursive(s["raw_args"], n_redacted_total)
            if isinstance(s, dict) and isinstance(s.get("resolved_args"), dict):
                s["resolved_args"] = _scrub_args_recursive(s["resolved_args"], n_redacted_total)
            steps_clean.append(s)
        record["steps"] = steps_clean
        if n_redacted_total[0] > 0:
            record["redacted"] = True
            record["n_redacted_fields"] = n_redacted_total[0]
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


# --- Hook synt-on-the-fly --------------------------------------------------

def _try_synt_compose(mnestoma, target_intent: str, mnest_id: str, *, verbose: bool = False) -> dict | None:
    """Tenta sintesi reattiva (solo compose, niente generate) per un proto-mnest
    appena registrato. Se synt trova una catena di executor firmati che chiude
    il proto, restituisce un dict con un suggerimento per il LLM. Se compose
    fallisce o synt e' indisponibile, ritorna None (degrada graziosamente).
    """
    try:
        s = Synt(mnestoma=mnestoma, router=None)  # router=None: niente generate
        req = synt_make_request(target_intent=target_intent, proto_mnest=mnest_id)
        prop = s.react(req)
    except Exception as ex:
        if verbose:
            print(f"[synt] react failed: {ex}")
        return None
    if prop.state == "composed":
        chain = prop.artefact.get("chain") or []
        first_hop = None
        names = []
        for hop in chain:
            dst = hop.get("dst_executor") if isinstance(hop, dict) else getattr(hop, "dst_executor", None)
            if dst:
                names.append(dst)
                if first_hop is None:
                    first_hop = dst
        return {
            "strategy": "compose",
            "state": "composed",
            "chain": names,
            "first_hop": first_hop,
            "suggestion": (
                f"Esiste una catena di executor firmati ({' -> '.join(names)}) "
                f"che copre questa esigenza. Riprova invocando '{first_hop}' come prossimo passo."
            ),
        }
    if prop.state in ("generating", "born", "proposed"):
        # generate richiede router non nullo, non dovrebbe accadere qui; ma se
        # arriva, segnaliamo proposta in attesa.
        return {
            "strategy": "generate",
            "state": prop.state,
            "suggestion": (
                f"E' stata creata una proposta di executor (stato: {prop.state}, in attesa di "
                f"approvazione utente). Per ora cerca una via alternativa o rinuncia."
            ),
        }
    if prop.state in ("abandoned", "rejected"):
        return {
            "strategy": prop.strategy,
            "state": prop.state,
            "suggestion": (
                f"Sintesi reattiva ha rinunciato (stato: {prop.state}). "
                f"Non c'e' executor disponibile per questa esigenza, cerca un'altra via."
            ),
        }
    return None


# --- Builtin handler registry (modulo-level) -------------------------------
# Builtin in-process handlers (vs executor subprocess via invoke_executor).
# Tabella unica usata dal fast-path playback, dall'auto-remediation, e da
# qualunque futuro caller. Niente duplicazioni file-by-file.
_BUILTIN_TOOL_HANDLERS: dict = {
    "describe_entries": handle_describe_entries,
    "classify_entries": handle_classify_entries,
    "extract_entries": handle_extract_entries,
    "create_tasks": handle_create_tasks,
    "list_tasks": handle_list_tasks,
    "delete_tasks": handle_delete_tasks,
    "read_tasks": handle_read_tasks,
    "set_tasks": handle_set_tasks,
    "read_tasks_history": handle_read_tasks_history,
    "list_skills": handle_list_skills,
    "set_skills": handle_set_skills,
    "find_entries": handle_find_entries,
    "write_entries": handle_write_entries,
    "delete_entries": handle_delete_entries,
    "compare_entries": handle_compare_entries,
    "describe_images": handle_describe_images,
}


@functools.lru_cache(maxsize=None)
def _builtin_execution_executor(tool_name: str, module_path: str):
    """Resolve one verified builtin contract once per daemon process."""
    from loader import builtin_contract_executor
    return builtin_contract_executor(tool_name, Path(module_path))

# Tool-spec OpenAI-style per i builtin in-process che NON sono iniettati nel
# catalog via loader (`*_tasks` lo sono via BUILTIN_INPROC_SPECS; describe/
# classify no). Serve a Engine v2 per costruire Executor virtuali da passare
# al Validator (altrimenti `tool_unknown` falso su helper universali §11).
# Universal §7.3: aggiungere una riga = nuovo builtin coperto, niente
# special-case per-tool.
_BUILTIN_TOOL_SPECS: dict = {
    "describe_entries": DESCRIBE_ENTRIES_TOOL,
    "classify_entries": CLASSIFY_ENTRIES_TOOL,
    "extract_entries": EXTRACT_ENTRIES_TOOL,
    "find_entries": FIND_ENTRIES_TOOL,
    "write_entries": WRITE_ENTRIES_TOOL,
    "delete_entries": DELETE_ENTRIES_TOOL,
}


_BUILTIN_ERROR_CODES = {
    "invalid_args": "ERR_ARG_INVALID",
    "not_found": "ERR_NOT_FOUND",
    "permission_denied": "ERR_PERMISSION_DENIED",
    "dependency_unavailable": "ERR_EXT_SVC_UNAVAILABLE",
    "operation_failed": "ERR_BUILTIN_FAILED",
    "internal_error": "ERR_BUILTIN_INVALID_RESULT",
}


def _normalize_builtin_result(tool_name: str, result: object) -> dict:
    """Enforce the terminal Executor Standard envelope for in-process tools.

    Individual builtin handlers predate the signed contract registry and use
    several domain-specific payloads.  This boundary preserves those payloads
    while making complete failures and mixed outcomes machine-observable.
    """
    if not isinstance(result, dict):
        return {
            "ok": False,
            "error": f"builtin {tool_name} returned a non-object result",
            "error_class": "internal_error",
            "error_code": _BUILTIN_ERROR_CODES["internal_error"],
            "tool": tool_name,
        }
    out = dict(result)
    if not isinstance(out.get("ok"), bool):
        out["ok"] = False
        out.setdefault("error", f"builtin {tool_name} omitted boolean ok")
        out.setdefault("error_class", "internal_error")

    if out["ok"] is False:
        current = str(out.get("error_class") or "").strip()
        if current in {"missing_input", "wrong_args"}:
            current = "invalid_args"
        if not current:
            message = str(out.get("error") or out.get("summary") or "").lower()
            code = str(out.get("error_code") or "")
            if code.startswith("ERR_ARG_") or any(token in message for token in (
                    "missing", "manca", "required", "must be", "deve ",
                    "invalid", "specifica ", "mutex", "serve '")):
                current = "invalid_args"
            elif code == "ERR_PERMISSION_DENIED" or any(token in message for token in (
                    "non tuo", "solo host", "security", "permission", "forbidden")):
                current = "permission_denied"
            elif code == "ERR_NOT_FOUND" or any(token in message for token in (
                    "not found", "non trovato", "sconosciut")):
                current = "not_found"
            elif code == "ERR_EXT_SVC_UNAVAILABLE" or any(token in message for token in (
                    "unreachable", "unavailable", "non disponibile")):
                current = "dependency_unavailable"
            else:
                current = "operation_failed"
        out["error_class"] = current
        out.setdefault(
            "error_code",
            _BUILTIN_ERROR_CODES.get(current, "ERR_BUILTIN_FAILED"),
        )

    failure_items = out.get("failed") or out.get("failures") or out.get("errors")
    fail_count = out.get("fail_count")
    has_failures = bool(failure_items) or (
        isinstance(fail_count, int) and fail_count > 0
    )
    if out.get("ok") is True and has_failures:
        out["partial"] = True
    return out


def _engine_v2_catalog_with_builtins(catalog: list) -> list:
    """Augmenta il catalog con Executor virtuali per i builtin in-process
    (describe_entries/classify_entries/...) mancanti.

    Engine v2 (`dispatch.run_turn`) passa lo STESSO catalog al Proposer e al
    `Validator`. I builtin in-process invocabili (`_BUILTIN_TOOL_HANDLERS`)
    NON sono tutti nel catalog del loader → il Validator li flaggerebbe come
    `tool_unknown` (falso positivo → re-propose sprecato). Allinea Engine v2
    al path legacy che fa `{e.name} | _BUILTIN_TOOL_HANDLERS.keys()`.

    Idempotente: builtin gia' presente (es. `*_tasks` via loader) → skip.
    `final_answer` resta virtual (gestito dal Validator nativamente).
    """
    try:
        from loader import builtin_contract_executor
    except Exception:
        return catalog
    present = {getattr(e, "name", None) for e in catalog}
    out = list(catalog)
    for name in _BUILTIN_TOOL_HANDLERS:
        if name in present:
            continue
        handler = _BUILTIN_TOOL_HANDLERS.get(name)
        module = sys.modules.get(getattr(handler, "__module__", ""))
        module_path = Path(getattr(module, "__file__", ""))
        try:
            out.append(builtin_contract_executor(name, module_path))
        except (OSError, ValueError) as exc:
            log.error("builtin %s excluded: invalid signed contract: %s", name, exc)
    return out


def _invoke_builtin_handler(tool_name: str, args: dict, *,
                              actor: str | None = None,
                              channel: str | None = None,
                              turn_id: str | None = None) -> dict:
    """Universal §7.9 wrapper: invoca handler builtin passando solo i kwargs
    che la signature accetta (introspection). Risolve crash su
    list_tasks/create_tasks/delete_tasks (kwarg actor/channel required).
    """
    handler = _BUILTIN_TOOL_HANDLERS.get(tool_name)
    if handler is None:
        return _normalize_builtin_result(
            tool_name, {"ok": False, "error": f"unknown builtin: {tool_name}",
                        "error_class": "not_found"})
    # Guard anti-costo run schedulati (12/6/2026): issue già trattate in
    # `issue_qa` NON ri-entrano negli step LLM-costosi (classify/describe/
    # extract → fino a frontier). Deterministico §7.9, fail-open §2.8,
    # no-op sui turni interattivi. Vedi runtime/treated_issues_guard.py.
    from treated_issues_guard import (
        filter_treated_issue_entries, annotate_skipped_known)
    args, _treated_info = filter_treated_issue_entries(tool_name, args)
    import inspect as _inspect
    try:
        sig = _inspect.signature(handler)
        accepts = set(sig.parameters.keys())
    except (ValueError, TypeError):
        accepts = set()
    kwargs = {}
    if "actor" in accepts:
        kwargs["actor"] = actor or "host"
    if "channel" in accepts:
        kwargs["channel"] = channel or ""
    if "turn_id" in accepts:
        kwargs["turn_id"] = turn_id or ""
    # Se signature ha **_ catch-all, possiamo passare safe.
    def _call_handler() -> dict:
        return annotate_skipped_known(handler(args, **kwargs), _treated_info)

    try:
        # In-process builtins use the same signed execution policy, central
        # scheduler and assigned worker budget as subprocess/remote executors.
        # Context-local injection avoids process-wide environment races.
        module = sys.modules.get(getattr(handler, "__module__", ""))
        module_path = str(Path(getattr(module, "__file__", "")))
        executor = _builtin_execution_executor(tool_name, module_path)
        from executor_scheduler import assigned_worker_budget, invoke_scheduled
        from executor_workers import worker_budget
        with worker_budget(assigned_worker_budget(executor)):
            result = invoke_scheduled(executor, _call_handler)
        return _normalize_builtin_result(tool_name, result)
    except (OSError, ValueError) as contract_error:
        log.error("Builtin %s execution contract rejected: %s",
                  tool_name, contract_error)
        return _normalize_builtin_result(
            tool_name,
            {"ok": False, "error": str(contract_error),
             "error_class": "internal_error"},
        )
    except TypeError as te:
        # Fallback: prova senza kwargs (handler legacy che vuole solo args)
        if "actor" in str(te) or "channel" in str(te):
            log.error("Builtin %s requires kwargs not provided: %s",
                      tool_name, te)
        return _normalize_builtin_result(
            tool_name,
            {"ok": False, "error": f"builtin call failed: {te}",
             "error_class": "internal_error", "tool": tool_name},
        )


def invoke_tool_by_name(tool_name: str, args: dict, *, catalog: list,
                        actor: str | None = None,
                        channel: str | None = None) -> dict:
    """Dispatch canonico di UN tool per nome, condiviso dal loop principale e
    dai percorsi di ripresa (post-gate/post-input, orchestration).

    Builtin in-process (registro `_BUILTIN_TOOL_HANDLERS`, unica fonte di
    verita') PRIMA, poi executor firmato del catalog. Cosi' un helper
    universale (`describe_entries`, `classify_entries`, ...) non e' mai un
    falso `tool_unknown` quando una pipeline riprende dopo un gate (§7.3: una
    riga nel registro basta, nessun elenco cablato per-tool).
    """
    if tool_name in _BUILTIN_TOOL_HANDLERS:
        return _invoke_builtin_handler(
            tool_name, args, actor=actor, channel=channel)
    exec_obj = next((e for e in (catalog or [])
                     if getattr(e, "name", None) == tool_name), None)
    if exec_obj is None:
        return {"ok": False, "error": f"tool '{tool_name}' non in catalog",
                "error_class": "tool_unknown"}
    return invoke_executor(
        exec_obj, args, timeout_s=(getattr(exec_obj, "timeout_s", None) or 120),
        actor=actor, channel=channel)


# --- Auto-remediation generalizzata (ADR 0153) -----------------------------

def _maybe_remediate_obs(
    obs: dict,
    original_args: dict,
    original_tool: str,
    *,
    catalog: list,
    turn_id: str,
    actor: str | None,
    channel: str | None,
    verbose: bool = False,
) -> tuple | None:
    """Tenta auto-remediation generica su un'observation.

    Se `obs` ha un `error_class` registrato in
    `auto_remediation.REMEDIATIONS`, invoca il prereq (executor o
    builtin), retry l'executor originale, e ritorna
    (prereq_step, prereq_obs, retry_obs).

    Ritorna None se non c'e' remediation applicabile o se il prereq
    fallisce (caller continua con `obs` originale).

    Pattern install_on_demand generalizzato: stesso codice per
    needs_content_fetch (describe_entries -> read_urls_html), futuro
    needs_ocr, needs_embedding, ecc. Nuova riga in
    `auto_remediation.REMEDIATIONS` = nuovo error_class supportato,
    senza modificare questo helper.
    """
    try:
        from auto_remediation import try_remediate, get_plan
    except Exception:
        return None
    plan = get_plan(obs.get("error_class"))
    if plan is None:
        return None

    def _invoke_prereq(tool_name: str, prereq_args: dict) -> dict:
        # Dispatcher uniforme builtin vs executor reale.
        if tool_name in _BUILTIN_TOOL_HANDLERS:
            return _invoke_builtin_handler(
                tool_name, prereq_args,
                actor=actor, channel=channel, turn_id=turn_id,
            )
        _exec = next(
            (e for e in catalog if e.name == tool_name), None,
        )
        if _exec is None:
            return {"ok": False,
                    "error": f"prereq tool {tool_name!r} non in catalog"}
        return invoke_executor(
            _exec, prereq_args,
            timeout_s=(getattr(_exec, "timeout_s", None) or 120),
            autonomy="supervised", turn_id=turn_id,
            actor=actor, channel=channel,
        )

    result = try_remediate(obs, original_args,
                            invoke_prereq=_invoke_prereq)
    if result is None:
        return None
    prereq_obs, retry_args, plan_info = result
    if not prereq_obs.get("ok"):
        if verbose:
            print(f"[auto_remediation] prereq {plan_info.get('prereq_tool')} "
                  f"failed: {prereq_obs.get('error')!r}")
        return None
    # Costruisci StepLog audit per il prereq.
    _rh_step = StepLog(step_num=0)  # caller imposta step_num corretto
    _rh_step.chosen_tool = plan_info["prereq_tool"]
    _rh_step.raw_args = (
        {k: v for k, v in retry_args.items()
         if k == plan.merge_field}
        if plan.merge_field in retry_args else {}
    )
    _rh_step.resolved_args = dict(_rh_step.raw_args)
    _rh_step.result = prereq_obs
    _rh_step.vaglio_approved = True
    if hasattr(_rh_step, "__dict__"):
        _rh_step.__dict__["auto_remediation"] = plan_info["error_class"]

    # Skip retry: usato per remediation fail-fast (dialog get_inputs).
    # Il prereq_obs (es. needs_inputs decision) e' l'esito di questo step;
    # il caller propaga senza ri-eseguire l'executor originale.
    if plan_info.get("skip_retry"):
        return (_rh_step, prereq_obs, prereq_obs)

    # Retry dell'executor originale con args arricchiti. Dispatcher
    # uniforme builtin vs executor reale (registry modulo-level).
    if original_tool in _BUILTIN_TOOL_HANDLERS:
        retry_obs = _invoke_builtin_handler(
            original_tool, retry_args,
            actor=actor, channel=channel, turn_id=turn_id,
        )
    else:
        _orig_exec = next(
            (e for e in catalog if e.name == original_tool), None,
        )
        if _orig_exec is None:
            return None
        try:
            retry_obs = invoke_executor(
                _orig_exec, retry_args,
                timeout_s=(getattr(_orig_exec, "timeout_s", None) or 120),
                autonomy="supervised", turn_id=turn_id,
                actor=actor, channel=channel,
            )
        except Exception as ex:
            retry_obs = {"ok": False,
                          "error": f"{type(ex).__name__}: {ex}"}
    return (_rh_step, prereq_obs, retry_obs)


# --- L3 Engine v2 dispatcher (ADR 0164) ----------------------------------

def _run_engine(
    query: str,
    catalog: list,
    *,
    turn_id: str,
    actor: str | None,
    channel: str | None,
    lang: str = "it",
    verbose: bool = False,
    progress=None,
    pre_approved_gate: bool = False,
    conversation_id: str = "",
    forced_object: str = "",
    reference_images=None,
    resume_steps=None,
    placement_target: str | None = None,
    user_query_raw: str = "",
    credential_meta=None,
) -> "dict | None":
    """Bridge agent_runtime → engine.dispatch.run_turn.

    Adatta i parametri legacy al nuovo dispatcher, gestisce intent
    extraction, costruisce runtime_ctx, e converte DispatchResult →
    dict legacy shape per minimal changes a caller.

    `reference_images` (ADR 0177 M1, assorbe il path PLANNER legacy ADR 0092):
    foto allegate al turno → seed-state `@uploaded` per l'engine, così il primo
    step reale (find_images_indices) le consuma via `from_step=1`.

    `resume_steps` (ADR 0177 M1, continuazione dialogo): lista di step GIÀ
    eseguiti in un turno precedente ({step,tool,args,observation}) → seed-state
    kind="done" per l'engine: il proposer (consapevole via «FATTO FINORA»)
    pianifica SOLO il resto, la guardia dedup salta le ri-emissioni.
    """
    try:
        from engine import dispatch as _dispatch
        from engine.types import Intent
        from intent_extractor import extract_intent
    except Exception as ex:
        log.warning("engine v2 import failed: %r", ex)
        return None

    # Provider LLM fast (per filler resolve)
    def _llm_call_fast(sys_msg, user_msg, *, max_tokens=80, think=False, **kw):
        # Robustezza + osservabilità (ADR 0181-ext): la fast-call era un
        # `except: return ""` MUTO — un hiccup di connessione LLM (reset/rifiuto)
        # spariva senza traccia e faceva declinare il turno (causa-radice del
        # declino intermittente → legacy). Ora: log + UN retry su errore
        # transitorio; su fallimento persistente ritorna "" e il chiamante
        # procede con intent VUOTO (non declina — vedi sotto).
        for _attempt in (1, 2):
            try:
                from llm_router import LLMRouter
                ck = {"max_tokens": max_tokens, "think": think}
                if kw.get("grammar") is not None:
                    ck["grammar"] = kw["grammar"]
                res = LLMRouter().provider("fast").chat(sys_msg, user_msg, **ck)
                return (getattr(res, "text", res) or "").strip()
            except Exception as _e:  # noqa: BLE001
                log.warning("engine v2 _llm_call_fast tentativo %d fallito: %r",
                            _attempt, _e)
        return ""

    # Provider LLM wise (per Proposer)
    def _llm_call_wise(sys_msg, user_msg, *, max_tokens=2048, think=True, **kw):
        try:
            from llm_router import LLMRouter
            tier = kw.get("tier_override") or "wise"
            # Inoltra `grammar` (GBNF) e `reasoning_budget` al provider: senza
            # questo il Proposer girava SEMPRE non vincolato anche con
            # METNOS_PROPOSER_GRAMMAR=1 → nomi tool allucinati (es. get_issues)
            # fuori dal pool (bug 2/6/2026). chat() supporta grammar →
            # payload['grammar'] a llama-server.
            ck = {"max_tokens": max_tokens, "think": think}
            if kw.get("grammar") is not None:
                ck["grammar"] = kw["grammar"]
            if kw.get("reasoning_budget") is not None:
                ck["reasoning_budget"] = kw["reasoning_budget"]
            res = LLMRouter().provider(tier).chat(sys_msg, user_msg, **ck)
            return (getattr(res, "text", res) or "").strip()
        except Exception as ex:
            log.warning("engine v2 _llm_call_wise: %r", ex)
            return ""

    # Intent extraction
    intent_raw = extract_intent(query, _llm_call_fast)
    if not intent_raw:
        # ROBUSTEZZA (ADR 0181-ext, causa-radice del declino intermittente):
        # intent VUOTO NON è fatale. `extract_intent`→None sia su query davvero
        # vuota sia — soprattutto — su un HICCUP TRANSITORIO della fast-call LLM
        # (connessione resettata → `_llm_call_fast` ritorna ""). Ma l'intent è
        # solo un HINT del prefilter: `build_routing_pool` degrada a
        # full-catalog/BoW su intent vuoto (il ramo upload lo prova già). Quindi
        # NON declinare (era `return None` → cadeva nel legacy con piano
        # degenere): procedi con intent {} e lascia decidere il PROPOSER dalla
        # query. Un turno DAVVERO non-pianificabile lo chiude onestamente il
        # terminator del dispatch (error→answer), non un hiccup del prefilter.
        # (Il caso upload-senza-testo resta coperto: stesso intent {}.)
        intent_raw = {}
    intent = Intent(
        verb=(intent_raw.get("verb") or "").lower(),
        object=(intent_raw.get("object") or "").lower(),
        keywords=list(intent_raw.get("keywords") or []),
        confidence=float(intent_raw.get("confidence") or 1.0),
        lang=lang,
        actions=list(intent_raw.get("actions") or []),
    )
    # Osservabilità (§2.8, 9/7): il TurnLog registra solo intent_verb — i
    # misroute da OBJECT sbagliato (es. «stato del server»→object=approval)
    # erano invisibili in prod. Una riga INFO per turno, costo zero.
    log.info("[intent] verb=%s object=%s conf=%.2f actions=%s q=%r",
             intent.verb or "-", intent.object or "-",
             intent.confidence, intent.actions or "-", query[:60])

    # L'estrazione pre-planner ha gia' scritto il vault senza autorita'
    # unattended. Prima di qualunque uso, un form secret-free sceglie il
    # mandato del binding. Per i compound il callback riprende la query gia'
    # redatta; per il puro set_credentials termina dopo l'aggiornamento.
    _credential_meta = [m for m in (credential_meta or [])
                        if isinstance(m, dict) and m.get("domain")]
    _web_credential_meta = [m for m in _credential_meta
                            if (m.get("context") or {}).get("binding") == "web"]
    if _web_credential_meta:
        from credential_mandates import dialog_step
        domains = [m["domain"] for m in _web_credential_meta]
        pure_store = _is_credentials_store_only_intent(intent)
        payload = {
            "title": msg("MSG_CREDENTIAL_MANDATE_TITLE",
                         binding=", ".join(domains)),
            "dialog": [dialog_step()],
            "fmt": "auto",
            "on_complete": {
                "type": "set_credential_mandates_and_resume",
                "bindings": domains,
                "resume_query": "" if pure_store else query,
                "conversation_id": conversation_id or "",
            },
        }
        result = {
            "ok": True,
            "decision": "needs_inputs", "needs_inputs": payload,
            "results": [], "final_message_hint": payload["title"],
        }
        step = StepLog(step_num=1)
        step.chosen_tool = "set_credentials"
        step.raw_args = {"bindings": domains}
        step.resolved_args = dict(step.raw_args)
        step.result = result
        return {
            "steps": [step],
            "final_text": payload["title"],
            "final_kind": "ask",
            "framework_hash": "",
            "verb": intent.verb,
            "object": intent.object,
            "keywords": intent.keywords,
            "match_source": "credential_extraction",
            "elapsed_ms": 0,
            "error_class": "",
            "needs_inputs_obs": result,
            "gate_obs": None,
        }
    if (_credential_meta and _is_credentials_store_only_intent(intent)):
        domains = [m["domain"] for m in _credential_meta]
        result = {
            "ok": True,
            "entries": [{"binding": domain, "status": "configured",
                         "fields_present": ["username", "password"]}
                        for domain in domains],
            "metadata": {"stored": len(domains)},
        }
        step = StepLog(step_num=1)
        step.chosen_tool = "set_credentials"
        step.raw_args = {"bindings": domains}
        step.resolved_args = dict(step.raw_args)
        step.result = result
        return {
            "steps": [step],
            "final_text": msg("MSG_CREDENTIALS_STORED",
                              domain=", ".join(domains)),
            "final_kind": "answer", "framework_hash": "",
            "verb": intent.verb, "object": intent.object,
            "keywords": intent.keywords,
            "match_source": "credential_extraction", "elapsed_ms": 0,
            "error_class": "", "needs_inputs_obs": None, "gate_obs": None,
        }

    # §2.11 — DISAMBIGUAZIONE ROUTING deterministica (no LLM). Su query AMBIGUA
    # sull'oggetto (≥2 oggetti-produttori in gara, intent ne ha scartato uno;
    # NON un compound) chiedi con un form invece di indovinare. Sulla RIPRESA
    # (forced_object = scelta utente) pinna l'oggetto del produttore e non
    # richiedere. No-op per ogni query non-ambigua (gate stretto).
    try:
        import route_disambiguation as _rdis
        if forced_object:
            for _a in (intent.actions or []):
                if isinstance(_a, dict) and (_a.get("verb") or "") in (
                        "read", "find", "get", "list"):
                    _a["object"] = forced_object
                    break
            if (intent.verb or "") in ("read", "find", "get", "list"):
                try:
                    intent.object = forced_object
                except Exception:  # noqa: BLE001 — intent best-effort
                    pass
        else:
            _amb = _rdis.detect_object_ambiguity(query, intent)
            if _amb:
                return {
                    "steps": [], "final_text": "", "final_kind": "needs_inputs",
                    "framework_hash": "", "verb": intent.verb,
                    "object": intent.object, "keywords": intent.keywords,
                    "match_source": "route_disambiguation", "elapsed_ms": 0,
                    "error_class": None, "gate_obs": None,
                    "needs_inputs_obs": _rdis.build_disambiguation_form(
                        query, _amb)}
    except Exception as _de:  # noqa: BLE001 — disambiguazione best-effort
        log.debug("route_disambiguation noop: %r", _de)

    # --- Chat-driven placement (ADR 0034): il PC bersaglio è già stato risolto a
    # livello run_turn (PRIMA di fast_path/engine, così entrambi i path
    # instradano) e passato qui come `placement_target` (nome device, o None =
    # server). La `query` in arrivo è già ripulita dell'adjunct di destinazione.
    # `_target_name` va a invoke_executor via _invoke; None → esecuzione locale.
    _target_name = placement_target
    try:
        import credential_mandates as _credential_mandates
        _site_credential_mode = _credential_mandates.site_mode_for_query(query)
    except Exception:
        _site_credential_mode = "default"
    # ADR 0191 P1: master + tecniche stealth indipendenti, risolti per-turno
    # dalle preferenze dell'attore. Il broker applica poi il ceiling deployment.
    try:
        import users as _users
        import devices as _devices
        # Fix adversarial #9: la pref e' per-UTENTE, non per-actor grezzo.
        _site_owner = _devices.owner_id_for_actor(actor) or actor
        _site_stealth_pref = _users.get_pref(
            _site_owner, "sites_stealth", "off") or "off"
        _site_browser_mode = _users.get_pref(
            _site_owner, "sites_browser_mode", "headless") or "headless"
        _site_stealth_techniques = [
            spec["name"]
            for spec in _users.sites_stealth_preference_specs()
            if (_users.get_pref(
                _site_owner, spec["preference_key"], "off") or "off") == "on"
        ]
        _site_lang = _users.get_pref(_site_owner, "lang", None) or ""
    except Exception:
        _site_stealth_pref = "off"
        _site_browser_mode = "headless"
        _site_stealth_techniques = []
        _site_lang = ""

    _catalog_by_name = {
        e.name: e for e in catalog if getattr(e, "name", None)
    }

    def _effective_executor_args(tool_name: str, args: dict) -> dict:
        """Apply the same planner-invisible arguments on sync and async paths."""
        effective_args = args
        if tool_name in {"open_sites", "login_sites"}:
            # Internal, planner-invisible per-query restriction. The broker
            # persists it on the session, so a later step cannot re-enable it.
            effective_args = {
                **args, "_credential_mode": _site_credential_mode,
            }
        if tool_name == "open_sites":
            # ADR 0191 P1: stealth fissato all'apertura sessione (planner-invisible).
            # Fix #9: `_lang` per locale/timezone del contesto browser.
            effective_args = {**effective_args,
                              "_stealth": _site_stealth_pref,
                              "_stealth_techniques": _site_stealth_techniques,
                              "_browser_mode": _site_browser_mode,
                              "_lang": _site_lang}
        return effective_args

    # Invoke executor callback wrapped — Executor v2 chiama via tool name
    def _invoke(tool_name: str, args: dict) -> dict:
        if tool_name in _BUILTIN_TOOL_HANDLERS:
            return _invoke_builtin_handler(
                tool_name, args, actor=actor or "host", channel=channel or "")
        exec_obj = _catalog_by_name.get(tool_name)
        if exec_obj is None:
            return {"ok": False, "error": f"tool '{tool_name}' non in catalog",
                     "error_class": "tool_unknown"}
        try:
            return invoke_executor(
                exec_obj, _effective_executor_args(tool_name, args),
                timeout_s=(getattr(exec_obj, "timeout_s", None) or 120),
                autonomy="supervised", turn_id=turn_id,
                actor=actor, channel=channel, target_device=_target_name)
        except Exception as ex:
            return {"ok": False, "error": f"{type(ex).__name__}: {ex}",
                     "error_class": "exception"}

    def _submit(tool_name: str, args: dict):
        """Async twin used only after the engine and scheduler both admit it."""
        exec_obj = _catalog_by_name.get(tool_name)
        if exec_obj is None or tool_name in _BUILTIN_TOOL_HANDLERS:
            raise ValueError(f"tool '{tool_name}' is not async-admissible")
        return submit_executor(
            exec_obj, _effective_executor_args(tool_name, args),
            timeout_s=(getattr(exec_obj, "timeout_s", None) or 120),
            autonomy="supervised", turn_id=turn_id,
            actor=actor, channel=channel, target_device=_target_name)

    def _can_parallelize(tool_name: str) -> bool:
        exec_obj = _catalog_by_name.get(tool_name)
        if exec_obj is None or tool_name in _BUILTIN_TOOL_HANDLERS:
            return False
        from executor_scheduler import can_schedule_parallel
        return can_schedule_parallel(exec_obj)

    runtime_ctx = {
        "actor": actor or "",
        "lang": lang,
        "channel": channel or "",
        # Identificatore opaco e filesystem-safe del turno. Serve ai sink
        # create-only per generare destinazioni nuove senza timestamp inventati
        # dal planner e senza rischio di overwrite su replay concorrenti.
        "turn_id": turn_id or "",
        "_gate_approved": bool(pre_approved_gate),
        "conversation_id": conversation_id or "",
        # Destinazione del turno (nome device, ""=server): i gate la usano nel
        # prompt («su PC-X», non «questo computer») — bug live 3db55063 6/7.
        "target_device": _target_name or "",
        # Query RAW dell'utente (CON l'adjunct di destinazione): il gate-resume
        # DEVE rilanciare questa, non la query strippata — altrimenti la
        # ri-esecuzione approva su un host diverso solo se lo sticky target
        # regge (bug live 981ddc9f 6/7: senza «su pc-roberto» nel resume, la
        # delete sarebbe stata LOCALE senza sticky).
        "user_query_raw": user_query_raw or query,
    }

    # Seed-state (ADR 0177 M1): foto allegate al turno (ADR 0092) → step 0
    # virtuale `@uploaded` con entries=[{path, reference_image, source}]. Stessa
    # forma del path legacy (entries con il campo `reference_image`, singolare di
    # `reference_images`) così il consumer-match porta i path nell'arg
    # find_images_indices.reference_images. Sostituisce il blocco legacy.
    seed_state = None
    _refs = [p for p in (reference_images or [])
             if isinstance(p, str) and p.strip()]
    if _refs:
        from engine.types import StepRun as _StepRun
        _upload_entries = [
            {"path": p, "reference_image": p, "source": "upload"}
            for p in _refs
        ]
        _upload_obs = {
            "ok": True, "entries": _upload_entries, "_virtual": True,
            "_kind": "uploaded_reference_images", "n": len(_upload_entries),
        }
        seed_state = [_StepRun(
            step_idx=0, tool="@uploaded",
            args={"source": "upload", "n": len(_upload_entries)},
            result=_upload_obs, ok=True, latency_ms=0,
            kind="input")]  # consumabile (foto), non «fatto»

    # Seed-state continuazione dialogo (ADR 0177 M1): gli step PRODUTTORI già
    # eseguiti nel turno che si era fermato a chiedere all'utente → kind="done".
    # Il proposer li vede in «FATTO FINORA» e pianifica solo il resto; la guardia
    # dedup salta le ri-emissioni; gli step a valle li referenziano via from_step.
    # Mutuamente esclusivo con le foto (un turno è o upload o ripresa).
    #
    # I marker di dialogo (get_inputs/get_approval) NON entrano nel seed: non
    # sono produttori (nessun risultato-lista riusabile), occuperebbero un indice
    # step rompendo l'allineamento `from_step`/`${stepN}` (bug e2e 23/6:
    # ${step2.summary} → get_inputs invece del producer). Le scelte raccolte
    # restano nella query/contesto; il proposer pianifica gli step a valle.
    # step_idx RINUMERATO contiguo (1..K) così from_step degli step nuovi è
    # stabile a prescindere dalla numerazione del turno originale.
    _DIALOG_MARKERS = {"get_inputs", "get_approval", "@uploaded"}
    _rsteps = [s for s in (resume_steps or [])
               if isinstance(s, dict)
               and (s.get("tool") or "") not in _DIALOG_MARKERS]
    if _rsteps and seed_state is None:
        from engine.types import StepRun as _StepRun
        seed_state = []
        for _i, _s in enumerate(_rsteps, start=1):
            _obs = _s.get("observation") if isinstance(_s.get("observation"), dict) else {}
            seed_state.append(_StepRun(
                step_idx=_i,
                tool=_s.get("tool") or "",
                args=_s.get("args") or {},
                result=_obs, ok=bool(_obs.get("ok", True)),
                latency_ms=0, kind="done"))

    # Engine v2 passa lo stesso catalog a Proposer e Validator: includi i
    # builtin in-process (describe_entries/classify_entries) altrimenti
    # mancanti → Validator falsa `tool_unknown` (§11, fix wiring B).
    catalog_v2 = _engine_v2_catalog_with_builtins(catalog)

    try:
        result = _dispatch.run_turn(
            query=query, intent=intent, catalog=catalog_v2,
            invoke_executor_cb=_invoke,
            submit_executor_cb=_submit,
            can_parallelize_cb=_can_parallelize,
            llm_call_wise=_llm_call_wise,
            llm_call_fast=_llm_call_fast,
            vaglio_guard=guard_check,  # guardia forbidden-path PRE-invoke (§sicurezza)
            runtime_ctx=runtime_ctx,
            seed_state=seed_state,  # foto allegate @uploaded (ADR 0177 M1)
            turn_id=turn_id, lang=lang, verbose=verbose,
            progress=progress)
    except Exception as ex:
        import traceback as _tb
        log.warning("engine.dispatch.run_turn failed: %r\n%s", ex, _tb.format_exc())
        return None

    # Converti DispatchResult → dict shape legacy (per minimal change caller)
    steps_out = []
    needs_inputs_obs = None
    gate_obs = None
    # §2.11 errore-runtime→form: il form viene dal RECOVERY (non da uno step) →
    # propagalo dal DispatchResult diretto (vedi _error_disambiguation_form).
    if getattr(result, "needs_inputs_obs", None):
        needs_inputs_obs = result.needs_inputs_obs
    if result.run:
        for s in result.run.steps:
            sl = StepLog(step_num=s.step_idx)
            sl.chosen_tool = s.tool
            sl.raw_args = dict(s.args)
            sl.resolved_args = dict(s.args)
            sl.exec_ms = s.latency_ms
            sl.result = s.result
            steps_out.append(sl)
            # §7.3: propaga needs_inputs all'upstream per dialog handling
            if isinstance(s.result, dict) and s.result.get("decision") == "needs_inputs":
                needs_inputs_obs = s.result
            # gate-resume (20/6): il gate get_approval (decision=input_required)
            # ha GIA' salvato il proprio dialog_pending + expandable_caps (FIX 1);
            # propaga il suo result cosi' l'upstream surfacea gli expandable_caps
            # al log → il channel daemon costruisce la inline keyboard
            # (Approva/Rifiuta/Annulla). Senza, il push e' solo-testo.
            if (isinstance(s.result, dict)
                    and s.result.get("decision") == "input_required"
                    and s.tool == "get_approval"):
                gate_obs = s.result
    # Tag di destinazione (ADR 0034): NON ottimistico. Il tag/campo del turno si
    # deriva dagli step che sono REALMENTE girati sul device (marker
    # `_ran_on_device`), in _finalize_engine_result — così un'operazione non
    # impacchettabile, girata in locale nonostante la destinazione, non viene
    # etichettata come remota.
    return {
        "steps": steps_out,
        "final_text": result.final_text,
        "final_kind": result.final_kind,
        "framework_hash": result.framework_hash,
        "verb": intent.verb,
        "object": intent.object,
        "keywords": intent.keywords,
        "match_source": result.match_source,
        "elapsed_ms": result.elapsed_ms,
        "error_class": result.error_class,
        "needs_inputs_obs": needs_inputs_obs,
        "gate_obs": gate_obs,
    }


def _finalize_engine_result(log, _engine_v2_res, *, actor, channel,
                            conversation_id, turn_id):
    """Mappa il DispatchResult dell'engine (dict legacy-shape da `_run_engine`)
    sul `TurnLog`: estende `steps`, gestisce needs_inputs/gate dialog, setta
    final_kind/message/intent_verb. Ritorna SEMPRE il log pronto (write incluso).

    Estratto (ADR 0177 M1) per riuso fra il path principale (run_turn, non-upload)
    e il branch foto-allegate (engine-uploads). Comportamento byte-invariato."""
    log.steps.extend(_engine_v2_res.get("steps") or [])
    # §7.3: se Engine ha ritornato needs_inputs → handle dialog
    _ni = _engine_v2_res.get("needs_inputs_obs")
    if _ni:
        try:
            from orchestration import orchestrate_needs_inputs
            _sender_id = f"{channel or 'http'}:{actor or 'host'}"
            if conversation_id:
                _sender_id = f"{_sender_id}:{conversation_id}"
            _dlg = orchestrate_needs_inputs(
                _ni, sender_id=_sender_id,
                actor=actor or "host", channel=channel or "http",
                origin_turn_id=turn_id or log.turn_id or "",
            )
            if isinstance(_dlg, dict) and _dlg.get("ok"):
                _hint = (_dlg.get("final_message_hint")
                          or _ni.get("final_message_hint") or "")
                if _hint:
                    log.final_kind = "ask"
                    log.final_message = _hint
                    log.intent_verb = _engine_v2_res.get("verb", "") or ""
                    # Propaga expandable_caps cosi' HTTP route puo'
                    # salvare cap_pending per consume next-turn.
                    _caps = _dlg.get("expandable_caps")
                    if isinstance(_caps, list) and _caps:
                        log.expandable_caps = _caps
                    log.ts_end = time.time()
                    log.write()
                    return log
        except Exception as _ex:
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "engine v2 needs_inputs handler failed: %s", _ex)
            # fallthrough: render hint plain
    # gate-resume (20/6): il gate get_approval ha messo in PAUSA (FIX 1)
    # e ha GIA' salvato il proprio dialog_pending. Surfacea i suoi
    # `expandable_caps` al log cosi' il channel daemon costruisce la
    # inline keyboard (Approva/Rifiuta/Annulla) — senza, il push verso
    # Telegram e' solo-testo e il tap/risposta non risolve nulla.
    # Roberto 20/6: i gate DEVONO usare i pulsanti del canale.
    _gate = _engine_v2_res.get("gate_obs")
    if _gate and isinstance(_gate, dict):
        log.final_kind = "ask"
        log.final_message = (_gate.get("final_message_hint")
                             or _engine_v2_res.get("final_text") or "")
        _gcaps = _gate.get("expandable_caps")
        if isinstance(_gcaps, list) and _gcaps:
            log.expandable_caps = _gcaps
        log.intent_verb = _engine_v2_res.get("verb", "") or ""
        log.ts_end = time.time()
        log.write()
        return log
    log.final_kind = _engine_v2_res.get("final_kind") or "answer"
    log.final_message = _engine_v2_res.get("final_text") or ""
    # Universal §7.3: se needs_inputs e nessun handler, ma c'e' un
    # final_message_hint nel result, usa quello (UX onestà).
    if not log.final_message and _ni and isinstance(_ni, dict):
        hint = _ni.get("final_message_hint")
        if isinstance(hint, str) and hint:
            log.final_message = hint
    log.intent_verb = _engine_v2_res.get("verb", "") or ""
    _apply_device_tag(log)   # ADR 0034: tag 📍 + campo dal device REALE degli step
    log.ts_end = time.time()
    log.write()
    return log


def _apply_device_tag(log) -> None:
    """Chat-driven placement (ADR 0034): se uno step è girato DAVVERO su un
    device (marker `_ran_on_device`, posto da invoke_executor sulla consegna
    remota), imposta `log.target_device` (campo strutturato per l'UI). Basato
    sull'esecuzione REALE, mai ottimistico: un'operazione girata in locale
    nonostante la destinazione non viene etichettata come remota.

    Separazione dei livelli (2026-07-04): il marcatore TESTUALE 📍<nome> NON
    viene più anteposto a `final_message` qui. Il web usa `target_device` nella
    meta line della risposta; i canali solo-testo (Telegram) lo antepongono nel
    formattatore (`channels/daemon._format_turn_result`). Così un canale non
    duplica ciò che l'altro rende in modo strutturato."""
    _dev = None
    for _s in (log.steps or []):
        _r = getattr(_s, "result", None)
        if isinstance(_r, dict) and _r.get("_ran_on_device"):
            _dev = _r["_ran_on_device"]
            break
    if not _dev:
        return
    log.target_device = _dev


# --- Strato 3 escalation UI (task #30) ----------------------------------

def _orchestrate_strato3_escalation(
    *, user_query: str, lang: str, actor: str, channel: str,
    conversation_id: str, consec_errors: int,
) -> dict | None:
    """Apre un dialog `get_inputs` con 4 azioni quando l'utente ha
    rifiutato ≥3 pipeline consecutive per la stessa query.

    Azioni offerte:
      1. **synth** — chiede al sintetizzatore un executor specifico.
      2. **frontier** — delega a LLM esterno di frontiera (Opus/Sonnet/GPT-5).
      3. **reformulate** — utente riformula la query da zero.
      4. **abandon** — termina senza fare nulla.

    Callback `restart_turn_with_chosen_query`: il `chosen_query` derivato
    dalla scelta diventa la nuova `user_query` del turno successivo.
    Determinismo §7.9: niente LLM, lookup deterministico.

    Ritorna il dict result di `invoke_get_inputs_internal` (con
    `final_message_hint`, `expandable_caps`) oppure None se errore.
    """
    from orchestration import invoke_get_inputs_internal
    if lang == "en":
        title = "I tried 3 different paths, all rejected. What do you want?"
        descr = (f"Pipelines attempted and rejected ({consec_errors} ✗): "
                  "the answer needs a different shape. Choose how to proceed.")
        choices = [
            "retry: try the same path again (engine may have been updated)",
            "synth: build a dedicated executor for this",
            "frontier: ask an external high-stakes LLM",
            "reformulate: I rewrite the request",
            "abandon: stop here",
        ]
        prompt_text = "Choose one of the five actions"
    else:
        title = "Ho provato 3 strade diverse, tutte rifiutate. Cosa preferisci?"
        descr = (f"Pipeline tentate e rifiutate ({consec_errors} ✗): "
                  "la risposta richiede una forma diversa. Scegli come procedere.")
        choices = [
            "ritenta: prova di nuovo lo stesso path (motore puo' essere aggiornato)",
            "sintetizza: costruisci un executor dedicato",
            "frontier: chiedi a un LLM esterno di frontiera",
            "riformula: riscrivo la richiesta",
            "abbandona: fermati qui",
        ]
        prompt_text = "Scegli una delle cinque azioni"
    sender_id = f"{channel}:{actor}" if channel else actor
    on_complete = {
        "type": "strato3_choice_dispatch",
        "original_query": user_query,
        "lang": lang,
        "actor": actor,
        "channel": channel,
        "conversation_id": conversation_id,
        "consec_errors": consec_errors,
    }
    return invoke_get_inputs_internal(
        sender_id=sender_id,
        title=title,
        description=descr,
        dialog=[{
            "var": "chosen_action",
            "prompt": prompt_text,
            "schema": {"kind": "choice", "choices": choices},
        }],
        fmt="auto",
        on_complete=on_complete,
        actor=actor,
        channel=channel,
    )


def _strato3_routing_changed(user_query: str, *, lang: str) -> bool:
    """True se il routing CORRENTE per `user_query` NON riprodurrebbe piu'
    alcuna pipeline gia' rifiutata dall'utente → i ✗ storici sono STANTII.

    Deadlock strato-3 (bug 13/6/2026): i ✗ consecutivi che alimentano
    l'escalation possono riferirsi a pipeline che il routing NON genera piu'
    (fix di intent/vocab/prefilter, nuovo executor, undeprecate, fastpath
    bonificato). In quel caso bloccare e' un cane che si morde la coda — non
    riesce perche' bloccato, bloccato perche' (storicamente) non riusciva. Se
    la forma che il sistema proporrebbe ORA e' cambiata, diamo una chance
    all'engine invece del dialog.

    Decisione ESATTA, non un proxy: ricostruiamo la pipeline che il sistema
    proporrebbe ORA con le STESSE funzioni di produzione — `build_routing_pool`
    + `get_proposer().propose()` (Mētis, grammar, verb_filter) — SENZA
    eseguirla (propose() non ha effetti). Se la firma della pipeline proposta
    non e' fra quelle rifiutate, il routing e' cambiato → niente escalation.
    Determinismo §7.9: seed pinnato + affinity boost (§11) rendono il routing
    riproducibile; il propose e' memoizzato (LRU per query+intent+lang) → il
    turno reale che segue riusa il risultato senza una seconda chiamata wise.
    Path raro (solo consec>=3).

    Fail-safe (§2.8): su qualunque incertezza (intent incompleto, propose None,
    errore) ritorna False → PRESERVA l'escalation (comportamento storico).
    """
    try:
        from turn_feedback import rejected_pipelines_for_query
        rejected = rejected_pipelines_for_query(user_query)
        if not rejected:
            # Nessuna pipeline rifiutata da riprodurre → niente deadlock.
            return True
        rejected_sigs = {tuple(p) for p in rejected if p}

        from intent_extractor import extract_intent
        from engine.types import Intent
        from engine.routing_pool import build_routing_pool
        from engine.proposer import get_proposer

        def _fast(sys_msg, user_msg, *, max_tokens=80, think=False, **kw):
            from llm_router import LLMRouter
            ck = {"max_tokens": max_tokens, "think": think}
            if kw.get("grammar") is not None:
                ck["grammar"] = kw["grammar"]
            res = LLMRouter().provider("fast").chat(sys_msg, user_msg, **ck)
            return (getattr(res, "text", res) or "").strip()

        def _wise(sys_msg, user_msg, *, max_tokens=2048, think=True, **kw):
            from llm_router import LLMRouter
            ck = {"max_tokens": max_tokens, "think": think}
            if kw.get("grammar") is not None:
                ck["grammar"] = kw["grammar"]
            if kw.get("reasoning_budget") is not None:
                ck["reasoning_budget"] = kw["reasoning_budget"]
            res = LLMRouter().provider(kw.get("tier_override") or "wise").chat(
                sys_msg, user_msg, **ck)
            return (getattr(res, "text", res) or "").strip()

        ir = extract_intent(user_query, _fast) or {}
        intent = Intent(
            verb=(ir.get("verb") or "").lower(),
            object=(ir.get("object") or "").lower(),
            keywords=list(ir.get("keywords") or []),
            confidence=float(ir.get("confidence") or 1.0),
            lang=lang,
            actions=list(ir.get("actions") or []),
        )
        if not intent.is_complete():
            return False  # intent incerto → preserva escalation
        # Catalog + pool COME in produzione (composer visibility + builtin
        # in-proc): l'esatta superficie che il proposer vede in dispatch.
        catalog = _engine_v2_catalog_with_builtins(
            filter_for_visibility(load_catalog(), VISIBILITY_COMPOSER))
        pool = build_routing_pool(user_query, intent, catalog)
        if not pool:
            return False
        framework = get_proposer().propose(
            query=user_query, intent=intent, pool=pool,
            excluded_hashes=set(), llm_call=_wise, lang=lang, catalog=catalog)
        if framework is None:
            return False  # non determinabile → preserva escalation
        import dataclasses
        d = dataclasses.asdict(framework) if dataclasses.is_dataclass(framework) else {}
        proposed_sig = tuple(
            s.get("tool") for s in (d.get("steps") or [])
            if s.get("tool") and s.get("tool") != "final_answer")
        if not proposed_sig:
            return False
        # Routing cambiato sse la pipeline proposta ORA non e' fra le rifiutate.
        return proposed_sig not in rejected_sigs
    except Exception as ex:
        log.warning("strato3 routing-change check fallito: %r", ex)
        return False


# --- Loop pianificatore (multistep con tool-use nativo) -------------------

def run_turn(user_query, *, model=None, k=None, k_min=5, k_max=8, think=None, progress=None,
             cap_steps=DEFAULT_CAP_STEPS, cap_same=DEFAULT_CAP_SAME_EXECUTOR,
             scratchpad_threshold=SCRATCHPAD_THRESHOLD_BYTES,
             actor="host", channel="", conversation_id="",
             reference_images=None,
             resume_with_scratchpad=None,
             pre_approved_gate=False,
             allow_disambig_synth=True,
             bypass_rejected_pipelines=False,
             forced_object="",
             verbose=False):
    """
    Se k=None (default v1.1), usa adaptive K fra k_min e k_max.
    Se k=int, usa K fisso (legacy/debug).

    `reference_images` (5/5/2026, ADR 0092): lista di path di foto allegate
    al turno (Telegram caption + photo, HTTP drag&drop). Se non vuoto, viene
    iniettato uno step 0 virtuale `@uploaded` nello scratchpad con
    `entries=[{path, reference_image, source}]`. Il PLANNER vede le foto
    come prima entry (consumer-match Layer 3 porta find_images_indices nel
    pool); chiama `find_images_indices(from_step=1, idx="scene")` e
    l'auto-explode (consumer match) inietta `reference_images=[paths]`.

    `resume_with_scratchpad` (12/5/2026, fix bug propose+notify): lista di
    step records pre-existing nella forma `[{step, tool, args, observation}]`.
    Quando presente, il turno parte con scratchpad gia' popolato e il loop
    inizia da step_num = len(prior_steps) + 1. Usato da
    `orchestration._process_resume_planner_with_dialog_values` per riprendere
    pipeline multi-pipeline dopo un get_inputs MID-pipeline. Bypassa
    fast_path / seed_step (sono inutili: il context ha gia' steps).
    Determinismo §7.9.
    """
    log = TurnLog(ts_start=time.time(), user_query=user_query,
                   actor=actor or "host", channel=channel or "",
                   conversation_id=conversation_id or "")

    # ── Strato 1 (ADR 0089): estrazione automatica delle credenziali ──
    # Se la query contiene user/pwd inline, salviamoli cifrati subito e
    # passiamo al PLANNER una versione redacted. La metadata (solo
    # domain+context, NO password) viene iniettata nel system prompt come
    # blocco prescrittivo cosi' il PLANNER puo' settare credentials_domain
    # nei tool che lo accettano (admin per CIFS, login_urls per HTTP web).
    redacted_query, extracted_meta = apply_credentials_extraction(user_query)
    user_query_for_run = redacted_query
    if extracted_meta:
        log.user_query = redacted_query  # niente plaintext nel log
        log.redacted = True
        log.n_redacted_fields = max(log.n_redacted_fields, len(extracted_meta))

    # Admin chat commands shortcut (11/5/2026): `/admin user <action>` per
    # gestire utenti e pair URL via chat o Telegram. Determinismo §7.9:
    # niente PLANNER, niente synth. Restricted a actor='host'.
    try:
        from admin_chat_commands import matches as _adm_match, dispatch as _adm_disp
        if _adm_match(user_query_for_run):
            import os as _os
            origin = _os.environ.get("METNOS_PUBLIC_ORIGIN",
                                       "http://localhost:8770")
            adm_msg = _adm_disp(user_query_for_run, actor=actor, origin=origin)
            if adm_msg is not None:
                log.turn_id = uuid.uuid4().hex[:16]
                log.final_message = adm_msg
                log.final_kind = "answer"
                log.ts_end = time.time()
                log.write()
                return log
    except ImportError:
        pass

    # ── Strato 3 escalation UI (task #30, 24/5/2026) ──────────────────
    # Quando l'utente ha rifiutato >=3 pipeline consecutive per la stessa
    # query, gli strati 1 (soft prompt) e 2 (hard constraint) non bastano.
    # Apriamo un dialog `get_inputs` con 4 azioni esplicite e ritorniamo
    # un final_kind="ask" senza chiamare il PLANNER. La scelta utente
    # diventa la nuova query del prossimo turno via `restart_turn_with_chosen_query`.
    # Determinismo §7.9 (no LLM in questo path).
    try:
        from turn_feedback import count_consecutive_errors_for_query
        _consec = count_consecutive_errors_for_query(user_query_for_run)
    except Exception:
        _consec = 0
    # bypass_rejected_pipelines (strato 3 "ritenta"): salta dialog escalation
    # e qualsiasi anti_skill filter, esegue il turno come fresh.
    if bypass_rejected_pipelines:
        _consec = 0
    # Anti-deadlock (bug 13/6/2026): NON escalare se il routing corrente
    # produrrebbe una pipeline NUOVA (mai rifiutata) — i ✗ storici sono
    # stantii (es. dopo un fix di intent/vocab/fastpath). Vedi
    # `_strato3_routing_changed` (deterministico §7.9). Fail-safe: in dubbio
    # ritorna False → escalation preservata.
    if (_consec >= 3 and channel != ""
            and not _strato3_routing_changed(user_query_for_run,
                                             lang=DEFAULT_LANG)):
        try:
            _ask_result = _orchestrate_strato3_escalation(
                user_query=user_query_for_run,
                lang=DEFAULT_LANG,
                actor=actor or "host",
                channel=channel,
                conversation_id=conversation_id or "",
                consec_errors=_consec,
            )
            if _ask_result is not None:
                log.turn_id = uuid.uuid4().hex[:16]
                log.final_message = _ask_result.get("final_message_hint", "")
                log.final_kind = "ask"
                log.expandable_caps = _ask_result.get("expandable_caps", []) or []
                log.ts_end = time.time()
                log.write()
                return log
        except Exception as ex:
            log.warning("strato3 escalation failed: %s", ex) if hasattr(log, "warning") else None

    # Blocco prescrittivo per il PLANNER: lista delle credenziali estratte
    # nel turno corrente. Solo metadata (domain + context), MAI le pwd.
    # ADR 0092: il PLANNER è caricato da runtime/prompts/<lang>/planner/.
    # Fase C (11/5/2026): rendering 3-layer (_core + sections + _footer) via
    # `prompt_loader.compose()`. Selettore deterministico delle sezioni via
    # `vocab.sections_for_object(intent.object)`. Quando l'intent extractor
    # non si e' ancora eseguito (early route_info=None nel turno) o l'object
    # e' unknown, passiamo sections=None → composer include TUTTE le sezioni
    # (degrade graceful). Lo split avviene piu' avanti nel turno via re-render
    # se serve, ma per il PLANNER prompt sistema il render iniziale e' OK con
    # all-sections — il routing si concretizza ai prossimi step.
    # Lang esplicito al call site (5/5/2026): default da config.DEFAULT_LANG.
    try:
        # `route_info` non e' ancora disponibile a questo punto (precede
        # l'intent extractor del turno principale). Per il primo prompt
        # PLANNER passiamo sections=None (= all sections) come degrade
        # graceful. Refactor futuro: rendering lazy del system prompt ad
        # ogni step, basato sull'intent extractor risolto.
        _planner_sections = None
    except Exception:
        _planner_sections = None
    _now_vars = _render_now_vars()
    planner_system = prompt_loader.compose(
        "planner",
        DEFAULT_LANG,
        sections=_planner_sections,
        vocab_actions=_vocab_actions(),
        vocab_objects=_vocab_objects(),
        vocab_qualifiers=_vocab_qualifiers(),
        project_paths=_render_project_paths_block(),
        users_known=_render_users_known_block(),
        telos_block=_render_telos_block(DEFAULT_LANG),
        rejected_block=("" if bypass_rejected_pipelines
                         else _render_rejected_pipelines_block(user_query, DEFAULT_LANG)),
        **_now_vars,
    )
    # ADR 0149 (18/5/2026): instruction block per il by-product
    # `canonical_query`. Iniettata solo quando il flag e' on, per non
    # alterare il behavior di default. Forma compatta + esempi
    # bilingua. Niente azione su tool selection: solo formato output.
    if os.environ.get("METNOS_CANONICAL_QUERY", "1") == "1":
        _cq_block = [
            "",
            "═" * 70,
            "CANONICAL_QUERY — BY-PRODUCT NORMALIZZAZIONE (ADR 0149)",
            "═" * 70,
            "Nel JSON di output, OLTRE a `name` e `arguments`, DEVI emettere",
            "il campo `canonical_query`: forma LEMMA della richiesta utente.",
            "",
            "Regole di flessione (TUTTE LE LINGUE — IT, EN, ES, FR, DE, ...):",
            "  • Verbi  → INFINITO  (essere/be/ser/sein, non è/is/es/ist)",
            "  • Nomi   → SINGOLARE non marcato (file/file, mail/email/mail)",
            "  • Articoli/preposizioni clitiche → RIMOSSI (i, il, le, the, ...)",
            "  • Aggettivi possessivi/dimostrativi → RIMOSSI (mio, mia, this, ...)",
            "  • Argomenti specifici → RIMOSSI (path, URL, ID, numeri, nomi propri,",
            "                                   glob, date concrete come 'oggi'/'domani')",
            "",
            "Lingua: stessa della query utente. NON tradurre.",
            "Lunghezza: ≤ 50 token.",
            "",
            "OK (IT):",
            '  "che ora e?"                       → "che ora essere"',
            '  "che ore sono?"                    → "che ora essere"  (plurale → sing.)',
            '  "elenca i file in /tmp"            → "elencare file"',
            '  "trova *.py in /opt/runtime"       → "trovare file"    (glob rimosso)',
            '  "leggi /tmp/x.txt"                 → "leggere file"',
            '  "le mie mail importanti di oggi"   → "leggere mail importante"',
            '  "dove sono?"                       → "trovare posizione"',
            '  "scarica https://x.com/api"        → "scaricare url"',
            "",
            "OK (EN):",
            '  "what time is it?"                 → "what time be"',
            '  "list the files in /tmp"           → "list file"',
            '  "find my latest photos"            → "find photo recent"',
            "",
            "OK (ES):",
            '  "qué hora es?"                     → "qué hora ser"',
            '  "encuentra mis archivos"           → "encontrar archivo"',
            "",
            "ERRORE:",
            '  "leggi /tmp/x.txt" → "leggi /tmp/x.txt"   (verbatim, non lemma)',
            '  "trova *.py"       → "trovare *.py"       (argomenti residui)',
            '  "che ora e?"       → "get_now"            (nome tool, non lemma)',
            '  "che ore sono?"    → "che ore essere"     (plurale non normalizzato)',
            '  "what time is it?" → "che ora essere"     (tradotto in IT — vietato)',
            "═" * 70,
            "",
        ]
        planner_system = planner_system + "\n" + "\n".join(_cq_block)
    if extracted_meta:
        lines = [
            "",
            "═" * 70,
            "CREDENZIALI ESTRATTE DALLA QUERY (Strato 1 — ADR 0089)",
            "Le credenziali user/pwd sono state estratte e salvate cifrate.",
            "Le password NON ti sono visibili. Se chiami admin/login_urls ",
            "per operazioni su questi host, passa `credentials_domain` cosi'",
            "il sudoer/login risolve le credenziali al fire time.",
            "",
        ]
        for m in extracted_meta:
            ctx = m.get("context") or {}
            host = ctx.get("host", "?")
            binding = ctx.get("binding", "?")
            share = ctx.get("share")
            line = f"  - domain=\"{m['domain']}\" binding={binding} host={host}"
            if share:
                line += f" share={share}"
            lines.append(line)
        lines.append("═" * 70)
        planner_system = planner_system + "\n" + "\n".join(lines)

    # Reference images uploaded (ADR 0092): blocco prescrittivo al PLANNER
    # cosi' il primo step richiama find_images_indices con from_step=1
    # (entries del @uploaded virtuale) invece di chiedere altre foto.
    _ref_images_for_prompt = [p for p in (reference_images or [])
                               if isinstance(p, str) and p.strip()]
    if _ref_images_for_prompt:
        n_ref = len(_ref_images_for_prompt)
        sample = _ref_images_for_prompt[0]
        ref_block = [
            "",
            "═" * 70,
            f"FOTO ALLEGATE AL TURNO (ADR 0092) — {n_ref} reference image(s)",
            "L'utente ha allegato foto. Sono gia' in scratchpad come step 1",
            "virtuale `@uploaded` con `entries=[{path, reference_image, ...}]`.",
            "DEVI: usare `find_images_indices(from_step=1, idx=\"scene\")` per",
            "trovare foto simili (image-to-image SigLIP). Per match per volti:",
            "`idx=\"persons\"`. Per prossimita' GPS: `idx=\"gps\"`.",
            "NON DEVI: chiedere all'utente altre foto: ce le ha gia' fornite.",
            f"Esempio path: {sample}",
            "═" * 70,
        ]
        planner_system = planner_system + "\n" + "\n".join(ref_block)

    # Progress canale visivo: avvio con messaggio "neutro" prima della
    # decisione fast-path vs PLANNER. Il messaggio "Sto pensando..." era
    # ingannevole sui turni fast-path (nessun thinking, replay diretto);
    # spostato al "ramo PLANNER" sotto. Qui solo ack iniziale (typing
    # animation Telegram, badge "live" HTTP) senza claim sul contenuto.
    if progress is not None:
        try:
            progress.start("")
        except Exception as _e:  # silent swallow (auto-fixed)
            _LOG.warning("silent exception in %s: %s", __name__, _e)
    catalog = filter_for_visibility(load_catalog(), VISIBILITY_COMPOSER)
    if len(catalog) == 0:
        log.final_kind = "error"; log.final_message = "(catalogo vuoto)"
        log.ts_end = time.time(); log.write(); return log

    turn_id = uuid.uuid4().hex[:16]
    log.turn_id = turn_id
    sp = Scratchpad.open()
    sp.gc()  # cleanup periodico

    # ── ASSORBIMENTO continuazione-dialogo → ENGINE v3 (ADR 0177 M1) ───────
    # Un turno di RIPRESA (`resume_with_scratchpad`: un dialogo si era fermato a
    # chiedere all'utente, ora prosegue) cadeva nel PLANNER legacy. Instradiamo
    # all'ENGINE con gli step pregressi come seed kind="done": il proposer
    # (consapevole via «FATTO FINORA») pianifica solo il resto, la guardia dedup
    # salta le ri-emissioni. Su risultato ritorna; su None (crash) cade nel
    # legacy come fallback. Gate METNOS_ENGINE_RESUME (default 1; =0 → legacy,
    # per A/B). Esclude le foto (gestite dal loro branch).
    if (resume_with_scratchpad and isinstance(resume_with_scratchpad, list)
            and not _ref_images_for_prompt
            and os.environ.get("METNOS_ENGINE_RESUME", "1") == "1"
            ):  # gate storico METNOS_ENGINE_V2 rimosso (6/7)
        _eng_rs_res = None
        try:
            _eng_rs_res = _run_engine(
                user_query_for_run, catalog,
                turn_id=turn_id, actor=actor, channel=channel,
                lang=DEFAULT_LANG, verbose=verbose, progress=progress,
                pre_approved_gate=pre_approved_gate,
                conversation_id=conversation_id,
                forced_object=forced_object,
                resume_steps=resume_with_scratchpad,
            )
        except Exception as _ex:
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "engine resume fallito: %s → fallback PLANNER legacy", _ex)
            _eng_rs_res = None
        if _eng_rs_res is not None:
            return _finalize_engine_result(
                log, _eng_rs_res, actor=actor, channel=channel,
                conversation_id=conversation_id, turn_id=turn_id)
        # else: engine resume ha declinato → esito onesto (legacy rimosso)

    # ── Fast path deterministico (ADR 0094) ──────────────────────────
    # Pattern catch-all PRIMA del PLANNER LLM per query triviali ad
    # alta confidenza (es. "che ora e", "what time is it"). Se match
    # esatto: invoca direttamente l'executor, formatta la final_message
    # con template deterministico, ZERO chiamate LLM. Risparmio: ~50 s
    # per pattern coperti vs flusso PLANNER completo.
    # Conservativo: sull'incertezza ritorna None e prosegue normale.
    # Reference images NON triviali: se ci sono allegati, salta il
    # fast path (l'utente ha intenzioni piu' ricche del pattern letterale).
    # resume_with_scratchpad: skip anche fast_path (turno gia' avviato, il
    # PLANNER continua dallo stato in history).
    # Chat-driven placement (ADR 0034): risolvi il PC bersaglio UNA volta, QUI,
    # PRIMA di fast_path e engine — così ENTRAMBI i path instradano al device e
    # il controllo di connessione è anticipato e onesto. `_placement_target` =
    # nome device (o None = server); `_query_for_planning` = query senza l'adjunct
    # di destinazione (per il pattern-match fast_path e per il planning). Nessun
    # PC citato → None + query invariata → comportamento IDENTICO a prima.
    _placement_target = None
    _query_for_planning = user_query_for_run
    _explicit_device_ref = False
    try:
        import devices as _dev_mod
        import target_device as _td_mod
        import chat_target_store as _cts_mod
        # #2 owner-filter: solo i device dell'ATTORE (isolamento multi-utente —
        # un utente non può nominare il PC di un altro). A3 (2026-07-04): owner
        # è un vero users.id → risolvi l'actor col resolver centrale (actor può
        # essere 'host'/device_id/user).
        _who = _dev_mod.owner_id_for_actor(actor)
        _dl = [d for d in _dev_mod.list_devices()
               if (getattr(d, "owner_user_id", "host") or "host") == _who]
        if _dl:
            _explicit_device_ref = _td_mod.references_device(user_query_for_run, _dl)
            _sid = f"{channel}:{actor}" if channel else (actor or "host")
            _tr = _td_mod.resolve_target(
                user_query_for_run, _dl, last_target=_cts_mod.get_last_target(_sid))
            if _tr.status in ("unreachable", "ambiguous"):
                # Bersaglio non raggiungibile o ambiguo → esito onesto, NIENTE
                # esecuzione (né qui né altrove) §2.8/§2.11.
                from messages import get as _pm
                if _tr.status == "unreachable":
                    # Fase 7 A.1: OFFERTA di differimento col consenso (§2.11,
                    # mai magia). Dialog yes_no riusando l'infrastruttura
                    # get_inputs/gate (form web via INLINE_FORM; il submit
                    # dispatcha orchestration:defer_turn → deferred_turns).
                    _dfr = _offer_defer_dialog(
                        query=user_query_for_run,
                        device_id=(getattr(_tr, "unreachable_id", None)
                                   or ""),
                        device_name=_tr.unreachable_name or "?",
                        actor=actor or "host", channel=channel or "",
                        conversation_id=conversation_id or "",
                        sender_id=_sid)
                    if _dfr:
                        log.final_kind = "ask"
                        log.final_message = _dfr["final_message"]
                        log.expandable_caps = _dfr["expandable_caps"]
                        log.target_device = _tr.unreachable_name
                        log.ts_end = time.time(); log.write(); return log
                    _pmsg = _pm("ERR_DEVICE_UNREACHABLE", name=_tr.unreachable_name or "?")
                else:
                    _pnm = ", ".join(n for _i, n in _tr.candidates if n)
                    _pmsg = _pm("ERR_DEVICE_AMBIGUOUS") + (f" ({_pnm})" if _pnm else "")
                log.final_kind = "answer"
                log.final_message = _pmsg
                log.target_device = _tr.unreachable_name
                log.ts_end = time.time(); log.write(); return log
            if _tr.target != _td_mod.SERVER:
                _placement_target = _tr.device_name
            _query_for_planning = _tr.cleaned_query or user_query_for_run
            if _tr.explicit:  # destinazione appiccicosa: solo su riferimento esplicito
                _cts_mod.set_last_target(_sid, _tr.target, _tr.device_name)
    except Exception as _pe:  # noqa: BLE001 — best-effort, mai bloccare il turno
        _LOG.warning("placement resolve fallito: %r", _pe)
        # #3: se l'utente ha nominato ESPLICITAMENTE un PC ma la risoluzione è
        # fallita, esito ONESTO — MAI una pianificazione locale silenziosa (§2.8).
        if _explicit_device_ref:
            from messages import get as _pm
            log.final_kind = "error"
            log.final_message = _pm("ERR_DEVICE_UNREACHABLE", name="?")
            log.ts_end = time.time(); log.write(); return log
        _placement_target = None
        _query_for_planning = user_query_for_run

    if not _ref_images_for_prompt and not resume_with_scratchpad:
        _fp_hit = try_fast_path(_query_for_planning, lang=DEFAULT_LANG,
                                  default_timezone=DEFAULT_TIMEZONE)
        # NB (11/6/2026): ritirato il Layer L1 BGE matcher su
        # canonical_query_log (ADR 0149 step 2c) — ridondante con la cache
        # query→piano di Engine v2 (engine/fastpath L0), che inoltre veniva
        # affamata dall'L1 (le query intercettate qui non alimentavano mai
        # il record del nuovo L0). Il by-product `canonical_query` resta
        # (telemetria → change_intents).
        if _fp_hit is not None and _fp_hit.get("direct_answer"):
            # Fast path a RISPOSTA DIRETTA (nessun executor): es. domanda di
            # identità "chi sei" → l'assistente si presenta come Metnos, senza
            # leggere il registro persone (§2.8: mai dumpare il profilo utente).
            log.final_kind = "answer"
            log.final_message = _fp_hit["direct_answer"]
            if verbose:
                print(f"[fast_path] direct-answer pattern='{_fp_hit.get('pattern')}'")
            log.ts_end = time.time(); log.write(); return log
        if _fp_hit is not None:
            _fp_exec = next((e for e in catalog if e.name == _fp_hit["executor"]), None)
            if _fp_exec is not None:
                _fp_step = StepLog(step_num=1)
                _fp_step.chosen_tool = _fp_hit["executor"]
                _fp_step.raw_args = dict(_fp_hit["args"])
                _fp_step.resolved_args = dict(_fp_hit["args"])
                _fp_step.vaglio_approved = True  # short-circuit, no vaglio (read-only)
                _t_fp = time.perf_counter()
                try:
                    _fp_obs = invoke_executor(
                        _fp_exec, _fp_hit["args"],
                        timeout_s=getattr(_fp_exec, "timeout_s", None) or 10,
                        autonomy="supervised", turn_id=turn_id,
                        actor=actor, channel=channel,
                        target_device=_placement_target,  # chat-driven placement (ADR 0034)
                    )
                except Exception as ex:
                    _fp_obs = {"ok": False,
                                "error": f"{type(ex).__name__}: {ex}"}
                _fp_step.exec_ms = int((time.perf_counter() - _t_fp) * 1000)
                _fp_step.result = _fp_obs
                # Marker audit per turn log: questo step e' arrivato
                # dal fast path, NON dal PLANNER.
                if hasattr(_fp_step, "__dict__"):
                    _fp_step.__dict__["fast_path"] = True
                if _fp_obs.get("ok"):
                    log.steps.append(_fp_step)
                    log.final_kind = "answer"
                    _fp_msg = _fp_hit["render"](_fp_obs)
                    # Chat-driven placement (ADR 0034): tag SOLO se lo step è
                    # girato DAVVERO sul device (marker da invoke_executor).
                    _fp_dev = _fp_obs.get("_ran_on_device") if isinstance(_fp_obs, dict) else None
                    if _fp_dev:
                        log.target_device = _fp_dev
                        if _fp_msg and not _fp_msg.startswith("📍"):
                            _fp_msg = f"📍 {_fp_dev}\n\n{_fp_msg}"
                    log.final_message = _fp_msg
                    if verbose:
                        print(f"[fast_path] hit pattern='{_fp_hit['pattern']}' "
                              f"executor={_fp_hit['executor']} exec_ms={_fp_step.exec_ms}")
                    log.ts_end = time.time(); log.write(); return log
                # Se l'executor fallisce: NON crashare il turno. Cadi nel
                # flusso normale PLANNER, che potra' tentare strade alternative
                # (timezone diversa, error reporting). NON aggiungere lo step
                # all'history: il PLANNER deve poter ripartire pulito.
                if verbose:
                    print(f"[fast_path] match ma executor fallito ({_fp_obs.get('error')!r}), "
                          f"fallback PLANNER")

        # ── L2.6 Scheduling deterministico (§7.9, bug live 10/6/2026) ───
        # «every 30 min: <corpo>» / «ogni giorno alle 8 <corpo>» = richiesta
        # di SCHEDULING: il corpo va eseguito al FIRE del task, non adesso.
        # Engine v2 non ha supporto scheduling (dispatch/fastpath/
        # routing_pool: zero logica *_tasks — il proposer pianifica il CORPO
        # e lo esegue subito → «Pipeline malformata o argomenti
        # insufficienti»); il PLANNER legacy che chiamava create_tasks e'
        # disattivato di default (METNOS_PLANNER_LEGACY=0, ADR 0163). La
        # grammatica dello scheduler e' CHIUSA (daily@HH:MM | every_Nm) →
        # parse NL deterministico e limitato. Parse PULITO → registra via
        # handle_create_tasks (stesso handler del tool create_tasks);
        # ambiguo/interrogativo → fallthrough al flusso normale.
        _rec_parsed = None
        if not resume_with_scratchpad and not _ref_images_for_prompt:
            try:
                from recurring_tasks import parse_recurrence_query as _parse_rec
                _rec_parsed = _parse_rec(user_query_for_run)
            except Exception as _ex_rec:
                _LOG.warning("parse_recurrence_query failed: %s", _ex_rec)
                _rec_parsed = None
        if _rec_parsed:
            _rt_obs = _invoke_builtin_handler(
                "create_tasks", dict(_rec_parsed),
                actor=actor, channel=channel, turn_id=turn_id)
            if verbose:
                print(f"[scheduling] recurrence parse when={_rec_parsed['when']!r} "
                      f"query={_rec_parsed['query']!r} → create_tasks "
                      f"ok={_rt_obs.get('ok')}")
            if _rt_obs.get("ok"):
                _rt_step = StepLog(step_num=1)
                _rt_step.chosen_tool = "create_tasks"
                _rt_step.raw_args = dict(_rec_parsed)
                _rt_step.resolved_args = dict(_rec_parsed)
                _rt_step.result = _rt_obs
                log.steps.append(_rt_step)
                log.final_kind = "answer"
                log.final_message = (_rt_obs.get("message")
                                      or f"Task registrato: {_rec_parsed['label']}")
                log.ts_end = time.time(); log.write(); return log
            # Registrazione fallita (quota, validazione): errore ONESTO
            # (§2.8). NON fallthrough: engine v2 eseguirebbe il CORPO del
            # task subito, che e' proprio il bug che questa route evita.
            _LOG.warning("scheduling route: create_tasks failed: %s",
                          _rt_obs.get("error"))
            log.final_kind = "error"
            log.final_message = (_rt_obs.get("error")
                                  or msg("ERR_QUERY_NOT_UNDERSTOOD"))
            log.ts_end = time.time(); log.write(); return log

        # ── L3 Engine v2 (ADR 0164) ────────────────────────────────────
        # Dispatcher 4-layer: fastpath → autopath → validator → engine.
        # Sostituisce Praxis legacy. (Il feature flag storico METNOS_ENGINE_V2 è stato rimosso 6/7: engine sempre attivo.)
        # (default ON post-migration).
        # NB (ADR 0177, 24/6): il DECOMPOSER deterministico è stato ELIMINATO.
        # Era un pre-stadio che decomponeva i compound (>=2 verbi) in step senza
        # LLM, come mitigatore del cold-start engine. Il bake `METNOS_DECOMPOSER=0`
        # (22-24/6) ha provato che l'engine copre il caso generale (incl.
        # extract→create, §2.8-onesto dopo il fix `a139dcd`); il decomposer
        # divergeva dall'engine (S1) e ne mascherava i bug. Path di planning
        # compound ora UNICO = engine (proposer + cache L0/L1). Gli helper
        # condivisi (PRODUCER_VERBS, derive_tool_name, split_query_chunks,
        # derive_extract_fields, _send_has_explicit_recipient) restano in
        # `compound_decomposer.py` — usati dai guard dell'engine.
        _force_legacy_compound = False

        # Universal §7.3: con reference_images allegate via drag&drop/upload
        # (ADR 0092), bypass Engine v2 e Praxis QUI. ASSORBIMENTO (ADR 0177 M1):
        # le foto allegate sono instradate all'ENGINE in un branch DEDICATO a
        # valle (vedi `_ref_images_for_prompt` prima del PLANNER legacy); questo
        # ramo resta il path NON-upload (`_ref_images_for_prompt` è vuoto per la
        # guardia del blocco esterno; gli upload hanno un branch dedicato a valle).
        _engine_v2_res = None
        # Gate storico METNOS_ENGINE_V2 rimosso (6/7): engine sempre attivo.
        if not _force_legacy_compound:
            try:
                _engine_v2_res = _run_engine(
                    _query_for_planning, catalog,
                    turn_id=turn_id, actor=actor, channel=channel,
                    lang=DEFAULT_LANG, verbose=verbose, progress=progress,
                    pre_approved_gate=pre_approved_gate,
                    conversation_id=conversation_id,
                    forced_object=forced_object,
                    placement_target=_placement_target,  # chat-driven placement (ADR 0034)
                    user_query_raw=user_query_for_run,
                    credential_meta=extracted_meta,
                )
            except Exception as _ex:
                import logging as _logging
                _logging.getLogger(__name__).warning(
                    "engine v2 fallito: %s", _ex)
                _engine_v2_res = None
        # Engine v2 wins when produces answer. Su error sintetizzato come
        # answer (terminator), comunque vince — niente PLANNER legacy.
        if _engine_v2_res is not None:
            return _finalize_engine_result(
                log, _engine_v2_res, actor=actor, channel=channel,
                conversation_id=conversation_id, turn_id=turn_id)
        # Qui si arriva SOLO se l'Engine v2 (moderno) ha ritornato None (crash
        # raro: normalmente il terminator del dispatch sintetizza error→answer).
        # Il PLANNER legacy è stato RIMOSSO (ADR 0181-ext): esito ONESTO §2.8,
        # INCONDIZIONATO — mai più un piano degenere. La causa-radice dei declini
        # (hiccup della fast-call LLM) è chiusa a monte (robustezza intent).
        if _engine_v2_res is None:
            log.final_kind = "error"
            # §11 messages: user-facing via i18n DB, mai diagnostica interna.
            log.final_message = msg("ERR_QUERY_NOT_UNDERSTOOD")
            if verbose:
                print("[engine] declino (engine None): intent/proposer non hanno "
                      "coperto la query")
            log.ts_end = time.time(); log.write(); return log

    # ── ASSORBIMENTO foto-allegate → ENGINE v3 (ADR 0177 M1) ───────────────
    # Foto allegate al turno (ADR 0092): il blocco fast-path/engine sopra è
    # saltato (guardia `not _ref_images_for_prompt`) e il controllo cadrebbe nel
    # PLANNER legacy sotto. Instradiamo invece all'ENGINE con un seed-state
    # `@uploaded` (find_images_indices via from_step=1, stesso consumer-match del
    # legacy ma senza ReAct). Su risultato ritorna; su None (crash engine) cade
    # nel PLANNER legacy come fallback. Gate METNOS_ENGINE_UPLOADS (default 1;
    # =0 → solo legacy, per A/B durante il bake).
    if (_ref_images_for_prompt
            and os.environ.get("METNOS_ENGINE_UPLOADS", "1") == "1"):
        _eng_up_res = None
        try:
            _eng_up_res = _run_engine(
                _query_for_planning, catalog,
                turn_id=turn_id, actor=actor, channel=channel,
                lang=DEFAULT_LANG, verbose=verbose, progress=progress,
                pre_approved_gate=pre_approved_gate,
                conversation_id=conversation_id,
                forced_object=forced_object,
                reference_images=_ref_images_for_prompt,
                placement_target=_placement_target,  # #4 threading (ADR 0034)
            )
        except Exception as _ex:
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "engine uploads fallito: %s → esito onesto (legacy rimosso)", _ex)
            _eng_up_res = None
        if _eng_up_res is not None:
            return _finalize_engine_result(
                log, _eng_up_res, actor=actor, channel=channel,
                conversation_id=conversation_id, turn_id=turn_id)
        # else: engine upload ha declinato → esito onesto (legacy rimosso)

    # ══ PLANNER legacy RIMOSSO (ADR 0181-ext, 2026-07-04) ═══════════════════
    # Il ReAct legacy (~3350 LOC) è stato CANCELLATO. Ci si arrivava solo quando
    # il motore moderno declinava su un fallthrough resume/upload (o nessun path
    # risolveva) — e mascherava il declino con un piano DEGENERE (§2.8). La
    # causa-radice dei declini (hiccup della fast-call LLM) è chiusa a monte
    # (robustezza intent: non si declina più su intent vuoto). Esito ONESTO qui.
    # Rollback: git revert del commit di cancellazione (ripristina il blocco).
    log.final_kind = "error"
    log.final_message = msg("ERR_QUERY_NOT_UNDERSTOOD")
    log.ts_end = time.time(); log.write(); return log


def format_simple_answer(executor_name, result):
    # §11 i18n: ogni ramo user-facing risolto via DB (it+en), niente hardcode.
    if not result.get("ok"):
        return msg("MSG_ANSWER_EXEC_ERROR", executor=executor_name,
                   error=result.get("error", "?"))
    content = result.get("content", "")
    meta = result.get("metadata", {})
    if executor_name == "get_now":
        return msg("MSG_ANSWER_NOW", time=(meta.get("time") or content),
                   tz=meta.get("timezone", "UTC"))
    if executor_name == "read_files":
        preview = (content or "")[:300] + ("…" if len(content) > 300 else "")
        return msg("MSG_ANSWER_FILE_PREVIEW", path=meta.get("path", "?"),
                   preview=preview)
    if executor_name == "write_files":
        return msg("MSG_ANSWER_BYTES_WRITTEN",
                   bytes=meta.get("bytes_written", 0), path=meta.get("path", "?"))
    if executor_name == "get_urls":
        preview = (content or "")[:300] + ("…" if len(content) > 300 else "")
        return msg("MSG_ANSWER_HTTP_GET", url=meta.get("url", "?"),
                   status=meta.get("status", "?"), bytes=meta.get("bytes", 0),
                   preview=preview)
    return json.dumps(result, ensure_ascii=False)[:300]


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("query", nargs="+")
    ap.add_argument("--model", default=None)
    ap.add_argument("--think", action="store_true", help="Abilita thinking del LLM (qwen3, deepseek)")
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--cap-steps", type=int, default=DEFAULT_CAP_STEPS)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    query = " ".join(args.query)
    log = run_turn(query, model=args.model, k=args.k,
                   cap_steps=args.cap_steps, think=args.think, verbose=args.verbose)
    print(f"\n>>> {log.final_message}\n")
    if args.verbose:
        total_in = sum(s.llm_in_tokens for s in log.steps)
        total_out = sum(s.llm_out_tokens for s in log.steps)
        total_lat = sum(s.llm_latency_ms for s in log.steps)
        print(f"--- log: {len(log.steps)} step, llm {total_in}->{total_out} toks in {total_lat}ms, turn {(log.ts_end - log.ts_start)*1000:.0f}ms, kind={log.final_kind}")
