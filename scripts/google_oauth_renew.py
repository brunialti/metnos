#!/usr/bin/env python3
"""Owner-scoped Google credential renewal; local Unix API, public consent only.

No resource operations or automatic retry of an interrupted publication.
The shared /oauth/callback delegates unrelated states to the existing server.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import html
import json
import os
from pathlib import Path
import secrets
import socket
import stat
import struct
import sys
import tempfile
import time
from urllib.parse import urlsplit

from aiohttp import ClientError, ClientSession, ClientTimeout, UnixConnector, web

PREFIX = "gwrenew.v1."
BASE = "/oauth/google-workspace"
TTL = 600


def runtime_imports():
    runtime = os.environ.get("METNOS_RUNTIME")
    if not runtime:
        runtime = next((str(p / "runtime") for p in Path(__file__).resolve().parents
                        if (p / "runtime/config.py").is_file()), None)
    if not runtime:
        runtime = Path(__file__).with_name("runtime-path").read_text().strip()
    if runtime not in sys.path:
        sys.path.insert(0, runtime)
    import config
    import messages
    return config, messages.get


def paths():
    config, _ = runtime_imports()
    home = Path(os.environ.get("METNOS_SKILL_HOME") or
                config.PATH_USER_DATA / "skills/google-workspace")
    # Stable for both the user daemon and Shibot's system unit (no XDG_RUNTIME_DIR).
    return home, config.PATH_USER_STATE / "google-oauth-renew" / "broker.sock"


def private_json(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError("private_file_required")
    return json.loads(path.read_text(encoding="utf-8"))


def scopes(value):
    if isinstance(value, str):
        value = value.split()
    if not isinstance(value, list) or not value or not all(isinstance(s, str) and s for s in value):
        raise ValueError("scopes_required")
    return sorted(set(value))


def refresh_revision(token):
    value = token.get("refresh_token")
    if not isinstance(value, str) or not value:
        raise ValueError("refresh_token_required")
    return hashlib.sha256(value.encode()).hexdigest()


def atomic_token(path, payload):
    fd, name = tempfile.mkstemp(prefix=".google-token-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(payload, out)
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, path)
        parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class Broker:
    def __init__(self, home, origin, upstream, msg, *, clock=time.monotonic, flow_factory=None):
        parsed = urlsplit(origin)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
            raise ValueError("https_origin_required")
        target = urlsplit(upstream)
        if target.scheme != "http" or target.hostname not in ("localhost", "127.0.0.1") or target.username or target.query or target.fragment or target.path not in ("", "/"):
            raise ValueError("loopback_upstream_required")
        self.home, self.origin, self.upstream = Path(home), origin.rstrip("/"), upstream.rstrip("/")
        self.msg, self.clock, self.flow_factory = msg, clock, flow_factory
        self.entry = None

    def current(self):
        entry = self.entry
        if entry and self.clock() > entry["deadline"] and entry["status"] == "pending":
            entry["status"] = "expired"
            entry.pop("flow", None)
            entry.pop("auth_url", None)
        return entry

    def begin(self):
        entry = self.current()
        if entry and entry["status"] in ("pending", "exchanging"):
            return self.public_status(entry)
        token = private_json(self.home / "google_token.json")
        client = private_json(self.home / "google_client_secret.json")
        client = client.get("web") or client.get("installed") or {}
        if client.get("client_id") != token.get("client_id"):
            raise ValueError("client_mismatch")
        requested = scopes(token.get("scopes"))
        factory = self.flow_factory
        if factory is None:
            from google_auth_oauthlib.flow import Flow
            factory = Flow.from_client_secrets_file
        state = PREFIX + secrets.token_urlsafe(32)
        flow = factory(str(self.home / "google_client_secret.json"), scopes=requested,
                       redirect_uri=self.origin + "/oauth/callback", autogenerate_code_verifier=True)
        auth_url, returned_state = flow.authorization_url(access_type="offline", prompt="consent", state=state)
        if returned_state != state:
            raise ValueError("state_mismatch")
        self.entry = {"id": secrets.token_urlsafe(24), "cap": secrets.token_urlsafe(32),
                      "state": state, "auth_url": auth_url, "flow": flow,
                      "scopes": requested, "revision": refresh_revision(token),
                      "deadline": self.clock() + TTL, "status": "pending"}
        return self.public_status(self.entry)

    def public_status(self, entry):
        result = {"ok": entry["status"] not in ("failed", "expired"),
                  "renewal_id": entry["id"], "status": entry["status"]}
        if entry["status"] == "pending":
            result["consent_url"] = self.origin + BASE + "/authorize/" + entry["cap"]
            result["expires_in"] = max(0, int(entry["deadline"] - self.clock()))
        if entry.get("error_code"):
            result["error_code"] = entry["error_code"]
        return result

    @staticmethod
    def local_owner(request):
        peer = request.transport.get_extra_info("socket") if request.transport else None
        if peer is None or peer.family != socket.AF_UNIX:
            return False
        _, uid, _ = struct.unpack("3i", peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        return uid == os.getuid()

    async def local_start(self, request):
        if not self.local_owner(request):
            return web.json_response({"ok": False, "error_code": "local_owner_required"}, status=403)
        try:
            return web.json_response(self.begin(), headers={"Cache-Control": "no-store"})
        except Exception:
            return web.json_response({"ok": False, "error_code": "configuration_invalid"}, status=503)

    async def local_status(self, request):
        if not self.local_owner(request):
            return web.json_response({"ok": False, "error_code": "local_owner_required"}, status=403)
        entry = self.current()
        if not entry or not secrets.compare_digest(request.query.get("id", ""), entry["id"]):
            return web.json_response({"ok": False, "status": "unknown"}, status=404)
        return web.json_response(self.public_status(entry), headers={"Cache-Control": "no-store"})

    def page(self, key, status=200):
        title = html.escape(self.msg("UI_GOOGLE_RENEW_TITLE"))
        body = html.escape(self.msg(key))
        lang = html.escape(runtime_imports()[0].INSTANCE_LANG, quote=True)
        return web.Response(text=f'<!doctype html><html lang="{lang}"><meta charset="utf-8">'
                            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
                            f'<title>{title}</title><main><h1>{title}</h1><p>{body}</p></main></html>',
                            content_type="text/html", status=status,
                            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
                                     "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
                                     "X-Content-Type-Options": "nosniff"})

    async def authorize(self, request):
        entry = self.current()
        if not entry or entry["status"] != "pending" or not secrets.compare_digest(request.match_info["cap"], entry["cap"]):
            return self.page("UI_GOOGLE_RENEW_EXPIRED", 410)
        raise web.HTTPFound(entry["auth_url"], headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})

    def exchange(self, entry, code):
        flow = entry["flow"]
        flow.fetch_token(code=code, timeout=20)
        granted = scopes(flow.oauth2session.token.get("scope"))
        if granted != entry["scopes"]:
            raise ValueError("scope_mismatch")
        payload = json.loads(flow.credentials.to_json())
        if not payload.get("token") or not payload.get("refresh_token"):
            raise ValueError("token_incomplete")
        payload["scopes"] = granted
        path = self.home / "google_token.json"
        current = private_json(path)
        if refresh_revision(current) != entry["revision"]:
            raise ValueError("token_changed")
        if payload.get("client_id") != current.get("client_id"):
            raise ValueError("client_mismatch")
        atomic_token(path, payload)

    async def callback(self, request):
        state = request.query.get("state", "")
        if not state.startswith(PREFIX):
            # Preserve existing Metnos OAuth flows; never follow redirects here.
            headers = {k: v for k, v in request.headers.items()
                       if k.lower() in ("cookie", "accept", "user-agent")}
            headers.update({"Host": urlsplit(self.origin).netloc, "X-Forwarded-Proto": "https"})
            try:
                async with ClientSession(timeout=ClientTimeout(total=30), auto_decompress=False) as session:
                    async with session.get(self.upstream + "/oauth/callback", params=request.query,
                                           headers=headers, allow_redirects=False) as response:
                        body = await response.read()
                        out = web.Response(body=body, status=response.status)
                        for key, value in response.headers.items():
                            if key.lower() not in ("content-length", "transfer-encoding", "connection"):
                                out.headers.add(key, value)
                        return out
            except (ClientError, OSError, asyncio.TimeoutError):
                return self.page("UI_GOOGLE_RENEW_FAILED", 502)
        entry = self.current()
        if not entry or entry["status"] != "pending" or not secrets.compare_digest(state, entry["state"]):
            return self.page("UI_GOOGLE_RENEW_EXPIRED", 410)
        # Consume before the first await: replays cannot exchange or overwrite.
        entry["status"] = "exchanging"
        if request.query.get("error") or not request.query.get("code"):
            entry.update(status="failed", error_code="consent_not_completed")
            entry.pop("flow", None)
            entry.pop("auth_url", None)
            return self.page("UI_GOOGLE_RENEW_DENIED", 400)
        try:
            await asyncio.to_thread(self.exchange, entry, request.query["code"])
        except Exception:
            # Provider exceptions can contain credentials/codes: expose no text.
            entry.update(status="failed", error_code="token_exchange_failed")
            return self.page("UI_GOOGLE_RENEW_FAILED", 502)
        finally:
            entry.pop("flow", None)
            entry.pop("auth_url", None)
        entry["status"] = "complete"
        return self.page("UI_GOOGLE_RENEW_COMPLETE")

    def app(self):
        app = web.Application(client_max_size=4096)
        app.add_routes([web.post("/local/start", self.local_start),
                        web.get("/local/status", self.local_status),
                        web.get(BASE + "/authorize/{cap}", self.authorize),
                        web.get("/oauth/callback", self.callback)])
        return app


async def serve(home, sock, msg):
    origin = os.environ["METNOS_OAUTH_PUBLIC_ORIGIN"]
    upstream = os.environ.get("METNOS_OAUTH_UPSTREAM", "http://127.0.0.1:8770")
    broker = Broker(home, origin, upstream, msg)
    sock.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(sock.parent, 0o700)
    if sock.exists():
        if not stat.S_ISSOCK(sock.lstat().st_mode) or sock.lstat().st_uid != os.getuid():
            raise ValueError("unsafe_socket")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
            probe.settimeout(1)
            try:
                probe.connect(str(sock))
            except ConnectionRefusedError:
                pass
            else:
                raise ValueError("broker_already_running")
        sock.unlink()
    runner = web.AppRunner(broker.app(), access_log=None)
    await runner.setup()
    try:
        await web.UnixSite(runner, str(sock)).start()
        os.chmod(sock, 0o600)
        await web.TCPSite(runner, "127.0.0.1", int(os.environ.get("METNOS_OAUTH_BROKER_PORT", "8781"))).start()
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
        sock.unlink(missing_ok=True)


async def local_call(sock, command, renewal_id):
    async with ClientSession(connector=UnixConnector(path=str(sock)), timeout=ClientTimeout(total=10)) as session:
        if command == "start":
            response = await session.post("http://localhost/local/start")
        else:
            response = await session.get("http://localhost/local/status", params={"id": renewal_id})
        async with response:
            return await response.json()


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("serve", "start", "status", "install-messages"))
    parser.add_argument("--id", default="")
    args = parser.parse_args()
    home, sock = paths()
    _, msg = runtime_imports()
    if args.command == "install-messages":
        import i18n
        catalog = json.loads(Path(__file__).with_name("google_oauth_renew_i18n.json").read_text())
        for key, translations in catalog.items():
            i18n.set_catalog_translations(key, translations, source_lang="it")
        print(json.dumps({"ok": True, "keys": len(catalog)}))
    elif args.command == "serve":
        asyncio.run(serve(home, sock, msg))
    else:
        try:
            result = asyncio.run(local_call(sock, args.command, args.id))
        except (ClientError, OSError, asyncio.TimeoutError):
            result = {"ok": False, "error_code": "broker_unavailable"}
        print(json.dumps(result))
        if not result.get("ok"):
            raise SystemExit(1)


if __name__ == "__main__":
    main()
