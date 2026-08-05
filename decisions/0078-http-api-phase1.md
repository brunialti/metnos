---
id: 0078
title: HTTP API Phase 1 — server amministrativo + canale agent uniforme
date: 2026-05-04
status: accepted
area: runtime, channels, admin
related:
  - 0066  # synth executors in user data dir
  - 0067  # introvertiva MVP + events.turn_id refactor
  - 0071  # safety signatures storage
related_external:
  - /opt/giorgio2/interfaces/voice_server.py
  - /opt/suprastructure/gateway/server.py
---

## Context

Fino al 4/5/2026 Metnos espone un solo server HTTP (`runtime.agent_server`,
porta 8765) limitato al pairing dei device remoti. Ogni sguardo al catalog,
alle proposte introvertiva, allo scheduler e alla safety store passava da
CLI o da accessi diretti al SQLite. Senza un canale HTTP user-facing
mancavano: dashboard amministrativa, esecuzione `run_turn` da web,
discovery del nodo, integrazione con il client Rust per turn remoti.

I codebase paralleli `giorgio2.interfaces.voice_server` e
`suprastructure.gateway.server` mostrano lo stile da replicare: aiohttp
bare, niente decorator-routing, helper `_error()`, ROUTES come tuple,
middleware funzionale.

## Decision

Aggiungere un secondo server HTTP, `runtime.metnos_http_server`, in
ascolto su porta `8770` (separata da 8765 di pairing), che espone la
HTTP API Phase 1.

### Endpoint Phase 1

| metodo | path | role | scopo |
|--------|------|------|-------|
| GET  | `/agent/health`            | anonymous | liveness + uptime |
| GET  | `/.well-known/metnos.json` | anonymous | discovery (name, channels, capabilities, fingerprint) |
| POST | `/agent/turn`              | user/admin | esecuzione `run_turn`; SSE se `Accept: text/event-stream`, altrimenti JSON |
| GET  | `/agent/devices/me`        | user      | info device chiamante |
| GET  | `/admin`                   | admin     | dashboard root HTML |
| GET  | `/admin/proposals`         | admin     | tabella proposte introvertiva (filtro `kind`) |
| POST | `/admin/proposals/{sig_key}/{approve\|reject\|defer}` | admin | azione su singola proposta |
| GET  | `/admin/executors`         | admin     | catalog con lifecycle |
| GET  | `/admin/executors/stats`   | admin     | counts + daily events per uPlot |
| GET  | `/admin/runs`              | admin     | scheduler runs |
| GET  | `/admin/safety`            | admin     | safety signatures |
| GET  | `/admin/turns`             | admin     | ultimi N turn (jsonl turns) |

### Autenticazione

Tre ruoli: `anonymous` < `user` < `admin`.

- **Admin key**: file `~/.config/metnos/admin.key` (mode 0600), 256-bit hex
  (`secrets.token_hex(32)`), auto-generata al primo boot. Nei log compare
  solo il fingerprint sha256 (primi 16 hex).
- **Device pairing token**: il middleware confronta il `Bearer` con la
  `public_key_b64` di ogni device pairato (tabella `devices`) — match →
  ruolo `user`.
- **LAN trusted**: 127.0.0.0/8, 192.168.0.0/16, 10.0.0.0/8 → `user` di
  default (solo se nessun Bearer e' stato presentato e fallito).
- **Anonymous whitelist**: `/agent/health`, `/agent/register`,
  `/.well-known/*`. Ogni altra path esige almeno `user`.
- **Admin gate**: path che inizia con `/admin/` esige ruolo `admin`,
  altrimenti 403.

### Content negotiation + ETag

Le rotte di collezione (`/admin/proposals`, `/admin/executors`,
`/admin/runs`, `/admin/safety`, `/admin/turns`) sono *negotiation-driven*:
`Accept: text/html` → fragment Jinja2; default JSON. In entrambi i casi
viene calcolato un `ETag` (sha256 del payload, primi 16 hex). Se il
chiamante ripresenta `If-None-Match` con lo stesso valore, la risposta
e' `304 Not Modified` senza body.

### SSE su `/agent/turn`

Quando `Accept: text/event-stream`, l'handler apre uno stream e installa
un `_SSEProgress` (implementa l'interfaccia `runtime.progress.Progress`)
che il `run_turn` invoca a ogni step. Eventi: `thinking`, `progress`,
`tool_call`, `tool_result`, `final`, `error`. Il `run_turn` gira in
thread executor (sync) mentre la coroutine main pompa gli eventi sul
loop tramite `asyncio.run_coroutine_threadsafe`.

### Stack frontend

htmx + Jinja2 + uPlot via CDN, niente build step. Template compatti
(< 50 righe) sotto `runtime/templates/`. Stile pulito system-ui, niente
framework CSS.

## Consequences

- **File creati** (`runtime/`, ~1100 LOC totali):
  `metnos_http_server.py`, `http_auth.py`, `http_render.py`,
  `http_routes_agent.py`, `http_routes_admin.py`, 8 template Jinja,
  `tests/test_http_server.py` (12 test).
- **Test**: 12/12 verdi via `AioHTTPTestCase` (no dipendenze esterne
  oltre aiohttp e jinja2 gia' installati).
- **Smoke live verificato**:
  - `GET /agent/health` → 200 `{ok:true, version:"1.1", uptime_s:...}`.
  - `GET /.well-known/metnos.json` → 200 con fingerprint admin key.
  - `GET /admin` (no auth, da loopback) → 403.
  - `GET /admin` (Bearer admin key) → 200 HTML dashboard.
  - `If-None-Match` round-trip → 304.
- **Integrazione**: nessun cambio al protocollo `agent_server` (8765
  pairing), nessun cambio a `run_turn`. Phase 1 e' additivo.
- **Single-instance gate**: lockfile flock POSIX in
  `~/.local/state/metnos/http_server.lock` (stesso pattern di
  `agent_server`).

## Open

- **TLS**: porta 8770 in chiaro per ora (bind 127.0.0.1 di default).
  Phase 2: cert self-signed pin-by-fingerprint, riuso del materiale di
  pairing.
- **HTTPChannel**: `/agent/turn` chiama `run_turn` con `channel="http"`
  ma non registra un Channel formale nel `channels.daemon` (modello
  poll-based incompatibile con request/response).
- **Eventi SSE granulari**: oggi emettiamo `thinking|progress|final|error`
  via Progress. Il run_turn non emette ancora `tool_call` /
  `tool_result` espliciti — Phase 2 estendera' l'interfaccia Progress
  con questi callback.
- **AppKey warnings**: aiohttp 3.13 raccomanda `web.AppKey` invece di
  string keys. Refactor cosmetico, non bloccante.

## References

- `runtime/metnos_http_server.py` (entrypoint, factory, lock).
- `runtime/http_auth.py` (admin key + middleware).
- `runtime/http_render.py` (Accept negotiation + ETag).
- `runtime/http_routes_agent.py` (SSE, well-known, devices/me).
- `runtime/http_routes_admin.py` (collezioni read-only + actions).
- `runtime/templates/` (8 file Jinja, htmx + uPlot CDN).
- `tests/runtime/http/test_http_server.py` (12 test, AioHTTPTestCase).
- ADR 0066 (synth executors path, riferito da catalog provider).
- Pattern reference: `/opt/giorgio2/interfaces/voice_server.py`,
  `/opt/suprastructure/gateway/server.py`.
