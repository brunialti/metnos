"""runtime/backends/messaging — builtin messaging backends.

Architettura (decisione 13/5/2026, Q1 canonical+args):
- Ogni file `<channel>_<provider>.py` espone le funzioni dei verbi
  messaging (send/read/find/delete/move) per UN provider builtin.
- Gli executor `send_messages.py`/`read_messages.py`/... dispatchano
  esplicitamente via `_HANDLERS` table cablato.

Backend builtin disponibili:
- `email_metnos`: IMAP+SMTP locale (riusa `runtime/mail_client.py`).
- `telegram_bot`: Telegram Bot API outbound (riusa `runtime/channels/telegram.py`).

Predisposizione plugin esterni (ADR pending):
- Quando si aggiungeranno plugin esterni in
  `~/.local/share/metnos/plugins/messaging-*/`, il dispatcher dell'executor
  arricchira' `_HANDLERS` via plugin loader (scan dei plugin manifest +
  import dinamico + trust gate). NIENTE registry magico ora; il pattern
  resta semplice e Pythonico.
- Vincoli previsti per il loader plugin:
  1. Manifest dichiara contract_version + (verb, channel, client) tuples
  2. Trust gate: enabled=true + consent_token in cred store
  3. Precedenza: builtin > plugin (no override builtin senza force flag)

Funzioni per backend:
- `send(args: dict) -> dict`: invio messaggi (vettoriale).
- `read(args: dict) -> dict`: lettura per id / window / criteri.
- `find(args: dict) -> dict`: ricerca con criteri.
- `delete(args: dict) -> dict`: cancellazione per UID.
- `move(args: dict) -> dict`: spostamento fra folder.

Contratto comune di ritorno: dict con `ok: bool` + campi specifici del
verbo (entries / results / failed / ok_count / fail_count / ...).
"""
