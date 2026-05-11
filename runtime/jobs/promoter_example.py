"""Generatore deterministico dell'esempio pratico per ogni promote.

§7.9: niente LLM. Compone un blocco markdown con i campi:

    Query: <una query reale dal corpus turn JSONL matching path_hash>
    Pipeline OGGI: <executor sequence dal path_shape pre-synth>
    Pipeline NUOVA: <name del proposal + args dal sig_key>
    Sostituisce: <pct dal call_freq_60d>
    NON sostituisce: <complement deterministico da sig_key shape>

Sorgenti dati:
- `sig_key` del proposal (lista JSON-parseable, ADR 0077).
- `path_hash` + `path_steps` del proposal (ADR 0122 enrichment).
- ETA index sqlite per latency p50/p95.
- Turn JSONL grep per UNA query reale matching `path_hash` (prima trovata).
- `vocab.py` per verbo+oggetto del nuovo executor.

Degrade graceful (mai fail-loud, §2.8 si applica solo a rollback senza blob):
- ETA vuoto → "Pipeline OGGI: <da definire>" + "Sostituisce: dati insufficienti".
- Nessuna query reale → "Query: <esempio sintetico>" (`user_query` del JSON).
- sig_key non parseable → "Pipeline NUOVA: <name>(args sconosciuti)".
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


_DEFAULT_TURNS_DIR = Path.home() / ".local" / "share" / "metnos" / "turns"
_DEFAULT_ETA_DB = (
    Path.home() / ".local" / "share" / "metnos" / "proposals_eta.sqlite"
)


def _turns_dir() -> Path:
    """Override via env per i test."""
    env = os.environ.get("METNOS_TURNS_DIR")
    return Path(env) if env else _DEFAULT_TURNS_DIR


def _eta_db_path() -> Path:
    env = os.environ.get("METNOS_PROPOSALS_ETA_DB")
    return Path(env) if env else _DEFAULT_ETA_DB


def _find_real_query_for_path_hash(path_hash: str) -> str | None:
    """Cerca la prima query reale nel corpus turn JSONL con path_hash matchante.

    Determinismo: itera file in ordine alfabetico, prima riga matchante vince.
    Restituisce None se path_hash vuoto o nessuna match nel corpus.
    """
    if not path_hash:
        return None
    base = _turns_dir()
    if not base.exists():
        return None
    try:
        from path_shape import path_shape_hash
    except ImportError:
        return None
    for fp in sorted(base.glob("*.jsonl")):
        try:
            content = fp.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in content.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except (TypeError, ValueError):
                continue
            steps = rec.get("steps") or []
            if not steps:
                continue
            try:
                computed = path_shape_hash(steps)
            except Exception:
                continue
            if computed == path_hash:
                q = rec.get("user_query") or rec.get("query") or ""
                if isinstance(q, str) and q.strip():
                    return q.strip()
    return None


def _lookup_eta(path_hash: str) -> dict | None:
    """Wrapper isolato di proposals_eta_index.lookup con env override."""
    if not path_hash:
        return None
    try:
        from proposals_eta_index import lookup
    except ImportError:
        return None
    try:
        return lookup(path_hash, db_path=_eta_db_path())
    except Exception:
        return None


def _format_args_from_sig_key(sig_key: Any) -> str:
    """Formatta gli args dal sig_key (lista JSON ADR 0077).

    Tre forme attese:
    - `["dedupe", reason, a, b]`
    - `["generalize", [exec1, exec2, ...]]`
    - `["specialize", exec, arg, val_json]`

    Per le proposte synth (non-introvertiva) il sig_key e' spesso il
    nome stesso + args inferred. Fallback: stringa raw troncata.
    """
    if sig_key is None:
        return "args sconosciuti"
    parsed: Any
    if isinstance(sig_key, str):
        try:
            parsed = json.loads(sig_key)
        except (TypeError, ValueError):
            return sig_key[:80] if sig_key else "args sconosciuti"
    else:
        parsed = sig_key
    if not isinstance(parsed, list) or not parsed:
        return str(parsed)[:80] if parsed else "args sconosciuti"
    head = parsed[0]
    if head == "dedupe" and len(parsed) >= 4:
        return f"dedupe({parsed[2]} -> {parsed[3]}, reason={parsed[1]})"
    if head == "generalize" and len(parsed) >= 2:
        seq = parsed[1]
        if isinstance(seq, list):
            return f"generalize({' -> '.join(str(s) for s in seq)})"
        return f"generalize({seq})"
    if head == "specialize" and len(parsed) >= 4:
        return f"specialize({parsed[1]}, arg={parsed[2]}, val={parsed[3]})"
    return str(parsed)[:80]


def _describe_new_executor(name: str, args_schema: dict) -> str:
    """Descrive il nuovo executor in forma `name(arg1, arg2, ...)`.

    Estrae i required args da `args_schema.required` o le properties top-level.
    """
    args_str = ""
    if isinstance(args_schema, dict):
        req = args_schema.get("required") or []
        if isinstance(req, list) and req:
            args_str = ", ".join(str(a) for a in req)
        else:
            props = args_schema.get("properties") or {}
            if isinstance(props, dict) and props:
                # Prendi i primi 4 nomi di property come hint.
                args_str = ", ".join(list(props.keys())[:4])
    return f"{name}({args_str})" if args_str else f"{name}()"


def _format_call_freq_pct(call_freq_60d: int | None,
                            n_total_60d: int | None) -> str:
    """Formatta la % di chiamate sostituite vs totale corpus 60g."""
    if not call_freq_60d or not n_total_60d or n_total_60d <= 0:
        if call_freq_60d:
            return f"{call_freq_60d} chiamate negli ultimi 60g"
        return "dati insufficienti"
    pct = (call_freq_60d / n_total_60d) * 100.0
    return (
        f"{pct:.1f}% delle chiamate ({call_freq_60d}/{n_total_60d} "
        f"negli ultimi 60g)"
    )


def _complement_description(verb: str, obj: str) -> str:
    """Descrive deterministicamente cosa il nuovo executor NON sostituisce.

    Heuristic: per verbi produttori (find/get/read/list/filter) il nuovo
    non sostituisce altre azioni sull'oggetto (write/delete/move/send).
    Per verbi trasformativi, dice che la versione read del dominio resta.
    """
    if not verb or not obj:
        return "azioni su oggetti diversi e altri verbi del catalogo"
    producers = {"find", "get", "read", "list", "filter"}
    transformers = {"move", "delete", "send", "write", "create", "extract",
                    "compress", "change", "set", "order"}
    if verb in producers:
        return (
            f"azioni trasformative su {obj} "
            f"(write/delete/move/send/create) ne' altri oggetti del catalogo"
        )
    if verb in transformers:
        return (
            f"lettura/discovery su {obj} "
            f"(find/get/read/list) ne' altri oggetti del catalogo"
        )
    return "azioni su oggetti diversi e altri verbi del catalogo"


def render_practical_example(
    proposal: dict,
    evaluator_verdict: dict,
    *,
    catalog: Any = None,
) -> str:
    """Compone l'esempio pratico in markdown.

    `proposal`: il dict JSON della proposta synth.
    `evaluator_verdict`: result.to_dict() da proposal_evaluator (per signals).
    `catalog`: opzionale, per arricchire la descrizione (non strettamente
        necessario, l'esempio resta affermativo anche senza catalog).
    """
    name = proposal.get("name") or proposal.get("expected_name") or "?"
    parts = name.split("_") if name else []
    verb = parts[0] if parts else ""
    obj = parts[1] if len(parts) >= 2 else ""

    # 1) Query reale o sintetica.
    path_hash = proposal.get("path_hash") or ""
    real_q = _find_real_query_for_path_hash(path_hash)
    if real_q is None:
        real_q = (proposal.get("user_query") or "").strip()
    if not real_q:
        real_q = f"[sintetico] esegui {verb} {obj}".strip()
    query_line = f"**Query**: {real_q}"

    # 2) Pipeline OGGI (path_steps + ETA).
    path_steps: list[str] = list(proposal.get("path_steps") or [])
    eta_p50_ms = None
    if not path_steps and path_hash:
        rec = _lookup_eta(path_hash)
        if rec:
            path_steps = list(rec.get("sample_steps") or [])
            eta_p50_ms = rec.get("p50_ms")
    elif path_hash:
        rec = _lookup_eta(path_hash)
        if rec:
            eta_p50_ms = rec.get("p50_ms")
    if path_steps:
        oggi_str = " -> ".join(path_steps)
        if eta_p50_ms:
            oggi_str = f"{oggi_str} (p50 {eta_p50_ms}ms)"
    else:
        oggi_str = "da definire"
    pipeline_oggi_line = f"**Pipeline OGGI**: {oggi_str}"

    # 3) Pipeline NUOVA.
    s2 = (proposal.get("stages") or [])
    args_schema: dict = {}
    if len(s2) >= 2 and isinstance(s2[1], dict):
        out = s2[1].get("output") or {}
        if isinstance(out, dict):
            # Stage 2 puo' avere args_schema o args_properties+args_required.
            if "args_schema" in out and isinstance(out["args_schema"], dict):
                args_schema = out["args_schema"]
            else:
                props = out.get("args_properties") or {}
                req = out.get("args_required") or []
                args_schema = {"properties": props, "required": req}
    nuova_desc = _describe_new_executor(name, args_schema)
    pipeline_nuova_line = f"**Pipeline NUOVA**: {nuova_desc}"

    # 4) Sostituisce: pct from signals.call_freq_60d.
    signals = evaluator_verdict.get("signals") or {}
    cf = signals.get("call_freq_60d")
    cf_total = signals.get("n_calls_60d_total") or signals.get("call_freq_total_60d")
    sostituisce_line = (
        f"**Sostituisce**: {_format_call_freq_pct(cf, cf_total)}"
    )

    # 5) NON sostituisce.
    non_line = f"**NON sostituisce**: {_complement_description(verb, obj)}"

    return "\n".join([
        query_line,
        pipeline_oggi_line,
        pipeline_nuova_line,
        sostituisce_line,
        non_line,
    ])


__all__ = ["render_practical_example"]
