"""Stage 6 (semantic verification) — Layer 6 di synth admission policy
(ADR 0114, 8/5/2026 sera).

Bug live 8/5: synth `find_texts` aveva `description` "motori di ricerca
online" ma il `code` faceva tutt'altro. Stage 6 confronta description vs
code via LLM (tier wise = Gemma 4 26B locale) e rifiuta i misalignments.

Determinismo §7.9: solo JSON parsing strict, retry 1x su malformed,
fallback `aligned=False` (fail-safe — meglio rifiutare un buon synth che
ammettere uno fasullo). Multi-model consensus optional via env
`LLM_VERIFY_MODELS=model1,model2,...`.

Audit append a `~/.local/share/metnos/synth_audit/verify_<ts>_<hash>.jsonl`.

NB: il modulo si chiama `synt_stage6_verify` (NON `synt.stage6_verify`)
perche' c'e' gia' un module top-level `runtime/synt.py` (orchestrator
synth originale). Naming flat coerente con il resto del corpus runtime.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Callable

import config as _C  # §7.11

VERIFY_AUDIT_DIR = _C.PATH_USER_DATA / "synth_audit"

VERIFY_PROMPT_TEMPLATE = """Sei un revisore stretto di executor Metnos. Confronta DESCRIPTION e CODE.

DESCRIPTION dichiara:
{description}

CODE body:
{code_body}

Output JSON SOLO (niente preamble): {{"aligned": bool, "mismatch": "spiegazione max 200 char"}}.
- aligned=true se il code esegue ESATTAMENTE quello che la description promette.
- aligned=false se il code non corrisponde, copre solo una parte, o fa cose extra non documentate.
"""


def _parse_verify_json(text: str) -> dict | None:
    """Parser strict: tenta `json.loads`. In caso di preamble, prova
    estrazione substring `{...}` (greedy) come ultima chance. Ritorna
    `None` se tutto fallisce — caller usa fail-safe."""
    if not text or not text.strip():
        return None
    s = text.strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    # Best-effort substring
    start = s.find("{")
    end = s.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(s[start:end + 1])
        except json.JSONDecodeError:
            return None
    return None


def _normalize_verdict(parsed: dict | None) -> dict:
    """Canonicalizza l'output del LLM. Fallback fail-safe (aligned=False)
    se parsed e' None o malformato."""
    if not isinstance(parsed, dict):
        return {"aligned": False, "mismatch": "parser fallback (malformed JSON)"}
    aligned = bool(parsed.get("aligned", False))
    mismatch = str(parsed.get("mismatch") or "")[:200]
    return {"aligned": aligned, "mismatch": mismatch}


def _audit_path(name_hint: str) -> Path:
    """Path del log audit per una verify run. Crea dir lazy."""
    VERIFY_AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    h = hashlib.sha256(name_hint.encode("utf-8", errors="replace")).hexdigest()[:8]
    return VERIFY_AUDIT_DIR / f"verify_{ts}_{h}.jsonl"


_VERIFY_SYSTEM_DEFAULT = (
    "Sei un revisore stretto di executor Metnos. Rispondi SOLO con il JSON "
    "richiesto, nessun preamble."
)


def _default_llm_call(prompt: str, model: str) -> dict:
    """LLM call wrapper: usa `LLMRouter().provider(<tier>).chat(...)`.
    Ritorna dict con `text`. Determinismo §7.9: nessun fallback silente —
    se LLM offline, l'eccezione propaga e il caller (`_single_verify`) usa
    fail-safe.

    NB (1/6/2026): `llm_router` NON espone una `call()` module-level — solo
    la classe `LLMRouter` con `.chat()`. Il vecchio `from llm_router import
    call` falliva con ImportError ad OGNI invocazione, mandando in fail-safe
    `aligned=False` qualunque import di skill (L6 sempre rigettante). Allineato
    al pattern di `skill_description_llm._call_llm` (stesso stadio pipeline)."""
    from llm_router import LLMRouter  # type: ignore
    router = LLMRouter()
    provider = router.provider(model)
    res = provider.chat(
        _VERIFY_SYSTEM_DEFAULT, prompt,
        max_tokens=300, temperature=0, think=False,
    )
    return {"text": getattr(res, "text", None) or ""}


def verify_semantic_alignment(
    description: str,
    code_body: str,
    *,
    timeout_s: float = 5.0,
    llm_call: Callable[[str, str], dict] | None = None,
    name_hint: str = "verify",
) -> dict:
    """Verifica che il `code_body` esegua quello che `description` dichiara.

    Args:
        description: testo della description (manifest [description].it).
        code_body: testo del file `<name>.py` (corpo della funzione `invoke`).
        timeout_s: timeout per LLM call. Default 5s.
        llm_call: opzionale, fn `(prompt, model) -> {"text": str}`. Se None
                 usa `runtime.llm_router.call`.
        name_hint: stringa breve per audit log (tipicamente nome executor).

    Returns:
        {
          "aligned": bool,
          "mismatch": str (spiegazione, max 200 char),
          "model": str (modello consultato; "consensus:N" se multi-model),
          "raw": dict | None (raw output JSON parsed; None se malformato),
        }

    Behavior:
        - Single-model di default. Multi-model consensus se env
          LLM_VERIFY_MODELS impostato (lista comma-separata): majority wins.
        - Retry 1x su malformed JSON.
        - Fallback fail-safe `aligned=False` se 2x parse fail.
        - Audit append per ogni verify call (PROMPT + RESPONSE + parsed).
    """
    prompt = VERIFY_PROMPT_TEMPLATE.format(
        description=description.strip(),
        code_body=code_body.strip(),
    )
    if llm_call is None:
        llm_call = _default_llm_call

    models_env = os.environ.get("LLM_VERIFY_MODELS", "").strip()
    models: list[str] = (
        [m.strip() for m in models_env.split(",") if m.strip()]
        if models_env else ["wise"]
    )

    # Single model path (default)
    if len(models) == 1:
        model = models[0]
        verdict, raw_text = _single_verify(prompt, model, llm_call)
        out = {**verdict, "model": model, "raw": _parse_verify_json(raw_text or "")}
        _write_audit(name_hint, prompt, raw_text, verdict, [model])
        return out

    # Multi-model consensus path
    verdicts: list[tuple[str, dict, str]] = []  # (model, verdict, raw_text)
    for m in models:
        v, raw_text = _single_verify(prompt, m, llm_call)
        verdicts.append((m, v, raw_text))
    # Majority wins on `aligned` field; tie → False (fail-safe).
    aligned_count = sum(1 for _, v, _ in verdicts if v.get("aligned"))
    misaligned_count = len(verdicts) - aligned_count
    final_aligned = aligned_count > misaligned_count
    # Concatena mismatch reasons di chi disagrees con il vincitore
    losers = [v for _, v, _ in verdicts
              if v.get("aligned") != final_aligned]
    mismatch = "; ".join(v.get("mismatch", "") for v in losers if v.get("mismatch")) \
              or (verdicts[0][1].get("mismatch", "") if not final_aligned else "")
    out = {
        "aligned": final_aligned,
        "mismatch": mismatch[:200],
        "model": f"consensus:{len(models)}",
        "raw": [{"model": m, "verdict": v} for m, v, _ in verdicts],
    }
    _write_audit(name_hint, prompt, "\n---\n".join(rt for _, _, rt in verdicts),
                 {"aligned": final_aligned, "mismatch": mismatch[:200]},
                 [m for m, _, _ in verdicts])
    return out


def _single_verify(prompt: str, model: str, llm_call: Callable) -> tuple[dict, str]:
    """Esegue una verify singola (1 modello). Retry 1x su malformed JSON.
    Ritorna (verdict, raw_text)."""
    raw_text = ""
    for attempt in range(2):  # 1 tentativo + 1 retry
        try:
            res = llm_call(prompt, model)
        except Exception:
            res = None
        raw_text = (res or {}).get("text") or ""
        parsed = _parse_verify_json(raw_text)
        if parsed is not None:
            return _normalize_verdict(parsed), raw_text
    # 2x fail → fail-safe: aligned=False
    return {"aligned": False, "mismatch": "parser fallback (malformed JSON)"}, raw_text


def _write_audit(name_hint: str, prompt: str, response: str | None,
                 verdict: dict, models: list[str]) -> None:
    """Append-only audit. Best-effort: errori OS non interrompono la verify."""
    try:
        path = _audit_path(name_hint)
        line = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "name_hint": name_hint,
            "models": models,
            "prompt_len": len(prompt),
            "response_len": len(response or ""),
            "verdict": verdict,
        }
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")
    except OSError:
        pass
