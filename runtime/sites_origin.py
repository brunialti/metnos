"""Origine credenziale canonica `(scheme, host, port)` — ADR 0191 P2 / §4.

Autorita' di DESTINAZIONE delle credenziali (distinta da `allowed_hosts`, che e'
autorizzazione di RETE). Due regimi (`origin_authorized`):
- `credential_origins` PRESENTE nel vault = autorita' ESPLICITA: match ESATTO
  della tupla normalizzata, nessun fold; lista vuota = deny-all fail-closed.
- chiave ASSENTE = contratto default STESSO SITO del domain handle: host uguale
  o sottodominio first-party (dot-anchored: `account.booking.com` per
  `booking.com`), `https` obbligatorio (`http` solo host locali). E' il
  contratto storico dei binding creati per nome-sito; il consenso one-shot
  resta per le origini DELEGATE (altro sito registrabile), a match esatto.

Regole di normalizzazione (deterministiche):
- host IDNA/punycode -> ASCII lowercase; trailing-dot rimosso; IPv6 tra `[...]`.
- porta effettiva resa SEMPRE esplicita (443 https, 80 http).
- forma canonica: ``scheme://host:port``.
- scheme: ``https`` obbligatorio, ``http`` ammesso SOLO per host loopback/privati/
  link-local (`127/8`, `::1`, `localhost`, `10/8`, `172.16/12`, `192.168/16`,
  `169.254/16`, `.local`) — estensione deliberata per pannelli LAN (FASTGate).
"""
from __future__ import annotations

import ipaddress
import urllib.parse


def _strip_brackets(host: str) -> str:
    if host.startswith("[") and host.endswith("]"):
        return host[1:-1]
    return host


def _norm_host(host: str | None) -> str | None:
    """Host in forma canonica ASCII lowercase, o None se invalido."""
    if not host or not isinstance(host, str):
        return None
    h = host.strip().lower().rstrip(".")
    if not h:
        return None
    # IPv6 esplicito fra parentesi
    if h.startswith("[") and h.endswith("]"):
        try:
            return "[" + str(ipaddress.ip_address(h[1:-1])) + "]"
        except ValueError:
            return None
    # IPv6 nudo (piu' di un ':')
    if h.count(":") > 1:
        try:
            return "[" + str(ipaddress.ip_address(h)) + "]"
        except ValueError:
            return None
    # IPv4 canonico
    try:
        return str(ipaddress.ip_address(h))
    except ValueError:
        pass
    # IDNA solo per host non-ASCII (evita il fragile .encode('idna') sugli ASCII)
    if any(ord(c) > 127 for c in h):
        try:
            import idna as _idna
            h = _idna.encode(h).decode("ascii").lower()
        except Exception:
            try:
                h = h.encode("idna").decode("ascii").lower()
            except Exception:
                return None
    return h


def is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(_strip_brackets(host))
        return True
    except ValueError:
        return False


def is_local(host: str) -> bool:
    """True per loopback/privati/link-local/localhost/.local (http ammesso)."""
    h = _strip_brackets((host or "").strip().lower().rstrip("."))
    if not h:
        return False
    if h == "localhost" or h.endswith(".local") or h.endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return False
    return bool(ip.is_loopback or ip.is_private or ip.is_link_local)


def scheme_ok_for(scheme: str, host: str) -> bool:
    s = (scheme or "").lower()
    if s == "https":
        return True
    if s == "http":
        return is_local(host)
    return False


def normalize_origin(scheme: str, host: str | None,
                     port: int | None = None) -> str | None:
    """Forma canonica ``scheme://host:port`` o None se invalida (host errato o
    ``http`` su host pubblico)."""
    s = (scheme or "").lower()
    if s not in ("http", "https"):
        return None
    h = _norm_host(host)
    if h is None:
        return None
    if not scheme_ok_for(s, h):
        return None
    p = int(port) if port else (443 if s == "https" else 80)
    if p <= 0 or p > 65535:
        return None
    return f"{s}://{h}:{p}"


def origin_of_url(url: str | None) -> str | None:
    """Origine canonica di un URL, o None se non http(s)/malformato."""
    if not url or not isinstance(url, str):
        return None
    try:
        sp = urllib.parse.urlsplit(url)
        host = sp.hostname
        port = sp.port  # puo' sollevare ValueError su porta malformata
    except ValueError:
        return None
    return normalize_origin(sp.scheme, host, port)


def normalize_entry(entry: str | None) -> str | None:
    """Normalizza una voce di `credential_origins` (URL o ``scheme://host[:port]``)."""
    return origin_of_url(entry)


def explicit_origins(payload: dict | None) -> list[str] | None:
    """Le origini ESPLICITE del payload, normalizzate — o None se la chiave
    `credential_origins` e' ASSENTE (regime stesso-sito). Chiave presente ma
    vuota/invalida -> `[]` = deny-all fail-closed (fix adversarial #3), MAI
    l'allargamento al default."""
    if not (isinstance(payload, dict) and "credential_origins" in payload):
        return None
    stored = payload.get("credential_origins")
    norm: set[str] = set()
    if isinstance(stored, (list, tuple)):
        for entry in stored:
            origin = normalize_entry(str(entry))
            if origin:
                norm.add(origin)
    return sorted(norm)


def same_site_origin(origin: str | None, domain: str | None) -> bool:
    """True se `origin` (canonica) appartiene allo STESSO SITO del domain
    handle: host uguale o sottodominio first-party dot-anchored, scheme lecito
    per l'host (`https`; `http` solo locali). Handle `www.<root>` ancora al
    root (contratto storico). IP/locali/label-singola = host esatto."""
    if not origin or not domain:
        return False
    try:
        sp = urllib.parse.urlsplit(origin)
    except ValueError:
        return False
    host = _norm_host(sp.hostname)
    d = _norm_host(domain)
    if not host or not d or not scheme_ok_for(sp.scheme, host):
        return False
    if d.startswith("www.") and len(d[4:].split(".")) >= 2:
        d = d[4:]
    if is_ip(d) or is_local(d) or is_ip(host) or "." not in d:
        return host == d
    return host == d or host.endswith("." + d)


def origin_authorized(origin: str | None, payload: dict | None,
                      storage_domain: str, *, extra: str | None = None) -> bool:
    """Autorita' del fill per una origine canonica (vedi docstring modulo).
    `extra` = origine one-shot approvata dall'utente (delega IdP, ADR 0188):
    match esatto, mai persistita."""
    if not origin:
        return False
    if extra and normalize_entry(extra) == origin:
        return True
    explicit = explicit_origins(payload)
    if explicit is not None:
        return origin in explicit
    return same_site_origin(origin, storage_domain)
