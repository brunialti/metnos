"""runtime/backends/contacts — builtin contacts backends.

Architettura coerente con `backends/messages` e `backends/events`:
ogni file `<provider>.py` espone le funzioni `find/read/...` per UN
provider. Gli executor canonical (find_contacts/read_contacts/...)
dispatcheranno via `_HANDLERS` table esplicito.

Provider builtin disponibili:
- `google_workspace`: Google People API (via skill bridge).
"""
