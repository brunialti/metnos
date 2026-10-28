# SPDX-License-Identifier: MIT
"""Names and synonyms asked in the credential form (Roberto, 14/10/2026).

When the form collects the fields of a site or mailbox credential, the
runtime adds one optional step: the names the user will call it by, the
first one the main name and the others its synonyms. The step belongs to the
runtime, not to the executor: its value never reaches the credential record
and is saved in the user's names after the form is sent.
"""
from __future__ import annotations

from logging_setup import get_logger

log = get_logger(__name__)

NAMES_VAR = "invocation_names"
_RESUME_TYPES = frozenset({"resume_executor_with_values",
                           "resume_executor_values_tail"})
_MAIL_PREFIXES = ("smtp_", "imap_")
_ERROR_MESSAGES = {
    "invalid": "MSG_INVOCATION_NAME_INVALID",
    "too_short": "MSG_INVOCATION_NAME_TOO_SHORT",
    "vocabulary": "MSG_INVOCATION_NAME_VOCABULARY",
    "taken": "MSG_INVOCATION_NAME_TAKEN",
    "invalid_target": "MSG_INVOCATION_TARGET_INVALID",
    "unknown_mailbox": "MSG_INVOCATION_TARGET_INVALID",
}


def _msg(key: str, **params) -> str:
    from messages import get
    return get(key, **params)


def error_message(reason: str, *, name: str = "", other: str = "") -> str:
    """User-facing reason a name was refused; empty for an unknown reason."""
    key = _ERROR_MESSAGES.get(reason)
    return _msg(key, name=name[:80], other=other[:2048]) if key else ""


def _credential_target(on_complete) -> tuple[str, str] | None:
    """(kind, credential binding) of a site or mailbox fields form."""
    if (not isinstance(on_complete, dict)
            or on_complete.get("type") not in _RESUME_TYPES
            or on_complete.get("executor") != "set_credentials"):
        return None
    args = on_complete.get("args_base")
    if not isinstance(args, dict) or args.get("_fields_form") is not True:
        return None
    kind = args.get("credential_kind")
    binding = str(args.get("binding") or "").strip()
    if kind == "mail":
        binding = next((binding[len(p):] for p in _MAIL_PREFIXES
                        if binding.startswith(p)), binding)
    if kind not in {"site", "mail"} or not binding:
        return None
    return kind, binding


def _site_target(owner_user_id: str, binding: str) -> str:
    """The named address of this site, or its home page when not named yet."""
    import sites_origin
    import users
    for entry in users.list_invocation_names(owner_user_id):
        if entry["kind"] == "site" and sites_origin.same_site_origin(
                sites_origin.origin_of_url(entry["target"]), binding):
            return entry["target"]
    return f"https://{binding}/"


def augment_dialog(dialog: list, on_complete, owner_user_id: str):
    """Add the optional names step to a site or mailbox credential form."""
    found = _credential_target(on_complete)
    if (not found or not owner_user_id
            or on_complete.get("ask_invocation_names") is False
            or any(isinstance(step, dict) and step.get("var") == NAMES_VAR
                   for step in dialog)):
        return dialog, on_complete
    kind, binding = found
    try:
        import users
        target = _site_target(owner_user_id, binding) if kind == "site" else binding
        entry = users.invocation_names_for(owner_user_id, kind, target)
    except Exception as exc:  # noqa: BLE001 -- the credential form must still open
        log.warning("credential names lookup failed: %r", exc)
        return dialog, on_complete
    step = {
        "var": NAMES_VAR,
        "prompt": _msg("MSG_CREDENTIAL_INVOCATION_NAMES_PROMPT"),
        "schema": {"kind": "text"},
        "optional": True,
    }
    if entry:
        step["default"] = ", ".join([entry["name"], *entry["synonyms"]])
    else:
        step["default"] = users.propose_invocation_name(
            kind, target, users.list_invocation_names(owner_user_id))
    runtime_vars = list(on_complete.get("runtime_vars") or ())
    return [*dialog, step], {
        **on_complete,
        "runtime_vars": [*runtime_vars, NAMES_VAR],
        "invocation_target": {"kind": kind, "target": target},
    }


def save_from_values(on_complete, values, owner_user_id: str) -> str:
    """Save the names typed in the form; return what to tell the user.

    An empty field changes nothing. A refused name is reported, never
    silently dropped; the credential itself is handled by its executor.
    """
    target = on_complete.get("invocation_target") if isinstance(on_complete, dict) else None
    raw = (values or {}).get(NAMES_VAR) if isinstance(values, dict) else None
    if not isinstance(target, dict) or not isinstance(raw, str) or not raw.strip():
        return ""
    import users
    names = users.parse_invocation_synonyms(raw)
    if not names:
        return ""
    try:
        saved = users.set_invocation_names(
            owner_user_id, str(target.get("kind") or ""),
            str(target.get("target") or ""), names[0], names[1:])
    except users.InvocationNameError as error:
        return (error_message(error.reason, name=error.name, other=error.other)
                or error_message("invalid_target"))
    return _msg("MSG_CREDENTIAL_INVOCATION_NAMES_SAVED",
                names=", ".join([saved["name"], *saved["synonyms"]]))
