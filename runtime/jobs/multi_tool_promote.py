# SPDX-License-Identifier: AGPL-3.0-only
"""multi_tool_promote.py — bridge L2 → L3 promozione (ADR 0150 19/5/2026 v4).

Quando una entry in `multi_tool_paths` raggiunge `uses >= K_synth`, crea un
proto-mnest in `mnestoma.mnests` per innescare la pipeline esistente di
synthesis (`synt.react()` su recurring_protos → executor unificato).

Distinzione tier:
- L2 path memoization: riesegue la sequenza memoizzata (N IPC executor calls).
  Threshold: `MTP_MIN_USES` (3) → match. Costo: ~ms (DB write).
- L3 synthesis: crea UN nuovo executor unificato che incorpora la pipeline.
  Threshold: `K_synth` (50) → proto-mnest → synt_request. Costo: ~150s wall.

Bridge ratio: L2 cattura il low-volume (uses 3-50), L3 prende il sopravvento
quando il pattern e' stabilmente ricorrente e vale la sintesi unica.

Trigger registrazione: callback scheduler v2 `multi_tool_promote` (daily@04:30
suggerito, dopo i18n_translate_pending @02:00 e prima di promoter @04:45).

§7.9 deterministico: niente LLM nel job. Solo lettura DB + scrittura proto-mnest.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

_LOG = logging.getLogger(__name__)


# Threshold per la promozione L2 → L3. Lookup via runtime_settings
# (hierarchy env > toml > default). Lazy: solo al primo call, cosi'
# eventuali modifiche runtime.toml sono picked-up al prossimo job.


def _k_synth() -> int:
    try:
        from runtime_settings import multi_tool_fast_path_k_synth
        return multi_tool_fast_path_k_synth()
    except Exception:
        return int(os.environ.get("METNOS_MTP_K_SYNTH", "50"))


def _build_desired_signature_from_path(
    tools_sequence: list[str],
    args_shape: list[dict],
    canonical_query: str,
) -> dict:
    """Costruisce desired_signature per il proto-mnest derivato dalla pipeline.

    Il PLANNER-side synth (synt.react) leggera' questa signature per
    generare un executor unificato che esegue la pipeline.

    summary: descrizione human-readable della pipeline.
    inputs: chiavi degli args del primo step (entry point).
    outputs: tipo di output (eredita dall'ultimo step se noto, else generic).
    """
    first_args = args_shape[0] if args_shape else {}
    last_tool = tools_sequence[-1] if tools_sequence else "?"
    inputs = []
    for k, v in first_args.items():
        if k == "from_step":
            continue
        # Type hint: se il valore e' un placeholder (es. "<URL>"), usa
        # il tipo; altrimenti il valore e' literal e non aggiungiamo
        # ipotesi di tipo (lasciamo solo la chiave).
        if isinstance(v, str) and v.startswith("<") and v.endswith(">"):
            inputs.append(f"{k}:{v[1:-1].lower()}")
        else:
            inputs.append(k)
    pipeline_desc = " → ".join(tools_sequence)
    summary = (
        f"Pipeline unificata derivata da memoization L2 "
        f"({len(tools_sequence)} step): {pipeline_desc}. "
        f"Canonical query: {canonical_query[:120]!r}."
    )
    return {
        "summary": summary,
        "inputs": inputs or ["unknown"],
        "outputs": [f"output_of_{last_tool}"],
        "errors": [],
        "pipeline": tools_sequence,
        "args_shape": args_shape,
        "canonical_query": canonical_query,
    }


def task_multi_tool_promote(payload: dict[str, Any] | None = None) -> dict:
    """Job daily: scan multi_tool_paths con uses>=K_synth, crea proto-mnest.

    Idempotente: una entry gia' promossa (state='promoted_to_synth') viene
    skippata. Sa solo SCRIVERE proto-mnest, non chiama synt: il PLANNER stesso
    in turn successivi raggiungera' la condizione `recurring_protos` esistente.

    Returns:
        {ok, promoted, skipped, errors}
    """
    payload = payload or {}
    k_synth = int(payload.get("k_synth") or _k_synth())
    stats = {"ok": True, "promoted": 0, "skipped": 0, "errors": []}
    try:
        from multi_tool_paths import MultiToolPathsDB
        from mnestoma import Mnestoma
    except Exception as ex:
        stats["ok"] = False
        stats["errors"].append(f"import: {ex!r}")
        return stats

    mtp = MultiToolPathsDB.get()
    mnest = Mnestoma()

    rows = mtp.conn.execute(
        """SELECT id, canonical_query, tools_sequence, args_shape, uses, state
           FROM multi_tool_paths
           WHERE uses >= ?
             AND state NOT IN ('promoted_to_synth', 'demoted')
           ORDER BY uses DESC, id""",
        (k_synth,),
    ).fetchall()

    for r in rows:
        try:
            tools = json.loads(r["tools_sequence"])
            shapes = json.loads(r["args_shape"])
        except Exception as ex:
            stats["errors"].append(f"parse id={r['id']}: {ex!r}")
            continue
        if not isinstance(tools, list) or len(tools) < 2:
            stats["skipped"] += 1
            continue
        sig_dict = _build_desired_signature_from_path(
            tools, shapes, r["canonical_query"],
        )
        # Passa dict completo (non DesiredSignature) cosi' mnestoma preserva
        # i campi extra `pipeline`, `args_shape`, `canonical_query` necessari
        # a synt.react per la sintesi della pipeline.
        desired = sig_dict
        # Synthesized executor name: analogo a synt stage 1 NAMING.
        # Heuristic: ultimo verbo + primo oggetto. Es. get_urls +
        # describe_entries → describe_urls (sintesi della pipeline).
        # Fallback: <first>__then__<last>. Synt stage 1 fixera' al volo.
        from multi_tool_paths import derive_synth_name
        desired_name = derive_synth_name(tools)
        try:
            mnest_id = mnest.record_passing(
                src_executor=tools[0],
                src_version="v1",
                dst_executor=desired_name,
                dst_version=None,
                dst_exists=False,  # proto-mnest
                desired_signature=desired,
                tags=["mtp_promotion", "ADR_0150"],
                turn_id=f"mtp_promote_{r['id']}",
            )
        except Exception as ex:
            stats["errors"].append(f"record_passing id={r['id']}: {ex!r}")
            continue
        # Marca la entry L2 come promossa (cosi' job successivi la skippano).
        mtp.conn.execute(
            "UPDATE multi_tool_paths SET state = 'promoted_to_synth' "
            "WHERE id = ?",
            (r["id"],),
        )
        stats["promoted"] += 1
        _LOG.info(
            "mtp_promote: L2 id=%d uses=%d → proto-mnest %s "
            "(pipeline=%s, desired=%s)",
            r["id"], r["uses"], mnest_id,
            " → ".join(tools), desired_name,
        )
    return stats


# _derive_synth_name spostato in runtime/multi_tool_paths.py::derive_synth_name
# (19/5 v5) per riuso fra promotion job + matcher L2 (auto-demote).
