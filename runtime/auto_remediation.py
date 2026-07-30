# SPDX-License-Identifier: AGPL-3.0-only
"""auto_remediation.py — pattern generale install_on_demand applicato al
contenuto (ADR 0153, 20/5/2026 v6).

Schema: un executor che rileva di non avere il prerequisito necessario
lo dichiara con un `error_class` strutturato + un campo hint. Il runtime
detect l'error_class, sintetizza al volo lo step prerequisito,
ricalcola gli args dell'executor originale, ed esegue il retry.

Tabella centrale `REMEDIATIONS`: mapping chiuso `error_class -> Plan`:

  Plan(
    prereq_tool = nome dell'executor da invocare prima,
    hint_field  = campo dell'observation con i parametri (es. urls),
    arg_builder = funzione (hint_value) -> dict args per il prereq,
    merge_into  = come incorporare l'output del prereq nei retry args
  )

Aggiungere un nuovo error_class = aggiungere 1 riga alla tabella.

ADR 0143 install_on_demand per binari mancanti e' un caso particolare
di questo schema (error_class=binary_missing, prereq_tool=admin).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Optional

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class RemediationPlan:
    """Specifica come rimediare a un error_class noto."""
    prereq_tool: Any
    """Nome dell'executor da invocare. `str` per dispatch statico, oppure
    `Callable[[dict], str]` per dispatch dinamico (es. cascade su
    intent.object). Risolto da try_remediate() al runtime."""
    hint_field: Optional[str]
    """Campo dell'observation con gli argomenti per il prereq. Se None,
    l'intera obs e' passata all'arg_builder (caso dynamic-dispatch)."""
    arg_builder: Callable[[Any], dict]
    """Funzione (hint_value | obs) -> dict args del prereq. Riceve il
    valore di hint_field se non-None, altrimenti l'intera obs."""
    merge_field: str = "entries"
    """Campo del retry_args dove iniettare l'observation arricchita
    (di default `entries`)."""
    merge_source: str = "entries"
    """Campo dell'observation del prereq da cui prendere il valore
    (di default `entries`)."""
    skip_retry: bool = False
    """Se True, il flusso si interrompe dopo il prereq senza retry
    dell'executor originale. Usato per remediation fail-fast come
    dialog get_inputs, dove il turno termina in attesa di input
    dell'utente (che arrivera' in un turno successivo)."""


def _choose_default_source(obs: dict) -> str:
    """Cascade per needs_data_source.

    Ordine deterministico:
      1. intent.object -> primo di OBJECT_PRIMARY_TOOLS[object]
      2. find_urls (default: internet)
    consult_frontier come 3a stage e' fase 2 (dopo verifica empirica).
    """
    obj = obs.get("intent_object")
    if obj:
        try:
            from prefilter import _OBJECT_PRIMARY_TOOLS
            primary = _OBJECT_PRIMARY_TOOLS.get(obj)
            if primary:
                # tuple di tool names; primo = preferito.
                return primary[0]
        except Exception as ex:
            _LOG.warning("auto_remediation: object primary lookup failed: %r", ex)
    return "find_urls"


def _build_data_source_args(obs: dict) -> dict:
    """Compone gli args per il producer scelto, sulla base di query+object."""
    query = obs.get("user_query") or ""
    tool = _choose_default_source(obs)
    if tool == "find_urls":
        return {"search_query": query} if query else {}
    if tool.startswith("read_messages"):
        return {}  # account default, time_window derivato dall'extractor
    if tool.startswith("get_processes") or tool == "get_now":
        return {}
    if tool.startswith("find_files"):
        return {"pattern": "*"}
    if tool.startswith("list_dirs"):
        return {"path": "."}
    # Default: nessun arg, lascia ai default dell'executor.
    return {}


def _build_action_dialog_args(obs: dict) -> dict:
    """Compone i dialog args per get_inputs su needs_action_target.

    Il dialog chiede all'utente di specificare il target esplicitamente.
    Pattern free_text per default; future estensioni possono fornire choice
    se intent.object suggerisce un set finito (es. persons noti).
    """
    obj = obs.get("intent_object") or "target"
    verb = obs.get("verb") or "azione"
    query = obs.get("user_query") or ""
    return {
        "title": f"Specifica il target per {verb}",
        "dialog": [{
            "var": "target",
            "prompt": (
                f"Per «{query[:60]}» serve un target esplicito ({obj}). "
                f"Puoi indicarlo?"
            ),
            "schema": {"kind": "text"},
        }],
    }


# Registry chiuso. Estendere con append-only.
REMEDIATIONS: dict[str, RemediationPlan] = {
    # describe_entries (e simili) su entries URL-only → fetch HTML.
    "needs_content_fetch": RemediationPlan(
        prereq_tool="read_urls_html",
        hint_field="needs_urls_html",
        arg_builder=lambda urls: {"urls": list(urls)[:5]},
    ),
    # Pipeline shape FSM: consumer/formatter senza sorgente upstream.
    # Cascade automatica: object→primary tool → find_urls (internet).
    "needs_data_source": RemediationPlan(
        prereq_tool=_choose_default_source,  # dinamico
        hint_field=None,                       # usa intera obs
        arg_builder=_build_data_source_args,
        merge_field="entries",
        merge_source="entries",
    ),
    # Pipeline shape FSM: azione senza target. MAI cascade automatica:
    # un target di mutazione non si inventa. Si chiede via get_inputs;
    # il turno termina in attesa, il prossimo turno riprende col target.
    "needs_action_target": RemediationPlan(
        prereq_tool="get_inputs",
        hint_field=None,
        arg_builder=_build_action_dialog_args,
        skip_retry=True,
    ),
    # Estendibile: ocr, embedding, ecc. Vedi ADR 0153 §"Tabella mapping".
}


def get_plan(error_class: Optional[str]) -> Optional[RemediationPlan]:
    """Ritorna il piano di rimedio per un error_class, o None."""
    if not error_class:
        return None
    return REMEDIATIONS.get(error_class)


def try_remediate(
    obs: dict,
    original_args: dict,
    *,
    invoke_prereq: Callable[[str, dict], dict],
) -> Optional[tuple[dict, dict, dict]]:
    """Tenta di rimediare a un error_class strutturato in `obs`.

    Args:
      obs: observation dell'executor originale (deve avere ok=False +
           error_class noto + hint_field).
      original_args: args con cui era stato chiamato l'executor originale.
      invoke_prereq: callable (tool_name, args) -> obs che il runtime
                     espone per eseguire il prereq. Astrae se il prereq
                     e' un builtin (handler in-process) o un executor
                     reale (subprocess via invoke_executor).

    Returns:
      None se nessuna remediation applicabile.
      tuple (prereq_obs, retry_args, plan_info) se rimedio applicato.
      Il caller usa retry_args per ri-chiamare l'executor originale; il
      plan_info contiene metadata audit (prereq_tool, error_class).

    Idempotency: il runtime deve evitare loop chiamando try_remediate
    UNA VOLTA per error_class per turno. Implementare un flag per-turno
    nel caller (es. `_remediations_attempted: set[str]`).
    """
    if obs.get("ok"):
        return None
    plan = get_plan(obs.get("error_class"))
    if plan is None:
        return None
    # Resolve prereq_tool: statico (str) o dinamico (Callable[[obs], str]).
    if callable(plan.prereq_tool):
        prereq_tool = plan.prereq_tool(obs)
    else:
        prereq_tool = plan.prereq_tool
    if not prereq_tool or not isinstance(prereq_tool, str):
        return None
    # Resolve hint: campo specifico o l'intera obs (per builder dinamici).
    if plan.hint_field is None:
        hint = obs
    else:
        hint = obs.get(plan.hint_field)
        if not hint:
            return None
    prereq_args = plan.arg_builder(hint)
    if not isinstance(prereq_args, dict):
        return None
    plan_info = {"prereq_tool": prereq_tool,
                 "error_class": obs.get("error_class"),
                 "skip_retry": plan.skip_retry}
    try:
        prereq_obs = invoke_prereq(prereq_tool, prereq_args)
    except Exception as ex:
        # §2.8 no silent failure: log warn cosi' problemi del prereq tool
        # vanno nei log e non spariscono in un fallback sintetico.
        _LOG.warning(
            "auto_remediation: prereq %r raised %r for error_class=%r",
            prereq_tool, ex, obs.get("error_class"),
        )
        return ({"ok": False,
                 "error": f"prereq {prereq_tool} raised: "
                          f"{type(ex).__name__}: {ex}"},
                {}, plan_info)
    if not prereq_obs.get("ok"):
        return (prereq_obs, {}, plan_info)
    if plan.skip_retry:
        # Fail-fast: prereq eseguito (es. dialog), niente retry.
        # Il caller usa prereq_obs come esito di questo step.
        return (prereq_obs, {}, plan_info)
    enriched_value = prereq_obs.get(plan.merge_source) or []
    retry_args = dict(original_args)
    retry_args[plan.merge_field] = enriched_value
    retry_args.pop("from_step", None)
    return (prereq_obs, retry_args, plan_info)
