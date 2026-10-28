# SPDX-License-Identifier: MIT
"""Add or update a site or mailbox credential from the administration page.

Roberto, 14/10/2026: credentials of sites and mailboxes are managed in one
place and also from the UI. The page opens the same protected form as the
chat: `set_credentials` runs through the shared dialog branch and its form is
created at the common form choke point, names step included. Secret values
travel only inside that form, never in the page request.
"""
from __future__ import annotations

import uuid
from urllib.parse import urlsplit

from logging_setup import get_logger

log = get_logger(__name__)

_MAIL_PREFIX = "smtp_"


def _msg(key: str, **params) -> str:
    from messages import get
    return get(key, **params)


def _binding(kind: str, target: str) -> str:
    """Credential record name of a site address or a mailbox name."""
    import credentials
    import sites_origin
    import users

    known = set(credentials.list_domains())
    if kind == "site":
        if target in known:
            return target
        url = users._invocation_target("site", target)
        return sites_origin._norm_host(urlsplit(url).hostname) or ""
    if kind == "mail":
        name = target[len(_MAIL_PREFIX):] if target.startswith(_MAIL_PREFIX) else target
        return _MAIL_PREFIX + users._invocation_target("mail", name)
    return ""


def open_form(*, owner_user_id: str, kind: str, target: str,
              ask_names: bool = True) -> dict:
    """Open the protected credential form; return where the browser goes.

    A credential that already exists opens the update form (stored
    non-secret values shown, secrets kept when left empty); a new one opens
    the form for its kind. `ask_names=False` leaves out the names step, for
    a profile other than the signed-in user's own.
    """
    import credentials
    import orchestration
    import users

    user = users.get_user(owner_user_id)
    if not user or kind not in {"site", "mail"}:
        return {"ok": False, "error": _msg("MSG_INVOCATION_TARGET_INVALID")}
    try:
        binding = _binding(kind, str(target or "").strip())
        credentials._validate_domain(binding)
    except ValueError:
        return {"ok": False, "error": _msg("MSG_INVOCATION_TARGET_INVALID")}
    args = {"binding": binding, "credential_kind": kind}
    if binding in credentials.list_domains():
        args["_update_form"] = True
    actor = str(user.get("name") or owner_user_id)
    turn_id = "ui" + uuid.uuid4().hex[:14]
    result = orchestration._esegui_ramo(
        "set_credentials", args, actor=actor, channel="http",
        owner_user_id=user["id"], turn_id=turn_id, contesto="credential_ui")
    if not isinstance(result, dict) or result.get("decision") != "needs_inputs":
        error = (result or {}).get("error") if isinstance(result, dict) else ""
        log.warning("credential form not opened kind=%s: %s", kind, error)
        return {"ok": False, "error": _msg(
            "MSG_CREDENTIAL_FORM_NOT_OPENED", detail=str(error or ""))}
    if not ask_names:
        payload = result.get("needs_inputs") or {}
        callback = payload.get("on_complete")
        if isinstance(callback, dict):
            payload["on_complete"] = {**callback, "ask_invocation_names": False}
    opened = orchestration.orchestrate_needs_inputs(
        result, sender_id=f"http:{actor}", actor=actor,
        owner_user_id=user["id"], channel="http", origin_turn_id=turn_id)
    dialog_id = opened.get("dialog_id") if isinstance(opened, dict) else None
    if not opened.get("ok") or not dialog_id:
        return {"ok": False, "error": _msg(
            "MSG_CREDENTIAL_FORM_NOT_OPENED", detail=str(opened.get("error") or ""))}
    return {"ok": True, "dialog_id": dialog_id,
            "form_url": f"/agent/dialog/{dialog_id}/form"}
