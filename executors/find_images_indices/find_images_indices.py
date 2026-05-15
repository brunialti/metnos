#!/usr/bin/env python3
"""find_images_indices — executor di Metnos (v4 unified, ADR 0117).

Interroga l'indice unificato delle immagini (un solo storage per corpus,
schema v4) e ritorna le foto che matchano i criteri richiesti. Sostituisce
il modello a 3 indici disgiunti (scene/persons/gps) con UN solo asse.

Args principali:
- `query_text`: ricerca semantica via cosine su `embedding_text` + BM25
  su `description+keywords` (combine con somma pesata).
- `name`: filtro identita' via PersonsRegistry (riusa i face embeddings di
  faces[].embedding_face contro l'indice ArcFace registrato).
- `reference_images`: filtro identita' via cosine ArcFace contro le facce
  estratte dalle reference (alternativa a `name`).
- `min_face_pixels` / `min_face_count` / `max_face_count`: filtri di
  composizione su faces[] e bbox.
- `paths_filter`: lista path → restringe lo scan.
- `top_k`: cap risultati (default 100, max 200).
- `time_window`: filtro per mtime/EXIF taken_at_iso.
- `near_lat/near_lon/radius_km`: filtro GPS via exif_gps.

Output (`entries`):
- `path`, `score` (composito), `match_type` ("text"|"face"|"gps"|"compose"),
  `description`, `bbox?`, ...
- Pattern §2.7: `truncated`, `truncated_what`, `used`, `available_total`,
  `cap_field='top_k'`, `cap_value`.
- error_class: `low_confidence` / `paths_filter_empty` /
  `schema_too_old` / `index_missing` / `no_faces_above_size_threshold`.

Backward compat:
- arg `idx=` accettato e LOGGATO (deprecato post-ADR0117). NON instrada
  a vecchio codice — ignora con warn.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent.parent / "runtime"
sys.path.insert(0, str(_RUNTIME))

from index_schema import INDEX_SCHEMA_VERSION, is_unified_schema

log = logging.getLogger(__name__)

_TOP_K_DEFAULT = 100
_TOP_K_MAX = 200
_LOW_CONF_FLOOR = 0.12
_FACE_MATCH_FLOOR = 0.4


def _index_image_root() -> Path:
    v = os.environ.get("METNOS_INDEX_ROOT")
    if v:
        return Path(v) / "image"
    base = os.environ.get("METNOS_USER_DATA")
    base_p = Path(base) if base else Path.home() / ".local" / "share" / "metnos"
    return base_p / "index" / "image"


def _user_data_root() -> Path:
    base = os.environ.get("METNOS_USER_DATA")
    return Path(base) if base else Path.home() / ".local" / "share" / "metnos"


def _index_dir(base_path: Path) -> Path:
    # 15/5/2026: identita' del corpus = path LOGICAL (no .resolve()). Se
    # `~/.local/share/metnos/Immagini` e' un symlink, il corpus resta lo
    # stesso anche se il storage sottostante (NAS, mount) cambia. Usare
    # `.resolve()` cambierebbe il digest e renderebbe inaccessibili gli
    # indici creati quando il path era una dir reale. Il caller passa
    # gia' path canonical assoluto.
    digest = hashlib.sha256(str(base_path).encode("utf-8")).hexdigest()
    return _index_image_root() / digest[:16] / "unified"


def _is_dry_run() -> bool:
    return os.environ.get("METNOS_DRY_RUN", "0") == "1"


def _resolve_base_path(base_path_arg) -> tuple[Path | None, list[Path] | None, str | None]:
    """Risolve `base_path` arg (3 modalita').

    Ritorna (single_dir, multi_dirs, message). Esattamente uno fra
    (single_dir, multi_dirs) e' valorizzato.
    """
    if base_path_arg is None or base_path_arg == "":
        root = _user_data_root()
        dirs: list[Path] = []
        if root.exists():
            for sub in root.iterdir():
                if sub.is_dir():
                    idx_dir = _index_dir(sub)
                    if (idx_dir / "meta.json").exists():
                        dirs.append(sub)
        if not dirs:
            return None, None, "no indexed dirs found"
        return None, dirs, f"discovered {len(dirs)} indexed dirs"
    arg = str(base_path_arg)
    p = Path(os.path.expanduser(arg))
    is_path_like = (
        p.is_absolute() or arg.startswith("./") or arg.startswith("../")
        or arg.startswith("~") or arg.startswith("/")
    )
    if is_path_like:
        if p.exists() and p.is_dir():
            # 15/5/2026: se il path esiste ma non ha indice, fallback a
            # discovery automatica. Bug live: LLM passa `/home/roberto/images`
            # (esiste, no idx), discovery trova `~/.local/share/metnos/Immagini`
            # (esiste, 30k entries). Resilienza > rigore.
            # NON usare .resolve(): l'identita' del corpus e' il path logical,
            # symlink->NAS deve mantenere lo stesso indice (vedi _index_dir).
            logical = p
            idx_dir = _index_dir(logical)
            if (idx_dir / "meta.json").exists():
                return logical, None, None
            # Fallback: discovery
            root = _user_data_root()
            dirs: list[Path] = []
            if root.exists():
                for sub in root.iterdir():
                    if sub.is_dir():
                        sub_idx = _index_dir(sub)
                        if (sub_idx / "meta.json").exists():
                            dirs.append(sub)
            if dirs:
                return None, dirs, (
                    f"base_path '{arg}' non indicizzato → fallback discovery "
                    f"({len(dirs)} indici trovati)"
                )
            return logical, None, None  # nessun fallback, ritorna come prima
        return None, None, f"base_path not found: {arg}"
    root = _user_data_root()
    if root.exists():
        target = arg.lower()
        for sub in root.iterdir():
            if sub.is_dir() and sub.name.lower() == target:
                return sub.resolve(), None, None
    return None, None, f"base_path symbolic match not found: {arg}"


def _load_unified_index(idx_dir: Path) -> tuple[list[dict], object | None, object | None, dict]:
    meta_p = idx_dir / "meta.json"
    if not meta_p.exists():
        return [], None, None, {}
    try:
        meta = json.loads(meta_p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        meta = {}
    entries: list[dict] = []
    entries_p = idx_dir / "entries.jsonl"
    if entries_p.exists():
        try:
            with entries_p.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            pass
    emb_text = None
    emb_face = None
    try:
        import numpy as np
        et_p = idx_dir / "embeddings_text.npy"
        if et_p.exists():
            emb_text = np.load(str(et_p))
        ef_p = idx_dir / "embeddings_face.npy"
        if ef_p.exists():
            emb_face = np.load(str(ef_p))
    except Exception:
        pass
    return entries, emb_text, emb_face, meta


def _bbox_area(face: dict) -> int:
    bb = face.get("bbox")
    if not bb or len(bb) < 4:
        return 0
    try:
        return int(bb[2]) * int(bb[3])
    except (TypeError, ValueError):
        return 0


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    R = 6371.0
    p1 = math.radians(float(lat1))
    p2 = math.radians(float(lat2))
    dp = math.radians(float(lat2) - float(lat1))
    dl = math.radians(float(lon2) - float(lon1))
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return float(2 * R * math.asin(math.sqrt(a)))


def _normalize_text_for_bm25(s: str) -> list[str]:
    if not s:
        return []
    s = s.lower()
    return re.findall(r"[a-zàèéìòù0-9]{2,}", s)


def _bm25_score(query_terms: list[str], doc_terms: list[str]) -> float:
    if not query_terms or not doc_terms:
        return 0.0
    k1 = 1.5
    score = 0.0
    for q in query_terms:
        tf = doc_terms.count(q)
        if tf == 0:
            continue
        score += (tf * (k1 + 1)) / (tf + k1)
    return float(score)


def _cosine(a, b) -> float:
    import numpy as np
    if a is None or b is None:
        return 0.0
    a = np.asarray(a, dtype="float32")
    b = np.asarray(b, dtype="float32")
    if a.shape != b.shape:
        return 0.0
    return float(np.dot(a, b))


def _l2_normalize(v):
    import numpy as np
    n = float(np.linalg.norm(v))
    if n == 0.0:
        return v
    return v / n


# ----- Query expansion via corpus tokens (15/5/2026 §7.3) ---------------
# BGE-M3 cosine puro confonde topici visivamente correlati (mare/neve
# entrambi outdoor → cosine 0.5). Query expansion deterministica:
#   1. Estraggo unique keyword + path_tokens dal corpus (~5k tokens)
#   2. Encode con BGE-M3, cache su disk <idx_dir>/corpus_tokens.npz
#   3. Per query Q: lookup top-K corpus tokens con cosine >= soglia → q_expanded
#   4. BM25 sull'espansione → match HARD su keyword reali del corpus
# Risultato: doc passa se contiene almeno UNO dei keyword expanded
# (incluso il keyword originale se appare nel corpus).

_QE_TOKEN_MIN_LEN = 3
_QE_TOKEN_MAX_LEN = 25
_QE_TOP_K = 15
# 0.65: bench su "mare" vs vocab → include i veri positivi semantici
# (spiaggia 0.66, oceano 0.66, bagno 0.66, ocean 0.75) e i prefix-related
# (marea 0.87, marina 0.77, mar 0.86), esclude i puri outdoor falsi
# positivi (campo 0.58, verde 0.58, prato 0.60, neve 0.63, montagna 0.59).
# Con soglia inferiore: include falsi outdoor. Con superiore: perde
# `bagno`, `oceano`, `spiaggia` (veri positivi).
_QE_MIN_COSINE = 0.65


def _corpus_token_embs(idx_dir):
    """Lazy load (o build) embedding del vocabolario keyword corpus.
    Cache file <idx_dir>/corpus_tokens.npz. Idempotente.
    Ritorna (tokens: list[str], embs: ndarray[N,1024] L2-normalized)
    o ([], None) se BGE non disponibile / nessun token.
    """
    import numpy as np
    cache_path = idx_dir / "corpus_tokens.npz"
    if cache_path.exists():
        try:
            data = np.load(cache_path, allow_pickle=False)
            return data["tokens"].tolist(), data["embs"]
        except Exception as ex:
            log.warning("corpus_tokens.npz read fail: %r", ex)
    # Build
    entries_file = idx_dir / "entries.jsonl"
    if not entries_file.exists():
        return [], None
    seen: set[str] = set()
    tokens: list[str] = []
    for line in entries_file.read_text().splitlines():
        if not line.strip():
            continue
        try:
            e = json.loads(line)
        except Exception:
            continue
        for tok in (e.get("keywords") or []):
            t = str(tok).strip().lower()
            if (_QE_TOKEN_MIN_LEN <= len(t) <= _QE_TOKEN_MAX_LEN
                    and t not in seen and t.isalpha()):
                seen.add(t); tokens.append(t)
        for tok in (e.get("path_tokens") or []):
            t = str(tok).strip().lower()
            if (_QE_TOKEN_MIN_LEN <= len(t) <= _QE_TOKEN_MAX_LEN
                    and t not in seen and t.isalpha()):
                seen.add(t); tokens.append(t)
    if not tokens:
        return [], None
    try:
        from bge_embedding import BGEEmbeddingService
        te = BGEEmbeddingService()
        embs = te.embed_texts(tokens).astype(np.float32, copy=False)
    except Exception as ex:
        log.warning("corpus tokens embed fail: %r", ex)
        return [], None
    try:
        tmp = cache_path.with_name(cache_path.name + ".tmp")
        with open(tmp, "wb") as f:
            np.savez_compressed(f, tokens=np.array(tokens), embs=embs)
        tmp.replace(cache_path)
    except Exception as ex:
        log.warning("corpus_tokens.npz write fail: %r", ex)
    return tokens, embs


def _common_prefix_len(a: str, b: str) -> int:
    """Lunghezza prefisso comune (case-insensitive)."""
    a, b = a.lower(), b.lower()
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


# Soglia cosine per accettare un candidate con prefisso lungo comune
# alla query (lemma/morfologico vs prefix-bias). BGE-M3 mappa "marrone"
# a "mare" con cosine 0.76 puramente per prefisso "mar" — NON e' semantica
# vera. La derivazione legittima (mare→marea→mari→mar) ha cosine >= 0.85.
_QE_LEMMA_COSINE = 0.85
_QE_PREFIX_MIN_LEN = 3


def _expand_query_via_corpus(query, tokens, embs,
                                top_k: int = _QE_TOP_K,
                                min_cosine: float = _QE_MIN_COSINE):
    """Top-K corpus tokens semanticamente simili alla query (cosine >= soglia).
    Filtra prefix-bias di BGE-M3: candidate che condivide >=3 char di
    prefisso con query DEVE avere cosine >= 0.85 (lemma threshold),
    altrimenti viene escluso come prefix-only match ("marrone/marche/
    marziale" vs "mare"). I candidate semanticamente diversi (no prefix
    comune) restano accettati a min_cosine. Determinismo §7.9.
    """
    import numpy as np
    q_lower = (query or "").strip().lower()
    if not q_lower or not tokens or embs is None:
        return [q_lower] if q_lower else []
    try:
        from bge_embedding import BGEEmbeddingService
        te = BGEEmbeddingService()
        qv = te.embed_query(query)
        qv = qv / np.linalg.norm(qv) if np.linalg.norm(qv) > 0 else qv
    except Exception:
        return [q_lower]
    scores = embs @ qv.astype(np.float32, copy=False)
    expanded: list[str] = [q_lower]
    seen = {q_lower}
    # Buffer 3× per assorbire i filtri prefix-bias.
    ranked_idx = np.argsort(-scores)[:top_k * 3]
    for idx in ranked_idx:
        s = float(scores[idx])
        if s < min_cosine:
            break
        tok = tokens[int(idx)]
        if tok in seen:
            continue
        # Filtro prefix-bias: prefisso lungo comune AND cosine sotto lemma.
        prefix = _common_prefix_len(q_lower, tok)
        min_len = min(len(q_lower), len(tok))
        if (prefix >= _QE_PREFIX_MIN_LEN
                and prefix >= min_len * 0.6
                and s < _QE_LEMMA_COSINE):
            continue  # prefix-bias rifiutato
        seen.add(tok); expanded.append(tok)
        if len(expanded) >= top_k + 1:
            break
    return expanded


def _query_expansion_enabled() -> bool:
    return os.environ.get("METNOS_QUERY_EXPANSION", "1") != "0"


# ----- LLM-based query expansion (15/5/2026) ----------------------------
# BGE-M3 corpus token expansion degenera per query brevi mono-token:
# "mare" → {amare, mappe, mercato, morte, madre, mese} (cosine generico).
# LLM expansion (Gemma 4 26B middle tier locale) genera sinonimi puliti
# rispettando la lingua della query (prompt language-instruction).
# Cache disk indefinita (sinonimi stabili).

_QE_LLM_CACHE_DIR = Path.home() / ".cache" / "metnos" / "query_expansion_llm"
_QE_LLM_MAX_TOKENS = 200


def _expand_query_via_llm(query: str) -> list[str]:
    """LLM-based query expansion language-sensitive con cache disk.

    Il prompt istruisce il LLM a mantenere la lingua della query e
    generare sinonimi semantici stretti (non parole generiche). Cache
    indefinita per query (sha256 del lowercased). Determinismo §7.9
    eccetto la singola call LLM irriducibilmente generativa.

    Output sempre include la query originale (lowercased) come primo
    elemento. Vuoto solo se LLM non disponibile.
    """
    q_clean = (query or "").strip()
    if not q_clean:
        return []
    q_lower = q_clean.lower()
    key = hashlib.sha256(q_lower.encode("utf-8")).hexdigest()[:16]
    cache_path = _QE_LLM_CACHE_DIR / f"{key}.json"
    if cache_path.exists():
        try:
            data = json.loads(cache_path.read_text())
            cached = data.get("expanded")
            if isinstance(cached, list) and cached:
                return cached
        except Exception:
            pass
    # Prompt language-sensitive: scritto in INGLESE (lingua neutra per
    # Gemma multilingua, evita bias verso italiano se prompt e' in italiano)
    # con few-shot examples in IT+EN per ancorare il behavior. Il LLM
    # detecta la lingua della query e risponde IN-LANG.
    prompt = (
        "You are a multilingual thesaurus. Detect the language of the "
        "input concept and produce synonyms IN THE SAME LANGUAGE.\n"
        f'Concept: "{q_clean}"\n'
        "Generate 8 specific synonyms (single words or short 2-token "
        "phrases). Constraints:\n"
        "- SAME LANGUAGE as the input (Italian input → Italian synonyms; "
        "English input → English synonyms; etc.)\n"
        "- Tight semantic synonyms only\n"
        "- NO generic words (thing, object, item, cosa, oggetto)\n"
        "- NO preamble, NO explanation\n"
        "Examples (showing language fidelity — pay attention to language match):\n"
        "- \"mare\" (Italian) → mare, oceano, marea, spiaggia, costa, mar, marina, onde\n"
        "- \"sea\" (English) → sea, ocean, tide, shore, coast, marina, waves, water\n"
        "- \"snow\" (English) → snow, ice, frost, blizzard, snowflake, snowfall, hail, winter\n"
        "- \"compleanno\" (Italian) → compleanno, festa, anniversario, torta, "
        "candeline, auguri, regalo, festeggiamento\n"
        "- \"birthday\" (English) → birthday, anniversary, party, celebration, "
        "jubilee, gala, fete, occasion\n"
        "- \"neige\" (French) → neige, glace, gel, flocon, blizzard, hiver, "
        "neigeux, poudreuse\n"
        "Output: single line, comma-separated synonyms only, "
        "NO concept name prefix."
    )
    try:
        from llm_router import LLMRouter
        r = LLMRouter()
        provider = r.provider("middle")
        res = provider.chat(
            "", prompt, max_tokens=_QE_LLM_MAX_TOKENS,
            temperature=0, think=False,
        )
        raw = (res.text or "").strip()
    except Exception as ex:
        log.warning("LLM query expansion failed: %r", ex)
        return [q_lower]
    # Parse: strip markdown fences, prendi prima riga utile
    raw = re.sub(r"^```[a-zA-Z]*\s*", "", raw)
    raw = re.sub(r"\s*```\s*$", "", raw)
    line = raw.splitlines()[0] if raw else ""
    expanded: list[str] = [q_lower]
    seen = {q_lower}
    for tok in line.split(","):
        t = tok.strip().lower()
        # Strip wrap quotes/asterischi LLM puo' aggiungere
        t = t.strip('"\'*` ')
        if t and t not in seen and 1 < len(t) < 30:
            seen.add(t); expanded.append(t)
    if len(expanded) <= 1:
        return [q_lower]
    # Cache disk (atomic write)
    try:
        _QE_LLM_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = cache_path.with_name(cache_path.name + ".tmp")
        tmp.write_text(json.dumps(
            {"query": q_clean, "expanded": expanded},
            ensure_ascii=False, indent=2,
        ))
        tmp.replace(cache_path)
    except Exception as ex:
        log.debug("LLM expansion cache write fail: %r", ex)
    return expanded


def _query_expansion_llm_enabled() -> bool:
    return os.environ.get("METNOS_QUERY_EXPANSION_LLM", "1") != "0"


# ----- Date extraction from path/filename (15/5/2026) -------------------
# EXIF taken_at puo' mancare (foto vecchie, scansioni, screenshot,
# whatsapp export). mtime filesystem e' spesso inaffidabile (copy/move
# resetta). Pattern utente comune: foto organizzate in cartelle datate
# `Immagini/2024/2024 12 14 compl. Matteo/IMG_xxx.jpg`. Parse data dal
# path per fallback affidabile prima di degenerare su mtime.

# Full date con separatori (es. "2024-12-14", "2024 12 14", "2024_12_14")
_PATH_DATE_FULL_SEP = re.compile(
    r"(?<!\d)(20\d\d)[\s_./\\-]{1,3}(0\d|1[012])[\s_./\\-]{1,3}([012]\d|3[01])(?!\d)"
)
# Full date compatta (es. "20251224" in "IMG-20251224-WA.jpg")
_PATH_DATE_FULL_COMPACT = re.compile(
    r"(?<!\d)(20\d\d)(0\d|1[012])([012]\d|3[01])(?!\d)"
)
_PATH_DATE_YM = re.compile(r"(?<!\d)(20\d\d)[\s_./\\-]{1,3}(0\d|1[012])(?!\d)")
_PATH_DATE_Y = re.compile(r"(?<!\d)(19[89]\d|20\d\d)(?!\d)")


def _extract_date_from_path(path: str):
    """Ritorna timestamp epoch o None. USA SOLO IL FILENAME (basename),
    NON l'organizzazione cartelle dell'utente (Roberto 15/5/2026: la
    struttura `/Immagini/2024/...` e' personale, non standard
    universale). Formato piu' specifico (full > year-month > year).
    Anno valido 1980-2099.

    Esempi:
      "IMG-20251224-WA0107.jpg" → 2025-12-24 (compact YYYYMMDD)
      "IMG_20241214_140034.jpg" → 2024-12-14 (compact con underscore)
      "foto_2018-04-15.jpg" → 2018-04-15
      "fototessera matteo.jpg" → None (no date in filename)
    """
    if not path:
        return None
    from datetime import datetime
    import os
    # Estrai SOLO il basename: ignora organizzazione cartelle utente
    name = os.path.basename(str(path))
    if not name:
        return None
    last_full = None
    for m in _PATH_DATE_FULL_SEP.finditer(name):
        last_full = m
    for m in _PATH_DATE_FULL_COMPACT.finditer(name):
        if last_full is None or m.start() > last_full.start():
            last_full = m
    if last_full is not None:
        y, mo, d = int(last_full.group(1)), int(last_full.group(2)), int(last_full.group(3))
        try:
            return datetime(y, mo, d).timestamp()
        except ValueError:
            pass
    last_ym = None
    for m in _PATH_DATE_YM.finditer(name):
        last_ym = m
    if last_ym is not None:
        y, mo = int(last_ym.group(1)), int(last_ym.group(2))
        try:
            return datetime(y, mo, 1).timestamp()
        except ValueError:
            pass
    last_y = None
    for m in _PATH_DATE_Y.finditer(name):
        last_y = m
    if last_y is not None:
        y = int(last_y.group(1))
        if 1980 <= y <= 2099:
            try:
                return datetime(y, 1, 1).timestamp()
            except ValueError:
                pass
    return None


def _parse_time_window(window: str) -> tuple[float, float] | None:
    if not window or window == "all":
        return None
    now = time.time()
    today = datetime.fromtimestamp(now)
    s = window.strip().lower()
    if s == "today":
        start = today.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        return float(start), float(now)
    if s == "yesterday":
        d = today - timedelta(days=1)
        start = d.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        end = today.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        return float(start), float(end)
    m = re.match(r"^last-(\d+)([dwmyh])$", s)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        seconds = {"h": 3600, "d": 86400, "w": 604800,
                   "m": 86400 * 30, "y": 86400 * 365}[unit]
        return float(now - n * seconds), float(now)
    m = re.match(r"^(\d{4})$", s)
    if m:
        y = int(m.group(1))
        start = datetime(y, 1, 1).timestamp()
        end = datetime(y + 1, 1, 1).timestamp() - 1
        return float(start), float(end)
    m = re.match(r"^(\d{4})-(\d{2})$", s)
    if m:
        y = int(m.group(1))
        mo = int(m.group(2))
        if 1 <= mo <= 12:
            start = datetime(y, mo, 1).timestamp()
            ny, nm = (y, mo + 1) if mo < 12 else (y + 1, 1)
            end = datetime(ny, nm, 1).timestamp() - 1
            return float(start), float(end)
    return None


def _extract_face_embeddings_from_reference(ref_paths: list[str]):
    try:
        from face_embedding import get_face_engine
    except Exception:
        return []
    engine = get_face_engine()
    if not engine.available:
        return []
    embs: list = []
    for rp in ref_paths:
        p = Path(os.path.expanduser(rp))
        if not p.exists():
            continue
        try:
            faces = engine.detect_faces(p)
        except Exception:
            continue
        for face in faces:
            if face.get("embedding") is not None:
                embs.append(_l2_normalize(face["embedding"]))
    return embs


def _filter_unified(
    entries: list[dict], emb_text, emb_face, meta: dict, args: dict,
    idx_dir=None,
) -> dict:
    """Pipeline filtri/score per indice unificato. Ritorna dict con
    entries/n_above_threshold/error_class/applied_paths_filter.

    idx_dir (opzionale): se passato, abilita query expansion BGE-M3 via
    cache `<idx_dir>/corpus_tokens.npz`. Compatibilita' all'indietro
    (default None: query expansion disattivata)."""
    query_text = (args.get("query_text") or "").strip() or None
    name = (args.get("name") or "").strip() or None
    # Multi-persona AND (15/5/2026): `names` array, ogni nome deve essere
    # presente in OGNI foto matchata. Es. ["iacopo","matteo"] → foto con
    # AMBEDUE. Bug live: PLANNER passava `name="Iacopo, Matteo"` come
    # stringa unica → lookup fallisce → BM25 fallback ammette qualunque
    # foto con un solo nome. names plurale risolve §2.1.
    names_list = args.get("names")
    if isinstance(names_list, list) and names_list:
        names_clean = [str(n).strip() for n in names_list if str(n).strip()]
    elif isinstance(name, str) and "," in name:
        # Tolleranza wildcard §2.4: LLM ha emesso "Iacopo, Matteo" → split
        names_clean = [n.strip() for n in name.split(",") if n.strip()]
        name = None  # promosso a names plurale
    else:
        names_clean = []
    reference_images = args.get("reference_images") or []
    min_face_pixels = args.get("min_face_pixels")
    min_face_count = args.get("min_face_count")
    max_face_count = args.get("max_face_count")
    paths_filter = args.get("paths_filter")
    top_k = int(args.get("top_k", _TOP_K_DEFAULT))
    if top_k < 1:
        top_k = _TOP_K_DEFAULT
    if top_k > _TOP_K_MAX:
        top_k = _TOP_K_MAX
    near_lat = args.get("near_lat")
    near_lon = args.get("near_lon")
    radius_km = float(args.get("radius_km", 5.0))
    time_window = args.get("time_window") or "all"
    similarity_threshold = float(args.get("similarity_threshold", 0.0))
    # text_score_min: soglia sul contributo testuale isolato (cosine BGE-M3
    # + BM25 boost). Default 0.25 quando `query_text` e' presente: la query
    # diventa un FILTRO AND (es. "Matteo al mare" richiede match face E
    # match contenuto), non solo un boost di ranking. Default 0.0 quando
    # query_text assente. Bug live 15/5/2026: foto di Matteo a Parigi
    # entravano in "Matteo al mare" perche' face_score alto dominava
    # text_score basso, e la sola soglia su _score totale (default 0) non
    # filtrava. Override esplicito accettato via arg.
    # Default text_score_min tarato sulla distribuzione BGE-M3:
    # cosine reale per query "mare" su 923 entries di Matteo:
    #   >=0.25: 52% (troppo permissivo, include "ambiente domestico")
    #   >=0.30: 27%
    #   >=0.40: 11% (foto effettivamente al mare/spiaggia)
    #   >=0.45: 10% (top semantica stretta)
    # Soglia 0.40 separa correlazione semantica significativa da
    # neighborhood loose (BGE-M3 mappa "campo verde" e "mare" entrambi
    # outdoor → cosine 0.5-0.6 ma falsi positivi).
    if "text_score_min" in args:
        text_score_min = float(args["text_score_min"])
    elif args.get("query_text"):
        text_score_min = 0.40
    else:
        text_score_min = 0.0

    applied_paths_filter = None
    if paths_filter:
        path_set = {os.path.realpath(p) for p in paths_filter}
        before = len(entries)
        entries = [e for e in entries if os.path.realpath(e.get("path", "")) in path_set]
        applied_paths_filter = len(entries)
        if not entries:
            return {
                "entries": [], "n_above_threshold": 0,
                "error_class": "paths_filter_empty",
                "applied_paths_filter": 0,
                "_msg": f"paths_filter ridusse {before} entries a 0",
            }

    # Time window
    if time_window != "all":
        win = _parse_time_window(time_window)
        if win is not None:
            start, end = win
            kept: list[dict] = []
            for e in entries:
                ts = None
                t_iso = e.get("taken_at_iso")
                if t_iso:
                    try:
                        ts = datetime.fromisoformat(t_iso).timestamp()
                    except Exception:
                        ts = None
                # Fallback 1: parse path (cartelle datate user pattern)
                if ts is None:
                    ts = _extract_date_from_path(e.get("path", ""))
                # Fallback 2: mtime filesystem (meno affidabile)
                if ts is None:
                    try:
                        ts = float(e.get("mtime", 0.0))
                    except (TypeError, ValueError):
                        ts = 0.0
                if start <= ts <= end:
                    kept.append(e)
            entries = kept

    # GPS filter
    if near_lat is not None and near_lon is not None:
        kept = []
        for e in entries:
            g = e.get("exif_gps")
            if isinstance(g, dict) and "lat" in g and "lon" in g:
                d = _haversine_km(near_lat, near_lon, g["lat"], g["lon"])
                if d <= radius_km:
                    e["_gps_distance_km"] = d
                    kept.append(e)
        entries = kept

    # Identity filter — multi-name AND (`names` plurale §2.1)
    if names_clean:
        try:
            from persons_registry import resolve_face_embeddings_for_name
        except Exception:
            resolve_face_embeddings_for_name = None
        # Per ogni nome, resolve target embeddings. Se UN nome non e'
        # enrollato, ritorniamo errore esplicito (no fallback BM25 perche'
        # ammettrebbe falsi positivi su AND multi-persona).
        targets_per_name: list[list] = []
        for n in names_clean:
            embs = []
            if resolve_face_embeddings_for_name is not None:
                try:
                    embs = list(resolve_face_embeddings_for_name(n) or [])
                except Exception:
                    embs = []
            if not embs:
                return {
                    "entries": [], "n_above_threshold": 0,
                    "error_class": "person_not_enrolled",
                    "error": (
                        f"names: '{n}' non enrollato. Per AND multi-persona "
                        f"tutti i nomi devono essere enrollati via set_persons."
                    ),
                    "applied_paths_filter": applied_paths_filter,
                }
            targets_per_name.append(embs)
        # Filter entries: ogni entry deve avere una face match per OGNI name
        kept = []
        for e in entries:
            faces = e.get("faces", [])
            all_match = True
            best_scores: list[float] = []
            for target_embs in targets_per_name:
                best = 0.0
                for face in faces:
                    eidx = face.get("embedding_face_idx")
                    if eidx is None or emb_face is None:
                        continue
                    try:
                        eidx_int = int(eidx)
                    except (ValueError, TypeError):
                        continue
                    if eidx_int >= len(emb_face):
                        continue
                    fv = _l2_normalize(emb_face[eidx_int])
                    for tv in target_embs:
                        s = _cosine(fv, tv)
                        if s > best:
                            best = s
                if best < _FACE_MATCH_FLOOR:
                    all_match = False
                    break
                best_scores.append(best)
            if all_match:
                # face_score = media dei best per ogni nome
                e["_face_score"] = sum(best_scores) / len(best_scores)
                kept.append(e)
        entries = kept
        target_face_embs: list = []  # gia' applicato sopra
        name_unenrolled = False
        # Skip il blocco single-name che segue
        if not entries:
            return {
                "entries": [], "n_above_threshold": 0,
                "applied_paths_filter": applied_paths_filter,
                "_msg": f"nessuna foto contiene tutti i nomi: {names_clean}",
            }
        name = None  # consumed

    # Identity filter (single name) — il path classico resta per backward compat
    target_face_embs: list = []
    name_unenrolled = False
    if name:
        try:
            from persons_registry import resolve_face_embeddings_for_name
            target_face_embs = list(resolve_face_embeddings_for_name(name) or [])
        except Exception as ex:
            log.debug("resolve persons %r: %r", name, ex)
            target_face_embs = []
        # Fallback (ADR 0119-bis): se `name` non e' enrollato in
        # PersonsRegistry E non ci sono reference_images, riusa il nome
        # come `query_text` per cercare via path_tokens/description BM25.
        # Senza questo fallback la query ritorna top-K generico (stesse
        # foto per qualunque nome non registrato — bug live 9/5/2026).
        if not target_face_embs and not reference_images:
            name_unenrolled = True
            if not query_text:
                query_text = name
                log.info(
                    "find_images_indices: name=%r non enrollato, fallback "
                    "a query_text=%r (BM25 path_tokens/description)", name, name,
                )
    if reference_images:
        target_face_embs.extend(_extract_face_embeddings_from_reference(reference_images))

    if target_face_embs:
        kept = []
        for e in entries:
            faces = e.get("faces", [])
            best = 0.0
            best_face_idx = -1
            for fi, face in enumerate(faces):
                eidx = face.get("embedding_face_idx")
                if eidx is None or emb_face is None or eidx >= len(emb_face):
                    continue
                fv = _l2_normalize(emb_face[eidx])
                for tv in target_face_embs:
                    s = _cosine(fv, tv)
                    if s > best:
                        best = s
                        best_face_idx = fi
            if best >= _FACE_MATCH_FLOOR:
                e["_face_score"] = best
                e["_matched_face_idx"] = best_face_idx
                kept.append(e)
        entries = kept

    # Composition
    if min_face_pixels is not None:
        thr = int(min_face_pixels)
        kept = [e for e in entries if any(_bbox_area(f) >= thr for f in e.get("faces", []))]
        if not kept and entries:
            return {
                "entries": [], "n_above_threshold": 0,
                "error_class": "no_faces_above_size_threshold",
                "_msg": f"min_face_pixels={thr} azzera i candidati",
            }
        entries = kept
    if min_face_count is not None:
        thr = int(min_face_count)
        entries = [e for e in entries if len(e.get("faces", [])) >= thr]
    if max_face_count is not None:
        thr = int(max_face_count)
        entries = [e for e in entries if len(e.get("faces", [])) <= thr]

    # Content filter (query_text)
    text_scores: dict[int, float] = {}
    q_expanded: list[str] = []  # cache for output meta
    if query_text:
        # Query expansion strategia (15/5/2026 §7.3):
        # (1) LLM expansion (Gemma 4 26B middle, language-sensitive) — preferita.
        #     Produce sinonimi puliti anche per query brevi mono-token.
        #     Cache disk indefinita → costo solo prima call per query.
        # (2) Fallback corpus token (BGE-M3) per query lunghe se LLM fail.
        # (3) Fallback nessuna expansion (query brevi senza LLM) → BM25 sul
        #     keyword originale + cosine fallback hybrid.
        q_clean = query_text.strip()
        _is_short_mono = (len(q_clean.split()) == 1 and len(q_clean) <= 5)
        # Strategia (1) LLM
        if _query_expansion_llm_enabled():
            try:
                q_expanded = _expand_query_via_llm(query_text)
            except Exception as ex:
                log.debug("LLM expansion fallita: %r", ex)
                q_expanded = []
        # Strategia (2) corpus BGE-M3 SOLO per query lunghe (>5 char,
        # multi-token) e se LLM ha fallito.
        if (not q_expanded and idx_dir is not None
                and _query_expansion_enabled() and not _is_short_mono):
            try:
                tokens, embs = _corpus_token_embs(idx_dir)
                if tokens and embs is not None:
                    q_expanded = _expand_query_via_corpus(query_text, tokens, embs)
            except Exception as ex:
                log.debug("corpus expansion fallita: %r", ex)
                q_expanded = []
        # Fallback: niente expansion → q_terms = tokens query originale.
        q_terms = _normalize_text_for_bm25(
            " ".join(q_expanded) if q_expanded else query_text
        )
        q_vec = None
        try:
            from bge_embedding import BGEEmbeddingService
            te = BGEEmbeddingService()
            qv = te.embed_texts([query_text])
            if qv.ndim == 2 and qv.shape[0] == 1:
                q_vec = _l2_normalize(qv[0])
        except Exception as ex:
            log.debug("query embed fallito: %r", ex)
        # text_components[i] = (cos_score, bm25): teniamo i due termini
        # separati per il filtro hybrid (15/5/2026 §7.3). BGE-M3 da solo
        # confonde topici visivamente correlati (mare/neve entrambi outdoor
        # paesaggio); BM25 sui keyword distingue.
        text_components: dict[int, tuple[float, float]] = {}
        for i, e in enumerate(entries):
            cos_score = 0.0
            if q_vec is not None and emb_text is not None:
                t_idx = e.get("embedding_text_idx")
                if t_idx is not None and 0 <= t_idx < len(emb_text):
                    try:
                        t_idx_int = int(t_idx)
                    except (ValueError, TypeError):
                        t_idx_int = -1
                    if 0 <= t_idx_int < len(emb_text):
                        cos_score = _cosine(q_vec, _l2_normalize(emb_text[t_idx_int]))
            doc_terms = _normalize_text_for_bm25(
                e.get("description", "") + " "
                + " ".join(e.get("keywords", [])) + " "
                + " ".join(e.get("path_tokens", []))
            )
            bm25 = _bm25_score(q_terms, doc_terms)
            score = cos_score + 0.2 * min(bm25, 5.0)
            text_scores[i] = score
            text_components[i] = (cos_score, bm25)

    # Text filter (15/5/2026 §7.3).
    # Strategia in base alla query:
    # (A) q_expanded valido (BGE-M3 corpus expansion ok) → BM25 hard match
    #     sull'expansion. Doc deve contenere ALMENO UN keyword espanso.
    # (B) q_expanded assente o degenere (query brevi mono-token <=5 char,
    #     BGE-M3 mappa cosine alto con token irrelati → expansion inquinata)
    #     → fallback hybrid: bm25 > 0 con cosine >= text_score_min OR
    #     cosine >= _COSINE_STRONG (0.55). Precision sub-ottimale ma stable.
    _COSINE_STRONG = 0.55
    if query_text and text_score_min > 0.0:
        keep_paths = set()
        for i, e in enumerate(entries):
            cos_s, bm25_s = text_components.get(i, (0.0, 0.0))
            if q_expanded and len(q_expanded) >= 2:
                if bm25_s > 0 and cos_s >= 0.25:
                    keep_paths.add(e.get("path"))
            else:
                if bm25_s > 0 and cos_s >= text_score_min:
                    keep_paths.add(e.get("path"))
                elif cos_s >= _COSINE_STRONG:
                    keep_paths.add(e.get("path"))
        score_by_path = {e.get("path"): text_scores.get(i, 0.0)
                         for i, e in enumerate(entries)}
        entries = [e for e in entries if e.get("path") in keep_paths]
        text_scores = {i: score_by_path[e.get("path")]
                       for i, e in enumerate(entries)}

    # Composito
    scored = []
    for i, e in enumerate(entries):
        s = 0.0
        match_type = "compose"
        if i in text_scores:
            s += text_scores[i]
            match_type = "text"
        if "_face_score" in e:
            s += e["_face_score"]
            match_type = "face" if match_type == "compose" else "compose"
        if "_gps_distance_km" in e:
            d = e["_gps_distance_km"]
            s += max(0.0, 1.0 - d / max(radius_km, 0.001))
            if match_type == "compose":
                match_type = "gps"
        e["_score"] = s
        e["_match_type"] = match_type
        scored.append(e)

    scored = [e for e in scored if e.get("_score", 0.0) >= similarity_threshold]
    scored.sort(key=lambda x: x.get("_score", 0.0), reverse=True)
    n_above_threshold = len(scored)

    # Low-confidence: solo se text-only e tutti sotto floor
    if (
        query_text and not target_face_embs
        and not (near_lat is not None and near_lon is not None) and scored
    ):
        max_text_score = max(text_scores.values(), default=0.0)
        if max_text_score < _LOW_CONF_FLOOR:
            return {
                "entries": [], "n_above_threshold": 0,
                "error_class": "low_confidence",
                "applied_paths_filter": applied_paths_filter,
                "_msg": "tutti i match testuali sotto floor 0.12",
            }

    out_entries: list[dict] = []
    for e in scored[:top_k]:
        d = {
            "path": e.get("path"),
            "name": e.get("name"),
            "score": float(e.get("_score", 0.0)),
            "match_type": e.get("_match_type", "compose"),
            "description": e.get("description", ""),
            "keywords": e.get("keywords", []),
        }
        if e.get("location_hint"):
            d["location_hint"] = e["location_hint"]
        if e.get("activity_hint"):
            d["activity_hint"] = e["activity_hint"]
        if e.get("exif_gps"):
            d["gps"] = e["exif_gps"]
        if e.get("taken_at_iso"):
            d["taken_at"] = e["taken_at_iso"]
        if "_matched_face_idx" in e:
            faces = e.get("faces", [])
            mi = e["_matched_face_idx"]
            if 0 <= mi < len(faces):
                d["bbox"] = faces[mi].get("bbox")
        out_entries.append(d)

    out_dict: dict = {
        "entries": out_entries,
        "n_above_threshold": n_above_threshold,
        "applied_paths_filter": applied_paths_filter,
    }
    if name_unenrolled:
        out_dict["name_unenrolled"] = True
        out_dict["_msg"] = (
            f"persona '{name}' NON registrata in PersonsRegistry; "
            f"fallback a ricerca testuale via path_tokens/description."
        )
    return out_dict


def _check_args(args: dict) -> str | None:
    has_query = bool(args.get("query_text"))
    has_ref = bool(args.get("reference_images"))
    _names = args.get("names")
    has_name = bool(args.get("name")) or bool(
        isinstance(_names, list) and _names
    )
    has_gps = (args.get("near_lat") is not None and args.get("near_lon") is not None)
    has_paths_filter = bool(args.get("paths_filter"))
    has_face_filter = (
        args.get("min_face_pixels") is not None
        or args.get("min_face_count") is not None
        or args.get("max_face_count") is not None
    )
    if not (has_query or has_ref or has_name or has_gps or has_paths_filter or has_face_filter):
        return (
            "missing search criterion: provide one of query_text|name|"
            "reference_images|near_lat+near_lon|paths_filter|min_face_*"
        )
    return None


def invoke(args):
    legacy_idx = args.get("idx")
    if legacy_idx is not None and legacy_idx not in ("", "all"):
        log.warning(
            "find_images_indices: arg `idx=%r` ignorato post-ADR0117 (unified)",
            legacy_idx,
        )

    err = _check_args(args)
    if err:
        return {"ok": False, "error": err}

    top_k = int(args.get("top_k", _TOP_K_DEFAULT))
    if top_k < 1:
        return {"ok": False, "error": "top_k must be >= 1"}

    single_dir, multi_dirs, msg = _resolve_base_path(args.get("base_path"))
    if single_dir is None and multi_dirs is None:
        return {"ok": False, "error": msg or "could not resolve base_path"}

    if multi_dirs is not None:
        return _invoke_multi_dirs(multi_dirs, args, msg)

    idx_dir = _index_dir(single_dir)
    if not (idx_dir / "meta.json").exists():
        # Detect schema_too_old (legacy v3 dirs presenti)
        sha_dir = idx_dir.parent
        if any((sha_dir / legacy / "meta.json").exists() for legacy in ("scene", "persons", "gps")):
            return {
                "ok": False, "entries": [], "error_class": "schema_too_old",
                "error": (
                    "indice in schema v3 (3 indici disgiunti). "
                    "Migration v3→v4 richiesta: vedi runtime/index_schema_upgrade_v4.py"
                ),
                "base_path": str(single_dir),
                "schema_version": INDEX_SCHEMA_VERSION,
            }
        return {
            "ok": False, "entries": [], "error_class": "index_missing",
            "error": (
                f"unified index missing for {single_dir}. "
                f"Run create_images_indices(base_path='{single_dir}') first."
            ),
            "base_path": str(single_dir),
            # Hint per il PLANNER: chiudi con final_answer onesto invece
            # di riprovare. Bug live 15/5/2026: LLM riprova 3× → loop_break
            # generico. Con hint esplicito il PLANNER puo' usare il
            # messaggio user-facing direttamente.
            "final_message_hint": (
                f"Nessun indice immagini disponibile per `{single_dir}`. "
                f"Crea prima l'indice con `create_images_indices(base_path='{single_dir}')` "
                f"(richiede ~6s per foto, build asincrona)."
            ),
            "_terminal": True,
        }

    entries, emb_text, emb_face, meta = _load_unified_index(idx_dir)
    if not is_unified_schema(meta):
        return {
            "ok": False, "entries": [], "error_class": "schema_too_old",
            "error": (
                f"unified meta schema_version={meta.get('schema_version')} "
                f"< {INDEX_SCHEMA_VERSION}"
            ),
            "base_path": str(single_dir),
        }
    if not entries:
        return {
            "ok": True, "entries": [], "n_above_threshold": 0,
            "schema_version": INDEX_SCHEMA_VERSION,
            "base_path": str(single_dir),
            "_msg": "indice vuoto",
        }

    res = _filter_unified(entries, emb_text, emb_face, meta, args,
                            idx_dir=idx_dir)

    out: dict = {
        "ok": True,
        "entries": res.get("entries", []),
        "n_above_threshold": int(res.get("n_above_threshold", 0)),
        "base_path": str(single_dir),
        "schema_version": INDEX_SCHEMA_VERSION,
    }
    if res.get("error_class"):
        out["error_class"] = res["error_class"]
        out["entries"] = []
    if res.get("applied_paths_filter") is not None:
        out["applied_paths_filter"] = res["applied_paths_filter"]
    n_returned = len(out["entries"])
    if n_returned >= top_k and res.get("n_above_threshold", 0) > top_k:
        out["truncated"] = True
        out["truncated_what"] = "entries"
        out["used"] = n_returned
        out["available_total"] = int(res["n_above_threshold"])
        out["cap_field"] = "top_k"
        out["cap_value"] = int(top_k)
    # Attachments per la chat HTTP/Telegram (photo_endpoint + gallery).
    # ADR 0119-bis: find_images_indices popola attachments con kind=image
    # cosi' agent_runtime li propaga e la chat renderizza thumb/full.
    out["attachments"] = _build_attachments_from_entries(out["entries"])
    return out


def _build_attachments_from_entries(entries: list[dict]) -> list[dict]:
    """Costruisce la lista attachments per il rendering chat.

    Ogni entry con `path` valido diventa un attachment kind=image con
    basename + score + caption (description troncata). Il runtime li
    propaga al TurnLog e photo_endpoint genera thumb_url/full_url.
    """
    atts: list[dict] = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        p = e.get("path")
        if not isinstance(p, str) or not p:
            continue
        att: dict = {
            "kind": "image",
            "path": p,
            "basename": Path(p).name,
        }
        if e.get("score") is not None:
            try:
                att["score"] = float(e["score"])
            except (TypeError, ValueError):
                pass
        desc = e.get("description") or ""
        if desc:
            cap = str(desc).strip().replace("\n", " ")
            att["caption"] = cap[:200]
        atts.append(att)
    return atts


def _invoke_multi_dirs(dirs: list[Path], args: dict, msg: str | None) -> dict:
    all_entries: list[dict] = []
    n_above = 0
    error_classes: set[str] = set()
    schema_too_old_dirs: list[str] = []
    for d in dirs:
        idx_dir = _index_dir(d)
        if not (idx_dir / "meta.json").exists():
            error_classes.add("index_missing")
            continue
        entries, emb_text, emb_face, meta = _load_unified_index(idx_dir)
        if not is_unified_schema(meta):
            error_classes.add("schema_too_old")
            schema_too_old_dirs.append(str(d))
            continue
        if not entries:
            continue
        res = _filter_unified(entries, emb_text, emb_face, meta, args,
                                idx_dir=idx_dir)
        if res.get("error_class"):
            error_classes.add(res["error_class"])
            continue
        for e in res.get("entries", []):
            e["_source_dir"] = str(d)
        all_entries.extend(res.get("entries", []))
        n_above += int(res.get("n_above_threshold", 0))

    all_entries.sort(key=lambda e: e.get("score", 0.0), reverse=True)
    top_k = int(args.get("top_k", _TOP_K_DEFAULT))
    if top_k > _TOP_K_MAX:
        top_k = _TOP_K_MAX
    truncated_entries = all_entries[:top_k]

    out: dict = {
        "ok": True,
        "entries": truncated_entries,
        "n_above_threshold": n_above,
        "schema_version": INDEX_SCHEMA_VERSION,
        "_resolved_dirs": [str(d) for d in dirs],
        "_resolve_msg": msg or "",
    }
    if not truncated_entries and error_classes:
        out["ok"] = False
        out["error_classes"] = sorted(error_classes)
        if "schema_too_old" in error_classes:
            out["error_class"] = "schema_too_old"
            out["schema_too_old_dirs"] = schema_too_old_dirs
    # Truncated check: confronto contro n_above (totale above threshold
    # PRE-truncation a top_k in _filter_unified), non contro len(all_entries)
    # che e' gia' top-k troncato per dir e quindi degenere a top_k.
    # Bug live 15/5/2026: query "Matteo al mare" ritornava 100 entries di 117
    # totali senza truncated=True → final_answer "100 foto" inaccurato.
    if int(n_above) > top_k:
        out["truncated"] = True
        out["truncated_what"] = "entries"
        out["used"] = len(truncated_entries)
        out["available_total"] = int(n_above)
        out["cap_field"] = "top_k"
        out["cap_value"] = int(top_k)
    out["attachments"] = _build_attachments_from_entries(out["entries"])
    return out


def main():
    try:
        args = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        sys.stdout.write(json.dumps({"ok": False, "error": f"invalid input json: {e}"}))
        return
    result = invoke(args)
    sys.stdout.write(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
