"""http_auth — admin key, middleware di auth e classificazione del ruolo.

Tre ruoli: anonymous / user / admin (admin >= user).

- Admin key: `~/.config/metnos/admin.key` (mode 0600), 256-bit hex,
  auto-generata al primo start. Solo il fingerprint sha256 va nei log.
- Device pairing token: lookup in `devices.db` (Bearer = public_key_b64);
  se trovato → ruolo `user`.
- LAN compatibility: disabled by default; when explicitly enabled it creates
  an isolated synthetic principal, never the registered host user.
- Altrove: `anonymous`.

Whitelist anonymous: `/agent/health`, `/agent/register`, `/.well-known/*`.
Path che inizia con `/admin/` richiede ruolo `admin`, altrimenti 403.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import secrets
import time

from aiohttp import web

from http_app_state import ADMIN_KEY as APP_ADMIN_KEY, app_get

import devices
import config as _C  # §7.11
from logging_setup import get_logger

log = get_logger(__name__)

import os as _os
# Admin key path rispetta METNOS_USER_CONFIG per isolamento test/e2e.
ADMIN_KEY_PATH = _C.PATH_USER_CONFIG / "admin.key"

ANON_WHITELIST_PREFIXES = (
    "/agent/health", "/.well-known/",
    "/admin/login",  # form di login deve essere raggiungibile per autenticarsi
    # I form possono essere aperti su un secondo browser tramite una capability
    # HMAC limitata a un solo dialogo. Le route applicano ownership/capability
    # prima di leggere o mutare lo stato; il middleware deve lasciarle arrivare
    # a quel verificatore invece di sostituire il suo 403 con un 401 generico.
    "/agent/dialog/",
    "/agent/photos/",  # auth via signed token nell URL stesso
    "/pair/",          # consumo pair token (ADR 0083 + 11/5/2026 channel='http')
    "/oauth/callback", # callback OAuth Google (state token nell URL)
    "/manifest.webmanifest",  # PWA manifest
    "/sw.js",          # service worker
    "/static/",        # asset PWA (icone, ...)
)

# Path completi (no-prefix match) accessibili ad anonymous: gestiscono
# il proprio redirect a login quando opportuno.
ANON_EXACT_PATHS = ("/", "/agent/register")
ADMIN_PREFIX = "/admin"
ADMIN_COOKIE = "metnos_admin"
ADMIN_COOKIE_TTL_S = 86400 * 7  # 7 giorni
USER_COOKIE = "metnos_user"
USER_COOKIE_TTL_S = 86400 * 90  # 90 giorni (device pairing persistente)


class IdentityStoreUnavailable(RuntimeError):
    """Firma valida, ma il binding/revocation store non e' consultabile."""

def _env_enabled(name: str, default: bool = False) -> bool:
    raw = _os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _parse_lan_nets() -> tuple:
    raw = _os.environ.get(
        "METNOS_TRUSTED_LAN_CIDRS",
        "127.0.0.0/8,::1/128,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16",
    )
    networks = []
    for token in raw.split(","):
        try:
            networks.append(ipaddress.ip_network(token.strip(), strict=False))
        except ValueError:
            log.warning("invalid METNOS_TRUSTED_LAN_CIDRS entry: %r", token)
    return tuple(networks)


LAN_NETS = _parse_lan_nets()

# Proxy fidati: SOLO se il peer TCP reale (`request.remote`) cade in queste
# reti gli header `CF-Connecting-IP` / `X-Forwarded-For` vengono onorati per
# derivare l'IP del client. Altrimenti chiunque potrebbe spoofare
# `X-Forwarded-For: 127.0.0.1` e ottenere il bypass LAN → ruolo `user`.
#
# Default = loopback: il tunnel Cloudflare (`cloudflared`) gira sullo stesso
# host e consegna a 127.0.0.1, quindi il deploy resta funzionante. Override
# (es. reverse-proxy su altro host LAN) via env `METNOS_TRUSTED_PROXIES`
# come lista CIDR separata da virgole (es. "127.0.0.0/8,10.0.0.5/32").
def _parse_trusted_proxies() -> tuple:
    raw = _os.environ.get("METNOS_TRUSTED_PROXIES", "").strip()
    if not raw:
        return (ipaddress.ip_network("127.0.0.0/8"),
                ipaddress.ip_network("::1/128"))
    nets = []
    for tok in raw.split(","):
        tok = tok.strip()
        if not tok:
            continue
        try:
            nets.append(ipaddress.ip_network(tok, strict=False))
        except ValueError:
            log.warning("[http] METNOS_TRUSTED_PROXIES: CIDR invalido ignorato: %r", tok)
    return tuple(nets)


TRUSTED_PROXY_NETS = _parse_trusted_proxies()


def _is_trusted_proxy(remote: str | None) -> bool:
    """True se il peer TCP reale e' un proxy fidato (puo' dettare XFF/CF-IP)."""
    if not remote:
        return False
    try:
        ip = ipaddress.ip_address(remote)
    except ValueError:
        return False
    return any(ip in net for net in TRUSTED_PROXY_NETS)


def get_or_create_admin_key() -> str:
    """Legge la admin key da ADMIN_KEY_PATH; se non esiste la crea (mode 0600)."""
    p = ADMIN_KEY_PATH
    if p.exists():
        _C.ensure_private_file(p)
        return p.read_text(encoding="utf-8").strip()
    key = secrets.token_hex(32)
    _C.write_private_text(p, key)
    fp = hashlib.sha256(key.encode()).hexdigest()[:16]
    log.warning("[http] generated admin key %s (fingerprint sha256:%s)", p, fp)
    return key


def _cookie_secret(admin_key: str) -> bytes:
    """Secret derivato dalla admin key per firmare i cookie di sessione."""
    return hashlib.sha256(("cookie:" + admin_key).encode()).digest()


def issue_admin_cookie(admin_key: str) -> str:
    """Costruisce il valore del cookie: `<exp_ts>.<hmac>` (no payload sensibile)."""
    exp = int(time.time()) + ADMIN_COOKIE_TTL_S
    msg = f"{exp}".encode()
    sig = hmac.new(_cookie_secret(admin_key), msg, hashlib.sha256).hexdigest()[:32]
    return f"{exp}.{sig}"


def verify_admin_cookie(value: str, admin_key: str) -> bool:
    """Cookie valido se la firma matcha e non e' scaduto."""
    try:
        exp_s, sig = value.split(".", 1)
        exp = int(exp_s)
    except (ValueError, AttributeError):
        return False
    if exp < int(time.time()):
        return False
    expected = hmac.new(_cookie_secret(admin_key), exp_s.encode(),
                        hashlib.sha256).hexdigest()[:32]
    return hmac.compare_digest(sig, expected)


def issue_user_cookie(admin_key: str, device_id: str,
                       ttl_s: int = USER_COOKIE_TTL_S) -> str:
    """Cookie pair-based per ruolo `user` su un device specifico.

    Payload: `<exp_ts>.<device_id>.<hmac>`. Il `device_id` e' legato in
    `users.user_channels` (channel='http'). Revoca: rimuovere il binding
    da `users.db` o ruotare admin_key.
    """
    exp = int(time.time()) + ttl_s
    payload = f"{exp}.{device_id}"
    sig = hmac.new(_cookie_secret(admin_key), payload.encode(),
                    hashlib.sha256).hexdigest()[:32]
    return f"{payload}.{sig}"


def _verified_user_cookie_device(value: str, admin_key: str) -> str | None:
    """Verify only the signed cookie envelope; it grants no role by itself."""
    try:
        exp_s, device_id, sig = value.split(".", 2)
        exp = int(exp_s)
    except (ValueError, AttributeError):
        return None
    if exp < int(time.time()):
        return None
    payload = f"{exp_s}.{device_id}"
    expected = hmac.new(_cookie_secret(admin_key), payload.encode(),
                        hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(sig, expected):
        return None
    return device_id


def verify_user_cookie_identity(
        value: str, admin_key: str) -> tuple[str, str] | None:
    """Resolve a valid cookie to one live, non-deleting logical owner."""

    device_id = _verified_user_cookie_device(value, admin_key)
    if not device_id:
        return None
    try:
        import users as _users
        owner = _users.find_user_by_recipient("http", device_id)
        if owner is None:
            return None
    except Exception as exc:
        # Una firma valida prova soltanto che il cookie fu emesso dal server;
        # il binding corrente e' la revocation source of truth. Se lo store non
        # e' consultabile non possiamo distinguere un device attivo da uno
        # revocato: segnala un errore ritentabile, senza promuovere il ruolo.
        log.warning("user cookie binding lookup failed: %s", exc)
        raise IdentityStoreUnavailable(str(exc)) from exc
    return device_id, str(owner["id"])


def verify_user_cookie(value: str, admin_key: str) -> str | None:
    """Compatibility projection returning the device of a live identity."""

    identity = verify_user_cookie_identity(value, admin_key)
    return identity[0] if identity else None


def _is_lan_trusted(remote: str | None) -> bool:
    if not remote:
        return False
    try:
        ip = ipaddress.ip_address(remote)
    except ValueError:
        return False
    return any(ip in net for net in LAN_NETS)


def _device_identity_for_token(token: str) -> tuple[str, str] | None:
    """Resolve one device bearer to its currently live logical owner."""
    try:
        for d in devices.list_devices():
            if hmac.compare_digest(d.public_key_b64, token):
                owner = devices.owner_user(d.owner_user_id)
                if owner is not None:
                    return d.id, str(owner["id"])
    except Exception as e:  # device DB non ancora inizializzato in test isolati
        log.debug("device lookup failed: %s", e)
    return None


def _device_for_token(token: str) -> str | None:
    """Compatibility projection for callers that only need the device ID."""

    identity = _device_identity_for_token(token)
    return identity[0] if identity else None


@web.middleware
async def auth_middleware(request: web.Request, handler):
    """Classifica il ruolo del chiamante e applica la policy /admin/."""
    path = request.path
    role = "anonymous"
    device_id = None
    authenticated_user_id = None
    lan_principal = None

    auth = request.headers.get("Authorization", "")
    token = auth[7:].strip() if auth.startswith("Bearer ") else ""

    admin_key = app_get(request.app, APP_ADMIN_KEY, "")
    if token and admin_key and hmac.compare_digest(token, admin_key):
        role = "admin"
    elif token:
        identity = _device_identity_for_token(token)
        if identity:
            device_id, authenticated_user_id = identity
            role = "user"
    else:
        # Cookie firmato (solo se Bearer assente: Bearer ha priorita').
        cookie_val = request.cookies.get(ADMIN_COOKIE, "")
        if cookie_val and admin_key and verify_admin_cookie(cookie_val, admin_key):
            role = "admin"
        else:
            # Pair cookie per device web (ADR 0083 multi-user + 11/5/2026).
            user_cookie = request.cookies.get(USER_COOKIE, "")
            if user_cookie and admin_key:
                try:
                    identity = verify_user_cookie_identity(
                        user_cookie, admin_key)
                except IdentityStoreUnavailable:
                    return web.json_response(
                        {"error": "identity_store_unavailable",
                         "message": "identity verification temporarily unavailable"},
                        status=503, headers={"Retry-After": "2"},
                    )
                if identity:
                    role = "user"
                    device_id, authenticated_user_id = identity

    if role == "anonymous":
        # LAN bypass solo se il chiamante non ha provato un Bearer fallito.
        # Reverse proxy / Cloudflare tunnel: il vero IP del client arriva
        # nell'header `CF-Connecting-IP` (Cloudflare) o `X-Forwarded-For`
        # (proxy generico). Se Metnos riceve da localhost (tunnel) ma il
        # client originale e' su Internet, NON e' LAN trusted. Senza questa
        # logica, chiunque dietro tunnel HTTPS si vedrebbe ruolo `user`
        # automatico (request.remote == 127.0.0.1).
        effective_remote = request.remote
        # Gli header forwarded sono fidati SOLO se il peer TCP reale e' un
        # proxy fidato (default: loopback = tunnel Cloudflare). Senza questo
        # gate, `X-Forwarded-For: 127.0.0.1` da Internet otterrebbe il bypass
        # LAN → ruolo `user` (spoofing).
        if _is_trusted_proxy(request.remote):
            cf_ip = request.headers.get("CF-Connecting-IP", "").strip()
            if cf_ip:
                effective_remote = cf_ip
            else:
                xff = request.headers.get("X-Forwarded-For", "").strip()
                if xff:
                    # XFF puo' essere lista "client, proxy1, proxy2": usa il primo.
                    effective_remote = xff.split(",")[0].strip()
        if (_env_enabled("METNOS_TRUST_LAN_ANONYMOUS", False)
                and _is_lan_trusted(effective_remote) and not token):
            role = "user"
            material = f"metnos-lan-principal-v1:{effective_remote}".encode()
            digest = (
                hmac.new(_cookie_secret(admin_key), material, hashlib.sha256)
                .hexdigest()[:24]
                if admin_key else hashlib.sha256(material).hexdigest()[:24]
            )
            # This identifier deliberately has no users.db binding.  All data
            # access remains in an empty synthetic scope until the browser is
            # paired or authenticates as admin.
            lan_principal = f"http_lan_{digest}"

    request["role"] = role
    request["device_id"] = device_id
    request["lan_principal"] = lan_principal

    # Admin credentials identify the unique live host principal.  Never defer
    # this mapping to individual handlers and never synthesize literal
    # ``host``: owner leases and deletion safety require the immutable UUID.
    if role == "admin" and not authenticated_user_id:
        try:
            import users as _users
            hosts = _users.list_users(role="host")
        except Exception:
            log.warning("admin host identity lookup failed", exc_info=True)
            return web.json_response(
                {"error": "identity_store_unavailable",
                 "message": "identity verification temporarily unavailable"},
                status=503, headers={"Retry-After": "2"},
            )
        if len(hosts) != 1:
            return web.json_response(
                {"error": "admin_identity_ambiguous",
                 "message": "exactly one live host identity is required"},
                status=503, headers={"Retry-After": "2"},
            )
        authenticated_user_id = str(hosts[0]["id"])
    request["authenticated_user_id"] = authenticated_user_id

    # Whitelist anonymous: la valutazione viene PRIMA del check admin/role
    # (altrimenti `/admin/login` non sarebbe raggiungibile per loggarsi).
    is_whitelisted = (
        path in ANON_EXACT_PATHS
        or any(path == w or path.startswith(w) for w in ANON_WHITELIST_PREFIXES)
    )
    if is_whitelisted:
        return await handler(request)

    if path.startswith(ADMIN_PREFIX) and role != "admin":
        return web.json_response(
            {"error": "forbidden", "message": "admin role required"},
            status=403,
        )

    if role == "anonymous":
        return web.json_response(
            {"error": "unauthorized", "message": "auth required"},
            status=401,
        )

    if authenticated_user_id:
        # Cover preprocessing too (uploads, pending dialogs, session takeover),
        # not only the eventual planner call.  Deletion takes the exclusive
        # side of this cross-process lease and therefore cannot interleave with
        # a request that was authenticated for the old owner.
        try:
            from user_lifecycle import OwnerUnavailable, async_owner_session
            async with async_owner_session(authenticated_user_id):
                return await handler(request)
        except OwnerUnavailable:
            return web.json_response(
                {"error": "user_unavailable", "message": "user unavailable"},
                status=401,
            )
    return await handler(request)
