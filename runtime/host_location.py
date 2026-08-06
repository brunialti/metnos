# SPDX-License-Identifier: AGPL-3.0-only
"""Sorgenti di posizione dell'HOST, dalla piu' precisa alla piu' grossolana.

`location_store` conserva la posizione che l'utente CONDIVIDE da un canale
fidato. Quando quella manca — o e' vecchia — Metnos non sapeva piu' nulla:
questo modulo aggiunge le sorgenti che la macchina puo' ricavare da se'.

Tre sorgenti, in ordine di precisione decrescente:

1. `configured` — la posizione dell'installazione, scritta in
   `~/.config/metnos/location.toml`. Per un host fermo e' ESATTA, non costa
   rete e non lascia uscire nulla. E' la risposta giusta per un server di
   casa; e' quella sbagliata per un portatile che viaggia.
2. `wifi` — trilaterazione dagli access point visibili tramite beaconDB
   (database di pubblico dominio, API compatibile Ichnaea/MLS, senza chiave).
   Tipicamente decine di metri quando gli AP sono nel database. Mozilla
   Location Service, il predecessore, e' stato spento nel 2024.
3. `ip` — geolocalizzazione dall'indirizzo IP pubblico. E' a livello di
   CITTA': gli studi comparativi 2026 danno il 15-35% dei casi entro 10 km.
   Spesso restituisce il centroide del comune, non una posizione.

Ogni sorgente dichiara `source` e `accuracy_m`, cosi' il consumatore non puo'
scambiare una citta' dedotta per una posizione osservata (§2.8). Nessuna
solleva: un guasto o un tempo scaduto ritorna `None` e la catena prosegue.
"""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

import config as _C
from logging_setup import get_logger

log = get_logger(__name__)

CONFIG_PATH = _C.PATH_USER_CONFIG / "location.toml"

# beaconDB chiede esplicitamente uno User-Agent che identifichi il client.
_USER_AGENT = "Metnos/1.1 (+https://metnos.com)"
_BEACONDB_URL = os.environ.get(
    "METNOS_WIFI_GEOLOCATE_URL", "https://api.beacondb.net/v1/geolocate")
_IP_GEOLOCATE_URL = os.environ.get(
    "METNOS_IP_GEOLOCATE_URL", "https://ipapi.co/json/")
_NET_TIMEOUT_S = float(os.environ.get("METNOS_LOCATION_TIMEOUT_S", "8"))

# `iw scan` completo richiede privilegi; `scan dump` legge la cache del
# kernel e basta a se stesso. Metnos non chiede root per sapere dove si trova.
_SCAN_TIMEOUT_S = float(os.environ.get("METNOS_WIFI_SCAN_TIMEOUT_S", "10"))

_BSS_RE = re.compile(r"^BSS ([0-9a-f:]{17})", re.MULTILINE)
_SIGNAL_RE = re.compile(r"^\s+signal: (-?\d+(?:\.\d+)?) dBm", re.MULTILINE)


def _coord(value) -> float | None:
    """Coordinata valida, o None. Un servizio esterno puo' mandare qualsiasi
    cosa: la validazione sta al confine, non nel consumatore."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if -180.0 <= number <= 180.0 else None


def _position(*, lat, lon, source: str, accuracy_m=None, label: str = "",
              **extra) -> dict | None:
    latitude, longitude = _coord(lat), _coord(lon)
    if latitude is None or longitude is None or not (-90.0 <= latitude <= 90.0):
        return None
    out = {"lat": latitude, "lon": longitude, "source": source,
           "accuracy_m": _coord(accuracy_m)}
    if label:
        out["label"] = label
    out.update(extra)
    return out


# --- 1. posizione configurata ---------------------------------------------

def configured_position(config_path: Path | None = None) -> dict | None:
    """Posizione dell'installazione da `location.toml`, o None se assente."""
    path = Path(config_path) if config_path is not None else CONFIG_PATH
    if not path.exists():
        return None
    try:
        import tomllib
        with open(path, "rb") as handle:
            raw = tomllib.load(handle)
    except Exception as exc:  # noqa: BLE001 — config illeggibile: si prosegue
        log.warning("location.toml illeggibile: %r", exc)
        return None
    section = raw.get("location") if isinstance(raw.get("location"), dict) else raw
    if not isinstance(section, dict):
        return None
    return _position(
        lat=section.get("lat"), lon=section.get("lon"), source="configured",
        # Una posizione dichiarata dall'operatore vale quanto la sua cura: il
        # raggio e' opzionale e non viene inventato.
        accuracy_m=section.get("accuracy_m"),
        label=str(section.get("label") or ""))


# --- 2. posizione da WiFi --------------------------------------------------

def _wireless_interfaces() -> list[str]:
    try:
        return sorted(p.parent.name
                      for p in Path("/sys/class/net").glob("*/wireless"))
    except OSError:
        return []


def visible_access_points(interface: str = "") -> list[dict]:
    """Access point dalla cache di scansione del kernel (nessun privilegio).

    `iw dev <if> scan dump` non innesca una scansione nuova: restituisce cio'
    che il kernel ha gia' visto. Basta a posizionarsi e non richiede root.
    """
    interfaces = [interface] if interface else _wireless_interfaces()
    points: list[dict] = []
    for name in interfaces:
        try:
            done = subprocess.run(
                ["iw", "dev", name, "scan", "dump"],
                capture_output=True, text=True, timeout=_SCAN_TIMEOUT_S,
                check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            log.debug("scan dump su %s non riuscito: %r", name, exc)
            continue
        blocks = done.stdout.split("\nBSS ")
        for index, block in enumerate(blocks):
            body = block if index == 0 else "BSS " + block
            mac = _BSS_RE.search(body)
            if not mac:
                continue
            signal = _SIGNAL_RE.search(body)
            entry = {"macAddress": mac.group(1)}
            if signal:
                entry["signalStrength"] = int(float(signal.group(1)))
            points.append(entry)
    return points


def wifi_position(*, timeout_s: float | None = None) -> dict | None:
    """Trilaterazione dagli AP visibili via beaconDB. None se non risolvibile."""
    points = visible_access_points()
    if not points:
        return None
    payload = json.dumps({"considerIp": False,
                          "wifiAccessPoints": points}).encode("utf-8")
    request = urllib.request.Request(
        _BEACONDB_URL, data=payload, method="POST",
        headers={"Content-Type": "application/json",
                 "User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(
                request, timeout=timeout_s or _NET_TIMEOUT_S) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, socket.timeout,
            ValueError, OSError) as exc:
        log.debug("beaconDB non ha risposto: %r", exc)
        return None
    location = body.get("location") if isinstance(body, dict) else None
    if not isinstance(location, dict):
        return None
    return _position(
        lat=location.get("lat"), lon=location.get("lng"), source="wifi",
        accuracy_m=body.get("accuracy"),
        access_points=len(points))


# --- 3. posizione da indirizzo IP -----------------------------------------

def _default_route_is_tunnel() -> bool:
    """Vero se la rotta predefinita esce da un tunnel (VPN).

    Non e' un dettaglio tecnico: con una VPN attiva la geolocalizzazione da IP
    restituisce l'uscita del tunnel, non dove sei. Dichiararlo e' §2.8.
    """
    try:
        done = subprocess.run(["ip", "route", "get", "1.1.1.1"],
                              capture_output=True, text=True, timeout=3,
                              check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    match = re.search(r"\bdev\s+(\S+)", done.stdout)
    if not match:
        return False
    device = match.group(1)
    return device.startswith(("wg", "tun", "tap", "ppp", "proton", "nord"))


def ip_position(*, timeout_s: float | None = None) -> dict | None:
    """Posizione a livello di CITTA' dall'IP pubblico. None se non risolvibile."""
    request = urllib.request.Request(
        _IP_GEOLOCATE_URL, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(
                request, timeout=timeout_s or _NET_TIMEOUT_S) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, socket.timeout,
            ValueError, OSError) as exc:
        log.debug("geolocalizzazione IP non riuscita: %r", exc)
        return None
    if not isinstance(body, dict) or body.get("error"):
        return None
    label = ", ".join(
        part for part in (body.get("city"), body.get("region"),
                          body.get("country_name"))
        if isinstance(part, str) and part)
    return _position(
        lat=body.get("latitude"), lon=body.get("longitude"), source="ip",
        # Nessun raggio dichiarato: una citta' non e' una posizione, e
        # inventare un numero la farebbe sembrare tale.
        accuracy_m=None, label=label,
        via_tunnel=_default_route_is_tunnel())


# --- catena ----------------------------------------------------------------

# Ordine fisso, dalla piu' precisa alla piu' grossolana. Le sorgenti di rete
# sono opzionali: un'installazione puo' spegnerle e restare locale.
def host_position(*, allow_network: bool = True) -> dict | None:
    """Prima sorgente disponibile fra configurata, WiFi e IP."""
    position = configured_position()
    if position:
        return position
    if not allow_network:
        return None
    if os.environ.get("METNOS_LOCATION_WIFI", "1") != "0":
        position = wifi_position()
        if position:
            return position
    if os.environ.get("METNOS_LOCATION_IP", "1") != "0":
        return ip_position()
    return None
