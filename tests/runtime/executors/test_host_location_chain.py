"""La catena di sorgenti di `get_location`: ordine, provenienza, onesta'.

Le sorgenti di rete non vengono mai contattate: si sostituiscono al confine.
Un test che dipendesse da beaconDB o da un servizio IP misurerebbe la rete,
non Metnos.
"""
from __future__ import annotations

import importlib
import sys
import time
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT / "executors" / "get_location"))

import host_location  # noqa: E402
import get_location  # noqa: E402


@pytest.fixture(autouse=True)
def _no_network(monkeypatch, tmp_path):
    """Nessuna sorgente dedotta, se un test non la chiede esplicitamente.

    La configurata resta la funzione VERA, puntata a un file inesistente: cosi'
    i test che la riguardano esercitano il codice, non un sostituto.
    """
    monkeypatch.setattr(host_location, "wifi_position", lambda **_: None)
    monkeypatch.setattr(host_location, "ip_position", lambda **_: None)
    monkeypatch.setattr(host_location, "CONFIG_PATH", tmp_path / "assente.toml")
    monkeypatch.delenv("METNOS_OWNER_USER_ID", raising=False)


def _shared(age_s: float) -> dict:
    return {"lat": 45.0, "lon": 9.0, "ts": time.time() - age_s,
            "accuracy": 12.5, "channel": "telegram"}


def test_condivisa_recente_batte_ogni_sorgente_dedotta(monkeypatch):
    monkeypatch.setenv("METNOS_OWNER_USER_ID", "owner")
    monkeypatch.setattr(get_location, "get_last_location",
                        lambda **_: _shared(60))
    monkeypatch.setattr(host_location, "configured_position",
                        lambda *a: {"lat": 1.0, "lon": 1.0,
                                    "source": "configured", "accuracy_m": None})
    out = get_location.invoke({})
    assert out["ok"] is True
    assert out["location"]["source"] == "shared"
    assert out["location"]["lat"] == 45.0
    assert out["age_seconds"] < 120


def test_condivisa_stantia_cede_alla_configurata(monkeypatch):
    monkeypatch.setenv("METNOS_OWNER_USER_ID", "owner")
    monkeypatch.setenv("METNOS_LOCATION_FRESH_S", "3600")
    monkeypatch.setattr(get_location, "get_last_location",
                        lambda **_: _shared(7 * 24 * 3600))
    importlib.reload(get_location)
    monkeypatch.setattr(get_location, "get_last_location",
                        lambda **_: _shared(7 * 24 * 3600))
    monkeypatch.setattr(get_location.host_location, "configured_position",
                        lambda *a: {"lat": 1.5, "lon": 2.5,
                                    "source": "configured", "accuracy_m": 5.0})
    monkeypatch.setattr(get_location.host_location, "wifi_position",
                        lambda **_: None)
    monkeypatch.setattr(get_location.host_location, "ip_position",
                        lambda **_: None)
    out = get_location.invoke({})
    assert out["location"]["source"] == "configured"
    assert out["location"]["lat"] == 1.5


def test_server_never_uses_the_users_shared_location(monkeypatch):
    monkeypatch.setenv("METNOS_OWNER_USER_ID", "owner")
    monkeypatch.setattr(get_location, "get_last_location", lambda **_: pytest.fail(
        "The user's location must not be read for a server request"))
    monkeypatch.setattr(host_location, "configured_position", lambda *a: {
        "lat": 1.5, "lon": 2.5, "source": "configured", "accuracy_m": 5.0})
    out = get_location.invoke({"subject": "server"})
    assert out["ok"] is True
    assert out["location"]["source"] == "configured"
    assert out["location"]["lat"] == 1.5
    monkeypatch.setattr(host_location, "configured_position", lambda *a: None)
    assert get_location.invoke({"subject": "server"})["error_class"] == "not_found"


@pytest.mark.parametrize("subject", [None, "unknown", [], {}])
def test_invalid_subject_is_rejected(subject):
    out = get_location.invoke({"subject": subject})
    assert out["ok"] is False
    assert out["error_code"] == "ERR_ARG_ENUM"


def test_senza_owner_logico_la_macchina_sa_comunque_dove_si_trova(monkeypatch):
    """Il primo anello e' owner-scoped; gli altri no. Prima di questa catena
    l'assenza di owner era un errore di autorizzazione secco."""
    monkeypatch.setattr(host_location, "ip_position",
                        lambda **_: {"lat": 41.9, "lon": 12.5, "source": "ip",
                                     "accuracy_m": None, "label": "Roma"})
    out = get_location.invoke({})
    assert out["ok"] is True
    assert out["location"]["source"] == "ip"


def test_nessuna_sorgente_dichiara_di_non_sapere():
    out = get_location.invoke({})
    assert out["ok"] is False
    assert out["error_class"] == "not_found"


def test_una_citta_dedotta_non_dichiara_un_raggio_inventato(monkeypatch):
    """§2.8: `ip` vale una citta'. Un `accuracy_m` numerico la farebbe
    sembrare una posizione osservata."""
    monkeypatch.setattr(host_location, "ip_position",
                        lambda **_: host_location._position(
                            lat=41.88, lon=12.48, source="ip",
                            accuracy_m=None, label="Rome, Lazio, Italy"))
    out = get_location.invoke({})
    assert out["location"]["accuracy_m"] is None
    assert out["location"]["label"]


def test_coordinate_fuori_scala_dal_servizio_esterno_sono_scartate():
    assert host_location._position(lat=91.0, lon=0.0, source="ip") is None
    assert host_location._position(lat="nord", lon=0.0, source="ip") is None
    assert host_location._position(lat=45.0, lon=9.0, source="ip") is not None


def test_configurata_legge_il_file_dell_installazione(tmp_path):
    path = tmp_path / "location.toml"
    path.write_text('[location]\nlat = 45.4642\nlon = 9.19\n'
                    'label = "casa"\naccuracy_m = 10\n')
    out = host_location.configured_position(path)
    assert out["source"] == "configured"
    assert out["lat"] == 45.4642
    assert out["label"] == "casa"
    assert out["accuracy_m"] == 10.0


def test_configurata_assente_o_illeggibile_non_solleva(tmp_path):
    assert host_location.configured_position(tmp_path / "manca.toml") is None
    rotto = tmp_path / "rotto.toml"
    rotto.write_text("questo non e' TOML [[[")
    assert host_location.configured_position(rotto) is None


def test_gli_access_point_si_leggono_senza_privilegi(monkeypatch):
    """`scan dump` legge la cache del kernel: Metnos non chiede root."""
    scan = (
        "BSS aa:bb:cc:dd:ee:ff(on wlp0)\n"
        "\tsignal: -42.00 dBm\n"
        "BSS 11:22:33:44:55:66(on wlp0)\n"
        "\tsignal: -71.00 dBm\n"
    )

    class _Done:
        stdout = scan

    monkeypatch.setattr(host_location, "_wireless_interfaces", lambda: ["wlp0"])
    monkeypatch.setattr(host_location.subprocess, "run",
                        lambda *a, **k: _Done())
    points = host_location.visible_access_points()
    assert [p["macAddress"] for p in points] == [
        "aa:bb:cc:dd:ee:ff", "11:22:33:44:55:66"]
    assert points[0]["signalStrength"] == -42


def test_senza_access_point_non_si_interroga_beacondb(monkeypatch):
    chiamate = []
    monkeypatch.setattr(host_location, "visible_access_points", lambda: [])
    monkeypatch.setattr(host_location.urllib.request, "urlopen",
                        lambda *a, **k: chiamate.append(a))
    assert host_location.wifi_position() is None
    assert chiamate == []
