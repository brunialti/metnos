---
id: 0131
title: Centralizzazione dei segreti in `runtime.credentials` (Fernet store)
date: 2026-05-14
status: accepted
area: runtime | credentials | security
related:
  - 0089  # credentials UX 3 strati (Fernet+HKDF)
  - 0083  # multi-user messaging
complements:
  - 0089
---

## Context

I segreti SMTP/IMAP per Migadu, register.it e tiscali risiedevano in
file `.env` plain-text sotto `~/.config/metnos/mail.env`,
`~/.config/mykleos/mail.env`, `~/.config/metnos/mail/<account>.env`.

ADR 0089 (10/5/2026) ha introdotto `runtime/credentials.py` come single
store cifrato (Fernet + HKDF + admin.key). I file `.env` sono rimasti
fuori dallo store per back-compat.

Roberto (14/5/2026): «tutti i dati sensibili dovrebbero essere storati,
come oggi su .env credo, in un unico punto».

## Decision

`runtime.credentials` e' la sorgente di verita' unica per i segreti
SMTP/IMAP. Domain convention: `smtp_<account>` (es.
`smtp_metnos_system`, `smtp_metnos_roberto`, `smtp_mykleos`,
`smtp_knowcastle`, `smtp_tiscali`).

Payload schema:

```json
{
  "user":       "metnos@metnos.com",
  "password":   "...",
  "imap_host":  "imap.migadu.com",
  "imap_port":  993,
  "smtp_host":  "smtp.migadu.com",
  "smtp_port":  465,
  "verify_tls": true
}
```

`mail_client._account_creds(account)` ORA tenta prima
`credentials.load("smtp_<account>")` (Layer 1 cifrato). Se mancante,
fallback ai file `.env` legacy per back-compat durante la migrazione
(Layer 2). I file `.env` possono essere eliminati manualmente quando
la migrazione e' verificata.

## Implementation

- `runtime/mail_client.py::_load_from_credentials_store(account)` helper.
- `runtime/credentials_migrate.py` CLI one-shot: legge file legacy,
  scrive nello store. Idempotente (skip se gia' presente).

```bash
python3 -m credentials_migrate --dry-run   # verifica
python3 -m credentials_migrate             # applica
```

Output sessione 14/5/2026:
```
[OK] created  smtp_metnos_system     user=metnos@metnos.com
[OK] created  smtp_metnos_roberto    user=roberto.brunialti@metnos.com
[OK] created  smtp_mykleos           user=mykleos@knowcastle.com
[OK] created  smtp_knowcastle        user=roberto.brunialti@knowcastle.com
[OK] created  smtp_tiscali           user=roberto_brunialti@tiscali.it
```

5/5 account migrati. Store: `~/.local/share/metnos/credentials/` (mode
0700; entries Fernet 0600).

## Estensione 14/5/2026 — API keys + Telegram bot + Google Maps

Audit di `~/.config/metnos/credentials.env` (671B) ha rivelato altri 4
segreti high-value in plaintext:

| File / Variabile | Domain store | Sensibilita' |
|---|---|---|
| `ANTHROPIC_API_KEY` | `anthropic_api_key` | ALTA (billing Claude) |
| `OPENAI_API_KEY` | `openai_api_key` | ALTA (billing OpenAI) |
| `TELEGRAM_BOT_TOKEN` | `telegram_bot_token` | MEDIA-ALTA (bot intero) |
| `TELEGRAM_CHAT_ID` (587627005) | `telegram_chat_id_host` | BASSA (chat id pubblico) |
| `~/.config/metnos/google_maps.env` GOOGLE_MAPS_API_KEY | `google_maps_api_key` | ALTA (billing Places) |

Payload schema (uniforme per API keys):
```json
{"value": "<secret>", "_env_var": "<ENV_NAME_ORIGINALE>"}
```

Consumer aggiornati a 3-layer (env → store → file legacy):
- `runtime/llm_provider.py::_read_anthropic_key()` / `_read_openai_key()`
  (nuovo helper `_read_api_key_from_store(domain)`).
- `runtime/channels/telegram.py::TelegramChannel.__init__` + helper
  `_read_from_store()` per `(token, chat_id)`.
- `runtime/google_places_client.py::_load_api_key()`.

CLI esteso: `python3 -m credentials_migrate --apis` (solo API keys),
`--all` (SMTP + APIs). Sessione 14/5: 5/5 SMTP + 5/5 APIs migrati.

## Classificazione user/system (direttiva 14/5/2026)

L'utente ha posto: «in `~/.local/` le credenziali user-bound, le system
in `/opt/myclaw/` (root del codebase Metnos)».

Per ora: store single-user in `~/.local/share/metnos/credentials/`
(coerente con `~/.local` user-bound). Quando arrivera' multi-user
(Phase 7), split fisico:

| Categoria | Domain | Location futura |
|---|---|---|
| User-bound | `telegram_chat_id_host`, `smtp_metnos_roberto`, `smtp_knowcastle`, `smtp_tiscali` | `~/.local/share/metnos/credentials/` |
| System | `anthropic_api_key`, `openai_api_key`, `google_maps_api_key`, `telegram_bot_token`, `smtp_metnos_system`, `smtp_mykleos` | path system-wide TBD (richiede root-owned dir + service uid) |

Oggi la distinzione e' SOLO semantica (nei nomi domain). Lo split fisico
e' future work.

## Open

- **Google OAuth token** (`~/.local/share/metnos/skills/google-workspace/
  google_token.json`): resta nello skill scope (`agentskills.io` import,
  ADR 0123). Il refresh-token e' gestito da `google_api.py` direttamente.
  Migrarlo nel credentials store richiederebbe fork dello skill (out-of-
  scope ADR 0131). Considerare quando si stabilizza il pattern «skill
  external_managed credentials» (vedi ADR 0132 plugin esterni).
- **Eliminazione file .env legacy**: lasciata manuale al user. Il
  fallback resta attivo per back-compat. Quando confermato il
  funzionamento, eseguire `mv ~/.config/metnos/{mail,credentials,
  google_maps}.env{,.bak}` ecc.
- **Caching**: `credentials.load()` legge da disco ogni volta. Per
  high-frequency callsite considerare LRU cache se diventa hot.
- **Split user/system fisico**: location system-wide TBD (es.
  `/var/lib/metnos/credentials/` con uid dedicato, oppure
  `~/.local/share/metnos/credentials/system/`). Pending Phase 7.

## References

- `runtime/mail_client.py::_load_from_credentials_store`
- `runtime/credentials_migrate.py`
- `runtime/credentials.py` (Fernet+HKDF store)
