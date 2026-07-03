"""test_agent_server_url — bug live 2/7: il link di join generato mentre si
naviga la console attraverso il tunnel Cloudflare pubblico (chat.metnos.com)
puntava alla porta device (8765) sullo STESSO host pubblico, che il tunnel
non instrada (solo 8770 e' in ingress) -> browser bloccato su un URL morto.

`_agent_server_url` ora rileva la richiesta-via-proxy-fidato (stesso segnale
di `http_auth._is_trusted_proxy` usato per il bypass LAN, ADR esistente) e
ripiega su un IP LAN reale del server (`_pick_lan_ip`) invece di riusare
l'Host header pubblico. L'accesso diretto (LAN o localhost, nessun proxy in
mezzo) resta invariato: e' il caso comune e gia' corretto.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import mock_open, patch

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))

import http_routes_admin as R  # noqa: E402


class _FakeRequest:
    """Stub minimale di aiohttp.web.Request — stesso pattern di
    test_http_ssrf_and_proxy.py::_FakeRequest."""

    def __init__(self, remote, headers=None):
        self.remote = remote
        self.headers = headers or {}


class DefaultRouteIfaceTests(unittest.TestCase):

    def test_returns_string_or_none_on_real_proc_route(self):
        # Legge il /proc/net/route reale della macchina che esegue il test:
        # nessuna asserzione sul valore (non portabile), solo che non crasha
        # e ritorna un tipo sensato.
        result = R._default_route_iface()
        self.assertTrue(result is None or isinstance(result, str))

    def test_none_when_proc_route_unreadable(self):
        with patch("builtins.open", side_effect=OSError("no /proc on this OS")):
            self.assertIsNone(R._default_route_iface())

    def test_picks_lowest_metric_among_default_routes(self):
        fake_route = (
            "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\n"
            "wlp195s0\t00000000\t0A01A8C0\t0003\t0\t0\t700\t00000000\n"
            "enp197s0\t00000000\t0A01A8C0\t0003\t0\t0\t0\t00000000\n"
        )
        with patch("builtins.open", mock_open(read_data=fake_route)):
            self.assertEqual(R._default_route_iface(), "enp197s0")


class PickLanIpTests(unittest.TestCase):

    def test_filters_loopback_and_non_lan(self):
        with patch.object(R, "_local_server_ips", return_value={
            "127.0.0.1", "::1", "localhost",
            "fda2:5dd7:982f:c2fa::1",  # ULA IPv6, fuori da LAN_NETS (IPv4-only)
            "192.168.1.33",
        }):
            self.assertEqual(R._pick_lan_ip(), "192.168.1.33")

    def test_none_when_no_lan_interface(self):
        with patch.object(R, "_local_server_ips", return_value={
            "127.0.0.1", "::1", "203.0.113.7",  # IP pubblico, non LAN
        }):
            self.assertIsNone(R._pick_lan_ip())

    def test_deterministic_pick_among_multiple(self):
        with patch.object(R, "_local_server_ips", return_value={
            "192.168.1.50", "10.8.0.1",
        }):
            picked = R._pick_lan_ip()
            self.assertEqual(picked, R._pick_lan_ip())  # stabile fra call
            self.assertIn(picked, {"192.168.1.50", "10.8.0.1"})

    def test_prefers_default_route_interface_over_alphabetical(self):
        # Bug live 3/7: wlp195s0 (.126, WiFi secondaria) ordina PRIMA di
        # enp197s0 (.33, cablata, default route reale) in ordine
        # alfabetico -> senza il tiebreak sulla default route si sceglie
        # l'interfaccia sbagliata.
        import socket as _socket

        class _Addr:
            def __init__(self, address):
                self.family = _socket.AF_INET
                self.address = address

        with patch.object(R, "_local_server_ips", return_value={
            "192.168.1.126", "192.168.1.33",
        }), patch.object(R, "_default_route_iface", return_value="enp197s0"), \
             patch("psutil.net_if_addrs", return_value={
                 "enp197s0": [_Addr("192.168.1.33")],
                 "wlp195s0": [_Addr("192.168.1.126")],
             }):
            self.assertEqual(R._pick_lan_ip(), "192.168.1.33")

    def test_falls_back_to_alphabetical_when_no_default_route(self):
        with patch.object(R, "_local_server_ips", return_value={
            "192.168.1.126", "192.168.1.33",
        }), patch.object(R, "_default_route_iface", return_value=None):
            self.assertEqual(R._pick_lan_ip(), "192.168.1.126")


class AgentServerUrlTests(unittest.TestCase):

    def test_direct_lan_unchanged(self):
        # Peer reale su LAN, nessun proxy in mezzo -> comportamento
        # preesistente invariato: usa l'Host header cosi' com'e'.
        req = _FakeRequest("192.168.1.50", {"Host": "192.168.1.33:8770"})
        self.assertEqual(R._agent_server_url(req), "http://192.168.1.33:8765")

    def test_direct_localhost_unchanged(self):
        req = _FakeRequest("192.168.1.50", {"Host": "localhost:8770"})
        self.assertEqual(R._agent_server_url(req), "http://localhost:8765")

    def test_tunneled_falls_back_to_lan_ip(self):
        # Peer = tunnel locale (proxy fidato di default), Host = dominio
        # pubblico che non instrada la 8765 -> deve ripiegare sulla LAN.
        req = _FakeRequest("127.0.0.1", {"Host": "chat.metnos.com"})
        with patch.object(R, "_pick_lan_ip", return_value="192.168.1.33"):
            self.assertEqual(
                R._agent_server_url(req), "http://192.168.1.33:8765")

    def test_tunneled_without_lan_ip_falls_back_to_host_honestly(self):
        # Nessuna interfaccia LAN rilevabile: meglio il vecchio
        # comportamento (che puo' comunque funzionare in setup atipici)
        # che un errore silenzioso o un crash.
        req = _FakeRequest("127.0.0.1", {"Host": "chat.metnos.com"})
        with patch.object(R, "_pick_lan_ip", return_value=None):
            self.assertEqual(
                R._agent_server_url(req), "http://chat.metnos.com:8765")

    def test_tunneled_respects_agent_port_env(self):
        req = _FakeRequest("127.0.0.1", {"Host": "chat.metnos.com"})
        with patch.object(R, "_pick_lan_ip", return_value="192.168.1.33"), \
             patch.dict("os.environ", {"METNOS_AGENT_PORT": "9999"}):
            self.assertEqual(
                R._agent_server_url(req), "http://192.168.1.33:9999")

    def test_never_leaks_public_host_when_lan_ip_available(self):
        # Guardia esplicita sul bug: il dominio pubblico non deve MAI
        # comparire nell'URL device quando una LAN e' disponibile.
        req = _FakeRequest("127.0.0.1", {"Host": "chat.metnos.com"})
        with patch.object(R, "_pick_lan_ip", return_value="192.168.1.33"):
            self.assertNotIn("chat.metnos.com", R._agent_server_url(req))


if __name__ == "__main__":
    unittest.main()
