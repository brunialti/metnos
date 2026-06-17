"""compare_entries — distanza semantica universale input↔candidati (inproc).

SCOPO: confronta semanticamente un `reference` con una lista di candidati
(`entries`), arricchisce OGNI candidato con `similarity` (cosine, BGE-M3 1024d
L2-normalized) e li ordina per valore calcolato (default desc). Mattoncino
RIUSABILE ovunque serva ranking/dedup/retrieval per vicinanza semantica:
dedup issue, foto↔testo, RAG, dedup mail, ecc.

Builtin INPROC (non subprocess): riusa il singleton embedder in-processo
(`affinity_semantic._get_embedder`, ADR 0134) — un subprocess ricaricherebbe
il modello a ogni chiamata. Vettoriale §2.1. Determinismo §7.9 (embed+cosine,
zero LLM). Se un candidato ha gia' `embedding` lo riusa (store pre-embeddato),
altrimenti embedda `entry[field]` al volo (qualsiasi lista).

Degrade ONESTO (§2.8): embedder assente → entries=[] + embedder_available=false.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from messages import get as _msg  # noqa: E402

# Campi-testo candidati in ordine di preferenza (auto-detect se `field` omesso).
_TEXT_FIELDS = ("text", "title", "summary", "subject", "body", "content",
                "name", "description", "question_text", "label", "ref")


def _l2(v) -> np.ndarray:
    v = np.asarray(v, dtype=np.float32).ravel()
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-9 else v


def _entry_text(e, field) -> str:
    """Testo da embeddare per un candidato. `field` esplicito vince; altrimenti
    primo campo-testo noto; fallback JSON compatto (lista a schema ignoto)."""
    if isinstance(e, dict):
        if field and e.get(field) is not None:
            return str(e.get(field) or "")
        for f in _TEXT_FIELDS:
            if e.get(f):
                return str(e[f])
        return json.dumps(e, ensure_ascii=False, default=str)[:2000]
    return str(e)


def _cand_vec(e, emb):
    """Vettore di un candidato: riusa `embedding` se valido (1024d), altrimenti
    None (→ embed batch del testo a valle)."""
    if isinstance(e, dict):
        ev = e.get("embedding")
        if ev is not None:
            try:
                vec = _l2(ev)
                if vec.shape[0] == 1024:
                    return vec
            except (ValueError, TypeError):
                pass
    return None


def handle_compare_entries(args, *, verbose: bool = False) -> dict:
    reference = args.get("reference")
    if not isinstance(reference, str) or not reference.strip():
        return {"ok": False, "error": _msg("ERR_ARG_MISSING", arg="reference"),
                "error_class": "invalid_args"}

    entries = args.get("entries")
    if entries is None:
        entries = []
    if not isinstance(entries, list):
        return {"ok": False, "error": _msg("ERR_ARG_NOT_LIST", arg="entries"),
                "error_class": "invalid_args"}

    field = args.get("field")
    order = str(args.get("order") or "desc").lower()
    if order not in ("desc", "asc"):
        order = "desc"
    min_sim = args.get("min_similarity")
    top_n = args.get("top_n") or args.get("max_results")

    if not entries:
        return {"ok": True, "ok_count": 0, "entries": [], "embedder_available": True}

    try:
        from affinity_semantic import _get_embedder  # type: ignore
        emb = _get_embedder()
    except Exception:
        emb = None
    if emb is None:
        # Degrade onesto: niente ranking, ma esito esplicito (§2.8).
        return {"ok": True, "ok_count": 0, "entries": [],
                "embedder_available": False}

    ref_vec = _l2(emb.embed_query(reference))

    # Vettori candidati: riusa `embedding` dove c'e', embedda il resto in BATCH.
    cand_vecs: list = [None] * len(entries)
    to_embed: list[str] = []
    need_idx: list[int] = []
    for i, e in enumerate(entries):
        v = _cand_vec(e, emb)
        if v is not None:
            cand_vecs[i] = v
        else:
            to_embed.append(_entry_text(e, field))
            need_idx.append(i)
    if to_embed:
        batch = emb.embed_texts(to_embed)
        for j, i in enumerate(need_idx):
            cand_vecs[i] = batch[j]

    out = []
    for i, e in enumerate(entries):
        v = cand_vecs[i]
        sim = float(np.dot(ref_vec, v)) if v is not None else 0.0
        rec = dict(e) if isinstance(e, dict) else {"value": e}
        rec["similarity"] = round(sim, 6)
        out.append(rec)

    out.sort(key=lambda r: r.get("similarity", 0.0), reverse=(order == "desc"))

    if isinstance(min_sim, (int, float)):
        out = [r for r in out if r.get("similarity", 0.0) >= float(min_sim)]

    res: dict = {"ok": True, "embedder_available": True}
    available_total = len(out)
    if isinstance(top_n, int) and top_n > 0 and len(out) > top_n:
        out = out[:top_n]
        # Cap user-richiesto = troncamento INTENZIONALE (§2.7/§2.11).
        res.update({"truncated": True, "truncated_intentional": True,
                    "truncated_what": "entries", "used": len(out),
                    "available_total": available_total,
                    "cap_field": "top_n", "cap_value": int(top_n)})
    res["entries"] = out
    res["ok_count"] = len(out)
    return res


COMPARE_ENTRIES_TOOL = {
    "type": "function",
    "function": {
        "name": "compare_entries",
        "description": (
            "SCOPO: confronta semanticamente un input (`reference`) con una "
            "lista di candidati e li ordina per vicinanza (cosine). PATTERN: "
            "compare_entries(reference=\"testo\", from_step=N, top_n=5). NON: "
            "confronto esatto/predicato -> filter_entries; aggregato numerico "
            "-> compute_entries; ricerca file/mail -> find_files/read_messages. "
            "OUT: entries=[{...campi originali, similarity}] ordinate per "
            "similarity desc; embedder_available."),
        "parameters": {
            "type": "object",
            "required": ["reference"],
            "properties": {
                "reference": {
                    "type": "string",
                    "description": "Testo input con cui confrontare i candidati. "
                                   "Es. il testo di una issue nuova."},
                "entries": {
                    "type": "array",
                    "description": "Candidati (dict). Di norma via `from_step=N` "
                                   "top-level: il runtime espande in `entries`."},
                "field": {
                    "type": "string",
                    "description": "Campo-testo dei candidati da confrontare. "
                                   "Omesso = auto (title/summary/text/...). Se un "
                                   "candidato ha `embedding`, quello vince."},
                "order": {
                    "type": "string",
                    "enum": ["desc", "asc"],
                    "description": "Ordinamento per similarity. Default 'desc' "
                                   "(piu' simili prima)."},
                "top_n": {
                    "type": "integer",
                    "description": "Cap: tieni i primi N per similarity (§2.7)."},
                "min_similarity": {
                    "type": "number",
                    "description": "Soglia: scarta i candidati sotto questa "
                                   "similarity (0..1). Default nessuna."},
            },
        },
    },
}

BUILTIN_INPROC_SPECS = [
    {"name": "compare_entries", "tool_spec": COMPARE_ENTRIES_TOOL,
     "affinity": ["simile", "similarità", "distanza semantica", "confronta",
                  "dedup", "piu vicino", "vicinanza", "ranking semantico",
                  "similar", "semantic distance", "nearest", "rank by similarity",
                  "compare"]},
]
