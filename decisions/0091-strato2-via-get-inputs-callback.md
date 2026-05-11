---
id: 0091
title: Strato 2 credentials migrato a get_inputs + on_complete callback
date: 2026-05-05
status: accepted
area: runtime, executor, security, ux
related:
  - 0070  # admin → sudoer chain
  - 0078  # http api phase 1
  - 0082  # encrypted credentials store + scrub
  - 0088  # admin exposed to PLANNER
  - 0089  # credentials 3-tier UX (questa ADR rimpiazza il rendering Strato 2)
  - 0090  # get_inputs dialog engine
modifies:
  - 0089  # Strato 2 ora vive su get_inputs + on_complete (non piu' stringa
          #   plain text custom). Strato 1 (extract+store) e Strato 3 (CLI)
          #   restano invariati.
complements:
  - 0090
---

## Context

Sprint 4 maggio 2026, sera. ADR 0089 aveva fissato il pattern «Strato 2:
admin emette `decision="credentials_required"` con stringa `summary` plain
text che il daemon ri-formatta come carta». Sprint successivo (ADR 0090,
4 maggio 2026 stesso giorno) ha introdotto `get_inputs(fmt="auto")` come
primitive declarativo per «chiedi questi campi all'utente». Roberto ha
sperimentato live il flow Strato 2 su HTTP e ha lamentato:

- la carta credentials_required e' una stringa di 6-8 righe plain text;
- niente form HTML, niente input password masked, nessun aiuto al canale
  HTTP per fare un'esperienza decente;
- l'orchestrazione su Telegram funziona (sequenza `user X pwd Y`) ma e'
  asimmetrica rispetto a HTTP, e mantiene un kind ad-hoc nel cap-pending
  registry (`kind="credentials_required"`) che duplica la macchina di
  stato di get_inputs.

Inoltre, ADR 0090 aveva gia' lasciato come carry-over: «se la migrazione
Strato 2 risulta troppo verbose nel PLANNER, valutare un short-circuit nel
runtime che traduce admin → credentials_required in get_inputs
automaticamente». Quel carry-over e' diventato decisione operativa: la
versione PLANNER-driven di ADR 0090 (esempio 6-sexies con get_inputs
chiamato dal PLANNER) richiede al modello medium (Gemma 4 26B) di
collegare due osservazioni in turni successivi, e in test live il PLANNER
non sempre lo faceva (overthinking, surrogati, loop).

Cinque cose sul tavolo:

1. Pattern UX uniforme fra Telegram (sequenza dialogue) e HTTP (form).
2. Niente kind ad-hoc nel cap-pending registry.
3. Niente stringhe plain text di carta nel codice del verb (admin).
4. Determinismo (CLAUDE.md §7.9): nessuna logica nuova nel PLANNER.
5. Niente shim di compatibilita' (CLAUDE.md §7.1, dev pre-1.0).

## Decision

Sostituire il rendering del Strato 2 di ADR 0089 con orchestrazione
runtime-side di `get_inputs` + `on_complete` callback.

### Nuovo decision name in admin: `needs_inputs`

`runtime/verb_unique/admin.py:invoke()` quando rileva il placeholder
`${METNOS_<KIND>_CREDS}` con dominio mancante non emette piu'
`decision="credentials_required"` con `summary` testuale. Emette:

```
{
  ok: true,
  decision: "needs_inputs",
  needs_inputs: {
    title: "Credenziali per cifs_192.168.1.20",
    description: "binding cifs · host 192.168.1.20 · share Public/x · le credenziali saranno cifrate.",
    dialog: [
      {var: "username", prompt: "Username:", schema: {kind: "text"}},
      {var: "password", prompt: "Password:", schema: {kind: "credentials", secret: true}},
    ],
    fmt: "auto",
    on_complete: {
      type: "save_credentials_and_resume",
      credentials_domain: "cifs_192.168.1.20",
      credentials_context: {binding, host, share},
      resume_call: "admin",
      resume_args: {intent, command_proposed, credentials_domain},
    },
  },
  argv, signature: "", approval_required: false, summary: "",
}
```

Il campo `summary` resta vuoto: la UX e' generata dal runtime via
`get_inputs.final_message_hint`.

### Orchestrazione runtime-side

Nuovo modulo `runtime/orchestration.py` (~310 LOC):

- `invoke_get_inputs_internal(*, sender_id, title, description, dialog,
  fmt, on_complete, actor, channel, timeout_s)` — replica del comportamento
  di `executors/get_inputs/get_inputs.py:invoke()` ma vive nel runtime e
  inietta il campo `on_complete` (non visibile al PLANNER) nello state
  persisto del dialogo. Ritorna lo stesso dict shape che get_inputs
  ritornerebbe, con `expandable_caps[0].sender_for_state = sender_id`.

- `process_completion_callback(sender_id, dialog_id, *, actor, channel)` —
  helper centralizzato che:
  1. carica lo state (`dialog_pending.load_pending`);
  2. legge `on_complete`;
  3. dispatcha per `type`:
     - `save_credentials_and_resume`: salva credentials cifrate
       (`credentials.store(domain, {username, password, context})`),
       quindi chiama `loader.invoke_verb_unique(resume_call, **resume_args,
       caller="agent_runtime")` con `actor` corrente; ritorna il `summary`
       del verb come messaggio user-facing.
  4. ritorna sempre una stringa (non None) cosi' il caller la inoltra al
     canale.

- `orchestrate_needs_inputs(obs, *, sender_id, actor, channel)` —
  dispatcher: dato un observation con `decision="needs_inputs"`, costruisce
  i parametri da `obs["needs_inputs"]` e chiama
  `invoke_get_inputs_internal`.

`runtime/agent_runtime.py:run_turn` intercetta `obs.get("decision") ==
"needs_inputs"` per il branch admin, calcola `sender_id =
f"{channel}:{actor}"` o `actor`, chiama `orchestrate_needs_inputs(obs,
...)`, setta `log.final_message = result["final_message_hint"]`,
propaga `expandable_caps` (`kind="get_inputs_response"`) e termina il
turno. Fallback: se l'orchestrazione fallisce (modulo mancante, storage
non scrivibile), il turno emette un messaggio diagnostico con suggerimento
`metnos-cli credentials add` (Strato 3, fallback indipendente).

### Storage del callback

`runtime/dialog_pending.py` schema esteso con campo opzionale
`on_complete: dict | null`. Persiste nel JSON di stato. Niente migrazione
necessaria (i dialoghi vecchi senza il campo restano validi: il branch
legacy in `_on_get_inputs_completed` mantiene il vecchio path
`credentials_domain` finche' tutti i nodi sono aggiornati, poi sara'
rimosso).

### Channel renderers aggiornati

- **Telegram daemon (`runtime/channels/daemon.py`)**:
  - `_on_get_inputs_completed(state, *, actor)` ora delega a
    `process_completion_callback` via `orchestration` se `state` contiene
    `on_complete`. Ritorna il messaggio del callback (carta vaglio admin
    o esito execute) come `str | None`. Il caller invia entrambi:
    `summary` del dialogo (var raccolte, password mascherata) +
    `callback_msg` (output del resume).
  - Rimosso il branch `kind="credentials_required"` dal cap-pending
    consume (era la macchina di stato Strato 2 ad-hoc).
  - `_consume_credentials_required` rimosso (dead code).
  - Quando il dialogo `get_inputs_response` si completa, NON rilancia piu'
    `original_query`: il callback fa direttamente `invoke_verb_unique`
    e ritorna l'esito.

- **HTTP routes (`runtime/http_routes_agent.py`)**:
  - `dialog_submit` dopo aver consumato gli step chiama
    `process_completion_callback` se lo state ha `on_complete`. Il
    completion screen mostra il messaggio del callback (`<pre>` blocco)
    invece del placeholder generico.
  - Rimosso il branch `kind="credentials_required"` da
    `_apply_cap_pending` (era il consume Strato 2 lato HTTP).
  - Aggiunto helper `_escape_html` per escape minimo del completion message.

### PLANNER prompt aggiornato

`Esempio 6-sexies` riscritto in CLAUDE.md §6 form (DEVI/NON DEVI/OK/ERRORE):
il PLANNER chiama UN solo tool (`admin`); la carta UX viene dal runtime
via auto-orchestrazione di get_inputs. NON deve chiamare get_inputs
manualmente per il caso credentials. Esplicita ERRORE l'anti-pattern
«PLANNER chiama get_inputs come step 2 dopo admin needs_inputs».

## Alternatives considered

**(a) PLANNER-driven get_inputs (status quo ADR 0090).** Il PLANNER vede
`decision="credentials_required"` e al turno successivo chiama
`get_inputs(...)` manualmente. Pro: tutto esplicito, niente magia
runtime-side, allineato col pattern «PLANNER orchestra». Con: in test
live con Gemma 4 26B il PLANNER non sempre fa il collegamento (turni
extra, surrogati, loop), e richiede di mantenere due copie del payload
(quella di admin + quella che il PLANNER costruisce). Rifiutato:
affidabilita' < runtime-side per LLM medium.

**(b) Pattern callback registry come ADR 0068 (recurring tasks).** Pro:
si potrebbe registrare `on_complete` come callback Python by name (es.
`save_credentials_and_resume`) e farlo dispatch da una tabella in
`runtime/callbacks_registry.py`. Con: l'attuale forma dichiarativa
(`on_complete: {type, params}`) e' piu' leggibile da un LLM medium che
in futuro potrebbe emettere `needs_inputs` da altri tool, e il dispatch
testuale e' equivalente in termini di sicurezza (il tipo di callback e'
ristretto a una whitelist nel dispatcher). Rifiutato per semplicita':
oggi un solo type esiste, basta if/else.

**(c) Trasformare admin → mini-builtin che fa lui stesso il get_inputs.**
Pro: zero codice runtime-side aggiuntivo. Con: viola la separazione
admin/runtime di ADR 0070 (admin produce decisioni, runtime esegue
orchestrazione). Inoltre il pattern `needs_inputs` e' generalizzabile:
domani un altro verb (set_notification_preferences, configure_dashboard,
collect_user_profile) potra' emettere `needs_inputs` con il proprio
on_complete senza duplicare la logica di orchestrazione. Rifiutato:
the orchestration belongs in the runtime, not in each verb.

**(d) Mantenere il branch credentials_required come fallback.**
Pro: shim retro-compat. Con: viola CLAUDE.md §7.1 (no backward compat in
dev pre-1.0). Il vecchio branch e' codice morto se ADR 0091 e' la nuova
verita'. Rifiutato: rimosso senza shim.

## Consequences

**What this opens.**

- UX uniforme: form HTML standalone per HTTP (con input `type="password"`
  masked nativo dal browser); sequenza dialogue per Telegram; voice come
  stub futuro. Tutto via lo stesso storage e la stessa state machine.
- `needs_inputs` e' un pattern riusabile: qualsiasi verb futuro che
  richieda input strutturati (preferenze utente, configurazione dashboard,
  profilo guest, ecc.) puo' emettere lo stesso decision name + payload e
  il runtime orchestra. Niente plumbing nuovo per ogni use case.
- Tracciabilita' migliore: il dialog state contiene `on_complete` con
  tutti i parametri necessari per il resume; debug post-hoc ricostruisce
  «cosa stava cercando di fare quel turno».

**What this closes.**

- La tentazione di chiedere al PLANNER medium di orchestrare follow-up
  multi-turno per pattern uniformi (l'orchestrazione vive nel runtime).
- Il fanout di `kind=...` nel cap-pending registry: oggi i kind sono
  `cap_expand`, `admin_approval`, `get_inputs_response`. Il vecchio
  `credentials_required` e' rimosso.

**What becomes easier.**

- Aggiungere un nuovo verb che richieda input dichiarativi: una entry
  in admin (o nel verb stesso) emette `decision="needs_inputs"` con
  `on_complete: {type: "<custom>", ...}`; un branch in
  `process_completion_callback` per il nuovo type.
- Test E2E: 3 test ricoprono admin → orchestration → credentials.store +
  resume admin. 10 test per orchestration unit. 3 test per il flow legacy
  ADR 0089 (ora aggiornati a `needs_inputs`).

**What becomes more expensive.**

- Marginalmente: una orchestration round-trip per turno con admin
  needs_inputs (un save_pending + un final_message_hint build → ~5 ms).
- Documentazione: ADR 0091 + esempio 6-sexies aggiornato + commento
  CLAUDE.md §10.6.X.

**Work items spawned.**

- `runtime/verb_unique/admin.py`: ~20 LOC modificati (decision rename,
  payload struct), `_format_credentials_required` /
  `_format_cli_instructions` documentate come fallback. AdminDecision
  estesa con campo `needs_inputs_payload` opzionale.
- `runtime/orchestration.py`: nuovo modulo, ~310 LOC.
- `runtime/agent_runtime.py`: branch admin needs_inputs (~30 LOC),
  esempio 6-sexies riscritto (~20 LOC).
- `runtime/dialog_pending.py`: schema docstring esteso (campo on_complete).
- `runtime/http_routes_agent.py`: dialog_submit chiama
  process_completion_callback, rimosso branch credentials_required da
  _apply_cap_pending, aggiunto _escape_html (~50 LOC delta).
- `runtime/channels/daemon.py`: rimosso _consume_credentials_required +
  branch nel cap-pending consume (~50 LOC), _on_get_inputs_completed
  delega a process_completion_callback (~30 LOC).
- `runtime/tests/test_get_inputs_credentials_flow.py`: 3 test riscritti
  per ADR 0091 (admin needs_inputs, orchestrate, completion+resume).
- `runtime/tests/test_orchestration.py`: 11 test nuovi per il modulo.
- `runtime/tests/test_credentials_3tier_flow.py`: 1 test rinominato
  + 1 test riallineato.

**Carry-over.**

- I dialog state vecchi senza `on_complete` ma con `credentials_domain`
  (legacy ADR 0090 path) sono ancora supportati dal branch fallback in
  `_on_get_inputs_completed`. Da rimuovere quando tutti i nodi avranno
  girato il refactor; oggi nessuno e' deployato in stato vecchio (test
  green su .33).
- Il pattern `needs_inputs` puo' essere emesso da altri verb in futuro:
  carry-over per integrare `request_location_from_user` (regola PLANNER
  §2-quater) come emettitore di `needs_inputs` invece di un kind custom.
- Telegram fallback per dialog form non renderizzabile inline: oggi se
  fmt='form' su Telegram il channel adapter fallback su dialogue. Il
  fallback e' dichiarato in `_decide_fmt`, gia' coperto.

## References

- `runtime/verb_unique/admin.py` (`invoke()`, `AdminDecision`, manifest
  description aggiornata).
- `runtime/orchestration.py` (`invoke_get_inputs_internal`,
  `process_completion_callback`, `orchestrate_needs_inputs`).
- `runtime/agent_runtime.py` (branch admin needs_inputs in `run_turn`,
  esempio 6-sexies in PLANNER_SYSTEM_NATIVE).
- `runtime/dialog_pending.py` (schema esteso con on_complete).
- `runtime/http_routes_agent.py` (dialog_submit + `_escape_html`).
- `runtime/channels/daemon.py` (`_on_get_inputs_completed` delega a
  process_completion_callback; branch credentials_required rimosso).
- `runtime/tests/test_get_inputs_credentials_flow.py` (3 test E2E).
- `runtime/tests/test_orchestration.py` (11 test unit).
- `runtime/tests/test_credentials_3tier_flow.py` (5 test, riallineati).
- ADR 0089 (Strato 2 origine, ora modificata).
- ADR 0090 (get_inputs primitive).
