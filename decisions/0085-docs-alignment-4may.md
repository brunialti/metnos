---
id: 0085
title: Allineamento documentazione canonica al 4/5/2026 + sostituzione scena QuickTour
date: 2026-05-04
status: accepted
area: docs
related:
  - 0072  # adaptive re-rank intra-turno
  - 0073  # bench embedding-vs-token
  - 0074  # scheduler gate user-activity
  - 0075  # prefilter primary tools per object
  - 0076  # synth_request short-circuit
  - 0077  # introvertiva quality filters
  - 0078  # http api phase 1
  - 0079  # planner anti-collision + project paths
  - 0080  # step telemetry fine
  - 0081  # web crawler multi-tier
  - 0082  # credenziali cifrate
  - 0083  # multi-user management
  - 0084  # cross-user send vaglio
---

## Context

Il blocco ADR 0072–0084 (4/5/2026) ha portato cambi strutturali che la
documentazione canonica non rifletteva: HTTP API (porta 8770) come nuovo
canale, multi-user con `users.db` host+guest e pairing via `/start`,
crawler web multi-tier con login programmatico, credenziali cifrate
Fernet con scrub turn log, telemetria fine StepLog (5 sotto-componenti),
short-circuit synth_request, filtri qualità introvertiva
deterministici, scheduler gated user-activity, anti-collision guard
PLANNER + GC synth, qualifier `_loc`, primary tools per object esteso
a tutti i 13 oggetti canonici. Pool executor passato da ~26 hand-crafted
a ~46 totali. Il QuickTour aveva inoltre la scena 5 («compleanno di
mamma») che era diventata generica rispetto alle capacità live, e
diversi riferimenti residui a `fetch_urls` (verbo rimosso il 3/5/2026,
ricondotto a `get_urls`).

Vincolo CLAUDE.md §9.1: «Architettura sempre allineata al codice».
Niente backlog «doc da aggiornare poi».

## Decision

### 1. Pagine canoniche aggiornate (15 IT + 15 EN)

Edit chirurgici, non riscritture, su:

- `index.html` — bump da 14 a 15 componenti, da ~26 a ~46 executor, ADR
  registry da 68 a 85, sezione «Stato del sistema» riscritta sul blocco
  ADR 0072–0084, link al nuovo `http_api.html` aggiunto nella tabella
  «Canale verso l'utente».
- `executor.html` — banner aggiornato a 4/5/2026, callout sostituita con
  le novità ADR 0072–0084 (crawler multi-tier, credenziali cifrate,
  qualifier `_loc`, short-circuit synth_request, telemetria fine), tabella
  Seed riscritta sui ~30 handcrafted attuali con i nomi reali del catalog.
- `agent_runtime.html` — banner aggiornato, callout sostituita con le sei
  estensioni 4/5/2026 (adaptive re-rank, PROJECT PATHS + ANTI-COLLISION,
  UTENTI NOTI, telemetria StepLog, scrub credenziali, canale HTTP); il
  callout 1/5 conservato come «storico».
- `synt.html` — callout sostituita con i tre interventi 4/5
  (filtri qualità introvertiva, short-circuit synth_request, GC synth in
  collisione); callout 1/5 come «storica».
- `channel.html` — banner aggiornato, nuovo cap. 9 «HTTP come canale
  alternativo (Phase 1)» con relazione `channel` ↔ `http_api`, multi-user
  e `/start`. Vecchio cap. 9 (limiti) rinominato cap. 10.
- `pairing.html` — banner aggiornato, due nuovi capitoli: cap. 11
  «Multi-user: `users.db` e `/start`» (registry, bootstrap automatico,
  pairing flow `/start <token>`, blocco UTENTI NOTI nel PLANNER) e
  cap. 12 «Login admin web».
- `vaglio.html` — banner aggiornato con tre nuovi hook 4/5/2026
  (`check_cross_user_send`, capability `crawl.recursive`, capability
  `auth.password_storage`).
- `policy.html` — banner aggiornato con due nuove capability e tier-policy
  a tre livelli (default / trusted / owned).
- `observability.html` — banner aggiornato con dashboard `/admin` HTTP
  htmx + uPlot, telemetria fine StepLog.
- `scratchpad.html` — banner aggiornato con scrub credenziali (campi
  `redacted` + `n_redacted_fields`) e telemetria fine.
- `mnestoma.html` / `mnestome.html` — banner aggiornato con scheduler
  gate user-activity (ADR 0074) e filtri qualità introvertiva (ADR 0077).

Pagine non toccate (perché non impattate dal blocco 4/5):
`approval_ux.html`, `mnest.html`, `sandbox.html`, `telos.html`. Il loro
banner riporta ancora 1/5/2026 ma il contenuto resta corretto.

### 2. Pagina nuova `http_api.html` (canonica + simmetrica EN)

Microprogettazione completa: porta 8770, endpoint Phase 1
(`/agent/health`, `/.well-known/metnos.json`, `POST /agent/turn` con SSE,
`/admin/*` con utenti, executors/stats, runs, safety, turns, proposals),
auth (admin key + cookie 7g + Bearer device + LAN trusted), content
negotiation con ETag/304, SSE per turn streaming (eventi
`thinking|progress|final|error`; `tool_call`/`tool_result` rinviati a
Phase 2), stack frontend htmx + Jinja2 + uPlot. Quindicesimo doc
canonico, status `tested`. Smoke live verificato 4/5/2026.

### 3. QuickTour: scena 5 sostituita

**Rimossa**: «Domani è il compleanno di mamma» (1/5/2026, scrittura su
`MEMORY.md` + recurring scheduler). Generica, sovrapposta alla
funzionalità di scheduler già illustrata in altre scene.

**Aggiunta**: «Riassunto giornaliero portale scuola» — multi-user +
login. Cinque passi: setup utente Lucia (guest pairato via `/start`),
setup credenziali con vaglio scrub, recurring task, run quotidiano alle
07:00 con pipeline `login_session → find_urls → filter_entries →
read_urls_html+pdf → group_entries → describe_entries → send_messages`,
gestione cookie scaduto. Bollino «Verificato live il 4/5/2026» perché
le sei capability sono attive (find_urls, read_urls_html, read_urls_pdf,
login_session, send_messages multi-user, recurring_tasks). Replica
simmetrica in EN. SVG di pipeline aggiunto (~360 righe HTML/SVG IT,
analoghe in EN).

Inoltre fix collaterali: `fetch_urls` → `get_urls` in scena 6 (titolo
SVG, nodo step 1, paragrafo «Cosa ha fatto il composer», chip
scene-meta), in linea con la rimozione di `fetch_urls` dal vocab del
3/5/2026 (CLAUDE.md §2.2: HTTP GET = lettura da URL = `get_urls`).
Stesso fix nei due esempi `fetch_urls` di `agent_runtime.html` IT+EN.

Paragrafo introduttivo «Dieci scene» riscritto sia IT sia EN per
rispecchiare la nuova scena 5.

### 4. CLAUDE.md

§1 «Cos'è Metnos»: aggiornata frase «Canale primario: Telegram» in
«Canali primari: Telegram (dialogo) + HTTP porta 8770 (dashboard host,
client Rust, automazioni curl/script)». Bump «ADR registry: 85 entries»
nei riferimenti. Aggiornato l'elenco delle decisioni di runtime (snapshot
4/5/2026): adaptive re-rank, anti-collision guard, project paths,
telemetria fine, scrub credenziali, multi-user.

### 5. Memorie

- Aggiornato `~/.claude/projects/-opt-myclaw/memory/reference_adr_registry.md`
  con ADR 0079–0085 (riassunti di una riga ciascuno).
- Creata `~/.claude/projects/-opt-myclaw/memory/metnos_docs_alignment_4may.md`
  con il riassunto della sessione di allineamento doc.
- Aggiunto pointer in `MEMORY.md`.

## Consequences

- **Documentazione coerente con il codice del 4/5/2026.** Nessuna
  pagina canonica mente sui numeri o cita capacità che non esistono.
- **Nuova pagina canonica** `http_api.html` (IT+EN) e' contattabile
  dall'index, dal banner di `channel.html`, dai banner di `pairing.html`,
  `observability.html`, `scratchpad.html`, `vaglio.html`, `policy.html`.
  Cross-link interni complete.
- **QuickTour piu' rappresentativo dello stato attuale.** La scena
  scuola tocca sei capacita' nuove appena live; e' la migliore
  vetrina del salto del 4/5/2026.
- **Niente sovrastrutture.** Non e' stato refactor del CSS, del
  template di nav, dei verified badge. Edit chirurgici nei banner e
  nei callout di sessione, nel rispetto del principio
  «niente full-rewrite se non strettamente necessario».
- **Open carry-over (non bloccante)**: alcune pagine (approval_ux,
  mnest, sandbox, telos) hanno il banner ancora a 1/5 perche' il
  contenuto non e' stato impattato; quando un loro contenuto cambiera'
  si bumpera' anche il banner. Verified badge della nuova scena 5
  lungo, ma testato manualmente come pipeline live (le sei capacita'
  sono effettivamente attive nel runtime).

## References

- `/opt/myclaw/docs/it/architecture/*.html` (15 pagine + nuova `http_api.html`).
- `/opt/myclaw/docs/en/architecture/*.html` (simmetriche + `http_api.html`).
- `/opt/myclaw/docs/it/Metnos_QuickTour_v1.html` (scena 5 sostituita,
  scena 6 fetch_urls→get_urls).
- `/opt/myclaw/docs/en/Metnos_QuickTour_v1.html` (idem).
- `/opt/myclaw/CLAUDE.md` (§1 + ADR registry).
- `~/.claude/projects/-opt-myclaw/memory/MEMORY.md` (pointer).
- `~/.claude/projects/-opt-myclaw/memory/metnos_docs_alignment_4may.md` (nuova).
- `~/.claude/projects/-opt-myclaw/memory/reference_adr_registry.md` (update).
