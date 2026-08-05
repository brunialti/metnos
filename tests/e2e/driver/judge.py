"""judge.py — LLM-as-judge per qualità semantica risposte agente.

Default ON. Disable via `METNOS_E2E_LLM_JUDGE=0` per test rapidi (solo
lint deterministico).

Modello: Gemma 4 26B locale via llama-server :8080 (compatibile OpenAI).
Prompt letto da `runtime/prompts/<lang>/e2e_judge.j2` come asset
testuale (NO import del codice runtime).

Cache: `.cache/judge/<sha256(query+answer+lang)>.json`. Ogni cache hit
e' istantaneo; cache miss ~5s.

API:
    verdict = await judge.evaluate(query, answer, lang="it")
    # verdict.ok: bool
    # verdict.score: float [0,1]
    # verdict.reason: str
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import aiohttp


_REPO_ROOT = Path(__file__).resolve().parents[3]
_PROMPTS_DIR = _REPO_ROOT / "runtime" / "prompts"
_CACHE_DIR = Path(__file__).resolve().parents[1] / ".cache" / "judge"

_LLM_BASE_URL = os.environ.get("METNOS_LLM_URL", "http://localhost:8080")
_LLM_MODEL = os.environ.get("METNOS_LLM_MODEL", "")  # auto-detect se vuoto


def is_judge_enabled() -> bool:
    """Default ON. Opt-out con `METNOS_E2E_LLM_JUDGE=0`."""
    return os.environ.get("METNOS_E2E_LLM_JUDGE", "1") != "0"


@dataclass
class JudgeVerdict:
    ok: bool
    score: float
    reason: str
    cached: bool = False
    error: Optional[str] = None


def _cache_key(query: str, answer: str, lang: str) -> str:
    h = hashlib.sha256()
    h.update(query.encode("utf-8"))
    h.update(b"\x00")
    h.update(answer.encode("utf-8"))
    h.update(b"\x00")
    h.update(lang.encode("utf-8"))
    return h.hexdigest()


def _cache_path(key: str) -> Path:
    return _CACHE_DIR / f"{key[:2]}" / f"{key}.json"


def _cache_get(key: str) -> Optional[JudgeVerdict]:
    p = _cache_path(key)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text())
        return JudgeVerdict(
            ok=bool(data["ok"]),
            score=float(data["score"]),
            reason=str(data["reason"]),
            cached=True,
        )
    except (json.JSONDecodeError, KeyError, OSError):
        return None


def _cache_put(key: str, verdict: JudgeVerdict) -> None:
    p = _cache_path(key)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "ok": verdict.ok,
        "score": verdict.score,
        "reason": verdict.reason,
    }, ensure_ascii=False))


_FRONTMATTER_RE = re.compile(r"^\{#\s*---.*?---\s*#\}\s*", re.DOTALL)


def _load_prompt(lang: str) -> str:
    """Carica il prompt judge come testo. Strip frontmatter Jinja."""
    p = _PROMPTS_DIR / lang / "e2e_judge.j2"
    if not p.exists():
        # Fallback en
        p = _PROMPTS_DIR / "en" / "e2e_judge.j2"
    if not p.exists():
        raise RuntimeError(f"judge prompt missing: {p}")
    txt = p.read_text(encoding="utf-8")
    return _FRONTMATTER_RE.sub("", txt)


def _render(template: str, **vars) -> str:
    """Mini-Jinja: sostituisce `{{ var }}` letteralmente + `{% if x %}...{% endif %}`.
    No escape (il prompt e' interno, fully controlled)."""
    out = template
    # if blocks (singolo if, no nested)
    def _if_repl(match: re.Match) -> str:
        var = match.group(1).strip()
        body = match.group(2)
        return body if vars.get(var) else ""
    out = re.sub(
        r"\{%\s*if\s+(\w+)\s*%\}(.*?)\{%\s*endif\s*%\}",
        _if_repl, out, flags=re.DOTALL,
    )
    # variables
    for k, v in vars.items():
        out = out.replace("{{ " + k + " }}", str(v or ""))
    return out


_JSON_BLOCK_RE = re.compile(r"\{[^{}]*?\"ok\"\s*:.*?\}", re.DOTALL)


def _parse_verdict(raw: str) -> JudgeVerdict:
    """Estrae JSON dal raw response (tollerante a leading/trailing text)."""
    try:
        data = json.loads(raw.strip())
    except json.JSONDecodeError:
        m = _JSON_BLOCK_RE.search(raw)
        if not m:
            return JudgeVerdict(
                ok=False, score=0.0,
                reason="judge output non parsabile",
                error=raw[:300],
            )
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return JudgeVerdict(
                ok=False, score=0.0,
                reason="judge JSON malformato",
                error=raw[:300],
            )
    try:
        return JudgeVerdict(
            ok=bool(data.get("ok", False)),
            score=float(data.get("score", 0.0)),
            reason=str(data.get("reason", "")),
        )
    except (TypeError, ValueError) as exc:
        return JudgeVerdict(
            ok=False, score=0.0,
            reason="judge schema invalido",
            error=f"{exc}: {data}",
        )


async def evaluate(
    query: str, answer: str, *,
    lang: str = "it",
    context: Optional[str] = None,
    timeout_s: float = 30.0,
) -> JudgeVerdict:
    """Valuta `(query, answer)` con LLM judge cached.

    Se `METNOS_E2E_LLM_JUDGE=0`, ritorna verdetto ok=True automatico
    (caller deve usare il lint deterministico per la validazione).
    """
    if not is_judge_enabled():
        return JudgeVerdict(
            ok=True, score=1.0,
            reason="judge LLM disabled (METNOS_E2E_LLM_JUDGE=0)",
        )
    key = _cache_key(query, answer, lang)
    cached = _cache_get(key)
    if cached is not None:
        return cached

    template = _load_prompt(lang)
    prompt = _render(template,
                       user_query=query, agent_answer=answer,
                       context=context or "")
    payload = {
        "messages": [
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 256,
        "temperature": 0.0,  # deterministic
        # Gemma 4 thinking mode: con `enable_thinking=True` Gemma scrive
        # reasoning_content e lascia content vuoto. Il judge produce JSON
        # diretto, niente thinking utile → disable.
        "chat_template_kwargs": {"enable_thinking": False},
    }
    if _LLM_MODEL:
        payload["model"] = _LLM_MODEL

    timeout = aiohttp.ClientTimeout(total=timeout_s)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                _LLM_BASE_URL + "/v1/chat/completions",
                json=payload,
            ) as r:
                if r.status >= 400:
                    text = await r.text()
                    return JudgeVerdict(
                        ok=False, score=0.0,
                        reason=f"llama-server HTTP {r.status}",
                        error=text[:300],
                    )
                data = await r.json()
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        return JudgeVerdict(
            ok=False, score=0.0,
            reason="llama-server unreachable",
            error=str(exc)[:300],
        )
    try:
        raw = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return JudgeVerdict(
            ok=False, score=0.0,
            reason="llama-server schema unexpected",
            error=str(data)[:300],
        )
    verdict = _parse_verdict(raw)
    if verdict.error is None:
        _cache_put(key, verdict)
    return verdict
