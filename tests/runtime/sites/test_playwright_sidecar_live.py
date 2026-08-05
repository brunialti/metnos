"""Test live del sidecar Playwright (ADR 0125, Phase 1).

Gated da env `METNOS_PLAYWRIGHT_LIVE=1`. Skip altrimenti, cosi' il sidecar
non e' un prerequisito della suite regression.

Quando attivato, presume che il sidecar sia gia' UP su 127.0.0.1:8771
(es. via `systemctl --user start metnos-playwright.service` oppure
`python -m playwright_sidecar.server`).

Sito target: httpbin.org/html (pagina statica con titolo + body parsabile).
NB: serve rete uscente.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")
sys.path.insert(0, str(_RUNTIME.parent / "executors" / "read_urls_html"))


@unittest.skipUnless(
    os.environ.get("METNOS_PLAYWRIGHT_LIVE") == "1",
    "METNOS_PLAYWRIGHT_LIVE not set; live sidecar test skipped",
)
class TestPlaywrightLive(unittest.TestCase):
    """Smoke test sul sidecar reale (non mock)."""

    def test_sidecar_is_up(self):
        from playwright_sidecar import client
        self.assertTrue(client.is_up(timeout_s=2.0),
                        "sidecar non risponde su 127.0.0.1:8771")

    def test_render_httpbin_html(self):
        from playwright_sidecar import client
        r = client.render("https://httpbin.org/html", timeout_s=30.0)
        self.assertTrue(r.get("ok"), r)
        # httpbin /html include <h1>Herman Melville - Moby-Dick</h1>
        self.assertIn("Melville", r.get("body_text", ""), r)
        self.assertIn("Moby", r.get("body_html", ""), r)

    def test_render_invalid_host_returns_network_error(self):
        from playwright_sidecar import client
        r = client.render("https://this.host.never.exists.example/x",
                          timeout_s=15.0)
        self.assertFalse(r.get("ok"))
        self.assertIn(r.get("error_class"), ("network", "timeout", "unknown"))


if __name__ == "__main__":
    unittest.main()
