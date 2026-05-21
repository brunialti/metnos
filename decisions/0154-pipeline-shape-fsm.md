---
id: 0154
title: Pipeline shape FSM — invariante universale `E+ (F | A)?` con auto-remediation
date: 2026-05-20
status: proposed
area: runtime | planner | vocab | auto_remediation
related:
  - 0143  # install_on_demand: pattern errore strutturato → runtime remediation
  - 0153  # content-fetch on-demand: prima applicazione del pattern remediation
  - 0150  # multi-tool memoization L2: invariante executor > fast-path
  - 0140  # prefilter modulare: consumer precursor injection (pre-plan)
  - 0092  # multilang: simmetria stretta IT/EN nei prompt
---

## Contesto

Il PLANNER LLM puo' emettere step in ordine illegale rispetto al data-flow:
consumer senza producer upstream, action senza target, step dopo
terminatore. Esempi osservati (sessione 20/5/2026):

- `describe_entries()` come step 1 (no source) &rarr; ricade in
  `request_new_executor` loop &rarr; ERROR finale.
- `delete_files()` senza paths &rarr; esecuzione spuria o silenzio.
- `find_files &rarr; move_files &rarr; describe_entries` &rarr; describe dopo
  terminatore action (pipeline gia' chiusa).

Prima del 20/5/2026, due meccanismi parziali esistevano:

1. **Prefilter consumer precursor** (ADR 0140 / prefilter.py:696-708):
   inietta un producer nel top-K pool quando il verbo emerge come consumer.
   **Limite**: agisce SOLO al pool ranking, NON valida l'output del
   planner. Se il producer e' filtrato come dormant o non scelto dal
   planner, lo step consumer parte senza alimentazione.
2. **Vocab.detect_implicit_actions** (vocab.py:746): rileva intent
   implicito multi-azione. **Limite**: advisory only, planner pu&ograve;
   ignorare.

Mancava un'enforcement runtime universale **derivato dal vocabolario
chiuso** (CLAUDE.md §2.2), non dalla whitelist per nome di executor.

## Decisione

Introdurre un **invariante universale di forma del pipeline**, deterministico,
derivato dal verb prefix via `vocab.ACTIONS` + `vocab.PRODUCER_VERBS`:

```
∀ turno: la sequenza dei suoi step matcha la regex

    E+ (F | A)?

seguita opzionalmente da `final_answer` (sempre lecito come terminatore).
```

Categorie (output-role):

- **E** (produces-out): emette entries riusabili a valle. Include
  `PRODUCER_VERBS = {read, find, list, get}` + i transformer
  `{filter, sort, group, classify, compute, compare, extract}`.
- **F** (formatter-out): presentazione, terminale. `{describe, render}`.
- **A** (action-out): mutazione di stato, terminale, output = metadata
  di esito. `{move, delete, send, share, write, set, create, change,
  order, compress}`.

System pseudo-executors (final_answer, undo_last_turn, request_new_executor,
admin, request_disambiguation_from_user) bypassano la FSM: meta-operazioni
del runtime, fuori dal data-flow.

### FSM deterministico a 3 stati

```
        E              E              F | A
START ─────► CHAIN ─────► CHAIN ─────► TERMINAL
  │            │            │
  │ F | A      │            │ * 
  ▼            ▼            ▼
ERROR        VALID        ERROR
(no source) (post-F|A)   (post-terminator)
```

Implementato in `runtime/pipeline_shape.py` (~150 righe). API:

```python
def category(name: str) -> str:        # E | F | A | ""
def has_literal_source(args: dict) -> bool
def compute_state(history) -> str       # START | CHAIN | TERMINAL
def next_state(state, name, args) -> tuple[str, str | None]
```

L'`has_literal_source` riconosce `from_step:int` o qualunque list-arg
non vuoto come producer implicito. Permette `delete_files(paths=["/x"])`
come pipeline valida 1-step.

### Auto-remediation registry esteso

`runtime/auto_remediation.py` (gia' esistente da ADR 0153, estende):

```python
@dataclass(frozen=True)
class RemediationPlan:
    prereq_tool: Any                 # str | Callable[[obs], str]   ← NEW
    hint_field: Optional[str]         # None = passa intera obs       ← NEW
    arg_builder: Callable[[Any], dict]
    merge_field: str = "entries"
    merge_source: str = "entries"
    skip_retry: bool = False          # NEW: dialog fail-fast
```

Due nuove entry oltre `needs_content_fetch` (0153):

| error_class           | Strategy            | Cascade                                          |
|-----------------------|---------------------|--------------------------------------------------|
| `needs_data_source`   | auto-recoverable    | `intent.object → OBJECT_PRIMARY_TOOLS → find_urls` |
| `needs_action_target` | fail-fast verso user| `get_inputs` dialog (mai cascade esterna)        |

La differenza categoriale: target di mutazione non si inventa. Il dialog
chiude il turno via `needs_inputs` decision (riusa orchestrate_needs_inputs).

### Pre-execution hook in agent_runtime

Un solo punto, dopo `chosen_name = tc.name` (line ~5616). Calcola lo stato
FSM dalla history degli step gia' eseguiti via `compute_state(log.steps)`,
simula `next_state`. Se `ERROR` con error_class noto:

```python
_synth_obs = {
    "ok": False, "error_class": _ps_err,
    "intent_object": intent.object,
    "user_query": ..., "verb": verb_of(chosen_name),
}
_maybe_remediate_obs(_synth_obs, raw_args, chosen_name, ...)
# se remediation succede:
#   needs_data_source -> prereq + retry executor originale (entries arricchite)
#   needs_action_target -> dialog, turno termina con needs_inputs
#   pipeline_already_closed -> final_answer immediato
```

### Planner prompt — regola 0-PRE

In `runtime/prompts/{it,en}/planner/_core.j2`, sezione 0-PRE (compatta,
§6 prescriptive). Insegna al planner il pattern UPSTREAM, cos&iacute; la FSM
runtime diventa safety net raramente attiva:

```
PATTERN: E+ (F | A)? + final_answer
DEVI: aprire il turno con E (o final_answer diretto se saluto).
NON DEVI: piazzare F o A senza E che li alimenti via from_step o literal.
NON DEVI: emettere altri step dopo F o A (pipeline gia' chiusa).
OK: read_messages → describe_entries → final_answer.
OK: delete_files(paths=["/tmp/x"]) → final_answer (literal e' E implicito).
ERRORE: describe_entries() come step 1 senza source.
```

Simmetria stretta EN per ADR 0092.

## Propriet&agrave;

- **Universale per costruzione**: classificazione automatica via verb
  prefix. Qualunque nuovo executor (`xyz_messages`, `summarize_entries`)
  e' categorizzato senza modifiche al codice. Nuovi verbi richiedono
  solo aggiunta in `FORMATTER_OUT_VERBS` / `ACTION_OUT_VERBS` (1 riga in
  vocab.py).
- **Deterministico** (CLAUDE.md §7.9): FSM puro, nessun LLM nella
  decisione. La cascade default usa `OBJECT_PRIMARY_TOOLS` (gia' chiusa).
- **Lang-agnostic**: opera su token canonici post-`intent_extractor`,
  invariante per lingua dell'utente.
- **Future-proof**:
  - Nuovo executor consumer/formatter/action &rarr; nessuna modifica.
  - Nuovo OBJECT in vocab &rarr; aggiornare `OBJECT_PRIMARY_TOOLS` (vincolo
    vocabolario §2.2, gia' obbligatorio).
  - Nuova lingua &rarr; irrilevante.
- **Compatibile con producer chained** (read_files con paths=[],
  read_messages con account=all): la vettorialit&agrave; §2.1 gestisce N=0
  in modo naturale, no remediation per "zero risultati legittimi".
- **Separa dati da metadata** (vedi `runtime/pipeline_shape.py` docstring):
  FSM opera sul data-flow output-role; metadata di esito (ok, error,
  truncated, ok_count) sono ortogonali e gestiti da final_answer in coda.

## Alternative considerate

1. **Whitelist per nome di executor** (`CONSUMER_EXECUTORS = {describe_entries,
   classify_entries, ...}`). Rifiutata: not future-proof, nuovi
   executor sintetizzati richiederebbero update manuale.
2. **Validazione end-of-plan** (planner emette N step, runtime valida
   prima di eseguire). Rifiutata: tardi per remediation (executor
   intermedi gi&agrave; eseguiti); meno reattivo.
3. **LLM-based classification del verbo**. Rifiutata: non deterministico,
   costo per-step ingiustificato.
4. **Regex sulla query utente** (intent-based gating). Rifiutata: bias
   per lingua e per stile lessicale, viola §7.3.

## Tabella mapping error_class &rarr; remediation (aggiornata)

| error_class             | Hint        | Auto-remediation                                |
|-------------------------|-------------|--------------------------------------------------|
| `binary_missing`        | suggested_install | `admin shell` (ADR 0143)                   |
| `needs_content_fetch`   | needs_urls_html | `read_urls_html(urls=hint[:5])` (ADR 0153) |
| `needs_data_source`     | full obs    | dynamic chooser → primary_tool / find_urls (0154) |
| `needs_action_target`   | full obs    | `get_inputs` dialog, skip_retry (0154)            |
| `pipeline_already_closed` | n/a       | force `final_answer` immediato (0154)             |

## Costi

- **Implementation**: 430 righe totali (147 nuovo + 121 hook + 137 registry
  + 42 prompt 0-PRE bilingue).
- **Latency**: ~10ms/step per `compute_state(history)`. Trascurabile.
- **Storage**: zero.
- **Maintenance**: aggiungere 1 riga in vocab quando si aggiungono nuovi
  verbi F/A. Aggiungere 1 entry in `REMEDIATIONS` per nuovo error_class.

## Test

- **Unit FSM**: 8/8 casi (mail summary, lone delete, lone describe,
  greeting, literal target, post-terminal, system pseudo, action chain).
- **Integration runtime**: 3 turni reali:
  - mail regression `read → classify → describe` &rarr; ok
  - lone summary IT — prompt 0-PRE steera planner a `find_urls` (FSM
    safety net non attivata) &rarr; ok
  - explicit search (back-compat) &rarr; ok

## Out of scope

- Speculation/preemption euristica del producer (`runtime/speculation.py`):
  opt-in sperimentale gated da `METNOS_SPECULATION=1`. ADR separato dopo
  bench A/B con dati significativi.
- Refactor di `agent_runtime.py` (7731 righe). Task #9 separato.
- Audit env flag fossili (METNOS_PLANNER_SPLIT, METNOS_GRAMMAR, ecc.).
  Task #11 separato.
- ADR 0155 (planner choice over runtime override): principio architetturale
  collaterale documentato in ADR dedicato.

## Implementation status

Implementato e testato il 20/5/2026 in 4 commit:

- `590a0c5` feat(pipeline_shape): invariante universale `E+ (F|A)?` +
  auto-remediation
- `f50e8ca` fix(memoization): args query-derived sempre placeholder
- `c60c16d` fix(news_summary): default = riassunto
- `31c33ec` refactor(runtime): rimuovi interceptor describe_on_find_urls

Tutti su `main` con il presente ADR a chiusura del ciclo.
