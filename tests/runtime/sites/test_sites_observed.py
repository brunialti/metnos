"""P4 (ADR 0191 §6.1) — precedenza deterministica dei codici osservativi."""
from __future__ import annotations

import sys
from pathlib import Path


import sites_observed as so


def test_precedence_status_before_content():
    # 429 batte tutto (anche challenge/empty presenti)
    assert so.observational_reason(status=429, challenge=True,
                                   body_len=0, control_count=0) == "rate_limited"
    # Retry-After senza 429 esplicito
    assert so.observational_reason(status=200, retry_after=True) == "rate_limited"
    # 403
    assert so.observational_reason(status=403, challenge=True) == "http_forbidden"
    # 5xx / net error
    assert so.observational_reason(status=503) == "page_unavailable"
    assert so.observational_reason(net_error=True) == "page_unavailable"
    # challenge batte empty
    assert so.observational_reason(status=200, challenge=True,
                                   body_len=0, control_count=0) == "challenge_observed"
    # empty surface (soglia)
    assert so.observational_reason(status=200, body_len=10,
                                   control_count=0) == "empty_surface"


def test_usable_page_returns_none():
    # 200 con contenuto -> nessun codice
    assert so.observational_reason(status=200, body_len=500,
                                   control_count=5) is None
    # body corto ma con controlli -> usabile (non empty)
    assert so.observational_reason(status=200, body_len=10,
                                   control_count=3) is None
    # 501/505 NON sono page_unavailable
    assert so.observational_reason(status=501) is None


def test_none_response_not_unavailable_without_neterror():
    # same-doc/hash-route: goto ritorna None senza errore -> pagina usabile
    assert so.observational_reason(status=None) is None
    # ma con net_error -> unavailable
    assert so.observational_reason(status=None, net_error=True) == "page_unavailable"


def test_response_signals_defensive():
    assert so.response_signals(None) == {"status": None, "retry_after": False}

    class _Resp:
        status = 429
        headers = {"Retry-After": "30", "X": "y"}
    sig = so.response_signals(_Resp())
    assert sig["status"] == 429 and sig["retry_after"] is True

    class _Bad:
        @property
        def status(self):
            raise RuntimeError("boom")
        headers = {}
    assert so.response_signals(_Bad())["status"] is None


def test_reason_msg_mapping_complete():
    # ogni slug ha una chiave i18n dedicata (nome inglese stabile)
    for slug in ("rate_limited", "http_forbidden", "page_unavailable",
                 "challenge_observed", "empty_surface"):
        assert so.REASON_MSG[slug].startswith("MSG_SITES_RC_")
