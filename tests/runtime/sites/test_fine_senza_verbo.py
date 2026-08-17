"""Un fine senza verbo e' un fine, non un'azione non supportata (2026-08-07).

Il manifest di `act_sites` chiede al pianificatore esattamente questa forma —
«DEVI nominare la COSA da raggiungere, non solo il verbo: 'le mie
prenotazioni', non 'vai'» — e porta come esempio canonico
`action="fatture 2026"`. Il resolver pretendeva invece un verbo per decidere la
primitiva, e rispondeva `unsupported_action` proprio all'esempio del manifest:
turno reale `0cde68a46a7a426d`, dove il piano era corretto in ogni suo passo e
l'ultimo e' morto sull'unica cosa che gli era stata chiesta di scrivere.

E' lo stesso filo del resto del dominio: il verbo con cui si chiede e' rumore
per chi deve riconoscere il contenuto (`_goal_noise`), e non puo' essere
insieme rumore e requisito.

Il vocabolario resta CHIUSO dall'altro lato: si ammette cio' che NOMINA
qualcosa che una pagina potrebbe mostrare, non un'espressione o un selettore.
"""
from __future__ import annotations

from playwright_sidecar import action_resolver as ar


def test_l_esempio_canonico_del_manifest_e_un_fine() -> None:
    for fine in ("le mie prenotazioni", "fatture 2026", "invoices 2026",
                 "my upcoming bookings", "le mie prenotazioni future"):
        parsed = ar.parse_action(fine)
        assert parsed["ok"] is True, fine
        assert parsed["primitive"] == "search", fine
        assert parsed["target"], fine
        assert ar.is_goal_navigation_request(fine) is True, fine


def test_cio_che_non_nomina_niente_resta_rifiutato() -> None:
    """Il confine non e' «ha un verbo», e' «e' un nome».

    Un'espressione, un selettore o un frammento di codice non e' il nome di una
    cosa che una pagina possa mostrare: resta fuori dal vocabolario chiuso
    anche adesso che un testo senza verbo puo' essere un fine.
    """
    for non_fine in ("esegui javascript alert(1)", "div[id=x]", "a > b",
                     "x=1;y=2", "", "   "):
        parsed = ar.parse_action(non_fine)
        assert parsed["ok"] is False, non_fine
        assert parsed["error_class"] in (
            "unsupported_action", "invalid_args"), non_fine
        assert ar.is_goal_navigation_request(non_fine) is False, non_fine


def test_un_verbo_atomico_resta_un_comando_atomico() -> None:
    """Guardia anti-regressione: la novita' vale SOLO dove non c'era verbo."""
    assert ar.parse_action("apri il menu account")["primitive"] == "click"
    assert ar.parse_action("clicca il menu account")["primitive"] == "click"
    assert ar.parse_action("compila e invia il modulo")["primitive"] == "submit"
    assert ar.parse_action("attendi 5 secondi")["primitive"] == "wait"
    goto = ar.parse_action("vai su https://example.test/path?a=1")
    assert goto["primitive"] == "goto"
    assert goto["target"] == "https://example.test/path?a=1"
    for atomica in ("apri il menu account", "clicca il menu account",
                    "compila e invia il modulo", "attendi 5 secondi"):
        assert ar.is_goal_navigation_request(atomica) is False, atomica
