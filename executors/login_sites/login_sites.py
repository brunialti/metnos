#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""login_sites — login web con credenziali cifrate (spec sites F1 §3.4).

Vettoriale (§2.1): `session_ids: array[str]` (o `from_step` da open_sites) →
un login per sessione. `critical=true`. Zero segreti nel result: `reason_code`
è uno slug i18n (mai username/password). Il broker (credential_injection) fa
l'iniezione §3.2 (origine verificata, destinazione risolta dal broker,
no-segreto-negli-shot); QUI passa solo session_id + domain (handle vault).

OUT: entries=[{session_id, logged_in: bool, reason_code?}]  (§3.4).
"""
from __future__ import annotations

import mimetypes
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_RT = os.environ.get("METNOS_RUNTIME") or str(
    _ROOT / "runtime")
for path in (_RT, str(_ROOT / "executors" / "get_approval")):
    if path not in sys.path:
        sys.path.insert(0, path)

from messages import get as _msg  # noqa: E402
from executor_helpers import run_stdio  # noqa: E402
from playwright_sidecar import session_client  # noqa: E402


def _collect_session_ids(args: dict) -> list[str]:
    sids = args.get("session_ids")
    if isinstance(sids, str):
        sids = [sids]
    if isinstance(sids, list) and sids:
        return [s for s in sids if isinstance(s, str) and s]
    # from_step materializzato come `entries` (§4.1): estrai session_id.
    ents = args.get("entries")
    if isinstance(ents, list):
        return [e.get("session_id") for e in ents
                if isinstance(e, dict) and e.get("session_id")]
    one = args.get("session_id")
    return [one] if isinstance(one, str) and one else []


def _reason_message(reason_code: str | None) -> str | None:
    """Mappa lo slug della tassonomia fallimento (§9) al messaggio i18n.
    Nessun eco di credenziali. Fallback onesto se lo slug non ha una chiave."""
    if not reason_code:
        return None
    # Fix adversarial #12: chiave CANONICA da `sites_observed.REASON_MSG` (evita
    # `MSG_SITES_RC_HTTP_FORBIDDEN` vs `MSG_SITES_RC_FORBIDDEN`); il cooldown usa
    # la sua chiave dedicata, non il prefisso RC.
    _special = {"sites_cooldown_active": "MSG_SITES_COOLDOWN_ACTIVE"}
    try:
        from sites_observed import REASON_MSG
    except Exception:
        REASON_MSG = {}
    key = (_special.get(reason_code) or REASON_MSG.get(reason_code)
           or f"MSG_SITES_RC_{reason_code.upper()}")
    msg = _msg(key)
    if msg.startswith("<missing:"):
        return None  # slug senza messaggio dedicato: nessun eco, solo il code
    return msg


def _attachment(path: str, sensitive: bool) -> dict:
    return {"kind": "image", "path": path, "basename": Path(path).name,
            "mime": mimetypes.guess_type(path)[0] or "image/png",
            "sensitive": sensitive}


def invoke(args: dict) -> dict:
    owner = os.environ.get("METNOS_ACTOR") or "host"
    channel = os.environ.get("METNOS_CHANNEL") or ""
    session_ids = _collect_session_ids(args)
    if not session_ids:
        return {"ok": False, "error": _msg("ERR_ARG_MISSING", arg="session_ids"),
                "error_class": "invalid_args", "entries": []}

    domain = args.get("domain")  # handle vault opzionale; default = origine pagina
    form_hint = args.get("form_hint")
    credential_mode = str(args.get("_credential_mode") or "default")
    if credential_mode not in {"default", "none"}:
        return {"ok": False,
                "error": _msg("ERR_ARG_INVALID", arg="_credential_mode",
                              reason="default|none"),
                "error_class": "invalid_args", "entries": []}
    approval_tokens = args.get("_approval_tokens") or {}
    if not isinstance(approval_tokens, dict):
        approval_tokens = {}
    otp_session_vars = args.get("_otp_session_vars") or {}
    if not isinstance(otp_session_vars, dict):
        otp_session_vars = {}

    def _one_time_code_for(session_id: str) -> str | None:
        for var, mapped_session in otp_session_vars.items():
            value = args.get(var)
            if mapped_session == session_id and isinstance(value, str) and value:
                return value
        return None

    entries = []
    pending = []
    otp_pending = []
    attachments = []
    for sid in session_ids:
        res = session_client.session_login(
            session_id=sid, owner=owner, domain=domain, form_hint=form_hint,
            approval_token=approval_tokens.get(sid),
            one_time_code=_one_time_code_for(sid),
            credential_mode=credential_mode)
        shot = res.get("screenshot_path")
        if shot:
            attachments.append(_attachment(
                shot, bool(res.get("sensitive"))))
        if res.get("approval_required"):
            token = res.get("approval_token")
            if token:
                pending.append((sid, token, res))
            continue
        logged_in = bool(res.get("logged_in"))
        reason = res.get("reason_code")
        # A rejected/expired email code is recoverable.  Keep the browser
        # session alive so the user can submit a fresh code instead of
        # restarting the whole login flow.
        if (not logged_in and res.get("error_class") in
                {"otp_failed", "two_factor_failed"}):
            reason = "two_factor_required"
        entry = {"session_id": sid, "logged_in": logged_in}
        if reason:
            entry["reason_code"] = reason  # slug i18n, MAI un segreto
            msg = _reason_message(reason)
            if msg:
                entry["message"] = msg
            # Un fallimento definitivo non deve lasciare context orfani ne'
            # consumare la quota del turno successivo. 2FA/CAPTCHA restano
            # aperti per la continuazione assistita dall'utente.
            if reason not in ("two_factor_required",
                               "two_factor_push_required",
                               "captcha_required", "approval_pending"):
                closed = session_client.session_close(
                    session_id=sid, owner=owner)
                entry["session_closed"] = bool(closed.get("count", 0))
        if reason == "two_factor_required":
            otp_pending.append(sid)
        entries.append(entry)

    if pending:
        from get_approval import invoke as approval_invoke
        tokens = {sid: token for sid, token, _ in pending}
        origin_gate = all(res.get("approval_kind") == "credential_origin"
                          for _, _, res in pending)
        if origin_gate:
            bindings = "; ".join(
                f"{res.get('vault_domain')} -> {res.get('credential_origin')}"
                for _, _, res in pending)
            prompt = _msg("MSG_SITES_CREDENTIAL_ORIGIN_APPROVAL_PROMPT",
                          bindings=bindings)
            title = _msg("MSG_SITES_CREDENTIAL_ORIGIN_APPROVAL_TITLE")
        else:
            descriptions = "; ".join(str(
                res.get("resolved_target") or res.get("description")
                or domain or form_hint or "") for _, _, res in pending)
            prompt = _msg("MSG_SITES_APPROVAL_PROMPT",
                          action=descriptions)
            title = _msg("MSG_SITES_APPROVAL_TITLE")
        resume_args = {
            "session_ids": list(tokens), "_approval_tokens": tokens,
            "_credential_mode": credential_mode,
        }
        if domain is not None:
            resume_args["domain"] = domain
        if form_hint is not None:
            resume_args["form_hint"] = form_hint
        gate = approval_invoke({
            "prompt": prompt, "title": title,
            "actor": owner, "channel": channel, "timeout_s": 3600,
            "on_approve": {"tool": "login_sites", "args": resume_args},
            "on_reject": {"tool": "delete_sites", "args": {
                "session_ids": list(tokens),
            }},
        })
        if attachments:
            gate["attachments"] = attachments
        gate["pending_sessions"] = list(tokens)
        return gate

    if otp_pending:
        prompt = _msg("MSG_SITES_RC_TWO_FACTOR_REQUIRED")
        dialog = []
        otp_vars = {}
        for index, sid in enumerate(otp_pending, start=1):
            var = ("one_time_code" if len(otp_pending) == 1
                   else f"one_time_code_{index}")
            otp_vars[var] = sid
            dialog.append({
                "var": var,
                "prompt": prompt,
                "schema": {"kind": "credentials", "secret": True},
            })
        resume_args = {
            "session_ids": otp_pending,
            "_otp_session_vars": otp_vars,
            "_credential_mode": credential_mode,
        }
        if domain is not None:
            resume_args["domain"] = domain
        if form_hint is not None:
            resume_args["form_hint"] = form_hint
        return {
            "ok": True,
            "decision": "needs_inputs",
            "needs_inputs": {
                "title": prompt,
                "dialog": dialog,
                "fmt": "form",
                "timeout_s": 600,
                "on_complete": {
                    "type": "resume_executor_with_values",
                    "executor": "login_sites",
                    "args_base": resume_args,
                },
            },
            "entries": entries,
            "metadata": {"logged_in": 0, "total": len(entries)},
            "final_message_hint": prompt,
        }

    ok = any(e["logged_in"] for e in entries)
    out = {"ok": ok, "entries": entries,
           "metadata": {"logged_in": sum(1 for e in entries if e["logged_in"]),
                        "total": len(entries)}}
    if attachments:
        out["attachments"] = attachments
    if not ok:
        first_reason = next((e.get("reason_code") for e in entries
                             if e.get("reason_code")), "login_failed")
        out["error"] = (_reason_message(first_reason)
                        or _msg("ERR_OP_FAILED", reason="login_sites"))
        # Credenziali mancanti/errate, CAPTCHA e 2FA non si correggono
        # riproponendo lo stesso piano: il Terminator deve cedere all'utente.
        out["error_class"] = "needs_user_action"
    return out


def main():
    run_stdio(invoke, error_extra={"entries": []})


if __name__ == "__main__":
    main()
