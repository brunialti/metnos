"""Un fallimento senza identita' non si annuncia con un segnaposto.

Il descrittore dei fallimenti antepone al motivo l'elemento che ha fallito
(«/tmp/x»: permesso negato). Alcuni domini pero' falliscono senza un singolo
elemento da nominare: la risoluzione di un catalogo di pacchetti riguarda la
richiesta intera, non una riga. Li' l'etichetta era «?», e il messaggio
arrivava all'utente come «Nessuna operazione eseguita. «?»: non trovo questi
pacchetti» — un punto interrogativo che non risponde a niente.

Trovato 18/8/2026 su un turno reale (`installa il pacchetto htop`).
"""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "runtime"))

from agent_runtime import TurnLog  # noqa: E402


def _finale(fallimento):
    """Il messaggio finale prodotto da UN solo step mutante fallito."""
    step = SimpleNamespace(
        chosen_tool="install_packages",
        result={"ok": True, "results": [], "failed": [fallimento]},
    )
    log = SimpleNamespace(steps=[step], final_message="fatto tutto!",
                          _MUTATE_SUCCESS_KEYS=TurnLog._MUTATE_SUCCESS_KEYS)
    TurnLog._enforce_mutating_honesty(log)
    return log.final_message


def test_senza_identita_resta_il_solo_motivo() -> None:
    testo = _finale({"error": "Non trovo questi pacchetti nel catalogo."})
    assert "«?»" not in testo, f"segnaposto vuoto nel messaggio: {testo}"
    assert "Non trovo questi pacchetti" in testo


def test_con_identita_l_elemento_resta_nominato() -> None:
    # Il rovescio: dove l'identita' c'e', dire QUALE ha fallito e' il punto.
    testo = _finale({"path": "/tmp/x.txt", "error": "permesso negato"})
    assert "/tmp/x.txt" in testo


def test_un_pacchetto_e_un_elemento_come_gli_altri() -> None:
    # `package_id` non era fra i campi-identita' noti al descrittore.
    testo = _finale({"package_id": "htop", "error": "gestore assente"})
    assert "htop" in testo
