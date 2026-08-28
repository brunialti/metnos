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
import time
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
        # Testa (fino a OUT:) entro HEAD_MAX=240 (§2.5): a 306 il render del
        # pool la TRONCAVA e il boundary verso describe_entries spariva
        # (warning live [manifest] 10/7 → rischio misroute).
        "description": (
            "SCOPO: descrive il contenuto di immagini col VLM "
            "(soggetti, scena, testo). "
            "PATTERN: describe_images(reference_images=[\"/a.jpg\"]) o "
            "from_step=1. NON: cercare foto simili -> find_images_indices; "
            "liste di record -> describe_entries. "
            "OUT: entries=[{path,description,keywords}] "
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

_DEFAULT_MAX_IMAGES = 8
_DEFAULT_REQUEST_BUDGET_S = 45.0
_MAX_IMAGES_SAFETY_CEILING = 32
_MAX_REQUEST_BUDGET_SAFETY_CEILING = 90.0


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


def _bounded_policy(value, *, default: float, minimum: float,
                    maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    if parsed != parsed or parsed in {float("inf"), float("-inf")}:
        return default
    return max(minimum, min(maximum, parsed))


def _work_policy() -> tuple[int, float]:
    """Resolve the synchronous VLM policy from the canonical Virt spec."""

    try:
        from virt import get_vlm
        spec = get_vlm()
    except Exception:
        spec = {}
    max_images = _bounded_policy(
        os.environ.get("METNOS_VLM_MAX_IMAGES_PER_REQUEST")
        or spec.get("max_images_per_request"),
        default=_DEFAULT_MAX_IMAGES, minimum=1,
        maximum=_MAX_IMAGES_SAFETY_CEILING,
    )
    budget_s = _bounded_policy(
        os.environ.get("METNOS_VLM_REQUEST_BUDGET_S")
        or spec.get("request_budget_s"),
        default=_DEFAULT_REQUEST_BUDGET_S, minimum=1,
        maximum=_MAX_REQUEST_BUDGET_SAFETY_CEILING,
    )
    return int(max_images), float(budget_s)


def handle_describe_images(args, *, verbose: bool = False) -> dict:
    """Builtin in-process: descrive le immagini col VLM. §2.8 mai solleva."""
    import vlm_client
    payload = args if isinstance(args, dict) else {}
    paths = _collect_paths(payload)
    if not paths:
        return {"ok": False,
                "error": _msg("ERR_ARG_MISSING", arg="reference_images"),
                "error_class": "invalid_args", "entries": [], "query_text": ""}
    max_images, budget_s = _work_policy()
    selected_paths = paths[:max_images]
    deadline_at = time.monotonic() + budget_s
    entries: list[dict] = []
    descriptions: list[str] = []
    budget_exhausted = False
    for p in selected_paths:
        if time.monotonic() >= deadline_at:
            budget_exhausted = True
            break
        d = vlm_client.describe_image(
            p, lang=payload.get("_lang"), deadline_at=deadline_at)
        desc = d.get("description", "")
        entry = {"path": p, "description": desc,
                 "keywords": d.get("keywords", [])}
        if d.get("_vlm_error"):
            entry["_vlm_error"] = d["_vlm_error"]
        entries.append(entry)
        if desc:
            descriptions.append(desc)
        if time.monotonic() >= deadline_at:
            budget_exhausted = True
            break
    query_text = " ".join(descriptions).strip()
    attempted = len(entries)
    available_total = len(paths)
    truncated = attempted < available_total
    fail_count = attempted - len(descriptions)
    out = {
        "ok": bool(descriptions),
        "entries": entries,
        "query_text": query_text,
        "ok_count": len(descriptions),
        "fail_count": fail_count,
        "partial": bool(descriptions) and (
            truncated or fail_count > 0),
        "used": attempted,
        "available_total": available_total,
    }
    if truncated:
        deadline_limited = budget_exhausted or attempted < len(selected_paths)
        out.update({
            "truncated": True,
            "truncated_what": _msg("MSG_OBJECT_IMAGE_FILES"),
            "cap_field": (
                "request_budget_s" if deadline_limited
                else "max_images_per_request"),
            "cap_value": (
                int(round(budget_s)) if deadline_limited else max_images),
            # This is an execution-policy ceiling, not a missing user count.
            # The generic cap-expansion UI must not turn it into an argument
            # that can bypass the synchronous safety budget.
            "cap_expandable": False,
            "budget_exhausted": deadline_limited,
        })
    return out


BUILTIN_INPROC_SPECS = [
    {"name": "describe_images", "tool_spec": DESCRIBE_IMAGES_TOOL,
     "affinity": ["descrivi foto", "cosa c'e' nella foto", "descrivi immagine",
                  "contenuto foto", "che foto e'", "descrivi questa immagine",
                  "describe photo", "describe image", "what's in the photo",
                  "image content", "caption"]},
]
