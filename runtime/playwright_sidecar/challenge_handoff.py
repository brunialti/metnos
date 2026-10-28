# SPDX-License-Identifier: MIT
"""Reuse the authenticated browser dialog for a paused executor vector."""
from messages import get as _msg


def request(*, executor: str, sessions: list[str], args: dict) -> dict:
    return {
        "ok": True, "decision": "needs_inputs",
        "needs_inputs": {
            "title": _msg("MSG_BROWSER_USER_TITLE"), "fmt": "form",
            "timeout_s": 600,
            "dialog": [{"var": f"browser_resume_{i}",
                        "prompt": _msg("MSG_BROWSER_USER_PROMPT"),
                        "schema": {"kind": "browser_session", "session_id": sid}}
                       for i, sid in enumerate(dict.fromkeys(sessions), start=1)],
            "on_complete": {"type": "resume_executor_with_values",
                            "executor": executor, "args_base": args},
        },
    }
