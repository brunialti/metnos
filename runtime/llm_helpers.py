"""runtime.llm_helpers — API minimale per executor LLM-augmented.

Pattern terza categoria di executor (28/4/2026, vedi
`feedback_llm_augmented_executors`): un executor riceve dati + un
prompt, dentro chiama un LLM, ritorna testo. Per non duplicare logica
di routing in ogni nuovo executor, esponiamo qui la funzione minima:

    from llm_helpers import call_llm
    from llm_workloads import tier_for
    text, meta = call_llm(
        query, prompt, tier=tier_for("entries.extract"), max_tokens=600)

`query` puo' essere stringa, dict, lista (verra' serializzata in JSON
compatto) o gia' una stringa formattata.

`prompt` e' il system prompt: il mestiere semantico del chiamante (es.
"sintetizza per importanza", "traduci in inglese", "estrai entita'").

`tier` è VIRTUALE e appartiene al vocabolario chiuso di `llm_router`.
I consumer di produzione lo ricavano normalmente da `llm_workloads`; il
default `fast` resta solo per compatibilita' dell'helper generico. Il modello
FISICO dietro ogni tier vive solo nella configurazione centrale.

Capability implicita: `llm:call` (l'executor che usa questo helper
deve dichiararla nel manifest, quando il loader le fara' rispettare).
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any

from llm_provider import LlamaCppProvider, make_provider_from_spec
from llm_router import resolved_tier_spec, tier_endpoint as _tier_endpoint


_OUTPUT_POLICIES = frozenset({"raw", "public"})
_PUBLIC_FORBIDDEN_MARKERS = (
    "<think", "</think", "<|channel", "<channel|>", "INLINE_FORM:",
)


def _postprocess_response(text: Any, output_policy: str) -> str:
    """Provider-neutral response normalization and public-output guard."""

    if output_policy not in _OUTPUT_POLICIES:
        raise ValueError(
            f"unknown LLM output policy {output_policy!r}; "
            f"valid: {sorted(_OUTPUT_POLICIES)}")
    normalized = str(text or "").strip()
    if output_policy == "public":
        folded = normalized.casefold()
        if any(marker.casefold() in folded
               for marker in _PUBLIC_FORBIDDEN_MARKERS):
            raise ValueError("LLM public output contains an internal marker")
    return normalized


def _serialize_query(q: Any, max_chars: int = 12000) -> str:
    if isinstance(q, str):
        return q if len(q) <= max_chars else q[:max_chars] + "\n... [truncated]"
    txt = json.dumps(q, ensure_ascii=False)
    if len(txt) <= max_chars:
        return txt
    # An arbitrary cut turns structured input into invalid JSON and can
    # silently remove trailing provenance while leaving a plausible prefix.
    # Structured consumers must budget complete fields before this boundary.
    raise ValueError(
        "structured LLM payload exceeds max_query_chars "
        f"({len(txt)} > {max_chars})"
    )


# --- Generazione DETERMINISTICA per costruzione (12/6/2026) ------------------
# Diagnosi (vedi memory describe-determinism): il llama-server CONDIVISO non
# e' riproducibile a parita' di richiesta nemmeno con seed fisso §11, temp=0,
# slot pinnato, cache_prompt=false e KV erase dello slot: uno stato interno
# del PROCESSO (avanza a ogni richiesta servita, si azzera solo al riavvio,
# identico cross-backend Vulkan/CPU) sposta i logits di ~0.1 e i near-tie
# greedy flippano — su ~100-400 token liberi il testo cambia quasi sempre.
# Un processo FRESCO e' invece byte-deterministico (llama-completion, 3/3
# hash identici a parita' di prompt). Strada quindi: per le chiamate che
# DEVONO essere riproducibili (describe_entries) si spawna un processo
# llama-completion monouso con: lo stesso GGUF del server (GET /props),
# lo stesso prompt renderizzato dal server (POST /apply-template con
# enable_thinking=false), temp=0 e seed §11. NIENTE template/cache del
# CONTENUTO: la generazione resta LLM piena sui dati correnti. Fallback
# onesto: se binario/server/render mancano, si torna al path HTTP e il
# meta riporta deterministic=false (§2.8, nessuna finta garanzia).

_PROC_TIMEOUT_S = int(os.environ.get("METNOS_LLM_PROC_TIMEOUT_S", "240"))
_END_OF_TEXT_RE = re.compile(r"\s*\[end of text\]\s*$")


def _remaining_budget(deadline_at: float | None, cap: float | None = None
                      ) -> float | None:
    if deadline_at is None:
        return cap
    remaining = deadline_at - time.monotonic()
    if not math.isfinite(remaining) or remaining <= 0:
        raise TimeoutError("LLM request deadline exhausted")
    return remaining if cap is None else min(remaining, cap)


def _completion_bin() -> str | None:
    """Risolve il binario llama-completion: env esplicito > PATH > layout
    convenzionale build llama.cpp sotto $HOME (§7.11: niente path assoluti
    di install-root nel codice; questo e' un tool host, home-relative)."""
    p = os.environ.get("METNOS_LLAMACPP_COMPLETION_BIN", "").strip()
    if p:
        return p if Path(p).is_file() else None
    w = shutil.which("llama-completion")
    if w:
        return w
    cand = Path.home() / "llama.cpp" / "build" / "bin" / "llama-completion"
    return str(cand) if cand.is_file() else None


def _server_model_path(endpoint: str, *, deadline_at: float | None = None
                       ) -> str | None:
    """GGUF servito dal llama-server (GET /props). SoT del modello: la
    generazione deterministica usa LO STESSO modello dei tier §11."""
    try:
        with urllib.request.urlopen(
                f"{endpoint}/props",
                timeout=_remaining_budget(deadline_at, 10)) as r:
            return json.loads(r.read().decode("utf-8")).get("model_path") or None
    except Exception:
        return None


def _render_chat_prompt(endpoint: str, system: str, user: str, *,
                        deadline_at: float | None = None) -> str | None:
    """Prompt renderizzato dal chat template del server (POST
    /apply-template, enable_thinking=false): identico al path HTTP,
    nessun template hardcodato lato Metnos (§7.3)."""
    payload = {
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "chat_template_kwargs": {"enable_thinking": False},
    }
    try:
        req = urllib.request.Request(
            f"{endpoint}/apply-template",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(
                req, timeout=_remaining_budget(deadline_at, 15)) as r:
            return json.loads(r.read().decode("utf-8")).get("prompt") or None
    except Exception:
        return None


def _call_llm_proc(system: str, user: str, *, max_tokens: int,
                   seed: int, endpoint: str | None = None,
                   meta_out: dict | None = None,
                   deadline_at: float | None = None) -> str | None:
    """Generazione byte-deterministica via processo llama-completion
    monouso. Ritorna il testo, o None se il path non e' disponibile
    (il chiamante ricade sul provider HTTP). `endpoint` = llama-server
    dei tier (default: risolto da llm_router.tier_endpoint, NON
    hardcoded). `meta_out` (opzionale): vi deposita `prompt_sha` =
    sha256 del prompt RENDERIZZATO — auditabilita' del determinismo:
    a parita' di prompt_sha+seed l'output DEVE essere identico; se
    varia, l'anomalia e' a valle del prompt (E2E 12/6/2026, caso 1/7)."""
    binary = _completion_bin()
    if not binary:
        return None
    endpoint = endpoint or _tier_endpoint("fast")
    model = _server_model_path(endpoint, deadline_at=deadline_at)
    if not model:
        return None
    rendered = _render_chat_prompt(
        endpoint, system, user, deadline_at=deadline_at)
    if not rendered:
        return None
    if meta_out is not None:
        meta_out["prompt_sha"] = hashlib.sha256(
            rendered.encode("utf-8")).hexdigest()
    # ctx: stima token ~ chars/3 + output + margine; clamp [4096, 32768].
    ctx = min(32768, max(4096, len(rendered) // 3 + max_tokens + 512))
    env = dict(os.environ)
    env.setdefault("AMD_VULKAN_ICD", "RADV")
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".prompt", delete=False,
                                         encoding="utf-8") as tf:
            tf.write(rendered)
            tmp_path = tf.name
        cmd = [
            binary, "-m", model, "-ngl", "999", "-fa", "on",
            "--temp", "0", "-s", str(seed), "-c", str(ctx),
            "-b", "4096", "-ub", "256",
            "-no-cnv", "-f", tmp_path, "-n", str(max_tokens),
            "--no-display-prompt", "--simple-io",
        ]
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=_remaining_budget(deadline_at, _PROC_TIMEOUT_S), env=env)
        if proc.returncode != 0:
            return None
        text = _END_OF_TEXT_RE.sub("", proc.stdout or "").strip()
        return text or None
    except Exception:
        return None
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def call_llm(
    query: Any,
    prompt: str,
    *,
    tier: str = "fast",
    max_tokens: int = 600,
    deterministic: bool = False,
    max_query_chars: int = 12000,
    output_policy: str = "raw",
    timeout_s: float | None = None,
) -> tuple[str, dict]:
    """Chiama il LLM del tier indicato. Ritorna (text, meta).

    Temperature, thinking, and reasoning budget are resolved only from the
    central tier policy. Callers select a logical role; they cannot retain a
    second decoding profile.

    `deterministic=True`: generazione byte-riproducibile via processo
    llama-completion monouso (vedi blocco DETERMINISTICA sopra). Richiede
    temp=0, think=False e seed §11 >= 0; in ogni altro caso, o se il path
    non e' disponibile, ricade sul provider HTTP e `meta["deterministic"]`
    riporta False (onesta' §2.8). Costo: ~+2-5s/chiamata (load processo,
    niente MTP).

    `max_query_chars`: budget di serializzazione del payload (default
    12000). I chiamanti con budget proprio piu' alto (describe_entries,
    §2.7) DEVONO passarlo. Un payload strutturato eccedente viene
    rifiutato: non viene mai troncato a meta' JSON.

    Solleva eccezione se il provider non e' raggiungibile o l'LLM
    risponde vuoto. L'executor chiamante deve gestirla e tradurla in
    una observation `{ok: false, error_code: ERR_EXT_SVC_UNAVAILABLE}`.
    """
    deadline_at = None
    if timeout_s is not None:
        parsed_timeout = float(timeout_s)
        if not math.isfinite(parsed_timeout) or parsed_timeout <= 0:
            raise TimeoutError("LLM request deadline exhausted")
        deadline_at = time.monotonic() + parsed_timeout
    spec = resolved_tier_spec(tier)
    resolved_temperature = float(spec.get("temperature", 0.0))
    resolved_think = spec.get("think")
    reasoning_budget = int(spec.get("reasoning_budget") or 0)
    provider_name = str(spec.get("provider") or "")
    endpoint = _tier_endpoint(tier)
    user_payload = _serialize_query(query, max_chars=max_query_chars)
    if (deterministic and provider_name == "llamacpp"
            and resolved_think is not True
            and resolved_temperature == 0.0):
        _seed = int(os.environ.get("METNOS_LLM_SEED", "42"))
        if _seed >= 0:
            t0 = time.time()
            _proc_meta: dict = {}
            text = _call_llm_proc(prompt, user_payload,
                                  max_tokens=max_tokens, seed=_seed,
                                  endpoint=endpoint, meta_out=_proc_meta,
                                  deadline_at=deadline_at)
            if text is not None:
                # Il contratto d'uscita vale per QUALUNQUE trasporto: senza
                # questa normalizzazione un consumer che chiede insieme
                # `deterministic=True` e `output_policy="public"` riceveva
                # testo non filtrato, cioe' una garanzia dichiarata e non
                # applicata (§2.8). Il ramo HTTP la applicava, questo no.
                return _postprocess_response(text, output_policy), {
                    "tier": tier,
                    "provider": provider_name,
                    "model": spec.get("model") or "local",
                    "in_tokens": 0,
                    "out_tokens": 0,
                    "latency_ms": int((time.time() - t0) * 1000),
                    "deterministic": True,
                    **_proc_meta,  # prompt_sha (audit determinismo)
                }
        # Path deterministico non disponibile: fallback HTTP sotto,
        # dichiarato nel meta.
    # Provider resolution is central too: a logical tier may move to another
    # backend without changing Tutor or executor callers.  Slot affinity is a
    # llama.cpp transport concern and remains confined to this gateway.
    if provider_name == "llamacpp":
        _slot_env = os.environ.get("METNOS_LLM_SLOT_ID", "1").strip()
        _slot = int(_slot_env) if _slot_env.isdigit() else None
        provider = LlamaCppProvider(
            model=spec.get("model") or "local",
            endpoint=endpoint,
            id_slot=_slot,
        )
    else:
        provider = make_provider_from_spec(spec)
    call_kwargs = {
        "max_tokens": max_tokens,
        "temperature": resolved_temperature,
        "think": resolved_think,
    }
    if provider_name == "llamacpp" and resolved_think is True:
        call_kwargs["reasoning_budget"] = max(1, reasoning_budget)
    if deadline_at is not None:
        call_kwargs["request_timeout_s"] = _remaining_budget(deadline_at)
    from llm_telemetry import tier_context

    t0 = time.time()
    with tier_context(tier):
        r = provider.chat(prompt, user_payload, **call_kwargs)
    latency_ms = int((time.time() - t0) * 1000)
    # Single response-normalization and policy point.  Future
    # provider-neutral post-processing belongs here, not in every consumer.
    text = _postprocess_response(r.text, output_policy)
    meta = {
        "tier": tier,
        "provider": getattr(r, "provider", provider_name),
        "model": getattr(r, "model", None) or getattr(provider, "model", ""),
        "in_tokens": r.in_tokens,
        "out_tokens": r.out_tokens,
        "latency_ms": latency_ms,
        "think": resolved_think,
    }
    if deterministic:
        meta["deterministic"] = False
    return text, meta
