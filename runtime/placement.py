"""runtime.placement — dove gira un executor (ADR 0034, §10 del design doc).

Funzione PURA `choose_placement(manifest_placement, intent, devices, now)`,
gemella di routing_pool: testabile senza device reali. Tre livelli valutati
in ordine, il primo che decide vince. KIS: tabella lineare, niente ML.

L1 affinita' assoluta (deterministico, mai scavalcato):
  1b `scope: server|device` vincolante;
  1c override esplicito utente (intent nomina un device per nome);
  1d gate disponibilita' (heartbeat e, quando tracciato, poll < 60s).
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
    """Gate L1.d: processo vivo E worker pronto, non revocato.

    I record reali recenti espongono ``last_poll``: deve essere fresco quanto
    il heartbeat. Gli oggetti legacy/test che non hanno ancora l'attributo
    conservano la semantica heartbeat-only finche' non passano da un poll.
    """
    if getattr(device, "revoked_at", None) is not None:
        return False
    hb = _parse_iso(getattr(device, "last_heartbeat", None))
    if hb is None:
        return False
    now = now or datetime.now(timezone.utc)
    if (now - hb).total_seconds() >= HEARTBEAT_FRESH_S:
        return False
    if hasattr(device, "last_poll"):
        poll = _parse_iso(getattr(device, "last_poll", None))
        if poll is None or (now - poll).total_seconds() >= HEARTBEAT_FRESH_S:
            return False
    return True


def _match_device_by_name(name: str, devices: list) -> object | None:
    wanted = name.strip().lower()
    for d in devices:
        if (d.name or "").strip().lower() == wanted:
            return d
    return None


def _platform_of(device) -> str:
    """os_family del device, normalizzato; assente = 'linux' (§16.1: stesso
    default onesto del manifest, un device che non ha mai fatto un heartbeat
    con os_family valorizzato non va trattato come jolly universale)."""
    return (getattr(device, "os_family", None) or "").strip().lower() or "linux"


def _check_platform(device, platforms: list[str] | None, executor_name: str) -> None:
    """Gate piattaforma (W3.0/W3.2, §16.1/§16.3): l'executor deve dichiarare
    l'OS del device compatibile. Errore onesto QUI, non un crash a meta'
    esecuzione remota (ModuleNotFoundError, comando POSIX assente, ...)
    scoperto solo dopo aver spedito l'invocazione (§2.8)."""
    plats = platforms or ["linux"]
    fam = _platform_of(device)
    if fam not in plats:
        raise PlacementError(
            f"executor '{executor_name}' non supporta il dispositivo "
            f"'{device.name}' (os={fam})",
            code="ERR_DEVICE_PLATFORM_UNSUPPORTED",
            fmt={"executor": executor_name, "name": device.name, "os": fam})


def choose_placement(manifest_placement: dict | None,
                     intent: dict | None,
                     devices: list,
                     *,
                     now: datetime | None = None,
                     platforms: list[str] | None = None,
                     executor_name: str = "") -> str:
    """Ritorna un device_id oppure `placement.SERVER`.

    - manifest_placement: la tabella `[placement]` del manifest
      (`{scope, targets, class}`) o None/{} = scope "any".
    - intent: dict con eventuale override utente (`device` = nome scelto
      al pairing). None = nessun override.
    - devices: lista `devices.Device` correnti (anche non disponibili:
      il gate L1.d decide QUI, per dare errori onesti e testabilita').
    - platforms: `Executor.platforms` (default `["linux"]` se None, stesso
      default onesto del loader). Un device il cui `os_family` non e' in
      questa lista viene escluso PRIMA della selezione — mai spedito a
      un'invocazione che non puo' eseguire (W3.0/W3.2, §16.1/§16.3).
    - executor_name: solo per il messaggio d'errore (§2.8, diagnosi chiara).

    Solleva PlacementError quando la richiesta VINCOLA a un device che non
    esiste, non e' raggiungibile, o non supporta l'OS richiesto (§12: attesa
    o errore onesto, mai silenzioso fallback sul server).
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
        # Un nome esplicito NON scavalca l'incompatibilita' di piattaforma:
        # l'utente ha scelto il device, non il crash che ne conseguirebbe.
        _check_platform(dev, platforms, executor_name)
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
        plats = platforms or ["linux"]
        compatible = [d for d in available if _platform_of(d) in plats]
        if not compatible:
            # Almeno un device raggiungibile, ma nessuno supporta l'OS
            # richiesto: distinto da "nessun device" (§2.8, diagnosi utile).
            _check_platform(available[0], platforms, executor_name)  # raises
        if len(compatible) == 1:
            return compatible[0].id
        # Piu' device compatibili disponibili e nessun nome nell'intent:
        # ambiguita' da risolvere con l'utente (§2.11), non a caso.
        raise PlacementError(
            "piu' dispositivi disponibili: serve il nome del device",
            code="ERR_DEVICE_AMBIGUOUS")

    # Scope sconosciuto nel manifest: comportati come "any" (compat).
    return SERVER
