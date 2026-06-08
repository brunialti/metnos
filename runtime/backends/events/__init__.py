"""runtime/backends/calendar — builtin calendar/events backends.

Architettura (decisione 13/5/2026, Q1 canonical+args, sequel di
`runtime/backends/files/` e `runtime/backends/messages/` del 13/5/2026):
- Ogni file `<provider>.py` espone le funzioni dei verbi events
  (read/create/delete/find_events_empty) per UN provider builtin. Calendar NON ha
  concetto di `channel` (a differenza di messaging): c'e' solo `client`.
- Gli executor `read_events.py`/`create_events.py`/`delete_events.py`/
  `find_events_empty.py` dispatchano esplicitamente via `_HANDLERS` table
  cablato (chiave: solo `client`).
- NIENTE registry magico, NIENTE @register decorator: dispatch table
  cablato esplicitamente (the design guide §7.2 + §7.9).

Backend builtin disponibili:
- `local_ics`: implementazione minimale (14/5/2026). `read` + `find_events_empty`
  girano su storage `~/.local/share/metnos/calendar.ics` (file mancante =
  calendar vuoto, stato legittimo §2.8). `create`/`delete` restano stub
  esplicito (NOT_IMPLEMENTED, §2.8) finche' parser/writer iCal completo
  non e' giustificato. Override storage via env `METNOS_CALENDAR_ICS`.

Predisposizione plugin esterni (ADR pending):
- Quando si aggiungeranno plugin esterni in
  `~/.local/share/metnos/plugins/calendar-*/`, il dispatcher dell'executor
  arricchira' `_HANDLERS` via plugin loader (scan dei plugin manifest +
  import dinamico + trust gate). NIENTE registry magico ora; il pattern
  resta semplice e Pythonico.
- Vincoli previsti per il loader plugin:
  1. Manifest dichiara contract_version + (verb, client) tuples
  2. Trust gate: enabled=true + consent_token in cred store
  3. Precedenza: builtin > plugin (no override builtin senza force flag)

Funzioni per backend:
- `read(args: dict) -> dict`: legge eventi per finestra temporale.
- `create(args: dict) -> dict`: crea UN evento (summary/start/end/...).
- `delete(args: dict) -> dict`: elimina eventi per id (vettoriale).
- `find_events_empty(args: dict) -> dict`: computa finestre VUOTE del calendar
  (gap fra eventi, filtrabili per size e time_of_day; ADR 0127). Nome 1:1
  con l'executor — il qualifier `_empty` da solo (`find_empty`) sarebbe
  ambiguo perche' «empty» non e' fra i 17 OBJECTS §2.2. `read`/`create`/
  `delete` invece mantengono l'oggetto implicito (calendar gestisce solo
  events) coerentemente con `messaging/email_metnos.py` e `files/local.py`.

Contratto comune di ritorno: dict con `ok: bool` + campi specifici del
verbo. Read/find_events_empty (produttori §2.6) ritornano `entries: list`.
Create/delete (trasformativi §2.6) ritornano `results: list`. Errori
con `error_class` esplicito (§2.8 no silent failure).
"""
