"""La direzione mettere/togliere viene dalla richiesta, non dal modello.

Storia misurata (17/8/2026). `install_packages` porta la direzione in un
argomento booleano — un verbo solo invece di due, come vuole il vocabolario —
ma il modello la sbagliava in modo costante: «installa PowerToys su
pc-roberto» produceva `uninstall=true` in **12 prove su 12**, con tre stesure
diverse della descrizione del manifest:

  1. testa che afferma cosa fa `true`            -> 4/4 sbagliate
  2. testa che definisce entrambe le direzioni   -> 3/3 sbagliate
  3. testa senza l'esempio letterale `=true`     -> 3/3 sbagliate

Il testo non era la leva. Una decisione che la richiesta contiene per intero
non si affida a un modello (§7.9), e le forme che la esprimono vivono nel
lessico i18n perche' sono traducibili (§7.13).

Dopo il resolver: 4/4 corrette, due per direzione, su turni reali.

Run: `python3 -m pytest tests/runtime/engine/test_install_direction_resolver.py -v`
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_RUNTIME = Path(__file__).resolve().parents[3] / "runtime"
if str(_RUNTIME) not in sys.path:
    sys.path.insert(0, str(_RUNTIME))

from install_direction_resolver import resolve_install_direction  # noqa: E402

_SCHEMA = {"type": "object", "properties": {
    "packages": {"type": "array"},
    "uninstall": {"type": "boolean", "default": False},
}}


def _r(args, query, schema=_SCHEMA):
    return resolve_install_direction("qualsiasi", args, query,
                                     args_schema=schema)


# ── Il caso reale ─────────────────────────────────────────────────────
def test_installa_resta_installazione_anche_se_il_modello_dice_altro():
    """Il difetto esatto: il modello proponeva `uninstall=true` per una
    richiesta di installazione. La richiesta e' l'autorita'."""
    out = _r({"packages": ["Microsoft.PowerToys"], "uninstall": True},
             "installa PowerToys su pc-roberto")
    assert out["uninstall"] is False


def test_disinstalla_resta_rimozione_anche_se_il_modello_dice_altro():
    """L'errore simmetrico e' piu' grave, non meno: installare quando si
    chiedeva di rimuovere lascia sulla macchina qualcosa che non era
    voluto."""
    out = _r({"packages": ["Microsoft.PowerToys"], "uninstall": False},
             "disinstalla PowerToys da pc-roberto")
    assert out["uninstall"] is True


@pytest.mark.parametrize("query,atteso", [
    ("installa PowerToys", False),
    ("installa 7-zip sul mio pc", False),
    ("metti su ripgrep", False),
    ("install PowerToys", False),
    ("set up ripgrep", False),
    ("disinstalla PowerToys", True),
    ("disinstallare 7-zip", True),
    ("rimuovi il programma PowerToys", True),
    ("togli il programma dal pc", True),
    ("uninstall PowerToys", True),
    ("remove the program from my pc", True),
])
def test_le_due_direzioni_in_italiano_e_in_inglese(query, atteso):
    out = _r({"packages": ["X"]}, query)
    assert out.get("uninstall", False) is atteso, query


# ── Perche' questo resolver CORREGGE, invece di limitarsi ad aggiungere ─
def test_corregge_un_valore_gia_scritto():
    """Il fratello `unique_rows` aggiunge un'opzione omessa, e un `true` di
    troppo costa una riga in meno. Qui un `true` di troppo disinstalla un
    programma al posto di installarlo: il valore proposto dal modello non
    ha l'ultima parola."""
    assert _r({"uninstall": True}, "installa X")["uninstall"] is False
    assert _r({"uninstall": False}, "disinstalla X")["uninstall"] is True


def test_un_valore_gia_giusto_non_viene_toccato():
    """Idempotenza: applicare due volte non cambia niente, e l'oggetto
    torna identico quando non c'e' niente da correggere."""
    a = {"packages": ["X"], "uninstall": True}
    assert _r(a, "disinstalla X") is a
    b = {"packages": ["X"], "uninstall": False}
    assert _r(b, "installa X") is b


# ── Confini ───────────────────────────────────────────────────────────
def test_uno_schema_senza_direzione_non_viene_toccato():
    """Nessun nome di executor e' cablato: il contratto lo dice lo schema,
    quindi cio' che non dichiara l'argomento resta invariato."""
    schema = {"type": "object", "properties": {"paths": {"type": "array"}}}
    a = {"paths": ["/x"]}
    assert _r(a, "disinstalla tutto", schema) is a


def test_un_argomento_omonimo_ma_di_altro_tipo_non_viene_toccato():
    schema = {"type": "object",
              "properties": {"uninstall": {"type": "string"}}}
    a = {"uninstall": "forse"}
    assert _r(a, "disinstalla X", schema) is a


def test_senza_query_non_si_reinterpreta_niente():
    """Una ripresa dopo un consenso porta argomenti gia' decisi:
    reinterpretarli cambierebbe l'operazione che la persona ha approvato."""
    a = {"packages": ["X"], "uninstall": True}
    assert _r(a, "") is a
    assert _r(a, "   ") is a


def test_il_lessico_irraggiungibile_lascia_il_valore_del_modello(monkeypatch):
    """Meglio la proposta del modello che un'operazione decisa a caso: il
    validator e la scheda di conferma restano comunque a valle."""
    import install_direction_resolver as R

    class _Rotto:
        @staticmethod
        def match(*_a, **_k):
            raise RuntimeError("lessico non disponibile")

    monkeypatch.setitem(sys.modules, "detection_lexicon", _Rotto())
    a = {"packages": ["X"], "uninstall": True}
    assert R.resolve_install_direction("t", a, "installa X",
                                       args_schema=_SCHEMA) is a


def test_le_forme_vengono_dal_lessico_non_dal_codice():
    """§7.13 e regola di Roberto: nessuna lista di sinonimi cablata. Le
    forme si traducono, quindi vivono nel lessico i18n."""
    codice = (_RUNTIME / "install_direction_resolver.py").read_text(
        encoding="utf-8")
    corpo = codice.split('"""', 2)[2]  # esclude il docstring
    for parola in ("disinstalla", "uninstall PowerToys", "rimuovi",
                   "togli", "remove the"):
        assert parola not in corpo, f"forma cablata nel codice: {parola}"


def test_il_concetto_esiste_nel_lessico_in_entrambe_le_lingue():
    import detection_lexicon as dl
    dl.ensure_seeded()
    for lingua, esempio in (("it", "disinstalla PowerToys"),
                            ("en", "uninstall PowerToys")):
        assert dl.match("packages.uninstall_request", esempio), lingua
