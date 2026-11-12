# SPDX-License-Identifier: MIT
"""The person's answers about generated tools, from the administration page (ADR 0240).

Two things only: the preference on the external model, set once, and the one
decision on a tested candidate. Nothing is signed here: a «yes» is recorded
and carried to the administrative signer in the background.
"""
from __future__ import annotations

import asyncio
import functools

from aiohttp import web

from http_render import wants_html


async def admin_changes_preferences(request: web.Request) -> web.Response:
    """POST /admin/changes/preferences: what the person decides once (ADR 0240)."""
    import generated_tools_policy
    from http_routes_admin import _error
    from messages import get as _msg

    # The page posts a form; API clients send JSON. Either way one field.
    field = "external_model"
    try:
        body = dict(await request.post())
        if field not in body:
            body = await request.json()
    except Exception:
        body = None
    choice = body.get(field) if isinstance(body, dict) else None
    if choice not in generated_tools_policy.EXTERNAL_MODEL_CHOICES:
        return _error(400, "INVALID_PREFERENCE", _msg("ERR_CHANGE_INVALID_BODY"))
    by = str(request.get("authenticated_user_id")
             or request.get("lan_principal") or "admin")
    await asyncio.to_thread(generated_tools_policy.set_external_model, choice, by=by)
    if wants_html(request):
        raise web.HTTPSeeOther("/admin/changes")
    return web.json_response({"ok": True, field: choice})


async def candidate_decision(request: web.Request, id_: str, action: str,
                              by: str) -> web.Response:
    """The one decision on a tested candidate: «activate» or «decline».

    Nothing is signed here. A «yes» is recorded and carried to the
    administrative signer in the background; the page shows the work and then
    its outcome. A «no» removes the candidate and rests the proposal.
    """
    import change_applier
    import change_intents as ci_mod
    import synth_activation
    from http_routes_admin import _change_error
    from messages import get as _msg

    current = ci_mod.get_intent(id_)
    if current is None:
        return _change_error(request, id_, 404, "NOT_FOUND",
                             _msg("ERR_CHANGE_NOT_FOUND", id=id_))
    waits = (current.state == ci_mod.STATE_ACCEPTED
             and current.intent_kind == ci_mod.KIND_CREATE_EXECUTOR
             and await asyncio.to_thread(change_applier.pending_review, current))
    if not waits:
        return _change_error(request, id_, 400, "NO_CANDIDATE",
                             _msg("ERR_CHANGE_NO_CANDIDATE"))
    if change_applier.activation_running(current):
        return _change_error(request, id_, 409, "ACTIVATION_RUNNING",
                             _msg("ERR_CHANGE_ACTIVATION_RUNNING"))
    if action == change_applier.DECLINE:
        await asyncio.to_thread(change_applier.decline_candidate, current, by=by)
    else:
        if not synth_activation.signer_available():
            return _change_error(request, id_, 409, "SIGNER_MISSING",
                                 _msg("UI_CHANGE_SIGNER_MISSING"))
        proposal_id, at = change_applier.start_activation(current, by=by)
        # Minutes of work: the answer arrives on the page, not on this request.
        asyncio.get_running_loop().run_in_executor(
            None, functools.partial(change_applier.run_activation, id_, proposal_id,
                                    by=by, at=at))
    # The page reloads and shows the new state on top; other clients read it.
    return web.json_response({"ok": True, "id": id_, "action": action},
                             headers={"HX-Refresh": "true"})
