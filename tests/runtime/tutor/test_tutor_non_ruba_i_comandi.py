"""Un comando non e' una domanda: il Tutor non se lo prende (7/8/2026).

Bug live: `/help` in chat rispondeva «Non ho ancora una guida consolidata per
questa domanda» — il Tutor. Da quando gira PRIMA del runtime, tutti i comandi
barra erano spariti: il dispatcher deterministico che li serve
(`admin_chat_commands`) sta dentro `run_turn`, e il turno li' non ci arrivava
piu'.

Il confine non e' una grammatica nuova: e' lo stesso matcher che il dispatcher
usa per dire «questo e' mio». Cosi' non c'e' modo di scambiare un percorso
assoluto per un comando, ne' di dimenticare un comando aggiunto domani.
"""
from __future__ import annotations

import pytest

import tutor_boundary


class _Principale:
    audience = "user"
    user_id = "u1"
    lang = "it"


@pytest.mark.parametrize("comando", [
    "/help", "/?", "/admin", "/admin user list",
    # Non solo quelli che il runtime conosce: anche i comandi del client
    # web e quelli inesistenti. Il Tutor non deve prendersi UNA BARRA.
    "/clear", "/clearbuf", "/reload", "/health", "/comando-che-non-esiste",
])
def test_il_tutor_non_acquisisce_autorita_sui_comandi(comando, monkeypatch) -> None:
    def _mai(*a, **kw):
        raise AssertionError(f"il Tutor ha preso il comando {comando!r}")

    monkeypatch.setattr(tutor_boundary, "_answer", _mai)
    assert tutor_boundary.answer(comando, _Principale()) is None


def test_una_domanda_normale_arriva_ancora_al_tutor(monkeypatch) -> None:
    """Il confine toglie i comandi, non le domande: senza questo controllo il
    rimedio sarebbe peggiore del male."""
    visti = []
    monkeypatch.setattr(tutor_boundary, "_answer",
                        lambda q, p, **kw: visti.append(q) or "risposta")
    assert tutor_boundary.answer("come funziona la ricerca foto?",
                                 _Principale()) == "risposta"
    assert visti == ["come funziona la ricerca foto?"]


def test_un_percorso_assoluto_non_e_un_comando(monkeypatch) -> None:
    """`/opt/metnos` inizia con una barra ma e' un percorso: deve restare una
    domanda ordinaria, altrimenti il rimedio si mangerebbe mezzo dominio file."""
    visti = []
    monkeypatch.setattr(tutor_boundary, "_answer",
                        lambda q, p, **kw: visti.append(q) or "risposta")
    assert tutor_boundary.answer("/opt/metnos quanto pesa?",
                                 _Principale()) == "risposta"
    assert visti == ["/opt/metnos quanto pesa?"]
