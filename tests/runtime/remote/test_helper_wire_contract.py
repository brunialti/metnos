"""Il corpo della firma deve combaciare fra client e aiutante (17/8/2026).

Sono due programmi separati per scelta di sicurezza: l'aiutante gira con i
privilegi di sistema e non deve linkare codice del client. Il prezzo di quella
separazione e' che il formato su cui si calcola la firma e' scritto DUE volte,
in due linguaggi che non si parlano.

Se una delle due stringhe cambia, la firma smette di verificare e ogni
installazione fallisce con «firma non attendibile» — un sintomo che non
somiglia per niente alla causa. Questa guardia lo dice subito, e in italiano.

Le due stringhe attese vivono nei rispettivi test Rust, scritte per esteso.
Qui si confrontano fra loro: e' l'unico posto che vede entrambi i progetti.

Run: `python3 -m pytest tests/runtime/remote/test_helper_wire_contract.py -v`
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
AIUTANTE = ROOT / "helper-rs" / "src" / "protocol.rs"
CLIENT = ROOT / "client-rs" / "src" / "helper_client.rs"

# Il corpo canonico di una richiesta d'esempio. I campi sono separati da un
# carattere di controllo che non puo' comparire in un identificativo: nel
# sorgente Rust e' scritto come sequenza di escape, ed e' cosi' che lo
# cerchiamo — leggendo il testo del file, non eseguendolo.
_ATTESA = re.compile(r'"(install\\u\{1f\}winget\\u\{1f\}[^"]+)"')


def _corpo_atteso(percorso: Path) -> str:
    testo = percorso.read_text(encoding="utf-8")
    trovate = _ATTESA.findall(testo)
    assert trovate, f"nessun corpo canonico atteso in {percorso.name}"
    assert len(trovate) == 1, (
        f"{percorso.name} dichiara {len(trovate)} corpi attesi: "
        "il contratto deve avere una sola fonte per progetto")
    return trovate[0]


def test_i_due_progetti_esistono():
    """Una guardia che non trova i file passerebbe senza verificare niente."""
    assert AIUTANTE.is_file(), AIUTANTE
    assert CLIENT.is_file(), CLIENT


def test_il_corpo_della_firma_combacia():
    """Il vincolo: due programmi separati, un solo formato.

    Se questo test diventa rosso, NON allineare la stringa a caso: decidere
    quale dei due e' cambiato per errore, perche' una firma calcolata su un
    formato e verificata su un altro non e' un dettaglio di scrittura."""
    assert _corpo_atteso(AIUTANTE) == _corpo_atteso(CLIENT)


def test_i_campi_sono_separati_da_un_carattere_impossibile_nei_valori():
    """Senza un separatore impossibile nei campi, due richieste diverse
    potrebbero produrre lo stesso corpo spostando un confine — e una firma
    varrebbe per un'operazione che nessuno ha approvato."""
    corpo = _corpo_atteso(AIUTANTE)
    assert corpo.count("u{1f}") == 4, "servono quattro separatori, cinque campi"


def _operazioni_dichiarate(percorso: Path) -> tuple:
    """I nomi delle operazioni, presi dalla DICHIARAZIONE dell'enumerazione.

    Non da una ricerca su tutto il file: `protocol.rs` contiene un test che
    manda `"exec"` per dimostrare che viene rifiutato, e una ricerca ingenua
    lo scambierebbe per un quarto verbo. Un test che sbaglia bersaglio e'
    peggio di nessun test, perche' fa perdere tempo su un problema che non
    c'e'.
    """
    testo = percorso.read_text(encoding="utf-8")
    corpo = re.search(r"enum Operation \{(.*?)\n\}", testo, re.S)
    assert corpo, f"{percorso.name}: nessuna enumerazione Operation"
    return tuple(re.findall(r"^\s{4}(\w+),", corpo.group(1), re.M))


def test_i_due_progetti_conoscono_le_stesse_tre_operazioni():
    """Un verbo in piu' da una parte sola sarebbe un'operazione che nessuno
    esegue, o peggio una che nessuno si aspetta."""
    assert _operazioni_dichiarate(AIUTANTE) == ("Query", "Install", "Uninstall")
    assert _operazioni_dichiarate(CLIENT) == _operazioni_dichiarate(AIUTANTE)


def test_nessuna_operazione_significa_esegui():
    """Il confine dell'intero disegno: tre operazioni su un pacchetto, e
    nessuna e' «esegui questo»."""
    for percorso in (AIUTANTE, CLIENT):
        for vietata in ("Exec", "Run", "Shell", "Command"):
            assert vietata not in _operazioni_dichiarate(percorso), (
                f"{percorso.name} dichiara l'operazione «{vietata}»")
