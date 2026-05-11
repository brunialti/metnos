---
id: 0083
title: Multi-user management — host + guest registry, channel binding, pairing
date: 2026-05-04
status: accepted
area: runtime, executors, http_api
related:
  - 0078  # http api phase 1
  - 0079  # planner anti-collision + project paths config (stesso pattern di prompt)
  - 0084  # cross-user send vaglio
complements:
  - 0079
---

## Context

Il modello "host + guest" di Metnos (memoria `metnos_host_guest_model.md`,
27/4/2026) era stato impostato come **fondamento minimo**: campo `actor`
nei record runtime, lazy auto-resolve in `actor_resolver.py`, default
`host` per single-user. Niente registro persistente di utenti logici,
niente mapping name → recipient_id, niente UI di gestione.

Lo sprint del 4/5/2026 chiude il giro: **Roberto vuole creare degli user
guest (es. familiari) e indirizzarli per nome dal canale primario**.
Caso d'uso target:

> "Ogni mattina alle 7 cerca novita' su classeviva.it (mode auth) e manda
>  il riassunto su Telegram a **Lucia**."

Il PLANNER deve risolvere `Lucia` → `user_id` → telegram `chat_id` e
inviare. Pattern multi-recipient ammesso ("manda a Lucia e a Marco"). I
guest devono potersi pairare via `/start <token>` su Telegram, senza che
l'host generi codici firmati Ed25519 ad-hoc per ognuno (flusso `pairing.py`
e' troppo formale per l'UX di un familiare).

## Decision

### 1. Modello dati `users.db` (`runtime/users.py`)

SQLite in `~/.local/share/metnos/users.db`. Due tabelle:

```sql
CREATE TABLE users (
  id TEXT PRIMARY KEY,             -- uuid hex 16 char
  name TEXT NOT NULL UNIQUE,       -- handle leggibile, lowercase, [a-z0-9_]
  display_name TEXT,
  role TEXT NOT NULL,              -- 'host' | 'guest'
  owner_user_id TEXT,              -- FK users.id, NULL per host
  autonomy_level TEXT NOT NULL,    -- 'read_only' | 'restricted' | 'full'
  created_at TEXT NOT NULL,
  notes TEXT
);
CREATE TABLE user_channels (
  user_id TEXT NOT NULL,           -- FK users.id
  channel TEXT NOT NULL,           -- 'telegram' | 'mail' | 'http'
  recipient_id TEXT NOT NULL,      -- chat_id Telegram | email | device_id
  verified_at TEXT,                -- NULL = pairing pending
  pairing_token TEXT,
  pairing_expires_at TEXT,
  PRIMARY KEY (user_id, channel)
);
CREATE INDEX idx_channels_recipient ON user_channels(channel, recipient_id);
```

Distinzione vs `pairing.py`:
- `pairing.py` mappa `(channel, sender_id) → autonomy + actor_string`
  (canale-specifico).
- `users.py` mappa **user logici** (host + guest) → uno o piu' canali.
  Sa di chi e' "Lucia" indipendentemente dal canale.

### 2. Bootstrap automatico

`init_db()` idempotente: se non esistono utenti, crea l'host con
`name = $USER` (lowercased, normalizzato) o `host` come fallback.
`autonomy_level='full'`, no owner. Single-host policy: a livello
applicativo `create_user(role='host')` solleva se gia' esiste un host.

`autobind_host_telegram(default_chat_id)`: il telegram daemon chiama al
primo poll che riconosce il `default_chat_id` di config. Se non ancora
bindato, autobinda l'host con `verified=True`. Idempotente.

### 3. Pairing flow `/start <token>`

`issue_pairing_token(user_id, channel, ttl_s=3600) → token` emette un
secret hex urlsafe 32 char con scadenza. Memorizzato in
`user_channels.pairing_token` (sovrascrive il binding precedente: il
flusso `pair` riparte).

`consume_pairing_token(channel, recipient_id, token) → user_dict`
verifica + binda. Solleva `ValueError` per token sconosciuti (caso del
secondo uso: dopo successo il token viene azzerato), scaduti, o
canale errato.

Telegram daemon (`runtime/channels/daemon.py`) intercetta `/start TOKEN`:
- accettato anche da chat_id NON pairati (come `/pair`),
- chiama `users.consume_pairing_token('telegram', chat_id, token)`,
- in caso di successo sincronizza un row in `pairings.db` cosi' i turni
  successivi passano i check classici (autonomy + actor_resolver):
  `autonomy='Full'` per host, `'Supervised'` per guest. `actor` = name.
- chat_id bindati in users.db ma senza row in pairings.db: handler
  `handle_message` crea on-the-fly il pairing (fallback multi-user).

### 4. Pairing lifecycle distinto da `/pair`

`/pair` (Ed25519 firmato) resta per dispositivi/canali tecnici.
`/start <token>` e' UX user-friendly per familiari: il token e' un
secret short-lived emesso dall'admin UI, niente firma a chiave pubblica.

### 5. Lookup + resolve

`find_user_by_recipient(channel, recipient_id) → user|None`: reverse
lookup, considera solo channel `verified_at IS NOT NULL`.

`resolve_recipients(targets: list[str], channel) → list[dict]`:
mappa una lista di target (mix di id/name/`@<id>` direct chat_id) a
struttura `{user, recipient_id, error}`. Best-effort: errori tracciati
nel campo `error`, niente exception.

### 6. Send_messages multi-user

`executors/send_messages` v0.4.0 estende il contratto:

- nuovi argomenti top-level (e per-message override): `to_user`
  (str|list), `via_channel` (`telegram`|`mail`|`http`|`auto`),
  `actor` (propagato da run_turn).
- back-compat MAIL classico: `to=email|list` continua a funzionare,
  apertura SMTP solo se serve (deferred).
- multi-recipient per messaggio: `to_user=["lucia","marco","@9999"]`
  → 3 send dispatch separati (auto-resolve canale per user, vaglio
  cross-user per ognuno).
- output `results[].channel` (`mail` | `telegram`) +
  `recipient_user_id` + `recipient_name` + `recipient_id`.

Backend telegram: `TelegramChannel.send_to(chat_id, OutboundMessage)`
(thin wrapper esplicito di `send`).

### 7. PLANNER prompt — UTENTI NOTI

`agent_runtime.py::_render_users_known_block()` itera
`users.list_users()` e produce una sezione iniettata nel
`PLANNER_SYSTEM_NATIVE` subito dopo PROJECT PATHS:

```
UTENTI NOTI (per `to_user` di send_messages)
  - roberto (host, autonomy=full) — telegram OK
  - lucia (guest, owner=roberto, autonomy=restricted) — telegram OK
  - marco (guest, owner=roberto, autonomy=restricted) — telegram pending

DEVI: quando l'utente nomina un destinatario per nome (es. "manda a
  Lucia"), risolvi via `to_user="lucia"` di send_messages.
NON DEVI: indovinare chat_id letterali, email o handle.
OK: "manda riassunto a Lucia" →
  send_messages(messages=[{"to_user":"lucia","body":"..."}]).
ERRORE: "manda riassunto a Lucia" →
  send_messages(messages=[{"to":"lucia@somewhere","body":"..."}]).
  E' UN ERRORE.
```

Pattern equivalente a PROJECT PATHS (ADR 0079): dato deterministico
iniettato nel prompt al load del modulo, modifica = restart del runtime.

### 8. Admin UI — `/admin/users`

`runtime/http_routes_admin.py` aggiunge 7 rotte:

| path | metodo | scopo |
|---|---|---|
| `/admin/users` | GET | tabella HTML/JSON di tutti gli user |
| `/admin/users` | POST | crea user da form (default owner = host) |
| `/admin/users/{id}` | GET | dettaglio HTML/JSON |
| `/admin/users/{id}/delete` | POST | elimina (cascade su channels) |
| `/admin/users/{id}/autonomy` | POST | cambia autonomy_level |
| `/admin/users/{id}/channels/{channel}/pair` | POST | issue token + istruzioni |
| `/admin/users/{id}/channels/{channel}/remove` | POST | scollega canale |

Templates Jinja2: `users.html`, `user_detail.html`, `user_pair.html`.
Nav link aggiunto in `base.html`. Sezione "Utenti" nel `dashboard.html`
con count host/guest e ultimi 5 pairati.

Ruolo richiesto: `admin` (policy gia' applicata da `auth_middleware`).

## Lifecycle esempio

1. **Roberto admin UI**: `POST /admin/users` con `name=lucia` → user
   creato con role=guest, autonomy=restricted, owner=roberto.
2. **Roberto**: `POST /admin/users/<id>/channels/telegram/pair` → token
   `a1b2c3...` valido 1h.
3. **Roberto via DM Telegram (al guest)**: "Lucia, scrivi al bot
   `/start a1b2c3...`".
4. **Lucia**: invia `/start a1b2c3...` al bot Metnos. Daemon chiama
   `consume_pairing_token`, sincronizza pairings.db, risponde "Sei stato
   pairato come lucia (guest). Benvenuto/a in Metnos."
5. **Roberto in chat**: "manda buongiorno a Lucia". PLANNER vede
   `to_user="lucia"` nel pool UTENTI NOTI → `send_messages(messages=
   [{"to_user":"lucia","body":"buongiorno"}])`. Executor risolve
   chat_id 1001, invia.

## Consequences

- **Persistenza dello stato user**: prima il modello viveva
  esclusivamente nei pairing per-channel + actor string. Ora c'e' un
  registro persistente con id stabile per la persona.
- **UX di pairing per familiari**: niente codici Ed25519 firmati,
  l'admin emette token monouso dal browser, il guest manda `/start
  TOKEN` come comando standard di Telegram.
- **Mantiene bilanciamento "parti largo, restringi se serve"** (memoria
  host_guest_model): nessun TELOS/USER duplicato, niente workspace per
  user, niente mnestoma personale. Solo ANAGRAFICA + binding canali.
- **PLANNER deterministico per resolve nomi**: il blocco UTENTI NOTI
  iniettato nel prompt evita LLM-guessing del chat_id (CLAUDE.md §7.9).
- **Test verificati 4/5/2026**:
  - `tests/test_users.py` — 10 test (bootstrap, create+get, name unique,
    channel binding, pairing token verify, expiry, double-use, cascade,
    autobind idempotenza, resolve mixed).
  - `tests/test_telegram_pairing.py` — 3 test (`/start` valid token,
    expired, double-use second fails).
  - `tests/test_send_messages_multiuser.py` — 5 test (host telegram,
    single guest, mixed list, vaglio cross-user, user inesistente).
  - `tests/test_http_server.py` — 3 nuovi test (admin users list/create/
    pair, invalid name).

## Aperti

- **Audit cross-user send**: ADR 0084 introduce hook minimale; il
  registry persistente di "chi ha tentato cosa" su risorse altrui
  resta da definire (probabilmente un nuovo audit log per actor).
- **GUI Telegram per il guest** (self-service): il guest non ha modo
  oggi di vedere i propri canali pairati o revocare. Ammissibile MVP:
  l'host gestisce tutto via `/admin/users`. Da promuovere quando il
  numero dei guest reali supera 1-2.
- **Autonomy fine-grained per capability**: oggi i tre livelli
  (`read_only`/`restricted`/`full`) sono opachi rispetto alla
  capability dell'executor. Quando si attaccheranno autorizzazioni
  per-capability (mail:send, fs:write, ...) il pairing diventera'
  piu' espressivo.
- **Migration dei pairing pre-esistenti**: i pairings.db che hanno
  `actor='host'` lazy-resolved restano validi; non c'e' migration
  forzata a users.db. Se Roberto non crea mai l'host esplicito,
  `init_db()` lo bootstrappa al primo `users.list_users()`.

## References

- `runtime/users.py` (~440 righe): modulo nuovo.
- `runtime/channels/telegram.py` (`send_to`): wrapper esplicito.
- `runtime/channels/daemon.py` (`_handle_start_command`, multi-user
  fallback in `handle_message`).
- `executors/send_messages/send_messages.py` v0.4.0 + manifest.toml v0.4.0.
- `runtime/agent_runtime.py` (`_render_users_known_block`).
- `runtime/http_routes_admin.py` (7 rotte `/admin/users/*`).
- `runtime/templates/{users,user_detail,user_pair}.html` + nav update
  in `base.html` + sezione "Utenti" in `dashboard.html`.
- `runtime/vaglio.py` (`check_cross_user_send`, vedi ADR 0084).
- Memorie: `metnos_host_guest_model.md` (27/4/2026 fondamento),
  `metnos_users_management_4may.md` (questo sprint).
