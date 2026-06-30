#!/usr/bin/env python3
"""create_images_indices — executor di Metnos.

Costruisce o aggiorna l'indice unificato delle immagini (ADR 0117 v4) in
`base_path` per ricerche veloci future. UN solo storage per corpus, UN
solo schema; sostituisce il modello a 3 indici disgiunti scene/persons/gps.

Storage:
    ~/.local/share/metnos/index/image/<sha8(base_path)>/unified/
        entries.jsonl           - una riga per FOTO (non per faccia)
        embeddings_text.npy     - shape (N, dim_text) float32, per
                                  ricerca semantica via description
        embeddings_face.npy     - shape (M, 512) float32, M = sum(faces)
        meta.json               - {schema_version=4, model_text, model_vlm,
                                   model_face, n_entries, ...}

Per-foto pipeline:
    1. EXIF + dims + sha256 (cheap).
    2. ArcFace detect (RetinaFace + ArcFace embedding, medium).
    3. VLM call → JSON {description, keywords, location_hint, activity_hint}
       via HTTP a `localhost:8081/v1/chat/completions` (Qwen2-VL-7B servito
       separatamente da Roberto in foreground).
    4. MiniLM/BGE su description → embedding_text (medium).
    5. Append entry a entries.jsonl tmp + atomic rename ogni N=500.

Resume incrementale: legge meta.json (n_entries, last_offset) e skip foto
gia' processate matchando sha256+mtime.

Backward compat: arg `idx` ignorato (warning log) — il vecchio modello a
3 indici e' superseded da ADR 0117. Vedi runtime/index_schema_upgrade_v4.py
per migration v3→v4.

Contratto:
    stdin:  JSON con args (base_path, recursive?, force?, max_files?,
                            idx? IGNORED, dry_run?)
    stdout: JSON {ok, ok_count, fail_count, base_path, n_entries_total,
                   refreshed_count, last_refresh_at, index_path,
                   schema_version=4, [truncated, ...]}
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

# Permetti import dei moduli runtime/. Universal pattern (rename/depth-agnostic):
# env METNOS_RUNTIME settato da agent_runtime > fallback walk-up via marker.
_RUNTIME = os.environ.get("METNOS_RUNTIME") or next(
    str(p / "runtime") for p in Path(__file__).resolve().parents
    if (p / "runtime" / "config.py").is_file())
sys.path.insert(0, _RUNTIME)


from messages import get as _msg  # noqa: E402
from executor_helpers import run_stdio  # noqa: E402
from index_schema import INDEX_SCHEMA_VERSION

log = logging.getLogger(__name__)

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".webp", ".tiff", ".bmp"}
_INDEX_VERSION = INDEX_SCHEMA_VERSION  # 4
_CHECKPOINT_EVERY = 500
def _resolve_vlm_url(env_val: str | None) -> str:
    """Normalizza il URL VLM: env puo' essere base (http://h:port) o full path.

    Risolve il bug 9/5/2026 in cui METNOS_VLM_URL=http://127.0.0.1:8081 era
    consumato letteralmente come endpoint, causando 100% HTTP 404 su 30k
    foto durante il batch live. Convenzione SDK-like (OpenAI client): se
    l'env var non contiene gia' '/v1/' o '/chat/completions', appende
    '/v1/chat/completions' alla base.
    """
    val = (env_val or "http://127.0.0.1:8081").strip()
    if "/v1/" in val or val.endswith("/chat/completions"):
        return val
    return val.rstrip("/") + "/v1/chat/completions"


# Config VLM virtualizzata: i default vengono da `virt.get_vlm()` (cioè da
# `~/.config/metnos/vlm_tiers.toml`), così cambiare modello/endpoint = editare il
# TOML, non il codice. L'env (METNOS_VLM_*) resta override esplicito a precedenza
# massima (back-compat). Vedi virt/__init__.py::get_vlm.
def _vlm_cfg() -> dict:
    try:
        from virt import get_vlm
        return get_vlm()
    except Exception:
        return {}

_VLM = _vlm_cfg()
_VLM_URL = _resolve_vlm_url(os.environ.get("METNOS_VLM_URL") or _VLM.get("endpoint"))
_VLM_MODEL = os.environ.get("METNOS_VLM_MODEL") or _VLM.get("model", "qwen3vl-2b")
_VLM_TIMEOUT_S = int(os.environ.get("METNOS_VLM_TIMEOUT_S") or _VLM.get("timeout_s", 60))
# Leve di accuratezza testuale (default dal TOML). Long-edge piu' alto = piu'
# dettaglio (scene fini, testo-in-foto) a costo di piu' vision-token/latenza;
# max_tokens: descrizioni piu' ricche.
_VLM_MAX_EDGE = int(os.environ.get("METNOS_VLM_MAX_EDGE") or _VLM.get("max_edge", 1024))
_VLM_MAX_TOKENS = int(os.environ.get("METNOS_VLM_MAX_TOKENS") or _VLM.get("max_tokens", 512))


def _index_image_root() -> Path:
    v = os.environ.get("METNOS_INDEX_ROOT")
    if v:
        return Path(v) / "image"
    base = os.environ.get("METNOS_USER_DATA")
    base_p = Path(base) if base else Path.home() / ".local" / "share" / "metnos"
    return base_p / "index" / "image"


def _is_dry_run() -> bool:
    return os.environ.get("METNOS_DRY_RUN", "0") == "1"


def _index_dir(base_path: Path) -> Path:
    """Risolve la dir dell'indice unificato per `base_path`.

    Path CANONICAL (symlink risolto): coerente con find_images_indices._index_dir.
    Build e lookup devono concordare sul digest; il default workspace
    `~/.local/share/metnos/Immagini` e' un symlink verso il mount reale, quindi
    symlink e path reale devono mappare sullo stesso indice (fix 30/5/2026).
    `invoke()` passa gia' un path resolved, ma canonicalizziamo anche qui per
    robustezza ai caller diretti."""
    from index_schema import canonical_corpus_path
    canon = canonical_corpus_path(base_path)
    digest = hashlib.sha256(canon.encode("utf-8")).hexdigest()
    return _index_image_root() / digest[:16] / "unified"


def _walk_images(base: Path, recursive: bool, max_files: int) -> tuple[list[Path], bool]:
    out: list[Path] = []
    truncated = False
    walker = base.rglob("*") if recursive else base.iterdir()
    for p in walker:
        try:
            if not p.is_file():
                continue
        except OSError:
            continue
        if p.suffix.lower() not in _IMAGE_EXTS:
            continue
        out.append(p)
        if len(out) >= max_files:
            truncated = True
            break
    return out, truncated


def _scan_dir_extensions(base: Path, recursive: bool) -> tuple[int, int, int, set[str]]:
    """Conta file per estensione. Ritorna (n_total, n_image, n_other, other_exts)."""
    n_total = 0
    n_image = 0
    n_other = 0
    other_exts: set[str] = set()
    walker = base.rglob("*") if recursive else base.iterdir()
    for p in walker:
        try:
            if not p.is_file():
                continue
        except OSError:
            continue
        n_total += 1
        ext = p.suffix.lower()
        if ext in _IMAGE_EXTS:
            n_image += 1
        else:
            n_other += 1
            other_exts.add(p.suffix.upper() if p.suffix else "<no_ext>")
    return n_total, n_image, n_other, other_exts


def _file_signature(path: Path) -> tuple[float, int]:
    st = path.stat()
    return float(st.st_mtime), int(st.st_size)


def _sha256_file(path: Path, *, max_bytes: int = 64 * 1024 * 1024) -> str:
    """sha256 del file (caps a max_bytes per evitare blow-up su file enormi).

    Per foto tipiche (<20MB) e' identico al sha completo; per file >max_bytes
    si calcola sha256 del prefisso, che e' comunque piu' robusto di mtime+size.
    """
    h = hashlib.sha256()
    read = 0
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
            read += len(chunk)
            if read >= max_bytes:
                break
    return h.hexdigest()


def _open_image_with_exif(path: Path):
    """Apre PIL.Image + EXIF dict. Ritorna (img, exif_named_dict)."""
    try:
        from PIL import Image
        from PIL.ExifTags import TAGS, GPSTAGS
        img = Image.open(str(path))
        img.load()
    except Exception:
        return None, {}
    try:
        exif_raw = img._getexif() or {}
    except Exception:
        exif_raw = {}
    # Risolvi tag a nomi
    named: dict = {}
    for tag_id, value in exif_raw.items():
        tname = TAGS.get(tag_id, tag_id)
        if tname == "GPSInfo" and isinstance(value, dict):
            gps = {GPSTAGS.get(k, k): v for k, v in value.items()}
            named["GPSInfo"] = gps
        else:
            named[tname] = value
    return img, named


def _exif_taken_at_iso(exif: dict) -> str | None:
    for key in ("DateTimeOriginal", "DateTime"):
        v = exif.get(key)
        if v:
            try:
                date_part, time_part = str(v).split(" ", 1)
                return f"{date_part.replace(':', '-')}T{time_part}"
            except (ValueError, AttributeError):
                continue
    return None


def _exif_gps(exif: dict) -> dict | None:
    gps = exif.get("GPSInfo")
    if not isinstance(gps, dict):
        return None

    def _dec(coord, ref):
        try:
            d = float(coord[0])
            m = float(coord[1])
            s = float(coord[2])
            dec = d + m / 60.0 + s / 3600.0
            if ref in ("S", "W"):
                dec = -dec
            return dec
        except (TypeError, IndexError, ValueError):
            return None

    lat = _dec(gps.get("GPSLatitude"), gps.get("GPSLatitudeRef", "N"))
    lon = _dec(gps.get("GPSLongitude"), gps.get("GPSLongitudeRef", "E"))
    if lat is None or lon is None:
        return None
    out = {"lat": float(lat), "lon": float(lon)}
    return out


# ── VLM call ────────────────────────────────────────────────────────────


def _vlm_prompt(lang: str, filename: str, parent_dir: str) -> str:
    """Costruisce il prompt VLM nella lingua corrente con hint filename+parent.

    `filename` e `parent_dir` sono passati come INDIZI: il VLM li valuta e
    li usa solo se aggiungono informazioni non deducibili dall'immagine
    (es. data, evento, soggetto). Numeri/codici/path generici → ignorati.

    Layout (ottimizzato 10/5/2026 09:50 per KV-prefix caching llama.cpp):
    parte FISSA prima (DEVI/schema/REGOLE) → cacheable contiguamente
    fra request di slot diversi e fra foto diverse. Hint variabile in
    CODA → cache miss minimo (~50 token tail). Pre-tuning il hint era
    nel mezzo, sprecava cache su ~280 token di REGOLE post-hint.
    """
    if (lang or "it").lower().startswith("en"):
        return (
            "Describe this photo in English. Respond ONLY with valid JSON "
            "in the exact format below, no extra text:\n"
            "{\n"
            '  "description": "descriptive text (max 50 words): main '
            'subjects, number of people, setting/place, relevant objects, '
            'dominant colors and visible action",\n'
            '  "keywords": ["8-15", "specific", "keywords"],\n'
            '  "location_hint": "place or environment (e.g., beach, '
            'mountain, kitchen, office); short",\n'
            '  "activity_hint": "main action/activity (e.g., running, '
            'dining, sleeping); short"\n'
            "}\n"
            "RULES: describe ONLY visible things, no assumptions; do not "
            "name people; keep keywords in English. If you cannot infer "
            'a location/activity, use empty string "". '
            "Use the path hints below ONLY if they ADD information not "
            "deducible from the image (e.g., date, event, subject name). "
            "Ignore numeric IDs / generic paths / camera codes.\n"
            f"\nFilename: {filename}\nParent folder: {parent_dir}"
        )
    # Default IT
    return (
        "Descrivi questa foto in italiano. Rispondi SOLO con JSON valido "
        "nel formato esatto seguente, senza testo aggiuntivo:\n"
        "{\n"
        '  "description": "frase descrittiva (max 50 parole): soggetti '
        'principali, numero di persone, ambiente/luogo, oggetti rilevanti, '
        'colori dominanti e azione visibile",\n'
        '  "keywords": ["8-15", "parole", "chiave", "specifiche"],\n'
        '  "location_hint": "luogo o ambiente (es. spiaggia, montagna, '
        'cucina, ufficio); breve",\n'
        '  "activity_hint": "azione/attivita\' principale (es. correre, '
        "cenare, dormire); breve\"\n"
        "}\n"
        "REGOLE: descrivi SOLO cose visibili, niente supposizioni; niente "
        "nomi propri di persone; mantieni le keyword in italiano. Se non "
        'identifichi una location/activity, usa stringa vuota "". '
        "Usa gli indizi di path qui sotto SOLO se AGGIUNGONO informazioni "
        "non deducibili dall'immagine (es. data, nome evento, soggetto). "
        "Se sono numeri/codici/percorsi generici, IGNORALI.\n"
        f"\nFilename: {filename}\nCartella: {parent_dir}"
    )


# ── Indicizzazione intelligente: contesto di cartella (ADR 0166) ──────────────
# Il VLM-7B descrive la SCENA ma scarta gli indizi di path: una foto di
# Notre-Dame resta "una vetrata gotica", senza "Parigi". La conoscenza del
# mondo "Parigi→viaggio" ce l'ha l'LLM-testo, non l'embedding né il VLM.
# Quindi classifichiamo la CARTELLA una volta (per cartella-unica, cache) e
# fondiamo un contesto ESPLICITO nell'embedding testuale → ricerca di categorie
# astratte ("foto dei viaggi") diventa possibile. §7.9: l'LLM è giustificato (un
# gazetteer place→trip sarebbe hardcoding vietato §7.3); §2.2 generale.
_FOLDER_CATEGORIES = {"VIAGGIO", "EVENTO", "PERSONE", "DOCUMENTI", "ALTRO"}
_FOLDER_CTX_CACHE: dict[str, tuple[str, str]] = {}  # label → (categoria, luogo)
_FOLDER_CTX_LLM = None


def _folder_ctx_llm():
    global _FOLDER_CTX_LLM
    if _FOLDER_CTX_LLM is None:
        from llm_router import LLMRouter
        _FOLDER_CTX_LLM = LLMRouter()
    return _FOLDER_CTX_LLM


def _classify_folder_label(label: str, lang: str) -> tuple[str, str]:
    """LLM → (categoria∈_FOLDER_CATEGORIES, luogo). Output STRUTTURATO (vs
    framing free-form, inaffidabile). Memoizzato per label. L'enum è universale;
    il prompt è nella lingua dell'istanza (i nomi cartella sono in quella lingua)."""
    label = (label or "").strip()
    if not label:
        return ("ALTRO", "")
    if label in _FOLDER_CTX_CACHE:
        return _FOLDER_CTX_CACHE[label]
    if (lang or "it").lower().startswith("en"):
        sysp = (
            "Classify a photo folder by its NAME. Answer EXACTLY 'CATEGORY|PLACE'.\n"
            "CATEGORY in {VIAGGIO, EVENTO, PERSONE, DOCUMENTI, ALTRO} (use these "
            "exact words). VIAGGIO = trip/holiday/tourist place away from home, "
            "PLACE = place name (or empty). EVENTO = birthday/school/ceremony/"
            "home. DOCUMENTI = scans/certificates. PERSONE = a named person. "
            "ALTRO = unclear.\nExamples:\n  'march malta' => VIAGGIO|Malta\n"
            "  'roberto birthday' => EVENTO|\n  'misc to sort' => ALTRO|\n"
            "Only 'CATEGORY|PLACE', nothing else."
        )
    else:
        sysp = (
            "Classifica una cartella di foto dal suo NOME. Rispondi ESATTAMENTE "
            "'CATEGORIA|LUOGO'.\nCATEGORIA in {VIAGGIO, EVENTO, PERSONE, "
            "DOCUMENTI, ALTRO} (usa queste parole esatte). VIAGGIO = gita/"
            "vacanza/luogo turistico lontano da casa, LUOGO = nome del posto (o "
            "vuoto). EVENTO = compleanno/scuola/cerimonia/casa. DOCUMENTI = "
            "scansioni/certificati. PERSONE = una persona con nome. ALTRO = "
            "incerto.\nEsempi:\n  'marzo malta' => VIAGGIO|Malta\n  "
            "'compleanno roberto' => EVENTO|\n  'varie da classificare' => ALTRO|\n"
            "Solo 'CATEGORIA|LUOGO', niente altro."
        )
    cat, place = "ALTRO", ""
    try:
        out = _folder_ctx_llm().chat(sysp, f"'{label}' =>", tier="middle").text
        line = (out or "").strip().splitlines()[0] if out else ""
        line = re.sub(r"^[\*\s>]+", "", line).strip()
        parts = (line.split("|") + [""])[:2]
        c = parts[0].strip().upper()
        if c in _FOLDER_CATEGORIES:
            cat = c
            place = parts[1].strip().strip("'\"")
    except Exception as ex:
        log.debug("folder classify fallita %r: %r", label, ex)
    _FOLDER_CTX_CACHE[label] = (cat, place)
    return (cat, place)


def _assemble_path_context(cat: str, place: str, label: str, lang: str) -> str:
    """Frase di contesto (lingua istanza) dalla classificazione. DISCRIMINANTE:
    solo VIAGGIO contiene 'viaggio'/'trip' → il coseno separa viaggi da casa."""
    en = (lang or "it").lower().startswith("en")
    if cat == "VIAGGIO":
        if en:
            return f"Photos of a trip to {place}." if place else "Photos of a trip."
        return f"Foto di un viaggio a {place}." if place else "Foto di un viaggio."
    if cat == "EVENTO":
        return f"Photos of an event: {label}." if en else f"Foto di un evento: {label}."
    if cat == "PERSONE":
        return f"Photos of: {label}." if en else f"Foto di: {label}."
    if cat == "DOCUMENTI":
        return (f"Photos of documents and scans: {label}." if en
                else f"Foto di documenti e scansioni: {label}.")
    return f"Photos: {label}." if en else f"Foto: {label}."


def folder_path_context(parent_dir: str, lang: str) -> str:
    """Contesto di cartella da fondere nell'embedding testuale (intelligent
    indexing). Rimuove l'anno (rumore) dal nome prima di classificare. Vuoto se
    label vuoto. API pubblica: la usa anche il re-embed retroattivo."""
    label = re.sub(r"\b(19|20)\d\d\b", "", parent_dir or "").replace("-", " ")
    label = re.sub(r"\s+", " ", label).strip()
    if not label:
        return ""
    cat, place = _classify_folder_label(label, lang)
    return _assemble_path_context(cat, place, label, lang)


def _call_vlm(img_path: Path, *, url: str = _VLM_URL,
              model: str = _VLM_MODEL,
              timeout_s: int = _VLM_TIMEOUT_S) -> dict:
    """Chiama il VLM su una foto (index-build). Plumbing HTTP/parse condivisa
    in `runtime/vlm_client.py` (SoT, §7.2 dedup 1/7/2026); qui resta solo il
    prompt index-build (hint cartella/filename). Ritorna {description,
    keywords, location_hint, activity_hint} (+_vlm_error su fallimento).
    Test-patchabile via mock.patch.object(cii, "_call_vlm")."""
    from vlm_client import describe_image as _describe
    try:
        from config import DEFAULT_LANG as _lang  # type: ignore
    except Exception:
        _lang = "it"
    prompt = _vlm_prompt(_lang, img_path.name, img_path.parent.name)
    return _describe(img_path, prompt=prompt, url=url, model=model,
                     timeout_s=timeout_s, max_tokens=_VLM_MAX_TOKENS)


# ── Existing storage I/O ────────────────────────────────────────────────


def _load_existing(idx_dir: Path) -> tuple[list[dict], object | None, object | None]:
    """Carica entries.jsonl + embeddings_text.npy + embeddings_face.npy.

    Ritorna (entries, emb_text_or_None, emb_face_or_None).
    """
    entries_path = idx_dir / "entries.jsonl"
    emb_text_path = idx_dir / "embeddings_text.npy"
    emb_face_path = idx_dir / "embeddings_face.npy"
    if not entries_path.exists():
        return [], None, None
    entries: list[dict] = []
    try:
        with entries_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return [], None, None
    emb_text = None
    emb_face = None
    try:
        import numpy as np
        if emb_text_path.exists():
            emb_text = np.load(str(emb_text_path))
        if emb_face_path.exists():
            emb_face = np.load(str(emb_face_path))
    except Exception:
        pass
    return entries, emb_text, emb_face


def _atomic_write_index(
    idx_dir: Path,
    entries: list[dict],
    emb_text,
    emb_face,
    emb_image=None,
    *,
    base_path: Path,
    model_text: str,
    dim_text: int,
    model_vlm: str,
    model_face: str,
    model_image: str = "none",
    dim_image: int = 0,
) -> None:
    idx_dir.mkdir(parents=True, exist_ok=True)
    # entries.jsonl
    entries_path = idx_dir / "entries.jsonl"
    tmp = entries_path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(e, ensure_ascii=False) + "\n")
    tmp.replace(entries_path)
    # embeddings
    if emb_text is not None and len(entries) > 0:
        import numpy as np
        p = idx_dir / "embeddings_text.npy"
        tmp_v = p.with_suffix(".tmp.npy")
        np.save(str(tmp_v), emb_text)
        tmp_v.replace(p)
    if emb_face is not None and len(emb_face) > 0:
        import numpy as np
        p = idx_dir / "embeddings_face.npy"
        tmp_v = p.with_suffix(".tmp.npy")
        np.save(str(tmp_v), emb_face)
        tmp_v.replace(p)
    if emb_image is not None and len(emb_image) > 0:
        import numpy as np
        p = idx_dir / "embeddings_image.npy"
        tmp_v = p.with_suffix(".tmp.npy")
        np.save(str(tmp_v), emb_image)
        tmp_v.replace(p)
    # meta
    meta = {
        "schema_version": _INDEX_VERSION,
        "version": _INDEX_VERSION,
        "n_entries": len(entries),
        "n_faces": int(len(emb_face)) if emb_face is not None else 0,
        "n_images_with_visual_emb": int(len(emb_image)) if emb_image is not None else 0,
        "base_path": str(base_path),
        "model_text": model_text,
        "dim_text": int(dim_text),
        "model_vlm": model_vlm,
        "model_face": model_face,
        "model_image": model_image,
        "dim_image": int(dim_image),
        "last_refresh_at": time.time(),
    }
    (idx_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ── Build pipeline ──────────────────────────────────────────────────────


def _build_unified(
    paths: list[Path],
    existing_entries: list[dict],
    existing_emb_text,
    existing_emb_face,
    existing_emb_image=None,
    *,
    force: bool,
) -> dict:
    """Build/refresh dell'indice unificato.

    Ritorna dict {entries, emb_text, emb_face, emb_image, ok_count,
                  fail_count, model_text, dim_text, model_vlm, model_face,
                  model_image, dim_image}.
    Lazy import dei backend (face_embedding, bge_embedding, clip_embedding)
    per consentire il dry_run senza dipendenze installate.
    """
    import numpy as np

    # Lingua istanza per il contesto di cartella (intelligent indexing, ADR 0166).
    try:
        from config import DEFAULT_LANG as _lang  # type: ignore
    except Exception:
        _lang = "it"

    # Lazy imports
    from face_embedding import get_face_engine
    try:
        from virt import get_embedder
        text_engine: object | None = None  # init alla prima call
        text_dim = 1024
        text_model_name = "bge-m3"
    except Exception:
        text_engine = None
        text_dim = 0
        text_model_name = "none"

    # §7.3: SigLIP image embedding (image-to-image visual similarity)
    try:
        from virt import get_embedder
        clip_engine_obj = get_embedder("image")
        if clip_engine_obj.available:
            image_dim = int(clip_engine_obj.dimension)
            image_model_name = "clip_siglip"
        else:
            clip_engine_obj = None
            image_dim = 0
            image_model_name = "none"
    except Exception as _ex:
        log.warning("ClipEngine init fallito: %r — embedding_image omesso", _ex)
        clip_engine_obj = None
        image_dim = 0
        image_model_name = "none"

    face_engine = get_face_engine()
    if not face_engine.available:
        raise FileNotFoundError(
            f"FaceEngine model pack non installato in {face_engine._model_dir}"
        )

    # Mappa path → (entry, idx) per resume incrementale
    by_path: dict[str, tuple[dict, int]] = {}
    if not force:
        for i, e in enumerate(existing_entries):
            p = e.get("path")
            if isinstance(p, str):
                by_path[p] = (e, i)

    new_entries: list[dict] = []
    new_emb_text_list: list = []
    new_emb_face_list: list = []
    new_emb_image_list: list = []
    text_idx_counter = 0
    face_idx_counter = 0
    image_idx_counter = 0
    ok = 0
    fail = 0

    # Parallelismo client-side: cap a llama-server --parallel slots (2).
    # Insightface (ONNX) e urllib sono thread-safe; BGE resta nel main
    # thread per preservare l'ordine di idx.
    _n_par = int(os.environ.get("METNOS_VLM_PARALLEL", "1"))
    _n_par = max(1, min(_n_par, 8))

    def _process_one(p):
        """Restituisce (path, status, payload). Eseguibile in parallelo."""
        try:
            mtime, size = _file_signature(p)
        except OSError:
            return (p, "fail_io", None)
        sp = str(p)
        prev = by_path.get(sp)
        if prev and not force:
            prev_e, _prev_idx = prev
            if prev_e.get("mtime") == mtime and prev_e.get("size") == size:
                # Resume: riusa SigLIP+volti gia' calcolati. Ma se l'asse VLM
                # manca (description vuota o `_vlm_error` da un build col server
                # VLM giu'), ritenta SOLO il VLM invece di ricongelare un
                # fallimento transitorio (§2.8 no silent failure). Il lavoro
                # visivo/volti, costoso e gia' valido, viene riusato.
                _has_vlm = bool(prev_e.get("description")) and "_vlm_error" not in prev_e
                if _has_vlm:
                    return (p, "resume", prev_e)
                vlm_out = _call_vlm(p)
                return (p, "resume_revlm", (prev_e, vlm_out))
        try:
            entry = _build_entry(p, mtime, size, face_engine=face_engine)
            return (p, "new", entry)
        except Exception as ex:
            return (p, "fail_build", repr(ex))

    if _n_par > 1:
        from concurrent.futures import ThreadPoolExecutor
        _pool = ThreadPoolExecutor(max_workers=_n_par)
    else:
        _pool = None

    _CHUNK = max(8, _n_par * 4)
    _i = 0
    _last_path: Path | None = None

    def _emit_prog():
        """Scrive il file di progresso (atomic) ogni 25 elementi o a fine.
        Usato da tutti i rami lenti (new, resume_revlm) per non lasciare un
        refresh di sole-re-VLM senza avanzamento visibile."""
        _pp = os.environ.get("METNOS_PROGRESS_FILE")
        if not _pp or not ((ok + fail) % 25 == 0 or (ok + fail) == len(paths)):
            return
        try:
            _tmp = _pp + ".tmp"
            with open(_tmp, "w") as _fh:
                json.dump({
                    "ts": time.time(), "phase": "running",
                    "n_total": len(paths), "n_processed": ok + fail,
                    "ok": ok, "fail": fail,
                    "last_path": str(_last_path) if _last_path else "",
                    "pct": round(100.0 * (ok + fail) / max(1, len(paths)), 2),
                    "parallel": _n_par,
                }, _fh)
            os.replace(_tmp, _pp)
        except Exception:
            pass

    while _i < len(paths):
        _chunk = paths[_i:_i + _CHUNK]
        if _pool is not None:
            _results = list(_pool.map(_process_one, _chunk))
        else:
            _results = [_process_one(p) for p in _chunk]

        for p, _status, _payload in _results:
            _last_path = p
            if _status == "fail_io" or _status == "fail_build":
                if _status == "fail_build":
                    log.warning("build entry failed %s: %s", p, _payload)
                fail += 1
                continue

            if _status == "resume":
                prev_e = _payload
                new_e = dict(prev_e)
                if "embedding_text_idx" in prev_e and existing_emb_text is not None:
                    old_t_idx = prev_e["embedding_text_idx"]
                    if 0 <= old_t_idx < len(existing_emb_text):
                        new_emb_text_list.append(existing_emb_text[old_t_idx])
                        new_e["embedding_text_idx"] = text_idx_counter
                        text_idx_counter += 1
                # Resume image embedding se presente in indice precedente
                if "embedding_image_idx" in prev_e and existing_emb_image is not None:
                    old_i_idx = prev_e["embedding_image_idx"]
                    if 0 <= old_i_idx < len(existing_emb_image):
                        new_emb_image_list.append(existing_emb_image[old_i_idx])
                        new_e["embedding_image_idx"] = image_idx_counter
                        image_idx_counter += 1
                new_faces: list[dict] = []
                for face in prev_e.get("faces", []):
                    nf = dict(face)
                    old_f_idx = face.get("embedding_face_idx")
                    if (old_f_idx is not None and existing_emb_face is not None
                            and 0 <= old_f_idx < len(existing_emb_face)):
                        new_emb_face_list.append(existing_emb_face[old_f_idx])
                        nf["embedding_face_idx"] = face_idx_counter
                        face_idx_counter += 1
                    new_faces.append(nf)
                new_e["faces"] = new_faces
                new_entries.append(new_e)
                ok += 1
                continue

            if _status == "resume_revlm":
                # Riempie SOLO l'asse VLM+testo (fallito col server VLM giu')
                # riusando SigLIP+volti gia' calcolati e verificati completi.
                prev_e, vlm_out = _payload
                new_e = dict(prev_e)
                new_e["description"] = vlm_out.get("description", "")
                new_e["keywords"] = list(vlm_out.get("keywords", []))
                new_e["location_hint"] = vlm_out.get("location_hint", "")
                new_e["activity_hint"] = vlm_out.get("activity_hint", "")
                if "_vlm_error" in vlm_out:
                    new_e["_vlm_error"] = vlm_out["_vlm_error"]
                else:
                    new_e.pop("_vlm_error", None)
                # SigLIP image emb: riusa la riga esistente
                new_e.pop("embedding_image_idx", None)
                if (isinstance(prev_e.get("embedding_image_idx"), int)
                        and existing_emb_image is not None):
                    old_i_idx = prev_e["embedding_image_idx"]
                    if 0 <= old_i_idx < len(existing_emb_image):
                        new_emb_image_list.append(existing_emb_image[old_i_idx])
                        new_e["embedding_image_idx"] = image_idx_counter
                        image_idx_counter += 1
                # Volti: riusa le righe esistenti
                new_faces = []
                for face in prev_e.get("faces", []):
                    nf = dict(face)
                    old_f_idx = face.get("embedding_face_idx")
                    nf.pop("embedding_face_idx", None)
                    if (isinstance(old_f_idx, int) and existing_emb_face is not None
                            and 0 <= old_f_idx < len(existing_emb_face)):
                        new_emb_face_list.append(existing_emb_face[old_f_idx])
                        nf["embedding_face_idx"] = face_idx_counter
                        face_idx_counter += 1
                    new_faces.append(nf)
                new_e["faces"] = new_faces
                # Text emb fresco da path_context + description (ADR 0166).
                new_e.pop("embedding_text_idx", None)
                desc = new_e.get("description") or ""
                _ctx = folder_path_context(
                    Path(new_e.get("path", "")).parent.name, _lang)
                new_e["path_context"] = _ctx
                _emb_input = (_ctx + " " + desc).strip()
                if _emb_input and text_model_name != "none":
                    if text_engine is None:
                        try:
                            text_engine = get_embedder("text")
                        except FileNotFoundError as ex:
                            log.warning("BGE non disponibile: %r — embedding_text omesso", ex)
                            text_engine = False
                        except Exception as ex:
                            log.warning("BGE init fallito: %r", ex)
                            text_engine = False
                    if text_engine and text_engine is not False:
                        try:
                            vec = text_engine.embed_texts([_emb_input])
                            if vec.ndim == 2 and vec.shape[0] == 1:
                                new_emb_text_list.append(vec[0])
                                new_e["embedding_text_idx"] = text_idx_counter
                                text_idx_counter += 1
                        except Exception as ex:
                            log.debug("embed_texts fallito: %r", ex)
                new_entries.append(new_e)
                ok += 1
                _emit_prog()
                continue

            # status == "new"
            entry = _payload
            # §7.3: SigLIP image embedding (image-to-image visual similarity)
            if clip_engine_obj is not None:
                try:
                    img_emb = clip_engine_obj.embed_images([str(p)], batch_size=1)
                    if img_emb is not None and img_emb.ndim == 2 and img_emb.shape[0] == 1:
                        new_emb_image_list.append(img_emb[0])
                        entry["embedding_image_idx"] = image_idx_counter
                        image_idx_counter += 1
                except Exception as ex:
                    log.debug("clip image embed fallito per %s: %r", p, ex)
            for face in entry["faces"]:
                emb = face.pop("_embedding_face", None)
                if emb is not None:
                    new_emb_face_list.append(emb)
                    face["embedding_face_idx"] = face_idx_counter
                    face_idx_counter += 1
            desc = entry.get("description") or ""
            _ctx = folder_path_context(Path(entry.get("path", "")).parent.name, _lang)
            entry["path_context"] = _ctx
            _emb_input = (_ctx + " " + desc).strip()
            if _emb_input and text_model_name != "none":
                if text_engine is None:
                    try:
                        text_engine = get_embedder("text")
                    except FileNotFoundError as ex:
                        log.warning("BGE non disponibile: %r — embedding_text omesso", ex)
                        text_engine = False
                    except Exception as ex:
                        log.warning("BGE init fallito: %r", ex)
                        text_engine = False
                if text_engine and text_engine is not False:
                    try:
                        vec = text_engine.embed_texts([_emb_input])
                        if vec.ndim == 2 and vec.shape[0] == 1:
                            new_emb_text_list.append(vec[0])
                            entry["embedding_text_idx"] = text_idx_counter
                            text_idx_counter += 1
                    except Exception as ex:
                        log.debug("embed_texts fallito: %r", ex)
            new_entries.append(entry)
            ok += 1
            _emit_prog()

        _i += _CHUNK

    if _pool is not None:
        _pool.shutdown(wait=True)

    emb_text = (
        np.stack(new_emb_text_list, axis=0).astype("float32")
        if new_emb_text_list else None
    )
    emb_face = (
        np.stack(new_emb_face_list, axis=0).astype("float32")
        if new_emb_face_list else None
    )
    emb_image = (
        np.stack(new_emb_image_list, axis=0).astype("float32")
        if new_emb_image_list else None
    )

    return {
        "entries": new_entries,
        "emb_text": emb_text,
        "emb_face": emb_face,
        "emb_image": emb_image,
        "ok_count": ok,
        "fail_count": fail,
        "model_text": text_model_name,
        "dim_text": int(text_dim if emb_text is not None else 0),
        "model_vlm": _VLM_MODEL,
        "model_face": face_engine.name,
        "model_image": image_model_name,
        "dim_image": int(image_dim if emb_image is not None else 0),
    }


_AUTO_FILENAME_PATTERNS = (
    # DSC, DSC_, DSCN, DSCF + digits  → device default
    re.compile(r"^DSC[NF_-]?\d+$", re.I),
    # IMG, IMG_, IMG- + digits[_digits]  → smartphone/Android default
    re.compile(r"^IMG[_-]?\d+(?:[_-]?[A-Z]*\d*)*$", re.I),
    re.compile(r"^IMG-?\d{8}-?WA\d+$", re.I),  # WhatsApp pattern
    # PIC, P, PICT + digits → various devices
    re.compile(r"^PI?C[T_]?\d+$", re.I),
    re.compile(r"^P\d+$"),
    # CIMG / CAM / SDC default Casio/SanDisk
    re.compile(r"^(CIMG|CAM|SDC)\d+$", re.I),
    # YYYYMMDD-NNNN, YYYY-MM-DD-NNNN, YYYYMMDDHHMMSS
    re.compile(r"^\d{4}[-_]?\d{2}[-_]?\d{2}([-_]?\d+)*$"),
    re.compile(r"^\d{14}$"),
    # Solo cifre, GUID/hex
    re.compile(r"^\d+$"),
    re.compile(r"^[0-9a-fA-F]{16,}$"),
    # Thumbs.db, .DS_Store like (gia' filtrati altrove ma per sicurezza)
    re.compile(r"^Thumbs$", re.I),
)


def _meaningful_filename_tokens(stem: str) -> list[str]:
    """Token significativi dal filename SENZA estensione.

    Ritorna [] per pattern auto-generati dai device. Per filename
    user-named (es. 'fototessera carol', 'compleanno_bob',
    'manate doza') ritorna i token alfa di lunghezza >=2.
    """
    if not stem:
        return []
    if any(p.match(stem) for p in _AUTO_FILENAME_PATTERNS):
        return []
    raw = re.split(r"[\s_\-]+", stem.lower())
    out: list[str] = []
    for t in raw:
        if len(t) < 2 or t.isdigit():
            continue
        # filtra residui camera (es. 'wa' da WhatsApp se sopravvissuto)
        if t in ("wa", "vid", "img", "dsc", "pic", "p"):
            continue
        out.append(t)
    return out


def _meaningful_dir_tokens(name: str) -> list[str]:
    """Token significativi da una directory genitore.

    Filtra root del corpus ('Immagini', 'Photos', '.', '/'). Tokenizza
    su separatori (spazi, _, -). Tiene anche l'anno (utile per query
    'foto 2018 ...'); scarta solo cifre molto corte tipo '01' '02'."""
    if not name or name in (".", "/", "Immagini", "Photos", "Foto"):
        return []
    raw = re.split(r"[\s_\-]+", name.lower())
    out: list[str] = []
    for t in raw:
        if len(t) < 2:
            continue
        if t.isdigit() and len(t) < 4:
            # mese/giorno standalone non utili in BM25; tieni solo l'anno
            continue
        out.append(t)
    return out


def _build_entry(
    p: Path, mtime: float, size: int,
    *, face_engine,
) -> dict:
    """Costruisce una unified entry per la foto `p`. Ritorna dict.

    `faces[].`_embedding_face`` contiene il vettore np.ndarray (popped dal
    chiamante e pushato in embeddings_face.npy).
    """
    sp = str(p)
    name = p.name
    # cheap: dims + EXIF
    img, exif = _open_image_with_exif(p)
    image_w = image_h = 0
    if img is not None:
        try:
            image_w, image_h = int(img.size[0]), int(img.size[1])
        except Exception:
            image_w = image_h = 0
    taken_at = _exif_taken_at_iso(exif)
    gps = _exif_gps(exif)

    # sha256 (limit 64MB read)
    try:
        sha = _sha256_file(p)
    except OSError:
        sha = ""

    # ArcFace detect
    faces_out: list[dict] = []
    try:
        faces = face_engine.detect_faces(p)
    except Exception:
        faces = []
    for face in faces:
        bb = face.get("bbox")
        sc = face.get("score")
        emb = face.get("embedding")
        lms = face.get("landmarks")
        nf: dict = {
            "bbox": [int(bb[0]), int(bb[1]), int(bb[2]), int(bb[3])] if bb else [],
            "detect_score": float(sc) if sc is not None else 0.0,
        }
        if lms is not None:
            try:
                nf["landmarks"] = [[float(lm[0]), float(lm[1])] for lm in lms]
            except Exception:
                pass
        if emb is not None:
            nf["_embedding_face"] = emb
        faces_out.append(nf)

    # VLM (heavy)
    vlm = _call_vlm(p)

    # Cleanup
    if img is not None:
        try:
            img.close()
        except Exception:
            pass

    # Token significativi da filename + 2 cartelle genitori (entrano nel
    # BM25 a search-time tramite find_images_indices). Filtra device
    # auto-generated (DSC*, IMG_2024..., PIC0204, ecc).
    _stem = name.rsplit(".", 1)[0]
    _path_tokens: list[str] = []
    _path_tokens.extend(_meaningful_filename_tokens(_stem))
    _path_tokens.extend(_meaningful_dir_tokens(p.parent.name))
    try:
        _gp = p.parent.parent.name
        _path_tokens.extend(_meaningful_dir_tokens(_gp))
    except Exception:
        pass
    # Dedup preserve order
    _seen = set()
    _path_tokens_uniq: list[str] = []
    for _t in _path_tokens:
        if _t not in _seen:
            _seen.add(_t)
            _path_tokens_uniq.append(_t)

    entry: dict = {
        "path": sp,
        "sha256": sha,
        "name": name,
        "mtime": float(mtime),
        "size": int(size),
        "image_w": int(image_w),
        "image_h": int(image_h),
        "taken_at_iso": taken_at,
        "exif_gps": gps,
        "description": vlm.get("description", ""),
        "keywords": list(vlm.get("keywords", [])),
        "location_hint": vlm.get("location_hint", ""),
        "activity_hint": vlm.get("activity_hint", ""),
        "path_tokens": _path_tokens_uniq,
        "faces": faces_out,
    }
    if "_vlm_error" in vlm:
        entry["_vlm_error"] = vlm["_vlm_error"]
    return entry


# ── Undo (§2.3 module.reverse fallback) ─────────────────────────────────


def reverse(plan, results):
    """Annulla un create_images_indices RIMUOVENDO l'indice creato dal turno.

    Reversibile SOLO se il forward ha creato l'indice ex-novo
    (`results.index_created == True`); un update incrementale di un indice
    preesistente NON e' ribaltabile (servirebbe un blob dell'intero indice) →
    ritorna `ok_count=0` onesto (§2.8). Safety: rmtree solo dentro la index
    root di Metnos (mai path arbitrari).
    """
    import shutil
    res = results or {}
    if not res.get("index_created"):
        return {"ok": True, "ok_count": 0, "fail_count": 0, "results": [],
                "note": "indice pre-esistente aggiornato: non ribaltabile"}
    idx_path = res.get("index_path")
    if not idx_path:
        return {"ok": True, "ok_count": 0, "fail_count": 0, "results": []}
    p = Path(idx_path)
    try:
        p_res = p.resolve()
        root_res = _index_image_root().resolve()
    except OSError as e:
        return {"ok": False, "ok_count": 0, "fail_count": 1, "results": [],
                "error": str(e)}
    if root_res not in p_res.parents:
        return {"ok": False, "ok_count": 0, "fail_count": 1, "results": [],
                "error": "index_path fuori dalla index root: rifiuto rmtree"}
    if not p.exists():
        return {"ok": True, "ok_count": 0, "fail_count": 0, "results": []}
    try:
        shutil.rmtree(str(p))
        parent = p_res.parent  # <sha16>/ : rimuovi se ora vuoto
        try:
            if parent != root_res and not any(parent.iterdir()):
                parent.rmdir()
        except OSError:
            pass
        return {"ok": True, "ok_count": 1, "fail_count": 0,
                "results": [{"removed": str(p)}]}
    except OSError as e:
        return {"ok": False, "ok_count": 0, "fail_count": 1, "results": [],
                "error": str(e)}


# ── Entry point ─────────────────────────────────────────────────────────


def invoke(args):
    base_path_arg = args.get("base_path")
    if not base_path_arg:
        return {"ok": False, "error": _msg("ERR_ARG_MISSING", arg="base_path")}

    # Backward compat: idx ignorato post-ADR0117
    if args.get("idx") not in (None, "", "all"):
        log.warning(
            "create_images_indices: arg `idx=%r` ignorato post-ADR0117 (unified)",
            args.get("idx"),
        )

    recursive = bool(args.get("recursive", True))
    force = bool(args.get("force", False))
    max_files = args.get("max_files", 50000)
    if not isinstance(max_files, int) or max_files < 1:
        return {"ok": False, "error": _msg("ERR_ARG_NOT_POSITIVE_INT", arg="max_files")}

    base = Path(os.path.expanduser(base_path_arg)).resolve()
    if not base.exists():
        return {"ok": False, "error": _msg("ERR_PATH_NOT_FOUND", path=base)}
    if not base.is_dir():
        return {"ok": False, "error": _msg("ERR_PATH_WRONG_TYPE", expected="dir", actual="file", path=base)}

    # Dry run: enumera senza side-effect
    if bool(args.get("dry_run")) or _is_dry_run():
        n_total, n_image, n_other, other_exts = _scan_dir_extensions(base, recursive)
        # Stima: VLM ~3s, ArcFace ~0.2s, text ~0.05s, EXIF/IO trascurabile
        est_per_file = 3.3
        bytes_per_entry = 4_000  # JSON entry + embedding refs
        return {
            "ok": True,
            "dry_run": True,
            "schema_version": _INDEX_VERSION,
            "base_path": str(base),
            "would_index_count": int(n_image),
            "n_other_files": int(n_other),
            "est_time_s": round(n_image * est_per_file, 1),
            "est_size_mb": round(n_image * bytes_per_entry / (1024 * 1024), 2),
        }

    # Pre-walk diagnostico
    n_total, n_image_files, n_other_files, other_exts = _scan_dir_extensions(
        base, recursive,
    )
    if n_image_files == 0:
        seen = ", ".join(sorted(other_exts)) if other_exts else "(nessuna)"
        supported = "jpg/jpeg/png/heic/webp/tiff/bmp"
        return {
            "ok": False,
            "error": (
                f"directory non contiene immagini supportate. "
                f"Estensioni viste: {{{seen}}}. "
                f"Estensioni supportate: {supported}."
            ),
            "n_total_files": int(n_total),
            "n_image_files": 0,
            "n_other_files": int(n_other_files),
            "extensions_seen": sorted(other_exts),
            "base_path": str(base),
        }

    paths, truncated = _walk_images(base, recursive, max_files)
    idx_dir = _index_dir(base)
    # Undo §2.3 (module.reverse): l'indice e' ribaltabile SOLO se questo turno
    # lo CREA ex-novo (idx_dir non esisteva). Un update incrementale di un
    # indice preesistente non e' ribaltabile senza blob backup dell'intero
    # indice (embeddings pesanti) → onesti, ok_count=0 nell'undo.
    idx_existed_before = idx_dir.exists()
    existing_entries, existing_emb_text, existing_emb_face = (
        ([], None, None) if force else _load_existing(idx_dir)
    )

    # Load existing image embeddings se presenti (resume incrementale)
    existing_emb_image = None
    try:
        import numpy as _np
        _eip = idx_dir / "embeddings_image.npy"
        if not force and _eip.exists():
            existing_emb_image = _np.load(str(_eip))
    except Exception:
        existing_emb_image = None

    try:
        result = _build_unified(
            paths, existing_entries, existing_emb_text, existing_emb_face,
            existing_emb_image=existing_emb_image, force=force,
        )
    except FileNotFoundError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        return {"ok": False, "error": f"build failed: {e!r}"}

    refreshed_count = max(
        0, len(result["entries"]) - len(existing_entries),
    )

    if paths and result["ok_count"] == 0 and result["fail_count"] > 0:
        return {
            "ok": False,
            "error": (
                f"{result['fail_count']} file con estensione supportata "
                f"MA tutti falliti l'encoding (modello ArcFace/VLM non "
                f"disponibile o file corrotti)."
            ),
            "fail_count": int(result["fail_count"]),
            "n_paths": len(paths),
        }

    try:
        _atomic_write_index(
            idx_dir,
            result["entries"],
            result["emb_text"],
            result["emb_face"],
            result.get("emb_image"),
            base_path=base,
            model_text=result["model_text"],
            dim_text=result["dim_text"],
            model_vlm=result["model_vlm"],
            model_face=result["model_face"],
            model_image=result.get("model_image", "none"),
            dim_image=result.get("dim_image", 0),
        )
    except OSError as e:
        return {"ok": False, "error": f"index write failed: {e}"}

    out = {
        "ok": True,
        "schema_version": _INDEX_VERSION,
        "base_path": str(base),
        "ok_count": int(result["ok_count"]),
        "fail_count": int(result["fail_count"]),
        "n_entries_total": len(result["entries"]),
        "refreshed_count": int(refreshed_count),
        "last_refresh_at": time.time(),
        "index_path": str(idx_dir),
        "index_created": (not idx_existed_before),
        "model_text": result["model_text"],
        "model_vlm": result["model_vlm"],
        "model_face": result["model_face"],
        "model_image": result.get("model_image", "none"),
        "dim_text": int(result["dim_text"]),
        "dim_image": int(result.get("dim_image", 0)),
    }

    # §7.3 Completion marker per `notification_dispatcher_task`
    # (runtime/http_async_tasks.py polla `/tmp/metnos_build_complete/*.json`).
    # Env var settata da _spawn_index_build in find_images_indices.
    _notify_marker = os.environ.get("METNOS_BUILD_NOTIFY_MARKER")
    if _notify_marker:
        try:
            from pathlib import Path as _P
            pending_p = _P(_notify_marker)
            pending_data = {}
            if pending_p.exists():
                try:
                    pending_data = json.loads(pending_p.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    pending_data = {}
            ts_start = float(pending_data.get("ts_started", time.time()))
            done_data = {
                "actor": pending_data.get("actor", "host"),
                "channel": pending_data.get("channel", "http"),
                "base_path": pending_data.get("base_path", str(base)),
                "idx": pending_data.get("idx", "unified"),
                "n_entries": int(out["n_entries_total"]),
                "duration_s": float(time.time() - ts_start),
                "errors_count": int(out["fail_count"]),
                "ok": True,
                "ts_done": time.time(),
            }
            complete_dir = _P("/tmp/metnos_build_complete")
            complete_dir.mkdir(parents=True, exist_ok=True)
            done_p = complete_dir / pending_p.name
            done_p.write_text(json.dumps(done_data, ensure_ascii=False))
            if pending_p.exists():
                try:
                    pending_p.unlink()
                except OSError:
                    pass
        except Exception as _ex:
            log.warning("completion marker write failed: %r", _ex)
    if result["fail_count"] > 0 and result["ok_count"] > 0:
        out["warning"] = (
            f"{result['fail_count']}/{len(paths)} file falliti l'encoding "
            f"(probabili file corrotti, VLM down, o formati non standard)."
        )
    if truncated:
        out["truncated"] = True
        out["truncated_what"] = "image-file"
        out["used"] = len(paths)
        out["cap_field"] = "max_files"
        out["cap_value"] = int(max_files)

    # End-of-task marker (opt-in via env var)
    _progress_path = os.environ.get("METNOS_PROGRESS_FILE")
    if _progress_path:
        try:
            _done_payload = {
                "ts": time.time(),
                "phase": "done",
                "ok": True,
                "n_total": len(paths),
                "n_processed": int(result["ok_count"]) + int(result["fail_count"]),
                "ok_count": int(result["ok_count"]),
                "fail_count": int(result["fail_count"]),
                "n_entries_total": len(result["entries"]),
                "index_path": str(idx_dir),
                "model_text": result["model_text"],
                "model_vlm": result["model_vlm"],
                "model_face": result["model_face"],
            }
            _tmp = _progress_path + ".tmp"
            with open(_tmp, "w") as _fh:
                json.dump(_done_payload, _fh)
            os.replace(_tmp, _progress_path)
        except Exception:
            pass
    return out


def main():
    run_stdio(invoke)


if __name__ == "__main__":
    main()
