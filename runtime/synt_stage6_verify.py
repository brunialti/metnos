"""Stage 6 (semantic verification) — Layer 6 di synth admission policy
(ADR 0114, 8/5/2026 sera).

Bug live 8/5: synth `find_texts` aveva `description` "motori di ricerca
online" ma il `code` faceva tutt'altro. Stage 6 confronta description vs
code via LLM (workload ``synt.semantic_verify`` → ``wise``) e rifiuta
i misalignments.

Determinismo §7.9: solo JSON parsing strict, retry 1x su malformed,
un payload malformato o un servizio indisponibile sollevano un errore tipizzato
e il chiamante rifiuta il candidato. Un eventuale consenso multi-modello viene
iniettato dalla politica versionata, mai dall'ambiente.

Audit append a `~/.local/share/metnos/synth_audit/verify_<ts>_<hash>.jsonl`.

NB: il modulo si chiama `synt_stage6_verify` (NON `synt.stage6_verify`)
perche' c'e' gia' un module top-level `runtime/synt.py` (orchestrator
synth originale). Naming flat coerente con il resto del corpus runtime.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Callable

import config as _C  # §7.11
from llm_workloads import tier_for

VERIFY_AUDIT_DIR = _C.PATH_USER_DATA / "synth_audit"
_VERDICT_KEYS = frozenset({"aligned", "mismatch"})
_VERDICT_ENVELOPE_KEYS = _VERDICT_KEYS | {"model", "raw"}


class SemanticVerdictInvalid(ValueError):
    """Il payload del revisore non rispetta il contratto tipizzato."""


def validate_stage6_verdict(value: object) -> dict:
    """Valida il payload autorevole legacy di Stage 6 senza coercizioni."""
    if not isinstance(value, dict) or not _VERDICT_KEYS.issubset(value):
        raise SemanticVerdictInvalid("keys")
    if not set(value).issubset(_VERDICT_ENVELOPE_KEYS):
        raise SemanticVerdictInvalid("extra_keys")
    aligned = value["aligned"]
    mismatch = value["mismatch"]
    if type(aligned) is not bool or not isinstance(mismatch, str):
        raise SemanticVerdictInvalid("types")
    if "\x00" in mismatch or len(mismatch.encode("utf-8")) > 200:
        raise SemanticVerdictInvalid("mismatch_length")
    if aligned and mismatch:
        raise SemanticVerdictInvalid("aligned_with_mismatch")
    if not aligned and not mismatch.strip():
        raise SemanticVerdictInvalid("misaligned_without_reason")
    return {"aligned": aligned, "mismatch": mismatch}

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
    return validate_stage6_verdict(parsed)


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
        max_tokens=300,
    )
    return {"text": getattr(res, "text", None) or ""}


def _request_label(request: str) -> str:
    """Human/audit label for a logical request, including a fast level."""
    level = getattr(request, "level", None)
    return f"{request}.{level}" if level else str(request)


def verify_semantic_alignment(
    description: str,
    code_body: str,
    *,
    timeout_s: float = 5.0,
    llm_call: Callable[[str, str], dict] | None = None,
    name_hint: str = "verify",
    models: tuple[str, ...] | None = None,
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
        - Un solo modello dal router per default; una tupla esplicita e
          versionata può richiedere consenso multi-modello.
        - Retry 1x su malformed JSON.
        - Dopo due payload malformati solleva `SemanticVerdictInvalid`.
        - Audit append per ogni verify call (PROMPT + RESPONSE + parsed).
    """
    prompt = VERIFY_PROMPT_TEMPLATE.format(
        description=description.strip(),
        code_body=code_body.strip(),
    )
    if llm_call is None:
        llm_call = _default_llm_call

    selected_models = models or (tier_for("synt.semantic_verify"),)
    if not selected_models or any(
        not isinstance(model, str) or not model.strip()
        for model in selected_models
    ):
        raise ValueError("models must be a non-empty tuple of non-empty strings")

    # Single model path (default)
    if len(selected_models) == 1:
        model = selected_models[0]
        verdict, raw_text = _single_verify(prompt, model, llm_call)
        out = {**verdict, "model": _request_label(model),
               "raw": _parse_verify_json(raw_text or "")}
        _write_audit(name_hint, prompt, raw_text, verdict, [model])
        return out

    # Multi-model consensus path
    verdicts: list[tuple[str, dict, str]] = []  # (model, verdict, raw_text)
    for m in selected_models:
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
        "model": f"consensus:{len(selected_models)}",
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
            try:
                return _normalize_verdict(parsed), raw_text
            except SemanticVerdictInvalid:
                continue
    # 2x fail → fail-safe: aligned=False
    raise SemanticVerdictInvalid("malformed_or_invalid_after_retry")


def _write_audit(name_hint: str, prompt: str, response: str | None,
                 verdict: dict, models: list[str]) -> None:
    """Append-only audit. Best-effort: errori OS non interrompono la verify."""
    from audit_jsonl import append_jsonl
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
        append_jsonl(path, line)
    except OSError:
        pass
