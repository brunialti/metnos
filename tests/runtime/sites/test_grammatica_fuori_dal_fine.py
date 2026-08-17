"""La grammatica della richiesta non entra in cio' che si cerca (2026-08-07).

Misurato su 60 richieste reali del corpus (`internal/tools/misura_residuo_fine.py`,
giudice: il modello che etichetta ogni parola col suo ruolo): una parola su
sette fra quelle che entravano nel fine era grammatica o verbo con cui si
chiede. Le due famiglie che mancavano sono qui, e sono entrambe generali —
`text.*`, non `sites.*`: una domanda ha la stessa forma in ogni dominio.

Due scelte che il banco ha imposto e che questi riscontri difendono:
  - una forma ambigua con un sostantivo NON entra in famiglia. «stato» e' lo
    stato del server molto piu' spesso che un participio di essere; toglierlo
    costava piu' del rumore che risparmiava.
  - un numero di una cifra e' contenuto. La soglia «meno di due caratteri»
    cancellava il «7» di «ultimi 7 giorni» e il «3» di «i 3 mittenti».
"""
from __future__ import annotations

from playwright_sidecar import action_resolver as ar


def test_gli_ausiliari_non_sono_cio_che_si_cerca() -> None:
    assert ar.goal_tokens("quali sono le persone enrolled") == (
        "persone", "enrolled")
    assert ar.goal_tokens("quali account mail hai") == ("account", "mail")
    assert "have" not in ar.goal_tokens("which bookings do I have")


def test_gli_interrogativi_non_sono_cio_che_si_cerca() -> None:
    assert ar.goal_tokens("che appuntamenti ho domani") == (
        "appuntamenti", "domani")
    assert ar.goal_tokens("dove sono le foto del 2020") == ("foto", "2020")


def test_i_connettivi_di_relazione_non_diventano_termini_del_dom() -> None:
    assert ar.goal_tokens(
        "trova le prenotazioni riguardanti Luxor") == ("booking", "luxor")
    assert ar.goal_tokens(
        "find bookings regarding Luxor") == ("booking", "luxor")
    assert ar.page_satisfies_goal(
        "trova le prenotazioni riguardanti Luxor",
        ["Le tue prenotazioni", "Luxor, 18 settembre"],
        scope_text="https://example.test/mytrips")


def test_una_forma_ambigua_con_un_sostantivo_resta_fuori_dalla_famiglia() -> None:
    """«stato» e «completate» restano: il costo di toglierli supera la resa."""
    assert "stato" in ar.goal_tokens("dimmi lo stato del server")
    assert "completate" in ar.goal_tokens("mostrami le attivita' completate")


def test_un_numero_di_una_cifra_e_contenuto() -> None:
    assert "7" in ar.goal_tokens("storico degli ultimi 7 giorni")
    assert "3" in ar.goal_tokens("dimmi i 3 mittenti principali")
    # Nella navigazione i numeri restano filtri differiti, non nomi di menu:
    # quella regola non cambia.
    assert "7" not in ar.goal_tokens("ultimi 7 giorni", navigation=True)


def test_le_faccette_di_stato_sopravvivono() -> None:
    """Guardia: ripulire non deve mangiare cio' che distingue un fine."""
    assert ar.goal_tokens("le mie prenotazioni prossime") == (
        "booking", "future")
    assert ar.goal_tokens("le prenotazioni passate") == ("booking", "past")
