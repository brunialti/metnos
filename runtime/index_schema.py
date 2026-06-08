"""index_schema — registry centralizzato di IDX_TYPES + ENRICHMENTS.

Schema corrente: v4 (unified, ADR 0117). Prima v3 era allineato all'aggiunta
di field ENRICHMENTS PR4; ora UN solo indice composito per corpus rimpiazza
i 3 separati scene/persons/gps. Vedi `UNIFIED_FIELDS`.

Le costanti V3 (`INDEX_SCHEMA_VERSION_V3`, `IDX_TYPES`,
`LEGACY_ENRICHMENTS_V3`) restano definite per migration v3→v4; non sono
piu' la sorgente di costruzione dei NUOVI indici.

Determinismo §7.9: tutti i compute restano pure helpers Python/numpy/PIL.
Le call VLM sono incapsulate nel builder (non qui) — questo modulo descrive
SOLO la forma dei dati.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal, Optional

# v4 — unified index (ADR 0117). Sostituisce v2/v3.
INDEX_SCHEMA_VERSION = 4
INDEX_SCHEMA_VERSION_V3 = 2  # alias storico per migration

# Legacy: 3 indici disgiunti. Riferito solo dalla migration v3→v4 e da
# index_schema_upgrade.py (modulo legacy). NON usare per build nuovi.
IDX_TYPES = ["scene", "persons", "gps"]


Domain = Literal["scene", "persons", "gps", "all"]
CostClass = Literal["cheap", "medium", "heavy"]
ComputeClass = Literal["exif", "vlm", "arcface", "derive"]


@dataclass(frozen=True)
class EnrichmentField:
    """Campo di arricchimento calcolato per ogni entry indice.

    `compute_fn` riceve kwargs nominati: `img` (PIL.Image), `face` (dict
    con bbox/landmarks/embedding o None se non-persons), `faces` (list
    di tutti i volti della stessa foto, denormalizzazione), `exif` (dict
    raw da img._getexif() o {}). Ritorna scalare JSON-serializable o None.
    """

    name: str
    compute_fn: Callable[..., Any]
    domain: Domain
    cost_class: CostClass
    schema_min_version: int
    description: str


# ── Compute helpers ────────────────────────────────────────────────────


def _img_w(*, img, **_kw) -> int:
    return int(img.size[0])


def _img_h(*, img, **_kw) -> int:
    return int(img.size[1])


def _exif_taken_at(*, exif, **_kw) -> Optional[str]:
    """EXIF DateTimeOriginal -> ISO 8601 (no TZ). Fallback DateTime."""
    if not exif:
        return None
    # Risolve nomi tag senza dipendere da PIL.ExifTags (chiavi numeriche).
    # Fast path: se exif e' gia' nominato (chiavi str), accesso diretto.
    raw = None
    for key in ("DateTimeOriginal", "DateTime"):
        if key in exif and exif[key]:
            raw = str(exif[key])
            break
    if raw is None:
        # tenta lookup numerico via TAGS
        try:
            from PIL.ExifTags import TAGS as _TAGS
        except Exception:
            return None
        named: dict = {}
        for tag_id, value in exif.items():
            tname = _TAGS.get(tag_id)
            if tname:
                named[tname] = value
        for key in ("DateTimeOriginal", "DateTime"):
            if key in named and named[key]:
                raw = str(named[key])
                break
    if not raw:
        return None
    # Format EXIF tipico: "YYYY:MM:DD HH:MM:SS"
    try:
        date_part, time_part = raw.split(" ", 1)
        date_iso = date_part.replace(":", "-")
        return f"{date_iso}T{time_part}"
    except (ValueError, AttributeError):
        return None


def _bbox_area_fraction(*, face, img, **_kw) -> Optional[float]:
    if face is None:
        return None
    try:
        w, h = img.size
    except Exception:
        return None
    if w <= 0 or h <= 0:
        return None
    bb = face.get("bbox") if isinstance(face, dict) else None
    if not bb or len(bb) < 4:
        return None
    try:
        return float(bb[2] * bb[3]) / float(w * h)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _face_count_in_photo(*, faces, **_kw) -> int:
    if not faces:
        return 0
    return int(len(faces))


def _is_grayscale(*, img, **_kw) -> bool:
    """Mean saturation (HSV) < 0.05 -> grayscale."""
    try:
        import numpy as np
        rgb = img.convert("RGB")
        arr = np.asarray(rgb, dtype="float32") / 255.0
        # Mean across pixels of (max-min) per pixel is a proxy of saturation.
        cmax = arr.max(axis=2)
        cmin = arr.min(axis=2)
        sat_proxy = (cmax - cmin).mean()
        return bool(sat_proxy < 0.05)
    except Exception:
        return False


def _brightness_mean(*, img, **_kw) -> Optional[float]:
    """Luminance Rec.709 normalizzata 0..1."""
    try:
        import numpy as np
        rgb = img.convert("RGB")
        arr = np.asarray(rgb, dtype="float32") / 255.0
        lum = 0.2126 * arr[..., 0] + 0.7152 * arr[..., 1] + 0.0722 * arr[..., 2]
        return float(lum.mean())
    except Exception:
        return None


def _is_blurry(*, img, threshold: float = 100.0, **_kw) -> Optional[bool]:
    """Variance of Laplacian < threshold -> blurry. None se compute fallisce."""
    try:
        import numpy as np
        gray = img.convert("L")
        arr = np.asarray(gray, dtype="float32")
        # Kernel Laplacian 3x3
        kernel = np.array([[0, 1, 0], [1, -4, 1], [0, 1, 0]], dtype="float32")
        # Convoluzione manuale via sliding view (no scipy dep)
        h, w = arr.shape
        if h < 3 or w < 3:
            return None
        # Padding zero
        pad = np.zeros((h + 2, w + 2), dtype="float32")
        pad[1:-1, 1:-1] = arr
        out = (
            kernel[0, 1] * pad[:-2, 1:-1]
            + kernel[1, 0] * pad[1:-1, :-2]
            + kernel[1, 1] * pad[1:-1, 1:-1]
            + kernel[1, 2] * pad[1:-1, 2:]
            + kernel[2, 1] * pad[2:, 1:-1]
        )
        var = float(out.var())
        return bool(var < float(threshold))
    except Exception:
        return None


def _frontal_score(*, face, **_kw) -> Optional[float]:
    """0..1 da simmetria dei 5 landmark RetinaFace.

    Layout `landmarks`: [eye_l, eye_r, nose, mouth_l, mouth_r] coordinate
    (x, y) image-space. Score = 1 - |asymmetry|, dove asymmetry e' la
    distanza orizzontale del naso dal punto medio occhi-bocca normalizzata
    sulla larghezza occhio-occhio. None se landmarks mancanti.
    """
    if face is None:
        return None
    lms = face.get("landmarks") if isinstance(face, dict) else None
    if not lms or len(lms) < 5:
        return None
    try:
        eye_l = lms[0]
        eye_r = lms[1]
        nose = lms[2]
        mouth_l = lms[3]
        mouth_r = lms[4]
        eye_mid_x = (float(eye_l[0]) + float(eye_r[0])) / 2.0
        mouth_mid_x = (float(mouth_l[0]) + float(mouth_r[0])) / 2.0
        face_axis_x = (eye_mid_x + mouth_mid_x) / 2.0
        eye_dist = abs(float(eye_r[0]) - float(eye_l[0]))
        if eye_dist <= 1e-6:
            return None
        nose_offset = abs(float(nose[0]) - face_axis_x)
        asymmetry = nose_offset / eye_dist
        # asymmetry tipica frontale ~0.0-0.1, profilo ~0.4+
        score = max(0.0, 1.0 - 2.0 * asymmetry)
        return float(min(1.0, score))
    except (TypeError, ValueError, IndexError):
        return None


# ── Registry ────────────────────────────────────────────────────────────


# Alias migration: gli ENRICHMENTS PR4 erano l'unica fonte v3. Post-v4 si
# usano solo i campi di `UNIFIED_FIELDS` per i NUOVI indici, ma la migration
# v3→v4 legge questi field per non perdere informazioni gia' calcolate.
LEGACY_ENRICHMENTS_V3 = "see ENRICHMENTS below — kept identical for migration"


ENRICHMENTS: list[EnrichmentField] = [
    EnrichmentField(
        "image_w", _img_w, "all", "cheap", 2,
        "image width in pixels",
    ),
    EnrichmentField(
        "image_h", _img_h, "all", "cheap", 2,
        "image height in pixels",
    ),
    EnrichmentField(
        "taken_at_iso", _exif_taken_at, "all", "cheap", 2,
        "EXIF DateTimeOriginal as ISO 8601 (no TZ)",
    ),
    EnrichmentField(
        "bbox_area_fraction", _bbox_area_fraction, "persons", "cheap", 2,
        "face bbox area / image area (0..1)",
    ),
    EnrichmentField(
        "face_count_in_photo", _face_count_in_photo, "persons", "cheap", 2,
        "number of faces detected in the same photo (denormalized)",
    ),
    EnrichmentField(
        "is_grayscale", _is_grayscale, "all", "cheap", 2,
        "true if photo is grayscale (mean saturation < 0.05)",
    ),
    EnrichmentField(
        "brightness_mean", _brightness_mean, "all", "cheap", 2,
        "mean luminance Rec.709 normalized 0..1",
    ),
    EnrichmentField(
        "is_blurry", _is_blurry, "all", "medium", 2,
        "Laplacian variance < 100 -> blurry",
    ),
    EnrichmentField(
        "frontal_score", _frontal_score, "persons", "medium", 2,
        "0..1 frontal symmetry from RetinaFace landmarks; None if absent",
    ),
]


def fields_for_domain(domain: str) -> list[EnrichmentField]:
    """Subset di ENRICHMENTS pertinenti al dominio. `all` aggrega base-fields.

    Legacy v3: usato dalla migration; per v4 vedi `UNIFIED_FIELDS`.
    """
    return [f for f in ENRICHMENTS if f.domain == domain or f.domain == "all"]


def needs_upgrade(meta: dict) -> bool:
    """True se l'indice e' precedente alla schema corrente.

    True se schema_version<2 (v1 raw) o se schema_version<4 e l'indice e' a
    storage v3 (cartelle scene/persons/gps separate). In v4 l'indice ha
    storage `unified/` e questa funzione ritorna False (la migration e'
    completa).
    """
    if not isinstance(meta, dict):
        return True
    sv = int(meta.get("schema_version", meta.get("version", 1)) or 1)
    return sv < INDEX_SCHEMA_VERSION


def is_unified_schema(meta: dict) -> bool:
    """True se l'indice e' a schema v4 (unified)."""
    if not isinstance(meta, dict):
        return False
    return int(meta.get("schema_version", 1) or 1) >= INDEX_SCHEMA_VERSION


def field_names_for_domain(domain: str) -> list[str]:
    return [f.name for f in fields_for_domain(domain)]


# ── Unified schema v4 (ADR 0117) ────────────────────────────────────────


@dataclass(frozen=True)
class UnifiedEntryField:
    """Descrittore di campo per l'entry unificata v4.

    Caratterizza ogni colonna logica dell'entry foto con:
    - `compute_class`: chi/come la calcola (exif | vlm | arcface | derive).
    - `cost_class`: relativa al build (cheap | medium | heavy).

    Non contiene compute_fn: il builder unified incapsula sia compute
    deterministici (PIL/EXIF) sia call VLM esterne. La fn map vive in
    `executors/create_images_indices/create_images_indices.py`.
    """

    name: str
    compute_class: ComputeClass
    cost_class: CostClass


# Closed registry. Ordine = ordine logico di build per-foto (cheap→heavy).
UNIFIED_FIELDS: list[UnifiedEntryField] = [
    # Filesystem + EXIF (cheap, sempre presenti)
    UnifiedEntryField("path", "exif", "cheap"),
    UnifiedEntryField("sha256", "exif", "cheap"),
    UnifiedEntryField("name", "exif", "cheap"),
    UnifiedEntryField("mtime", "exif", "cheap"),
    UnifiedEntryField("size", "exif", "cheap"),
    UnifiedEntryField("image_w", "exif", "cheap"),
    UnifiedEntryField("image_h", "exif", "cheap"),
    UnifiedEntryField("taken_at_iso", "exif", "cheap"),
    UnifiedEntryField("exif_gps", "exif", "cheap"),
    # VLM (heavy — Qwen2-VL-7B, una call/foto)
    UnifiedEntryField("description", "vlm", "heavy"),
    UnifiedEntryField("keywords", "vlm", "heavy"),
    UnifiedEntryField("location_hint", "vlm", "heavy"),
    UnifiedEntryField("activity_hint", "vlm", "heavy"),
    # Derive (medium — MiniLM/BGE su description)
    UnifiedEntryField("embedding_text_idx", "derive", "medium"),
    # ArcFace (medium — RetinaFace + ArcFace)
    UnifiedEntryField("faces", "arcface", "medium"),
]


def unified_field_names() -> list[str]:
    return [f.name for f in UNIFIED_FIELDS]


def unified_fields_by_compute(compute_class: str) -> list[UnifiedEntryField]:
    return [f for f in UNIFIED_FIELDS if f.compute_class == compute_class]


__all__ = [
    "INDEX_SCHEMA_VERSION",
    "INDEX_SCHEMA_VERSION_V3",
    "IDX_TYPES",
    "ENRICHMENTS",
    "EnrichmentField",
    "UnifiedEntryField",
    "UNIFIED_FIELDS",
    "fields_for_domain",
    "field_names_for_domain",
    "needs_upgrade",
    "is_unified_schema",
    "unified_field_names",
    "unified_fields_by_compute",
]
