---
id: 0090
title: get_inputs — dialog engine for declarative user input
date: 2026-05-04
status: accepted
area: runtime, executor, naming, ux
related:
  - 0089  # credentials 3-tier UX (migrated to get_inputs as Strato 2 prompt UI)
  - 0088  # admin exposed to PLANNER (admin → credentials_required → get_inputs)
  - 0086  # indices objects (analogue: get_inputs is the 16th OBJECT, oggetto-strumento)
  - 0078  # HTTP API phase 1 (renderer form lives in /agent/dialog/<id>/form)
  - 0070  # admin → sudoer chain (Strato 2 admin emits credentials_required)
complements:
  - 0089
  - 0088
  - 0086
---

## Context

By 4 May 2026 Metnos had accumulated three or four ad-hoc UX patterns
for *asking the user something mid-turn*:

- **cap-expand prompt** (CLAUDE.md §2.11): «hai 234 mail; rispondi sì
  per allargare il cap a 1000»;
- **admin approval card** (ADR 0088): «ti propongo `mount -t cifs ...`,
  rispondi sì per eseguire»;
- **credentials request** (ADR 0089 Strato 2): «mi servono user e
  password per cifs_192.168.1.20»;
- **location request** (`request_location_from_user`, regola PLANNER
  §2-quater): «mi serve la tua posizione, condividila o scrivila».

Each one was wired with its own bespoke proposal kind in the
cap-pending registry (`kind="admin_approval"`, `"credentials_required"`,
ecc.), its own glue in `channels/daemon.py`, its own UX text. The
accumulation worked but it was about to fan out further: a generic
«ti chiedo X campi compila e mandami» pattern was needed for setup
flows (notification preferences, dashboard targets, recurring tasks)
without coding *yet another* bespoke pending kind.

The pattern is universal: «structured user input, declarative
description, channel-agnostic rendering». Every modern UI toolkit calls
it a *form schema*. Metnos needs the same primitive at the executor
layer so the PLANNER can request user inputs uniformly.

## Decision

Introduce **`get_inputs`** — a single executor canonical that drives a
declarative dialog with the user.

### Vocabulary (16th OBJECT)

Add `inputs` to `OBJECTS` in `runtime/vocab.py` as plural-invariant
oggetto-strumento, parallel to `indices` (ADR 0086) and `signatures`.
Like `indices`, `inputs` is an *abstract instrument*: it does not
itself host data; it carries the values the user supplies in response
to a dialog declaration.

The executor follows the canonical naming `verbo_oggetto`:
`get_inputs(title, dialog=[{var, prompt, schema}, ...], fmt=...)`.

### Args schema

- `title: str` (required, max 80 char) — short headline.
- `description: str | null` — optional second line.
- `dialog: list[dict]` (required, 1..30 step) — each step
  `{var, prompt, schema, optional?, default?}`:
  - `var`: snake_case identifier (becomes a dict key in the output).
  - `prompt`: question text shown to the user (max 200 char).
  - `schema.kind ∈ {text, credentials, yes_no, choice, multi_choice,
    number, date, file_path, location}`.
  - `schema.choices` for `choice`/`multi_choice`.
  - `schema.secret=true` for `credentials` (mask in UI + log).
  - `optional: bool` (default false), `default: any`.
- `fmt: "auto"|"dialogue"|"form"|"voice"` (default `"auto"`):
  - `auto` chooses `form` on HTTP with ≥3 step, `dialogue` otherwise.
  - `dialogue` = sequential chat (Telegram, CLI).
  - `form` = single HTTP form at `/agent/dialog/<id>/form`.
  - `voice` is a stub (degrades to dialogue).
- `actor`, `timeout_s` — optional multi-user / TTL hooks.

### Output

```
{
  ok: bool,
  decision: "input_required" | "completed" | "cancelled",
  dialog_id: str,                # uuid hex16
  step_index: int, step_total: int,
  values: {var: value},          # populated when completed
  fmt: str,
  final_message_hint: str,       # UX card per il channel adapter
  expandable_caps: [{kind: "get_inputs_response", dialog_id, ...}],
}
```

The output dict is `values` (not `entries`) — a controlled semantic
exception parallel to `find_indices_<dom>`: the OBJECT `inputs` is
*l'oggetto-strumento* of the dialog, not a domain corpus. The PLANNER
reads the values as a snapshot at completion time.

### Pattern PLANNER-driven

The PLANNER orchestrates explicitly. When `admin` returns
`decision="credentials_required"` (Strato 2, ADR 0089), the next
PLANNER turn issues `get_inputs(...)` and `final_answer` of the
returned `final_message_hint`. The user replies on the same channel.
The channel daemon (or the HTTP form) parses each response according
to `schema.kind`, advances the state via `dialog_pending`, and on
completion fires `_on_get_inputs_completed` (deterministic side-effect:
e.g. salva credenziali cifrate per `credentials_domain`) and
ri-esegue la query originale. The PLANNER al turno seguente vede le
credenziali salvate e procede con `admin` come da ADR 0089.

### Storage

`runtime/dialog_pending.py` (~150 LOC) implementa `save_pending`,
`load_pending`, `list_pending`, `consume_pending_step`,
`cancel_pending`, `cleanup_expired`. File JSON `0600` in
`~/.local/share/metnos/get_inputs/<sender_id>/<dialog_id>.json`. TTL
default 1 ora, override per dialogo via `timeout_s`.

### Channel renderers

- **Telegram (channels/daemon.py)**: il daemon riconosce il
  `kind="get_inputs_response"` nel cap-pending registry; sequenziale,
  un prompt per turno; parser `parse_step_value()` modulo-level
  deterministico per ogni `kind`.
- **HTTP (http_routes_agent.py)**: tre route nuove,
  `GET /agent/dialog/<id>/form` (render Jinja2 con widget appropriati
  per ogni kind), `POST /agent/dialog/<id>/submit`,
  `GET /agent/dialog/<id>/cancel`.
- **Voice**: stub (degrada a dialogue). Implementazione futura quando
  il canale voice sara' wired (ADR fase 6).

### Determinismo (CLAUDE.md §7.9)

Tutto il critical path e' deterministico:

- la validazione args e' regex + dict lookup;
- il parser di risposta e' switch su `schema.kind`;
- lo storage e' JSON puro;
- l'avanzamento di stato e' un increment di indice;
- l'on-completion side-effect (creds.store) e' una chiamata cifrata
  (Fernet+HKDF) gia' wired in `runtime/credentials.py` (ADR 0082).

Niente LLM nel critical path: il PLANNER LLM e' a monte (decide se
chiamare get_inputs e con quale dialog), ma la macchina di dialogo e'
pure code.

## Alternatives considered

**(a) One bespoke pending kind per UX use case (status quo).** Pro:
massimo controllo, ogni pattern puo' avere stringhe e regole sue. Con:
lock-in di tre/quattro varianti gia' esistenti, fan-out garantito al
prossimo «ti chiedo X». Rifiutato: il pattern e' uniforme di base, la
specializzazione vive nelle stringhe del manifest (description) non
nella plumbing del runtime.

**(b) Generic key/value Q&A senza schema.** Pro: API minima
`ask_user(prompt) -> answer`. Con: niente validation deterministica,
LLM costretto a parsing risposte, niente form HTTP unico (3 step
diventerebbero 3 turni anche su HTTP), niente type safety per il
PLANNER. Rifiutato: violiamo §7.9 (LLM al posto di codice).

**(c) get_inputs come builtin in-runtime (non subprocess).** Pro:
accesso diretto a actor/channel senza env. Con: aggiunge ancora un
case speciale al runtime; il pattern «tutti gli executor sono
subprocess salvo i 4 builtin storici» e' chiaro. Rifiutato: get_inputs
e' un executor handcrafted con manifest.toml firmato, come ogni altro;
la tracciabilita' e' migliore. Il runtime intercetta solo il
`decision="input_required"` per chiudere il turno con la carta UX.

**(d) Tutto via /admin/dialog HTML form (zero CLI).** Rifiutato:
Telegram resta canale primario per uso quotidiano (ADR 0078).

## Consequences

- `OBJECTS` cresce a 16 entry. CLAUDE.md §2.2 e prefilter
  `_OBJECT_PRIMARY_TOOLS` aggiornati.
- Nuovo executor handcrafted firmato in
  `executors/get_inputs/{manifest.toml, get_inputs.py, manifest.toml.sig}`.
- Nuovo modulo runtime `runtime/dialog_pending.py` (~140 LOC) +
  `parse_step_value` modulo-level in `runtime/channels/daemon.py`.
- 3 nuove route HTTP + template `dialog_form.html` (HTML5 widget
  per ogni kind, validation client-side `pattern`/`required`/`min`/`max`).
- Esempio 6-sexies aggiunto al PLANNER prompt (CLAUDE.md §6 stile,
  forma DEVI/NON DEVI/OK/ERRORE).
- 28 test verdi (16 executor unit, 9 dialog_pending, 3 credentials
  flow integration). Pattern PLANNER-driven della migrazione Strato 2
  e' coperto E2E.
- Sprint successivi (carry-over): migrare cap-expand prompt,
  admin_approval card e location request al pattern get_inputs.
  Aggiungere widget HTML5 per `location` (oggi stub: due input
  numerici); persistenza draft del form via `localStorage` lato
  client. Implementare `voice` adapter quando il canale voice sara'
  wired. Se la migrazione Strato 2 risulta troppo verbose nel
  PLANNER, valutare un short-circuit nel runtime che traduce
  `admin → credentials_required` in `get_inputs` automaticamente
  (oggi PLANNER-driven per esplicitabilita' del flow).
