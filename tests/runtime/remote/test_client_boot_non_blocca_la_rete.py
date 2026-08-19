"""L'avvio del client non deve stare dietro a un lavoro lungo.

Trovato dal vivo il 18/8/2026 su PC-ROBERTO. Il client, all'avvio, revocava i
permessi concessi alla sandbox in sessioni precedenti PRIMA di contattare il
server. Revocare un permesso su una cartella ne riscrive i permessi in tutto
cio' che contiene: con 75 cartelle arretrate — fra cui Documenti e Download —
sono stati oltre venti minuti.

Due conseguenze, e la seconda e' peggiore della prima:

1. il computer risultava spento per tutto quel tempo, pur essendo acceso e
   funzionante;
2. un client appena aggiornato non faceva in tempo a confermarsi entro la
   finestra di prova, e veniva riportato indietro alla versione precedente.
   Cioe': su quella macchina gli aggiornamenti automatici non potevano
   riuscire, e il sintomo non somigliava per niente alla causa.

Il vincolo di sicurezza resta intero — nessun executor gira con permessi
vecchi addosso — ma l'attesa vive dove il vincolo vive: prima di ESEGUIRE,
non prima di COLLEGARSI.

Run: `python3 -m pytest tests/runtime/remote/test_client_boot_non_blocca_la_rete.py -v`
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
RUNNER = ROOT / "client-rs" / "src" / "runner.rs"


def _senza_commenti(percorso: Path) -> str:
    """Il sorgente senza commenti: si guarda cosa fa, non cosa spiega."""
    return "\n".join(
        riga for riga in percorso.read_text(encoding="utf-8").splitlines()
        if not riga.lstrip().startswith(("//", "///", "//!"))
    )


def test_la_pulizia_dei_permessi_non_e_attesa_all_avvio():
    codice = _senza_commenti(RUNNER)
    # Parte in disparte: il collegamento al server non la aspetta.
    assert "spawn_blocking(\n            crate::appcontainer::cleanup_all_grants)" in codice \
        or "spawn_blocking(crate::appcontainer::cleanup_all_grants)" in codice, (
            "la pulizia ACL non parte piu' in disparte: se torna davanti alla "
            "rete, il computer risulta spento mentre lavora")
    # E non viene chiamata direttamente, che rimetterebbe l'attesa all'avvio.
    assert "cleanup_all_grants()" not in codice, (
        "la pulizia ACL e' di nuovo chiamata in linea: l'avvio torna a "
        "dipendere da quanto e' grosso l'arretrato")


def test_ma_nessun_executor_gira_prima_che_sia_finita():
    """Il vincolo di sicurezza non si e' perso per strada: e' solo spostato."""
    codice = _senza_commenti(RUNNER)
    posizione_attesa = codice.find("self.attendi_pulizia_acl().await?")
    assert posizione_attesa != -1, "nessuna attesa prima di eseguire"

    posizione_esecuzione = codice.find("async fn execute(")
    assert posizione_esecuzione != -1
    assert posizione_attesa > posizione_esecuzione, (
        "l'attesa deve stare DENTRO execute, non altrove")


def test_un_fallimento_della_pulizia_vale_per_sempre_non_solo_una_volta():
    """Fail-closed che non si dimentica.

    L'esito si conserva: se i permessi vecchi non si sono potuti togliere,
    non si esegue niente — non solo alla prima esecuzione che se n'e'
    accorta, ma a tutte.
    """
    codice = _senza_commenti(RUNNER)
    assert "acl_errore" in codice, "l'esito della pulizia non viene conservato"
    assert "bail!(\"{motivo}\")" in codice, (
        "un fallimento della pulizia non ferma piu' l'esecuzione")
