# SPDX-License-Identifier: AGPL-3.0-only
"""runtime.describe_images — builtin universale: IMMAGINE → descrizione VLM.

Sibling-immagine di `describe_entries` (che riassume i CAMPI di una lista):
qui il VLM legge il CONTENUTO di una o piu' foto e ritorna per ognuna una
descrizione RICCA + keywords. La descrizione e' pensata anche come QUERY di
RICERCA del contenuto (campo top-level `query_text` = descrizioni unite), cosi'
il piano upload-default puo' incatenarla a `find_images_indices(query_text=...)`
per la ricerca per-scena (foto caricata senza volto, ADR 0177 M1).

In-process (no subprocess): registrato in `agent_runtime._BUILTIN_TOOL_HANDLERS`
+ `_BUILTIN_TOOL_SPECS`; iniettato nel catalog engine v2 via
`_engine_v2_catalog_with_builtins`. §2.6: ritorna `entries` (arricchite).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_RUNTIME = os.environ.get("METNOS_RUNTIME") or next(
    (str(p / "runtime") for p in Path(__file__).resolve().parents
     if (p / "runtime" / "config.py").is_file()), str(Path(__file__).resolve().parent))
if _RUNTIME not in sys.path:
    sys.path.insert(0, _RUNTIME)
from messages import get as _msg  # noqa: E402


DESCRIBE_IMAGES_TOOL = {
    "type": "function",
    "function": {
        "name": "describe_images",
        "description": (
            "SCOPO: descrive il CONTENUTO di una o piu' immagini col VLM "
            "(soggetti, scena, oggetti, testo visibile, tipo). "
            "PATTERN: describe_images(reference_images=[\"/a.jpg\"]) oppure "
            "describe_images(from_step=1). NON: cerca foto simili "
            "(-> find_images_indices); NON descrive una lista di record "
            "(-> describe_entries). OUT: entries=[{path,description,keywords}] "
            "+ query_text (descrizioni unite, usabile come ricerca contenuto)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "reference_images": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Path locali delle immagini da descrivere.",
                },
                "paths": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Alias di reference_images.",
                },
                "entries": {
                    "type": "array", "items": {"type": "object"},
                    "description": ("Entries con campo path/reference_image "
                                    "(da from_step). Alternativa a reference_images."),
                },
            },
        },
    },
}

# Entries-consumer: il seed-wiring (@uploaded) inietta le entries qui via
# from_step=1; il piano upload-default lo usa come primo step.
IS_ENTRIES_CONSUMER = True


def _collect_paths(args: dict) -> list[str]:
    out: list[str] = []
    for e in (args.get("entries") or []):
        if isinstance(e, dict):
            p = e.get("reference_image") or e.get("path")
            if isinstance(p, str) and p:
                out.append(p)
    for key in ("reference_images", "paths"):
        for p in (args.get(key) or []):
            if isinstance(p, str) and p:
                out.append(p)
    # dedup preservando l'ordine
    seen: set = set()
    return [p for p in out if not (p in seen or seen.add(p))]


def handle_describe_images(args, *, verbose: bool = False) -> dict:
    """Builtin in-process: descrive le immagini col VLM. §2.8 mai solleva."""
    import vlm_client
    paths = _collect_paths(args if isinstance(args, dict) else {})
    if not paths:
        return {"ok": False,
                "error": _msg("ERR_ARG_MISSING", arg="reference_images"),
                "error_class": "invalid_args", "entries": [], "query_text": ""}
    entries: list[dict] = []
    descriptions: list[str] = []
    for p in paths:
        d = vlm_client.describe_image(p)
        desc = d.get("description", "")
        entry = {"path": p, "description": desc,
                 "keywords": d.get("keywords", [])}
        if d.get("_vlm_error"):
            entry["_vlm_error"] = d["_vlm_error"]
        entries.append(entry)
        if desc:
            descriptions.append(desc)
    query_text = " ".join(descriptions).strip()
    return {
        "ok": bool(descriptions),
        "entries": entries,
        "query_text": query_text,
        "ok_count": len(descriptions),
        "fail_count": len(paths) - len(descriptions),
    }


BUILTIN_INPROC_SPECS = [
    {"name": "describe_images", "tool_spec": DESCRIBE_IMAGES_TOOL,
     "affinity": ["descrivi foto", "cosa c'e' nella foto", "descrivi immagine",
                  "contenuto foto", "che foto e'", "descrivi questa immagine",
                  "describe photo", "describe image", "what's in the photo",
                  "image content", "caption"]},
]
