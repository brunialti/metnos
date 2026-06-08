#!/usr/bin/env python3
"""vaglio.py — il valutatore costituzionale (Metnos v1.1).

Due fasi distinte, come da cap.11 dell'Architettura:

1. **Guardia**: binaria. Blocca violazioni delle Leggi e tocchi a forbidden
   path. Se la guardia ferma, niente score: la decisione e' "denied".

2. **Giudice**: graduato. Misura l'allineamento dell'azione ai telos
   dell'utente, restituisce un punteggio in [0, 1]. Sotto soglia,
   l'azione e' negata.

In v1.1 il **giudice e' rule-based** (heuristiche locali, niente LLM):
delibera in microsecondi e non costa nulla. Il giudice LLM e' rimandato
a v1.2 (richiede tier middle/wise configurato + budget). La separazione
deontologia/teleologia (binaria/graduata) evita il fenomeno chiamato
"auto-conferma del modello" (cap.11.1 Architettura): se mescolassi le
due in un unico punteggio, un giudice teleologico tenderebbe a
giustificare anche cio' che la guardia bloccherebbe.

API:
    judge(intent, executor_name, args, context) -> Verdict
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import config as _C  # §7.11

VAGLIO_LOG_DIR = _C.PATH_USER_DATA / "vaglio"

# --- Costanti ---------------------------------------------------------------

# Soglia del giudice: sotto questa, l'azione e' negata. Default basso (regime
# benevolente): in v1.1 il vero filtro e' la guardia, il giudice rule-based
# segnala outlier ma non blocca per default.
JUDGE_THRESHOLD = float(os.environ.get("METNOS_JUDGE_THRESHOLD", "0.30"))

# Backend del giudice: "rule-based-v1" (default, deterministico, costo zero) o
# "llm-v1" (usa il tier middle dell'LLMRouter, contesto separato dal proponente,
# prompt sulle 4 Leggi e i telos). LLM e' opt-in via env var perche' costa.
JUDGE_KIND = os.environ.get("METNOS_JUDGE_KIND", "rule-based-v1")

# Forbidden paths: anche solo MENZIONATI in un argomento, l'azione e' negata.
# Lista hard-coded per design (cap.5 Architettura): non viene rilassata da
# alcun livello di autonomia, perche' rappresenta il "nucleo non negoziabile".
_FORBIDDEN_PATH_PATTERNS = [
    re.compile(r"(^|/)\.ssh(/|$)"),
    re.compile(r"^/etc/(passwd|shadow|sudoers)"),
    re.compile(r"^/etc/ssh(/|$)"),
    re.compile(r"^/root(/|$)"),
    re.compile(r"^/boot(/|$)"),
    re.compile(r"^/sys(/|$)"),
    re.compile(r"^/proc(/[0-9]|$)"),
    re.compile(r"^/dev/(sd|nvme|mmcblk|loop)"),
    # Credenziali utente (varianti comuni)
    re.compile(r"\.aws/credentials"),
    re.compile(r"\.config/[^/]+/credentials\.env"),
    re.compile(r"\.gnupg(/|$)"),
]

# Comandi shell quasi-irrecuperabili (Legge 1: no irrecoverable state).
# Si applica solo se executor_name == "shell_exec" o ha capability code:exec.
_DANGEROUS_SHELL_PATTERNS = [
    re.compile(r"\brm\s+-rf?\s+/(\s|$)"),         # rm -rf / (intera radice)
    re.compile(r"\brm\s+-rf?\s+~(\s|$|/)"),       # rm -rf ~
    re.compile(r"\bmkfs\b"),
    re.compile(r"\bdd\s+.*\bof=/dev/"),
    re.compile(r":\s*\(\s*\)\s*\{\s*:\|:&\s*\}"),  # fork bomb
    re.compile(r"\bchmod\s+-?R?\s*0?77[0-9]\s+/"),
]


# --- Tipi -------------------------------------------------------------------

@dataclass
class Verdict:
    approved: bool
    reason: str
    ts: float = field(default_factory=time.time)
    judge_kind: str = "rule-based-v1"
    score: float = 1.0  # rilevante solo se la guardia ha lasciato passare
    blocked_by: str | None = None  # "guard" | "judge" | None


# --- Guardia (binaria) ------------------------------------------------------

def _flatten_str_values(obj) -> list[str]:
    """Estrae ricorsivamente tutti i valori stringa da args (annidati)."""
    out: list[str] = []
    if isinstance(obj, str):
        out.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            out.extend(_flatten_str_values(v))
    elif isinstance(obj, list):
        for v in obj:
            out.extend(_flatten_str_values(v))
    return out


def _expand_user(s: str) -> str:
    return os.path.expanduser(s) if isinstance(s, str) and s.startswith("~") else s


def guard_check(executor_name: str, args: dict, context: dict | None = None) -> tuple[bool, str | None]:
    """Ritorna (ok, reason_se_blocca). True = passa; False = bloccata."""
    args = args or {}
    strs = _flatten_str_values(args)
    expanded = [_expand_user(s) for s in strs]

    # Forbidden paths
    for s in expanded:
        for pat in _FORBIDDEN_PATH_PATTERNS:
            if pat.search(s):
                return False, f"forbidden path violato: pattern {pat.pattern!r} in args"

    # Comandi shell pericolosi (Legge 1)
    if executor_name in ("shell_exec",) or (context or {}).get("capability") == "code:exec":
        cmd = args.get("command") or args.get("cmd") or ""
        if isinstance(cmd, list):
            cmd = " ".join(str(x) for x in cmd)
        if isinstance(cmd, str):
            for pat in _DANGEROUS_SHELL_PATTERNS:
                if pat.search(cmd):
                    return False, f"comando shell quasi-irrecuperabile: {pat.pattern!r}"

    return True, None


# --- Giudice (graduato, rule-based MVP) -------------------------------------

def judge_score(intent: str, executor_name: str, args: dict, context: dict | None = None) -> tuple[float, str]:
    """Score di allineamento in [0,1] + breve rationale.

    Heuristiche conservative: base 0.7, alza per segnali di intento esplicito,
    abbassa per segnali anomali. Niente LLM (rimandato a v1.2).
    """
    score = 0.7
    notes: list[str] = []

    # Bonus: il nome dell'executor e' menzionato (anche parzialmente) nel
    # prompt utente. Segnale di intento esplicito.
    if intent and isinstance(intent, str):
        intent_low = intent.lower()
        for token in re.split(r"[_\s]+", executor_name):
            if len(token) >= 3 and token in intent_low:
                score += 0.1
                notes.append(f"intent menziona '{token}'")
                break

    # Penalita': path traversal sospetto
    for s in _flatten_str_values(args or {}):
        if isinstance(s, str) and ".." in s and "/" in s:
            score -= 0.2
            notes.append("possibile path traversal ('..' in path)")
            break

    # Penalita': args con chiavi insolite (heuristica grezza: contiene caratteri non alfanumerici)
    for k in (args or {}).keys():
        if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", k):
            score -= 0.1
            notes.append(f"chiave args anomala: {k!r}")
            break

    # Bonus: executor non-critical (capability di sola lettura) ha un floor piu' alto
    if (context or {}).get("critical") is False:
        score += 0.05

    # Clamp [0,1]
    score = max(0.0, min(1.0, score))
    reason = "; ".join(notes) if notes else "score base"
    return score, reason


# --- Giudice LLM v1.2 (opt-in) ---------------------------------------------
# Prompt persistito in `runtime/prompts/<lang>/vaglio.j2` (ADR 0092 Phase 2),
# caricato lazy via `prompt_loader.get('vaglio')` ai call site di `_judge_score_llm`.


def _judge_score_llm(intent: str, executor_name: str, args: dict, context: dict | None = None) -> tuple[float, str]:
    """Giudice LLM tier=middle. Ritorna (score, reason).

    Il prompt user contiene SOLO chiavi di args (non valori) per privacy:
    stesso principio del logging JSONL.
    """
    try:
        from llm_router import LLMRouter
    except Exception as e:
        return 0.5, f"llm-judge fallback (router missing): {e}"
    args_keys = sorted((args or {}).keys())
    user = (
        f"Intent dell'utente: {intent or '(non specificato)'}\n"
        f"Executor proposto: {executor_name}\n"
        f"Chiavi argomenti: {args_keys}\n"
        f"Contesto: critical={ctx_get(context, 'critical')}, "
        f"capability={ctx_get(context, 'capability')}, "
        f"step={ctx_get(context, 'step')}\n\n"
        f"Restituisci il punteggio di allineamento."
    )
    try:
        import prompt_loader
        from config import DEFAULT_LANG
        router = LLMRouter()
        # think=False per evitare che Gemma sprechi i 1024 token di reasoning
        # in bullet-list invece di emettere il JSON. Il giudice e' un task
        # procedurale (label + reason breve), niente thinking necessario.
        res = router.chat(prompt_loader.get("vaglio", DEFAULT_LANG), user,
                          tier="middle",
                          max_tokens=120, for_code=False, think=False)
    except Exception as e:
        return 0.5, f"llm-judge fallback (chat failed): {e}"

    text = (res.text or "").strip()
    # Estrai JSON robusto: cerca {"score":...,"reason":...}
    m = re.search(r'\{[^{}]*"score"\s*:\s*([0-9.]+)[^{}]*"reason"\s*:\s*"([^"]*)"[^{}]*\}', text)
    if not m:
        return 0.5, f"llm-judge fallback (parse fail): {text[:80]}"
    try:
        score = max(0.0, min(1.0, float(m.group(1))))
        reason = m.group(2)[:200]
    except Exception:
        return 0.5, "llm-judge fallback (numeric parse)"
    return score, f"llm: {reason}"


def ctx_get(ctx, key, default=None):
    return (ctx or {}).get(key, default) if isinstance(ctx, dict) else default


# --- Orchestrazione ---------------------------------------------------------

def _log(record: dict) -> None:
    try:
        VAGLIO_LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_path = VAGLIO_LOG_DIR / f"{time.strftime('%Y-%m')}.jsonl"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass  # log fail-safe: non blocca mai una decisione


def _action_of(executor_name: str) -> str:
    """Ritorna il verbo (action) dal naming `azione_oggetto[_qualifier]`."""
    if not executor_name or "_" not in executor_name:
        return executor_name or ""
    return executor_name.split("_", 1)[0]


def judge(intent, executor_name, args, context=None) -> Verdict:
    """Orchestratore: prima guardia, poi (se passa) giudice.

    ADR 0107 — short-circuit per executor safe-by-construction: se
    l'action e' in `vocab.SAFE_VERBS` (read-only / pure-compute), la
    guardia ha gia' bloccato i tocchi a forbidden path; non c'e' bisogno
    di chiamare il giudice (rule-based o LLM). Risparmio: ~3-5 s per
    step su safe verbs quando JUDGE_KIND='llm-v1'.
    """
    ctx = context or {}
    args = args or {}

    # Fase 1: guardia (binaria)
    ok, reason = guard_check(executor_name, args, ctx)
    if not ok:
        v = Verdict(approved=False, reason=f"guard: {reason}",
                    score=0.0, blocked_by="guard")
        _log({**asdict(v), "intent": intent, "executor": executor_name,
              "args_keys": sorted(args.keys()),
              "context_keys": sorted(ctx.keys())})
        return v

    # Fase 1.5 (ADR 0107): short-circuit safe-verb. Se l'executor e'
    # read-only/pure-compute, approva senza scomodare il giudice.
    try:
        from vocab import SAFE_VERBS  # lazy import: vocab e' parente
    except Exception:
        SAFE_VERBS = frozenset()
    action = _action_of(executor_name)
    if action in SAFE_VERBS:
        v = Verdict(approved=True,
                    reason=f"safe-verb: {action} (read-only/pure-compute)",
                    score=1.0, blocked_by=None,
                    judge_kind="safe-verb-shortcut")
        _log({**asdict(v), "intent": intent, "executor": executor_name,
              "args_keys": sorted(args.keys()),
              "context_keys": sorted(ctx.keys())})
        return v

    # Fase 2: giudice (graduato). Backend selezionato da JUDGE_KIND.
    if JUDGE_KIND == "llm-v1":
        score, judge_reason = _judge_score_llm(intent, executor_name, args, ctx)
        kind = "llm-v1"
    else:
        score, judge_reason = judge_score(intent, executor_name, args, ctx)
        kind = "rule-based-v1"

    if score < JUDGE_THRESHOLD:
        v = Verdict(approved=False,
                    reason=f"judge: score {score:.2f} < soglia {JUDGE_THRESHOLD:.2f} ({judge_reason})",
                    score=score, blocked_by="judge", judge_kind=kind)
    else:
        v = Verdict(approved=True,
                    reason=f"approved: score {score:.2f} ({judge_reason})",
                    score=score, blocked_by=None, judge_kind=kind)
    _log({**asdict(v), "intent": intent, "executor": executor_name,
          "args_keys": sorted(args.keys()),
          "context_keys": sorted(ctx.keys())})
    return v


def check_cross_user_send(actor_id: str, target_user_id: str | None,
                          channel: str) -> dict:
    """Vaglio cross-user per send_messages (ADR 0084, 4/5/2026).

    Contratto:
    - actor_id == 'host' → permesso (host e' gatekeeper di tutte le risorse).
    - actor_id == target_user_id → permesso (self-send).
    - altrimenti → richiede vaglio one-shot. MVP: deny + reason
      ("guest_to_other_user_requires_vaglio"). Hook futuro: dialog manager
      con prompt "Permetti a <actor> di inviare a <target> via <channel>?".

    Ritorna {allowed: bool, reason: str|None}.
    """
    if not actor_id or actor_id == "host":
        return {"allowed": True, "reason": None}
    if target_user_id and (actor_id == target_user_id):
        return {"allowed": True, "reason": None}
    # Cross-user: per MVP deny. Audit minimo via _log per traccia.
    _log({
        "ts_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "kind": "cross_user_send_blocked",
        "actor": actor_id,
        "target_user_id": target_user_id,
        "channel": channel,
    })
    return {"allowed": False,
            "reason": "guest_to_other_user_requires_vaglio"}


if __name__ == "__main__":
    # Smoke test
    print(judge("leggi il file ~/notes.txt", "read_files", {"path": "/home/u/notes.txt"}))
    print(judge("leggi la chiave", "read_files", {"path": "~/.ssh/id_rsa"}))
    print(judge("cancella tutto", "shell_exec", {"command": "rm -rf /"}))
    print(f"log dir: {VAGLIO_LOG_DIR}")
