#!/usr/bin/env python3
"""get_location — executor di Metnos v1.1 (eccezione singolare per op unica).

Ritorna DOVE SI TROVA l'attore corrente, per catena di sorgenti dalla piu'
precisa alla piu' grossolana:

1. `shared`     posizione condivisa da un canale fidato (Telegram 📎), se
                recente: e' un'osservazione, non una deduzione.
2. `configured` posizione dell'installazione da `~/.config/metnos/location.toml`.
                Per un host fermo e' esatta e non costa rete.
3. `wifi`       trilaterazione dagli access point visibili (beaconDB).
4. `ip`         citta' dedotta dall'indirizzo IP pubblico. Ultima spiaggia.
5. `shared`     la condivisa stantia, se non c'e' altro: meglio vecchia che
                niente, purche' l'eta' sia dichiarata.

Ogni risposta porta `source` e `accuracy_m`, cosi' una citta' dedotta dall'IP
non puo' essere scambiata per una posizione osservata (§2.8). Le sorgenti di
rete vivono in `runtime/host_location.py` e falliscono in silenzio: la catena
prosegue, non si interrompe.

Storage della condivisa: `~/.local/share/metnos/locations.jsonl` (append-only),
scritto dal daemon Telegram via `runtime/location_store.record_location`.

Singolare per design: l'utente ha UNA posizione corrente per actor; la storia
(lista posizioni nel tempo) sara' un futuro `list_locations` se servira'.

Contratto:
    stdin: JSON {actor?: str = "host"}
    stdout: JSON {ok, location: {lat, lon, source, accuracy_m, ...},
                  age_seconds?, actor}
            oppure {ok: false, error: "..."} se NESSUNA sorgente sa rispondere.
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.environ.get("METNOS_RUNTIME") or next(
    str(p / "runtime") for p in Path(__file__).resolve().parents
    if (p / "runtime" / "config.py").is_file()))
from messages import get as _msg  # noqa: E402
from executor_helpers import run_stdio  # noqa: E402
from location_store import get_last_location  # noqa: E402
import host_location  # noqa: E402

# Oltre questa eta' la posizione condivisa descrive dove l'utente ERA, non
# dov'e'. Resta comunque l'ultima risorsa in fondo alla catena.
_FRESH_S = float(os.environ.get("METNOS_LOCATION_FRESH_S", "86400"))


def _presentation(position: dict) -> dict:
    """Frase autorevole che dichiara la PROVENIENZA insieme al dato.

    Senza questa, la risposta erano due coordinate nude: una citta' dedotta
    dall'IP sembrava una posizione osservata (§2.8). L'executor e' l'unico che
    sa da dove viene il numero, quindi e' lui a dettarne la lettura; lo scopo
    `always` dice al runtime che vale per qualunque domanda di posizione.
    """
    lat = f"{position['lat']:.4f}"
    lon = f"{position['lon']:.4f}"
    source = position.get("source")
    label = position.get("label") or ""
    if source == "configured":
        text = _msg("MSG_LOCATION_FROM_CONFIGURED", lat=lat, lon=lon,
                    place=f" ({label})" if label else "")
    elif source == "wifi":
        radius = position.get("accuracy_m")
        text = _msg("MSG_LOCATION_FROM_WIFI", lat=lat, lon=lon,
                    radius=f"{radius:.0f}" if radius else "?")
    elif source == "ip":
        text = _msg("MSG_LOCATION_FROM_IP", lat=lat, lon=lon,
                    place=label or "?")
        if position.get("via_tunnel"):
            text += " " + _msg("MSG_LOCATION_IP_VIA_TUNNEL")
    else:
        text = _msg("MSG_LOCATION_FROM_SHARED", lat=lat, lon=lon)
    return {"scope": "always", "text": text}


def _pipeable(location: dict) -> list[dict]:
    """La stessa posizione in forma consumabile a valle (§2.10, §4.1).

    `get_places` e i suoi simili leggono `entries` con un campo `gps`. Finche'
    get_location emetteva solo lo scalare `location`, il planner provava a
    citarlo con un segnaposto (`{{step1.coords}}`) che non esisteva: il runtime
    lo scartava e il consumatore falliva per argomento mancante — misurato sul
    turno «dimmi la via piu' vicina». Una lista di un solo elemento non
    contraddice la singolarita' dell'operazione: e' il caso degenere N=1.
    """
    return [{
        "gps": {"lat": location["lat"], "lon": location["lon"]},
        "lat": location["lat"],
        "lon": location["lon"],
        "source": location.get("source"),
    }]


def _from_shared(rec: dict, actor: str) -> dict:
    age = int(time.time() - rec["ts"])
    return {
        "ok": True,
        "location": {
            "lat": rec["lat"],
            "lon": rec["lon"],
            "ts": rec["ts"],
            "source": "shared",
            # L'accuratezza dichiarata dal canale, quando c'e'. `accuracy`
            # resta per compatibilita' con i consumatori esistenti.
            "accuracy": rec.get("accuracy"),
            "accuracy_m": rec.get("accuracy"),
            "channel": rec.get("channel"),
        },
        "entries": _pipeable({"lat": rec["lat"], "lon": rec["lon"],
                              "source": "shared"}),
        "age_seconds": age,
        "actor": actor,
        "authoritative_presentation": _presentation(
            {"lat": rec["lat"], "lon": rec["lon"], "source": "shared"}),
    }


def _coords(position: dict) -> str:
    label = position.get("label")
    text = f"{position['lat']:.4f}, {position['lon']:.4f}"
    return f"{text} ({label})" if label else text


def _verify(actor: str) -> dict:
    """Confronto esplicito fra la posizione DICHIARATA e quella RILEVATA ORA.

    La configurata descrive la MACCHINA, non chi la usa: da un portatile in
    viaggio, o dietro una VPN, resterebbe «casa» con tutta l'autorevolezza di
    un dato esatto. Quando l'utente chiede di rideterminare la posizione non
    si sceglie per lui: si mostrano le due e si dice quale e' quale.
    """
    configured = host_location.configured_position()
    detected = (host_location.wifi_position()
                or host_location.ip_position())
    out = {"ok": True, "actor": actor, "verified": True}
    if configured:
        out["location"] = {**configured, "ts": time.time()}
        out["entries"] = _pipeable(configured)
    if detected:
        out["detected"] = detected
        if not configured:
            out["location"] = {**detected, "ts": time.time()}
            out["entries"] = _pipeable(detected)
    if not configured and not detected:
        return {"ok": False, "error_code": "ERR_NO_LOCATION_YET",
                "error_class": "not_found",
                "error": _msg("ERR_NO_LOCATION_YET")}
    if configured and detected:
        text = _msg("MSG_LOCATION_VERIFY_BOTH",
                    configured=_coords(configured), detected=_coords(detected))
    elif configured:
        text = _msg("MSG_LOCATION_VERIFY_NO_LIVE", configured=_coords(configured))
    else:
        text = _presentation(detected)["text"]
    out["authoritative_presentation"] = {"scope": "always", "text": text}
    return out


def invoke(args):
    actor = args.get("actor") or "host"
    if not isinstance(actor, str):
        return {"ok": False, "error_code": "ERR_ARG_NOT_STRING",
                "error_class": "invalid_args",
                "error": _msg("ERR_ARG_NOT_STRING", arg="actor")}

    if args.get("verify"):
        return _verify(actor)

    # La condivisa e' owner-scoped; le sorgenti dell'host no. Senza owner
    # logico si salta il primo anello e si prosegue: la macchina sa comunque
    # dove si trova.
    shared = None
    owner_user_id = str(os.environ.get("METNOS_OWNER_USER_ID") or "").strip()
    if owner_user_id:
        shared = get_last_location(owner_user_id=owner_user_id)
    if shared and (time.time() - shared["ts"]) <= _FRESH_S:
        return _from_shared(shared, actor)

    position = host_location.host_position()
    if position:
        out = {"ok": True, "location": dict(position), "actor": actor,
               "entries": _pipeable(position),
               "authoritative_presentation": _presentation(position)}
        out["location"]["ts"] = time.time()
        return out

    if shared:
        # Stantia ma vera: `age_seconds` dice al consumatore quanto fidarsi.
        return _from_shared(shared, actor)

    return {
        "ok": False,
        "error_code": "ERR_NO_LOCATION_YET",
        "error_class": "not_found",
        "error": _msg("ERR_NO_LOCATION_YET"),
    }


def main():
    run_stdio(invoke)


if __name__ == "__main__":
    main()
