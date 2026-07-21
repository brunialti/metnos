#!/usr/bin/env python3
"""find_persons_indices — thin alias post-ADR0117 unified.

Da modulo standalone (v3, ricerca per name/reference su indice persons
disgiunto) a thin alias che inietta `name=`/`reference_images=` e invoca
direttamente `find_images_indices` (executor unificato).

Tre modalita' di chiamata (riusiamo l'engine unificato):
  (a) `name="bob"` → `find_images_indices(name=...)`.
  (b) `reference_images=[...]` → `find_images_indices(reference_images=...)`.

Backward compat per il PLANNER: questo executor mantiene il nome canonico
gia' affermato. La logica di matching, l'index storage e la pipeline di
filtri vivono in find_images_indices unified.

Determinismo §7.9: zero LLM, glue layer.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.environ.get("METNOS_RUNTIME") or next(
    str(p / "runtime") for p in Path(__file__).resolve().parents
    if (p / "runtime" / "config.py").is_file()))
from messages import get as _msg  # noqa: E402
from executor_helpers import run_stdio  # noqa: E402


def invoke(args):
    if not isinstance(args, dict):
        return {
            "ok": False,
            "entries": [],
            "error": _msg("ERR_ARGS_NOT_OBJECT"),
            "error_class": "invalid_input",
            "error_code": "args_not_object",
        }

    # Validate before importing the unified engine or touching local models.
    if not args.get("name") and not args.get("reference_images"):
        return {
            "ok": False,
            "entries": [],
            "error": _msg(
                "ERR_ARG_MISSING_ONE_OF", options="name, reference_images",
            ),
            "error_class": "invalid_input",
            "error_code": "search_criterion_missing",
        }

    # Glue: forwarda name/reference_images all'executor unificato.
    sib = Path(__file__).resolve().parent.parent / "find_images_indices"
    sys.path.insert(0, str(sib))
    import find_images_indices as fii

    forwarded = dict(args)
    # Drop `idx` se presente (deprecato, non instrada via unified).
    forwarded.pop("idx", None)

    return fii.invoke(forwarded)


def main():
    run_stdio(invoke)


if __name__ == "__main__":
    main()
