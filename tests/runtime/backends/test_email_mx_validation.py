"""MX/nullMX pre-flight validation in email_metnos.send (bounce fix 22/5/2026).

Razionale: 4 bounce reali (1-19/5/2026) verso indirizzi mai raggiungibili —
`mario@example.com` (nullMX RFC 7505), `roberto@example.com` (user unknown),
`roberto@example.com` (mailbox inesistente). Senza pre-flight, lo SMTP
locale accetta il messaggio e il bounce arriva ore dopo nella INBOX di
`metnos@metnos.com` come MAILER-DAEMON. §2.8 — no silent failure.

Run: `python3 -m pytest tests/runtime/backends/test_email_mx_validation.py -v`.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")


class _FakeSMTP:
    def __init__(self):
        self.sent: list[dict] = []

    def send_message(self, msg, *, from_addr, to_addrs):
        self.sent.append({"from": from_addr, "to": list(to_addrs),
                          "subject": msg.get("Subject")})

    def quit(self):
        pass


class _FakeCreds(dict):
    def __init__(self):
        super().__init__(user="metnos@metnos.com", password="x",
                         host="smtp.example", port=465)


def _send_with_mocks(messages, *, mx_results=None, a_results=None):
    """Helper: invoca email_metnos.send con mock di open_smtp/_account_creds
    e di _query_mx/_query_a. `mx_results`/`a_results` sono dict
    `domain → (has_mx, is_null)` / `domain → bool`.
    """
    from backends.messages import email_metnos as bem

    mx_results = mx_results or {}
    a_results = a_results or {}

    def fake_mx(domain, **_kw):
        # _query_mx ritorna 3-tupla (has_mx, is_null, mx_ok) dal 4/6/2026
        # (mx_ok=query DNS riuscita, fail-open). Pad le 2-tuple dei test a
        # mx_ok=True (caso query riuscita) senza toccare ogni singolo test.
        v = mx_results.get(domain, (False, False, True))
        return v if len(v) == 3 else (v[0], v[1], True)

    def fake_a(domain, **_kw):
        return a_results.get(domain, False)

    fake_smtp = _FakeSMTP()
    with mock.patch("mail_client.open_smtp", return_value=fake_smtp), \
         mock.patch("mail_client._account_creds", return_value=_FakeCreds()), \
         mock.patch.object(bem, "_query_mx", side_effect=fake_mx), \
         mock.patch.object(bem, "_query_a", side_effect=fake_a):
        res = bem.send({"messages": messages, "account": "metnos_system"})
    return res, fake_smtp


class MXValidationTests(unittest.TestCase):

    def test_valid_domain_accepted(self):
        """Dominio con MX validi → send va a buon fine, no rejected."""
        res, smtp = _send_with_mocks(
            [{"to": "a@gmail.com", "subject": "hi", "body": "ok"}],
            mx_results={"gmail.com": (True, False)},
        )
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["ok_count"], 1)
        self.assertEqual(res["fail_count"], 0)
        self.assertEqual(len(smtp.sent), 1)
        self.assertEqual(smtp.sent[0]["to"], ["a@gmail.com"])
        self.assertNotIn("rejected_recipients", res["results"][0])

    def test_null_mx_rejected(self):
        """nullMX (es. example.com) → recipient rifiutato, send skippato."""
        res, smtp = _send_with_mocks(
            [{"to": "mario@example.com", "subject": "hi", "body": "ok"}],
            mx_results={"example.com": (False, True)},
        )
        self.assertFalse(res["ok"], res)
        self.assertEqual(res["ok_count"], 0)
        self.assertEqual(res["fail_count"], 1)
        self.assertEqual(len(smtp.sent), 0)  # SMTP mai chiamato
        self.assertEqual(res["failed"][0]["error_code"], "ERR_INVALID_RECIPIENT_MX")
        self.assertEqual(res["failed"][0]["to"], "mario@example.com")

    def test_nxdomain_rejected(self):
        """Dominio senza MX né A record → reason='no_dns_record', skip send."""
        res, smtp = _send_with_mocks(
            [{"to": "x@nx.invalid", "subject": "hi", "body": "ok"}],
            mx_results={"nx.invalid": (False, False)},
            a_results={"nx.invalid": False},
        )
        self.assertFalse(res["ok"], res)
        self.assertEqual(res["fail_count"], 1)
        self.assertEqual(len(smtp.sent), 0)
        self.assertEqual(res["failed"][0]["error_code"], "ERR_INVALID_RECIPIENT_MX")
        self.assertIn("no_dns_record", res["failed"][0]["error"])

    def test_implicit_mx_via_a_record(self):
        """No MX ma A record presente → RFC 5321 implicit MX, accept."""
        res, smtp = _send_with_mocks(
            [{"to": "b@legacy.tld", "subject": "hi", "body": "ok"}],
            mx_results={"legacy.tld": (False, False)},
            a_results={"legacy.tld": True},
        )
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["ok_count"], 1)
        self.assertEqual(len(smtp.sent), 1)

    def test_soft_fail_mixed_recipients(self):
        """1 destinatario nullMX + 1 valido nella stessa `to_list`: send procede
        sul valido, nullMX finisce in failed[]. §2.7 visibility:
        results[0]["rejected_recipients"] mostra l'addr rifiutato."""
        res, smtp = _send_with_mocks(
            [{"to": ["good@gmail.com", "bad@example.com"],
              "subject": "hi", "body": "ok"}],
            mx_results={"gmail.com": (True, False),
                        "example.com": (False, True)},
        )
        self.assertTrue(res["ok_count"] >= 1)
        self.assertEqual(res["fail_count"], 1)
        self.assertEqual(len(smtp.sent), 1)
        self.assertEqual(smtp.sent[0]["to"], ["good@gmail.com"])
        self.assertEqual(res["results"][0]["rejected_recipients"],
                         [{"addr": "bad@example.com", "reason": "null_mx"}])

    def test_per_call_cache_single_lookup_per_domain(self):
        """5 destinatari `*@example.com` → 1 sola call a _query_mx (cache per-call)."""
        from backends.messages import email_metnos as bem
        calls = []

        def counting_mx(domain, **_kw):
            calls.append(domain)
            return (True, False, True)

        fake_smtp = _FakeSMTP()
        with mock.patch("mail_client.open_smtp", return_value=fake_smtp), \
             mock.patch("mail_client._account_creds", return_value=_FakeCreds()), \
             mock.patch.object(bem, "_query_mx", side_effect=counting_mx), \
             mock.patch.object(bem, "_query_a", return_value=False):
            bem.send({"messages": [
                {"to": [f"u{i}@example.com" for i in range(5)],
                 "subject": "x", "body": "y"},
            ], "account": "metnos_system"})
        self.assertEqual(calls.count("example.com"), 1)

    def test_parse_addr_with_display_name(self):
        """`Name <a@d.com>` deve estrarre `d.com`."""
        from backends.messages.email_metnos import _parse_addr
        self.assertEqual(_parse_addr("Mario Rossi <m@gmail.com>"), "gmail.com")
        self.assertEqual(_parse_addr("plain@gmail.com"), "gmail.com")
        self.assertIsNone(_parse_addr("garbage"))
        self.assertIsNone(_parse_addr(""))


if __name__ == "__main__":
    unittest.main()
