#!/usr/bin/env python3
"""smoke_imports.py — BATTERY runtime per executor importati da SKILL.md.

Separato da `smoke.py::BATTERY` canonica (curata a mano da Roberto).
L'importer skill (`runtime/cli/skills_cli.py::cmd_import`) APPENDE qui
le routing assertion per ogni nuovo executor accepted. Lo smoke runner
combina BATTERY + BATTERY_IMPORTS.

Schema entry identico a `smoke.py::BATTERY`:
    {
      "q": <str>,                    # query naturale
      "tool_re": <regex>,            # regex su chosen_tool che deve match
      "kind": "answer",
      "expected_first_tool": <str>,  # NAME canonico del tool atteso primo
      "expected_arg_keys": set([...]),
      "min_pass_rate": 1.0,
      "imported_from": <str>,        # provenance per debug
    }

L'importer deduce 1-3 case per executor partendo dal manifest description
+ affinity (es. read_events affinity=["appuntamenti","agenda"] -> case
"lista miei appuntamenti domani" -> read_events).

Determinismo §7.9: nessun LLM in lettura/scrittura. Append idempotente
(skip se esiste case identico per stesso executor name).

Created: 10/5/2026 (gap 6 importer skill).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# BATTERY_IMPORTS: lista mutevole, popolata dall'importer.
# ---------------------------------------------------------------------------


BATTERY_IMPORTS: list = []


# Storage persistente JSON: l'importer scrive qui, smoke.py legge al boot.
# Niente persistenza in moduli .py per evitare race con concurrent import.
import config as _C  # §7.11
_STORE_PATH = Path(
    os.environ.get(
        "METNOS_SMOKE_IMPORTS_PATH",
        str(_C.PATH_USER_DATA / "smoke_imports.json"),
    )
)


# ---------------------------------------------------------------------------
# Persistence (JSON store)
# ---------------------------------------------------------------------------


def _load_store() -> list:
    """Carica BATTERY_IMPORTS da JSON. Ritorna [] se assente o corrotto."""
    if not _STORE_PATH.is_file():
        return []
    try:
        with _STORE_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            return []
        # Re-hydrate set() per expected_arg_keys (JSON non supporta set).
        for case in data:
            if isinstance(case.get("expected_arg_keys"), list):
                case["expected_arg_keys"] = set(case["expected_arg_keys"])
        return data
    except (json.JSONDecodeError, OSError):
        return []


def _save_store(cases: list) -> None:
    """Salva BATTERY_IMPORTS su JSON. Atomic write tmp+rename."""
    _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    serializable = []
    for case in cases:
        c = dict(case)
        if isinstance(c.get("expected_arg_keys"), set):
            c["expected_arg_keys"] = sorted(c["expected_arg_keys"])
        serializable.append(c)
    tmp = _STORE_PATH.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(serializable, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.rename(_STORE_PATH)


# Auto-load al primo import del modulo.
BATTERY_IMPORTS.extend(_load_store())


# ---------------------------------------------------------------------------
# API: append + read
# ---------------------------------------------------------------------------


def add_case(*, query: str, expected_first_tool: str,
             expected_arg_keys: Optional[set] = None,
             imported_from: str = "",
             min_pass_rate: float = 1.0) -> bool:
    """Aggiunge un case alla BATTERY_IMPORTS. Idempotente: skip se gia' presente
    (stesso `expected_first_tool` + stesso normalized `q`).

    Ritorna True se aggiunto, False se duplicato.

    DEVI: passare query naturale + expected_first_tool canonico.
    NON DEVI: chiamare per duplicati (la funzione li ignora; logga 0
    silente per audit).
    OK: add_case(query="lista appuntamenti domani",
                 expected_first_tool="read_events",
                 imported_from="agentskills.io/x/y").
    ERRORE: add_case(query="x", expected_first_tool="ufoize_xyzzy")
    (executor non in catalogo: lo smoke fallira' al boot).
    """
    if expected_arg_keys is None:
        expected_arg_keys = set()
    q_norm = query.strip().lower()
    for existing in BATTERY_IMPORTS:
        if (
            existing.get("expected_first_tool") == expected_first_tool
            and existing.get("q", "").strip().lower() == q_norm
        ):
            return False
    # Strict match: any() gia' copre pipeline read->delete via delete_X.
    # Relaxation creava false-green se delete non eseguito (loop break,
    # final_answer short-circuit, mid-pipeline error): il regex relaxed
    # ^(read_X|delete_X)$ passava anche se nel turno comparivano solo read_X.
    tool_re = rf"^{expected_first_tool}$"

    case = {
        "q": query,
        "tool_re": tool_re,
        "kind": "answer",
        "expected_first_tool": expected_first_tool,
        "expected_arg_keys": set(expected_arg_keys),
        "min_pass_rate": min_pass_rate,
        "imported_from": imported_from,
    }
    BATTERY_IMPORTS.append(case)
    _save_store(BATTERY_IMPORTS)
    return True


def remove_cases_for(expected_first_tool: str) -> int:
    """Rimuove tutti i case per un dato executor (es. dopo uninstall).
    Ritorna numero rimossi."""
    global BATTERY_IMPORTS
    before = len(BATTERY_IMPORTS)
    BATTERY_IMPORTS[:] = [
        c for c in BATTERY_IMPORTS
        if c.get("expected_first_tool") != expected_first_tool
    ]
    after = len(BATTERY_IMPORTS)
    if after != before:
        _save_store(BATTERY_IMPORTS)
    return before - after


def list_cases() -> list:
    """Ritorna copia della lista (immutabile per il chiamante)."""
    return [dict(c) for c in BATTERY_IMPORTS]


def reset() -> None:
    """Cancella tutti i case (usato da test). Atomic rewrite."""
    global BATTERY_IMPORTS
    BATTERY_IMPORTS.clear()
    _save_store([])
