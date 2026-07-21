"""translator_quality.py — score qualita' di una traduzione.

Score finale (deterministico, range [0,1]):

    score = 0.5 * cosine_sim + 0.4 * roundtrip_sim + 0.1 * placeholder_integrity

dove:

  - `cosine_sim`  = cosine similarity in spazio embedding multilingua
                     (best-effort, vedi `_embed_texts`).
  - `roundtrip_sim` = cosine similarity tra source originale e back-translation
                     (target → source via stessa pipeline LLM).
                     E' il segnale piu' affidabile per qualita' cross-lingua.
  - `placeholder_integrity` = 1.0 se il set di placeholder Jinja2 (`{{var}}`,
                     `{% if %}`, ...) e' identico tra source e target,
                     altrimenti 0.0.

Embedding strategy (the design guide §7.9 — codice deterministico):

  - Preferenza 1: SigLIP text encoder (gia' caricato per pipeline immagini).
                   Cross-lingual robusto sui modelli moderni a 768 dim.
                   Usato indistintamente per IT, EN, e back-translation.
  - Preferenza 2: fallback char-trigram Jaccard set-based (nessun ML, sempre
                   disponibile). Approssimazione grossolana — buona per
                   detect di traduzioni "vuote" o totalmente off-topic, ma
                   non discriminante per piccole differenze di qualita'.
                   In pratica, in fallback, il segnale dominante e' il
                   roundtrip (40%) + placeholder (10%).

the design guide §7.9: zero LLM nel critical path qui DENTRO. L'unico LLM e' il
back-translate (`_back_translate`), invocato esplicitamente dal caller
(audit batch offline, non dal runtime live).
"""

from __future__ import annotations

import logging
import re
import threading
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)


# Pattern Jinja2: cattura `{{...}}` e `{% ... %}` come marker invarianti.
# Volutamente non greedy per evitare cattura cross-block.
_JINJA_RE = re.compile(r"\{\{[^{}]*\}\}|\{%[^{}]*%\}")


# ── Embedding ───────────────────────────────────────────────────────────────

_clip_engine = None
_clip_lock = threading.Lock()


def _get_clip():
    """Singleton ClipEngine (lazy-load) o None se SigLIP non disponibile."""
    global _clip_engine
    if _clip_engine is not None:
        return _clip_engine
    with _clip_lock:
        if _clip_engine is not None:
            return _clip_engine
        try:
            from virt import get_embedder  # type: ignore
            engine = get_embedder("image")
            if not engine.available:
                log.info("translator_quality: SigLIP non disponibile, "
                          "fallback char-trigram Jaccard")
                _clip_engine = False
                return None
            _clip_engine = engine
            return engine
        except Exception as exc:
            log.info("translator_quality: SigLIP load failed (%s), "
                      "fallback char-trigram", exc)
            _clip_engine = False
            return None


def _embed_texts(texts: list[str]) -> Optional[np.ndarray]:
    """Embedding multilingua via SigLIP. Ritorna `(N, dim)` L2-normalizzato.

    Se SigLIP non e' disponibile, ritorna None (caller deve usare fallback).
    Cache dell'engine. Le stringhe vengono troncate dal tokenizer interno
    (max 64 token).
    """
    if not texts:
        return np.zeros((0, 768), dtype=np.float32)
    engine = _get_clip()
    if engine is None:
        return None
    try:
        # Tronca strings lunghe a un budget (il tokenizer SigLIP ha cap 64
        # token; oltre, il segnale degenera). Per prompt lunghi prendiamo
        # head + tail per catturare apertura e chiusura.
        clipped = []
        for t in texts:
            t = (t or "").strip()
            if len(t) > 800:
                t = t[:400] + " ... " + t[-400:]
            clipped.append(t)
        return engine.embed_texts(clipped, normalize=True)
    except Exception as exc:
        log.warning("translator_quality: embed_texts failed: %s", exc)
        return None


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity di due vettori 1-D L2-normalizzati. Range [-1, 1].

    Per vettori SigLIP gia' L2-normalizzati, e' un dot product.
    """
    a = np.asarray(a, dtype=np.float32).ravel()
    b = np.asarray(b, dtype=np.float32).ravel()
    if a.size == 0 or b.size == 0:
        return 0.0
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na < 1e-9 or nb < 1e-9:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def _char_trigram_set(text: str) -> set[str]:
    """Set di char-trigrammi normalizzati per fallback similarity."""
    t = re.sub(r"\s+", " ", (text or "").lower()).strip()
    if len(t) < 3:
        return set()
    return {t[i:i + 3] for i in range(len(t) - 2)}


def _jaccard_trigram(a: str, b: str) -> float:
    """Jaccard sui char-trigrammi. Range [0, 1].

    Cross-lingua e' un fallback grossolano (lingue affini condividono
    radici latine -> trigrammi parzialmente comuni). Discrimina almeno
    traduzioni totalmente off-topic vs ragionevoli.
    """
    sa = _char_trigram_set(a)
    sb = _char_trigram_set(b)
    if not sa or not sb:
        return 0.0
    inter = len(sa & sb)
    union = len(sa | sb)
    if union == 0:
        return 0.0
    return inter / union


# ── Placeholder integrity ───────────────────────────────────────────────────

def _placeholder_set(text: str) -> set[str]:
    """Estrae set di placeholder Jinja2 normalizzati.

    Normalizzazione: collapse whitespace e strip dell'interno per rendere
    `{{var}}` == `{{ name }}` == `{{  name  }}` ai fini del confronto.
    `{% if x %}` viene normalizzato a `{% if x %}` con singolo spazio.
    """
    out: set[str] = set()
    for m in _JINJA_RE.finditer(text or ""):
        raw = m.group(0)
        # Estrai tipo (`{{` o `{%`) e contenuto interno trimmed.
        if raw.startswith("{{"):
            inner = raw[2:-2].strip()
            inner = re.sub(r"\s+", " ", inner)
            out.add(f"{{{{ {inner} }}}}")
        elif raw.startswith("{%"):
            inner = raw[2:-2].strip()
            inner = re.sub(r"\s+", " ", inner)
            out.add(f"{{% {inner} %}}")
    return out


# ── Back-translate ──────────────────────────────────────────────────────────

def _back_translate(target_text: str, *, from_lang: str, to_lang: str,
                     tier: str) -> Optional[str]:
    """Traduce `target_text` da `from_lang` a `to_lang` riusando la pipeline
    `_translate_short_text` di `i18n_translator`.

    Ritorna la stringa back-translated, o None su fallimento.
    """
    try:
        from i18n_translator import _translate_short_text  # type: ignore
    except Exception as exc:
        log.error("back_translate: import _translate_short_text failed: %s",
                    exc)
        return None
    try:
        out, errs = _translate_short_text(
            target_text, source_lang=from_lang, target_lang=to_lang,
            tier=tier,
        )
        if not out:
            log.warning("back_translate: empty output (errs=%s)", errs)
            return None
        return out
    except Exception as exc:
        log.warning("back_translate: failed: %s", exc)
        return None


# ── Score finale ────────────────────────────────────────────────────────────

def score_translation(source_text: str, translated_text: str,
                       source_lang: str, target_lang: str,
                       *, tier_used: str,
                       skip_roundtrip: bool = False) -> dict:
    """Calcola score qualita' di una traduzione.

    Args:
        source_text: testo sorgente (lingua `source_lang`).
        translated_text: traduzione candidata (lingua `target_lang`).
        source_lang: codice lingua sorgente (es. 'it').
        target_lang: codice lingua target (es. 'en').
        tier_used: tier LLM usato per generare la traduzione (logged).
                    Lo stesso tier viene usato per il back-translate.
        skip_roundtrip: se True, salta il back-translate (peso 0.0 al posto
                         di 0.4 nel finale; usato per dry-run e test).

    Returns:
        dict con campi:
          - score: float [0, 1]
          - cosine_sim: float [-1, 1] (clip a [0,1] nel finale)
          - roundtrip_sim: float [-1, 1] (clip a [0,1] nel finale)
          - placeholder_integrity: bool
          - details: dict con sub-risultati di debug
    """
    details: dict = {
        "source_lang": source_lang,
        "target_lang": target_lang,
        "tier_used": tier_used,
        "src_len": len(source_text or ""),
        "tgt_len": len(translated_text or ""),
        "embedding_method": "siglip",
        "skip_roundtrip": skip_roundtrip,
    }

    # 1. Cosine similarity cross-lang.
    embs = _embed_texts([source_text or "", translated_text or ""])
    if embs is None:
        # Fallback Jaccard char-trigram.
        cos = _jaccard_trigram(source_text or "", translated_text or "")
        details["embedding_method"] = "jaccard_trigram_fallback"
    else:
        cos = cosine(embs[0], embs[1])

    # 2. Roundtrip: target → source, similarity con source originale.
    if skip_roundtrip:
        rt = 0.0
        details["roundtrip_skipped"] = True
    else:
        bt = _back_translate(translated_text or "",
                              from_lang=target_lang, to_lang=source_lang,
                              tier=tier_used)
        if bt is None:
            rt = 0.0
            details["roundtrip_failed"] = True
        else:
            details["back_translation"] = bt[:200]
            rt_embs = _embed_texts([source_text or "", bt])
            if rt_embs is None:
                rt = _jaccard_trigram(source_text or "", bt)
            else:
                rt = cosine(rt_embs[0], rt_embs[1])

    # 3. Placeholder integrity.
    src_ph = _placeholder_set(source_text or "")
    tgt_ph = _placeholder_set(translated_text or "")
    ph_ok = src_ph == tgt_ph
    details["src_placeholders_count"] = len(src_ph)
    details["tgt_placeholders_count"] = len(tgt_ph)
    details["placeholder_diff_missing"] = sorted(src_ph - tgt_ph)[:5]
    details["placeholder_diff_extra"] = sorted(tgt_ph - src_ph)[:5]

    # Clip cos / rt a [0, 1] per la formula finale (negativi non hanno senso
    # come quality score; SigLIP cross-lang puo' dare valori bassi ma > 0
    # per traduzioni ragionevoli).
    cos_clip = max(0.0, min(1.0, float(cos)))
    rt_clip = max(0.0, min(1.0, float(rt)))

    if skip_roundtrip:
        # Riequilibra senza roundtrip: cos=0.83 (era 0.5), ph=0.17 (era 0.1)
        score = 0.83 * cos_clip + 0.17 * (1.0 if ph_ok else 0.0)
    else:
        score = (0.5 * cos_clip
                  + 0.4 * rt_clip
                  + 0.1 * (1.0 if ph_ok else 0.0))

    return {
        "score": round(float(score), 4),
        "cosine_sim": round(float(cos), 4),
        "roundtrip_sim": round(float(rt), 4),
        "placeholder_integrity": bool(ph_ok),
        "details": details,
    }
