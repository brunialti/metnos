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
    prereq_tool: str
    """Nome dell'executor (o builtin) da invocare prima del retry."""
    hint_field: str
    """Campo dell'observation con gli argomenti per il prereq."""
    arg_builder: Callable[[Any], dict]
    """Funzione (hint_value) -> dict args del prereq."""
    merge_field: str = "entries"
    """Campo del retry_args dove iniettare l'observation arricchita
    (di default `entries`)."""
    merge_source: str = "entries"
    """Campo dell'observation del prereq da cui prendere il valore
    (di default `entries`)."""


# Registry chiuso. Estendere con append-only.
REMEDIATIONS: dict[str, RemediationPlan] = {
    # describe_entries (e simili) su entries URL-only → fetch HTML.
    "needs_content_fetch": RemediationPlan(
        prereq_tool="read_urls_html",
        hint_field="needs_urls_html",
        arg_builder=lambda urls: {"urls": list(urls)[:5]},
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
    hint = obs.get(plan.hint_field)
    if not hint:
        return None
    prereq_args = plan.arg_builder(hint)
    if not isinstance(prereq_args, dict):
        return None
    try:
        prereq_obs = invoke_prereq(plan.prereq_tool, prereq_args)
    except Exception as ex:
        # §2.8 no silent failure: log warn cosi' problemi del prereq tool
        # vanno nei log e non spariscono in un fallback sintetico.
        _LOG.warning(
            "auto_remediation: prereq %r raised %r for error_class=%r",
            plan.prereq_tool, ex, obs.get("error_class"),
        )
        return ({"ok": False,
                 "error": f"prereq {plan.prereq_tool} raised: "
                          f"{type(ex).__name__}: {ex}"},
                {}, {"prereq_tool": plan.prereq_tool,
                     "error_class": obs.get("error_class")})
    if not prereq_obs.get("ok"):
        return (prereq_obs, {}, {"prereq_tool": plan.prereq_tool,
                                  "error_class": obs.get("error_class")})
    enriched_value = prereq_obs.get(plan.merge_source) or []
    retry_args = dict(original_args)
    retry_args[plan.merge_field] = enriched_value
    retry_args.pop("from_step", None)
    return (prereq_obs, retry_args,
            {"prereq_tool": plan.prereq_tool,
             "error_class": obs.get("error_class")})
