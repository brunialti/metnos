"""github_dedup — semantic dedup logic per il watcher GitHub (Fase D).

Quattro responsabilita' indipendenti:
  1. `embed_query(text)` — wrapper BGE-M3 con riuso singleton (ADR 0134).
  2. `classify_hint(title, body)` — keyword match deterministico §7.9.
  3. `check_4_and_safety(top_match, hint)` — 4 condizioni AND per auto-reply.
  4. `format_auto_reply_body(top_matches, similarity_band)` — testo finale.

Determinismo §7.9: nessun LLM. Le 4 funzioni sono pure (no side-effect).
"""
from __future__ import annotations

import logging
from typing import Any

import numpy as np


_LOG = logging.getLogger(__name__)


# Soglie standard (override via ~/.config/metnos/github_dedup.json).
SIM_AUTO_REPLY = 0.85       # condizione AND #1
SIM_HIGH_BAND = 0.92        # >=: single-ref reply; tra 0.85-0.92: 2-3 ref
MIN_ACCEPTED_REPLY_LEN = 100  # condizione AND #4


# Keyword match deterministico (ADR github_provider §6.5). Primo match vince.
_CLASS_KEYWORDS: dict[str, list[str]] = {
    "bug": [
        "bug", "error", "crash", "exception", "doesn't work",
        "broken", "regression", "traceback", "nameerror",
        "errore", "non funziona", "crasha",
    ],
    "support": [
        "how to", "come si fa", "documentazione", "esempio",
        "can i", "tutorial", "guida",
    ],
    "enhancement": [
        "feature", "would be nice", "proposal",
        "enhancement", "potresti aggiungere",
    ],
    # `question` deve esistere: il config utente (`classify_for_auto_reply`) e
    # `check_4_and_safety` accettano "question" per l'auto-reply. Senza questo
    # bucket la classe era IRRAGGIUNGIBILE → config inerte (bug 1/6/2026).
    "question": [
        "how do i", "is it possible", "is there a way", "what is",
        "why does", "can someone", "any idea", "?",
        "come mai", "perche'", "perché", "qual e'", "qual è",
        "cosa significa", "si puo'", "si può", "è possibile",
    ],
}


def embed_query(text: str) -> np.ndarray | None:
    """Embed BGE-M3 1024d L2-normalized. Ritorna None se BGE non disponibile
    (degrade silent: il watcher fallback a flow gate normale).

    Riusa il singleton di `runtime/affinity_semantic.py::_get_embedder()`
    (ADR 0134, regola del 3 §7.2: no doppione di lazy init).
    """
    if not text or not text.strip():
        return None
    try:
        # Riuso del singleton di affinity_semantic per evitare doppio caricamento
        # del modello (la cache embedder e' lock-protected).
        from affinity_semantic import _get_embedder  # type: ignore
        emb = _get_embedder()
        if emb is None:
            return None
        return emb.embed_query(text)
    except Exception as e:
        _LOG.info("github_dedup: embed_query fail (%r)", e)
        return None


def classify_hint(title: str | None, body: str | None) -> str:
    """Match keyword case-insensitive. Primo bucket che ha hit vince.
    No match → 'unknown' (flow gate normale, mai auto-reply).

    Ordine di preferenza: bug > support > question > enhancement.
    Bug ha priorita' perche' anche una keyword bug singola in mezzo a
    una richiesta di documentazione deve sospettare un bug nascosto.
    `question` dopo `support` (doc-seeking ha precedenza su interrogativo
    generico) ma prima di `enhancement`."""
    text = " ".join(filter(None, [title or "", body or ""])).lower()
    if not text.strip():
        return "unknown"
    for cls in ("bug", "support", "question", "enhancement"):
        for kw in _CLASS_KEYWORDS[cls]:
            if kw in text:
                return cls
    return "unknown"


def check_4_and_safety(
    top_match: dict[str, Any] | None,
    classification_hint: str,
    min_similarity: float | None = None,
) -> bool:
    """4 condizioni AND. Una sola falsa → False (flow gate normale).
    Vedi github_provider_architecture §6.3.

    1. top.similarity >= soglia (`min_similarity` da config `auto_reply_threshold`,
       default SIM_AUTO_REPLY)
    2. classification_hint in {'support', 'question'}
    3. top.user_satisfied == 1
    4. top.accepted_reply length > 100
    """
    if not top_match:
        return False
    threshold = SIM_AUTO_REPLY if min_similarity is None else float(min_similarity)
    sim = float(top_match.get("similarity") or 0.0)
    if sim < threshold:
        return False
    if classification_hint not in {"support", "question"}:
        return False
    if int(top_match.get("user_satisfied") or 0) != 1:
        return False
    reply = top_match.get("accepted_reply") or ""
    if len(reply) <= MIN_ACCEPTED_REPLY_LEN:
        return False
    return True


def format_auto_reply_body(
    top_matches: list[dict[str, Any]],
    similarity_band: str | None = None,
) -> str:
    """Genera il body markdown della auto-reply.

    similarity_band ∈ {'high', 'mid'} (calcolato dal caller da top.similarity):
      - 'high' (>=0.92): single-ref reply
      - 'mid'  (0.85-0.92): multi-ref reply (top-1 reply + lista refs)

    Se `similarity_band` non passato, deriva da top_matches[0].similarity.
    """
    if not top_matches:
        return ""
    top = top_matches[0]
    if similarity_band is None:
        s = float(top.get("similarity") or 0.0)
        similarity_band = "high" if s >= SIM_HIGH_BAND else "mid"
    ref = top.get("ref") or ""
    reply = (top.get("accepted_reply") or "").strip()
    if similarity_band == "high":
        return (
            f"Questa domanda e' gia' stata trattata in {ref}. "
            f"In sintesi: {reply}. "
            f"Se non risolve, rispondi qui ed estendero' l'analisi."
        )
    # mid band: 2-3 refs
    refs = [m.get("ref") or "" for m in top_matches[:3] if m.get("ref")]
    refs_str = " e ".join(refs) if len(refs) <= 2 else (
        ", ".join(refs[:-1]) + " e " + refs[-1]
    )
    return (
        f"Domande simili sono state trattate in {refs_str}. "
        f"In sintesi: {reply}. "
        f"Se non risolve, rispondi qui."
    )


def similarity_band_of(similarity: float) -> str:
    """Band derivation centralizzata. Riusato dal watcher."""
    return "high" if float(similarity) >= SIM_HIGH_BAND else "mid"
