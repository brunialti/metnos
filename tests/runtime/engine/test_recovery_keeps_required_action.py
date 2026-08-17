"""Il piano di ripiego non degrada una mutazione a una ricerca (17/8/2026).

Il 16/8 il motore ha imparato a non sostituire un effetto esplicito con una
consultazione innocua: chi chiede un'azione che nessuno strumento sa eseguire
riceve la ragione, non un rinvio (`capability_missing`). Quel cancello pero'
stava solo sul percorso iniziale.

Il ramo di ripiego L3 e' PIU' esposto, non meno: `recover` toglie dal pool lo
strumento fallito, quindi quando quello strumento era l'unico a portare il
verbo richiesto, al proponente resta solo un fratello in sola lettura. Il
piano alternativo ha un'impronta diversa da quella originale, quindi il
controllo di uguaglianza non lo ferma, e l'utente riceve «ecco cosa ho
trovato» per una richiesta di cancellare: l'anti-pattern §2.8 esatto che il
cancello era stato scritto per chiudere.

Rilevato dalla revisione del 17/8, verificato e chiuso qui.

## Che cosa difende davvero questo cancello, misurato

Scrivendo il test si e' scoperto che le guardie deterministiche **riparano da
sole** un ripiego in sola lettura quando lo strumento mutante e' ancora nel
catalogo: da `find_files` la pipeline ricostruisce produttore + consenso +
`delete_files`. Il cancello quindi non e' la difesa principale — e' la rete
sotto di essa, e scatta quando la riparazione non e' possibile perche' lo
strumento mutante non e' piu' offerto dal catalogo.

Il test e' costruito su quella configurazione, non su una in cui le guardie
riparano: un test che passasse grazie alla riparazione non direbbe nulla sul
cancello.

Nota sul caso scelto: `delete_files` / `find_files` sono verbi e strumenti del
vocabolario ODIERNO. Il caso dell'installazione — che ha originato il rilievo
— non e' utilizzabile finche' `install` non entra in `vocab.py` (ITEM 3 parte
A): oggi un piano con `install_packages` viene fermato PRIMA, dal cancello
iniziale, e un test scritto cosi' passerebbe senza mai esercitare il ripiego.

Run: `python3 -m pytest tests/runtime/engine/test_recovery_keeps_required_action.py -v`
"""
from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest import mock

from engine.types import Intent, Framework, StepSpec
from engine import dispatch as eng_dispatch


def _fw(*tools, args_map=None):
    args_map = args_map or {}
    steps = [StepSpec(tool=t, args=dict(args_map.get(t, {}))) for t in tools]
    steps.append(StepSpec(tool="final_answer", args={}))
    return Framework(steps=steps)


class _FakeProposer:
    def __init__(self, fw):
        self.fw = fw
        self.calls = 0

    def propose(self, **kw):
        self.calls += 1
        return self.fw


# Catalogo SENZA lo strumento mutante: e' la configurazione in cui le guardie
# non possono riparare il ripiego, quindi l'unica in cui il cancello e'
# osservabile. Il piano iniziale lo nomina lo stesso (glielo passa il
# proponente finto), come farebbe un piano che sopravvive al ritiro di un
# executor.
_CATALOG = [
    SimpleNamespace(name="find_files",
                    args_schema={"type": "object", "properties": {}}),
]

# Catalogo COMPLETO: qui le guardie riparano, e il cancello non deve fare
# danni — un ripiego che porta ancora l'azione deve poter girare.
_CATALOG_FULL = _CATALOG + [
    SimpleNamespace(name="delete_files",
                    args_schema={"type": "object", "properties": {}}),
]

_QUERY = "cancella i file temporanei in /tmp/prova"
_INTENT = Intent(verb="delete", object="files",
                 actions=[{"verb": "delete", "object": "files"}])
_INITIAL = _fw("delete_files", args_map={"delete_files": {"paths": ["/x"]}})


def _run(recovery_fw, invoked, catalog=None):
    """Turno in cui `delete_files` fallisce con wrong_args e il ripiego
    propone `recovery_fw`."""
    def invoke(name, args):
        invoked.append(name)
        if name == "delete_files" and args.get("paths") == ["/x"]:
            return {"ok": False, "error": "arg non valido",
                    "error_class": "wrong_args"}
        return {"ok": True, "entries": [{"path": "/tmp/prova/a.tmp"}],
                "results": [{"path": "/tmp/prova/a.tmp", "ok": True}]}

    fake_proposer = _FakeProposer(_INITIAL)
    fake_recovery = SimpleNamespace(recover=lambda **kw: recovery_fw)
    fake_terminator = SimpleNamespace(
        explain=lambda **kw: SimpleNamespace(final_text="non riuscito"))
    env = {"METNOS_ENGINE": "simple", "METNOS_FASTPATH": "0"}
    with mock.patch.dict(os.environ, env), \
         mock.patch("engine.proposer.get_proposer",
                    return_value=fake_proposer), \
         mock.patch("engine.recovery.get_recovery",
                    return_value=fake_recovery), \
         mock.patch("engine.terminator.get_terminator",
                    return_value=fake_terminator), \
         mock.patch("engine.cluster.embed", new=lambda q: None):
        return eng_dispatch.run_turn(
            query=_QUERY, intent=_INTENT,
            catalog=(_CATALOG if catalog is None else catalog),
            invoke_executor_cb=invoke, turn_id="recovery-required-action")


class TestRecoveryKeepsRequiredAction(unittest.TestCase):

    def test_il_piano_iniziale_arriva_davvero_al_ripiego(self):
        """Precondizione del test: il cancello iniziale NON deve fermare il
        piano di partenza, altrimenti i casi sotto passerebbero senza mai
        esercitare il ramo di ripiego."""
        from engine.dispatch import _dropped_required_verbs
        self.assertEqual(
            _dropped_required_verbs(_INITIAL, _QUERY, _INTENT), set())

    def test_un_ripiego_in_sola_lettura_non_esegue(self):
        """Il caso del rilievo: la mutazione fallisce, il ripiego propone una
        ricerca. Non deve eseguirla, e non deve rispondere «ecco»."""
        invoked: list[str] = []
        res = _run(_fw("find_files"), invoked)

        self.assertEqual(invoked, ["delete_files"],
                         "la ricerca non deve essere eseguita al posto "
                         "della cancellazione")
        self.assertEqual(res.error_class, "capability_missing")
        self.assertEqual(res.match_source, "terminator")

    def test_l_esito_non_si_spaccia_per_riuscito(self):
        """§2.8: il turno non dichiara un esito che non corrisponde."""
        res = _run(_fw("find_files"), [])
        self.assertNotEqual(res.match_source, "recovery")

    def test_un_ripiego_che_mantiene_l_azione_resta_ammesso(self):
        """Il cancello non e' un divieto di ripiego: un piano alternativo che
        porta ancora la mutazione richiesta deve poter girare.

        Il grafo dev'essere DIVERSO da quello iniziale: rieseguire la stessa
        sequenza di strumenti non ripara un errore di argomenti, e il motore
        la salta apposta (controllo di impronta subito sotto il cancello)."""
        invoked: list[str] = []
        res = _run(_fw("find_files", "delete_files"),
                   invoked, catalog=_CATALOG_FULL)

        self.assertEqual(invoked.count("delete_files"), 2,
                         "il ripiego che mantiene l'azione deve eseguire")
        self.assertNotEqual(res.error_class, "capability_missing")

    def test_le_guardie_riparano_prima_quando_possono(self):
        """La difesa principale non e' il cancello: col catalogo completo, un
        ripiego in sola lettura viene RIPARATO dalle guardie in una pipeline
        che l'azione ce l'ha. Documentato perche' spiega perche' il cancello
        e' una rete e non un muro."""
        invoked: list[str] = []
        res = _run(_fw("find_files"), invoked, catalog=_CATALOG_FULL)

        self.assertIn("delete_files", invoked,
                      "le guardie devono ricostruire l'azione richiesta")
        self.assertNotEqual(res.error_class, "capability_missing")


if __name__ == "__main__":
    unittest.main()
