"""Missing temperature uses one implementation-neutral i18n message."""
from __future__ import annotations

import pytest

from orchestration import _thermal_absence_message


def _salute(os_name: str = "Windows") -> dict:
    return {"system": {"os": os_name, "os_release": "11", "arch": "AMD64"}}


def test_windows_without_sensor_names_the_provider_contract_not_a_product() -> None:
    testo = _thermal_absence_message(
        {"available": False, "source": "none",
         "reason_code": "no_supported_sensor"}, _salute())
    assert "provider" in testo.casefold()
    assert "LibreHardwareMonitor" not in testo


def test_provider_message_is_bilingual_and_does_not_delegate_startup() -> None:
    import i18n

    for lingua, frase in (("it", "provider"), ("en", "provider")):
        with i18n.language_context(lingua):
            testo = i18n.get("MSG_HEALTH_THERMAL_NO_BACKEND_WINDOWS")
        assert frase in testo
        assert "Avviarlo resta una cosa tua" not in testo
        assert "Starting it stays up to you" not in testo


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
