"""La fotografia di un SQLite comprende cio' che sta ancora nel registro.

Difetto trovato il 17/8/2026. La suite copia alcuni database nella propria
casa isolata, e li copiava con `shutil.copy2`: il solo file principale. In
modalita' WAL — quella che questi database usano — una scrittura recente vive
nel file `-wal` finche' qualcuno non la travasa, quindi la copia era una
fotografia VECCHIA.

Il sintomo era muto: un messaggio i18n riscritto risultava nella versione
precedente dentro la suite e in quella nuova fuori, e il test diceva soltanto
«disallineato». Mezz'ora per capirlo, e sarebbe tornato.

Il difetto non riguardava un test in particolare: riguardava QUALUNQUE test
che legga uno di quei database.

Run: `python3 -m pytest tests/runtime/infra/test_sqlite_snapshot_completa.py -v`
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from _runtime_conftest import module as _runtime_conftest  # noqa: E402

_copy_if_present = _runtime_conftest._copy_if_present


def _db_in_wal(percorso: Path, valore: str) -> None:
    """Un database in WAL con una scrittura NON ancora travasata."""
    conn = sqlite3.connect(str(percorso))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE IF NOT EXISTS prova (chiave TEXT, testo TEXT)")
    conn.execute("DELETE FROM prova")
    conn.execute("INSERT INTO prova VALUES ('k', ?)", (valore,))
    conn.commit()
    # Nessun checkpoint: e' il punto. La connessione resta aperta, cosi' il
    # travaso automatico alla chiusura non nasconde il caso da verificare.
    return conn


def test_una_scrittura_nel_registro_arriva_nella_copia(tmp_path):
    """Il caso reale: l'ultimo valore scritto deve comparire nella copia."""
    sorgente = tmp_path / "dati.sqlite"
    aperta = _db_in_wal(sorgente, "valore-nuovo")
    try:
        assert (sorgente.with_name("dati.sqlite-wal")).exists(), (
            "il caso da verificare richiede un registro non travasato")

        destinazione = tmp_path / "copia" / "dati.sqlite"
        _copy_if_present(sorgente, destinazione)

        letto = sqlite3.connect(f"file:{destinazione}?mode=ro", uri=True)
        valore = letto.execute("SELECT testo FROM prova").fetchone()[0]
        letto.close()
        assert valore == "valore-nuovo", (
            "la copia porta uno stato vecchio: i test girerebbero su una "
            "macchina diversa da quella vera")
    finally:
        aperta.close()


def test_la_copia_e_un_file_solo(tmp_path):
    """Niente `-wal` accanto alla copia: un database che si porta dietro un
    registro separato e' di nuovo due file da tenere allineati."""
    sorgente = tmp_path / "dati.sqlite"
    aperta = _db_in_wal(sorgente, "x")
    try:
        destinazione = tmp_path / "copia" / "dati.sqlite"
        _copy_if_present(sorgente, destinazione)
        assert destinazione.exists()
        assert not destinazione.with_name("dati.sqlite-wal").exists()
    finally:
        aperta.close()


def test_un_file_normale_si_copia_come_prima(tmp_path):
    """La regola vale per i database, non per tutto: un JSON resta una copia
    di byte, senza passare da SQLite."""
    sorgente = tmp_path / "config.json"
    sorgente.write_text('{"a": 1}', encoding="utf-8")
    destinazione = tmp_path / "copia" / "config.json"
    _copy_if_present(sorgente, destinazione)
    assert destinazione.read_text(encoding="utf-8") == '{"a": 1}'


def test_un_file_assente_non_e_un_errore(tmp_path):
    _copy_if_present(tmp_path / "mai-esistito.sqlite", tmp_path / "x.sqlite")
    assert not (tmp_path / "x.sqlite").exists()


def test_un_finto_sqlite_ricade_sulla_copia_semplice(tmp_path):
    """Un file col nome giusto e il contenuto sbagliato non fa cadere la
    preparazione della suite: e' un problema del test che lo usa."""
    sorgente = tmp_path / "rotto.sqlite"
    sorgente.write_bytes(b"non sono un database")
    destinazione = tmp_path / "copia" / "rotto.sqlite"
    _copy_if_present(sorgente, destinazione)
    assert destinazione.read_bytes() == b"non sono un database"
