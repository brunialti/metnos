---
id: 0130
title: Backend tree allineato agli OBJECTS canonici §2.2 + provider per-file
date: 2026-05-14
status: accepted
area: runtime | naming | backends
related:
  - 0078  # HTTP API + dispatcher canonical pattern
  - 0083  # multi-user messaging via_channel
  - 0123  # skill importer agentskills.io
  - 0128  # importer verb boundary
complements:
  - 0083
---

## Context

Il refactor 13/5/2026 ha introdotto "plugin areas" deterministiche
(`runtime/backends/<dominio>/<provider>.py`) per separare dispatcher
canonical da backend implementation:

- `backends/calendar/local_ics.py`
- `backends/calendar/google_workspace.py`  (14/5/2026)
- `backends/messaging/email_metnos.py`
- `backends/messaging/telegram_bot.py`
- `backends/files/local.py`
- `backends/web/{httpx_default,playwright_stub}.py`

Il nome "dominio" era una scelta libera (`calendar`/`messaging`/`web`),
non allineata al vocabolario canonico §2.2 dei 17 OBJECTS
(`events`/`messages`/`urls`/`files`/...).

Roberto (14/5/2026): «mi sembra piu' coerente allineare ai nomi
oggetto».

## Decision

Cartelle `runtime/backends/<OBJECT>/<provider>.py`. OBJECT preso 1:1 da
`vocab.OBJECTS` (§2.2). Rinomine eseguite:

| Pre-refactor          | Post-refactor (canonical) |
|-----------------------|---------------------------|
| `backends/calendar/`  | `backends/events/`        |
| `backends/messaging/` | `backends/messages/`      |
| `backends/web/`       | `backends/urls/`          |
| `backends/files/`     | `backends/files/` (gia' canonical) |

I file dei provider mantengono il loro nome (NOT renamed):
`local_ics.py`, `google_workspace.py`, `email_metnos.py`,
`telegram_bot.py`, `httpx_default.py`, `playwright_stub.py`.

## Motivazione

1. **Coerenza naming**: il dispatcher canonical `<verb>_<object>.py`
   cerca naturalmente i suoi backend in `backends/<object>/`. Esempio:
   `create_events.py` → `from backends.events import local_ics`.
   La regola «directory = object» elimina la traduzione mentale
   `calendar↔events`, `messaging↔messages`.

2. **Lookup deterministico**: dato il nome di un executor canonical
   `<verb>_<object>(_qualifier)?`, il path del backend e' derivato
   meccanicamente. Tooling (test, doc, IDE) puo' navigare senza
   mappa esplicita.

3. **Disambigua plurale**: i 17 OBJECTS §2.2 sono PLURALI («events»,
   non «event»). Il pattern «directory PLURAL OBJECT» previene
   regressione (es. nessuno scrive `backends/event/` o
   `backends/message/`).

4. **Pattern A vs B** (utente, 14/5/2026): la struttura `backends/
   <object>/<provider>.py` (A) e' preferita su `backends/<provider>/
   <object>.py` (B) perche':
   - Il dispatcher canonical importa 1 cartella: `from
     backends.events import local_ics, google_workspace`.
   - L'oggetto §2.2 e' la radice deterministica del dominio.
   - Helper provider-shared (es. OAuth google_workspace) restano
     nello skill agentskills.io
     (`~/.local/share/metnos/skills/google-workspace/`), non nel
     runtime backends/, quindi B non offre vantaggi compilativi.

## Convenzione futura

- Aggiungere un provider → drop file in `backends/<object>/<provider>.py`
  (no registry magico).
- Aggiungere un nuovo object §2.2 → creare `backends/<object>/` solo se
  qualche provider lo implementa.
- Plugin esterni (ADR pending): scan
  `~/.local/share/metnos/plugins/<provider>-*/<object>.py` con manifest
  che dichiari `[(object, provider), ...]`. Trust gate enabled +
  consent_token, precedenza builtin > plugin.

## Implementation

- `git mv` per le 3 directory (calendar/messaging/web).
- sed-replace su 19 file Python: `backends.calendar→backends.events`,
  `backends.messaging→backends.messages`, `backends.web→backends.urls`.
- Re-firma di 10 executor (modificati nei loro `.py`).
- Test mirati: 141 PASS / 0 FAIL.
- Convergenza live propose+create+notify 3/3 OK (Google Calendar
  scrittura confermata, eventi visibili nel calendar utente).

## Effetto collaterale

Nuovo backend `backends/events/google_workspace.py` introdotto
contestualmente: chiude il gap «Metnos creava eventi su Google
Calendar prima del refactor backends» (utente, 14/5/2026). Default
auto-detect: `_default_client()` ritorna `google_workspace` se
`~/.local/share/metnos/skills/google-workspace/google_token.json`
esiste, altrimenti `local` (local_ics).

Backend gemelli aggiunti contestualmente:
- `backends/messages/gmail_google_workspace.py` (send/read/find/delete
  via Gmail API; richiede `Mail service enabled` sull'account Google).
- `backends/files/google_workspace.py` (find/read/write/delete/share
  + create_dirs/find_dirs/delete_dirs via Drive API).

## Open

- Plugin esterni (`~/.local/share/metnos/plugins/`): ADR pending.
  Manifest + trust gate + scan dinamico.
- Backend `dirs/` separato vs riuso `backends/files/google_workspace.py`
  (oggi tutte le ops dirs sono dentro `files/google_workspace.py` con
  funzioni `create_dirs/find_dirs/delete_dirs`). Mantenuto cosi'
  perche' Drive considera folder = file con MIME folder; nessun
  vantaggio nel separare. Local `files/local.py` invece ha
  `create_dirs/find_dirs/delete_dirs` come funzioni distinte.

## References

- `runtime/backends/{events,messages,urls,files}/`
- `executors/{create,read,delete,find_events_empty,send_messages,
  read_messages,find_files,read_files,write_files,find_dirs,
  create_dirs,delete_dirs}/`
- `runtime/skill_wrapper.py::_run_api`
