---
id: 0181
title: Chat-driven placement — «esegui sul mio PC» dalla chat
date: 2026-07-04
status: accepted
area: runtime
related:
  - 0011  # architettura client/server asimmetrica
  - 0034  # placement 3 livelli (choose_placement)
  - 0046  # client Rust + runtime remoto
modifies:
  - 0034  # L1.c (override device) reso raggiungibile dal turno reale
---

# Chat-driven placement — «esegui sul mio PC» dalla chat

## Contesto

Gli executor remoti (ADR 0011/0034/0046) permettono di eseguire un executor su
un PC appaiato (client Rust). Lo scopo dichiarato dall'utente: **parlare alla
chat** e far eseguire l'operazione sul PC giusto — non via endpoint di test
(`/admin/devices/{id}/test-invoke`), ma dalla conversazione naturale.

Il motore `placement.choose_placement` (ADR 0034) già instradava per nome device
(livello L1.c, `intent["device"]`) e controllava la connessione (L1.d,
`is_available` → `ERR_DEVICE_UNREACHABLE`). MA l'unico chiamante nel turno reale
(`agent_runtime.invoke_executor`) attivava il blocco SOLO per manifest
`scope="device"` (nessuno in produzione) e passava `intent=None`: il percorso
«l'utente ha nominato un device» era **codice morto**.

## Decisione

### Identificazione del PC = NOME device (dato), non origine di rete
Il PC bersaglio si ricava dal **nome del device** citato nella query, abbinato ai
nomi REALI dei device appaiati dell'utente. Scartate due alternative:
- **IP di connessione**: fragile — via il tunnel pubblico il server vede
  `127.0.0.1`, e da Telegram non c'è alcun PC dietro il messaggio.
- **LLM / liste di sinonimi**: viola §7.9 (deterministico > LLM) e la regola
  anti-contaminazione (un abbinamento a un dato curato, non lessico nel prompt).

Il nome è abbinato SOLO se preceduto da una **preposizione locativa**
(`su|sul|…|on`): «sul portatile-ufficio» instrada, il nome nudo in mezzo a una
frase no (una foto «di casa» non deve finire sul device chiamato «casa»).
Marcatori «locale» («su questo pc / il mio pc / localmente») risolvono al device
dell'utente (uno → quello; più → chiede). Marcatore «server» riporta a `.33`.

### Destinazione appiccicosa
Default = **ultima destinazione** usata nella chat (per `sender_id`); primo turno
= server; reset esplicito con «sul server / qui». Così i turni di seguito
(«…ora comprimila») restano sullo stesso PC senza ripeterlo.

### Controllo di connessione SEMPRE, errore onesto
Il target risolto (esplicito O appiccicoso) è verificato con `is_available`
(battito < 60s, non revocato) PRIMA dell'invio. Offline → «device non connesso»
(`ERR_DEVICE_UNREACHABLE`), MAI un fallback silenzioso sul server (§2.8).

### Prod-safe per costruzione
Senza riferimento a un PC (né appiccicoso) il resolver ritorna `server` e il
turno gira su `.33` con path IDENTICO a prima. Il resolver è best-effort: se
solleva, il turno prosegue locale (mai bloccare).

## Implementazione (R1)

- `runtime/target_device.py` — resolver puro `resolve_target(query, devices,
  last_target, is_available)` → SERVER | device_id | `ambiguous` | `unreachable`
  + `cleaned_query` (adjunct di destinazione rimosso).
- `runtime/chat_target_store.py` — destinazione appiccicosa per `sender_id`
  (sqlite `chat_target.db`, co-locato con `devices.db`).
- `runtime/agent_runtime.py`:
  - `invoke_executor(..., target_device=None)`: hook SGANCIATO (`scope=="device"`
    OPPURE `target_device` presente), passa `{"device": nome}` a
    `choose_placement`.
  - `_try_engine_v2`: risolve il target, gestisce `unreachable`/`ambiguous` con
    ritorno onesto, usa `cleaned_query` per il dispatch, aggiorna l'appiccicoso
    solo su riferimento esplicito, tagga `📍 <nome>` nel `final_message` + campo
    strutturato `target_device` (propagato al `TurnLog` e alla risposta HTTP).

Validazione: unit 16/16; turno REALE su device Windows fisico
(«…sul PC-ROBERTO» → eseguito sul PC); suite 3306 pass; prod-safe.

## Conseguenze / follow-up

- **Owner-filter multi-utente**: oggi si usano tutti i device non-revocati
  (`owner='host'`, mono-utente). In multi-utente va filtrato per
  `owner_user_id == actor` — altrimenti un utente potrebbe nominare il device di
  un altro. **Da fare prima di aprire a più utenti.**
- **Ambiguo → form**: R1 risponde con testo che chiede il nome; un form
  get_inputs con i candidati (forced_device sul resume) è migliore UX (§2.11).
- **Copertura executor (C7)**: bundlabili al device oggi solo get_files/
  compute_files_loc/list_dirs; find/read (R2) e mutanti (R3) = fasi successive.
- Report di dettaglio + assessment: `internal/reports/chat_driven_placement_R1_assessment.md`.
