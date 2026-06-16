#!/usr/bin/env python3
"""nlu — centralized phrase->structured-JSON extraction (schema-constrained LLM).

One spec, all languages: a single JSON-schema FRAME constrains the OUTPUT; the
input in ANY language is parsed by the multilingual model. Drop-in replacement
for the pre-wired detection regex (ordering / time-window / recurrence /
count / visualize). Deterministic (§7.9: temp 0 + fixed seed); the JSON is
valid by construction (constrained decoding).

INERT BY DEFAULT. The gate `METNOS_NLU` makes rollout first-class and
regression-proof — nothing changes behavior until explicitly enabled:
    off    (default) -> frame() is None; adapters return their fallback (== today).
    shadow           -> frame is computed and logged vs the fallback, but the
                        behavior stays the fallback (collect agreement, live, 0 risk).
    llm              -> adapters return the frame; fallback only if the model is down.

Adding a slot = one schema field + one 2-line adapter (see bottom). The prompt
is English (language-independent function), imperative, pattern-oriented
(placeholders, never copy-able literals).
"""
from __future__ import annotations

import json
import os
import urllib.request
from collections import OrderedDict
from typing import Callable, Optional

import i18n as _i18n
from llm_router import tier_endpoint
from logging_setup import get_logger

log = get_logger("metnos.nlu")

_SEED = int(os.environ.get("METNOS_LLM_SEED", "42"))
_CACHE_CAP = 256
_cache: "OrderedDict[tuple, Optional[dict]]" = OrderedDict()


def _gate() -> str:
    return os.environ.get("METNOS_NLU", "off").lower()


# ── one spec: the semantic frame (language-independent OUTPUT) ───────────────
SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "ordering": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "mode": {"type": "string", "enum": ["none", "sort", "group"]},
                "key": {"type": "string"},
                "desc": {"type": "boolean"}},
            "required": ["mode", "key", "desc"]},
        "time_window": {"type": "string"},
        "recurrence": {
            "type": "object", "additionalProperties": False,
            "properties": {"every": {"type": "string"},
                           "at": {"type": "string"}},
            "required": ["every", "at"]},
        "count_intent": {"type": "boolean"},
        "visualize_intent": {"type": "boolean"},
    },
    "required": ["ordering", "time_window", "recurrence",
                 "count_intent", "visualize_intent"],
}

INSTRUCTION = (
    "Extract NLU slots from a user phrase in ANY language. Explicit slots only; "
    "precision over recall; empty/none/false when unsure. Output JSON only.\n"
    "ordering.mode: 'sort' ONLY on an explicit sort verb "
    "(ordina|sort|order|trie|ordena|sortieren); 'group' ONLY on an explicit "
    "group verb (raggruppa|group by|gruppiere|agrupa); else 'none'. Never infer "
    "from adjectives (new|recent|important). ordering.key: the field or ''. "
    "ordering.desc: true only if descending.\n"
    "time_window: ONLY on an explicit time reference; normalize to "
    "today|last-<N>d|last-<N>h|next-<N>d|next-<N>h|YYYY-MM-DD..YYYY-MM-DD, "
    "N = number from the phrase, keep past(last)/future(next); else ''.\n"
    "recurrence.every: ONLY on explicit repetition, pattern <N>m|<N>h|<N>d|<N>w; "
    "else ''. recurrence.at: 'HH:MM' or ''.\n"
    "count_intent: true ONLY on an explicit quantity ask "
    "(quanti|how many|conta). visualize_intent: true ONLY on an explicit show "
    "verb (mostra|visualizza|show|display); never for list|read|summarize.\n"
    "Invent nothing."
)


def _extract(q: str) -> Optional[dict]:
    """One constrained, deterministic round-trip. None on any failure."""
    payload = {
        "model": "local", "temperature": 0, "seed": _SEED, "max_tokens": 200,
        "chat_template_kwargs": {"enable_thinking": False},
        "response_format": {"type": "json_schema",
                            "json_schema": {"name": "nlu", "schema": SCHEMA,
                                            "strict": True}},
        "messages": [{"role": "system", "content": INSTRUCTION},
                     {"role": "user", "content": q}],
    }
    url = tier_endpoint("fast").rstrip("/") + "/v1/chat/completions"
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        resp = json.load(urllib.request.urlopen(req, timeout=30))
        return json.loads(resp["choices"][0]["message"]["content"])
    except Exception as ex:  # model down / invalid JSON -> caller falls back
        log.warning("nlu: extraction failed (%s); falling back", type(ex).__name__)
        return None


def frame(query: str) -> Optional[dict]:
    """Schema-constrained frame, memoized per (query, lang). None if gate is
    off/regex or the model is unavailable. One call shared by all adapters."""
    q = (query or "").strip()
    if not q or _gate() in ("off", "regex"):
        return None
    key = (q, _i18n.current_lang())
    if key in _cache:
        _cache.move_to_end(key)
        return _cache[key]
    out = _extract(q)
    _cache[key] = out
    if len(_cache) > _CACHE_CAP:
        _cache.popitem(last=False)
    return out


def clear_cache() -> None:
    _cache.clear()


# ── drop-in adapters: identical shapes to the legacy regex functions ─────────
def _decide(query: str, fallback: Optional[Callable], project: Callable):
    """Single point for gate + shadow + fallback. `project` maps frame->shape."""
    fb = fallback(query) if fallback else None
    g = _gate()
    if g in ("off", "regex"):
        return fb
    fr = frame(query)
    val = project(fr) if fr is not None else fb
    if g == "shadow":
        if val != fb:
            log.info("nlu shadow diff q=%r llm=%r regex=%r", query, val, fb)
        return fb                       # shadow never changes behavior
    return val                          # llm (fallback already applied if fr None)


def ordering(query: str, *, fallback: Callable | None = None) -> Optional[dict]:
    def p(f):
        o = f.get("ordering") or {}
        if o.get("mode", "none") == "none":
            return None
        return {"mode": o["mode"], "key_text": (o.get("key") or "").strip().lower(),
                "desc": bool(o.get("desc"))}
    return _decide(query, fallback, p)


def time_window(query: str, *, fallback: Callable | None = None) -> Optional[str]:
    return _decide(query, fallback,
                   lambda f: (f.get("time_window") or "").strip() or None)


def recurrence(query: str, *, fallback: Callable | None = None) -> Optional[dict]:
    def p(f):
        r = f.get("recurrence") or {}
        ev = (r.get("every") or "").strip()
        return {"every": ev, "at": (r.get("at") or "").strip()} if ev else None
    return _decide(query, fallback, p)


def count_intent(query: str, *, fallback: Callable | None = None) -> bool:
    # bool contract: never None (no signal / no fallback -> False).
    return bool(_decide(query, fallback, lambda f: bool(f.get("count_intent"))))


def visualize_intent(query: str, *, fallback: Callable | None = None) -> bool:
    return bool(_decide(query, fallback, lambda f: bool(f.get("visualize_intent"))))


if __name__ == "__main__":
    import sys
    os.environ.setdefault("METNOS_NLU", "llm")
    clear_cache()
    print(json.dumps(frame(" ".join(sys.argv[1:]) or
                           "ordina le mail per data, le piu' recenti prima"),
                     ensure_ascii=False, indent=2))
