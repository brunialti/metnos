# google-workspace (skill first-party, Tier 2)

Capacità Google Workspace di Metnos — Gmail, Calendar, Drive, Contacts,
Sheets, Docs — esposte agli executor `*_google_workspace` / `write_files_doc`
/ `*_events` / `*_messages` tramite i backend in
`runtime/backends/{events,files,messages,contacts}/google_workspace.py`.

**Tier**: first-party (builtin). Dormant finché non è configurato OAuth.
La dipendenza esterna (credenziali OAuth Google) è dichiarata QUI; gli
executor restano provider-agnostici e il `backend_resolver` (ADR 0165)
sceglie il provider da configurazione.

## Contenuto

- `SKILL.md` — descrizione, setup OAuth passo-passo, comandi, regole.
- `scripts/google_api.py` — CLI verso le API Google (preferisce `gws` se
  presente, altrimenti client Python). Comandi `gmail/calendar/drive/
  contacts/sheets/docs`. `docs delete-range` = supporto undo §2.3 dell'append.
- `scripts/setup.py` — flusso OAuth2 (`--check`/`--auth-url`/`--auth-code`).
- `scripts/gws_bridge.py` — ponte token ↔ CLI `gws`.
- `scripts/_skill_home.py` — risolve la home della skill via
  `METNOS_SKILL_HOME` (iniettata dal runtime).
- `references/gmail-search-syntax.md` — operatori di ricerca Gmail.

## Credenziali (mai versionate)

`google_token.json` e `google_client_secret.json` vivono nella copia
INSTALLATA in `<user-data>/skills/google-workspace/`, non nel repo. Generate
da `setup.py`. Vedi `.gitignore` del bundle.

## Note di provenienza

Implementazione ridisegnata e mantenuta nel progetto Metnos. Lo script CLI
parla direttamente con le Google API (OAuth2 desktop flow); nessun servizio
intermedio di terzi.
