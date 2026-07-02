"""runtime.placement — dove gira un executor (ADR 0034, §10 del design doc).

Funzione PURA `choose_placement(manifest_placement, intent, devices, now)`,
gemella di routing_pool: testabile senza device reali. Tre livelli valutati
in ordine, il primo che decide vince. KIS: tabella lineare, niente ML.

L1 affinita' assoluta (deterministico, mai scavalcato):
  1b `scope: server|device` vincolante;
  1c override esplicito utente (intent nomina un device per nome);
  1d gate disponibilita' (heartbeat < 60s).
L2 classificazione workload: manifest `class` — per l'MVP ogni classe
  defaulta a `.33` (net/cpu/mixed/llm_local: il server e' lo Strix Halo;
  io_fs senza device nominato = filesystem del server).
L3 tiebreaker: default `.33` ("server").

Un executor senza [placement] = scope "any": gira su .33 come oggi.
"""
from __future__ import annotations

from datetime import datetime, timezone

HEARTBEAT_FRESH_S = 60  # gate L1.d (§10)

SERVER = "server"


class PlacementError(Exception):
    """Placement impossibile: errore ONESTO §2.8 (mai fallback silenzioso).

    `code` = chiave i18n (registrate in remote_exec); `fmt` = kwargs del
    template, cosi' il chiamante rende il messaggio nella lingua istanza.
    """

    def __init__(self, message: str, *, code: str = "ERR_DEVICE_UNREACHABLE",
                 fmt: dict | None = None):
        super().__init__(message)
        self.code = code
        self.fmt = fmt or {}


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def is_available(device, now: datetime | None = None) -> bool:
    """Gate L1.d: heartbeat fresco (< HEARTBEAT_FRESH_S) e non revocato."""
    if getattr(device, "revoked_at", None) is not None:
        return False
    hb = _parse_iso(getattr(device, "last_heartbeat", None))
    if hb is None:
        return False
    now = now or datetime.now(timezone.utc)
    return (now - hb).total_seconds() < HEARTBEAT_FRESH_S


def _match_device_by_name(name: str, devices: list) -> object | None:
    wanted = name.strip().lower()
    for d in devices:
        if (d.name or "").strip().lower() == wanted:
            return d
    return None


def choose_placement(manifest_placement: dict | None,
                     intent: dict | None,
                     devices: list,
                     *,
                     now: datetime | None = None) -> str:
    """Ritorna un device_id oppure `placement.SERVER`.

    - manifest_placement: la tabella `[placement]` del manifest
      (`{scope, targets, class}`) o None/{} = scope "any".
    - intent: dict con eventuale override utente (`device` = nome scelto
      al pairing). None = nessun override.
    - devices: lista `devices.Device` correnti (anche non disponibili:
      il gate L1.d decide QUI, per dare errori onesti e testabilita').

    Solleva PlacementError quando la richiesta VINCOLA a un device che non
    esiste o non e' raggiungibile (§12: attesa o errore onesto, mai
    silenzioso fallback sul server).
    """
    p = manifest_placement or {}
    scope = (p.get("scope") or "any").strip().lower()
    now = now or datetime.now(timezone.utc)

    # L1.c — override esplicito utente: vince su tutto tranne scope=server.
    wanted_name = None
    if isinstance(intent, dict):
        wanted_name = intent.get("device") or intent.get("device_name")
    if wanted_name and scope != SERVER:
        dev = _match_device_by_name(str(wanted_name), devices)
        if dev is None:
            raise PlacementError(
                f"dispositivo '{wanted_name}' non appaiato",
                code="ERR_DEVICE_UNKNOWN", fmt={"name": str(wanted_name)})
        if not is_available(dev, now):
            raise PlacementError(
                f"dispositivo '{wanted_name}' non raggiungibile",
                code="ERR_DEVICE_UNREACHABLE", fmt={"name": str(wanted_name)})
        return dev.id

    # L1.b — scope vincolante.
    if scope == SERVER or scope == "any":
        # L2/L3: per l'MVP ogni classe workload defaulta al server (.33).
        return SERVER
    if scope == "device":
        available = [d for d in devices if is_available(d, now)]
        if not available:
            raise PlacementError(
                "nessun dispositivo raggiungibile per un executor device-only",
                code="ERR_DEVICE_NONE_AVAILABLE")
        if len(available) == 1:
            return available[0].id
        # Piu' device disponibili e nessun nome nell'intent: ambiguita'
        # da risolvere con l'utente (§2.11), non a caso.
        raise PlacementError(
            "piu' dispositivi disponibili: serve il nome del device",
            code="ERR_DEVICE_AMBIGUOUS")

    # Scope sconosciuto nel manifest: comportati come "any" (compat).
    return SERVER
