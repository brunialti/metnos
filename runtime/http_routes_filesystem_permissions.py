"""Independent server permissions and per-device filesystem permissions."""
from __future__ import annotations

import asyncio
import json
import time

from aiohttp import web

import devices
import users
import filesystem_permissions as permissions
from http_app_state import ADMIN_KEY, app_get
from http_render import render_template
from messages import get as _msg


def _admin(request):
    if request.get("role") != "admin" or not request.get("authenticated_user_id"):
        raise web.HTTPForbidden()


async def device_permissions(request: web.Request) -> web.Response:
    _admin(request)
    device_id = request.match_info["id"]
    dev = await asyncio.to_thread(devices.get_device, device_id)
    if dev is None or dev.revoked_at:
        raise web.HTTPNotFound()
    try:
        profile = json.loads(dev.profile_json or "{}")
    except (TypeError, ValueError):
        profile = {}
    caps = profile.get("protocol_capabilities") if isinstance(profile, dict) else None
    supported = isinstance(caps, list) and permissions.CAPABILITY in caps
    return await _page(request, scope=device_id, owner=dev.owner_user_id,
                       title=dev.name, style=permissions.path_style(device_id),
                       supported=supported)


async def server_permissions(request: web.Request) -> web.Response:
    _admin(request)
    owner = request.query.get("owner", request["authenticated_user_id"])
    user = await asyncio.to_thread(users.get_user, owner)
    if user is None or user["id"] != owner:
        raise web.HTTPNotFound()
    all_users = await asyncio.to_thread(users.list_users)
    return await _page(request, scope="server", owner=owner,
                       title=_msg("MSG_FS_SERVER"), style="posix",
                       supported=True, all_users=all_users)


async def _page(request, *, scope, owner, title, style, supported, all_users=None):
    # Bind the token to both the account and the host. A device form cannot
    # change server rights, nor can a form for one account affect another.
    token_scope = scope + ":" + owner
    error = ""
    if request.method == "POST":
        form = await request.post()
        if not permissions.valid_form_token(form.get("form_token"), app_get(request.app, ADMIN_KEY, ""), token_scope):
            raise web.HTTPForbidden()
        if form.get("scope", scope) != scope:
            raise web.HTTPBadRequest()
        try:
            if form.get("action") == "revoke":
                changed = await asyncio.to_thread(
                    permissions.revoke_grant, form.get("grant_id", ""),
                    owner=owner, device_id=scope,
                    actor=request["authenticated_user_id"])
                if not changed:
                    raise web.HTTPNotFound()
            elif form.get("action") == "grant":
                ttl = form.get("ttl", "")
                if ttl not in {"", "3600", "86400", "604800"}:
                    raise permissions.PermissionError("ERR_FS_GRANT_INVALID")
                await asyncio.to_thread(
                    permissions.create_grant, owner=owner, device_id=scope,
                    path=form.get("path", ""), kind=form.get("kind", ""),
                    read=form.get("read") == "on", write=form.get("write") == "on",
                    recursive=form.get("recursive") == "on", overwrite=form.get("overwrite") == "on",
                    expires_at=int(time.time()) + int(ttl) if ttl else None,
                    actor=request["authenticated_user_id"])
            else:
                raise web.HTTPBadRequest()
        except permissions.PermissionError as exc:
            error = _msg(str(exc))
        else:
            raise web.HTTPSeeOther(str(request.rel_url))
    grants = await asyncio.to_thread(permissions.list_grants, owner, scope)
    return web.Response(text=render_template(
        "filesystem_permissions.html", scope=scope, owner=owner, title=title,
        grants=grants, style=style, supported=supported, all_users=all_users,
        error=error, form_token=permissions.form_token(app_get(request.app, ADMIN_KEY, ""), token_scope)),
        content_type="text/html", status=400 if error else 200,
        headers={"Cache-Control": "no-store", "Referrer-Policy": "same-origin"})


_LOOPBACK = frozenset({"127.0.0.1", "::1"})


async def device_filesystem_relay(request: web.Request) -> web.Response:
    """Server files for a device, relayed by this host's device port.

    The device port can run in a sandbox; this service holds the rights the
    server broker already uses. Only that local port arrives here: admin key,
    loopback and no forwarded client. The device signature is verified again.
    """
    if (request.get("role") != "admin" or request.remote not in _LOOPBACK
            or "X-Forwarded-For" in request.headers or "Forwarded" in request.headers):
        raise web.HTTPForbidden()
    import agent_server
    verified = await agent_server._verified_device_body(request)
    if isinstance(verified, web.Response):
        return verified
    return await agent_server.filesystem_response(*verified)


ROUTES = [(method, "/admin/devices/{id}/permissions", device_permissions) for method in ("GET", "POST")]
ROUTES += [(method, "/admin/permissions", server_permissions) for method in ("GET", "POST")]
ROUTES += [("POST", "/agent/filesystem/server", device_filesystem_relay)]  # agent_server.FILESYSTEM_RELAY_PATH
