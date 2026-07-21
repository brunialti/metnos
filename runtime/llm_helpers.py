"""runtime.llm_helpers — API minimale per executor LLM-augmented.

Pattern terza categoria di executor (28/4/2026, vedi
`feedback_llm_augmented_executors`): un executor riceve dati + un
prompt, dentro chiama un LLM, ritorna testo. Per non duplicare logica
di routing in ogni nuovo executor, esponiamo qui la funzione minima:

    from llm_helpers import call_llm
    text, meta = call_llm(query, prompt, tier='middle', max_tokens=600)

`query` puo' essere stringa, dict, lista (verra' serializzata in JSON
compatto) o gia' una stringa formattata.

`prompt` e' il system prompt: il mestiere semantico del chiamante (es.
"sintetizza per importanza", "traduci in inglese", "estrai entita'").

`tier` è VIRTUALE: 'fast' / 'middle' / 'wise'. Default 'middle' per
sintesi/classificazione/scrittura breve. Il modello FISICO dietro ogni
tier (datato) vive solo in `llm_router.py::DEFAULT_TIERS`; qui si parla
solo di tier.

Capability implicita: `llm:call` (l'executor che usa questo helper
deve dichiararla nel manifest, quando il loader le fara' rispettare).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any

from llm_provider import LlamaCppProvider
from llm_router import tier_endpoint as _tier_endpoint

TIER_MODELS = {
    # Tier VIRTUALI → placeholder "local": llama-server serve il GGUF
    # caricato e ignora il campo model. Il mapping tier→modello FISICO
    # (datato) sta solo in llm_router.py::DEFAULT_TIERS.
    "fast": "local",
    "middle": "local",
    "wise": "local",
}


def _serialize_query(q: Any, max_chars: int = 12000) -> str:
    if isinstance(q, str):
        return q if len(q) <= max_chars else q[:max_chars] + "\n... [truncated]"
    txt = json.dumps(q, ensure_ascii=False)
    if len(txt) <= max_chars:
        return txt
    return txt[:max_chars] + "\n... [truncated]"


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


def _server_model_path(endpoint: str) -> str | None:
    """GGUF servito dal llama-server (GET /props). SoT del modello: la
    generazione deterministica usa LO STESSO modello dei tier §11."""
    try:
        with urllib.request.urlopen(f"{endpoint}/props", timeout=10) as r:
            return json.loads(r.read().decode("utf-8")).get("model_path") or None
    except Exception:
        return None


def _render_chat_prompt(endpoint: str, system: str, user: str) -> str | None:
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
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode("utf-8")).get("prompt") or None
    except Exception:
        return None


def _call_llm_proc(system: str, user: str, *, max_tokens: int,
                   seed: int, endpoint: str | None = None,
                   meta_out: dict | None = None) -> str | None:
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
    endpoint = endpoint or _tier_endpoint("middle")
    model = _server_model_path(endpoint)
    if not model:
        return None
    rendered = _render_chat_prompt(endpoint, system, user)
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
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=_PROC_TIMEOUT_S, env=env)
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
    tier: str = "middle",
    max_tokens: int = 600,
    temperature: float = 0.0,
    think: bool = False,
    deterministic: bool = False,
    max_query_chars: int = 12000,
) -> tuple[str, dict]:
    """Chiama il LLM del tier indicato. Ritorna (text, meta).

    `deterministic=True`: generazione byte-riproducibile via processo
    llama-completion monouso (vedi blocco DETERMINISTICA sopra). Richiede
    temp=0, think=False e seed §11 >= 0; in ogni altro caso, o se il path
    non e' disponibile, ricade sul provider HTTP e `meta["deterministic"]`
    riporta False (onesta' §2.8). Costo: ~+2-5s/chiamata (load processo,
    niente MTP).

    `max_query_chars`: budget di serializzazione del payload (default
    12000). I chiamanti con budget proprio piu' alto (describe_entries,
    §2.7) DEVONO passarlo, altrimenti il bundle viene troncato qui in
    silenzio a meta' JSON.

    Solleva eccezione se il provider non e' raggiungibile o l'LLM
    risponde vuoto. L'executor chiamante deve gestirla e tradurla in
    una observation `{ok: false, error_code: ERR_EXT_SVC_UNAVAILABLE}`.
    """
    if tier not in TIER_MODELS:
        raise ValueError(f"unknown tier {tier!r}; valid: {list(TIER_MODELS)}")
    model = TIER_MODELS[tier]
    # Endpoint dei tier: SoT llm_router.tier_endpoint (llm_tiers.toml;
    # LOCAL_DEFAULT_ENDPOINT solo come ultimo default). Un solo punto di
    # verita' per TUTTI i consumer: provider HTTP + path deterministico.
    endpoint = _tier_endpoint(tier)
    user_payload = _serialize_query(query, max_chars=max_query_chars)
    if deterministic and not think and temperature == 0.0:
        _seed = int(os.environ.get("METNOS_LLM_SEED", "42"))
        if _seed >= 0:
            t0 = time.time()
            _proc_meta: dict = {}
            text = _call_llm_proc(prompt, user_payload,
                                  max_tokens=max_tokens, seed=_seed,
                                  endpoint=endpoint, meta_out=_proc_meta)
            if text is not None:
                return text, {
                    "tier": tier,
                    "model": model,
                    "in_tokens": 0,
                    "out_tokens": 0,
                    "latency_ms": int((time.time() - t0) * 1000),
                    "deterministic": True,
                    **_proc_meta,  # prompt_sha (audit determinismo)
                }
        # Path deterministico non disponibile: fallback HTTP sotto,
        # dichiarato nel meta.
    # ADR 0120: slot affinity. Default Metnos = id_slot=1 (image enrichment
    # batch). Override via env var METNOS_LLM_SLOT_ID. None disabilita.
    _slot_env = os.environ.get("METNOS_LLM_SLOT_ID", "1").strip()
    _slot = int(_slot_env) if _slot_env.isdigit() else None
    provider = LlamaCppProvider(model=model, endpoint=endpoint, id_slot=_slot)
    t0 = time.time()
    r = provider.chat(prompt, user_payload, max_tokens=max_tokens,
                      temperature=temperature, think=think)
    latency_ms = int((time.time() - t0) * 1000)
    text = (r.text or "").strip()
    meta = {
        "tier": tier,
        "model": model,
        "in_tokens": r.in_tokens,
        "out_tokens": r.out_tokens,
        "latency_ms": latency_ms,
    }
    if deterministic:
        meta["deterministic"] = False
    return text, meta
