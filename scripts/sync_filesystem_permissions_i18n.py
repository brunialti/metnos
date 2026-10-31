#!/usr/bin/env python3
"""Replayable IT/EN catalogue for explicit device filesystem permissions."""
from __future__ import annotations
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
TRANSLATIONS = {
    "MSG_FS_PERMISSIONS": ("Permessi", "Permissions"),
    "MSG_FS_DEVICES": ("Dispositivi", "Devices"),
    "MSG_FS_EXPLANATION": ("Autorizza soltanto i file e le cartelle che vuoi usare con Metnos. I permessi del sistema operativo continuano a valere. Le concessioni e le revoche vengono registrate.", "Authorize only the files and folders you want to use with Metnos. Operating system permissions still apply. Grants and revocations are recorded."),
    "MSG_FS_UPDATE_REQUIRED": ("Il dispositivo deve aggiornare il client per utilizzare i permessi sui file. Le nuove operazioni restano bloccate fino all’aggiornamento.", "The device client must be updated to use filesystem permissions. New operations remain blocked until the update."),
    "MSG_FS_SERVER": ("Server Metnos", "Metnos server"),
    "MSG_FS_SERVER_PERMISSIONS": ("Permessi del server", "Server permissions"),
    "MSG_FS_SERVER_DEFAULTS": ("Le directory personali e gli spazi di lavoro già previsti da Metnos restano disponibili senza nuove concessioni. Qui gestisci soltanto accessi aggiuntivi. Revocare una concessione aggiuntiva non modifica gli accessi predefiniti.", "Personal directories and working areas already provided by Metnos remain available without new grants. Manage only additional access here. Revoking an additional grant does not change default access."),
    "MSG_FS_ADDITIONAL": ("Accessi aggiuntivi", "Additional access"),
    "MSG_FS_USER": ("Utente", "User"),
    "MSG_FS_SHOW": ("Mostra", "Show"),
    "MSG_FS_SERVER_HINT": ("Gestisci l’accesso ai percorsi del server per ciascun utente. Le concessioni sono indipendenti dai dispositivi e sono disponibili alle operazioni di Metnos che richiedono accesso ai file, comprese le copie da e verso un PC.", "Manage server path access for each user. Grants are independent of devices and are available to Metnos file operations, including copies to and from a PC."),
    "MSG_FS_PATH": ("Percorso completo", "Absolute path"),
    "MSG_FS_KIND": ("Tipo", "Type"),
    "MSG_FS_FILE": ("File", "File"),
    "MSG_FS_FOLDER": ("Cartella", "Folder"),
    "MSG_FS_READ": ("Lettura", "Read"),
    "MSG_FS_WRITE": ("Scrittura", "Write"),
    "MSG_FS_RECURSIVE": ("Includi sottocartelle", "Include subfolders"),
    "MSG_FS_OVERWRITE": ("Consenti sovrascrittura", "Allow overwrite"),
    "MSG_FS_EXPIRY": ("Scadenza", "Expiry"),
    "MSG_FS_24H": ("Tra 24 ore", "In 24 hours"),
    "MSG_FS_1H": ("Tra un’ora", "In one hour"),
    "MSG_FS_7D": ("Tra 7 giorni", "In 7 days"),
    "MSG_FS_NO_EXPIRY": ("Nessuna scadenza", "No expiry"),
    "MSG_FS_SCOPE_HINT": ("Una cartella comprende i file direttamente al suo interno; per i livelli successivi seleziona “Includi sottocartelle”. La scrittura permette creazione, modifica e cancellazione; per sostituire un file esistente serve anche “Consenti sovrascrittura”. I collegamenti e i percorsi interni di Metnos sono esclusi.", "A folder includes its immediate files; select “Include subfolders” for deeper levels. Write permits creation, modification and deletion; replacing an existing file also requires “Allow overwrite”. Links and Metnos internal paths are excluded."),
    "MSG_FS_GRANT": ("Concedi permessi", "Grant permissions"),
    "MSG_FS_REVOKE": ("Revoca", "Revoke"),
    "MSG_FS_EMPTY": ("Nessuna concessione aggiuntiva.", "No additional grants."),
    "MSG_FS_STATUS": ("Stato", "Status"),
    "MSG_FS_STATE_ACTIVE": ("Attivo", "Active"),
    "MSG_FS_STATE_EXPIRED": ("Scaduto", "Expired"),
    "MSG_FS_STATE_REVOKED": ("Revocato", "Revoked"),
    "ERR_FS_PATH_INVALID": ("Percorso non valido: indica un percorso completo, per esempio C:\\Users\\Nome\\Documents o /mnt/documenti. Non sono ammessi percorsi relativi o di rete.", "Invalid path: enter an absolute path, such as C:\\Users\\Name\\Documents or /mnt/documents. Relative and network paths are not supported."),
    "ERR_FS_DEVICE_INVALID": ("Dispositivo non disponibile o appartenente a un altro utente.", "Device unavailable or owned by another user."),
    "ERR_FS_GRANT_INVALID": ("Scegli un file o una cartella e almeno un permesso. La sovrascrittura richiede la scrittura; le sottocartelle richiedono una cartella.", "Choose a file or folder and at least one permission. Overwrite requires write; subfolders require a folder."),
    "ERR_FS_GRANT_LIMIT": ("Raggiunto il limite di 128 concessioni attive in questo ambito. Revoca quelle non più necessarie.", "The limit of 128 active grants in this scope has been reached. Revoke grants you no longer need."),
    "ERR_FS_PERMISSION_REQUIRED": ("Accesso al percorso non autorizzato o permesso scaduto/revocato. Per i percorsi del server apri Sistema → Permessi del server; per quelli del PC apri Dispositivi → Permessi. Concedi lettura all’origine e scrittura alla destinazione; per sostituire file esistenti autorizza anche la sovrascrittura.", "Path access is not authorized or the permission expired/was revoked. For server paths open System → Server permissions; for PC paths open Devices → Permissions. Grant read on the source and write on the destination; also allow overwrite to replace existing files."),
    "ERR_FS_RESERVED_PATH": ("Il percorso contiene dati interni protetti di Metnos e non può essere usato come un normale file.", "The path contains protected Metnos internal data and cannot be used as an ordinary file."),
    "ERR_FS_LINK_REFUSED": ("Operazione rifiutata: il percorso è un collegamento o contiene collegamenti. Usa il percorso diretto del file o della cartella.", "Operation refused: the path is or contains a link. Use the direct file or folder path."),
    "ERR_FS_CONTRACT_INVALID": ("Contratto dell’operazione sui file non valido. Occorre verificare il rilascio del client e dell’executor.", "Invalid filesystem operation contract. The client and executor release must be checked."),
    "ERR_FS_AUTHORITY_INVALID": ("Non è stato possibile verificare l’autorizzazione del server. L’operazione sui file è stata interrotta.", "The server authorization could not be verified. The filesystem operation was stopped."),
    "ERR_FS_INVOCATION_INVALID": ("L’operazione sui file è scaduta o è stata annullata. Ripeti la richiesta.", "The filesystem operation expired or was cancelled. Repeat the request."),
    "ERR_FS_REQUEST_EXPIRED": ("L’autorizzazione temporanea è scaduta. Verifica l’ora del dispositivo e ripeti la richiesta.", "The temporary authorization expired. Check the device clock and repeat the request."),
    "ERR_FS_TRANSFER_INVALID": ("La copia non ha superato i controlli di integrità. Verifica i percorsi e ripeti la richiesta.", "The copy failed its integrity checks. Check the paths and repeat the request."),
    "ERR_FS_SIZE_LIMIT": ("Raggiunto il limite dell’operazione sui file. Riduci il numero di file o la quantità di dati richiesta.", "The filesystem operation limit was reached. Reduce the number of files or amount of data requested."),
    "ERR_FS_DEST_EXISTS": ("Il file di destinazione esiste già. Per sostituirlo servono una richiesta esplicita e il permesso di sovrascrittura.", "The destination file already exists. Replacing it requires an explicit request and overwrite permission."),
    "ERR_FS_HASH_MISMATCH": ("La verifica dei dati copiati non è riuscita. La destinazione non è stata sostituita.", "Copied data verification failed. The destination was not replaced."),
    "ERR_FS_SOURCE_CHANGED": ("Il file di origine è cambiato durante la copia. Ripeti la richiesta quando il file non viene modificato.", "The source file changed during the copy. Repeat the request when the file is no longer being modified."),
    "ERR_FS_BINARY_ENCODING_REQUIRED": ("Il file non è testo UTF-8. Richiedi una lettura binaria o specifica la codifica corretta.", "The file is not UTF-8 text. Request a binary read or specify the correct encoding."),
    "ERR_FS_BROKER_UNSUPPORTED": ("Questa variante dell’operazione sui file non è supportata dal client. Usa percorsi completi di file e cartelle esistenti.", "This filesystem operation variant is not supported by the client. Use absolute paths to existing files and folders."),
    "ERR_FS_IO": ("Il sistema operativo non ha consentito l’operazione sul file. Verifica che il percorso esista, che il file non sia occupato e che l’utente del client abbia i diritti necessari.", "The operating system did not allow the file operation. Check that the path exists, the file is not locked, and the client user has the required rights."),
}

def sync(path: Path) -> int:
    sys.path.insert(0, str(ROOT / "runtime"))
    import i18n
    i18n.DB_PATH = path
    i18n._SEED_DB_PATH = path
    i18n._conn = None
    conn = i18n._open()
    conn.execute("CREATE TABLE IF NOT EXISTS i18n_seed_history (key TEXT NOT NULL,lang TEXT NOT NULL,version_hash TEXT NOT NULL,PRIMARY KEY(key,lang,version_hash))")
    for key, (it, en) in TRANSLATIONS.items():
        for lang, previous in conn.execute("SELECT lang,text FROM i18n WHERE key=? AND text IS NOT NULL", (key,)).fetchall():
            conn.execute("INSERT OR IGNORE INTO i18n_seed_history VALUES(?,?,?)", (key,lang,i18n._sha256_full(previous)))
        i18n.set_catalog_translations(key, {"it": it, "en": en}, source_lang="it")
    conn.commit()
    i18n._checkpoint(conn)
    conn.close()
    i18n._conn = None
    return len(TRANSLATIONS)*2

if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT/"install/data/i18n_seed.sqlite")
    args=parser.parse_args()
    print(f"{sync(args.db)} translations")
