"""L'assenza di temperatura dice anche PERCHE' (6/8/2026).

Turno reale `ec998e3e` — «temperatura cpu pc-roberto»: risposta onesta ma
cieca, «sensori di temperatura non disponibili su questo sistema», che non
distingue «questa macchina non puo'» da «manca il backend». Verificato sul PC
quel giorno: LibreHardwareMonitor non installato, nessun namespace WMI, e le
uniche zone ACPI esposte davano 0 K e 283 K (cioe' niente e 10 °C) — quindi
nessuna temperatura CPU attestabile finche' non si installa il backend.

Il confine e' la parte che conta: si nomina il backend SOLO quando il motivo
dichiarato dall'executor e' «nessun sensore supportato» E il dispositivo e'
Windows. Un probe fallito o un PowerShell assente hanno un'altra causa, e
suggerire un'installazione sarebbe una diagnosi inventata (§2.8).
"""
from __future__ import annotations

import pytest

from orchestration import _thermal_absence_message


def _salute(os_name: str = "Windows") -> dict:
    return {"system": {"os": os_name, "os_release": "11", "arch": "AMD64"}}


def test_windows_senza_backend_dice_quale_installare() -> None:
    testo = _thermal_absence_message(
        {"available": False, "source": "none",
         "reason_code": "no_supported_sensor"}, _salute())
    assert "LibreHardwareMonitor" in testo


def test_il_messaggio_dichiara_anche_che_metnos_non_installa() -> None:
    """Nominare il programma senza dire «io non lo installo» lascerebbe
    credere che il sistema possa arrangiarsi: il confine e' parte della
    risposta (decisione di Roberto, 6/8). Verificato in ENTRAMBE le lingue —
    un confine tradotto a meta' e' un confine perso."""
    import i18n

    for lingua, frase in (("it", "non installa"), ("en", "does not install")):
        with i18n.language_context(lingua):
            testo = i18n.get("MSG_HEALTH_THERMAL_NO_BACKEND_WINDOWS")
        assert frase in testo, f"{lingua}: manca il confine di mandato"


@pytest.mark.parametrize("motivo", [
    "thermal_probe_failed", "thermal_probe_timeout",
    "powershell_unavailable", "invalid_backend_output", "",
])
def test_un_altro_motivo_non_diventa_un_consiglio_di_installazione(motivo) -> None:
    testo = _thermal_absence_message(
        {"available": False, "reason_code": motivo}, _salute())
    assert "LibreHardwareMonitor" not in testo


def test_su_linux_il_backend_windows_non_si_nomina() -> None:
    testo = _thermal_absence_message(
        {"available": False, "reason_code": "no_supported_sensor"},
        _salute("Linux"))
    assert "LibreHardwareMonitor" not in testo


def test_salute_senza_sistema_non_esplode() -> None:
    assert _thermal_absence_message({}, {})
    assert _thermal_absence_message(None, None)
