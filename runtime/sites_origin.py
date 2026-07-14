"""Origine credenziale canonica `(scheme, host, port)` — ADR 0191 P2 / §4.

Autorita' di DESTINAZIONE delle credenziali (distinta da `allowed_hosts`, che e'
autorizzazione di RETE). Il fill e' consentito SOLO se l'origine corrente
appartiene, per MATCH ESATTO della tupla normalizzata, a `credential_origins`.
Nessun fold `www` implicito: `www.<root>` e' autorizzato solo come entry esplicita.

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


def derive_default_origins(domain: str | None) -> list[str]:
    """Migrazione legacy (ADR 0191 §4): origini di default per un record senza
    `credential_origins`. Host pubblico DNS -> ``https://D:443`` + controparte
    stretta ``www`` (una sola label, ≥2 label DNS, mai IP/localhost/.local). Host
    locale/privato -> ``http://host:80`` (pannelli LAN tipo FASTGate)."""
    h = _norm_host(domain)
    if h is None:
        return []
    if is_local(h):
        o = normalize_origin("http", h, 80)
        return [o] if o else []
    out: list[str] = []
    base = normalize_origin("https", h, 443)
    if base:
        out.append(base)
    if not is_ip(h):
        labels = h.split(".")
        if len(labels) >= 2:
            counterpart = None
            if h.startswith("www.") and len(h[4:].split(".")) >= 2:
                counterpart = normalize_origin("https", h[4:], 443)
            elif not h.startswith("www."):
                counterpart = normalize_origin("https", "www." + h, 443)
            if counterpart and counterpart not in out:
                out.append(counterpart)
    return out


def authorized_origins(payload: dict | None, storage_domain: str) -> list[str]:
    """Origini autorizzate per il fill.

    ADR 0191 §4 (fix adversarial #3): se la chiave `credential_origins` e'
    PRESENTE nel payload, e' un'autorita' ESPLICITA e vincola il fill — anche se
    vuota o tutta invalida, il risultato e' `[]` = **deny-all fail-closed**, MAI
    l'allargamento alla migrazione. La derivazione di migrazione (apex+www)
    scatta SOLO per i record legacy dove la chiave e' ASSENTE.
    """
    if isinstance(payload, dict) and "credential_origins" in payload:
        stored = payload.get("credential_origins")
        norm: set[str] = set()
        if isinstance(stored, (list, tuple)):
            for entry in stored:
                origin = normalize_entry(str(entry))
                if origin:
                    norm.add(origin)
        return sorted(norm)  # puo' essere [] = nessuna origine (fail-closed)
    return sorted(set(derive_default_origins(storage_domain)))


def authorize(url: str | None, origins, *, extra: str | None = None) -> bool:
    """True se l'origine di `url` e' in `origins` (match esatto). `extra` = origine
    one-shot approvata (IdP delegato, ADR 0188), non persistita."""
    cur = origin_of_url(url)
    if not cur:
        return False
    allowed = set(origins or ())
    if extra:
        e = normalize_entry(extra)
        if e:
            allowed.add(e)
    return cur in allowed
