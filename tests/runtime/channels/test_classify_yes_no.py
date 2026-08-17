"""Il classificatore delle conferme — la porta da cui passa ogni approvazione.

Prima del 16/8/2026 questa funzione non aveva UN test in tutta la suite. Si
poteva riportarla a `_dl.search` (che con i concetti `phrases` non compila
nulla e restituisce sempre None) e la suite restava verde a 5430 test, mentre
in esercizio nessuna conferma testuale funzionava piu'.

Qui si fissano le tre proprieta' che contano:
  1. una conferma reale continua a valere;
  2. una frase che CONTIENE una forma ma non la apre NON vale;
  3. l'ambiguita' non e' mai un consenso.
"""
import pytest

from channels.daemon import _classify_yes_no


# Frasi vere, dal corpus dei turni (`~/.local/share/metnos/turns/*.jsonl`).
@pytest.mark.parametrize("testo", [
    "si", "sì", "SI", "Sì", "ok", "OK", "okay", "yes", "Y", "y",
])
def test_conferme_reali_valgono_si(testo):
    assert _classify_yes_no(testo) == "yes"


@pytest.mark.parametrize("testo", [
    "no", "NO", "annulla", "Annulla.", "stop", "n", "lascia", "niente",
])
def test_rifiuti_reali_valgono_no(testo):
    assert _classify_yes_no(testo) == "no"


def test_una_richiesta_di_undo_non_e_un_rifiuto():
    """«annulla ultima azione» compare 12 volte nello storico ed e' una
    RICHIESTA di undo, non un no a una carta aperta. La regola ancorata la
    mangiava come rifiuto; l'uguaglianza esatta la lascia al motore."""
    assert _classify_yes_no("annulla ultima azione") == "other"
    assert _classify_yes_no("Annulla l ultima operazione.") == "other"


# REGRESSIONE DI SICUREZZA. Ognuna di queste, letta come contenimento, era un
# «yes»: su una carta `admin_approval` un yes fa partire il comando
# privilegiato con il consenso gia' firmato. L'italiano scrive il «si»
# impersonale ovunque, e l'inglese nega con «not ok».
@pytest.mark.parametrize("testo", [
    "come si fa?",
    "dove si trova?",
    "quando si aggiorna?",
    "spiegami come si usa",
    "non si capisce",
    "che ne dici, si o no?",
    "not ok",
    "not okay at all",
])
def test_una_frase_che_contiene_una_forma_non_e_una_conferma(testo):
    assert _classify_yes_no(testo) != "yes"


# Una risposta che non e' ne' un si' ne' un no non deve poter essere scambiata
# per un consenso da chi la riceve.
@pytest.mark.parametrize("testo", [
    "boh", "aspetta", "cosa?", "non lo so", "non farlo", "forse", "",
])
def test_il_resto_e_other(testo):
    assert _classify_yes_no(testo) == "other"


def test_una_richiesta_lunga_non_e_una_conferma():
    """Il cancello dei 30 caratteri: nessun test lo fissava, si poteva
    cancellare senza far cadere niente."""
    lunga = "si, e poi dimmi anche che ore sono per favore"
    assert len(lunga) > 30
    assert _classify_yes_no(lunga) == "other"


def test_ambiguita_non_e_consenso():
    """Una risposta che porta sia un si' sia un no non e' un consenso."""
    assert _classify_yes_no("si o no") == "other"
    assert _classify_yes_no("no o si") == "other"


def test_la_punteggiatura_non_impedisce_una_conferma():
    """«si.» e «ok!» sono conferme; «si?» e' una domanda e non lo e'."""
    for t in ("si.", "ok!", "sì...", "  ok  ", "«si»"):
        assert _classify_yes_no(t) == "yes", t
    assert _classify_yes_no("ok?") == "other"


def test_la_rete_di_emergenza_non_e_piu_larga_del_lessico():
    """PROPRIETA' che conta: una rete di sicurezza sul consenso deve essere
    piu' STRETTA del percorso normale, mai piu' larga. La prima versione era
    piu' larga e riapriva in silenzio i casi che il lessico aveva chiuso."""
    from channels.daemon import _EMERGENCY_CONFIRMATIONS
    import detection_lexicon as _dl
    for concetto, emergenza in _EMERGENCY_CONFIRMATIONS.items():
        lessico = {f.lower() for f in _dl.forms(concetto)}
        assert emergenza <= lessico, (concetto, emergenza - lessico)


def test_le_forme_vengono_dal_lessico(monkeypatch):
    """Autorita' unica: cambio il lessico, non il codice, e il
    classificatore segue."""
    from channels import daemon as _daemon
    finto = {"confirm.yes": ["zzyes"], "confirm.no": ["zzno"]}
    monkeypatch.setattr(_daemon, "_dl",
                        type("_L", (), {"forms": staticmethod(
                            lambda c: finto.get(c, []))})())
    assert _classify_yes_no("zzyes") == "yes"
    assert _classify_yes_no("zzno") == "no"
    assert _classify_yes_no("si") == "other"


def test_una_forma_in_entrambe_le_liste_non_e_un_consenso(monkeypatch):
    """Il consenso non cade dalla parte del si'.

    Le due liste sono tradotte per lingua: una forma rivendicata da entrambe
    e' un difetto di localizzazione, e un difetto si legge «richiedi di
    nuovo», mai «ha acconsentito»."""
    from channels import daemon as _daemon
    ambiguo = {"confirm.yes": ["zzok"], "confirm.no": ["zzok"]}
    monkeypatch.setattr(_daemon, "_dl",
                        type("_L", (), {"forms": staticmethod(
                            lambda c: ambiguo.get(c, []))})())
    assert _classify_yes_no("zzok") == "other"


def test_lessico_irraggiungibile_lascia_confermare(monkeypatch):
    """Un dialogo aperto che non si puo' chiudere e' peggio di un vocabolario
    ridotto: senza lessico restano le forme di emergenza."""
    from channels import daemon as _daemon

    class _Rotto:
        @staticmethod
        def forms(_c):
            raise RuntimeError("lessico non disponibile")

    monkeypatch.setattr(_daemon, "_dl", _Rotto())
    assert _classify_yes_no("si") == "yes"
    assert _classify_yes_no("no") == "no"
    assert _classify_yes_no("boh") == "other"


# Una risposta che apre con un'affermazione ma nega subito dopo NON e' un
# consenso. Trovato durante la verifica del 16/8: «ok, non farlo» apriva con
# «ok» e valeva SI', cioe' su una carta di approvazione faceva partire il
# comando che l'utente stava rifiutando.
# REGRESSIONE DI SICUREZZA, seconda ondata. Ognuna di queste sopravviveva
# alla regola ANCORATA (la forma apre la risposta) e valeva ancora un si'.
# La piu' pericolosa e' la prima famiglia: «ok» seguito da una NUOVA
# richiesta fa partire il comando privilegiato pendente al posto della
# richiesta. Il tentativo di tapparle con un elenco di negatori era una lista
# nera su un dominio aperto: obiezione e rinvio non finiscono mai.
@pytest.mark.parametrize("testo", [
    "ok mostrami i file", "si mostrami tutto", "ok procedi", "ok leggi la mail",
    "si può fare?", "si fa?", "si cancella tutto?", "si torna indietro?",
    "ok aspetta", "ok, ho sbagliato", "si, scherzavo", "ok basta", "ok, dopo",
    "ok?", "si?", "y?",
    "ok, non farlo", "si ma non adesso", "ok non serve", "yes, not now",
    "ok don't",
])
def test_una_risposta_che_non_e_solo_una_conferma_non_e_un_consenso(testo):
    assert _classify_yes_no(testo) != "yes"


@pytest.mark.parametrize("testo", ["SİSTEMA", "Sİnistra", "sìstema", "sínistra"])
def test_unicode_non_fabbrica_conferme(testo):
    """La maiuscola turca puntata e le forme decomposte collassavano su «si»
    dopo il lowercase: normalizzazione NFKC prima del confronto."""
    assert _classify_yes_no(testo) != "yes"


def test_un_incertezza_non_e_un_rifiuto():
    """«non lo so» e' un'incertezza: dichiararla rifiuto sarebbe inventare una
    risposta che l'utente non ha dato."""
    assert _classify_yes_no("non lo so") == "other"
    assert _classify_yes_no("non farlo") == "other"
