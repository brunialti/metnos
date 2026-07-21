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
- `time_window`: filtro su data di scatto (EXIF taken_at_iso) o data dal
  path; NON mtime (data di modifica del file, mente sull'età foto §2.8).
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
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent.parent / "runtime"
sys.path.insert(0, str(_RUNTIME))

from messages import get as _msg  # noqa: E402
from executor_helpers import run_stdio  # noqa: E402
from index_schema import INDEX_SCHEMA_VERSION, is_unified_schema

log = logging.getLogger(__name__)

_TOP_K_DEFAULT = 100
_TOP_K_MAX = 200
_LOW_CONF_FLOOR = 0.12
_FACE_MATCH_FLOOR = 0.4
# Sigma del taglio-rilevanza scena DENTRO un set-identità (foto di UNA persona).
# Più basso del 3σ globale: nel sotto-corpus di una persona la scena è un CLUSTER,
# non un outlier raro → il 3σ sovra-restringe (bug live 9/6: «<persona> montagna»
# 2860→11). 2σ = coda superiore ~0.6%; sul corpus a embedding densi collassati
# (μ~0.61, σ~0.04) corrisponde al ginocchio di precisione misurato (~0.69 cos:
# sopra = montagna genuina; sotto = "vista/terrazza" borderline). §2.4 tarabile.
_IDENTITY_SCENE_SIGMA = 2.0

# Operatori SEMANTICI multi-persona (NON stopword): «alice E/insieme bob» =
# AND (foto con ENTRAMBI i volti); «alice O bob» = OR (unione). AND è il
# default. «insieme/con/together/with» NON sono rumore: confermano l'AND (Roberto
# 13/6: «insieme equivale ad AND»). Set CHIUSO minimo, riconosciuto a monte e
# tolto dalla scena (è un operatore, non un descrittore). i18n-estendibile.
_OR_MARKERS = frozenset({"o", "oppure", "or", "oder", "ou"})
_AND_MARKERS = frozenset({"insieme", "assieme", "con", "together", "with", "e",
                          "ed", "and", "et", "und"})


@lru_cache(maxsize=16)
def _scene_stopwords(lang: str) -> frozenset:
    """Stopword di SCENA per `lang` (ISO 639-1) dalla libreria `stopwordsiso`
    (i18n: ~58 lingue keyed by code, allineato a METNOS_LANG/`i18n.current_lang`).
    Cache per lingua. Best-effort §2.8: se libreria/lingua mancano → set vuoto
    (residuo non ripulito, il pipeline regge). Le parole di CONTENUTO (mare,
    montagna) NON sono stopword → sopravvivono. Sostituisce la lista hardcoded:
    lista curata e mantenuta a monte, non nel codice (Roberto 13/6)."""
    try:
        import stopwordsiso as _sw
        code = (lang or "").split("-")[0].lower() or "it"
        if _sw.has_lang(code):
            return frozenset(_sw.stopwords(code))
    except Exception as ex:
        log.debug("find_images_indices: stopwordsiso non disponibile (%r)", ex)
    return frozenset()


def _current_lang() -> str:
    """Lingua dell'istanza (i18n) per la selezione stopword. Fallback 'it'."""
    try:
        from i18n import current_lang
        return (current_lang() or "it")
    except Exception:
        return "it"


def _resolve_cap(args) -> tuple[int, bool]:
    """Ritorna (top_k_effettivo, explicit_cap).

    `max_results` (§2.1, Roberto 13/6) = conteggio ESPLICITO dell'utente
    («cerca 100 foto» → 100): PREVALE sul ranking (bypassa il taglio di
    rilevanza nel core) e fissa il cap senza il guard <50. `top_k` = budget
    INTERNO con guard anti-mistake del PLANNER (un top_k piccolo NON richiesto
    era un errore del LLM, 15/5). Unica fonte di verità per core + wrapper."""
    _mr = args.get("max_results")
    explicit = (isinstance(_mr, (int, float)) and not isinstance(_mr, bool)
                and int(_mr) > 0)
    tk = int(args.get("top_k", _TOP_K_DEFAULT))
    if explicit:
        tk = min(int(_mr), _TOP_K_MAX)
    elif tk < 50:
        tk = _TOP_K_DEFAULT
    if tk > _TOP_K_MAX:
        tk = _TOP_K_MAX
    return tk, explicit


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


def _canonical_corpus_path(base_path) -> str:
    """Delega alla SoT condivisa `index_schema.canonical_corpus_path`: chiave
    LOGICA stabile al mount per il corpus-workspace (symlink Immagini e suo
    target → stessa chiave, ma indipendente dal punto di mount NAS), resolve
    per dir reali. Fix 23/6: il vecchio `resolve()` legava la chiave al mount
    → rimount /tmp->/mnt orfanizzava l'indice → re-index spurio."""
    from index_schema import canonical_corpus_path as _ccp
    return _ccp(base_path)


def _index_dir(base_path: Path) -> Path:
    # Il digest del corpus e' calcolato sul path CANONICAL (symlink risolto),
    # coerente con create_images_indices._index_dir, cosi' symlink e path
    # reale mappano sullo stesso indice (fix 30/5/2026).
    digest = hashlib.sha256(
        _canonical_corpus_path(base_path).encode("utf-8")
    ).hexdigest()
    return _index_image_root() / digest[:16] / "unified"


@dataclass(frozen=True)
class _IndexedCorpus:
    """A persisted corpus label paired with its already materialized index.

    ``base_path`` is descriptive metadata and may be offline or intentionally
    absent from the reader sandbox. ``idx_dir`` is the authority-bearing
    materialized directory discovered under the read-only index root.
    """

    base_path: Path
    idx_dir: Path


def _path_identity(value) -> str:
    """Lexical absolute path identity that never requires source visibility."""
    path = Path(os.path.expanduser(str(value)))
    if not path.is_absolute():
        path = Path.cwd() / path
    return os.path.normcase(os.path.normpath(str(path)))


def _meta_base_path(meta_path: Path) -> Path | None:
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    raw = meta.get("base_path")
    if not isinstance(raw, str) or not raw.strip():
        return None
    return Path(os.path.expanduser(raw))


def _find_materialized_index(base_path: Path) -> _IndexedCorpus | None:
    """Resolve an explicit corpus from index metadata before probing storage.

    The direct digest remains the fast path.  The metadata scan is essential
    when the index was keyed by the logical workspace symlink while
    ``meta.json.base_path`` records its NAS target: inside the conforming
    reader sandbox neither the symlink nor the target is mounted.
    """
    direct = _index_dir(base_path)
    if (direct / "meta.json").exists():
        return _IndexedCorpus(base_path=base_path, idx_dir=direct)
    wanted = _path_identity(base_path)
    for meta_path in sorted(_index_image_root().glob("*/unified/meta.json")):
        persisted = _meta_base_path(meta_path)
        if persisted is not None and _path_identity(persisted) == wanted:
            return _IndexedCorpus(base_path=persisted,
                                  idx_dir=meta_path.parent)
    return None


def _is_dry_run() -> bool:
    return os.environ.get("METNOS_DRY_RUN", "0") == "1"


def _discover_indexed_dirs() -> list[_IndexedCorpus]:
    """Discover corpora from read-only index metadata, not from user storage.

    This keeps a query inside ``index:read``: the executor does not need to
    enumerate ``PATH_USER_DATA`` or mount the original photo trees merely to
    find an already materialized index.
    """
    dirs: list[_IndexedCorpus] = []
    seen: set[str] = set()
    for meta_path in sorted(_index_image_root().glob("*/unified/meta.json")):
        path = _meta_base_path(meta_path)
        if path is None:
            continue
        key = _path_identity(path)
        if key not in seen:
            seen.add(key)
            dirs.append(_IndexedCorpus(base_path=path,
                                       idx_dir=meta_path.parent))
    return dirs


# Sentinel nel campo `message`: path-like esplicito ben formato ma inesistente
# → il caller traduce in ERR_PATH_NOT_FOUND (no fallback silenzioso, opz 3).
_BP_NOT_FOUND = "\x00bp_not_found:"


def _resolve_base_path(base_path_arg) -> tuple[
        _IndexedCorpus | None, list[_IndexedCorpus] | None, str | None]:
    """Risolve `base_path` arg (3 modalita').

    Ritorna (single_dir, multi_dirs, message). Esattamente uno fra
    (single_dir, multi_dirs) e' valorizzato.
    """
    if base_path_arg is None or base_path_arg == "":
        dirs = _discover_indexed_dirs()
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
        # The source corpus may be offline while its materialized index remains
        # queryable.  Check the index before probing the source filesystem.
        materialized = _find_materialized_index(p)
        if materialized is not None:
            return materialized, None, None
        if p.exists() and p.is_dir():
            # Path esistente con indice → usalo direttamente. _index_dir
            # canonicalizza (resolve) cosi' symlink e path reale collimano.
            logical = p
            idx_dir = _index_dir(logical)
            if (idx_dir / "meta.json").exists():
                return _IndexedCorpus(logical, idx_dir), None, None
            # Path esiste ma senza indice → discovery prima di proporre build.
            discovered = _discover_indexed_dirs()
            if discovered:
                return None, discovered, (
                    f"base_path '{arg}' non indicizzato → fallback discovery "
                    f"({len(discovered)} indici trovati)"
                )
            return _IndexedCorpus(logical, idx_dir), None, None
        # Path-like (ben formato) ma INESISTENTE → ERRORE (decisione 2/6, opz 3):
        # un path esplicito che non esiste e' un errore, NON un fallback
        # silenzioso a un altro corpus (coerente con find_dirs §path-not-found).
        # Il fallback discovery resta SOLO per base_path vuoto o NOME SIMBOLICO
        # (categoria astratta tipo 'Immagini'/'viaggi', gestita sotto).
        return None, None, _BP_NOT_FOUND + arg
    # Arg simbolico (nome cartella, non un path). Match esatto sui figli di
    # user_data; altrimenti fallback discovery prima del dialog di build.
    discovered = _discover_indexed_dirs()
    target = arg.lower()
    for sub in discovered:
        if sub.base_path.name.lower() == target:
            return sub, None, None
    if discovered:
        return None, discovered, (
            f"base_path '{arg}' non corrisponde a una cartella nota → "
            f"fallback discovery ({len(discovered)} indici trovati)"
        )
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
    emb_image = None
    try:
        import numpy as np
        et_p = idx_dir / "embeddings_text.npy"
        if et_p.exists():
            emb_text = np.load(str(et_p))
        ef_p = idx_dir / "embeddings_face.npy"
        if ef_p.exists():
            emb_face = np.load(str(ef_p))
        # SigLIP scene embeddings (image-to-image, 6/7): fallback per
        # reference_images SENZA volto. Lazy = solo se il file c'è.
        ei_p = idx_dir / "embeddings_image.npy"
        if ei_p.exists():
            emb_image = np.load(str(ei_p))
    except Exception:
        pass
    meta["_emb_image"] = emb_image
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
        from virt import get_local_embedder
        te = get_local_embedder("text")
        embs = te.embed_texts(tokens).astype(np.float32, copy=False)
    except Exception as ex:
        log.warning("corpus tokens embed fail: %r", ex)
        return [], None
    # Query executors are strictly read-only.  Cache materialization belongs
    # to create_images_indices; a missing cache is computed in memory only.
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
        from virt import get_local_embedder
        te = get_local_embedder("text")
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


# ----- Date extraction from path/filename (15/5/2026) -------------------
# EXIF taken_at puo' mancare (foto vecchie, scansioni, screenshot,
# whatsapp export). mtime filesystem e' spesso inaffidabile (copy/move
# resetta). Pattern utente comune: foto organizzate in cartelle datate
# `Immagini/2024/2024 12 14 compl. Carol/IMG_xxx.jpg`. Parse data dal
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
      "fototessera carol.jpg" → None (no date in filename)
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
    # before-/after-YYYY[-MM]: il planner emette questo per "prima del 2000" /
    # "anteriori al 2000" / "dopo il 2010". before = tutto PRIMA dell'inizio
    # dell'anno/mese indicato; after = tutto DOPO la sua fine. (§2.4 confine NL.)
    m = re.match(r"^(before|after)-(\d{4})(?:-(\d{2}))?$", s)
    if m:
        direction = m.group(1)
        y = int(m.group(2))
        mo = int(m.group(3)) if m.group(3) else None
        if mo is None or 1 <= mo <= 12:
            if direction == "before":
                return 0.0, float(datetime(y, mo or 1, 1).timestamp())
            if mo is None:
                start = datetime(y + 1, 1, 1).timestamp()
            else:
                ny, nm = (y, mo + 1) if mo < 12 else (y + 1, 1)
                start = datetime(ny, nm, 1).timestamp()
            return float(start), float(now)
    return None


# Mesi IT+EN → numero. Usato per estrarre un time_window da query_text (§2.4).
_MONTHS_NUM = {
    "gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4, "maggio": 5,
    "giugno": 6, "luglio": 7, "agosto": 8, "settembre": 9, "ottobre": 10,
    "novembre": 11, "dicembre": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5,
    "june": 6, "july": 7, "august": 8, "september": 9, "october": 10,
    "november": 11, "december": 12,
}
# Filler temporali (IT+EN) da scartare se restano orfani dopo l'estrazione.
_TEMPORAL_FILLERS = {
    "del", "dello", "della", "dell", "dei", "degli", "delle", "nel", "nell",
    "nello", "nella", "di", "da", "l", "the", "of", "in", "anno", "year",
    "mese", "month",
}


def _split_temporal_from_query(query_text: str) -> tuple[str, str | None]:
    """Estrae un riferimento temporale (anno, o mese+anno) da `query_text` e lo
    converte in time_window canonico ('YYYY' / 'YYYY-MM'). Ritorna
    (query_residua, time_window|None).

    Razionale §2.4/§7.9: un anno in query_text è un FILTRO, non contenuto
    semantico. Lasciarlo nell'embedding inquina la ricerca (bug live "viaggi
    2016" → documenti che CITANO '2016' vincono sui viaggi reali). Estrazione
    deterministica, dominio-agnostica (solo grammatica data: anno 19xx/20xx +
    nomi mese IT/EN). Mese senza anno NON viene estratto (ambiguo)."""
    if not query_text:
        return query_text, None
    year: int | None = None
    month: int | None = None
    keep: list[str] = []
    for tok in query_text.split():
        core = tok.strip(".,;:!?()[]'\"«»").lower()
        if re.fullmatch(r"(19|20)\d\d", core):
            year = int(core)
            continue
        if core in _MONTHS_NUM:
            month = _MONTHS_NUM[core]
            continue
        keep.append(tok)
    if year is None:
        return query_text, None  # niente anno → nessuna estrazione
    cleaned = [w for w in keep
               if w.strip(".,;:!?()[]'\"«»").lower() not in _TEMPORAL_FILLERS]
    residual = " ".join(cleaned).strip()
    tw = f"{year:04d}-{month:02d}" if month is not None else f"{year:04d}"
    return residual, tw


def _split_persons_from_query(query_text: str,
                              lang: str = "it") -> tuple[str, list[str], str]:
    """Estrae le persone ENROLLATE + l'operatore semantico da `query_text`.
    Ritorna (query_residua_scena, [name, ...], op) con op ∈ {'and','or'}.

    Razionale §7.9/§2.4 (gemello di `_split_temporal_from_query`): «ospite al
    mare» = volto ospite ∩ scena «al mare»; «alice e bob insieme» =
    names=['Alice','Bob'], op=AND (foto con ENTRAMBI i volti); «alice o bob» =
    op=OR (unione). Default AND.

    - Match nomi SOLO contro il set CHIUSO di PersonsRegistry (no falsi positivi
      su parole comuni); un token conta se mappa a UN unico slug. Più token-
      persona = più persone (NON ambiguo: l'ambiguità è UN token→2 slug).
    - Operatore (Roberto 13/6 «insieme equivale ad AND»): «insieme/con/together/
      with»=AND-conferma, «o/oppure/or»=OR. Riconosciuti a monte e TOLTI dalla
      scena (sono operatori, non descrittori).
    - Residuo-scena ripulito dalle stopword della LINGUA (`stopwordsiso`, i18n):
      query di sole persone → identità pura (residuo vuoto); le parole di
      CONTENUTO (mare, montagna) sopravvivono."""
    if not query_text:
        return query_text, [], "and"
    registry = None
    try:
        from persons_registry import PersonsRegistry
        registry = PersonsRegistry(read_only=True)
        persons = registry.list_all()
    except Exception:
        return query_text, [], "and"
    finally:
        if registry is not None:
            registry.close()
    if not persons:
        return query_text, [], "and"
    tok2slug: dict[str, set] = {}
    slug2name: dict[str, str] = {}
    for p in persons:
        slug = (p.get("slug") or "").strip()
        if not slug:
            continue
        nm = (p.get("name") or slug).strip()
        slug2name[slug] = nm
        toks = set(re.split(r"[_\s]+", slug.lower())) | set(re.split(r"\s+", nm.lower()))
        for t in toks:
            t = t.strip()
            if len(t) < 2:
                continue
            tok2slug.setdefault(t, set()).add(slug)
    matched: list[str] = []
    keep: list[str] = []
    op = "and"
    for tok in query_text.split():
        core = tok.strip(".,;:!?()[]'\"«»").lower()
        slugs = tok2slug.get(core)
        if slugs and len(slugs) == 1:
            s = next(iter(slugs))
            if s not in matched:
                matched.append(s)
            continue  # nome-persona → fuori dalla scena
        if core in _OR_MARKERS:
            op = "or"
            continue  # operatore → fuori dalla scena
        if core in _AND_MARKERS:
            continue  # operatore AND-conferma → fuori dalla scena
        keep.append(tok)
    if not matched:
        return query_text, [], "and"
    stop = _scene_stopwords(lang)
    residual = [t for t in keep
                if t.strip(".,;:!?()[]'\"«»").lower() not in stop]
    return " ".join(residual).strip(), [slug2name.get(s, s) for s in matched], op


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


def _apply_relevance_gate(entries, text_components, text_scores, rel_thr):
    """Filtra le entries tenendo solo coseno >= rel_thr e ri-mappa text_scores
    sui nuovi indici. Identita' = INDICE originale (univoco per costruzione):
    il path NON e' affidabile come chiave (duplicati symlink/copie, oppure
    None) -> con un set di path i dupe sotto-soglia passerebbero il gate e i
    punteggi collasserebbero (last-wins). Ritorna (entries, text_scores)."""
    kept = [(i, e) for i, e in enumerate(entries)
            if text_components.get(i, (0.0, 0.0))[0] >= rel_thr]
    entries_out = [e for _, e in kept]
    scores_out = {new_i: text_scores.get(old_i, 0.0)
                  for new_i, (old_i, _) in enumerate(kept)}
    return entries_out, scores_out


_SCENE_MATCH_FLOOR = 0.35  # cosine SigLIP: sotto = scena non correlata


def _apply_scene_similarity(entries, reference_images, meta, args):
    """Similarity di SCENA (SigLIP image-to-image, 6/7): codifica le
    reference con SigLIP e le confronta col corpus (`embeddings_image`).
    Ritorna un result-dict come il ramo volto, o None se non applicabile
    (indice senza emb_image / SigLIP assente / reference non codificabili) —
    il chiamante degrada onesto §2.8. Il floor `_SCENE_MATCH_FLOOR` tiene
    fuori le scene non correlate."""
    import numpy as np
    emb_image = meta.get("_emb_image")
    if emb_image is None or not len(emb_image):
        return None
    try:
        from clip_embedding import get_clip_engine
        eng = get_clip_engine()
        refs = [p for p in reference_images
                if isinstance(p, str) and os.path.exists(p)]
        if not refs:
            return None
        ref_emb = eng.embed_images(refs, normalize=True)
    except Exception as ex:
        log.warning("scene similarity non disponibile: %r", ex)
        return None
    if ref_emb is None or not len(ref_emb):
        return None
    # media delle reference = query di scena (robusta a più foto input).
    q = _l2_normalize(np.mean(ref_emb, axis=0))
    kept = []
    for e in entries:
        ii = e.get("embedding_image_idx")
        if ii is None or ii >= len(emb_image):
            continue
        s = float(np.dot(q, _l2_normalize(emb_image[ii])))
        if s >= _SCENE_MATCH_FLOOR:
            e["_scene_score"] = s
            e["_score"] = s
            kept.append(e)
    kept.sort(key=lambda e: e.get("_scene_score", 0.0), reverse=True)
    top_k, _ = _resolve_cap(args)
    if top_k and top_k > 0:
        kept = kept[:top_k]
    return {
        "entries": kept,
        "n_above_threshold": len(kept),
        "match_mode": "scene_similarity",
        "_msg": (f"Similarity di SCENA (SigLIP) su {len(reference_images)} "
                 f"foto di riferimento: {len(kept)} risultati."),
    }


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
    # §7.3: True dopo un filtro-VOLTO (name/names/reference). In tal caso il
    # query_text e' SOLO ranking, mai esclusione delle foto della persona.
    identity_filtered = False
    # Multi-persona AND (15/5/2026): `names` array, ogni nome deve essere
    # presente in OGNI foto matchata. Es. ["alice","carol"] → foto con
    # AMBEDUE. Bug live: PLANNER passava `name="Alice, Carol"` come
    # stringa unica → lookup fallisce → BM25 fallback ammette qualunque
    # foto con un solo nome. names plurale risolve §2.1.
    names_list = args.get("names")
    if isinstance(names_list, list) and names_list:
        names_clean = [str(n).strip() for n in names_list if str(n).strip()]
    elif isinstance(name, str) and "," in name:
        # Tolleranza wildcard §2.4: LLM ha emesso "Alice, Carol" → split
        names_clean = [n.strip() for n in name.split(",") if n.strip()]
        name = None  # promosso a names plurale
    else:
        names_clean = []
    # Operatore multi-persona: 'and' (default, foto con TUTTI) | 'or' (unione).
    names_op = "or" if str(args.get("names_op", "and")).lower() == "or" else "and"
    reference_images = args.get("reference_images") or []
    min_face_pixels = args.get("min_face_pixels")
    min_face_count = args.get("min_face_count")
    max_face_count = args.get("max_face_count")
    paths_filter = args.get("paths_filter")
    # max_results (§2.1, Roberto 13/6): conteggio ESPLICITO richiesto dall'utente
    # ("cerca 100 foto" → max_results=100). Quando presente PREVALE sul ranking:
    # bypassa il taglio di rilevanza adattivo (sotto) → ritorna le top-N per
    # punteggio, anche match più deboli (resta solo il pavimento anti-rumore
    # text_score_min). Distinto dal budget interno top_k, che mantiene il guard
    # <50 anti-mistake del PLANNER (15/5): un top_k piccolo NON richiesto era un
    # errore del LLM; max_results invece è intenzione esplicita dell'utente.
    top_k, explicit_cap = _resolve_cap(args)
    near_lat = args.get("near_lat")
    near_lon = args.get("near_lon")
    radius_km = float(args.get("radius_km", 5.0))
    time_window = args.get("time_window") or "all"
    similarity_threshold = float(args.get("similarity_threshold", 0.0))
    # text_score_min: soglia sul contributo testuale isolato (cosine BGE-M3
    # + BM25 boost). Default 0.25 quando `query_text` e' presente: la query
    # diventa un FILTRO AND (es. "Carol al mare" richiede match face E
    # match contenuto), non solo un boost di ranking. Default 0.0 quando
    # query_text assente. Bug live 15/5/2026: foto di Carol a Parigi
    # entravano in "Carol al mare" perche' face_score alto dominava
    # text_score basso, e la sola soglia su _score totale (default 0) non
    # filtrava. Override esplicito accettato via arg.
    # Default text_score_min tarato sulla distribuzione BGE-M3:
    # cosine reale per query "mare" su 923 entries di Carol:
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

    # match_all (31/5/2026): enumera TUTTO il corpus SOLO quando e' l'UNICO
    # criterio (caso «quante foto in totale» / «quanti GB occupano»). Se il
    # proposer lo combina con un criterio SPECIFICO (query_text/name/gps/volti),
    # quel criterio VINCE e match_all e' ignorato — l'utente vuole una ricerca
    # filtrata, non tutto il corpus. Bug 31/5: «fammi vedere foto di persone in
    # montagna» con match_all=true azzerava query_text → 31445 random. §2.4.
    # match_all=true da solo non ha criteri da azzerare → passa _check_args e
    # il downstream (nessun narrowing) ritorna tutto.

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
        if win is None:
            # §2.8 NO SILENT FAILURE: il filtro temporale e' stato richiesto ma il
            # formato non e' riconosciuto. Ignorarlo restituirebbe foto NON
            # filtrate mentre l'assembler annuncia "prima del 2000" (esito falso).
            # Errore onesto con i formati validi.
            return {
                "entries": [], "n_above_threshold": 0,
                "error_class": "bad_time_window", "error_code": "ERR_ARG_INVALID",
                "_msg": (f"time_window={time_window!r} non riconosciuto. Formati: "
                         "today, yesterday, last-7d, YYYY, YYYY-MM, "
                         "before-YYYY[-MM], after-YYYY[-MM]."),
            }
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
                # Fallback: data dal path (cartelle/nomi datati). NIENTE mtime:
                # e' la data di MODIFICA del file (spesso una copia recente) e
                # MENTE sulle foto vecchie — una scansione del 1995 copiata nel
                # 2024 sembrerebbe "dopo il 2010" (§2.8 falso esito). Senza data
                # EXIF/path la foto e' NON DATABILE → fuori dalla finestra.
                if ts is None:
                    ts = _extract_date_from_path(e.get("path", ""))
                if ts is None:
                    continue
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

    # Identity filter — multi-name AND/OR (`names` plurale §2.1, op da `names_op`)
    multi_unenrolled: list[str] = []
    if names_clean:
        try:
            from persons_registry import resolve_face_embeddings_for_name
        except Exception:
            resolve_face_embeddings_for_name = None
        # Resolve target embeddings per nome. Un nome NON enrollato non puo'
        # essere matchato per volto: invece di fallire l'INTERA query (vecchio
        # comportamento), filtriamo sui nomi ENROLLATI e segnaliamo i mancanti
        # (§2.8 — Roberto «se uno solo e' enrolled»). AND/OR si applicano ai soli
        # enrollati; il non-enrollato e' dichiarato, non silenziosamente perso.
        targets_per_name: list[list] = []
        enrolled_names: list[str] = []
        for n in names_clean:
            embs = []
            if resolve_face_embeddings_for_name is not None:
                try:
                    embs = list(resolve_face_embeddings_for_name(n) or [])
                except Exception:
                    embs = []
            if embs:
                targets_per_name.append(embs)
                enrolled_names.append(n)
            else:
                multi_unenrolled.append(n)
        if not targets_per_name:
            return {
                "entries": [], "n_above_threshold": 0,
                "error_class": "person_not_enrolled",
                "error": (f"nessun nome enrollato fra {names_clean}: impossibile "
                          f"filtrare per volto (registra via set_persons)."),
                "applied_paths_filter": applied_paths_filter,
            }
        # Filter entries: best match per OGNI nome enrollato; AND → tutti
        # matchano, OR → almeno uno.
        kept = []
        for e in entries:
            faces = e.get("faces", [])
            per_name_best: list[float] = []
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
                per_name_best.append(best)
            n_matched = sum(1 for b in per_name_best if b >= _FACE_MATCH_FLOOR)
            if names_op == "or":
                keep_it = n_matched >= 1
                score = max(per_name_best) if per_name_best else 0.0
            else:  # and
                keep_it = n_matched == len(targets_per_name)
                score = (sum(per_name_best) / len(per_name_best)
                         if per_name_best else 0.0)
            if keep_it:
                e["_face_score"] = score
                kept.append(e)
        entries = kept
        identity_filtered = True
        target_face_embs: list = []  # gia' applicato sopra
        name_unenrolled = False
        # Skip il blocco single-name che segue
        if not entries:
            _rel = "tutti i" if names_op == "and" else "alcuno dei"
            _note = (f" (non enrollati, ignorati: {multi_unenrolled})"
                     if multi_unenrolled else "")
            return {
                "entries": [], "n_above_threshold": 0,
                "applied_paths_filter": applied_paths_filter,
                "names_unenrolled": multi_unenrolled,
                "_msg": f"nessuna foto contiene {_rel} nomi: {enrolled_names}{_note}",
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
        # §2.8 no silent failure: se reference_images sono state fornite ma
        # NESSUN volto è stato estratto → l'indice NON ha image-to-image
        # embedding (solo face). Senza face il filtro è NOOP e ritorneremmo
        # TUTTE le entries del corpus, ingannando l'utente. Errore onesto.
        if not target_face_embs:
            # Nessun VOLTO nelle reference → prova la similarity di SCENA
            # (SigLIP image-to-image, 6/7): «trova foto simili a QUESTO
            # paesaggio». Fallback onesto solo se l'indice ha emb_image e
            # SigLIP sa codificare la reference; altrimenti errore §2.8.
            scene_res = _apply_scene_similarity(
                entries, reference_images, meta, args)
            if scene_res is not None:
                return scene_res
            return {
                "entries": [], "n_above_threshold": 0,
                "error_class": "no_face_in_reference",
                "_msg": (f"Nessun volto rilevato nelle {len(reference_images)} "
                         "foto di riferimento e similarity di scena non "
                         "disponibile (indice senza embedding immagine o "
                         "SigLIP assente)."),
            }

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
        identity_filtered = True

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
        # Deterministic, local-only expansion from the indexed corpus.  Short
        # mono-token queries intentionally skip expansion because the local
        # embedding neighbourhood is too broad; BM25 + cosine remain active.
        q_clean = query_text.strip()
        _is_short_mono = (len(q_clean.split()) == 1 and len(q_clean) <= 5)
        if (idx_dir is not None and _query_expansion_enabled()
                and not _is_short_mono):
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
            from virt import get_local_embedder
            te = get_local_embedder("text")
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
                + e.get("path_context", "") + " "  # ADR 0166: contesto cartella
                + " ".join(e.get("keywords", [])) + " "
                + " ".join(e.get("path_tokens", []))
            )
            bm25 = _bm25_score(q_terms, doc_terms)
            score = cos_score + 0.2 * min(bm25, 5.0)
            text_scores[i] = score
            text_components[i] = (cos_score, bm25)

    # Text filter (15/5/2026 §7.3).
    # §7.3 UNIVERSALE: l'identita' (volto risolto) e' un FILTRO DURO di
    # appartenenza. La scena (query_text residuo dopo lo split persona) RESTRINGE
    # DENTRO le foto della persona SOLO se e' una scena reale; se e' rumore
    # ("cerca foto") il gate svuoterebbe → fallback al set-identita' intero
    # (scena = solo ranking). Cosi': "ospite montagna" = volto∩montagna
    # (ristretto); "cerca foto ospite" = tutte le sue foto. Bug live 9/6: 2860
    # foto dell'ospite NON ristrette da "in montagna" perche' il gate era saltato
    # del tutto sotto identita'.
    if query_text and text_score_min > 0.0:
        # Taglio di rilevanza ADATTIVO (core: runtime/relevance_cut.py, §7.3).
        # Gli embedding densi collassano le similarita' coseno in una banda
        # stretta ad alta media (μ~0.6 misurato su questo corpus): una soglia
        # ASSOLUTA e' priva di senso (99% del corpus supera 0.40; 91% supera
        # 0.55) — per questo il vecchio filtro a soglia fissa lasciava passare
        # l'intero corpus ("persone in montagna" → 31062/31062). La rilevanza
        # e' RELATIVA: solo gli outlier nella coda superiore della distribuzione
        # PER-QUERY. Regola 3-sigma: tieni cos >= μ+3σ (soglia statistica, non
        # un valore di dominio), con `text_score_min` come pavimento assoluto
        # anti-rumore per query senza match reali. Il segnale di gating e' il
        # coseno (semantica pura); il bm25 resta nel composito SOLO per il
        # ranking (sotto): "persone" matcha quasi tutte le foto → come gate
        # inquina, come tie-break ordina.
        from relevance_cut import adaptive_relevance_threshold
        cos_all = [text_components.get(i, (0.0, 0.0))[0]
                   for i in range(len(entries))]
        # Sotto identità il candidato è il sotto-corpus di UNA persona: la scena è
        # un cluster, non un outlier → soglia più inclusiva (2σ) per non scartare
        # le foto-scena genuine. Senza identità resta il 3σ globale (default).
        if explicit_cap:
            # Conteggio esplicito utente (Roberto 13/6): il NUMERO prevale sul
            # ranking → niente taglio sigma, solo il pavimento anti-rumore
            # (text_score_min). I top-N per punteggio vengono presi più sotto.
            rel_thr = text_score_min
        elif identity_filtered:
            rel_thr = adaptive_relevance_threshold(
                cos_all, sigma=_IDENTITY_SCENE_SIGMA, floor=text_score_min)
        else:
            rel_thr = adaptive_relevance_threshold(cos_all, floor=text_score_min)
        _g_entries, _g_scores = _apply_relevance_gate(
            entries, text_components, text_scores, rel_thr)
        if not identity_filtered:
            entries, text_scores = _g_entries, _g_scores
        elif _g_entries:
            # Identita' presente + scena reale (il gate tiene >=1): restringi a
            # volto∩scena. Se _g_entries fosse vuoto (residuo = rumore), tieni il
            # set-identita' intero (sotto, nessuna riassegnazione).
            entries, text_scores = _g_entries, _g_scores

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
        query_text and not identity_filtered
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
        # size_bytes esplicito per aggregati (compute_entries op=sum key=size_bytes).
        # Sorgente: campo `size` di entries.jsonl (schema v4).
        _sz = e.get("size")
        if isinstance(_sz, (int, float)) and _sz >= 0:
            d["size_bytes"] = int(_sz)
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

    # Aggregati su corpus COMPLETO matched (scored intero, non solo top_k).
    # Permette query «quante X / quanti GB» di leggere metadata direttamente,
    # senza compute_entries su lista truncated (ADR §7.3 general-purpose).
    total_size_bytes = 0
    n_with_size = 0
    for e in scored:
        _sz = e.get("size")
        if isinstance(_sz, (int, float)) and _sz >= 0:
            total_size_bytes += int(_sz)
            n_with_size += 1

    out_dict: dict = {
        "entries": out_entries,
        "n_above_threshold": n_above_threshold,
        "applied_paths_filter": applied_paths_filter,
        "metadata": {
            "total_count": n_above_threshold,
            "total_size_bytes": total_size_bytes,
            "total_size_gb": round(total_size_bytes / (1024 ** 3), 2),
            "n_with_size": n_with_size,
        },
    }
    if name_unenrolled:
        out_dict["name_unenrolled"] = True
        out_dict["_msg"] = (
            f"persona '{name}' NON registrata in PersonsRegistry; "
            f"fallback a ricerca testuale via path_tokens/description."
        )
    # §2.8: nomi multi-persona NON enrollati → dichiarati (filtro ridotto agli
    # enrollati, non silenziosamente perso). Roberto «se uno solo e' enrolled».
    if multi_unenrolled:
        out_dict["names_unenrolled"] = multi_unenrolled
        out_dict["_msg"] = (
            f"nomi non enrollati (non filtrabili per volto): {multi_unenrolled}; "
            f"risultati filtrati sui soli enrollati."
        )
    return out_dict


def _check_args(args: dict) -> str | None:
    if bool(args.get("match_all")):
        return None  # match_all=true e' un criterio valido: enumera tutto il corpus
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


def _index_missing_result(base_path: Path | None) -> dict:
    """Return an honest handoff; a read executor never starts a build."""
    target = base_path or (_user_data_root() / "Immagini")
    return {
        "ok": False,
        "entries": [],
        "error_class": "index_missing",
        "error_code": "image_index_missing",
        "error": _msg("ERR_IMAGE_INDEX_MISSING", base_path=str(target)),
        "base_path": str(target),
        "recommended_action": {
            "executor": "create_images_indices",
            "args": {"base_path": str(target)},
        },
    }


def invoke(args):
    if not isinstance(args, dict):
        return {
            "ok": False,
            "entries": [],
            "error_class": "invalid_input",
            "error_code": "args_not_object",
            "error": _msg("ERR_ARGS_NOT_OBJECT"),
        }
    legacy_idx = args.get("idx")
    if legacy_idx is not None and legacy_idx not in ("", "all"):
        log.warning(
            "find_images_indices: arg `idx=%r` ignorato post-ADR0117 (unified)",
            legacy_idx,
        )

    err = _check_args(args)
    if err:
        return {"ok": False, "error": err}

    # §2.4: un anno/mese-anno in query_text è un filtro temporale, non
    # contenuto. Estrailo in time_window (se non già esplicito) prima dello
    # scoring, così l'embedding semantico non viene inquinato dall'anno.
    if args.get("query_text") and not args.get("time_window"):
        _residual, _tw = _split_temporal_from_query(str(args["query_text"]))
        if _tw is not None:
            args = dict(args)
            args["time_window"] = _tw
            if _residual:
                args["query_text"] = _residual
            else:
                # query SOLO temporale ("foto del 2016") → enumera la finestra
                args.pop("query_text", None)
                args["match_all"] = True
            log.info("find_images_indices: temporal split → time_window=%r, "
                     "query_text=%r", _tw, args.get("query_text"))

    # §7.9 FUSIONE IDENTITÀ: un nome di persona ENROLLATA dentro query_text è
    # un filtro-VOLTO, non scena. Estrailo in `name` (se non già esplicito) così
    # la ricerca INTRECCIA volto∩scena ("ospite al mare" → ospite ∩ mare) invece
    # di cercare "ospite" come testo (bug live 8/6: trovava il mare, non la persona).
    # Dopo lo split temporale → "ospite al mare 2016" = volto∩scena∩tempo.
    if args.get("query_text") and not args.get("name") and not args.get("names"):
        _lang = str(args.get("_lang") or "").strip() or _current_lang()
        _resid, _persons, _op = _split_persons_from_query(
            str(args["query_text"]), _lang)
        if _persons:
            args = dict(args)
            if len(_persons) == 1:
                args["name"] = _persons[0]
            else:
                args["names"] = _persons  # multi-persona
                args["names_op"] = _op    # 'and' (default) | 'or'
            if _resid:
                args["query_text"] = _resid
            else:
                args.pop("query_text", None)  # solo persone → tutte le loro foto
                args["match_all"] = True
            log.info("find_images_indices: persons split → names=%r op=%s query_text=%r",
                     _persons, _op, args.get("query_text"))

    top_k, explicit_cap = _resolve_cap(args)
    # Validazione top_k ESPLICITO (schema minimum=1): _resolve_cap normalizza i
    # piccoli-ma-validi (1-49 → default 100, guard anti-mistake), ma 0/negativi/
    # non-numerici restano malformati → reject sul valore GREZZO.
    _raw_tk = args.get("top_k")
    if _raw_tk is not None:
        try:
            _tk_ok = int(_raw_tk) >= 1
        except (ValueError, TypeError):
            _tk_ok = False
        if not _tk_ok:
            return {"ok": False, "error": _msg("ERR_ARG_NOT_POSITIVE_INT", arg="top_k")}

    single_corpus, multi_dirs, msg = _resolve_base_path(args.get("base_path"))
    if single_corpus is None and multi_dirs is None:
        # Path-like esplicito inesistente (opz 3): errore, non dialog di build.
        if msg and msg.startswith(_BP_NOT_FOUND):
            _bad = msg[len(_BP_NOT_FOUND):]
            return {"ok": False, "entries": [], "error_class": "not_found",
                    "error_code": "ERR_PATH_NOT_FOUND",
                    "error": _msg("ERR_PATH_NOT_FOUND", path=_bad)}
        return _index_missing_result(None)

    if multi_dirs is not None:
        return _invoke_multi_dirs(multi_dirs, args, msg)

    single_dir = single_corpus.base_path
    idx_dir = single_corpus.idx_dir
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
        return _index_missing_result(single_dir)

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
    # Propaga metadata aggregato (total_count, total_size_bytes) calcolato
    # su corpus COMPLETO matched. Permette al PLANNER di rispondere
    # «quante X / quanti GB» senza compute_entries su lista truncated.
    if isinstance(res.get("metadata"), dict):
        out["metadata"] = res["metadata"]
    if res.get("error_class"):
        out["error_class"] = res["error_class"]
        out["entries"] = []
    if res.get("applied_paths_filter") is not None:
        out["applied_paths_filter"] = res["applied_paths_filter"]
    # §2.8: propaga le note di onestà (nomi non enrollati non filtrabili per
    # volto, fallback testuale) dal core al risultato — altrimenti perse nel
    # rebuild del wrapper.
    if res.get("names_unenrolled"):
        out["names_unenrolled"] = res["names_unenrolled"]
    if res.get("name_unenrolled"):
        out["name_unenrolled"] = True
    if res.get("_msg"):
        out["_msg"] = res["_msg"]
    n_returned = len(out["entries"])
    if n_returned >= top_k and res.get("n_above_threshold", 0) > top_k:
        out["truncated"] = True
        out["truncated_what"] = _msg("MSG_OBJECT_ENTRIES")
        out["used"] = n_returned
        out["available_total"] = int(res["n_above_threshold"])
        out["cap_field"] = "max_results" if explicit_cap else "top_k"
        out["cap_value"] = int(top_k)
        if explicit_cap:
            out["truncated_intentional"] = True  # §2.11: l'utente ha chiesto N
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
        # Data della foto (Roberto 6/7): la proiezione entries espone
        # `taken_at` (EXIF taken_at_iso dall'indice). La UI la mostra
        # nell'header per-foto della gallery e nel lightbox.
        taken = e.get("taken_at") or e.get("taken_at_iso")
        if taken:
            att["date"] = str(taken)[:19]
        atts.append(att)
    return atts


def _invoke_multi_dirs(dirs: list[_IndexedCorpus], args: dict,
                       msg: str | None) -> dict:
    all_entries: list[dict] = []
    n_above = 0
    total_size_bytes = 0
    n_with_size = 0
    error_classes: set[str] = set()
    schema_too_old_dirs: list[str] = []
    merged_unenrolled: list[str] = []
    for corpus in dirs:
        d = corpus.base_path
        idx_dir = corpus.idx_dir
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
        if res.get("names_unenrolled"):
            merged_unenrolled = res["names_unenrolled"]  # dir-independent
        if res.get("error_class"):
            error_classes.add(res["error_class"])
            continue
        for e in res.get("entries", []):
            e["_source_dir"] = str(d)
        all_entries.extend(res.get("entries", []))
        n_above += int(res.get("n_above_threshold", 0))
        # Somma metadata aggregati per-dir su corpus completo matched.
        _md = res.get("metadata") or {}
        if isinstance(_md, dict):
            total_size_bytes += int(_md.get("total_size_bytes", 0) or 0)
            n_with_size += int(_md.get("n_with_size", 0) or 0)

    all_entries.sort(key=lambda e: e.get("score", 0.0), reverse=True)
    top_k, explicit_cap = _resolve_cap(args)
    if top_k > _TOP_K_MAX:
        top_k = _TOP_K_MAX
    truncated_entries = all_entries[:top_k]

    out: dict = {
        "ok": True,
        "entries": truncated_entries,
        "n_above_threshold": n_above,
        "schema_version": INDEX_SCHEMA_VERSION,
        "_resolved_dirs": [str(corpus.base_path) for corpus in dirs],
        "_resolve_msg": msg or "",
        "metadata": {
            "total_count": n_above,
            "total_size_bytes": total_size_bytes,
            "total_size_gb": round(total_size_bytes / (1024 ** 3), 2),
            "n_with_size": n_with_size,
        },
    }
    if merged_unenrolled:
        out["names_unenrolled"] = merged_unenrolled
        out["_msg"] = (f"nomi non enrollati (non filtrabili per volto): "
                       f"{merged_unenrolled}; risultati sui soli enrollati.")
    if not truncated_entries and error_classes:
        out["ok"] = False
        out["error_classes"] = sorted(error_classes)
        if "schema_too_old" in error_classes:
            out["error_class"] = "schema_too_old"
            out["schema_too_old_dirs"] = schema_too_old_dirs
        elif "no_face_in_reference" in error_classes:
            out["error_class"] = "no_face_in_reference"
            out["error"] = ("Nessun volto rilevato nelle foto di "
                            "riferimento. L'indice locale supporta solo "
                            "similarity per VOLTO (ArcFace), non per "
                            "scena/oggetto generico. Per ricerca scena "
                            "sul web usa 'cerca foto simili sul web' "
                            "(Google Vision API).")
        elif "index_missing" in error_classes:
            missing = _index_missing_result(
                dirs[0].base_path if dirs else None)
            out.update({
                "error_class": missing["error_class"],
                "error_code": missing["error_code"],
                "error": missing["error"],
                "recommended_action": missing["recommended_action"],
            })
        elif error_classes:
            out["error_class"] = sorted(error_classes)[0]
    # Truncated check: confronto contro n_above (totale above threshold
    # PRE-truncation a top_k in _filter_unified), non contro len(all_entries)
    # che e' gia' top-k troncato per dir e quindi degenere a top_k.
    # Bug live 15/5/2026: query "Carol al mare" ritornava 100 entries di 117
    # totali senza truncated=True → final_answer "100 foto" inaccurato.
    if int(n_above) > top_k:
        out["truncated"] = True
        out["truncated_what"] = _msg("MSG_OBJECT_ENTRIES")
        out["used"] = len(truncated_entries)
        out["available_total"] = int(n_above)
        out["cap_field"] = "max_results" if explicit_cap else "top_k"
        out["cap_value"] = int(top_k)
        if explicit_cap:
            out["truncated_intentional"] = True  # §2.11: l'utente ha chiesto N
    out["attachments"] = _build_attachments_from_entries(out["entries"])
    return out


def main():
    run_stdio(invoke)


if __name__ == "__main__":
    main()
