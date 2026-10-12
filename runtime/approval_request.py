# SPDX-License-Identifier: MIT
"""Consent requested from inside an executor, created by the runtime.

An executor runs in the OS sandbox, which mounts its own code and the runtime
but no sibling executor. It therefore does not build the consent form itself:
it returns this `needs_inputs` descriptor and the runtime creates the bound
form, verifies the human submission and dispatches the chosen branch once
(`gate_dispatch`), exactly as for a `get_approval` step.
"""
from __future__ import annotations

from messages import get as _msg

MAX_PROMPT_LEN = 200  # same bound as the get_approval executor
APPROVE = "approve"


def approval_request(*, prompt: str, title: str, on_approve: dict,
                     on_reject: dict | None = None,
                     timeout_s: int = 3600) -> dict:
    """Return the closed consent descriptor for the runtime to present."""
    if len(prompt) > MAX_PROMPT_LEN:
        prompt = prompt[:MAX_PROMPT_LEN - 1].rstrip() + "…"
    callback = {"type": "gate_dispatch", "approve_value": APPROVE,
                "on_approve": on_approve}
    if on_reject is not None:
        callback["on_reject"] = on_reject
    return {
        "ok": True,
        "decision": "needs_inputs",
        "needs_inputs": {
            "title": title,
            "fmt": "auto",
            "timeout_s": timeout_s,
            "dialog": [{
                "var": "decision",
                "prompt": prompt,
                "schema": {"kind": "choice", "choices": [
                    {"label": _msg("MSG_BTN_APPROVE"), "value": APPROVE},
                    {"label": _msg("MSG_BTN_REJECT"), "value": "reject"},
                ]},
            }],
            "on_complete": callback,
        },
        "final_message_hint": prompt,
    }
