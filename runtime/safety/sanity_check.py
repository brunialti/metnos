"""sanity_check.py — fast-LLM second-opinion on a candidate command.

This is the *only* non-deterministic component in the safety stack. It is
invoked by `sudoer` at fire time, AFTER the deterministic guards have
cleared the command. It can never authorise what they have blocked: its
authority is purely additive.

The model used is the `fast` tier (qwen3:8b, think=false, ~400 tokens).
The prompt is fixed and the output is JSON-schema-guided. Three kinds:
  - "ok"             — proceed
  - "suspicious"     — proceed but log a warning to audit
  - "urgent_review"  — STOP, notify the user with `reason`

Activation heuristic (ADR 0070):
  - skip if scheduler_delay_minutes < 5 AND reversibility == 'reversible'
  - invoke if reversibility in ('irreversible','unknown')
  - invoke if scheduler_delay_minutes >= 5
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal


SmellKind = Literal["ok", "suspicious", "urgent_review"]


@dataclass
class SanityResult:
    smell: SmellKind
    reason: str | None = None
    raw_output: str | None = None  # for audit, never shown to user


PROMPT_TEMPLATE = """\
You are the sanity checker for a privileged shell command about to run on
the user's host. The deterministic guards have already cleared this command.
Your job is a SECOND OPINION on whether it still makes sense IN THE CURRENT
CONTEXT, given the time elapsed since planning and the system state.

You can only ADD blocks, never lift them. If unsure, say "ok".

Original user intent:
  {intent_text}

Canonical argv to execute:
  {argv}

Time elapsed since planning: {age_minutes} minutes
Reversibility class: {reversibility}

System state snapshot (concise):
  {system_state}

Respond with JSON, exactly one of:
  {{"smell": "ok"}}
  {{"smell": "suspicious", "reason": "..."}}
  {{"smell": "urgent_review", "reason": "..."}}

Rules:
- "ok" if nothing has clearly changed since planning.
- "suspicious" if you spot a soft anomaly (e.g. command unusual at this hour,
  or system metric somewhat off) but not enough to block.
- "urgent_review" only on a clear contextual issue (e.g. disk almost full
  for an install, target service missing, planning was hours ago for a
  time-sensitive task).
"""


def should_invoke(
    *,
    scheduler_delay_minutes: int,
    reversibility: str,
) -> bool:
    """ADR 0070 activation heuristic for the sanity check."""
    if reversibility in ("irreversible", "unknown"):
        return True
    if scheduler_delay_minutes >= 5:
        return True
    return False


def compute_sanity_check(
    *,
    intent_text: str,
    argv: list[str],
    scheduler_delay_minutes: int,
    reversibility: str,
    system_state: dict | None = None,
    llm_call=None,
) -> SanityResult:
    """Run the sanity check.

    `llm_call` is a callable `(prompt: str, format: str) -> str` that returns
    the JSON output of a fast-tier LLM. Injected so the function is
    testable; in production it is wired to `runtime.llm_router.call_fast`.
    If `llm_call` is None, returns smell='ok' (degenerate / dev mode).
    """
    if llm_call is None:
        return SanityResult(smell="ok", reason=None, raw_output=None)

    state_text = (
        ", ".join(f"{k}={v}" for k, v in (system_state or {}).items())
        or "no snapshot available"
    )
    prompt = PROMPT_TEMPLATE.format(
        intent_text=intent_text or "(no intent text)",
        argv=" ".join(argv),
        age_minutes=scheduler_delay_minutes,
        reversibility=reversibility,
        system_state=state_text,
    )
    raw = llm_call(prompt, "json")
    try:
        data = json.loads(raw)
        smell = data.get("smell", "ok")
        if smell not in ("ok", "suspicious", "urgent_review"):
            smell = "ok"
        return SanityResult(
            smell=smell,
            reason=data.get("reason"),
            raw_output=raw,
        )
    except (json.JSONDecodeError, AttributeError, TypeError):
        # Parse failure: conservative default = ok (we don't add a block on
        # a malformed second opinion; the deterministic guards have already
        # cleared the command).
        return SanityResult(
            smell="ok",
            reason="sanity check parse error, defaulted to ok",
            raw_output=raw,
        )
