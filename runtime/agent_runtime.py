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
    - default planner = llamacpp + Gemma 4 26B su :8080 (ADR 0146, supersedes
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
from cost_tracker import CostTracker
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
from config import DEFAULT_LANG, DEFAULT_TIMEZONE
from fast_path import try_fast_path, try_seed_step

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
from vaglio import judge
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
_CRED_RE = re.compile(
    r"(\bp(?:wd|assword|sw|ass)\s*[:=]?\s*)(\S+)", re.IGNORECASE
)
_USER_RE = re.compile(
    r"(\bu(?:ser|name|tente)\s*[:=]?\s*)(\S+)", re.IGNORECASE
)


# --- Think budget modulation per planner step (19/5/2026) ------------------
# Pattern A+B (manifest [planning] complexity + verb-of-name fallback).
# Bench Gemma 4 26B 19/5/2026 + euristica Roberto:
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
    return cleaned, n_matches


# Anti thinking-leak (ADR 0102, 7/5/2026). Gemma 4 26B think=true a volte
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

# Pattern italiani — meta-permission e self-talk di Gemma 4 26B think=true.
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
        if isinstance(_obs, dict) and _obs.get("ok") is True:
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

    Gemma 4 26B think=true a volte emette thinking nel canale text invece
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
                "password", "pwd", "psw", "pass",
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
# Pattern coperti:
#   "user X pwd Y", "utente X password Y", "user=X pass=Y",
#   "username: X, password: Y", "nome utente X passw Y", "X / Y" come slot.
#
# Riconoscimento del dominio:
#   - share CIFS:  "//192.0.2.20/Public" / "\\\\host.local\\share" → cifs_<host>
#   - URL/host web: "https://webmail.example.com" → web_<host>
#   - ssh:          "ssh roberto@host.local"        → ssh_<host>
#   - hint testuale: "share|smb|cifs|nas" → cifs ; "login|portale|sito" → web ;
#                    "ssh" → ssh.
#   - fallback: "generic" se nessun host derivabile (caso degenere).

# Pattern di estrazione: cattura coppie user/pwd in una passata sola.
# Usiamo finditer per ricavare gli offset esatti (per scrubbing offsets).
# Le keyword sono ordinate per lunghezza (LONGEST FIRST) per evitare match
# parziali tipo "user" che taglia "username" ⇒ value="name:carlo".
_USER_KEYWORD = (
    r"(?:\busername|\busernam|\butente|\buser|\bnome\s+utente|\blogin)"
)
_PWD_KEYWORD = (
    r"(?:\bpassword|\bpasswd|\bpasw|\bpwd|\bpsw|\bpass)"
)
_VAL = r"[^\s,;]+"
# user prima di pwd (caso piu' comune)
_USER_THEN_PWD = re.compile(
    rf"({_USER_KEYWORD})\s*[:=]?\s*({_VAL})"
    rf"\s*[,;]?\s*"
    rf"({_PWD_KEYWORD})\s*[:=]?\s*({_VAL})",
    re.IGNORECASE,
)
# pwd prima di user (caso meno comune ma valido)
_PWD_THEN_USER = re.compile(
    rf"({_PWD_KEYWORD})\s*[:=]?\s*({_VAL})"
    rf"\s*[,;]?\s*"
    rf"({_USER_KEYWORD})\s*[:=]?\s*({_VAL})",
    re.IGNORECASE,
)

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
    binding = detect_binding(query)
    host, ctx = _detect_host(query, binding)
    domain_prefix = binding if binding != "generic" else "host"
    if host:
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

    for m in _USER_THEN_PWD.finditer(query):
        # group 2 = user value, group 4 = pwd value
        user_val = m.group(2).rstrip(",;.")
        pwd_val = m.group(4).rstrip(",;.")
        # Scrub spans: solo i VALUE, non le keyword (per leggibilita').
        spans = [(m.start(2), m.start(2) + len(user_val)),
                 (m.start(4), m.start(4) + len(pwd_val))]
        _add(user_val, pwd_val, spans)
    for m in _PWD_THEN_USER.finditer(query):
        pwd_val = m.group(2).rstrip(",;.")
        user_val = m.group(4).rstrip(",;.")
        spans = [(m.start(2), m.start(2) + len(pwd_val)),
                 (m.start(4), m.start(4) + len(user_val))]
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
        })
    if all_spans:
        # Per il redact uso il primo dominio come tag, ma ogni run cattura
        # un solo dominio per query nella pratica (un solo host).
        primary_domain = creds[0]["domain"]
        redacted = _redact_spans(query, all_spans, primary_domain)
    return redacted, safe_meta


# --- Mode router ------------------------------------------------------------

class ModeRouter:
    def __init__(self, mode="local"):
        self.mode = mode

    def select(self, query, catalog):
        return self.mode


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
    prompt↔schema sotto schema-guided decoding (Ollama/Gemma).

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

# Marker auto-referenziali di RIFIUTO/meta-commento del modello (IT+EN, Gemma
# risponde in-lang). Frasi distintive che non compaiono mai come valore
# legittimo di un argomento (titolo, path, id, query). Usate da validate_args
# per intercettare i rifiuti che il PLANNER trapela DENTRO un arg.
_LLM_REFUSAL_MARKERS = (
    "come modello linguistico", "come un modello linguistico",
    "in qualità di assistente", "in qualita di assistente",
    "in qualità di modello", "in qualita di modello",
    "non ho accesso diretto", "non ho accesso ai tuoi",
    "non posso eseguire questa", "non posso completare questa",
    "non sono in grado di", "mi dispiace, non posso", "mi dispiace ma non posso",
    "non posso fornire", "non posso accedere",
    "as a language model", "as an ai", "as an a.i", "as an artificial intelligence",
    "i do not have access", "i don't have access", "i'm unable to", "i am unable to",
    "i cannot fulfill", "i cannot assist", "i can't assist", "i'm sorry, i can",
)


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
        expected_type = spec.get("type")
        if expected_type == "string" and not isinstance(value, str):
            failures.append(f"arg '{name}' deve essere string, e' {type(value).__name__}")
        elif expected_type == "integer" and not isinstance(value, int):
            failures.append(f"arg '{name}' deve essere integer, e' {type(value).__name__}")
        elif expected_type == "number" and not isinstance(value, (int, float)):
            failures.append(f"arg '{name}' deve essere number, e' {type(value).__name__}")
        elif expected_type == "boolean" and not isinstance(value, bool):
            failures.append(f"arg '{name}' deve essere boolean, e' {type(value).__name__}")
        elif expected_type == "object" and not isinstance(value, dict):
            failures.append(f"arg '{name}' deve essere object, e' {type(value).__name__}")
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
        return any(m in lo for m in _LLM_REFUSAL_MARKERS)
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
_HEALTH_IMPERATIVE_KEYWORDS = (
    "kill", "uccidi", "ferma", "termina", "stop ", "spegni",
    "manda", "invia", "scrivi", "esegui", "lancia", "riavvia", "restart",
)


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
_MULTISTEP_CONJUNCTIONS_RE = re.compile(
    r"\b(e\s+poi|e\s+dopo|e\s+inoltre|e\s+anche|"
    r"inoltre|poi|dopodiche'|dopodiche|"
    r"and\s+then|and\s+also|and\s+after|"
    r"moreover|then|afterwards|additionally)\b",
    re.IGNORECASE,
)


def _query_has_continuation(query: str) -> bool:
    """True se la query e' multi-step esplicito. Detection a 2 strati:

    1. Congiunzione strutturale di continuita' (regex universale IT+EN).
    2. Classe semantica: 2+ verbi canonici distinti nel query (derivato da
       prefilter._VERB_TO_CANONICAL, gia' contiene sinonimi IT+EN per le
       22 azioni di vocab.ACTIONS). §7.3 generale, non enumerativa.

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
    if _MULTISTEP_CONJUNCTIONS_RE.search(query):
        return True
    # Multi-verb detection via vocab classes. Tokenize semplice + lookup
    # _VERB_TO_CANONICAL (gia' usato da prefilter, IT+EN sinonimi per le 22
    # ACTIONS). 2+ verbi canonici distinti → multi-step.
    try:
        from prefilter import tokenize, detect_canonical_verbs_all
        verbs = detect_canonical_verbs_all(tokenize(query))
        return len(verbs) >= 2
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
    # Sottoinsieme delle 23 ACTIONS §2.2 con side-effect remoto:
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
_RESUME_AFTER_DIALOG_HINTS_IT = (
    "mandami", "manda", "inviami", "invia", "notificami",
    "scrivimi", "avvisami", "informami",
    "e poi crea", "e poi prenota", "e poi fissa", "e poi sposta",
    "e poi cancella", "e poi invia", "e poi manda",
    "e crea", "e prenota", "e fissa", "e sposta", "e cancella",
    "e invia", "e manda",
)
_RESUME_AFTER_DIALOG_HINTS_EN = (
    "send me", "notify me", "tell me", "email me", "let me know",
    "and create", "and book", "and schedule", "and move",
    "and delete", "and send", "and notify",
    "then create", "then book", "then schedule", "then send",
)


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
    q_low = query.lower()
    for hint in _RESUME_AFTER_DIALOG_HINTS_IT:
        if hint in q_low:
            return True
    for hint in _RESUME_AFTER_DIALOG_HINTS_EN:
        if hint in q_low:
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


def invoke_executor(executor, args, timeout_s=30, *, autonomy="supervised",
                    turn_id=None, actor=None, channel=None):
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
    import sandbox as _sandbox  # lazy: evita import circolare e overhead per moduli che non lo usano
    payload = json.dumps(args)
    base_cmd = [sys.executable, str(executor.code_path)]
    cmd = _sandbox.wrap_command(executor, base_cmd, autonomy=autonomy)
    # PYTHONPATH augmentato: gli executor (specie quelli sintetizzati) importano
    # moduli runtime (mail_client, messages, platform_policy, ...) per nome.
    # Senza questo, il subprocess vede solo stdlib e fallisce con
    # ModuleNotFoundError. Vedi caso live 29/4/2026 sera (move_messages errore in
    # esecuzione anche dopo birth tests verdi).
    env = os.environ.copy()
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
    return parsed_result


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
_COUNT_QUANTIFIER_MARKERS = (
    "quanti ", "quante ",       # IT interrogativo numerico
    "how many ", " count ",     # EN
    " conta ", "numero di ",    # IT imperativo / nominale
)


def _is_count_intent(intent_verb: str, user_query: str) -> bool:
    """Vero se l'intent e' un count: verb=compute (canonical §2.2) o pattern
    testuale quantificatore. Pattern §7.3 universale, no per-domain."""
    if (intent_verb or "").lower() == "compute":
        return True
    q = f" {(user_query or '').lower()} "
    return any(m in q for m in _COUNT_QUANTIFIER_MARKERS)


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
        mut = None
        for s in reversed(self.steps):
            res = s.result if isinstance(s.result, dict) else {}
            if not res:
                continue
            has_signals = (
                any(k in res for k in self._MUTATE_SUCCESS_KEYS)
                or "not_found" in res or "failed" in res)
            if "results" in res and has_signals:
                mut = res
                break
            # primo result puramente di lettura (entries, no segnali) → il turno
            # non e' mutating in coda: non intervenire.
            if "entries" in res and not has_signals:
                return
        if mut is None:
            return
        not_found = mut.get("not_found") or []
        failed = mut.get("failed") or []
        not_found = not_found if isinstance(not_found, list) else [not_found]
        failed = failed if isinstance(failed, list) else [failed]
        if not not_found and not failed:
            return  # esito pieno: nessun claim da correggere
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
                    out.append(str(it.get("id") or it.get("event_id")
                                   or it.get("path") or it.get("error") or it))
                else:
                    out.append(str(it))
            return ", ".join(out[:8])

        detail = _ids(not_found) or _ids(failed)
        from i18n import register_key_if_missing as _rk
        if success == 0:
            _rk("MSG_MUTATE_NONE_DONE",
                "Nessun elemento «{detail}» trovato: nessuna operazione "
                "eseguita.",
                "No item «{detail}» found: no operation performed.")
            self.final_message = msg("MSG_MUTATE_NONE_DONE", detail=detail)
        else:
            _rk("MSG_MUTATE_PARTIAL",
                "Attenzione: {n} elemento/i non trovato/i o fallito/i "
                "({detail}).",
                "Warning: {n} item(s) not found or failed ({detail}).")
            notice = msg("MSG_MUTATE_PARTIAL",
                         n=len(not_found) + len(failed), detail=detail)
            if notice not in (self.final_message or ""):
                self.final_message = ((self.final_message or "").rstrip()
                                      + "\n\n" + notice).strip()

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
                    labels.append(str(lab)[:50] if lab is not None else "?")
            n = len(failed)
            key = (s.chosen_tool, n, tuple(labels))
            if key in seen:
                continue
            seen.add(key)
            from i18n import register_key_if_missing as _rk
            _rk("MSG_PARTIAL_ITEM_FAILURE",
                "Attenzione: {n} non controllati per errore ({labels}); il "
                "risultato è incompleto, il conteggio può non essere reale.",
                "Warning: {n} not checked due to error ({labels}); the result "
                "is incomplete, the count may not be real.")
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
        # fallback "risultati".
        preview_label = "risultati"
        for s in self.steps:
            if s.chosen_tool == executor:
                res = s.result if isinstance(s.result, dict) else {}
                tw = res.get("truncated_what")
                if isinstance(tw, str) and tw:
                    preview_label = tw
                break

        prompt = (
            f"Hai chiesto un risultato troncato a {used} {preview_label}; "
            f"in totale ce ne sono {available_total}. "
            f"Allargo a {cap_suggested}?"
        )
        title = "Allargamento risultato"
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
        for s in self.steps:
            res = s.result if isinstance(s.result, dict) else {}
            h = res.get("health")
            if isinstance(h, dict):
                health = h
                # Stesso step: prendi anche le entries (lista processi).
                e = res.get("entries")
                if isinstance(e, list):
                    entries = e
                break
        if not health:
            return
        try:
            # runtime/ già su sys.path (agent_runtime VIVE in runtime/).
            from orchestration import _fmt_health_block, _fmt_entries_block
            block = _fmt_health_block(health)
            if entries:
                # Top 10 processi col detail cpu%/mem% (non solo nomi nudi).
                # Cap 10 perche' health gia' occupa righe; per piu' c'e'
                # cap-expand.
                proc_block = _fmt_entries_block(entries, cap=10)
                if proc_block:
                    block = block + "\n\n**" + msg("MSG_HEALTH_TOP_PROCESSES") + "**\n" + proc_block
        except (ImportError, KeyError, AttributeError):
            return
        if not block or "Stato server" in (self.final_message or ""):
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
        if not any(k in _q for k in _HEALTH_IMPERATIVE_KEYWORDS):
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
            used = res.get("used") or res.get("ok_count") or res.get("count")
            available = res.get("available_total")
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
                from i18n import register_key_if_missing as _rk
                _rk("MSG_FALSE_SUCCESS_NOTICE",
                    "⚠ Nota: in questo turno nessun elemento è stato trovato "
                    "o processato (0 risultati in tutti gli step): nessuna "
                    "azione è stata realmente eseguita.",
                    "⚠ Note: this turn found and processed no items (0 "
                    "results in every step): no action was actually "
                    "performed.")
                _fs_notice = msg("MSG_FALSE_SUCCESS_NOTICE")
                if _fs_notice not in (self.final_message or ""):
                    self.final_message = (
                        _fs_notice + "\n\n"
                        + (self.final_message or "")
                    ).strip()
        # Propaga attachments dall ultimo step che ne ha prodotti (use
        # case realistico: un solo find_images_indices per turno).
        for s_step in reversed(self.steps):
            res = s_step.result if isinstance(s_step.result, dict) else {}
            atts = res.get("attachments")
            if isinstance(atts, list) and atts:
                self.attachments = atts
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
    "list_skills": handle_list_skills,
    "set_skills": handle_set_skills,
}

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
}


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
        from loader import Executor as _LExec
    except Exception:
        return catalog
    present = {getattr(e, "name", None) for e in catalog}
    out = list(catalog)
    for name in _BUILTIN_TOOL_HANDLERS:
        if name in present:
            continue
        spec = _BUILTIN_TOOL_SPECS.get(name)
        fn_block = (spec or {}).get("function") or {}
        params = fn_block.get("parameters") or {"type": "object", "properties": {}}
        desc = fn_block.get("description") or ""
        out.append(_LExec(
            name=name, version="1.0.0", description=desc,
            affinity=[], args_schema=params, capabilities=[], tests=[],
            code_path=None, manifest_path=Path(""),
            signed_by="(inproc builtin)", revertible=False,
            lifecycle="active",
        ))
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
        return {"ok": False, "error": f"unknown builtin: {tool_name}"}
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
    try:
        return annotate_skipped_known(handler(args, **kwargs), _treated_info)
    except TypeError as te:
        # Fallback: prova senza kwargs (handler legacy che vuole solo args)
        if "actor" in str(te) or "channel" in str(te):
            log.error("Builtin %s requires kwargs not provided: %s",
                      tool_name, te)
        return {"ok": False, "error": f"builtin call failed: {te}",
                "tool": tool_name}


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

def _try_engine_v2(
    query: str,
    catalog: list,
    *,
    turn_id: str,
    actor: str | None,
    channel: str | None,
    lang: str = "it",
    verbose: bool = False,
    progress=None,
) -> "dict | None":
    """Bridge agent_runtime → engine.dispatch.run_turn.

    Adatta i parametri legacy al nuovo dispatcher, gestisce intent
    extraction, costruisce runtime_ctx, e converte DispatchResult →
    dict legacy shape per minimal changes a caller.
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
        try:
            from llm_router import LLMRouter
            ck = {"max_tokens": max_tokens, "think": think}
            if kw.get("grammar") is not None:
                ck["grammar"] = kw["grammar"]
            res = LLMRouter().provider("fast").chat(sys_msg, user_msg, **ck)
            return (getattr(res, "text", res) or "").strip()
        except Exception:
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
        return None
    intent = Intent(
        verb=(intent_raw.get("verb") or "").lower(),
        object=(intent_raw.get("object") or "").lower(),
        keywords=list(intent_raw.get("keywords") or []),
        confidence=float(intent_raw.get("confidence") or 1.0),
        lang=lang,
        actions=list(intent_raw.get("actions") or []),
    )

    # Invoke executor callback wrapped — Executor v2 chiama via tool name
    def _invoke(tool_name: str, args: dict) -> dict:
        if tool_name in _BUILTIN_TOOL_HANDLERS:
            # Estrai actor/channel se disponibili nel closure scope
            _ac = locals().get("actor") or "host"
            _ch = locals().get("channel") or ""
            return _invoke_builtin_handler(tool_name, args, actor=_ac, channel=_ch)
        exec_obj = next((e for e in catalog if e.name == tool_name), None)
        if exec_obj is None:
            return {"ok": False, "error": f"tool '{tool_name}' non in catalog",
                     "error_class": "tool_unknown"}
        try:
            return invoke_executor(
                exec_obj, args,
                timeout_s=(getattr(exec_obj, "timeout_s", None) or 120),
                autonomy="supervised", turn_id=turn_id,
                actor=actor, channel=channel)
        except Exception as ex:
            return {"ok": False, "error": f"{type(ex).__name__}: {ex}",
                     "error_class": "exception"}

    runtime_ctx = {
        "actor": actor or "",
        "lang": lang,
        "channel": channel or "",
    }

    # Engine v2 passa lo stesso catalog a Proposer e Validator: includi i
    # builtin in-process (describe_entries/classify_entries) altrimenti
    # mancanti → Validator falsa `tool_unknown` (§11, fix wiring B).
    catalog_v2 = _engine_v2_catalog_with_builtins(catalog)

    try:
        result = _dispatch.run_turn(
            query=query, intent=intent, catalog=catalog_v2,
            invoke_executor_cb=_invoke,
            llm_call_wise=_llm_call_wise,
            llm_call_fast=_llm_call_fast,
            runtime_ctx=runtime_ctx,
            turn_id=turn_id, lang=lang, verbose=verbose,
            progress=progress)
    except Exception as ex:
        import traceback as _tb
        log.warning("engine.dispatch.run_turn failed: %r\n%s", ex, _tb.format_exc())
        return None

    # Converti DispatchResult → dict shape legacy (per minimal change caller)
    steps_out = []
    needs_inputs_obs = None
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
    }


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

def run_turn(user_query, *, mode="local", model=None, k=None, k_min=5, k_max=8, think=None, progress=None,
             cap_steps=DEFAULT_CAP_STEPS, cap_same=DEFAULT_CAP_SAME_EXECUTOR,
             scratchpad_threshold=SCRATCHPAD_THRESHOLD_BYTES,
             actor="host", channel="", conversation_id="",
             reference_images=None,
             resume_with_scratchpad=None,
             allow_disambig_synth=True,
             bypass_rejected_pipelines=False,
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
    # nei tool che lo accettano (admin per CIFS, login_session per web).
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
        from vocab import sections_for_object as _sections_for_object
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
            "Le password NON ti sono visibili. Se chiami admin/login_session ",
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
    if not _ref_images_for_prompt and not resume_with_scratchpad:
        _fp_hit = try_fast_path(user_query_for_run, lang=DEFAULT_LANG,
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
                    log.final_message = _fp_hit["render"](_fp_obs)
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
        # Sostituisce Praxis legacy. Feature flag METNOS_ENGINE_V2=1
        # (default ON post-migration).
        # Universal §7.9 — compound query: prova DECOMPOSER deterministico
        # PRIMA di Engine v2/Praxis. Se decompose succeed → build Framework
        # e esegui via ExecutorEngine direttamente (skip LLM Mētis).
        # Fallback a legacy PLANNER ReAct se decomposer fail ma >=2 verbi.
        _force_legacy_compound = False
        _decomposed_steps = None
        try:
            from prefilter import (
                tokenize as _pf_tokenize,
                detect_canonical_verbs_all as _pf_detect_verbs,
            )
            _q_tokens = _pf_tokenize(user_query_for_run)
            _q_verbs = _pf_detect_verbs(_q_tokens)
            # Guard scheduling (§7.9, bug live 10/6/2026): una query con
            # marker scheduling («every 30 min: read the issues…», «ogni
            # giorno alle 8 controlla…») NON va decomposta: il corpo del
            # task si esegue al FIRE, non adesso. Il decomposer non conosce
            # create_tasks → produceva pipeline del corpo con args mancanti
            # («Pipeline malformata o argomenti insufficienti»). Defer a
            # Engine v2 (intent create/tasks via bypass ricorrenza) o al
            # PLANNER legacy (CREATE_TASKS_TOOL nel pool).
            from tool_grammar import query_has_tasks_marker as _qhtm
            _q_is_scheduling = _qhtm(user_query_for_run)
            if len(set(_q_verbs)) >= 2 and not _q_is_scheduling:
                # Try deterministic decomposer
                try:
                    from compound_decomposer import decompose_query
                    # Union catalog + builtin in-process handlers (describe_entries,
                    # classify_entries, create_tasks, ecc.) — universal §7.9.
                    _avail_tools = ({e.name for e in catalog}
                                    | set(_BUILTIN_TOOL_HANDLERS.keys())
                                    | {"final_answer"})
                    # Schemi per il confidence gate del decomposer (§7.9/§2.8):
                    # se gli args euristici non sono schema-coerenti, deferisce
                    # al PLANNER LLM invece di emettere una pipeline rotta.
                    _tool_schemas = {e.name: getattr(e, "args_schema", None)
                                     for e in catalog}
                    _decomposed_steps = decompose_query(
                        user_query_for_run, _avail_tools, _tool_schemas)
                except Exception as _ex_dec:
                    import logging as _logging
                    _logging.getLogger(__name__).warning(
                        "decomposer failed: %s", _ex_dec)
                # Guard coverage produttori (§2.8/§7.3, universale): se il
                # decomposer deterministico ha SALTATO un verbo PRODUCER
                # (find/read/get/list) richiesto dalla query, la decomposizione
                # è INCOMPLETA — parte da un consumer/mutating senza i dati (es.
                # "cerca online ... crea evento" → solo create_events, find_urls
                # droppato). Defer all'Engine v2 (proposer: pattern J +
                # extract_entries + guard). Vale per ogni executor/dominio.
                if _decomposed_steps:
                    try:
                        from vocab import (COVERAGE_REQUIRED_VERBS as _CRV,
                                            ACTIONS as _ACT)
                        _step_vrb = {s["tool"].split("_", 1)[0]
                                     for s in _decomposed_steps
                                     if s.get("tool") and s["tool"] != "final_answer"
                                     and s["tool"].split("_", 1)[0] in _ACT}
                        _missing = (set(_q_verbs) & set(_CRV)) - _step_vrb
                        if _missing:
                            import logging as _logging
                            _logging.getLogger(__name__).info(
                                "decomposer DROP producer %s → defer Engine v2",
                                sorted(_missing))
                            _decomposed_steps = None
                    except Exception:
                        pass
                if _decomposed_steps:
                    import logging as _logging
                    _logging.getLogger(__name__).info(
                        "COMPOUND DECOMPOSED: %d steps %s",
                        len(_decomposed_steps),
                        [s["tool"] for s in _decomposed_steps])
                else:
                    # Decomposer deterministico non confidente → NON forzare il
                    # PLANNER legacy (deprecato, ADR 0161/0163): fall-through a
                    # Engine v2 metis (proposer_metis), che e' il path moderno e
                    # gestisce il multi-step via LLM. Il legacy ReAct tentava
                    # request_new_executor (synth) invece di usare gli executor
                    # esistenti (es. find_issues_github) — bug 2/6/2026.
                    import logging as _logging
                    _logging.getLogger(__name__).info(
                        "COMPOUND query: %d verbs %s → decomposer deferred, "
                        "use Engine v2 metis", len(set(_q_verbs)),
                        sorted(set(_q_verbs)))
        except Exception:
            pass

        # Se decomposer ha prodotto steps → esegui via ExecutorEngine
        if _decomposed_steps:
            try:
                from engine.executor import Executor as _EngineExec
                from engine.types import Framework, StepSpec
                framework = Framework(
                    steps=[StepSpec(tool=s["tool"], args=s.get("args") or {})
                            for s in _decomposed_steps] + [StepSpec(tool="final_answer")],
                    fillers={},
                    final_message="",
                )
                # invoke_executor: dispatcher condiviso
                def _exec_invoke(tool_name: str, args: dict) -> dict:
                    if tool_name in _BUILTIN_TOOL_HANDLERS:
                        return _invoke_builtin_handler(
                            tool_name, args, actor=actor,
                            channel=channel, turn_id=turn_id)
                    _ex = next((e for e in catalog if e.name == tool_name), None)
                    if _ex is None:
                        return {"ok": False, "error": f"unknown tool: {tool_name}"}
                    return invoke_executor(
                        _ex, args,
                        timeout_s=(getattr(_ex, "timeout_s", None) or 120),
                        autonomy="supervised", turn_id=turn_id,
                        actor=actor, channel=channel,
                    )
                _engine = _EngineExec(invoke_executor=_exec_invoke)
                _runtime_ctx = {"actor": actor or "host",
                                "lang": DEFAULT_LANG,
                                "channel": channel or ""}
                _eng_res = _engine.run(
                    framework, query=user_query_for_run,
                    runtime_ctx=_runtime_ctx,
                )
                # Convert RunResult to TurnLog
                for _sr in _eng_res.steps:
                    log.steps.append(StepLog(
                        step_num=_sr.step_idx, chosen_tool=_sr.tool,
                        raw_args=_sr.args, resolved_args=_sr.args,
                        llm_text="", result=_sr.result,
                    ))
                log.final_kind = _eng_res.final_kind or "answer"
                log.final_message = _eng_res.final_text or ""
                log.intent_verb = ""
                log.ts_end = time.time()
                log.write()
                return log
            except Exception as _ex:
                import logging as _logging
                _logging.getLogger(__name__).warning(
                    "decomposed execution failed: %s → fallthrough", _ex)
                # Fallthrough to next engine

        # Universal §7.3: con reference_images allegate via drag&drop/upload
        # (ADR 0092), bypass Engine v2 e Praxis perché queste cascade non
        # consumano `_ref_images_for_prompt`. Il PLANNER legacy ha il blocco
        # FOTO ALLEGATE nel system prompt + virtual step `@uploaded` in
        # scratchpad → routing corretto a find_images_indices(from_step=1).
        _bypass_for_uploads = bool(_ref_images_for_prompt)

        _engine_v2_res = None
        if (os.environ.get("METNOS_ENGINE_V2", "1") == "1"
            and not _force_legacy_compound
            and not _bypass_for_uploads):
            try:
                _engine_v2_res = _try_engine_v2(
                    user_query_for_run, catalog,
                    turn_id=turn_id, actor=actor, channel=channel,
                    lang=DEFAULT_LANG, verbose=verbose, progress=progress,
                )
            except Exception as _ex:
                import logging as _logging
                _logging.getLogger(__name__).warning(
                    "engine v2 fallito: %s", _ex)
                _engine_v2_res = None
        # Engine v2 wins when produces answer. Su error sintetizzato come
        # answer (terminator), comunque vince — niente PLANNER legacy.
        if _engine_v2_res is not None:
            log.steps.extend(_engine_v2_res.get("steps") or [])
            # §7.3: se Engine v2 ha ritornato needs_inputs → handle dialog
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
            log.final_kind = _engine_v2_res.get("final_kind") or "answer"
            log.final_message = _engine_v2_res.get("final_text") or ""
            # Universal §7.3: se needs_inputs e nessun handler, ma c'e' un
            # final_message_hint nel result, usa quello (UX onestà).
            if not log.final_message and _ni and isinstance(_ni, dict):
                hint = _ni.get("final_message_hint")
                if isinstance(hint, str) and hint:
                    log.final_message = hint
            log.intent_verb = _engine_v2_res.get("verb", "") or ""
            log.ts_end = time.time()
            log.write()
            return log
        # NOTA bonifica 2026-05-28: rimosso il ramo Praxis legacy morto+rotto
        # (cascata _legacy via shim). Engine v2 (sopra) ritorna SEMPRE non-None
        # — terminator sintetizza error→answer. Quindi questo punto è raggiunto
        # solo se Engine v2 ritorna None (crash) → cade sul blocco G3 sotto.

        # G3 (ADR 0161 ext): PLANNER step-by-step DEPRECATO da 25/5/2026.
        # Default METNOS_PLANNER_LEGACY=0 → return error invece di entrare nel
        # PLANNER legacy. Bench strict 25/25 INTENT_OK conferma Praxis copre 100%.
        # Override METNOS_PLANNER_LEGACY=1 per re-abilitare temporaneamente.
        if os.environ.get("METNOS_PLANNER_LEGACY", "0") == "0":
            log.final_kind = "error"
            # §11 messages: user-facing via i18n DB, mai diagnostica interna
            # (bug 11/6/2026: "no" fuori contesto mostrava "Praxis non ha
            # coperto questa query (LEGACY=0)..." all'utente). La diagnostica
            # resta nel log verbose sotto.
            log.final_message = msg("ERR_QUERY_NOT_UNDERSTOOD")
            if verbose:
                print("[praxis] LEGACY=0 + no cascade match → error return "
                      "(Praxis non ha coperto la query: verifica intent "
                      "extraction + framework propose)")
            log.ts_end = time.time(); log.write(); return log

    # ╔════════════════════════════════════════════════════════════════════╗
    # ║ PLANNER step-by-step (LEGACY FALLBACK, ADR 0163 fase #7).          ║
    # ║ ~3000 LOC. Da Praxis Engine ADR 0161 questo path è disattivato di  ║
    # ║ default (METNOS_PLANNER_LEGACY=0). Praxis cascata copre ~94% query;║
    # ║ il 6% miss finisce in Aporia (vicolo cieco onesto), NON qui.       ║
    # ║ Re-enable temporaneo: METNOS_PLANNER_LEGACY=1.                     ║
    # ║ Rimozione fisica: sessione dedicata richiesta (rischio alto multi- ║
    # ║ file refactor). Status: ATTIVO come safety net opt-in.             ║
    # ╚════════════════════════════════════════════════════════════════════╝
    chosen_mode = ModeRouter(mode).select(user_query_for_run, catalog)
    log.mode = chosen_mode

    # Tutti i fast-path L0/L1/L2 hanno mancato → entro nel PLANNER LLM.
    # Aggiorno il progress con il messaggio "Sto pensando..." perche'
    # da qui in avanti c'e' davvero pensiero LLM in corso.
    if progress is not None:
        try:
            if hasattr(progress, "update_free"):
                progress.update_free(
                    "Sto pensando il modo migliore di rispondere…"
                )
        except Exception as _e:
            _LOG.warning("silent exception in %s: %s", __name__, _e)

    # Telemetria fine (ADR 0080): prefilter_ms + intent_ms misurati al
    # confine, attribuiti allo step 1 (sotto). intent_ms e' la quota LLM
    # interna a rank_adaptive; prefilter_ms = totale - quota LLM.
    _intent_ms_acc = 0  # accumulatore quota LLM dentro _intent_llm
    if k is None:
        # Intent extractor LLM-based (gemma 4 26B middle tier) come primary
        # signal del prefilter (Roberto 29/4/2026). Fallback al bag-of-words
        # se l'LLM e' down o non riesce a parsare.
        def _intent_llm(system, user, max_tokens=80, think=False):
            nonlocal _intent_ms_acc
            from llm_router import LLMRouter
            _r = LLMRouter()
            _p = _r.provider("middle")  # gemma 4 26B
            _t0 = time.perf_counter()
            _res = _p.chat(system, user, max_tokens=max_tokens,
                           temperature=0, think=think)
            _intent_ms_acc += int((time.perf_counter() - _t0) * 1000)
            return {"text": _res.text or "",
                    "in_tokens": _res.in_tokens,
                    "out_tokens": _res.out_tokens}
        _t_prefilter0 = time.perf_counter()
        candidates, route_info = rank_adaptive(
            user_query_for_run, catalog, k_min=k_min, k_max=k_max,
            llm_call=_intent_llm,
        )
        _prefilter_total_ms = int((time.perf_counter() - _t_prefilter0) * 1000)
        # Propaga intent.verb a log.intent_verb per il guard mutating-intent
        # in write() (§2.8): se intent.verb e' mutating ma il PLANNER non
        # chiama mai un tool con quel verbo, dichiarare incompletezza.
        try:
            _iv = ((route_info or {}).get("intent") or {}).get("verb")
            if isinstance(_iv, str) and _iv:
                log.intent_verb = _iv
        except Exception:
            pass
        if verbose:
            conf = route_info.get('confidence')
            conf_s = f"{conf:.2f}" if isinstance(conf, (int, float)) else str(conf)
            print(f"[router] K={route_info['chosen_k']} confidence={conf_s} reason={route_info['reason']}")
            if route_info.get("intent"):
                print(f"[router] intent={route_info['intent']}")
    else:
        _t_prefilter0 = time.perf_counter()
        candidates = rank(user_query_for_run, catalog, k=k)
        _prefilter_total_ms = int((time.perf_counter() - _t_prefilter0) * 1000)
        route_info = {"chosen_k": len(candidates), "confidence": None, "reason": "fixed_k"}
    # Quota prefilter «non-LLM» (token rank + adattivita') = totale - intent.
    _prefilter_only_ms = max(0, _prefilter_total_ms - _intent_ms_acc)

    # ───── Preemption euristica (cluster C, 20/5/2026) ────────────────────
    # Warming del cache HTTP (ADR 0105) in parallelo al PLANNER LLM.
    # Opt-in METNOS_SPECULATION=1. Thread daemon, best-effort, no harm.
    try:
        from speculation import kick_off as _spec_kick
        def _spec_invoke(tool_name: str, spec_args: dict) -> dict:
            _spec_exec = next(
                (e for e in catalog if e.name == tool_name), None,
            )
            if _spec_exec is None:
                return {"ok": False, "error": "no such tool"}
            return invoke_executor(
                _spec_exec, spec_args,
                timeout_s=(getattr(_spec_exec, "timeout_s", None) or 30),
                autonomy="supervised", turn_id=turn_id,
                actor=actor, channel=channel,
            )
        _spec_kick(
            (route_info or {}).get("intent"),
            user_query_for_run or "",
            invoke=_spec_invoke,
        )
    except Exception as _spec_ex:
        # Mai bloccare il turno per un errore di speculazione.
        _LOG.warning("speculation kickoff failed: %r", _spec_ex)
    # ─────────────────────────────────────────────────────────────────────

    # P6 (12/5/2026) — Multi-pipeline propose / notify injection.
    # Bug live turn 35431172: query «proponi N orari ... e mandami email
    # con la scelta». Intent LLM ha estratto verb=send object=messages →
    # rank_with_intent ha popolato top-K con send_messages,
    # find_messages_google_workspace, read_messages — NESSUN tool calendar.
    # Step 1: find_messages_google_workspace (sbagliato).
    # Step 2: send_messages premature (sbagliato, scelta non fatta).
    # Step 3: find_events_empty (corretto ma tardi).
    # Defense in depth §7.9: il runtime detecta tre forme di multi-pipeline
    # e amplia il pool. Le tre forme sono ortogonali (propose XOR notify XOR
    # entrambi) ma applicano la STESSA logica add-only (no shim §7.1):
    #   propose-only → inietta find_events_empty + get_inputs + create_events
    #                   (variante propose+fire; calendar pipeline producer/
    #                   consumer + dialog).
    #   notify-only  → inietta send_messages (consumer di notifica).
    #   entrambi     → inietta TUTTA la pipeline 6-step + rimuove gli
    #                   hijackers mail-search (la query NON cerca mail).
    # NB: NON rimuoviamo send_messages: e' il consumer corretto della
    # variante (a)/(b). NON rimuoviamo find_events_empty/create_events/
    # read_events: sono i producer/consumer della pipeline. La rimozione
    # dei hijackers mail-search scatta SOLO se entrambi i flag, perche'
    # in solo-notify la query potrebbe legittimamente cercare destinatari
    # via Gmail (caso «manda email a Mario» non triggera notify-cont).
    _is_propose = _query_is_propose_intent(user_query_for_run)
    _is_notify = _query_has_notify_continuation(user_query_for_run)
    if _is_propose or _is_notify:
        _CALENDAR_PROPOSE_TOOLS = (
            "get_now",
            "find_events_empty",
            "create_events",
            "read_events",
            "get_inputs",
        )
        _NOTIFY_TOOLS = ("send_messages",)
        _HIJACKERS_BOTH = frozenset({
            # Tool di RICERCA mail: la query NON cerca mail esistenti.
            "find_messages_google_workspace",
            "read_messages",
            "read_messages_google_workspace",
        })
        if _is_propose and _is_notify:
            needed = _CALENDAR_PROPOSE_TOOLS + _NOTIFY_TOOLS
            hijackers = _HIJACKERS_BOTH
            route_info["multi_pipeline_propose_notify"] = True
        elif _is_propose:
            needed = _CALENDAR_PROPOSE_TOOLS
            hijackers = frozenset()
            route_info["multi_pipeline_propose_only"] = True
        else:  # notify-only
            needed = _NOTIFY_TOOLS
            hijackers = frozenset()
            route_info["multi_pipeline_notify_only"] = True

        existing_names = {e.name for e in candidates}
        # Rimuovi hijackers (add-only e' la default policy del rerank, ma
        # qui rimuoviamo perche' sono distrattori semantici dimostrati).
        if hijackers:
            candidates = [e for e in candidates if e.name not in hijackers]
        # Promote i pipeline tools all'INIZIO della lista (priorita'): bug
        # live turn e0cd5bfe — planner ha skippato get_inputs perche' era
        # in 7° posizione del top-K, scegliendo send_messages diretto.
        # Inserire prima rende visibile il sequencing corretto al LLM.
        _to_promote = []
        for _need in needed:
            _exec = next((e for e in catalog if e.name == _need), None)
            if _exec is None:
                continue
            if _need not in existing_names:
                _to_promote.append(_exec)
            else:
                # Gia' nel pool: rimuovi e re-inserisci all'inizio.
                candidates = [e for e in candidates if e.name != _need]
                _to_promote.append(_exec)
        # Ordine pipeline canonico: get_now, find_events_empty, get_inputs,
        # create_events, read_events, send_messages. Riordina _to_promote
        # per rispettare la sequenza naturale.
        _pipeline_order = {n: i for i, n in enumerate(needed)}
        _to_promote.sort(key=lambda e: _pipeline_order.get(e.name, 99))
        candidates = _to_promote + candidates
        if verbose:
            print(f"[multi_pipeline] propose={_is_propose} notify={_is_notify}: "
                  f"injected {needed}, hijackers={list(hijackers)}")

    # Strato 2 (E.3): se l'utente ha rifiutato pipeline per QUESTA query,
    # forza nel pool gli "escape hatch" così il LLM vede alternative
    # concrete invece di ripetere il solito tool (root cause turn cf4ce937
    # 22/5/2026: 4 ✗ consecutive ma pool = [find_events_empty, filter_lists,
    # find_places, get_processes, consult_frontier] — admin/request_new
    # mancavano, LLM forzato a get_processes).
    try:
        from turn_feedback import count_consecutive_errors_for_query
        if count_consecutive_errors_for_query(user_query_for_run) >= 1:
            _have = {e.name for e in candidates}
            for _ehatch in ("admin", "consult_frontier", "request_new_executor"):
                if _ehatch in _have:
                    continue
                _e = next((e for e in catalog if e.name == _ehatch), None)
                if _e is not None:
                    candidates = list(candidates) + [_e]
                    _have.add(_ehatch)
    except Exception as _ex:
        log.warning("escape_hatch injection failed: %s", _ex)

    log.candidates = [e.name for e in candidates]
    if verbose:
        print(f"[prefilter] candidati: {log.candidates}")

    # Fase C3 (11/5/2026): re-render planner_system con sezioni mirate dopo
    # che `route_info` (con intent.verb/intent.object) e' disponibile. Selettore
    # deterministico `vocab.sections_for_object(obj)` (§7.9). Fallback: se
    # l'object e' unknown o non mappato, include TUTTE le sezioni (degrade
    # graceful — comportamento del primo render). Confidence dal route_info
    # come ulteriore guard: se < 0.6 (intent extractor incerto), all sections.
    try:
        from vocab import sections_for_object as _sections_for_object
        from vocab import object_is_core_only as _object_is_core_only
        _intent_for_route = (route_info or {}).get("intent") or {}
        _conf = (route_info or {}).get("confidence")
        # Compound (decomposer §4/ADR 0114): le sezioni vanno scelte sull'UNIONE
        # degli object di TUTTE le clausole, non solo l'object primario. Una
        # query "find urls ... send messages" tocca due domini: potare sul solo
        # primario (urls) fa sparire la sezione 'mail' allo step di send, e con
        # essa la regola self-send (§7.3 generale, non per-query).
        _objs = []
        if _intent_for_route.get("object"):
            _objs.append(_intent_for_route["object"])
        for _a in (_intent_for_route.get("actions") or []):
            if isinstance(_a, dict) and _a.get("object"):
                _objs.append(_a["object"])
        _objs = list(dict.fromkeys(_objs))  # dedup, preserva ordine
        if not isinstance(_conf, (int, float)) or _conf < 0.6 or not _objs:
            _sections_resolved = None  # all (degrade graceful)
        else:
            _secs: list[str] = []
            _any_unknown = False
            for _o in _objs:
                _cs = _sections_for_object(_o)
                if _cs:
                    for _s in _cs:
                        if _s not in _secs:
                            _secs.append(_s)
                elif _object_is_core_only(_o):
                    continue  # core-only: nessuna sezione dominio
                else:
                    _any_unknown = True  # object ignoto → all (safe degrade)
                    break
            _sections_resolved = None if _any_unknown else _secs
        # Re-render solo se la lista differisce dall'all-sections iniziale.
        if _sections_resolved is not None:
            _planner_targeted = prompt_loader.compose(
                "planner", DEFAULT_LANG,
                sections=_sections_resolved,
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
            # Riapplica gli addenda (credenziali + reference images) gia'
            # accumulati nel `planner_system`, calcolando la diff rispetto
            # all'iniziale rendering all-sections.
            _planner_all = prompt_loader.compose(
                "planner", DEFAULT_LANG, sections=None,
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
            if planner_system.startswith(_planner_all):
                _suffix = planner_system[len(_planner_all):]
                planner_system = _planner_targeted + _suffix
            # Se l'utente ha esteso planner_system in modo non-prefix (caso
            # raro), lasciamo l'iniziale all-sections (no regress, no info loss).
    except Exception as _e:
        # Niente fail su difetti del routing: rimaniamo con all-sections.
        log.warning("planner section routing skipped: %s", _e)

    # IMPLICIT ACTIONS injection (ADR 0129, 14/5/2026): se l'intent extractor
    # ha rilevato azioni mutating implicite (pattern noun→object senza verbo
    # mutating esplicito nella query), inietta un blocco strutturato nel
    # prompt PLANNER cosi' il LLM vede le entry come hint deterministico
    # (la regola comportamentale e' nell'invariante `_core.j2`).
    try:
        _intent = route_info.get("intent") if isinstance(route_info, dict) else None
        _implicit = (_intent or {}).get("implicit_actions") if isinstance(_intent, dict) else None
        print(f"[implicit_actions] route_intent={bool(_intent)} "
              f"implicit_count={len(_implicit or []) if isinstance(_implicit, list) else 'N/A'} "
              f"resume={bool(resume_with_scratchpad)}", flush=True)
        if isinstance(_implicit, list) and _implicit:
            _ia_lines = [
                "",
                "═" * 70,
                "IMPLICIT ACTIONS rilevate dall'intent extractor (ADR 0129):",
            ]
            for _a in _implicit:
                if not isinstance(_a, dict):
                    continue
                _ia_lines.append(
                    f"  - verb={_a.get('verb')!r} "
                    f"object={_a.get('object')!r} "
                    f"strategy={_a.get('strategy')!r} "
                    f"confidence={_a.get('confidence')} "
                    f"(noun='{_a.get('noun_token','')}')"
                )
            _ia_lines.append("Applica la regola IMPLICIT ACTIONS dell'invariante.")
            _ia_lines.append("═" * 70)
            planner_system = planner_system + "\n" + "\n".join(_ia_lines)
    except Exception as _e:
        if verbose:
            print(f"[implicit_actions] injection failed: {_e}")

    # Provider selection (27/4 sera): default = Gemma 4 26B (llamacpp) come "middle" tier
    # locale per pianificare task multi-step. Override esplicito via env METNOS_PLANNER_*.
    # think (28/4 sera): default True sul planner.
    # Il bug Gemma "tool_call magnetico get_files anche con reasoning
    # corretto" si manifestava solo con tools_for_step gonfio (15-22 tool):
    # il pattern matching del modello sotto-pesava le description e si attaccava
    # a nomi calamita. Con prefilter k_max=8 + cap effettivo a 9 (incl. synth),
    # il bug non si riproduce piu': budget 256/512/768/1024 producono tutti
    # tool_call corretto su query mail/foto/compute. Default reasoning_budget
    # 512 (sweet spot: ragionamento sufficiente, latenza contenuta).
    env_think = os.environ.get("METNOS_PLANNER_THINK")
    if think is None:
        if env_think is not None:
            think = env_think.lower() in ("1", "true", "yes", "on")
        else:
            think = True  # default v1.1: think on (post-fix prefilter k_max=8)
    if model:
        provider = OllamaProvider(model=model, think=think)
    else:
        # ADR 0146: default planner = llamacpp + Gemma 4 26B su :8080.
        # METNOS_PLANNER_PROVIDER=ollama resta supportato per back-compat,
        # ma richiede ora METNOS_PLANNER_MODEL esplicito (no fallback silenzioso
        # a qwen3:8b che era latente-broken post-ADR 0146 con ollama disabilitato).
        planner_provider = os.environ.get("METNOS_PLANNER_PROVIDER", "llamacpp")
        if planner_provider == "ollama":
            ollama_model = os.environ.get("METNOS_PLANNER_MODEL")
            if not ollama_model:
                raise ProviderError(
                    "METNOS_PLANNER_PROVIDER=ollama richiede METNOS_PLANNER_MODEL "
                    "esplicito (no default post-ADR 0146). Imposta es. "
                    "METNOS_PLANNER_MODEL=qwen3:8b oppure rimuovi "
                    "METNOS_PLANNER_PROVIDER per usare il default llamacpp+Gemma."
                )
            provider = OllamaProvider(
                model=ollama_model,
                endpoint=os.environ.get("METNOS_PLANNER_ENDPOINT", "http://localhost:11434"),
                think=think,
            )
        elif planner_provider == "anthropic":
            # Tier frontier (Claude) per il PLANNER. Opt-in via env, usato
            # per bench comparativi vs LLM medium locale (Gemma 4 26B).
            # Costa ~$0.015/turno (Sonnet) o ~$0.075/turno (Opus). Default
            # haiku se non specificato (cheap+veloce).
            from llm_provider import AnthropicProvider
            anth_model = os.environ.get("METNOS_PLANNER_MODEL",
                                          "claude-haiku-4-5-20251001")
            provider = AnthropicProvider(model=anth_model)
        else:
            provider = make_provider_from_spec({
                "provider": "llamacpp",
                "model": os.environ.get("METNOS_PLANNER_MODEL", "gemma-4-26B-A4B-it-UD-Q4_K_M.gguf"),
                "endpoint": os.environ.get("METNOS_PLANNER_ENDPOINT", "http://127.0.0.1:8080"),
            })
    tracker = CostTracker()
    is_multistep = (chosen_mode == "local")
    mnestoma = Mnestoma()  # storage di mnest e proto-mnest

    base_tools = render_tools_for_provider(candidates)
    history_for_llm = []  # messages array per chat API
    history_for_refs = []  # per resolve_references
    same_count = Counter()

    # ── resume_with_scratchpad (12/5/2026) ────────────────────────────
    # Continuation di un turno precedente fermato a get_inputs MID-pipeline.
    # Pre-popola scratchpad + history LLM + step counter. Bypass fast_path
    # e seed_step (sono per turni nuovi). Determinismo §7.9: nessun LLM.
    _resume_step_offset = 0
    if resume_with_scratchpad and isinstance(resume_with_scratchpad, list):
        for rs in resume_with_scratchpad:
            if not isinstance(rs, dict):
                continue
            _rs_step = int(rs.get("step") or 0) or (len(history_for_refs) + 1)
            _rs_tool = rs.get("tool") or "unknown"
            _rs_args = rs.get("args") or {}
            _rs_obs = rs.get("observation") or {}
            history_for_refs.append({
                "step": _rs_step, "tool": _rs_tool,
                "args": _rs_args, "observation": _rs_obs,
            })
            # StepLog audit-only: il log mostra la genesi del turno
            # continuation. Step records senza exec_ms (gia' eseguiti
            # nel turno precedente). vaglio_approved=True: gia' passati.
            _rs_step_log = StepLog(step_num=_rs_step)
            _rs_step_log.chosen_tool = _rs_tool
            _rs_step_log.raw_args = dict(_rs_args) if isinstance(_rs_args, dict) else {}
            _rs_step_log.resolved_args = dict(_rs_args) if isinstance(_rs_args, dict) else {}
            _rs_step_log.result = _rs_obs
            _rs_step_log.vaglio_approved = True
            _rs_step_log.error = "resumed_from_prior_turn"
            log.steps.append(_rs_step_log)
            # Tool_call virtuale per il LLM: il prossimo step PLANNER vede
            # gli step precedenti come tool calls eseguiti.
            _rs_call_id = f"resume_{_rs_step}"
            history_for_llm.append({
                "role": "assistant",
                "tool_calls": [{
                    "id": _rs_call_id, "type": "function",
                    "function": {
                        "name": _rs_tool,
                        "arguments": (_rs_args
                                      if isinstance(_rs_args, dict) else {}),
                    },
                }],
            })
            history_for_llm.append({
                "role": "tool", "tool_call_id": _rs_call_id,
                "name": _rs_tool,
                "content": json.dumps(_rs_obs, ensure_ascii=False),
            })
            _resume_step_offset = max(_resume_step_offset, _rs_step)
        if verbose:
            print(f"[resume] pre-populated {len(history_for_refs)} steps "
                  f"(offset={_resume_step_offset})")

    # ── Seed-step injection (ADR 0099) ────────────────────────────────
    # Quando la query contiene un URL completo, inietta deterministicamente
    # `read_urls_html(urls=[URL])` come step 1, BYPASSANDO la chiamata
    # PLANNER per quel primo step. Il PLANNER prende il controllo dallo
    # step 2 in poi, vedendo il risultato del read in history.
    #
    # Razionale: la regola PLANNER (url_explicit_seed) di ADR 0098 (URL
    # esplicito → read primo step) si e' rivelata insufficiente live (turn federvolley
    # 7/5/2026 15:29: PLANNER ha comunque scelto find_urls). Garantirlo
    # nel runtime e' deterministico; PLANNER resta libero post step 1.
    #
    # Esclusioni: skip se reference_images allegati (semantica diversa) o
    # se resume_with_scratchpad (history gia' popolata).
    _seed_step_used = False
    _seed_step_n = 0
    if not _ref_images_for_prompt and not resume_with_scratchpad:
        _seed_hit = try_seed_step(user_query_for_run)
        if _seed_hit is not None:
            _seed_exec = next(
                (e for e in catalog if e.name == _seed_hit["executor"]),
                None,
            )
            if _seed_exec is not None:
                _seed_step_n = 1
                _seed_step = StepLog(step_num=_seed_step_n)
                _seed_step.chosen_tool = _seed_hit["executor"]
                _seed_step.raw_args = dict(_seed_hit["args"])
                _seed_step.resolved_args = dict(_seed_hit["args"])
                _seed_step.vaglio_approved = True  # read-only safe-by-construction
                _t_seed = time.perf_counter()
                try:
                    _seed_obs = invoke_executor(
                        _seed_exec, _seed_hit["args"],
                        timeout_s=getattr(_seed_exec, "timeout_s", None) or 30,
                        autonomy="supervised", turn_id=turn_id,
                        actor=actor, channel=channel,
                    )
                except Exception as ex:
                    _seed_obs = {"ok": False,
                                  "error": f"{type(ex).__name__}: {ex}"}
                _seed_step.exec_ms = int((time.perf_counter() - _t_seed) * 1000)
                _seed_step.result = _seed_obs
                _seed_step.seed_step = True
                # Inserimento nel log + history per il PLANNER successivo.
                log.steps.append(_seed_step)
                _seed_call_id = "seed_0"
                history_for_refs.append({
                    "step": _seed_step_n,
                    "tool": _seed_hit["executor"],
                    "args": _seed_hit["args"],
                    "observation": _seed_obs,
                })
                history_for_llm.append({
                    "role": "assistant",
                    "tool_calls": [{
                        "id": _seed_call_id, "type": "function",
                        "function": {
                            "name": _seed_hit["executor"],
                            "arguments": _seed_hit["args"],
                        },
                    }],
                })
                history_for_llm.append({
                    "role": "tool",
                    "tool_call_id": _seed_call_id,
                    "name": _seed_hit["executor"],
                    "content": json.dumps(_seed_obs, ensure_ascii=False),
                })
                _seed_step_used = True
                if verbose:
                    print(f"[seed_step] hit url={_seed_hit['url']} "
                          f"executor={_seed_hit['executor']} "
                          f"exec_ms={_seed_step.exec_ms} "
                          f"ok={_seed_obs.get('ok')}")

    # ── Reference images uploaded (ADR 0092, 5/5/2026) ────────────
    # Foto allegate al turno via Telegram caption-photo o HTTP drag&drop.
    # Inietta uno step 0 virtuale `@uploaded` nello scratchpad cosi' il
    # PLANNER (al primo step reale) trova entries pronte per consumer-match
    # → find_images_indices(from_step=1, idx=...) → auto-explode in
    # reference_images. Il singular di `reference_images` e' `reference_image`,
    # quindi includere quel campo nelle entries (in piu' a `path` per i
    # consumer-match generici tipo read_files).
    _ref_images = list(reference_images or [])
    _ref_images = [p for p in _ref_images if isinstance(p, str) and p.strip()]
    if _ref_images:
        upload_entries = [
            {"path": p, "reference_image": p, "source": "upload"}
            for p in _ref_images
        ]
        upload_obs = {
            "ok": True,
            "entries": upload_entries,
            "_virtual": True,
            "_kind": "uploaded_reference_images",
            "n": len(upload_entries),
        }
        # Tool call + tool result virtuali, formato compatibile con i
        # provider OpenAI/Ollama tool-use. call_id univoco per evitare
        # collisioni con tc.call_id reali.
        _upload_call_id = "upload_0"
        history_for_refs.append({
            "step": 1,
            "tool": "@uploaded",
            "args": {"source": "upload", "n": len(upload_entries)},
            "observation": upload_obs,
        })
        history_for_llm.append({
            "role": "assistant",
            "tool_calls": [{
                "id": _upload_call_id, "type": "function",
                "function": {"name": "@uploaded", "arguments": {}},
            }],
        })
        history_for_llm.append({
            "role": "tool", "tool_call_id": _upload_call_id,
            "name": "@uploaded",
            "content": json.dumps(upload_obs, ensure_ascii=False),
        })
        # StepLog con step_num=0: virtual, NON conta verso cap_steps. Visibile
        # nel turn log per audit.
        _virtual_step = StepLog(step_num=0)
        _virtual_step.chosen_tool = "@uploaded"
        _virtual_step.raw_args = {"source": "upload"}
        _virtual_step.resolved_args = {}
        _virtual_step.result = upload_obs
        _virtual_step.vaglio_approved = True
        log.steps.append(_virtual_step)
        if verbose:
            print(f"[upload] {len(upload_entries)} reference images → step 0 virtual")


    # Storia (tool, identifier) per detectare duplicati di lettura
    # identifier = args.path per fs_*, args.url per get_urls
    read_calls_seen = []  # list of (step_num, tool_name, identifier)
    consecutive_blocked = 0  # step consecutivi senza progresso (duplicate/inline/error)
    LOOP_BREAK_THRESHOLD = 3

    # Loop start: 2 se seed_step ha gia' consumato step_num=1, altrimenti 1.
    # cap_steps non cambia: il seed_step CONTA come step (consume budget).
    # Se resume_with_scratchpad: parte da `max(prior_step) + 1`.
    if _resume_step_offset > 0:
        _loop_start_step = _resume_step_offset + 1
    else:
        _loop_start_step = _seed_step_n + 1 if _seed_step_used else 1

    # Flag turno: frontier loop-retry consumato? (16/5/2026, una sola
    # volta per turno; pattern fallback su CYCLIC_CALL primo step).
    _frontier_loop_retry_done = False

    for step_num in range(_loop_start_step, cap_steps + 1):
        # Install-on-demand auto-inject (§7.3, 17/5/2026): se l'ULTIMO step
        # ha ritornato `binary_missing` E nessuno step admin ha gia' processato
        # quel `suggested_install`, sintetizza step admin AUTO senza chiamare
        # PLANNER. Pattern §7.9 deterministico. Razionale: il LLM Gemma 26B
        # ignora sistematicamente la rule planner `install_on_demand_binary_missing`
        # (final_answer e' il path di minor resistenza per il LLM), quindi
        # runtime forza con tool_call sintetico. Una volta che admin emette
        # approval_required, il flow ricade nella pipeline standard
        # (CARD HMAC consent -> user approve -> sudoer -> auto-resume T2).
        if log.steps:
            _last_step = log.steps[-1]
            _last_obs_io = _last_step.result if isinstance(_last_step.result, dict) else {}
            _io_bm = _detect_binary_missing_in_obs(_last_obs_io)
            if _io_bm and (_last_step.chosen_tool or "") != "admin":
                _io_sugg = _io_bm.get("suggested_install", "")
                _io_pkg = _io_bm.get("package", "?")
                _io_bin = _io_bm.get("missing_binary", "?")
                _io_already = any(
                    (s.chosen_tool or "") == "admin"
                    and isinstance(s.raw_args, dict)
                    and s.raw_args.get("command_proposed") == _io_sugg
                    for s in log.steps
                )
                if _io_sugg and not _io_already:
                    # Sintetizza tool_call admin via invoke_verb_unique
                    from loader import invoke_verb_unique
                    _io_admin_args = {
                        "intent": f"install {_io_pkg} required by {_last_step.chosen_tool} (auto-inject install-on-demand)",
                        "command_proposed": _io_sugg,
                    }
                    _io_obs = invoke_verb_unique(
                        "admin", caller="agent_runtime",
                        intent=_io_admin_args["intent"],
                        command_proposed=_io_admin_args["command_proposed"],
                        actor=actor or "host",
                    )
                    # Append step sintetico nel log
                    _io_step = StepLog(
                        step_num=step_num,
                        chosen_tool="admin",
                        raw_args=_io_admin_args,
                        resolved_args=_io_admin_args,
                        llm_text=f"(install_on_demand: auto-inject admin for {_io_bin})",
                        result=_io_obs,
                    )
                    log.steps.append(_io_step)
                    # Se admin emette approval_required, save pending resume
                    # + chiudi turno con CARD (esattamente come fa branch admin
                    # standard).
                    if isinstance(_io_obs, dict) and _io_obs.get("approval_required"):
                        try:
                            from install_resume_state import save as _io_save
                            _io_save(
                                admin_signature=_io_obs.get("signature", ""),
                                executor=_last_step.chosen_tool or "",
                                args_base=dict(_last_step.resolved_args
                                                or _last_step.raw_args or {}),
                                actor=actor or "host",
                                channel=channel or "",
                            )
                        except Exception as _io_e:  # noqa: BLE001
                            import logging as _logging
                            _logging.getLogger(__name__).warning(
                                "install_on_demand T1 save failed: %s", _io_e)
                        _io_proposal = {
                            "kind": "admin_approval",
                            "step_num": step_num,
                            "executor": "admin",
                            "cap_field": "actor_consent_token",
                            "cap_suggested": _io_obs.get("consent_token", ""),
                            "args_original": dict(_io_admin_args),
                            "args_suggested": dict(_io_admin_args,
                                                    actor_consent_token=_io_obs.get("consent_token", "")),
                            "approval_card": _io_obs.get("approval_card") or {},
                            "signature": _io_obs.get("signature", ""),
                        }
                        log.final_kind = "answer"
                        log.final_message = (
                            f"Per completare l'operazione precedente serve "
                            f"installare il pacchetto `{_io_pkg}`. "
                            + (_io_obs.get("summary") or "Approva la card.")
                        )
                        log._pending_admin_approval = _io_proposal  # type: ignore[attr-defined]
                        log.ts_end = time.time()
                        log.write()
                        log.expandable_caps = [_io_proposal]
                        return log
                    # Se admin returna execute_silent (pkg whitelisted)
                    # ed ok=True, lascia il loop continuare: il prossimo
                    # iterazione attivera' il T2 hook (post-admin success)
                    # e ri-eseguira' l'executor originale.
                    continue

        # Strategia E (ADR 0133): early loop-detect su (tool, error_class)
        # ripetuti. Cattura il caso residuo dove duplicate_call (args
        # identici) + cap_same_executor (10) + consecutive_blocked (3)
        # non scattano abbastanza presto. Soglia 2: due fail consecutivi
        # stesso (tool, error_class) = loop confermato.
        if step_num > _loop_start_step + 1:  # serve almeno 2 step pregressi
            try:
                from loop_detect import (is_repeated_failure,
                                          repeated_failure_hint)
                if is_repeated_failure(log.steps, threshold=2):
                    _e_hint = repeated_failure_hint(log.steps)
                    # Caso specifico §2.8: loop su `invalid_args` con
                    # `validation_failures` indica che il PLANNER non e'
                    # riuscito a fornire gli args required. MSG_LOOP_BREAK
                    # generico e' cripto per l'utente. Sostituiamo con un
                    # final_answer dignitoso che cita esattamente il
                    # constraint violato.
                    _last = log.steps[-1] if log.steps else None
                    _last_res = (getattr(_last, "result", None) or {}) \
                        if _last else {}
                    _is_invalid_loop = (
                        isinstance(_last_res, dict)
                        and _last_res.get("error_class") == "invalid_args"
                        and bool(_last_res.get("validation_failures"))
                    )
                    # Loop su `request_new_executor` rejected per jaccard
                    # (L2 admission). Il PLANNER ha tentato di creare un
                    # executor duplicato di uno gia' nel catalog. Pattern
                    # anti-§2.8: final_answer onesto che spiega che il
                    # tool esiste gia' e suggerisce di chiarire l'intent.
                    _is_synth_loop = (
                        getattr(_last, "chosen_tool", "") == "request_new_executor"
                        and isinstance(_last_res, dict)
                        and (
                            _last_res.get("rejected") is True
                            or "jaccard" in str(_last_res.get("reason", "")).lower()
                            or "jaccard" in str(_last_res.get("error", "")).lower()
                            or "duplicates" in str(_last_res.get("error", ""))
                        )
                    )
                    if _is_synth_loop:
                        _exp = ""
                        try:
                            _exp = (getattr(_last, "raw_args", None) or {}).get(
                                "expected_name", "") or ""
                        except Exception:
                            _exp = ""
                        log.final_kind = "answer"
                        try:
                            log.final_message = msg(
                                "MSG_SYNTH_LOOP_FINAL", expected=_exp,
                            )
                        except Exception:
                            log.final_message = (
                                f"Un executor simile a `{_exp}` esiste gia' "
                                f"nel catalog. Per completare la richiesta "
                                f"riformula con maggiori dettagli (file, "
                                f"directory, parole chiave) invece di "
                                f"proporre un nuovo strumento."
                            )
                        log.ts_end = time.time(); log.write(); return log
                    if _is_invalid_loop:
                        _vfails = _last_res.get("validation_failures") or []
                        _vmsg = "; ".join(str(v) for v in _vfails)
                        _tool = getattr(_last, "chosen_tool", "") or ""
                        log.final_kind = "answer"
                        try:
                            log.final_message = msg(
                                "MSG_VALIDATION_LOOP_FINAL",
                                tool=_tool, fails=_vmsg,
                            )
                        except Exception:
                            log.final_message = (
                                f"Per completare l'operazione `{_tool}` "
                                f"mancano argomenti richiesti ({_vmsg}). "
                                f"Riformula la query indicando i parametri "
                                f"mancanti (es. file, directory, parole "
                                f"chiave)."
                            )
                        log.ts_end = time.time(); log.write(); return log
                    log.final_kind = "loop_break"
                    log.final_message = msg(
                        "MSG_LOOP_BREAK", n=2,
                        hint=_e_hint or _loop_break_hint(
                            _intent_object_from_route(route_info)),
                    )
                    log.ts_end = time.time(); log.write(); return log
            except Exception as _e:
                import logging as _logging
                _logging.getLogger(__name__).warning(
                    "loop_detect failed: %s", _e)
        step = StepLog(step_num=step_num)
        # Attribuisci prefilter+intent al PRIMO step LLM (potrebbe essere 1 o 2
        # a seconda del seed_step). Sono il costo di setup del turno
        # (rank_adaptive + intent extractor LLM, una volta sola per turno).
        if step_num == _loop_start_step:
            step.prefilter_ms = _prefilter_only_ms
            step.intent_ms = _intent_ms_acc if _intent_ms_acc > 0 else None
        if progress is not None:
            try:
                progress.update_free(f"step {step_num} · sto decidendo il prossimo passo…")
            except Exception as _e:  # silent swallow (auto-fixed)
                _LOG.warning("silent exception in %s: %s", __name__, _e)
        # Aggiungi il builtin scratchpad_read se ci sono entries di questo turno.
        # SYNTH_REQUEST_TOOL e' sempre disponibile: e' il telos di non-rinuncia
        # cablato come tool meta che il LLM puo' chiamare quando nessun seed copre.
        sp_entries = sp.list_for_turn(turn_id)
        # describe_entries e' magnetic per il planner: lo includiamo SOLO
        # quando il verbo dell'utente e' read/list/find/describe (cioe' la
        # richiesta termina nel mostrare/riassumere). Per verbi d'azione
        # (move/delete/send/...) describe distrae il planner dal completare
        # l'azione (caso live 29/4/2026: sposta mail → describe → loop_break).
        # classify_entries resta sempre: utile come passo intermedio per la
        # maggior parte delle pipeline.
        _intent = (route_info or {}).get("intent") or {}
        _intent_verb = _intent.get("verb")
        _action_verbs = {"move", "delete", "send", "write", "extract", "create",
                         "compress", "compute", "set"}
        _allow_describe = (_intent_verb is None) or (_intent_verb not in _action_verbs)
        # I tool `*_tasks` (scheduler v2) sono iniettati condizionalmente:
        # solo se la query contiene marker scheduling. Altrimenti il PLANNER
        # LLM li seleziona erroneamente su query mail/file (bug live 23/5:
        # «cerca mail bookings» → PLANNER scelse read_tasks_history). Lista
        # marker in `tool_grammar._TASKS_MARKERS`; helper `_has_word` per
        # word-boundary deterministic match §7.9.
        # Predicato CONDIVISO con il pool grammar (tool_grammar): include
        # anche le frasi di scheduling «every 30 min» / «ogni 30 minuti»
        # (_RE_SCHEDULE_PHRASE), non solo le parole _TASKS_MARKERS — prima
        # "every 30 min" non iniettava create_tasks (bug live 10/6/2026).
        from tool_grammar import query_has_tasks_marker as _qhtm_inject
        _query_has_tasks_marker = _qhtm_inject(user_query or "")
        synth_tools = [
            SYNTH_REQUEST_TOOL, CLASSIFY_ENTRIES_TOOL, LOCATION_REQUEST_TOOL,
        ]
        if _query_has_tasks_marker:
            synth_tools.extend([
                CREATE_TASKS_TOOL, LIST_TASKS_TOOL,
                DELETE_TASKS_TOOL, READ_TASKS_TOOL,
                SET_TASKS_TOOL, READ_TASKS_HISTORY_TOOL,
            ])
        if _allow_describe:
            synth_tools.append(DESCRIBE_ENTRIES_TOOL)
        # filter_entries e' un pipeline-helper come classify_entries: serve
        # quasi sempre come step intermedio (es. "sposta mail di pubblicita"
        # = read+classify+filter+move). Lo iniettiamo sempre se disponibile
        # nel catalog (e' un executor regolare, non un builtin runtime).
        # Caso live 29/4/2026: senza filter, il planner si blocca dopo
        # classify perche' non puo' selezionare il subset.
        # undo_last_turn e' universalmente utile: l'utente puo' chiedere
        # "annulla" in qualunque momento, indipendentemente dal verbo
        # estratto dall'intent (caso live 29/4/2026: intent mappava
        # "annulla" → delete, undo_last_turn non in candidati → planner
        # innescava synt inutile).
        _UNIVERSAL_HELPERS = ("filter_entries", "sort_entries", "compute_entries",
                              "filter_lists", "undo_last_turn")
        # Pipeline helpers che richiedono `from_step` su una lista preesistente:
        # esclusi dal pool al primo step §4.2. Caso live 15/5/2026: query
        # "fissa appuntamento mercoledi mattina dopo le 9" → PLANNER sceglie
        # `filter_entries(from_step=1, ...)` riferendo a se stesso (step 1
        # vuoto) → loop. General-purpose §7.3: vale per ogni *_entries
        # helper data-piping che opera su observation di step precedente.
        # `undo_last_turn` resta perche' e' azione utente diretta a qualsiasi
        # step (compreso il primo: «annulla» fa undo del turno PRECEDENTE).
        _FROM_STEP_HELPERS = frozenset({
            "filter_entries", "sort_entries", "compute_entries",
            "filter_lists", "classify_entries", "group_entries",
            "describe_entries",
        })
        _existing_names = {e.name for e in candidates}
        _added_any = False
        for _helper in _UNIVERSAL_HELPERS:
            if _helper in _existing_names:
                continue
            # Skip al primo step gli helpers from_step-only: senza step
            # precedente con entries non hanno argomento valido.
            if step_num == 1 and _helper in _FROM_STEP_HELPERS:
                continue
            _helper_exec = next((e for e in catalog if e.name == _helper), None)
            if _helper_exec is not None:
                candidates = list(candidates) + [_helper_exec]
                _added_any = True
        # Anche per i candidates gia' presenti via prefilter: al primo step
        # escludi i from_step-helpers. Vale anche se il prefilter li ha
        # scelti (rumore semantico, non utile §4.2).
        if step_num == 1:
            candidates = [e for e in candidates
                          if e.name not in _FROM_STEP_HELPERS]
            _added_any = True
        # Install-on-demand (§7.3, 17/5/2026): se uno step precedente ha
        # ritornato `binary_missing` in observation, inietta `admin` nel pool
        # corrente cosi' il PLANNER possa emettere admin(cmd=suggested_install)
        # come da rule planner `install_on_demand_binary_missing`. Senza
        # questo, admin non e' visibile al LLM (non nel pool top-K) e il
        # flow install-on-demand si interrompe a final_answer testuale.
        if step_num > 1:
            for _prev_step in log.steps:
                _prev_obs = _prev_step.result if isinstance(_prev_step.result, dict) else {}
                if _detect_binary_missing_in_obs(_prev_obs):
                    if "admin" not in _existing_names:
                        _admin_exec = next((e for e in catalog if e.name == "admin"), None)
                        if _admin_exec is not None:
                            candidates = list(candidates) + [_admin_exec]
                            _added_any = True
                    break
        if _added_any:
            base_tools = render_tools_for_provider(candidates)
        tools_for_step = base_tools + synth_tools + ([SCRATCHPAD_READ_TOOL] if sp_entries else [])

        # Reasoning budget dinamico (ADR 0099): step >= 2 ha history,
        # ranker gia' applicato, pool tool ristretto → decisione piu'
        # vincolata. Riduciamo da 512 (default LlamaCpp) a 256 → ~50%
        # latency PLANNER per step 2+. Step 1 mantiene 768 (poco piu' del
        # default) per il setup iniziale piu' aperto. Pass-through solo a
        # provider che lo supportano (LlamaCpp); altri provider ignorano
        # il kwarg via filter.
        #
        # Per-call think BUDGET modulation (19/5/2026, post bench Gemma 4 26B).
        # Euristica Roberto: pattern matching (tool calling) ha bisogno di
        # thinking ma non troppo. Skip completo causa loop_break (planner
        # sceglie tool sbagliato). Solo modulazione del budget basata su:
        # (a) complexity manifest [planning] o inferred via verb-of-name;
        # (b) dimensione pool tools_for_step.
        # Vedi [[feedback_thinking_budget_heuristic]] e
        # [[metnos_todo_high_think_per_model]] per validazione cross-model.
        # Opt-out: METNOS_THINK_MODULATION=0 → ritorno alla formula dyn legacy.
        _chat_kwargs: dict = dict(max_tokens=4096, temperature=0, think=think)
        if getattr(provider, "name", "") == "llamacpp":
            # Override env-driven:
            # METNOS_REASONING_BUDGET="dyn" (legacy, default safe per Gemma 4 26B)
            # | "ctx" (context-aware 19/5/2026 — opt-in per bench, pattern A+B
            #         su manifest [planning] complexity + verb-of-name fallback;
            #         bench iniziali mostrano regressione su query multi-step,
            #         richiede tuning corpus-based prima del default-on)
            # | "<int>" flat per tutti gli step.
            _rb_env = os.environ.get("METNOS_REASONING_BUDGET", "dyn")
            _think_mod = os.environ.get("METNOS_THINK_MODULATION", "1") == "1"
            if _rb_env == "ctx" and _think_mod:
                _chat_kwargs["reasoning_budget"] = _decide_reasoning_budget(
                    candidates, tools_for_step, step_num, _loop_start_step
                )
            elif _rb_env == "dyn":
                _chat_kwargs["reasoning_budget"] = 768 if step_num == _loop_start_step else 256
            else:
                try:
                    _chat_kwargs["reasoning_budget"] = int(_rb_env)
                except ValueError:
                    _chat_kwargs["reasoning_budget"] = 768 if step_num == _loop_start_step else 256

            # ADR 0133: grammar-constrained tool_call opt-in via env.
            # `METNOS_GRAMMAR=1` → genera grammar GBNF dal pool tools_for_step
            # e forza il LLM a emettere SOLO JSON tool_call valido. Risolve
            # bug PLANNER fragility (thinking loop, prosa al posto di
            # tool_call). Implicitamente disabilita thinking per quel call
            # (grammar + thinking + max_tokens collidono). §7.9 deterministico.
            if os.environ.get("METNOS_GRAMMAR", "0") == "1":
                try:
                    from tool_grammar import (generate_tool_grammar,
                                                filter_pool_for_grammar)
                    from prefilter import _QUERY_DEPENDENT_PRECURSORS
                    _proximity_markers = next(
                        (mk for _, prec, mk in _QUERY_DEPENDENT_PRECURSORS
                         if prec == "get_location"), ()
                    )
                    _pool_for_grammar, _excluded = filter_pool_for_grammar(
                        tools_for_step,
                        user_query_for_run or "",
                        proximity_markers=_proximity_markers,
                    )
                    # final_answer synthetic tool (ADR 0133 ext, 15/5/2026):
                    # abilitato da step 2 in poi. Allo step 1 forziamo
                    # l'esecuzione di un producer (no early-exit). Senza
                    # questo, il LLM grammar-mode non puo' emettere final
                    # naturale: regrediva su describe_entries duplicato.
                    _allow_fa = step_num >= 2
                    # request_disambiguation_from_user synthetic (16/5/2026,
                    # Test 6 fix sistemico): abilitato SOLO al primo step.
                    # La disambiguazione ha senso PRIMA di scegliere quale
                    # pipeline eseguire; dopo che si e' iniziato a eseguire,
                    # interrompere per chiedere all'utente sarebbe regression.
                    # Anti-loop: disabilitato dopo che la query proviene da
                    # un restart_turn_with_chosen_query (vedi
                    # orchestration._process_restart_turn_with_chosen_query
                    # che passa allow_disambig_synth=False). Senza, il
                    # PLANNER potrebbe ri-disambiguare la query disambiguata.
                    _allow_disambig = (step_num == 1) and bool(allow_disambig_synth)
                    # ADR 0149: opt-in canonical_query by-product (env flag,
                    # default off until step 2b/2v finishes verification).
                    _include_cq = os.environ.get(
                        "METNOS_CANONICAL_QUERY", "1"
                    ) == "1"
                    _grammar = generate_tool_grammar(
                        _pool_for_grammar,
                        allow_final_answer=_allow_fa,
                        allow_disambiguation=_allow_disambig,
                        include_canonical_query=_include_cq,
                    )
                    if _grammar:
                        _chat_kwargs["grammar"] = _grammar
                        if verbose:
                            print(f"[grammar] step {step_num}: "
                                  f"grammar {len(_grammar)} chars su "
                                  f"{len(_pool_for_grammar)} tools "
                                  f"(filtered {_excluded or '-'}, "
                                  f"final_answer={_allow_fa}, "
                                  f"disambig={_allow_disambig})")
                except Exception as _ex:
                    # `log` qui e' TurnLog (shadow): uso logger module
                    import logging as _logging
                    _logging.getLogger(__name__).warning(
                        "grammar generation failed: %s", _ex)

        # PLANNER call (ADR 0163 fase #7, 26/5/2026): planner_split rimosso
        # (Praxis Engine ADR 0161 sostituisce il path SPLIT). Single
        # chat_with_tools monolithic resta come fallback per il 6% query
        # che Praxis non gestisce.
        try:
            r = provider.chat_with_tools(
                planner_system, user_query_for_run, tools_for_step,
                history=history_for_llm, **_chat_kwargs,
            )
        except ProviderError as e:
            step.error = f"LLM error: {e}"; log.steps.append(step)
            log.final_kind = "error"; log.final_message = f"(errore LLM: {e})"
            log.ts_end = time.time(); log.write(); return log

        # Fallback tier FRONTIER per step 1 quando middle non capisce
        # (16/5/2026, Roberto): se al primo step il PLANNER middle
        # (a) emette `request_disambiguation_from_user` OR
        # (b) non emette alcun tool_call (text-only final)
        # → re-run con tier frontier (Sonnet/GPT-5 online). Una sola
        # volta per turno. Pattern §7.3 generale: middle prova, se non
        # capisce → frontier. Default attivo; disattivabile con
        # METNOS_PLANNER_TIER_FALLBACK=0. Skip automatico se frontier
        # non configurato (~/.config/metnos/llm_tiers.toml) o se solleva
        # eccezione: graceful degrade al risultato middle originale.
        if (
            step_num == 1
            and allow_disambig_synth
            and os.environ.get("METNOS_PLANNER_TIER_FALLBACK", "1") != "0"
            and (
                (r.tool_calls and r.tool_calls[0].name == "request_disambiguation_from_user")
                or not r.tool_calls
            )
        ):
            _front_skip_reason: str | None = None
            # Universal opt-out (§7.3): blocca chiamate frontier (Anthropic
            # Opus / GPT-5 / ecc.) per evitare costi. Usato in test E2E
            # paralleli e in scenari offline. Coerente anche con prod se
            # l'utente ha esplicitamente disabilitato il tier online.
            if os.environ.get("METNOS_DISABLE_FRONTIER") == "1":
                _front_skip_reason = "METNOS_DISABLE_FRONTIER=1"
            try:
                from llm_router import LLMRouter
                _front_router = LLMRouter()
                _front_spec = _front_router.tiers.get("frontier") or {}
                _front_model = _front_spec.get("model")
                _front_provider = (_front_spec.get("provider") or "").lower()
                if _front_skip_reason is not None:
                    pass  # gia' deciso skip via env
                elif not _front_model:
                    _front_skip_reason = "non configurato (tiers.toml)"
                else:
                    # Pre-check API key per evitare chiamata sicuramente
                    # fallita su provider online. Mappa provider → key
                    # source. §7.9 graceful degrade.
                    _has_key = True
                    if _front_provider == "anthropic":
                        try:
                            from llm_provider import _read_anthropic_key
                            _has_key = bool(_read_anthropic_key())
                        except Exception:
                            _has_key = False
                    elif _front_provider == "openai":
                        try:
                            from llm_provider import _read_openai_key
                            _has_key = bool(_read_openai_key())
                        except Exception:
                            _has_key = bool(os.environ.get("OPENAI_API_KEY"))
                    # Provider locali (llamacpp/ollama) non hanno API key
                    # da controllare: il provider falsa direttamente sulla
                    # connessione HTTP se l'endpoint non risponde.
                    if not _has_key:
                        _front_skip_reason = (
                            f"API key {_front_provider!r} non configurata "
                            f"(env, credentials store, o ~/.config/metnos/*.env)"
                        )
                # Telemetria fallback frontier (16/5/2026): counter JSON
                # persistente ~/.local/share/metnos/frontier_fallback_stats.json
                # per misurare % ricorso e tasso risoluzione. §7.9 deterministico.
                def _bump_front_stats(key: str) -> None:
                    try:
                        import json as _json
                        import config as _C  # §7.11 (lazy)
                        _p = _C.PATH_USER_DATA / "frontier_fallback_stats.json"
                        _p.parent.mkdir(parents=True, exist_ok=True)
                        _data = {}
                        if _p.exists():
                            try:
                                _data = _json.loads(_p.read_text())
                            except Exception:
                                _data = {}
                        _data[key] = int(_data.get(key, 0)) + 1
                        _p.write_text(_json.dumps(_data, indent=2))
                    except Exception:
                        pass

                if _front_skip_reason:
                    _bump_front_stats("skipped")
                    if verbose:
                        print(f"[step {step_num}] fallback frontier SKIP "
                              f"({_front_skip_reason})")
                else:
                    if verbose:
                        print(f"[step {step_num}] middle->{'disambig' if r.tool_calls else 'no-tool'}, fallback tier=frontier ({_front_provider}/{_front_model})")
                    # Filtra kwargs incompatibili col provider frontier.
                    # `grammar`/`reasoning_budget` sono specifici di
                    # llama-server; Anthropic/OpenAI non li accettano.
                    _LLAMACPP_ONLY_KWARGS = {
                        "grammar", "reasoning_budget", "n_predict",
                        "top_k", "min_p", "tfs_z", "typical_p",
                    }
                    _front_kwargs = {
                        k: v for k, v in _chat_kwargs.items()
                        if k not in _LLAMACPP_ONLY_KWARGS
                    }
                    # Marca il turno come "frontier gia' speso" PRIMA della
                    # chiamata: previene dual-fire con la cyclic fallback
                    # ~riga 4500 (entrambi i path costano una call frontier
                    # online; un solo budget per turno). Coerente con il
                    # set a riga ~4501 del cyclic branch.
                    _frontier_loop_retry_done = True
                    _bump_front_stats("invoked")
                    _r_front = _front_router.chat_with_tools(
                        planner_system, user_query_for_run, tools_for_step,
                        tier="frontier", history=history_for_llm, **_front_kwargs,
                    )
                    # Accetta frontier solo se ha emesso un tool_call NON-disambig
                    # (la disambig frontier non e' miglioria rispetto a middle).
                    if _r_front.tool_calls and _r_front.tool_calls[0].name != "request_disambiguation_from_user":
                        r = _r_front
                        _bump_front_stats("resolved")
                        if verbose:
                            print(f"[step {step_num}] frontier resolved → {r.tool_calls[0].name}")
                    else:
                        _bump_front_stats("no_improvement")
            except Exception as _ex:
                import logging as _logging
                _logging.getLogger(__name__).warning(
                    "tier fallback frontier failed: %s — degrade al middle result",
                    _ex)

        tracker.record_post_call(provider.name, r.model, r.in_tokens, r.out_tokens)
        step.llm_in_tokens = r.in_tokens; step.llm_out_tokens = r.out_tokens
        step.llm_latency_ms = r.latency_ms
        step.llm_text = r.text or ""
        step.llm_thinking = r.thinking or ""
        if verbose:
            print(f"[step {step_num}] llm {r.in_tokens}->{r.out_tokens} toks in {r.latency_ms}ms, tool_calls={len(r.tool_calls)}")
            if r.thinking:
                print(f"[step {step_num}] thinking: {r.thinking[:120]}…")

        # Caso 1: nessun tool_call -> testo finale
        # ADR 0154 shape FSM: step terminale deve dichiarare chosen_tool
        # ="final_answer" per garantire pipeline shape `E+ (F|A)?`.
        # Senza normalizzazione, chosen_tool=="" (default StepLog) genera
        # shape come "E?" che fallisce la regex `^E+F?$`.
        if not r.tool_calls:
            step.chosen_tool = "final_answer"
            log.steps.append(step)
            log.final_kind = "answer"; log.final_message = r.text or "(risposta vuota)"
            log.ts_end = time.time(); log.write(); return log

        # Caso 2: tool_call (D7 sequenziale = uno solo per turno).
        # Bug Gemma 4 26B: a volte emette 2+ tool_calls paralleli — il primo e'
        # un "placeholder magnetico" con args vuoti (es. get_files
        # entries=[]), il secondo/ultimo e' quello corretto coi reali args
        # derivati dalla query. Selettore: scegli il tool_call con args NON
        # vuoti; se piu' di uno qualifica, prendi l'ultimo (Gemma tende a
        # mettere l'intent "vero" in coda). Se nessuno ha args, prendi il
        # primo (fallback degenere).
        def _has_real_args(tcx):
            a = tcx.arguments if isinstance(tcx.arguments, dict) else {}
            for v in a.values():
                if v is None: continue
                if isinstance(v, (list, dict)) and len(v) == 0: continue
                if isinstance(v, str) and not v.strip(): continue
                return True
            return False
        candidates_tc = [t for t in r.tool_calls if _has_real_args(t)]
        if not candidates_tc:
            candidates_tc = list(r.tool_calls)
        tc = candidates_tc[-1]
        if len(r.tool_calls) > 1 and verbose:
            print(f"[step {step_num}] selected '{tc.name}' from {len(r.tool_calls)} tool_calls: {[t.name for t in r.tool_calls]}")
        chosen_name = tc.name
        raw_args = tc.arguments if isinstance(tc.arguments, dict) else {}
        step.chosen_tool = chosen_name
        step.raw_args = raw_args
        # ADR 0149: persist by-product canonical_query nel step log.
        step.canonical_query = getattr(tc, "canonical_query", "") or ""

        # ───── Pipeline shape FSM (20/5/2026, "sempre e per tutti") ─────
        # Invariante `E+ (F|A)?` + final_answer. Calcola stato accumulato
        # dagli step precedenti e simula la prossima transizione. Se ERROR
        # -> remediation pre-dispatch (cascade per data, dialog per action).
        _ps_handled = False
        try:
            from pipeline_shape import compute_state as _ps_state_fn
            from pipeline_shape import next_state as _ps_next
            _, _ps_err = _ps_next(
                _ps_state_fn(log.steps), chosen_name, raw_args,
            )
            if _ps_err in ("needs_data_source", "needs_action_target"):
                _ps_intent = (intent or {}).get("object") if isinstance(
                    intent, dict) else None
                _synth_obs = {
                    "ok": False, "error_class": _ps_err,
                    "intent_object": _ps_intent,
                    "user_query": user_query_for_run or "",
                    "verb": chosen_name.split("_", 1)[0],
                }
                _ps_remed = _maybe_remediate_obs(
                    _synth_obs, raw_args, chosen_name,
                    catalog=catalog, turn_id=turn_id,
                    actor=actor, channel=channel, verbose=verbose,
                )
                if _ps_remed is not None:
                    _ps_prereq_step, _ps_prereq_obs, _ps_final_obs = _ps_remed
                    _ps_prereq_step.step_num = step_num
                    log.steps.append(_ps_prereq_step)
                    history_for_refs.append({
                        "step": step_num,
                        "tool": _ps_prereq_step.chosen_tool,
                        "args": _ps_prereq_step.raw_args,
                        "observation": _ps_prereq_obs,
                    })
                    # Record per LLM history per coerenza (no tool_call_id
                    # canonico — uso name+content come da pattern):
                    history_for_llm.append({
                        "role": "tool",
                        "name": _ps_prereq_step.chosen_tool,
                        "content": json.dumps(_ps_prereq_obs,
                                                ensure_ascii=False)[:4000],
                    })
                    # needs_action_target -> _ps_final_obs e' needs_inputs:
                    # chiude il turno via orchestratore dialog (pattern
                    # esistente, riusato).
                    if (isinstance(_ps_final_obs, dict)
                            and _ps_final_obs.get("decision") == "needs_inputs"):
                        try:
                            from orchestration import orchestrate_needs_inputs
                            _sender = (
                                f"{channel}:{actor}" if channel
                                else (actor or "host")
                            )
                            _gi = orchestrate_needs_inputs(
                                _ps_final_obs,
                                sender_id=_sender,
                                actor=actor or "host",
                                channel=channel or None,
                            )
                            log.final_kind = "needs_inputs"
                            log.final_message = (
                                _gi.get("message", "")
                                if isinstance(_gi, dict) else ""
                            )
                        except Exception as _ie:
                            log.final_kind = "error"
                            log.final_message = (
                                f"Servono input ma orchestrazione "
                                f"fallita: {type(_ie).__name__}"
                            )
                        log.ts_end = time.time(); log.write(); return log
                    # needs_data_source: retry dell'executor originale ha
                    # gia' prodotto _ps_final_obs (entries arricchite).
                    # Appendo step originale col risultato e proseguo
                    # all'iterazione successiva del loop planner.
                    step.result = _ps_final_obs
                    log.steps.append(step)
                    history_for_refs.append({
                        "step": step_num + 1, "tool": chosen_name,
                        "args": raw_args, "observation": _ps_final_obs,
                    })
                    history_for_llm.append({
                        "role": "tool", "name": chosen_name,
                        "content": json.dumps(_ps_final_obs,
                                                ensure_ascii=False)[:4000],
                    })
                    _ps_handled = True
                # Se remediation = None: fall-through, executor originale
                # tentato e fallira' naturalmente (final_answer aggrega).
            elif _ps_err == "pipeline_already_closed":
                # Step dopo terminatore F/A: log, force final_answer.
                _LOG.warning(
                    "pipeline_already_closed: step %d %r emesso dopo "
                    "terminatore; chiudo turno.", step_num, chosen_name,
                )
                # Final_answer rinvia all'ultimo step utile gia' eseguito.
                # Il runtime aggrega metadata via _append_search_results
                # e formatter standard.
                log.final_kind = "answer"
                if not log.final_message:
                    log.final_message = ""
                log.ts_end = time.time(); log.write(); return log
        except Exception as _ps_ex:
            _LOG.warning(
                "pipeline_shape check failed for %s: %r",
                chosen_name, _ps_ex,
            )
        if _ps_handled:
            continue
        # ──────────────────────────────────────────────────────────────

        # Synthetic `final_answer` da grammar (ADR 0133 ext, 15/5/2026):
        # il LLM in grammar-mode emette `final_answer({message:"..."})`
        # come tool_call per chiudere il turno con testo naturale. Il
        # runtime intercetta qui e termina senza invocare alcun executor.
        if chosen_name == "final_answer":
            _msg = raw_args.get("message", "") if isinstance(raw_args, dict) else ""
            # 21/5/2026 v8 — detail_md autoritativo universale (§7.9).
            # Quando l'ULTIMO step produttivo ha emesso `detail_md`, quel
            # blocco e' la rappresentazione canonica del risultato: l'LLM
            # nel final_answer NON deve riscriverlo (perderebbe ID precisi,
            # tabelle, link e formato strutturato, rompendo l'usabilita'
            # come "cancella timer 17"). Politica content-driven (non
            # interceptor tool-pair, ADR 0155 compatibile): l'executor
            # decide se produrre detail_md, il runtime lo rispetta.
            # Coerente con `_compose_final_message_from_obs` (linee 1770-77)
            # che gia' fa lo stesso negli auto-final paths.
            for _past_step in reversed(log.steps):
                _obs = getattr(_past_step, "observation", None)
                if not isinstance(_obs, dict):
                    continue
                # Considera solo l'ultimo step con observation strutturata.
                if _obs.get("ok") is None and "detail_md" not in _obs:
                    continue
                _md = _obs.get("detail_md")
                if isinstance(_md, str) and _md.strip():
                    _msg = _md.strip()
                break
            log.steps.append(step)
            log.final_kind = "answer"
            _msg_final = str(_msg).strip() or (r.text or "(risposta vuota)")
            # §2.8 leak guard: il PLANNER LLM a volte copia messaggi
            # internal del runtime ("DUPLICATE_CALL:", "FORMULA LA
            # FINAL_ANSWER", "validation failed:") direttamente nel
            # final_answer.message. Quei marker sono gergo runtime: il
            # destinatario e' il LLM stesso, non l'utente. Detection
            # deterministica §7.9: se prefisso runtime-internal,
            # sostituisci con messaggio onesto sintetico basato sul vero
            # last error (validation_failures o ultimo step ok=False).
            if _has_runtime_internal_leak(_msg_final):
                _msg_final = _compose_honest_from_last_error(log)
            log.final_message = _msg_final
            log.ts_end = time.time(); log.write(); return log

        # Synthetic `request_disambiguation_from_user` da grammar
        # (Test 6 fix sistemico, 16/5/2026). Il LLM in grammar-mode emette
        # `request_disambiguation_from_user({question, options[]})` quando
        # rileva due interpretazioni plausibili. Le `options` sono query
        # RIFRASATE complete (es. "Manda una email a me con gli
        # appuntamenti di domani" vs "Cerca nelle mie email i messaggi
        # sugli appuntamenti"). Runtime materializza `get_inputs(kind=
        # choice)` riusando l'orchestratore esistente; la scelta utente
        # diventa la nuova `user_query` del turno successivo. Pattern §7.3
        # generale, language-agnostic.
        if chosen_name == "request_disambiguation_from_user":
            _q = raw_args.get("question", "") if isinstance(raw_args, dict) else ""
            _opts = raw_args.get("options", []) if isinstance(raw_args, dict) else []
            _opts_clean = [str(o).strip() for o in _opts if isinstance(o, str) and o.strip()]
            try:
                from orchestration import invoke_get_inputs_internal
                # Sender_id convention per channel (allinea con
                # _http_sender_id / telegram daemon: ogni channel ha la
                # propria, e la load_pending in continuation deve usare la
                # stessa). Pattern §7.3 dispatch table.
                _conv_id = getattr(log, "conversation_id", "") or ""
                _ch = (channel or "").lower()
                if _ch.startswith("http"):
                    _sender = f"http:{actor or 'host'}:{_conv_id or '_'}"
                elif _ch.startswith("telegram"):
                    _sender = f"telegram:{actor or 'host'}"
                else:
                    _sender = (f"{channel}:{actor}" if channel
                                else (actor or "host"))
                _resp = invoke_get_inputs_internal(
                    sender_id=_sender,
                    title=str(_q).strip()[:80] or "Quale interpretazione?",
                    description=None,
                    dialog=[{
                        "var": "chosen_query",
                        "prompt": str(_q).strip(),
                        "schema": {"kind": "choice", "choices": _opts_clean},
                    }],
                    fmt="auto",
                    on_complete={
                        "type": "restart_turn_with_chosen_query",
                        "original_query": user_query_for_run or "",
                        "options": _opts_clean,
                        "conversation_id": getattr(log, "conversation_id", "") or "",
                    },
                    actor=actor or "host",
                    channel=channel or "",
                )
            except Exception as _ex:
                import logging as _logging
                _logging.getLogger(__name__).warning(
                    "disambig orchestrate failed: %s", _ex)
                _resp = {"ok": False,
                         "error": f"invoke_get_inputs_internal failed: {_ex}"}
            # Setta `step.result` con la observation del get_inputs cosi'
            # `_collect_expandable_caps` (Pass 1) propaga automaticamente
            # `expandable_caps` al log. Stessa shape del get_inputs executor.
            step.result = {
                "ok": bool(_resp.get("ok")) if isinstance(_resp, dict) else False,
                "decision": "disambiguation_required",
                "question": str(_q).strip(),
                "options": _opts_clean,
                "expandable_caps": (
                    _resp.get("expandable_caps")
                    if isinstance(_resp, dict) else None
                ) or [],
            }
            log.steps.append(step)
            log.final_kind = "answer"
            _fmh = _resp.get("final_message_hint") if isinstance(_resp, dict) else None
            log.final_message = _fmh or "\n".join(
                [str(_q).strip()] + [f"{i+1}. {o}" for i, o in enumerate(_opts_clean)]
            )
            log.ts_end = time.time(); log.write(); return log

        # ADR 0133 Strategia 3: post-decode semantic validation per grammar
        # mode. Grammar GBNF garantisce sintassi (JSON ben formato + name in
        # enum), NON semantica (args possono non rispettare schema, es.
        # required missing, type mismatch, enum non in list). Se validazione
        # fail: NON eseguiamo l'executor (perderemmo tempo subprocess);
        # iniettiamo error nel history_for_llm cosi' il prossimo step LLM
        # vede il messaggio e corregge. Determinismo §7.9.
        if os.environ.get("METNOS_GRAMMAR", "0") == "1":
            try:
                from tool_grammar import validate_tool_call as _vtc
                _ok, _err = _vtc(
                    {"name": chosen_name, "arguments": raw_args},
                    tools_for_step,
                    allow_final_answer=(step_num >= 2),
                    allow_disambiguation=(step_num == 1),
                )
            except Exception as _ex:
                _ok, _err = True, ""  # fail-open: non bloccare se validator buggy
            if not _ok:
                step.error = f"grammar_post_validate: {_err}"
                step.result = {
                    "ok": False,
                    "error": _err,
                    "error_class": "invalid_args",
                    "_grammar_post_validate_failed": True,
                }
                log.steps.append(step)
                history_for_refs.append({
                    "step": step_num, "tool": chosen_name,
                    "args": raw_args, "observation": step.result,
                })
                # History LLM: error visibile al prossimo step → il LLM
                # corregge args. Limite consecutive_blocked previene loop.
                history_for_llm.append({
                    "role": "assistant",
                    "tool_calls": [{
                        "id": tc.call_id, "type": "function",
                        "function": {"name": chosen_name,
                                     "arguments": json.dumps(raw_args)},
                    }],
                })
                history_for_llm.append({
                    "role": "tool", "tool_call_id": tc.call_id,
                    "name": chosen_name,
                    "content": json.dumps({"ok": False, "error": _err,
                                            "error_class": "invalid_args"}),
                })
                consecutive_blocked += 1
                if consecutive_blocked >= LOOP_BREAK_THRESHOLD:
                    log.final_kind = "loop_break"
                    _hint = _loop_break_hint(_intent_object_from_route(route_info))
                    log.final_message = msg("MSG_LOOP_BREAK",
                                              n=consecutive_blocked, hint=_hint)
                    log.ts_end = time.time(); log.write(); return log
                continue
        if progress is not None:
            try:
                # Label canale-agnostic: niente tag HTML qui (Telegram con
                # parse_mode=HTML li renderizza, ma SSE → chat HTML browser e
                # fallback Telegram plain mostrano tag letterali). Il canale
                # decide se applicare formatting nel proprio adapter.
                progress.update_free(f"step {step_num} · {chosen_name}")
                # tool_call strutturato (per chat HTML breadcrumb live).
                # Path = tutti i tool degli step gia' completati + corrente.
                if hasattr(progress, "tool_call"):
                    path_so_far = [
                        s.chosen_tool for s in log.steps if s.chosen_tool
                    ] + [chosen_name]
                    # Previsione step rimanenti (euristica intent-based).
                    predicted_remaining = _predict_remaining_path(
                        intent=(route_info or {}).get("intent"),
                        current_tool=chosen_name,
                    )
                    progress.tool_call(
                        tool=chosen_name, step_num=step_num,
                        path_so_far=path_so_far,
                        args=raw_args if isinstance(raw_args, dict) else {},
                        predicted_remaining=predicted_remaining,
                    )
            except Exception as _e:  # silent swallow (auto-fixed)
                _LOG.warning("silent exception in %s: %s", __name__, _e)
        if verbose:
            print(f"[step {step_num}] tool_call: {chosen_name}({raw_args})")

        # Reject placeholder malformati (graffa singola, pipe, ternario): no loop, errore subito.
        malformed = check_malformed_reference(raw_args)
        if malformed:
            obs = {"ok": False, "_malformed_ref": True, "error": malformed}
            step.result = obs
            step.error = "malformed_reference"
            log.steps.append(step)
            history_for_refs.append({"step": step_num, "tool": chosen_name, "args": raw_args, "observation": obs})
            history_for_llm.append({"role": "assistant", "tool_calls": [{"id": tc.call_id, "type": "function", "function": {"name": chosen_name, "arguments": raw_args}}]})
            history_for_llm.append({"role": "tool", "tool_call_id": tc.call_id, "name": chosen_name, "content": json.dumps(obs, ensure_ascii=False)})
            consecutive_blocked += 1
            if consecutive_blocked >= LOOP_BREAK_THRESHOLD:
                log.final_kind = "loop_break"
                _last_err = (step.result.get("error") if isinstance(step.result, dict) else None) or step.error or "n/a"
                _hint = _loop_break_hint(_intent_object_from_route(route_info))
                log.final_message = msg("MSG_LOOP_BREAK", n=consecutive_blocked, hint=_hint)
                log.ts_end = time.time(); log.write(); return log
            continue

        # Reject inline data passing: liste di dict inline >= soglia devono passare via reference.
        inline_violation = check_inline_data(raw_args)
        if inline_violation:
            obs = {"ok": False, "_inline_rejected": True, "error": inline_violation}
            step.result = obs
            step.error = "inline_data_rejected"
            log.steps.append(step)
            history_for_refs.append({"step": step_num, "tool": chosen_name, "args": raw_args, "observation": obs})
            history_for_llm.append({"role": "assistant", "tool_calls": [{"id": tc.call_id, "type": "function", "function": {"name": chosen_name, "arguments": raw_args}}]})
            history_for_llm.append({"role": "tool", "tool_call_id": tc.call_id, "name": chosen_name, "content": json.dumps(obs, ensure_ascii=False)})
            consecutive_blocked += 1
            if consecutive_blocked >= LOOP_BREAK_THRESHOLD:
                log.final_kind = "loop_break"
                _last_err = (step.result.get("error") if isinstance(step.result, dict) else None) or step.error or "n/a"
                _hint = _loop_break_hint(_intent_object_from_route(route_info))
                log.final_message = msg("MSG_LOOP_BREAK", n=consecutive_blocked, hint=_hint)
                log.ts_end = time.time(); log.write(); return log
            continue

        # Duplicate detection: stessa chiamata identica alla precedente del medesimo tool.
        # NON incrementa same_count (e' un blocco a monte): il modello deve solo formulare la
        # final_answer dai risultati precedenti.
        last_args_for_tool = None
        for _prev in reversed(log.steps):
            if _prev.chosen_tool == chosen_name:
                last_args_for_tool = _prev.raw_args
                break
        # Default: non duplicato. Sara' ricalcolata nel branch sottostante
        # se entriamo nel ramo duplicate-detected (incluso post-frontier).
        _is_still_dup = False
        if last_args_for_tool is not None and last_args_for_tool == raw_args:
            # Fallback FRONTIER per CYCLIC_CALL primo step (16/5/2026).
            # Quando il PLANNER middle ripete il SAME tool+args al secondo
            # tentativo, e' un signal autoritativo di confusione semantica.
            # Tenta una sola volta retry con tier=frontier (Opus 4.7) con
            # hint nel system: "questo tool e' stato gia' tentato, scegli
            # diverso". Filtri:
            #   (a) chosen_name != final_answer / disambig (close, no retry).
            #   (b) step_num <= 3 (cyclic primo step, dopo e' loop diverso).
            #   (c) verbo NON mutating CON ok=True: se mutazione e' avvenuta
            #       non c'e' rollback safe. Mutating con ok=False e' OK:
            #       il fail (target_not_found / tool_inesistente) NON ha
            #       mutato nulla → retry frontier SAFE.
            _verb_dup = (chosen_name or "").split("_", 1)[0]
            _prev_obs_for_retry = None
            for _prev in reversed(log.steps):
                if (_prev.chosen_tool == chosen_name
                        and _prev.raw_args == raw_args):
                    _prev_obs_for_retry = (
                        _prev.result if isinstance(_prev.result, dict)
                        else None
                    )
                    break
            _mutating_succeeded = (
                _verb_dup in _MUTATING_VERBS
                and isinstance(_prev_obs_for_retry, dict)
                and _prev_obs_for_retry.get("ok") is True
            )
            # Skip frontier quando auto_final_on_duplicate (riga ~5086) o
            # auto_final_on_duplicate_fail (riga ~5184) possono chiudere
            # il turno deterministicamente (19/5/2026, project_cyclic_frontier
            # _findings). Casi:
            #  - last_obs.ok == True  → auto_final_on_duplicate chiuderebbe.
            #  - last_obs.ok == False CON error message valido → fail-close.
            # In entrambi i casi il frontier sarebbe SPRECO (latency + costo).
            # Frontier viene chiamato SOLO se nessuno dei 2 path deterministici
            # potrebbe chiudere (last_obs non dict, o ok=false senza error).
            _det_close_possible = False
            if isinstance(_prev_obs_for_retry, dict):
                if _prev_obs_for_retry.get("ok") is True:
                    _det_close_possible = True
                else:
                    _err_for_det = (_prev_obs_for_retry.get("error") or
                                     _prev_obs_for_retry.get("message") or "")
                    if isinstance(_err_for_det, str) and _err_for_det.strip():
                        _det_close_possible = True
            _can_retry_front = (
                not _frontier_loop_retry_done
                and step_num <= 3
                and chosen_name not in ("final_answer",
                                          "request_disambiguation_from_user")
                and not _mutating_succeeded
                and not _det_close_possible
                and os.environ.get("METNOS_PLANNER_TIER_FALLBACK", "1") != "0"
            )
            # Universal opt-out (§7.3): METNOS_DISABLE_FRONTIER=1 blocca anche
            # questo call site (CYCLIC_CALL retry), coerente con il primo
            # path frontier ~riga 5670.
            if _can_retry_front and os.environ.get("METNOS_DISABLE_FRONTIER") == "1":
                _can_retry_front = False
            if _can_retry_front:
                _frontier_loop_retry_done = True
                try:
                    from llm_router import LLMRouter
                    _front_router = LLMRouter()
                    _front_spec = _front_router.tiers.get("frontier") or {}
                    _front_model = _front_spec.get("model")
                    _front_provider = (_front_spec.get("provider") or "").lower()
                    _has_key = True
                    if _front_provider == "anthropic":
                        try:
                            from llm_provider import _read_anthropic_key
                            _has_key = bool(_read_anthropic_key())
                        except Exception:
                            _has_key = False
                    if _front_model and _has_key:
                        # Inietta hint nel system per orientare il frontier:
                        # "il middle ha gia' tentato questo tool, riprova
                        # con un'interpretazione diversa".
                        _retry_hint = (
                            f"\n\nNOTA RUNTIME: al passo {step_num-1} hai gia' "
                            f"emesso `{chosen_name}({json.dumps(raw_args, ensure_ascii=False)[:200]})`. "
                            f"Stai per emetterlo di nuovo identico → DUPLICATE_CALL. "
                            f"Riconsidera la query e scegli un tool DIVERSO oppure "
                            f"componi una pipeline differente. Tool dello stesso oggetto "
                            f"o del dominio adiacente sono probabilmente piu' adatti."
                        )
                        _front_kwargs = {
                            k: v for k, v in _chat_kwargs.items()
                            if k not in {"grammar", "reasoning_budget",
                                          "n_predict", "top_k", "min_p",
                                          "tfs_z", "typical_p"}
                        }
                        if verbose:
                            print(f"[step {step_num}] cyclic detected, "
                                  f"fallback frontier ({_front_provider}/{_front_model})")
                        # Telemetria
                        try:
                            import config as _C  # §7.11 (lazy)
                            _stats_p = _C.PATH_USER_DATA / "frontier_fallback_stats.json"
                            _stats_p.parent.mkdir(parents=True, exist_ok=True)
                            _d = {}
                            if _stats_p.exists():
                                try: _d = json.loads(_stats_p.read_text())
                                except: _d = {}
                            _d["cyclic_invoked"] = int(_d.get("cyclic_invoked", 0)) + 1
                            _stats_p.write_text(json.dumps(_d, indent=2))
                        except Exception:
                            pass
                        _r_front = _front_router.chat_with_tools(
                            planner_system + _retry_hint,
                            user_query_for_run, tools_for_step,
                            tier="frontier", history=history_for_llm,
                            **_front_kwargs,
                        )
                        if _r_front.tool_calls:
                            _front_tc = _r_front.tool_calls[-1]
                            _front_name = _front_tc.name
                            _front_args = (_front_tc.arguments
                                            if isinstance(_front_tc.arguments, dict)
                                            else {})
                            # Accetta solo se DIVERSO dal duplicato
                            # (name diverso OPPURE args diversi).
                            if (_front_name != chosen_name
                                    or _front_args != raw_args):
                                chosen_name = _front_name
                                raw_args = _front_args
                                step.chosen_tool = chosen_name
                                step.raw_args = raw_args
                                try:
                                    _d["cyclic_resolved"] = int(_d.get("cyclic_resolved", 0)) + 1
                                    _stats_p.write_text(json.dumps(_d, indent=2))
                                except Exception:
                                    pass
                                if verbose:
                                    print(f"[step {step_num}] cyclic resolved by frontier → {chosen_name}")
                                # Salta il branch duplicate: il nuovo
                                # chosen_name non e' duplicato. Ricontrolla
                                # last_args_for_tool con il NUOVO chosen.
                                last_args_for_tool = None
                                for _prev in reversed(log.steps):
                                    if _prev.chosen_tool == chosen_name:
                                        last_args_for_tool = _prev.raw_args
                                        break
                                # Se anche il nuovo e' duplicato, fall-through
                                # al branch normale (auto_final).
                                if (last_args_for_tool is None
                                        or last_args_for_tool != raw_args):
                                    # Skip il branch duplicate, esegui normale.
                                    pass
                                else:
                                    # Frontier ha proposto un altro duplicato.
                                    # Lascia procedere il branch originale.
                                    pass
                except Exception as _ex:
                    import logging as _logging
                    _logging.getLogger(__name__).warning(
                        "cyclic frontier fallback failed: %s", _ex)
            # Se il retry frontier ha sostituito chosen_name e ora NON e'
            # piu' duplicato → salta il blocco duplicate.
            _is_still_dup = (last_args_for_tool is not None
                              and last_args_for_tool == raw_args)
        # Se _is_still_dup e' False (frontier ha risolto), salta il blocco
        # duplicate completo e procedi all'invoke normale.
        if _is_still_dup:
            # Auto-final-on-duplicate: se l'ultima call con questi args ha
            # avuto successo, il modello sta solo cercando rassicurazione.
            # Chiudi il turno con un final_answer derivato dall'ULTIMO step
            # productive del turno (qualsiasi tool, non solo il duplicato),
            # cosi' pipeline come read→describe→send chiudono dichiarando
            # send (azione utente-significativa) e non read (data fetch).
            # Skip step di pure data-piping (scratchpad_read, classify, filter)
            # se ci sono action verb piu' recenti.
            last_obs_for_dup = None
            for _prev in reversed(log.steps):
                if _prev.chosen_tool == chosen_name and _prev.raw_args == raw_args:
                    last_obs_for_dup = _prev.result
                    break
            if isinstance(last_obs_for_dup, dict) and last_obs_for_dup.get("ok"):
                # Cerca l'ultimo step productive del turno (preferendo action
                # verbs su producer): scorre dalla fine, prende il primo tool
                # con ok:true che NON sia un mere data-piping helper o un
                # narrator LLM (describe_entries). Logica estratta a livello
                # modulo per testabilita' (cf. _resolve_auto_final_from_steps).
                # 19/5/2026 fix #12: se il duplicate trigger e' describe_entries
                # stesso, preferisci la prosa del describer (precedente call)
                # invece di walk-back-skip-describe verso il producer raw.
                # Razionale: producer non ha `summary`/`final_message_hint` LLM,
                # describer si'. Output "Esito gia' nei risultati precedenti"
                # vs prosa Gemma: la seconda e' user-facing utile.
                if chosen_name == "describe_entries":
                    lp_tool = "describe_entries"
                    lp_obs = last_obs_for_dup
                else:
                    lp_tool, lp_obs = _resolve_auto_final_from_steps(log.steps)
                    if lp_tool is None:
                        lp_tool = chosen_name
                        lp_obs = last_obs_for_dup if isinstance(last_obs_for_dup, dict) else {}
                step.error = "auto_final_on_duplicate"
                log.steps.append(step)
                ok_count, n_above_threshold = _extract_auto_final_count(lp_obs)
                # Quattro fonti per il messaggio finale auto, in ordine di
                # precedenza:
                # 0. `detail_md`: blocco multi-riga gia' renderizzato
                #    dall'executor (executor producer ricchi). Quando c'e',
                #    e' la fonte autorevole — niente boilerplate aggiuntivo.
                # 1. `summary`: 1-2 righe pronto-uso (executor cooperativi).
                # 2. `results`: lista di dict trasformativi (move/write/send).
                # 3. `entries`: lista di dict producer (find/get/list) con
                #    campi identificativi.
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
                    # Cap a 1500 caratteri per evitare messaggi troppo lunghi
                    # su Telegram. Un blocco serio ha tipicamente 200-800.
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
                log.final_kind = "answer"
                # Quando il count e' ignoto E abbiamo un 'message' descrittivo
                # autorevole (verb-unique / azione singola), non aggiungere
                # rumore "(? elementi)" — usa direttamente il message.
                # 15/5/2026: detail_md autoritativo → usalo come final PURO
                # senza wrap "{tool}: completato (?)" rumoroso. Pattern:
                # executor producer (read_tasks_history, lifecycle_summary)
                # popolano detail_md con markdown ricco, finale display-ready.
                if explicit_detail_md and isinstance(explicit_detail_md, str) and explicit_detail_md.strip():
                    log.final_message = explicit_detail_md.strip()[:1500]
                elif ok_count is None and explicit_message and isinstance(explicit_message, str):
                    log.final_message = f"{lp_tool}: {explicit_message.strip()}"
                else:
                    count_str = _format_auto_final_count(ok_count, n_above_threshold)
                    log.final_message = msg(
                        "MSG_AUTO_FINAL_COMPLETED",
                        tool=lp_tool, count_str=count_str,
                        detail=(detail if detail else msg("MSG_AUTO_FINAL_NO_DETAIL")),
                    )
                log.ts_end = time.time(); log.write(); return log
            # Duplicate call ma ok=False al primo tentativo: l'errore e'
            # gia' definitivo (target_not_found, missing_credentials, etc.).
            # Riprovare e' anti-pattern §2.8 (no silent failure: il fail
            # autorevole va trasformato in final user-facing onesto).
            # Bug live 15/5/2026 turn "cancella task test_inesistente":
            # list_tasks ok → delete_tasks ok=False (non trovato) →
            # LLM riprova 3× delete_tasks identico → loop_break generico.
            # Fix: chiudi turno con error del primo step come final.
            if isinstance(last_obs_for_dup, dict) and not last_obs_for_dup.get("ok"):
                _err_msg = (last_obs_for_dup.get("error") or
                              last_obs_for_dup.get("message") or "")
                if isinstance(_err_msg, str) and _err_msg.strip():
                    step.error = "auto_final_on_duplicate_fail"
                    log.steps.append(step)
                    log.final_kind = "answer"
                    log.final_message = f"{chosen_name}: {_err_msg.strip()}"
                    log.ts_end = time.time(); log.write(); return log
            obs = {
                "ok": False,
                "_duplicate": True,
                "error": (
                    f"DUPLICATE_CALL: hai gia' chiamato '{chosen_name}' con questi stessi args "
                    "al passo precedente. Il risultato sara' identico. FORMULA LA FINAL_ANSWER "
                    "usando i risultati gia' ottenuti, non chiamare di nuovo questo tool."
                ),
            }
            step.result = obs
            step.error = "duplicate_call_blocked"
            log.steps.append(step)
            history_for_refs.append({"step": step_num, "tool": chosen_name, "args": raw_args, "observation": obs})
            history_for_llm.append({"role": "assistant", "tool_calls": [{"id": tc.call_id, "type": "function", "function": {"name": chosen_name, "arguments": raw_args}}]})
            history_for_llm.append({"role": "tool", "tool_call_id": tc.call_id, "name": chosen_name, "content": json.dumps(obs, ensure_ascii=False)})
            consecutive_blocked += 1
            if consecutive_blocked >= LOOP_BREAK_THRESHOLD:
                log.final_kind = "loop_break"
                _last_err = (step.result.get("error") if isinstance(step.result, dict) else None) or step.error or "n/a"
                _hint = _loop_break_hint(_intent_object_from_route(route_info))
                log.final_message = msg("MSG_LOOP_BREAK", n=consecutive_blocked, hint=_hint)
                log.ts_end = time.time(); log.write(); return log
            continue

        # Vectorial violation guard (ADR 0130, 12/5/2026).
        # Bug live turn `8f8080c0`: find_events_empty x9 consecutivi su args
        # `time_windows` che variavano (DUPLICATE_CALL non scattava). §2.1
        # vettoriale: chi accetta args plurali (paths/urls/time_windows/...)
        # va chiamato UNA volta con N args, non N volte. Detection §7.9
        # introspettiva sul `args_schema.properties` (zero whitelist §7.3).
        # Posizione: post-DUPLICATE (args diff), pre-cap_same (intercetta
        # PRIMA di consumare budget) e pre-invoke (zero cost, no work).
        _executor_for_guard = catalog.get(chosen_name)
        if (
            _executor_for_guard is not None
            and log.steps
            and log.steps[-1].chosen_tool == chosen_name
            and isinstance(log.steps[-1].result, dict)
            and log.steps[-1].result.get("ok") is True
        ):
            _sig = _vectorial_schema_signature(_executor_for_guard.args_schema)
            if _executor_has_plural_args(chosen_name, _sig):
                obs = {
                    "ok": False,
                    "_anti_vectorial": True,
                    "error": (
                        f"VECTORIAL_VIOLATION: hai gia' chiamato '{chosen_name}' "
                        f"al passo precedente con esito ok=True. Questo executor "
                        f"accetta args plurali (§2.1 vettoriale): se hai bisogno "
                        f"di MULTIPLE finestre/path/id/url, passali TUTTI in UNA "
                        f"sola call come lista. NON chiamarlo di nuovo. Formula "
                        f"final_answer dai risultati gia' ottenuti, oppure procedi "
                        f"al next step della pipeline."
                    ),
                }
                step.result = obs
                step.error = "anti_vectorial_blocked"
                log.steps.append(step)
                history_for_refs.append({"step": step_num, "tool": chosen_name, "args": raw_args, "observation": obs})
                history_for_llm.append({"role": "assistant", "tool_calls": [{"id": tc.call_id, "type": "function", "function": {"name": chosen_name, "arguments": raw_args}}]})
                history_for_llm.append({"role": "tool", "tool_call_id": tc.call_id, "name": chosen_name, "content": json.dumps(obs, ensure_ascii=False)})
                consecutive_blocked += 1
                if consecutive_blocked >= LOOP_BREAK_THRESHOLD:
                    log.final_kind = "loop_break"
                    _hint = _loop_break_hint(_intent_object_from_route(route_info))
                    log.final_message = msg("MSG_LOOP_BREAK", n=consecutive_blocked, hint=_hint)
                    log.ts_end = time.time(); log.write(); return log
                continue

        # Cap chiamate stesso tool: 10 default per qualsiasi tool. Soglia generosa
        # per permettere iterazioni legittime (universal helpers su args
        # diversi, producer su windows/account differenti). Loop reali
        # vengono comunque catturati prima da duplicate_call_blocked.
        # ADR 0130: cap_same per executor vettoriali abbassato a 2 (vs 10
        # default) via `_cap_same_for_executor`: chiamare un vettoriale piu'
        # di 2 volte e' anti-pattern §2.1 anche con args diversi.
        same_count[chosen_name] += 1
        _cap_same_effective = _cap_same_for_executor(_executor_for_guard, cap_same)
        if same_count[chosen_name] > _cap_same_effective:
            step.error = f"cap_same_executor superato per {chosen_name}"
            log.steps.append(step)
            log.final_kind = "cap_same_executor"
            # Final user-facing §7.3: prima cerca un last_productive ok=True
            # per restituire l'esito parziale (es. find_images_indices ha
            # trovato 0 risultati), poi appende il notice MSG_CAP_SAME_EXECUTOR
            # i18n. Senza last_productive utile, il notice da solo basta.
            # Il messaggio criptico "(stop: ... chiamato N volte)" e' stato
            # rimosso: era opaco per il judge LLM e per l'utente finale.
            _cap_lp = ""
            for _ps in reversed(log.steps[:-1]):  # skip step appena loggato
                _po = _ps.result if isinstance(_ps.result, dict) else None
                if _po and _po.get("ok") is True:
                    try:
                        _fm, _, _ = _compose_final_message_from_obs(
                            _ps.chosen_tool or "", _po)
                        if _fm and _fm.strip():
                            _cap_lp = _fm.strip()
                            break
                    except Exception:
                        pass
            try:
                _cap_notice = msg("MSG_CAP_SAME_EXECUTOR",
                                  tool=chosen_name,
                                  n=_cap_same_effective)
            except Exception:
                _cap_notice = (
                    f"Ho provato `{chosen_name}` "
                    f"{_cap_same_effective} volte senza nuovo risultato."
                )
            log.final_message = (
                f"{_cap_lp}\n\n{_cap_notice}".strip()
                if _cap_lp else _cap_notice
            )
            log.ts_end = time.time(); log.write(); return log

        # Cap_max_per_turn (8/5/2026 notte, the design guide §4.4 estesa).
        # Trigger live: turn 9-step thrashing «cerca organico scuola Roma»
        # con find_texts ×7. cap_same a 10 e duplicate_call non bastano:
        # gli args cambiavano leggermente ogni call (topic ridotto progress.).
        # Soglia: stesso tool non-action chiamato >= DEFAULT_CAP_MAX_PER_TURN
        # volte nel turno con args near-identical (Jaccard token-set > 0.7
        # sui campi semantici topic/query/pattern) → forza final_answer
        # con `_compose_final_message_from_obs(last_productive)`.
        # I verbi action sono protetti dalle guardie a monte (vaglio,
        # cyclic-call, duplicate); qui solo non-action verbs.
        if _is_non_action_tool(chosen_name):
            _cur_norm = _normalize_args_for_dup(raw_args)
            _near_count = 1  # questa chiamata
            for _prev in log.steps:
                if _prev.chosen_tool != chosen_name:
                    continue
                _prev_norm = _normalize_args_for_dup(_prev.raw_args)
                if _args_jaccard(_cur_norm, _prev_norm) >= 0.7:
                    _near_count += 1
            if _near_count >= DEFAULT_CAP_MAX_PER_TURN:
                lp_tool, lp_obs = _resolve_auto_final_from_steps(log.steps)
                if lp_tool is None:
                    lp_tool = chosen_name
                    lp_obs = {}
                step.error = "cap_max_per_turn"
                step.loop_break_total = "max_calls_per_turn"
                log.steps.append(step)
                final_msg_str, _, _ = _compose_final_message_from_obs(lp_tool, lp_obs)
                log.final_kind = "answer"
                log.final_message = final_msg_str
                log.ts_end = time.time(); log.write(); return log

        # Cyclic-call guard (3/5/2026, ADR informale «evita doppia
        # esecuzione inutile, sempre»). Pattern A → X → A con A in
        # `_DESTRUCTIVE_VERBS` indica che il PLANNER sta richiamando un
        # executor distruttivo dopo un'interruzione: artefatto di
        # ragionamento, non lavoro genuino. Blocchiamo qui, senza eseguire.
        try:
            from vocab import DESTRUCTIVE_VERBS as _DV
        except Exception:  # pragma: no cover
            _DV = frozenset({"write", "move", "delete", "send", "extract", "create"})
        _verb_of = chosen_name.split("_", 1)[0] if "_" in chosen_name else chosen_name
        if (
            _verb_of in _DV
            and len(log.steps) >= 2
            and log.steps[-2].chosen_tool == chosen_name
        ):
            obs = {
                "ok": False,
                "_cyclic": True,
                "error": (
                    f"CYCLIC_CALL: hai gia' chiamato '{chosen_name}' due passi "
                    f"fa, con un altro tool nel mezzo. Pattern A → X → A su "
                    f"verbo destructive ('{_verb_of}'): la seconda chiamata "
                    f"non aggiunge lavoro utile. Vai a final_answer ora."
                ),
            }
            step.result = obs
            step.error = "cyclic_call_blocked"
            log.steps.append(step)
            history_for_refs.append({"step": step_num, "tool": chosen_name, "args": raw_args, "observation": obs})
            history_for_llm.append({"role": "assistant", "tool_calls": [{"id": tc.call_id, "type": "function", "function": {"name": chosen_name, "arguments": raw_args}}]})
            history_for_llm.append({"role": "tool", "tool_call_id": tc.call_id, "name": chosen_name, "content": json.dumps(obs, ensure_ascii=False)})
            consecutive_blocked += 1
            if consecutive_blocked >= LOOP_BREAK_THRESHOLD:
                log.final_kind = "loop_break"
                _hint = _loop_break_hint(_intent_object_from_route(route_info))
                log.final_message = msg(
                    "MSG_LOOP_BREAK", n=consecutive_blocked, hint=_hint,
                )
                log.ts_end = time.time(); log.write(); return log
            continue

        # Resolve from_step shortcut → arg consumer injection (refactor F1, 29/4;
        # Layer 4 consumer-arg match aggiunto 5/5/2026 per Bug A turn 23be1548:
        # find_urls produce entries=[{url,...}], read_urls_html consuma `urls`
        # (NON `entries`). Senza schema il fallback inietta sotto `entries` e il
        # validate_args fallisce. Con schema: estrae entries[*].url → urls=[...]).
        _consumer_executor = catalog.get(chosen_name)
        _consumer_schema = _consumer_executor.args_schema if _consumer_executor else None
        # 8/5/2026: pre-pass per espandere `from_step:N` annidati in liste
        # args (es. paths_filter: ["from_step:2"]). Bug live: il PLANNER
        # passa la stringa `from_step:2` letterale come elemento di lista,
        # senza questa espansione finisce a paths_filter literal → match
        # vuoto → 0 entries. Pre-pass idempotente.
        raw_args_pre, nfs_errors = _expand_nested_from_step(raw_args, history_for_refs)
        args_after_from_step, fs_errors = resolve_from_step(
            raw_args_pre, history_for_refs, consumer_schema=_consumer_schema,
        )
        fs_errors = list(nfs_errors) + list(fs_errors)
        if fs_errors:
            obs = {"ok": False, "error": "from_step: " + "; ".join(fs_errors)}
            step.result = obs
            log.steps.append(step)
            history_for_refs.append({"step": step_num, "tool": chosen_name, "args": raw_args, "observation": obs})
            history_for_llm.append({"role": "assistant", "tool_calls": [{"id": tc.call_id, "type": "function", "function": {"name": chosen_name, "arguments": raw_args}}]})
            history_for_llm.append({"role": "tool", "tool_call_id": tc.call_id, "name": chosen_name, "content": json.dumps(obs, ensure_ascii=False)})
            consecutive_blocked += 1
            if consecutive_blocked >= LOOP_BREAK_THRESHOLD:
                log.final_kind = "loop_break"
                _last_err = (step.result.get("error") if isinstance(step.result, dict) else None) or step.error or "n/a"
                _hint = _loop_break_hint(_intent_object_from_route(route_info))
                log.final_message = msg("MSG_LOOP_BREAK", n=consecutive_blocked, hint=_hint)
                log.ts_end = time.time(); log.write(); return log
            if not is_multistep:
                log.final_kind = "error"; log.final_message = f"(from_step: {fs_errors})"
                log.ts_end = time.time(); log.write(); return log
            continue

        # Resolve references (sintassi {{stepN.field}}, retro-compat)
        args, ref_errors = resolve_references(args_after_from_step, history_for_refs)
        step.resolved_args = args
        if ref_errors:
            obs = {"ok": False, "error": "references: " + "; ".join(ref_errors)}
            step.result = obs
            log.steps.append(step)
            history_for_refs.append({"step": step_num, "tool": chosen_name, "args": raw_args, "observation": obs})
            history_for_llm.append({"role": "assistant", "tool_calls": [{"id": tc.call_id, "type": "function", "function": {"name": chosen_name, "arguments": raw_args}}]})
            history_for_llm.append({"role": "tool", "tool_call_id": tc.call_id, "name": chosen_name, "content": json.dumps(obs, ensure_ascii=False)})
            if not is_multistep:
                log.final_kind = "error"; log.final_message = f"(errore reference: {ref_errors})"
                log.ts_end = time.time(); log.write(); return log
            continue

        # Caso speciale: request_location_from_user e' builtin UX (regola §2-quater).
        # Salva pending state, invoca channel adapter coi bottoni, termina turno
        # silenziosamente. Daemon rilancia il turno quando l'utente risponde.
        if chosen_name == "request_location_from_user":
            chat_id_for_prompt = getattr(progress, "chat_id", None) if progress else None
            req_meta = _location_request.request(
                turn_id=turn_id,
                actor=actor,
                channel=channel or "cli",
                original_query=user_query_for_run,
                goal=args.get("goal", "rispondere alla tua richiesta"),
                chat_id=chat_id_for_prompt,
            )
            obs = dict(req_meta)
            obs["awaiting"] = True
            obs["suppress_final"] = True
            step.result = obs
            log.steps.append(step)
            history_for_refs.append({"step": step_num, "tool": chosen_name, "args": args, "observation": obs})
            log.pending_location = req_meta  # passato al daemon per render UI
            log.final_kind = "awaiting"
            log.final_message = ""
            log.ts_end = time.time()
            log.write()
            return log

        # Casi speciali builtin per scheduling ricorrente (1/5/2026 sera).
        # I 3 tool sono callable dal PLANNER per registrare/elencare/cancellare
        # task ricorrenti che il scheduler builtin esegue al fire automatico.
        if chosen_name in ("create_tasks", "list_tasks",
                             "delete_tasks", "read_tasks",
                             "set_tasks", "read_tasks_history"):
            _handler = {
                "create_tasks": handle_create_tasks,
                "list_tasks": handle_list_tasks,
                "delete_tasks": handle_delete_tasks,
                "read_tasks": handle_read_tasks,
                "set_tasks": handle_set_tasks,
                "read_tasks_history": handle_read_tasks_history,
            }[chosen_name]
            _cid = getattr(progress, "chat_id", None) if progress else None
            obs = _handler(args, actor=actor, channel=channel, chat_id=_cid)
            step.result = obs
            log.steps.append(step)
            history_for_refs.append({"step": step_num, "tool": chosen_name, "args": args, "observation": obs})
            history_for_llm.append({"role": "assistant", "tool_calls": [{"id": tc.call_id, "type": "function", "function": {"name": chosen_name, "arguments": raw_args}}]})
            obs_str = _trim_obs_for_history(obs)
            history_for_llm.append({"role": "tool", "tool_call_id": tc.call_id, "name": chosen_name, "content": obs_str})
            if not is_multistep:
                log.final_kind = "answer" if obs.get("ok") else "error"
                log.final_message = obs.get("message") or json.dumps(obs, ensure_ascii=False)
                log.ts_end = time.time(); log.write(); return log
            continue

        # Caso speciale: request_new_executor e' builtin (telos di non-rinuncia).
        # Lancia synt multistage sincrono (~150 s wall) e ritorna esito al LLM.
        if chosen_name == "request_new_executor":
            # B.5 soft-gate (#10 prompt PLANNER re-eng, 19/5/2026 v4):
            # se il top-K ha un candidate con jaccard affinity vs query >= 0.3,
            # rifiuta synth e forza il PLANNER a usare il candidate esistente.
            # Razionale: bug live 11/5 «appuntamenti domani» → request_new_executor
            # (read_events posizione 3 ignorato). request_new_executor DEVE
            # essere last-resort.
            _jaccard_gate = _check_top_k_affinity_jaccard(
                user_query_for_run, candidates, threshold=0.3,
            )
            if _jaccard_gate is not None:
                # Soft-reject: ritorna osservazione che invita a riusare top-K.
                obs = {
                    "ok": False,
                    "error_code": "ERR_OP_FAILED",
                    "error": msg("ERR_OP_FAILED",
                                  reason=f"request_new_executor rejected: "
                                         f"candidate '{_jaccard_gate[0]}' "
                                         f"copre la query (jaccard {_jaccard_gate[1]:.2f}). "
                                         f"Riusalo invece di sintetizzare."),
                    "rejected_synth": True,
                    "suggested_tool": _jaccard_gate[0],
                    "jaccard_score": _jaccard_gate[1],
                }
                step.result = obs
                step.error = "synth_request_blocked_by_jaccard_gate"
                log.steps.append(step)
                history_for_refs.append({"step": step_num, "tool": chosen_name,
                                          "args": args, "observation": obs})
                history_for_llm.append({"role": "assistant", "tool_calls": [
                    {"id": tc.call_id, "type": "function",
                     "function": {"name": chosen_name, "arguments": raw_args}}]})
                history_for_llm.append({"role": "tool", "tool_call_id": tc.call_id,
                                         "name": chosen_name,
                                         "content": _trim_obs_for_history(obs)})
                continue
            # ADR 0122: passa gli step gia' eseguiti del turno corrente
            # cosi' synth_request puo' calcolare il path_shape_hash e
            # arricchire la proposta con i campi path_eta_*/call_count.
            obs = handle_synth_request(args, user_query=user_query_for_run, progress=progress, verbose=verbose, current_steps=list(log.steps))
            step.result = obs
            log.steps.append(step)
            history_for_refs.append({"step": step_num, "tool": chosen_name, "args": args, "observation": obs})
            history_for_llm.append({"role": "assistant", "tool_calls": [{"id": tc.call_id, "type": "function", "function": {"name": chosen_name, "arguments": raw_args}}]})
            obs_str = _trim_obs_for_history(obs)
            history_for_llm.append({"role": "tool", "tool_call_id": tc.call_id, "name": chosen_name, "content": obs_str})

            # Reload catalog se synthesis ha installato un nuovo executor:
            # cosi' il LLM al passo successivo lo vede e puo' chiamarlo
            # direttamente per chiudere il task originale.
            if obs.get("installed") and obs.get("proposed_name"):
                try:
                    new_catalog = filter_for_visibility(load_catalog(), VISIBILITY_COMPOSER)
                    new_ex = new_catalog.executors.get(obs["proposed_name"])
                    if new_ex is not None:
                        # aggiungi il nuovo executor in coda ai candidates,
                        # cosi' base_tools viene rinfrescato al prossimo iter.
                        if new_ex.name not in {e.name for e in candidates}:
                            candidates.append(new_ex)
                        catalog = new_catalog
                        base_tools = render_tools_for_provider(candidates)
                        if verbose:
                            print(f"[catalog] reloaded, {obs['proposed_name']} now visible to planner")
                except Exception as ex:
                    if verbose:
                        print(f"[catalog] reload failed: {ex}")

            if not is_multistep:
                log.final_kind = "answer" if obs.get("ok") else "error"
                log.final_message = obs.get("message") or json.dumps(obs, ensure_ascii=False)
                log.ts_end = time.time(); log.write(); return log
            continue

        # Caso speciale: describe_entries e' builtin LLM-augmented.
        # Loop+ragionamento dentro l'executor, niente subprocess.
        if chosen_name == "describe_entries":
            # ADR 0111 (7/5/2026): Level 2 — inietta `health_context` se
            # lo step sorgente (raw_args.from_step) ha un campo `health`
            # non vuoto. Senza questo, describe_entries vede solo le
            # entries (processi) e dichiarerebbe "non disponibile" sui
            # dati salute, contraddicendo il blocco che il runtime
            # prependera' al final_message.
            try:
                _fs = raw_args.get("from_step") if isinstance(raw_args, dict) else None
                if isinstance(_fs, str) and _fs.isdigit():
                    _fs = int(_fs)
                if (isinstance(_fs, int)
                        and 1 <= _fs <= len(history_for_refs)):
                    _src_obs = history_for_refs[_fs - 1].get("observation", {})
                    _h = _src_obs.get("health") if isinstance(_src_obs, dict) else None
                    if isinstance(_h, dict) and _h and "health_context" not in args:
                        args = dict(args)
                        args["health_context"] = _h
            except (KeyError, IndexError, TypeError):
                pass
            # 20/5/2026: l'interceptor che dirottava describe_entries(find_urls)
            # verso lista link (originato 10/5 come fix UX) e' stato rimosso.
            # Razionale (lang-independent §7.3): la scelta del verbo e' del
            # planner, che decide gia' via intent_extractor + vocab.py se
            # l'utente vuole sintesi (describe_entries) o lista (find_urls
            # senza describe). Il runtime non deve sovrascrivere quella scelta.
            # ADR 0153 auto-remediation needs_content_fetch -> read_urls_html
            # garantisce che describe produca sintesi reale anche su entries
            # url+title+snippet. _append_search_results_if_any aggiunge i link
            # come fonti DOPO l'abstract, ristretti agli URL effettivamente
            # processati da read_urls_html (body_text >= 100 char).
            # Guard anti-costo run schedulati (12/6/2026): issue già
            # trattate in issue_qa escluse PRIMA del costo LLM/frontier.
            from treated_issues_guard import (
                filter_treated_issue_entries as _ti_filter,
                annotate_skipped_known as _ti_annotate)
            args, _ti_info = _ti_filter(chosen_name, args)
            obs = handle_describe_entries(args, verbose=verbose)
            _ti_annotate(obs, _ti_info)
            # ADR 0153 (20/5/2026 v6): auto-remediation generalizzata.
            # Se l'observation ha error_class noto al registry
            # `auto_remediation.REMEDIATIONS`, il runtime invoca il
            # prereq, ricalcola gli args, retry. Stesso codice per
            # describe_entries, classify_entries, futuri executor.
            _remed = _maybe_remediate_obs(
                obs, args, chosen_name,
                catalog=catalog, turn_id=turn_id,
                actor=actor, channel=channel,
                verbose=verbose,
            )
            if _remed is not None:
                _rh_step, _rh_obs, obs = _remed
                log.steps.append(_rh_step)
                history_for_refs.append({
                    "step": step_num, "tool": _rh_step.chosen_tool,
                    "args": _rh_step.raw_args,
                    "observation": _rh_obs,
                })
            step.result = obs
            log.steps.append(step)
            history_for_refs.append({"step": step_num, "tool": chosen_name, "args": args, "observation": obs})
            history_for_llm.append({"role": "assistant", "tool_calls": [{"id": tc.call_id, "type": "function", "function": {"name": chosen_name, "arguments": raw_args}}]})
            obs_str = _trim_obs_for_history(obs)
            history_for_llm.append({"role": "tool", "tool_call_id": tc.call_id, "name": chosen_name, "content": obs_str})
            if not is_multistep:
                log.final_kind = "answer" if obs.get("ok") else "error"
                log.final_message = obs.get("summary") or obs.get("error") or json.dumps(obs, ensure_ascii=False)
                log.ts_end = time.time(); log.write(); return log
            continue

        # Caso speciale: classify_entries e' builtin LLM-augmented.
        # Arricchisce ogni entry con un campo `<dimension>` etichettato; non
        # partiziona — la partizione si fa con filter_entries downstream.
        if chosen_name == "classify_entries":
            # Guard anti-costo run schedulati (12/6/2026): vedi
            # runtime/treated_issues_guard.py (deterministico §7.9).
            from treated_issues_guard import (
                filter_treated_issue_entries as _ti_filter,
                annotate_skipped_known as _ti_annotate)
            args, _ti_info = _ti_filter(chosen_name, args)
            obs = handle_classify_entries(args, verbose=verbose)
            _ti_annotate(obs, _ti_info)
            step.result = obs
            # Offload a scratchpad per non leakare le entries arricchite.
            obs_for_history = obs
            obs_str = json.dumps(obs, ensure_ascii=False)
            # Offload a scratchpad SOLO per liste grandi: sotto la soglia
            # il modello vede le entries inline nell'observation, evitando
            # di vedere solo l'handle (con il rischio di "fabbricare" output).
            # Soglia 20: tipica top-K query (top 5/10) sta sotto, niente
            # offload, modello vede i veri dati. Liste lunghe vanno in scratchpad.
            _ent = obs.get("entries")
            has_structured_list = isinstance(_ent, list) and len(_ent) > 20
            if obs.get("ok") and (has_structured_list or len(obs_str) > scratchpad_threshold):
                obs_for_history = sp.put(turn_id, step_num, chosen_name, obs)
            log.steps.append(step)
            history_for_refs.append({"step": step_num, "tool": chosen_name, "args": args, "observation": obs})
            history_for_llm.append({"role": "assistant", "tool_calls": [{"id": tc.call_id, "type": "function", "function": {"name": chosen_name, "arguments": raw_args}}]})
            obs_str_h = _trim_obs_for_history(obs_for_history)
            history_for_llm.append({"role": "tool", "tool_call_id": tc.call_id, "name": chosen_name, "content": obs_str_h})
            if not is_multistep:
                log.final_kind = "answer" if obs.get("ok") else "error"
                if obs.get("ok"):
                    counts = obs.get("counts") or {}
                    log.final_message = f"Classificato: {counts}"
                else:
                    log.final_message = obs.get("error") or json.dumps(obs, ensure_ascii=False)
                log.ts_end = time.time(); log.write(); return log
            continue

        # Caso speciale: admin (ADR 0088) e' verb-unique builtin VISIBILE al
        # PLANNER. Va instradato via VERB_UNIQUE_REGISTRY (vaglio always-on),
        # non via subprocess executor. Il sudoer resta invisibile.
        if chosen_name == "admin":
            from loader import invoke_verb_unique
            try:
                obs = invoke_verb_unique(
                    "admin", caller="agent_runtime",
                    intent=args.get("intent", ""),
                    command_proposed=args.get("command_proposed", ""),
                    credentials_domain=args.get("credentials_domain"),
                    actor_consent_token=args.get("actor_consent_token"),
                    actor=actor,
                )
            except (PermissionError, KeyError, RuntimeError) as e:
                obs = {"ok": False, "error": f"admin invoke failed: {e}"}
            step.result = obs
            log.steps.append(step)
            history_for_refs.append({"step": step_num, "tool": chosen_name, "args": args, "observation": obs})
            history_for_llm.append({"role": "assistant", "tool_calls": [{"id": tc.call_id, "type": "function", "function": {"name": chosen_name, "arguments": raw_args}}]})
            obs_str = _trim_obs_for_history(obs)
            history_for_llm.append({"role": "tool", "tool_call_id": tc.call_id, "name": chosen_name, "content": obs_str})

            # ADR 0091 (5/5/2026): admin ha bisogno di credenziali NON
            # ancora salvate. Auto-orchestriamo `get_inputs(fmt="auto")`:
            # il runtime invoca direttamente l'orchestratore (no PLANNER
            # round-trip) e salva un `dialog_pending` con `on_complete`
            # callback che salvera' le creds e ri-invochera' admin con i
            # suoi args originali. Il turno chiude con una carta UI
            # (form HTTP standalone oppure prima domanda dialogue).
            if isinstance(obs, dict) and obs.get("decision") == "needs_inputs":
                from orchestration import orchestrate_needs_inputs
                sender_for_state = (
                    f"{channel}:{actor}" if channel else (actor or "host")
                )
                gi_result = orchestrate_needs_inputs(
                    obs,
                    sender_id=sender_for_state,
                    actor=actor or "host",
                    channel=channel or None,
                )
                if not gi_result.get("ok"):
                    # Fallback: se l'orchestrazione fallisce (es. payload
                    # malformato, storage non scrivibile), usa la stringa
                    # plain text legacy come fallback diagnostico per non
                    # lasciare l'utente senza risposta.
                    log.final_kind = "error"
                    log.final_message = (
                        "Servono credenziali ma l'orchestrazione "
                        "get_inputs e' fallita: "
                        + (gi_result.get("error") or "errore sconosciuto")
                        + ". Ritenta con `metnos-cli credentials add ...`."
                    )
                    log.ts_end = time.time(); log.write(); return log

                # Successo: chiudi il turno con la carta UX e propaga
                # expandable_caps di tipo `get_inputs_response` cosi' il
                # daemon riconosce il prossimo messaggio utente come
                # risposta al dialogo (e il submit HTTP lo trova via
                # /agent/dialog/<id>/form).
                log.final_kind = "answer"
                log.final_message = (
                    gi_result.get("final_message_hint")
                    or "Servono alcuni input per continuare."
                )
                log.ts_end = time.time()
                log.write()
                # Forza expandable_caps DOPO write() (write() popola la
                # lista da self._collect_expandable_caps che pero' non vede
                # questo dialogo perche' obs viene da admin, non da get_inputs).
                caps = gi_result.get("expandable_caps") or []
                if caps:
                    log.expandable_caps = caps
                return log

            # Se admin ha emesso una carta vaglio: registriamo un'expandable_caps
            # speciale (kind="admin_approval") che il channel daemon riconosce
            # e consuma diversamente dal cap-expand standard. Termina il turno
            # subito con la summary come final_answer.
            if isinstance(obs, dict) and obs.get("approval_required"):
                # Install-on-demand T1 hook (§7.3, 17/5/2026): se lo step
                # PRECEDENTE aveva `binary_missing`, persisti il
                # pending_install_resume con chiave = admin signature.
                # Cosi' quando l'utente clicca SI' sulla card e admin
                # ri-entra in T2 con execute_silent, il post-admin hook
                # (sotto) ri-esegue l'executor originale automaticamente.
                try:
                    if log.steps:
                        _prev = log.steps[-1]
                        _prev_obs = _prev.result if isinstance(_prev.result, dict) else {}
                        _bm_rec = _detect_binary_missing_in_obs(_prev_obs)
                        if _bm_rec and (_prev.chosen_tool or "") != "admin":
                            from install_resume_state import save as _save_resume
                            _save_resume(
                                admin_signature=obs.get("signature", ""),
                                executor=_prev.chosen_tool or "",
                                args_base=dict(_prev.resolved_args or _prev.raw_args or {}),
                                actor=actor or "host",
                                channel=channel or "",
                            )
                except Exception as _e:  # noqa: BLE001
                    import logging as _logging
                    _logging.getLogger(__name__).warning(
                        "install_on_demand T1 save failed: %s", _e)

                approval_proposal = {
                    "kind": "admin_approval",
                    "step_num": step_num,
                    "executor": "admin",
                    "cap_field": "actor_consent_token",
                    "cap_suggested": obs.get("consent_token", ""),
                    "args_original": dict(raw_args or {}),
                    "args_suggested": dict(raw_args or {},
                                          actor_consent_token=obs.get("consent_token", "")),
                    # carta vaglio per UI (Telegram bottoni o altro)
                    "approval_card": obs.get("approval_card") or {},
                    "signature": obs.get("signature", ""),
                }
                # _collect_expandable_caps non riconosce questa shape; la
                # iniettiamo manualmente settando expandable_caps qui (dopo
                # write() la lista normale viene sovrascritta — la
                # impostiamo dopo write()).
                log.final_kind = "answer"
                log.final_message = obs.get("summary") or "Operazione richiede approvazione."
                # Forza expandable_caps direttamente, write() lo preserva
                # solo se non sovrascrive: aggiungiamo il flag prima di write
                # via override locale.
                log._pending_admin_approval = approval_proposal  # type: ignore[attr-defined]
                log.ts_end = time.time()
                log.write()
                # Dopo write(), inietta la proposta come unica entry di
                # expandable_caps cosi' il daemon la trova.
                log.expandable_caps = [approval_proposal]
                return log

            # Install-on-demand T2 hook (§7.3, 17/5/2026): se admin ok
            # E c'e' un pending_install_resume associato al signature,
            # ri-esegui l'executor originale con args_base e chiudi il
            # turno con l'esito reale. Determinismo §7.9: niente LLM.
            if isinstance(obs, dict) and obs.get("ok") is True:
                try:
                    from install_resume_state import (
                        load as _load_resume, delete as _del_resume,
                    )
                    _pending = _load_resume(obs.get("signature", ""))
                    if _pending:
                        _del_resume(obs.get("signature", ""))
                        _cat = load_catalog()
                        _ex = _cat.executors.get(_pending["executor"])
                        if _ex is not None:
                            _resume_obs = invoke_executor(
                                _ex, _pending["args_base"],
                                timeout_s=getattr(_ex, "timeout_s", 60),
                                actor=_pending.get("actor", "host"),
                                channel=_pending.get("channel", ""),
                            )
                            _resume_step = StepLog(
                                step_num=step_num + 1,
                                chosen_tool=_pending["executor"],
                                raw_args=_pending["args_base"],
                                resolved_args=_pending["args_base"],
                                llm_text="(install_on_demand: auto-resume)",
                                result=_resume_obs,
                            )
                            log.steps.append(_resume_step)
                            _ok_resume = (isinstance(_resume_obs, dict)
                                            and _resume_obs.get("ok"))
                            log.final_kind = "answer" if _ok_resume else "error"
                            if _ok_resume:
                                _detail = (_resume_obs.get("summary")
                                            or "completato.")
                                log.final_message = (
                                    f"Installato e {_pending['executor']} "
                                    f"completato: {_detail}"
                                )
                            else:
                                _err = (_resume_obs.get("error")
                                         if isinstance(_resume_obs, dict)
                                         else "?")
                                log.final_message = (
                                    f"Pacchetto installato ma il riavvio "
                                    f"di {_pending['executor']} e' fallito: "
                                    f"{_err}"
                                )
                            log.ts_end = time.time(); log.write(); return log
                except Exception as _e:  # noqa: BLE001
                    import logging as _logging
                    _logging.getLogger(__name__).warning(
                        "install_on_demand T2 resume failed: %s", _e)

            # Decisione finale (execute_silent o reject): chiudi turno.
            if not is_multistep:
                log.final_kind = "answer" if obs.get("ok") else "error"
                log.final_message = obs.get("summary") or json.dumps(obs, ensure_ascii=False)
                log.ts_end = time.time(); log.write(); return log
            continue

        # Caso speciale: scratchpad_read e' builtin (no manifest, no subprocess)
        if chosen_name == "scratchpad_read":
            sp_id = args.get("scratchpad_id")
            if not sp_id:
                obs = {"ok": False, "error": "scratchpad_id mancante"}
            else:
                obs = sp.read(sp_id, mode=args.get("mode", "head"),
                              n=args.get("n", 2000),
                              start=args.get("start", 0),
                              end=args.get("end"))
            step.result = obs
            log.steps.append(step)
            history_for_refs.append({"step": step_num, "tool": chosen_name, "args": args, "observation": obs})
            history_for_llm.append({"role": "assistant", "tool_calls": [{"id": tc.call_id, "type": "function", "function": {"name": chosen_name, "arguments": raw_args}}]})
            obs_str = _trim_obs_for_history(obs)
            history_for_llm.append({"role": "tool", "tool_call_id": tc.call_id, "name": chosen_name, "content": obs_str})
            if not is_multistep:
                log.final_kind = "answer"; log.final_message = format_simple_answer(chosen_name, obs)
                log.ts_end = time.time(); log.write(); return log
            continue

        executor = catalog.get(chosen_name)
        if executor is None:
            obs = {"ok": False, "error": f"executor inesistente: {chosen_name}"}
            # Hook proto-mnest: se il LLM ha chiesto un tool inesistente con piping
            # da uno step precedente, registra un proto-mnest src=step_referenziato,
            # dst=nome_desiderato. Senza piping non e' un "passing".
            refs = extract_step_refs(raw_args)
            last_mnest_id = None
            for ref_step in refs:
                if 1 <= ref_step <= len(history_for_refs):
                    src_tool_name = history_for_refs[ref_step - 1]["tool"]
                    src_executor = catalog.get(src_tool_name)
                    if src_executor is None:
                        continue  # il src stesso era proto/scratchpad: skip
                    sig = build_desired_signature(chosen_name, raw_args, user_query=user_query_for_run)
                    try:
                        last_mnest_id = mnestoma.record_passing(
                            src_executor.name, src_executor.version,
                            chosen_name, dst_version=None, dst_exists=False,
                            desired_signature=sig, turn_id=turn_id,
                        )
                    except Exception as ex:
                        if verbose:
                            print(f"[mnest] proto record failed: {ex}")
            # Tentativo synt-on-the-fly: solo compose (router=None disabilita generate
            # costoso). Se compose trova una catena di executor firmati che copre il
            # proto-mnest, suggerisce al LLM di riprovare invocando il primo hop.
            if last_mnest_id is not None:
                synt_hint = _try_synt_compose(mnestoma, chosen_name, last_mnest_id, verbose=verbose)
                if synt_hint:
                    obs["synt"] = synt_hint
            step.result = obs
            log.steps.append(step)
            history_for_refs.append({"step": step_num, "tool": chosen_name, "args": args, "observation": obs})
            history_for_llm.append({"role": "assistant", "tool_calls": [{"id": tc.call_id, "type": "function", "function": {"name": chosen_name, "arguments": raw_args}}]})
            history_for_llm.append({"role": "tool", "tool_call_id": tc.call_id, "name": chosen_name, "content": json.dumps(obs, ensure_ascii=False)})
            if not is_multistep:
                log.final_kind = "error"; log.final_message = f"(errore: tool '{chosen_name}' inesistente)"
                log.ts_end = time.time(); log.write(); return log
            continue

        # Guard: duplicate read detection (read_files/write_files/get_urls su stesso path/url)
        # Evita loop dove il LLM ri-legge lo stesso file con args leggermente diversi
        # invece di formulare la final_answer.
        identifier = None
        if chosen_name in ("read_files", "write_files"):
            identifier = args.get("path")
        elif chosen_name == "get_urls":
            identifier = args.get("url")
        if identifier:
            for prev_step, prev_tool, prev_id in read_calls_seen:
                if prev_tool == chosen_name and prev_id == identifier:
                    obs = {
                        "ok": False,
                        "error": "duplicate_call",
                        "message": f"Hai gia' chiamato {chosen_name} su '{identifier}' al passo {prev_step}. Usa il content di quella observation per formulare la final_answer all'utente. Non rifare la lettura.",
                    }
                    step.result = obs
                    step.error = "duplicate read intercepted by runtime"
                    log.steps.append(step)
                    history_for_refs.append({"step": step_num, "tool": chosen_name, "args": args, "observation": obs})
                    history_for_llm.append({"role": "assistant", "tool_calls": [{"id": tc.call_id, "type": "function", "function": {"name": chosen_name, "arguments": raw_args}}]})
                    history_for_llm.append({"role": "tool", "tool_call_id": tc.call_id, "name": chosen_name, "content": json.dumps(obs, ensure_ascii=False)})
                    if not is_multistep:
                        log.final_kind = "error"; log.final_message = f"(rifiuto: {obs['message']})"
                        log.ts_end = time.time(); log.write(); return log
                    # In multistep, continua: il LLM al prossimo step dovrebbe formulare
                    break
            else:
                read_calls_seen.append((step_num, chosen_name, identifier))
        if identifier and read_calls_seen and read_calls_seen[-1] != (step_num, chosen_name, identifier) and step.error == "duplicate read intercepted by runtime":
            continue

        # P4 (12/5/2026) — Availability check gate per calendar write tools.
        # Bug live turn b1d9c236: query «fissa appuntamento ... SE C'È POSTO»
        # → planner ha chiamato set_events direttamente, bypassando il
        # workflow (check_availability) di calendar.j2 (1.get_now → 2.read_events
        # → 3.set_events se vuoto). Roberto aveva impegno tutto mercoledi:
        # l'evento e' stato creato lo stesso, overlap.
        # Defense in depth §7.9: il runtime rifiuta set_events quando:
        #   (1) chosen_name e' calendar write tool,
        #   (2) user_query contiene un availability marker,
        #   (3) NESSUN step precedente e' read_events ok.
        # L'obs di rifiuto e' lasciata in history: il planner LLM al prossimo
        # turno vede l'errore con hint e chiama read_events.
        if (chosen_name in _calendar_write_tools()
                and _query_requires_availability_check(user_query_for_run)
                and not _has_prior_read_events_ok(log.steps)):
            obs = {
                "ok": False,
                "_availability_check_required": True,
                "error": (
                    f"AVAILABILITY_CHECK_REQUIRED: la query contiene un "
                    f"marker di disponibilita' (es. «se c'e' posto», «if "
                    f"available»). DEVI chiamare read_events(time_window=...) "
                    f"PRIMA di '{chosen_name}' per verificare lo slot, poi "
                    f"set_events solo se entries vuota. Workflow numerato in "
                    f"section calendar (check_availability)."
                ),
            }
            step.result = obs
            step.error = "availability_check_required"
            log.steps.append(step)
            history_for_refs.append({"step": step_num, "tool": chosen_name, "args": raw_args, "observation": obs})
            history_for_llm.append({"role": "assistant", "tool_calls": [{"id": tc.call_id, "type": "function", "function": {"name": chosen_name, "arguments": raw_args}}]})
            history_for_llm.append({"role": "tool", "tool_call_id": tc.call_id, "name": chosen_name, "content": json.dumps(obs, ensure_ascii=False)})
            consecutive_blocked += 1
            if consecutive_blocked >= LOOP_BREAK_THRESHOLD:
                log.final_kind = "loop_break"
                _hint = _loop_break_hint(_intent_object_from_route(route_info))
                log.final_message = msg("MSG_LOOP_BREAK", n=consecutive_blocked, hint=_hint)
                log.ts_end = time.time(); log.write(); return log
            continue

        # P5 (12/5/2026) — Propose-intent gate per calendar write tools.
        # Bug live turn a0b96f6f: query «proponi 3 orari per appuntamento
        # la prossima settimana mattina» → planner ha chiamato set_events
        # con whole-week blob (lun→sab 8-12). Atteso: read_events + final
        # testuale con N slot. NESSUN evento creato.
        # Defense in depth §7.9: il runtime rifiuta calendar-write tools
        # quando:
        #   (1) chosen_name e' calendar write tool (set/create _events),
        #   (2) user_query e' propose-intent (regex semantica universale).
        # NB: a differenza di P4, NON serve "prior read_events ok": una
        # propose-intent query e' SEMPRE risolta con final_answer testuale,
        # mai con set_events. Il read_events e' raccomandato per dati ma
        # opzionale (planner puo' rispondere generico se serve). Cio' che
        # blocchiamo qui e' la creazione effettiva di eventi.
        # ADR 0129 fire-after-propose exception: la guardia propose_intent
        # va sospesa quando siamo in continuation post-dialog (utente ha
        # gia' fatto la scelta esplicita → la "proposta" e' diventata
        # "fire"). Detect: history contiene `get_inputs` con observation
        # `decision="completed"` o `_resumed=True`. §7.9 deterministico.
        _dialog_completed = any(
            isinstance(h, dict)
            and h.get("tool") == "get_inputs"
            and isinstance(h.get("observation"), dict)
            and (h["observation"].get("decision") == "completed"
                 or h["observation"].get("_resumed"))
            for h in history_for_refs
        )
        if (chosen_name in _calendar_write_tools()
                and _query_is_propose_intent(user_query_for_run)
                and not _dialog_completed):
            obs = {
                "ok": False,
                "_propose_intent_detected": True,
                "error": (
                    f"PROPOSE_INTENT_NO_WRITE: la query e' una richiesta di "
                    f"proposta/suggerimento (es. «proponi 3 orari», «suggest "
                    f"options», «what are free slots»). DEVI rispondere "
                    f"testualmente con N slot/alternative computate da "
                    f"read_events. NON DEVI invocare '{chosen_name}' o "
                    f"altri tool calendar-write: la query non chiede di "
                    f"CREARE un evento, ma di SUGGERIRE alternative. "
                    f"Workflow: get_now → read_events(time_window=...) → "
                    f"final_answer con N proposte. Section calendar (propose_intent)."
                ),
            }
            step.result = obs
            step.error = "propose_intent_no_write"
            log.steps.append(step)
            history_for_refs.append({"step": step_num, "tool": chosen_name, "args": raw_args, "observation": obs})
            history_for_llm.append({"role": "assistant", "tool_calls": [{"id": tc.call_id, "type": "function", "function": {"name": chosen_name, "arguments": raw_args}}]})
            history_for_llm.append({"role": "tool", "tool_call_id": tc.call_id, "name": chosen_name, "content": json.dumps(obs, ensure_ascii=False)})
            consecutive_blocked += 1
            if consecutive_blocked >= LOOP_BREAK_THRESHOLD:
                log.final_kind = "loop_break"
                _hint = _loop_break_hint(_intent_object_from_route(route_info))
                log.final_message = msg("MSG_LOOP_BREAK", n=consecutive_blocked, hint=_hint)
                log.ts_end = time.time(); log.write(); return log
            continue

        # Risoluzione scope-arg mancanti/placeholder PRIMA della validazione
        # (universale §7.9): arg-esplicito-valido → inline-dalla-query →
        # ricordato → config. La cattura del valore usato è dopo l'invoke OK.
        try:
            from args_resolver import resolve_scope_args
            args = resolve_scope_args(
                chosen_name, args, executor.args_schema,
                actor=args.get("_actor") or actor or "host",
                query=user_query_for_run)
        except Exception:
            pass
        # Validazione, sandbox, vaglio
        validation = validate_args(args, executor.args_schema)
        step.validation_failures = validation
        if validation:
            # Arricchimento hint §7.3: il vincolo `requires_one_of` indica
            # all'LLM che servono args alternativi, ma il LLM medium spesso
            # ignora e riprova identico. Aggiungiamo hint sistemico generico
            # «se non hai gli args, chiama un producer e usa from_step=N»
            # (PRODUCER_VERBS lookup dal vocab, no enum hardcoded di tool).
            _hint = ""
            if any("requires one of" in f for f in validation):
                try:
                    from vocab import PRODUCER_VERBS as _PV
                    _verbs = ", ".join(sorted(_PV))
                    _hint = (f" Se non disponi di questi args, prima invoca "
                             f"un executor producer (verbo fra: {_verbs}) "
                             f"e passa `from_step=N` qui (§4.1).")
                except Exception:
                    _hint = (" Se non hai questi args, prima invoca un "
                             "producer step (find/list/get/read) e usa "
                             "`from_step=N`.")
            obs = {
                "ok": False,
                "error": f"validation failed: {validation}{_hint}",
                "error_class": "invalid_args",
                "validation_failures": validation,
            }
            step.error = "validation_failed"
        else:
            scope_violation = check_hints(args, executor.capabilities)
            step.scope_violation = scope_violation
            if scope_violation:
                obs = {"ok": False, "error": scope_violation}
            else:
                # Telemetria fine (ADR 0080): vaglio_ms misurato al confine.
                _t_vaglio0 = time.perf_counter()
                verdict = judge(user_query_for_run, chosen_name, args, {"mode": chosen_mode, "step": step_num})
                step.vaglio_ms = int((time.perf_counter() - _t_vaglio0) * 1000)
                step.vaglio_approved = verdict.approved
                if not verdict.approved:
                    obs = {"ok": False, "error": f"vaglio rifiuta: {verdict.reason}"}
                else:
                    if verbose:
                        print(f"[step {step_num}] exec {chosen_name}({args})")
                    op_id = None
                    if executor.revertible:
                        op_id = uuid.uuid4().hex
                        try:
                            UndoLog().append_pending(op_id, turn_id, executor.name, args, plan={}, actor=actor, channel=channel)
                        except Exception as ex:
                            if verbose:
                                print(f"[undo] append_pending failed: {ex}")
                            op_id = None
                    # Telemetria fine (ADR 0080): exec_ms = pura execution
                    # (subprocess + I/O), esclusa la chiamata LLM del PLANNER.
                    _t_exec0 = time.perf_counter()
                    try:
                        obs = invoke_executor(
                            executor, args, turn_id=turn_id,
                            timeout_s=getattr(executor, "timeout_s", 30),
                            actor=actor, channel=channel,
                        )
                    except subprocess.TimeoutExpired:
                        obs = {"ok": False, "error": "executor timeout"}
                    step.exec_ms = int((time.perf_counter() - _t_exec0) * 1000)
                    if op_id and obs.get("ok"):
                        try:
                            UndoLog().append_done(op_id, obs)
                        except Exception as ex:
                            if verbose:
                                print(f"[undo] append_done failed: {ex}")
                    # Cattura scope-arg: ultimo valore usato → default per il
                    # giro dopo (anche se inline o esplicito). §7.9, no LLM.
                    if isinstance(obs, dict) and obs.get("ok"):
                        try:
                            from args_resolver import remember_scope_args
                            remember_scope_args(
                                chosen_name, args,
                                actor=args.get("_actor") or actor or "host")
                        except Exception:
                            pass

        step.result = obs
        log.steps.append(step)
        history_for_refs.append({"step": step_num, "tool": chosen_name, "args": args, "observation": obs})

        # Terminal-failure short-circuit (15/5/2026): executor che ritorna
        # `_terminal: True` + `final_message_hint` indica un fail con
        # messaggio user-facing autoritativo (es. index_missing). NIENTE
        # senso a iterare: chiudi il turno con quel hint come final_message.
        # Bug live 15/5/2026 turn e362785f: find_images_indices su path
        # non indicizzato → 3× retry → loop_break generico. Con _terminal
        # il turno chiude al primo fail con messaggio chiaro.
        if isinstance(obs, dict) and obs.get("_terminal") \
                and obs.get("final_message_hint"):
            log.final_kind = "answer"
            log.final_message = str(obs.get("final_message_hint", ""))
            log.ts_end = time.time(); log.write(); return log

        # Generic needs_inputs orchestration: qualsiasi executor (set_persons
        # face picker, delete_persons batch ambiguous, future tools) che
        # ritorna `decision="needs_inputs"` deve far chiudere il turno
        # con la carta UI persistita via `dialog_pending`. Senza questo
        # branch il PLANNER continua e inventa una final_answer al posto
        # del face picker (10/5/2026: bug live turn 9e066cb9 set_persons).
        # Il branch admin-specifico sopra (chosen_name=="admin") resta
        # come fast-path ma non e' piu' l'unico path.
        if (isinstance(obs, dict)
                and obs.get("decision") == "needs_inputs"
                and chosen_name not in ("admin", "get_inputs")):
            from orchestration import orchestrate_needs_inputs
            history_for_llm.append({"role": "assistant", "tool_calls": [{"id": tc.call_id, "type": "function", "function": {"name": chosen_name, "arguments": raw_args}}]})
            obs_str_h = _trim_obs_for_history(obs)
            history_for_llm.append({"role": "tool", "tool_call_id": tc.call_id, "name": chosen_name, "content": obs_str_h})
            sender_for_state = (
                f"{channel}:{actor}" if channel else (actor or "host")
            )
            gi_result = orchestrate_needs_inputs(
                obs,
                sender_id=sender_for_state,
                actor=actor or "host",
                channel=channel or None,
            )
            if not gi_result.get("ok"):
                log.final_kind = "error"
                log.final_message = (
                    f"Orchestrazione get_inputs fallita per {chosen_name}: "
                    + (gi_result.get("error") or "errore sconosciuto")
                )
                log.ts_end = time.time(); log.write(); return log
            log.final_kind = "answer"
            log.final_message = (
                gi_result.get("final_message_hint")
                or obs.get("final_message_hint")
                or "Servono alcuni input per continuare."
            )
            log.ts_end = time.time()
            log.write()
            # Forza expandable_caps DOPO write(): _collect_expandable_caps
            # non riconosce decision=needs_inputs su tool generici (come
            # set_persons), quindi la lista naturale e' vuota. Copiamo
            # quella prodotta da orchestrate_needs_inputs perche' il
            # daemon (Telegram) e _apply_cap_pending (HTTP) leggono da
            # qui per dispatchare la prossima risposta utente al dialog.
            caps = gi_result.get("expandable_caps") or []
            for cap in caps:
                if cap.get("kind") == "get_inputs_response":
                    cap.setdefault("sender_for_state", sender_for_state)
            if caps:
                log.expandable_caps = caps
            return log

        # ADR 0111 (7/5/2026): Level 3 — auto-final deterministico dopo
        # `get_processes` ok con campo `health` non vuoto. La query e' di
        # tipo "stato sistema/server/carico/ram"; il blocco "Stato server"
        # gia' formattato sara' prepended da `_prepend_health_block_if_any`
        # in TurnLog.write(). Saltare il PLANNER step 2+ evita che il
        # modello chiami describe_entries (che vede solo entries=processi
        # e dichiarerebbe "non disponibile" su salute, contraddicendo il
        # blocco prepended) o ri-chieda lo stato (loop). §7.9 deterministico
        # > LLM. Skip se l'utente ha richiesto un'azione (kill/ferma/manda/
        # scrivi/...) — heuristic su intent.verb e keyword imperative.
        if (chosen_name == "get_processes"
                and isinstance(obs, dict)
                and obs.get("ok")
                and isinstance(obs.get("health"), dict)
                and obs.get("health")
                and is_multistep):
            _q_low = (user_query_for_run or "").lower()
            _has_imperative = any(k in _q_low for k in _HEALTH_IMPERATIVE_KEYWORDS)
            _is_action_intent = _intent_verb in _ACTION_VERBS_PRED
            if not _has_imperative and not _is_action_intent:
                # history_for_llm append per la step record completa.
                history_for_llm.append({
                    "role": "assistant",
                    "tool_calls": [{
                        "id": tc.call_id, "type": "function",
                        "function": {"name": chosen_name, "arguments": raw_args},
                    }],
                })
                obs_str_h = _trim_obs_for_history(obs)
                history_for_llm.append({
                    "role": "tool", "tool_call_id": tc.call_id,
                    "name": chosen_name, "content": obs_str_h,
                })
                log.final_kind = "answer"
                # final_message minimo: il blocco "Stato server" verra'
                # prepended automaticamente da _prepend_health_block_if_any
                # in write(). Lasciamo stringa vuota → il blocco diventa
                # tutto il messaggio (no boilerplate aggiuntivo, vedi
                # ADR 0095 output deterministico).
                log.final_message = ""
                step.error = "auto_final_health"
                log.ts_end = time.time()
                log.write()
                return log

        # Approval card generica (the design guide 2.11 fase 2): qualsiasi executor
        # regolare puo' chiedere conferma esplicita ritornando
        # `approval_required:true` + `final_message_hint` + `expandable_caps`.
        # Esempio: find_images_indices lazy build chiede di indicizzare 30k foto.
        # Senza questo break il PLANNER vede observation con ok=true, legge
        # `args_suggested` nel cap, e RILANCIA lo stesso tool con
        # `force_build=true` di sua iniziativa — bypassando l'utente. Stop qui:
        # write() preserva expandable_caps, daemon salva pending state, prossimo
        # turno l'utente conferma o annulla.
        if (isinstance(obs, dict)
                and obs.get("ok")
                and obs.get("approval_required")
                and obs.get("final_message_hint")
                and chosen_name not in ("admin", "get_inputs")):
            history_for_llm.append({"role": "assistant", "tool_calls": [{"id": tc.call_id, "type": "function", "function": {"name": chosen_name, "arguments": raw_args}}]})
            obs_str_h = _trim_obs_for_history(obs)
            history_for_llm.append({"role": "tool", "tool_call_id": tc.call_id, "name": chosen_name, "content": obs_str_h})
            log.final_kind = "answer"
            log.final_message = obs.get("final_message_hint")
            log.ts_end = time.time()
            log.write()
            return log

        # Caso speciale get_inputs (ADR 0090): chiude il turno immediatamente
        # quando il dialogo richiede input (evita che il PLANNER prosegua o
        # che il modello inventi una final_answer al posto del prompt UX
        # dell'executor). final_message = `final_message_hint` dell'observation.
        # `expandable_caps` con kind="get_inputs_response" propagato a TurnLog
        # (via _collect_expandable_caps + injecting sender_for_state).
        if (chosen_name == "get_inputs"
                and isinstance(obs, dict)
                and obs.get("ok")
                and obs.get("decision") == "input_required"):
            history_for_llm.append({"role": "assistant", "tool_calls": [{"id": tc.call_id, "type": "function", "function": {"name": chosen_name, "arguments": raw_args}}]})
            obs_str_h = _trim_obs_for_history(obs)
            history_for_llm.append({"role": "tool", "tool_call_id": tc.call_id, "name": chosen_name, "content": obs_str_h})

            # MID-PIPELINE get_inputs (12/5/2026, bug residuo propose+notify):
            # quando la query e' propose+notify e/o ha continuation di
            # notifica/azione, il PLANNER deve riprendere DOPO il pick utente
            # con send_messages/create_events/... Patchamo il dialog state per
            # iniettare `on_complete=resume_planner_with_dialog_values` con
            # snapshot dello scratchpad. process_completion_callback prendera'
            # la palla al submit dell'utente. Determinismo §7.9.
            try:
                if _should_resume_planner_after_dialog(
                        user_query_for_run, route_info, history_for_refs):
                    sender_for_state = (
                        f"{channel}:{actor}" if channel else (actor or "host")
                    )
                    dialog_id = obs.get("dialog_id")
                    if dialog_id:
                        import dialog_pending as _dp
                        _state = _dp.load_pending(sender_for_state, dialog_id)
                        if _state is not None and not _state.get("on_complete"):
                            _dialog_var = None
                            _dlg = _state.get("dialog") or []
                            if _dlg and isinstance(_dlg[0], dict):
                                _dialog_var = _dlg[0].get("var")
                            # Estrai implicit_actions dall'intent del turno
                            # corrente per orchestrazione deterministica post-
                            # dialog (ADR 0129). Generalizza l'orchestratore: il
                            # callback non deve indovinare quali verbi mutating
                            # eseguire, li legge dall'intent gia' classificato.
                            _intent_dict = (route_info.get("intent")
                                            if isinstance(route_info, dict)
                                            else None) or {}
                            _implicit_actions = (
                                _intent_dict.get("implicit_actions")
                                if isinstance(_intent_dict, dict) else None
                            ) or []
                            _state["on_complete"] = {
                                "type": "resume_planner_with_dialog_values",
                                "original_query": user_query_for_run,
                                "prior_steps": _snapshot_scratchpad(
                                    history_for_refs),
                                "dialog_step_num": step_num,
                                "dialog_var_name": _dialog_var or "values",
                                "conversation_id": conversation_id or "",
                                "implicit_actions": list(_implicit_actions),
                            }
                            _dp.save_pending(sender_for_state, dialog_id, _state)
                            if verbose:
                                print(f"[resume_after_dialog] on_complete "
                                      f"iniettato per dialog_id={dialog_id} "
                                      f"prior_steps={len(history_for_refs)}")
            except Exception as _ex:
                log.warning("inject on_complete (resume_planner) fallito: %s", _ex)

            log.final_kind = "answer"
            log.final_message = (
                obs.get("final_message_hint")
                or obs.get("summary")
                or "Servono alcuni input per continuare."
            )
            log.ts_end = time.time()
            log.write()
            # Dopo write() (che ha gia' propagato expandable_caps via
            # _collect_expandable_caps), arricchisci la prima entry con
            # `sender_for_state` (channel:actor) cosi' il daemon trova il
            # dialog state al posto giusto. write() preserva i campi extra.
            if log.expandable_caps:
                sender_for_state = (
                    f"{channel}:{actor}" if channel else (actor or "host")
                )
                for cap in log.expandable_caps:
                    if cap.get("kind") == "get_inputs_response":
                        cap.setdefault("sender_for_state", sender_for_state)
                        # actor_consent_token-style: se admin-credentials migration
                        # passa qui, il chiamante ha gia' settato `credentials_domain`.
            return log

        # Hook executor_aging: ogni invocazione effettiva di un executor
        # del pool aggiorna last_used_at + total_calls. Idempotente,
        # best-effort. Il decay `apply_executor_ager` notturno legge da
        # qui per decidere quale executor retirare.
        try:
            from executor_aging import touch as _exec_touch
            _exec_touch(chosen_name, ok=bool(obs.get("ok")))
        except Exception:
            pass  # silent: tracking history non blocca mai un turno

        # Hook adaptive re-rank (4/5/2026): se lo step e' andato a buon
        # fine, ricalcola il pool di candidati per il prossimo step
        # basandosi su keywords estratte dall'observation. Add-only:
        # i tool gia' selezionati restano nel pool (cap 2 × k_max).
        # Indipendente dal ranker sottostante (token-based oggi, embedding
        # domani). Costo a regime ~5 ms su 300 executor (misurato).
        if isinstance(obs, dict) and obs.get("ok"):
            # Telemetria fine (ADR 0080): rerank_ms.
            _t_rerank0 = time.perf_counter()
            try:
                from adaptive_rerank import re_rank_for_step
                _candidates_before = list(candidates)
                candidates_post, rr_info = re_rank_for_step(
                    original_query=user_query_for_run,
                    catalog=catalog,
                    current_candidates=_candidates_before,
                    latest_observation=obs,
                    k_min=k_min,
                    k_max=k_max,
                    history_tools=[h["tool"] for h in history_for_refs],
                )
                if rr_info.get("applied"):
                    candidates = candidates_post
                    if verbose:
                        print(f"[rerank] step {step_num}: +{len(rr_info.get('added') or [])} "
                              f"({','.join(rr_info.get('added') or [])})")
                    # Ricostruzione base_tools: i nuovi tool diventano
                    # disponibili al prossimo turno della LLM.
                    base_tools = render_tools_for_provider(candidates)
            except Exception as _e:
                # Re-rank non blocca mai il turno: e' un'ottimizzazione.
                if verbose:
                    print(f"[rerank] step {step_num}: skipped ({type(_e).__name__})")
            step.rerank_ms = int((time.perf_counter() - _t_rerank0) * 1000)

        # Hook mnest: se lo step e' andato a buon fine E i raw_args contenevano
        # riferimenti {{stepM.field}}, registra un mnest src=step_M_tool, dst=current.
        if obs.get("ok"):
            refs = extract_step_refs(raw_args)
            for ref_step in refs:
                if 1 <= ref_step <= len(history_for_refs) - 1:  # -1 perche' history include lo step corrente
                    src_tool_name = history_for_refs[ref_step - 1]["tool"]
                    src_executor = catalog.get(src_tool_name)
                    if src_executor is None:
                        continue  # src non era un executor reale (proto/scratchpad)
                    try:
                        mnestoma.record_passing(
                            src_executor.name, src_executor.version,
                            executor.name, executor.version,
                            dst_exists=True, turn_id=turn_id,
                        )
                    except Exception as ex:
                        if verbose:
                            print(f"[mnest] active record failed: {ex}")

        # Offload a scratchpad: SEMPRE per output strutturati (liste in candidate_keys)
        # o per content > soglia. Il pianificatore vede solo handle (scratchpad_id +
        # count + schema + ref_hint), mai i dati raw — i dati raw passano al prossimo
        # executor solo via reference {{stepN.field}}, risolta dal runtime.
        obs_for_history = obs
        obs_str = json.dumps(obs, ensure_ascii=False)
        # Offload solo per liste grandi (>20). Sotto soglia, il modello
        # vede entries inline (no handle): evita fabricazione su top-K piccoli.
        has_structured_list = any(
            isinstance(obs.get(k), list) and len(obs.get(k)) > 20
            for k in ("entries", "matches", "items", "results", "files", "paths")
        )
        if obs.get("ok") and (has_structured_list or len(obs_str) > scratchpad_threshold):
            obs_for_history = sp.put(turn_id, step_num, chosen_name, obs)
            if verbose:
                print(f"[step {step_num}] obs -> scratchpad id={obs_for_history['scratchpad_id']} (size={len(obs_str)} list={has_structured_list})")

        # Reset consecutive_blocked counter: questo step e' produttivo (executor eseguito)
        if obs.get("ok"):
            consecutive_blocked = 0
        else:
            consecutive_blocked += 1
            if consecutive_blocked >= LOOP_BREAK_THRESHOLD:
                log.final_kind = "loop_break"
                _last_err = (step.result.get("error") if isinstance(step.result, dict) else None) or step.error or "n/a"
                _hint = _loop_break_hint(_intent_object_from_route(route_info))
                log.final_message = msg("MSG_LOOP_BREAK", n=consecutive_blocked, hint=_hint)
                log.ts_end = time.time(); log.write(); return log

        # Aggiungi tool_call e tool_result alla history LLM
        history_for_llm.append({"role": "assistant", "tool_calls": [{"id": tc.call_id, "type": "function", "function": {"name": chosen_name, "arguments": raw_args}}]})
        obs_str_h = _trim_obs_for_history(obs_for_history)
        history_for_llm.append({"role": "tool", "tool_call_id": tc.call_id, "name": chosen_name, "content": obs_str_h})

        # In single-shot, finiamo qui col simple-format
        if not is_multistep:
            log.final_kind = "answer"; log.final_message = format_simple_answer(chosen_name, obs)
            log.ts_end = time.time(); log.write(); return log

        # ─── Deterministic seed-step injection per pipeline propose+notify ────
        # ADR 0129 extended (14/5/2026 sera): dopo `find_events_empty` ok con
        # entries in pipeline propose+notify, il PLANNER medium (Gemma 4 26B)
        # va in thinking loop su query con dettagli aggiuntivi («con Bob»,
        # «di una ora la mattina», ecc.) — esaurisce max_tokens senza emettere
        # `get_inputs`. Bug live turn cc8d3980 (166s, step 2 vuoto).
        # Fix: emetto deterministicamente lo step `get_inputs(choice)` come
        # next-step, bypassando il PLANNER per quel singolo step. Pattern
        # analogo a `try_seed_step` di ADR 0099. §7.9.
        if (chosen_name == "find_events_empty"
                and isinstance(obs, dict) and obs.get("ok")
                and (obs.get("entries") or [])
                and _should_resume_planner_after_dialog(
                    user_query_for_run, route_info, history_for_refs)):
            try:
                _seed_gi = _inject_get_inputs_choice_for_propose(
                    entries=obs.get("entries") or [],
                    object_canonical=(_intent_object_from_route(route_info)
                                       or "events"),
                )
            except Exception as _ex:
                log.warning("seed get_inputs injection failed: %s", _ex)
                _seed_gi = None
            if _seed_gi is not None and verbose:
                print(f"[seed-step] inject get_inputs(choice) "
                      f"dopo find_events_empty step={step_num}")
            if _seed_gi is not None:
                # Eseguo direttamente get_inputs (autonomous step+1).
                _gi_args = _seed_gi
                _gi_step_num = step_num + 1
                _gi_step = StepLog(step_num=_gi_step_num)
                _gi_step.chosen_tool = "get_inputs"
                _gi_step.raw_args = dict(_gi_args)
                _gi_step.resolved_args = dict(_gi_args)
                _gi_step.vaglio_approved = True
                _gi_step.error = "seed_step_after_find_empty"
                try:
                    from loader import load_catalog as _lc
                    _cat = _lc(verify=True, include_synth=True)
                    _gi_ex = _cat.executors.get("get_inputs")
                    if _gi_ex is None:
                        raise RuntimeError("get_inputs executor non in catalog")
                    _gi_obs = invoke_executor(
                        _gi_ex, _gi_args,
                        timeout_s=getattr(_gi_ex, "timeout_s", 30),
                        actor=actor, channel=channel or "",
                    )
                except Exception as _ex:
                    _gi_step.error = f"seed_step_failed: {type(_ex).__name__}: {_ex}"
                    log.steps.append(_gi_step)
                    # Lascio il loop continuare: il PLANNER tentera' step+1 reale
                else:
                    _gi_step.result = _gi_obs
                    log.steps.append(_gi_step)
                    history_for_refs.append({
                        "step": _gi_step_num, "tool": "get_inputs",
                        "args": _gi_args, "observation": _gi_obs,
                    })
                    # Chiudi il turno se input_required (uguale al ramo
                    # `decision == "input_required"` piu' sotto, ma evita
                    # round trip PLANNER + duplicazione codice).
                    if (isinstance(_gi_obs, dict) and _gi_obs.get("ok")
                            and _gi_obs.get("decision") == "input_required"):
                        log.final_kind = "answer"
                        log.final_message = (
                            _gi_obs.get("final_message_hint")
                            or "Servono alcuni input per continuare."
                        )
                        # Inject on_complete per resume planner (stessa
                        # logica del blocco standard a riga ~4978).
                        try:
                            if _should_resume_planner_after_dialog(
                                    user_query_for_run, route_info,
                                    history_for_refs):
                                sender_for_state = (
                                    f"{channel}:{actor}" if channel
                                    else (actor or "host")
                                )
                                dialog_id = _gi_obs.get("dialog_id")
                                if dialog_id:
                                    import dialog_pending as _dp
                                    _state = _dp.load_pending(
                                        sender_for_state, dialog_id)
                                    if (_state is not None
                                            and not _state.get("on_complete")):
                                        _dialog_var = None
                                        _dlg = _state.get("dialog") or []
                                        if _dlg and isinstance(_dlg[0], dict):
                                            _dialog_var = _dlg[0].get("var")
                                        _intent_dict = (
                                            route_info.get("intent")
                                            if isinstance(route_info, dict)
                                            else None) or {}
                                        _implicit_actions = (
                                            _intent_dict.get(
                                                "implicit_actions")
                                            if isinstance(_intent_dict, dict)
                                            else None) or []
                                        _state["on_complete"] = {
                                            "type": "resume_planner_with_dialog_values",
                                            "original_query": user_query_for_run,
                                            "prior_steps":
                                                _snapshot_scratchpad(
                                                    history_for_refs),
                                            "dialog_step_num": _gi_step_num,
                                            "dialog_var_name":
                                                _dialog_var or "values",
                                            "conversation_id":
                                                conversation_id or "",
                                            "implicit_actions":
                                                list(_implicit_actions),
                                        }
                                        _dp.save_pending(
                                            sender_for_state, dialog_id, _state)
                        except Exception as _ex:
                            log.warning(
                                "inject on_complete (seed-step) fallito: %s",
                                _ex)
                        log.ts_end = time.time()
                        log.write()
                        if log.expandable_caps:
                            sender_for_state = (
                                f"{channel}:{actor}" if channel
                                else (actor or "host")
                            )
                            for cap in log.expandable_caps:
                                if cap.get("kind") == "get_inputs_response":
                                    cap.setdefault("sender_for_state",
                                                    sender_for_state)
                        return log

        # Auto-final dopo executor transformative idempotente che ha gia'
        # creato/modificato una entita' remota: il planner LLM puo' oscillare
        # con args leggermente diversi (es. start 9-10 vs 9-11) bypassando
        # DUPLICATE_CALL, creando N entita' duplicate. Bug live turn c627784c
        # (11/5/2026 sera): set_events x5 → 5 eventi calendario.
        # Pattern: chosen_name in lista whitelist + obs.ok + _undo presente
        # (= operazione registrata revertibile) → final_answer immediato con
        # link/id. §7.9 deterministico, niente LLM aggiuntivo.
        #
        # P3 (12/5/2026): suppress auto-final se la user_query contiene una
        # congiunzione di continuation («e mandami email», «and notify me»).
        # Bug live turn b1d9c236: «fissa appuntamento ... E MANDAMI EMAIL»
        # chiudeva il turno dopo set_events ok, perdendo la send_messages.
        # `_query_has_continuation` lookup regex deterministico.
        # `_query_has_continuation` inibisce auto-final per evitare di
        # chiudere il turno a meta' pipeline (turn b1d9c236, 12/5: «fissa
        # appuntamento E MANDAMI EMAIL» chiudeva dopo create_events). Ma se
        # TUTTI i verbi della query sono gia' coperti dagli step ok=True
        # (incluso il corrente), la pipeline e' completa e bisogna chiudere
        # (turn 7f7381d2, 14/5: PLANNER ri-eseguiva find_events_empty dopo
        # send_messages ok perche' continuation era ancora True). §7.9.
        _executed_verbs_now = [
            (s.chosen_tool or "") for s in log.steps
            if isinstance(s.result, dict) and s.result.get("ok") is True
        ] + [chosen_name]
        _pipeline_complete = _all_query_verbs_satisfied(
            user_query_for_run, _executed_verbs_now,
        )
        # Reversibili (set_events/create_events/...): chiude su _undo+ids
        # presenti (= operazione registrata revertibile, htmlLink/id nel detail).
        # Irreversibili (send_messages): nessun `_undo`; chiude SE pipeline
        # complete (tutti i verbi query satisfied). Senza pipeline_complete
        # la guardia continuation resta intatta (memoria turn b1d9c236).
        _has_undo = (isinstance(obs.get("_undo"), dict)
                     and obs.get("_undo", {}).get("ids"))
        _final_safe = (
            (not _query_has_continuation(user_query_for_run) and _has_undo)
            or _pipeline_complete
        )
        # Auto-final transformative: triggera SOLO se almeno 1 elemento e'
        # davvero stato modificato. ok_count==0 con outer ok=true significa
        # tutti i results sono fallimenti (binary_missing, dst_exists,
        # converter_failed, etc.) — il PLANNER deve continuare per emettere
        # admin install (install-on-demand pattern §7.3, 17/5/2026) o
        # gestire l'errore. Sintassi: ok_count manca o None → backward
        # compat (vecchi executor non riportavano) → considera implicit 1.
        _ok_count_eff = obs.get("ok_count") if isinstance(obs, dict) else None
        if _ok_count_eff is None:
            _ok_count_eff = obs.get("n_created") if isinstance(obs, dict) else None
        # Se esplicito a 0 → NIENTE auto-final (lascia PLANNER continuare).
        _has_real_change = _ok_count_eff != 0
        if (_is_auto_final_transformative(chosen_name)
                and isinstance(obs, dict)
                and obs.get("ok") is True
                and _has_real_change
                and _final_safe):
            r0 = (obs.get("results") or [{}])[0]
            _detail = (r0.get("htmlLink") or r0.get("id")
                       or r0.get("dst")
                       or _format_send_messages_detail(obs))
            log.final_kind = "answer"
            log.final_message = msg(
                "MSG_TRANSFORMATIVE_AUTO_FINAL",
                executor=chosen_name,
                count=obs.get("n_created") or obs.get("ok_count") or 1,
                detail=_detail,
            )
            log.ts_end = time.time(); log.write(); return log

        # Auto-final dopo undo successful: il modello tipicamente non rispetta
        # la regola 2-bis del prompt (chiamare undo una sola volta per turno);
        # il runtime forza la chiusura cosi' l'utente vede subito l'esito.
        if (chosen_name == "undo_last_turn"
                and isinstance(obs, dict)
                and obs.get("ok")
                and (obs.get("undone_count") or 0) >= 1):
            details = obs.get("details") or []
            d0 = details[0] if details else {}
            target_executor = d0.get("executor", "azione")
            target_count = d0.get("ok_count", obs.get("undone_count", 0))
            log.final_kind = "answer"
            log.final_message = msg(
                "MSG_UNDO_AUTO_FINAL",
                executor=target_executor, count=target_count,
            )
            log.ts_end = time.time(); log.write(); return log

        # Auto-final dopo undo FALLITO (ok:false, undone_count=0): bug live
        # turn d7417418 — query «annulla ultima azione» quando non c'e' nulla
        # da annullare faceva 3x undo → loop_break con hint generico. Il
        # planner non rispettava la regola 2-bis (undo ok:false → final).
        # Forziamo deterministicamente. §7.9.
        if (chosen_name == "undo_last_turn"
                and isinstance(obs, dict)
                and obs.get("ok") is False
                and (obs.get("undone_count") or 0) == 0):
            log.final_kind = "answer"
            log.final_message = msg("MSG_UNDO_NOTHING")
            log.ts_end = time.time(); log.write(); return log

    # Cap steps superato
    log.final_kind = "cap_steps"
    log.final_message = msg("MSG_CAP_STEPS", cap=cap_steps)
    log.ts_end = time.time(); log.write(); return log


def format_simple_answer(executor_name, result):
    if not result.get("ok"):
        return f"Errore in {executor_name}: {result.get('error', 'sconosciuto')}"
    content = result.get("content", "")
    meta = result.get("metadata", {})
    if executor_name == "get_now":
        return f"Sono le {content} ({meta.get('timezone','UTC')})."
    if executor_name == "read_files":
        preview = (content or "")[:300]
        return f"{meta.get('path','?')}:\n{preview}{'…' if len(content) > 300 else ''}"
    if executor_name == "write_files":
        return f"Scritti {meta.get('bytes_written',0)} byte in {meta.get('path','?')}."
    if executor_name == "get_urls":
        preview = (content or "")[:300]
        return f"GET {meta.get('url','?')} -> {meta.get('status','?')}, {meta.get('bytes',0)} byte:\n{preview}{'…' if len(content) > 300 else ''}"
    return json.dumps(result, ensure_ascii=False)[:300]


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("query", nargs="+")
    ap.add_argument("--mode", default="local", choices=["local", "online", "hybrid"])
    ap.add_argument("--model", default=None)
    ap.add_argument("--think", action="store_true", help="Abilita thinking del LLM (qwen3, deepseek)")
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--cap-steps", type=int, default=DEFAULT_CAP_STEPS)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    query = " ".join(args.query)
    log = run_turn(query, mode=args.mode, model=args.model, k=args.k,
                   cap_steps=args.cap_steps, think=args.think, verbose=args.verbose)
    print(f"\n>>> {log.final_message}\n")
    if args.verbose:
        total_in = sum(s.llm_in_tokens for s in log.steps)
        total_out = sum(s.llm_out_tokens for s in log.steps)
        total_lat = sum(s.llm_latency_ms for s in log.steps)
        print(f"--- log: {len(log.steps)} step, llm {total_in}->{total_out} toks in {total_lat}ms, turn {(log.ts_end - log.ts_start)*1000:.0f}ms, kind={log.final_kind}")
