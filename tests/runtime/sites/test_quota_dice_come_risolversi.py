"""A full quota must name the way out, and that way must work (2026-08-07).

The message used to end with "close one and try again", which is advice the
user cannot act on: sessions have no name they ever saw, and the only remedy
that actually worked was restarting a service. The owner hit it four times in
half an hour.

Two halves, and both have to hold: the message names a phrase, and the phrase
routes to the executor that closes sessions. Pinning only the first would
leave the user with a sentence that does nothing.
"""
from __future__ import annotations

import pytest

from messages import get as _msg

_FRASI = ("chiudi le sessioni web", "close the web sessions")


@pytest.mark.parametrize("code", ["MSG_SITES_RC_QUOTA",
                                  "MSG_SITES_RC_QUOTA_HOSTS"])
@pytest.mark.parametrize("lang", ["it", "en"])
def test_il_messaggio_nomina_la_frase_che_risolve(code, lang) -> None:
    import i18n

    testo = i18n.get(code, lang=lang) or ""
    assert testo and not testo.startswith("<missing:"), f"{code}/{lang}"
    assert any(f in testo for f in _FRASI), (
        f"{code}/{lang}: la quota piena non dice come uscirne")


def test_il_messaggio_reso_non_e_una_chiave_mancante() -> None:
    reso = _msg("MSG_SITES_RC_QUOTA", n=2, quota=2, minutes=15)
    assert not reso.startswith("<missing:")
    assert "2" in reso and "15" in reso


@pytest.mark.parametrize("frase", [
    "chiudi le sessioni web",
    "chiudi le sessioni del browser",
    "chiudi tutte le sessioni aperte sui siti",
])
def test_la_frase_instrada_su_delete_sites(frase) -> None:
    """The other half: a sentence that does nothing is not a way out."""
    from loader import load_catalog
    from prefilter import rank

    classifica = rank(frase, list(load_catalog()))
    assert classifica, frase
    primo = classifica[0]
    nome = getattr(primo[1] if isinstance(primo, tuple) else primo, "name", "")
    assert nome == "delete_sites", (frase, nome)
