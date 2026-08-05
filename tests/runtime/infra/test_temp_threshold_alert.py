"""Test deterministic per `task_temp_threshold_alert` (24/5/2026).

Verifica che il callback scheduler:
- INVIA notifica se thermal supera threshold (con anti-flap)
- NON INVIA notifica se sotto threshold
- NON INVIA notifica se anti-flap window non scaduto
- Multi-component: trigger se ALMENO uno supera

Mock `host_health.collect_thermal` + send backends. §7.9 deterministic.
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest


from scheduler_v2.builtin_callbacks import task_temp_threshold_alert  # noqa: E402


@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    """Isola lo state path per non toccare il live."""
    import config as _C
    monkeypatch.setattr(_C, "PATH_USER_STATE", tmp_path)
    yield tmp_path


@pytest.fixture
def mock_telegram_send():
    """Mock send Telegram: ritorna ok=True senza chiamare il bot."""
    sent = []

    def _fake_send(args: dict) -> dict:
        sent.append(args)
        return {"ok": True, "ok_count": 1, "results": [{"channel": "telegram"}]}

    with patch("backends.messages.telegram_bot.send", side_effect=_fake_send):
        yield sent


# --- Below-threshold: skip silenzioso -------------------------------------

def test_below_threshold_skip(isolated_state, mock_telegram_send):
    """CPU 46, GPU 40, threshold 80 → no send."""
    with patch("host_health.collect_thermal", return_value={
        "available": True, "cpu_c": 46, "gpu_c": 40, "nvme_c": 38,
    }):
        r = task_temp_threshold_alert({
            "threshold_c": 80, "channel": "telegram",
            "chat_id": "12345",
        })
    assert r["ok"] is True
    assert r["skipped"] == "below_threshold"
    assert r["threshold_c"] == 80
    assert mock_telegram_send == []  # NESSUN send


# --- Above-threshold: invia notifica --------------------------------------

def test_above_threshold_sends_notification(isolated_state,
                                              mock_telegram_send):
    """CPU 85, GPU 60, threshold 80 → send con dettagli."""
    with patch("host_health.collect_thermal", return_value={
        "available": True, "cpu_c": 85, "gpu_c": 60, "nvme_c": 42,
    }):
        r = task_temp_threshold_alert({
            "threshold_c": 80, "channel": "telegram",
            "chat_id": "12345",
        })
    assert r["ok"] is True
    assert r.get("over_threshold") == [("CPU", 85.0)]
    assert len(mock_telegram_send) == 1
    msg = mock_telegram_send[0]["messages"][0]
    assert msg["recipient_id"] == "12345"
    assert "CPU 85°C" in msg["body"]
    assert "Soglia: 80" in msg["body"]


# --- Multi-component over: lista tutti ------------------------------------

def test_multi_component_over_threshold(isolated_state, mock_telegram_send):
    """CPU 82, GPU 88, NVMe 75, threshold 80 → 2 componenti sopra."""
    with patch("host_health.collect_thermal", return_value={
        "available": True, "cpu_c": 82, "gpu_c": 88, "nvme_c": 75,
    }):
        r = task_temp_threshold_alert({
            "threshold_c": 80, "channel": "telegram",
            "chat_id": "12345",
        })
    assert r["ok"] is True
    over = r["over_threshold"]
    assert len(over) == 2
    over_names = {c for c, _ in over}
    assert over_names == {"CPU", "GPU"}
    assert len(mock_telegram_send) == 1


# --- Anti-flap: 2 fire in finestra → 1 solo send --------------------------

def test_anti_flap_within_window(isolated_state, mock_telegram_send):
    """2 trigger in 30s, min_pause_s=60 → solo 1 send."""
    thermal_over = {"available": True, "cpu_c": 85, "gpu_c": 70}
    with patch("host_health.collect_thermal", return_value=thermal_over):
        r1 = task_temp_threshold_alert({
            "threshold_c": 80, "channel": "telegram",
            "chat_id": "12345", "min_pause_s": 60,
        })
        # subito dopo (no sleep)
        r2 = task_temp_threshold_alert({
            "threshold_c": 80, "channel": "telegram",
            "chat_id": "12345", "min_pause_s": 60,
        })
    assert r1["ok"] is True
    assert r2.get("skipped") == "anti_flap"
    assert len(mock_telegram_send) == 1  # solo 1 send


# --- No sensors disponibili: skip --------------------------------------

def test_no_sensors_skip(isolated_state, mock_telegram_send):
    with patch("host_health.collect_thermal", return_value={"available": False}):
        r = task_temp_threshold_alert({
            "threshold_c": 80, "channel": "telegram",
            "chat_id": "12345",
        })
    assert r["ok"] is True
    assert r["skipped"] == "no_thermal_sensors"
    assert mock_telegram_send == []


# --- Args validation -----------------------------------------------------

def test_missing_chat_id_for_telegram(isolated_state):
    r = task_temp_threshold_alert({"channel": "telegram"})
    assert r["ok"] is False
    assert "chat_id" in r["error"]


def test_missing_to_for_email(isolated_state):
    r = task_temp_threshold_alert({"channel": "email"})
    assert r["ok"] is False
    assert "to" in r["error"]


# --- Boundary: esattamente threshold ---------------------------------

def test_exactly_at_threshold_triggers(isolated_state, mock_telegram_send):
    """CPU 80.0 == 80 threshold → trigger (>= operator)."""
    with patch("host_health.collect_thermal", return_value={
        "available": True, "cpu_c": 80.0,
    }):
        r = task_temp_threshold_alert({
            "threshold_c": 80, "channel": "telegram",
            "chat_id": "12345",
        })
    assert r["ok"] is True
    assert r.get("over_threshold") == [("CPU", 80.0)]
    assert len(mock_telegram_send) == 1
