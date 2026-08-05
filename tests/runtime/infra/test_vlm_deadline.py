"""The VLM lazy-start path must consume, not renew, a caller deadline."""
from __future__ import annotations

import subprocess
import urllib.error


def test_lazy_start_uses_remaining_shared_budget(monkeypatch, tmp_path):
    import virt

    clock = [0.0]
    subprocess_timeouts = []
    health_timeouts = []
    helper = tmp_path / "vlm-server"
    helper.write_text("placeholder", encoding="utf-8")

    monkeypatch.setenv("METNOS_VLM_SERVER_SH", str(helper))
    monkeypatch.setattr(
        virt, "get_vlm",
        lambda _role="default": {"base_url": "http://127.0.0.1:8081"},
    )
    virt._vlm_started.clear()

    def urlopen(_request, *, timeout):
        health_timeouts.append(timeout)
        clock[0] += timeout
        raise urllib.error.URLError("offline")

    def run(_argv, *, timeout, **_kwargs):
        subprocess_timeouts.append(timeout)
        clock[0] += timeout
        return subprocess.CompletedProcess(_argv, 0, "", "")

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    monkeypatch.setattr("subprocess.run", run)
    monkeypatch.setattr("time.monotonic", lambda: clock[0])
    monkeypatch.setattr("time.sleep", lambda seconds: clock.__setitem__(
        0, clock[0] + seconds))

    assert virt.ensure_vlm_up(deadline_at=5.0, wait_s=35) is False
    assert health_timeouts == [2.0]
    assert subprocess_timeouts == [3.0]
    assert clock[0] == 5.0
