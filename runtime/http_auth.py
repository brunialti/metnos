"""http_auth — admin key, middleware di auth e classificazione del ruolo.

Tre ruoli: anonymous / user / admin (admin >= user).

- Admin key: `~/.config/metnos/admin.key` (mode 0600), 256-bit hex,
  auto-generata al primo start. Solo il fingerprint sha256 va nei log.
- Device pairing token: lookup in `devices.db` (Bearer = public_key_b64);
  se trovato → ruolo `user`.
- LAN trusted: 127.0.0.1, 192.0.2.0/16, 10.0.0.0/8 → `user` di default.
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

import devices
import config as _C  # §7.11
from logging_setup import get_logger

log = get_logger(__name__)

import os as _os
# Admin key path rispetta METNOS_USER_CONFIG per isolamento test/e2e.
ADMIN_KEY_PATH = _C.PATH_USER_CONFIG / "admin.key"

ANON_WHITELIST_PREFIXES = (
    "/agent/health", "/agent/register", "/.well-known/",
    "/admin/login",  # form di login deve essere raggiungibile per autenticarsi
    "/agent/photos/",  # auth via signed token nell URL stesso
    "/pair/",          # consumo pair token (ADR 0083 + 11/5/2026 channel='http')
    "/oauth/callback", # callback OAuth Google (state token nell URL)
    "/manifest.webmanifest",  # PWA manifest
    "/sw.js",          # service worker
    "/static/",        # asset PWA (icone, ...)
)

# Path completi (no-prefix match) accessibili ad anonymous: gestiscono
# il proprio redirect a login quando opportuno.
ANON_EXACT_PATHS = ("/",)
ADMIN_PREFIX = "/admin"
ADMIN_COOKIE = "metnos_admin"
ADMIN_COOKIE_TTL_S = 86400 * 7  # 7 giorni
USER_COOKIE = "metnos_user"
USER_COOKIE_TTL_S = 86400 * 90  # 90 giorni (device pairing persistente)

LAN_NETS = (
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("192.0.2.0/16"),
    ipaddress.ip_network("10.0.0.0/8"),
)

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
        return p.read_text().strip()
    p.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_hex(32)
    p.write_text(key)
    p.chmod(0o600)
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


def verify_user_cookie(value: str, admin_key: str) -> str | None:
    """Ritorna `device_id` se cookie valido, None altrimenti."""
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
    # Verifica che il device_id sia ancora legato (non revocato).
    try:
        import users as _users
        if not _users.is_device_bound("http", device_id):
            return None
    except Exception:
        # Test env senza users.db: accetta sulla base della firma.
        pass
    return device_id


def _is_lan_trusted(remote: str | None) -> bool:
    if not remote:
        return False
    try:
        ip = ipaddress.ip_address(remote)
    except ValueError:
        return False
    return any(ip in net for net in LAN_NETS)


def _device_for_token(token: str) -> str | None:
    """Ritorna `device_id` se `token` matcha la public_key_b64 di un device pairato."""
    try:
        for d in devices.list_devices():
            if hmac.compare_digest(d.public_key_b64, token):
                return d.id
    except Exception as e:  # device DB non ancora inizializzato in test isolati
        log.debug("device lookup failed: %s", e)
    return None


@web.middleware
async def auth_middleware(request: web.Request, handler):
    """Classifica il ruolo del chiamante e applica la policy /admin/."""
    path = request.path
    role = "anonymous"
    device_id = None

    auth = request.headers.get("Authorization", "")
    token = auth[7:].strip() if auth.startswith("Bearer ") else ""

    admin_key = request.app.get("admin_key", "")
    if token and admin_key and hmac.compare_digest(token, admin_key):
        role = "admin"
    elif token:
        device_id = _device_for_token(token)
        if device_id:
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
                dev = verify_user_cookie(user_cookie, admin_key)
                if dev:
                    role = "user"
                    device_id = dev

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
        if _is_lan_trusted(effective_remote) and not token:
            role = "user"

    request["role"] = role
    request["device_id"] = device_id

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

    return await handler(request)
