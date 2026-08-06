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
