### F6 — Routine personali e comprensione ellittica

**Contratti.** Un solo modulo nuovo, `runtime/user_context/routines.py`, che non importa planner, HTTP, Telegram né executor (direzione delle dipendenze §13).

```python
# runtime/user_context/routines.py — [IPOTESI] tutto ciò che segue
class RoutineState(str, Enum):
    CANDIDATE = "candidate"; ACTIVE = "active"
    CONFLICT = "conflict";   REVOKED = "revoked"

class RewriteOutcome(str, Enum):          # reason code, mai prosa
    REWRITTEN = "rewritten"; NO_MATCH = "no_match"
    AMBIGUOUS = "ambiguous"               # ≥2 candidati entro il margine → astensione
    NEEDS_VALUES = "needs_values"         # la forma non è chiudibile senza valori → astensione
    DISABLED = "disabled"; UNAVAILABLE = "unavailable"

@dataclass(frozen=True)
class RoutineStep:                        # SOLO vocabolario chiuso §2.2
    verb: str; object: str

@dataclass(frozen=True)
class RoutineBinding:
    routine_id: str; principal_id: str
    steps: tuple[RoutineStep, ...]
    slot_names: tuple[str, ...]           # nomi di argomento, MAI valori
    context_sig: str                      # sha256 di soli enum e classi d'oggetto
    state: RoutineState
    n_success: int; last_success_at: str
    revision: int; expires_at: str

@dataclass(frozen=True)
class CanonicalRewrite:
    outcome: RewriteOutcome
    routine_id: str = ""
    canonical_query: str = ""             # «find files -> compress files -> create messages»
    original_query: str = ""
    snapshot_revision: str = ""           # UserContextSnapshot F0: memory_revision+deletion_epoch
    candidates: tuple[str, ...] = ()      # routine_id in gioco, per l'audit dell'astensione

def canonical_text(steps, slot_names) -> str: ...          # puro, deterministico, testabile da solo
def resolve_routine(query, *, principal, snapshot, catalog_names) -> CanonicalRewrite: ...
def compile_routines(*, principal_id: str, now: str) -> dict: ...   # passo notturno, legge W1
def revoke_routines(*, principal_id: str, routine_ids) -> int: ...  # chiamato dall'oblio F1
```

**Dati.** In `user_memory.sqlite` (store F1), tabella prevista da §6.2 più una tabella di legame all'evidenza, che §6.2 non elenca ma che l'invariante 16 impone: un vincolo `routine_id → source_events.event_id` reali, non un conteggio.

```sql
-- [IPOTESI] F6
CREATE TABLE routine_bindings (
  routine_id       TEXT PRIMARY KEY,
  principal_id     TEXT NOT NULL,
  shape_hash       TEXT NOT NULL,           -- sha256(steps || slot_names || context_sig)
  steps_json       TEXT NOT NULL,           -- [{"verb":..,"object":..}] vocabolario chiuso
  slot_names_json  TEXT NOT NULL,           -- ["project_folder","recipient"] — nomi soltanto
  context_sig      TEXT NOT NULL,
  state            TEXT NOT NULL CHECK (state IN
                     ('candidate','active','conflict','revoked')),
  n_success        INTEGER NOT NULL DEFAULT 0,
  last_success_at  TEXT NOT NULL,
  revision         INTEGER NOT NULL DEFAULT 1,
  created_at       TEXT NOT NULL,
  expires_at       TEXT NOT NULL,           -- classe `behavioral` §6.6
  deletion_epoch_at_compile INTEGER NOT NULL
);
CREATE UNIQUE INDEX rb_shape ON routine_bindings(principal_id, shape_hash);
CREATE INDEX rb_pick ON routine_bindings(principal_id, state, last_success_at DESC);

CREATE TABLE routine_evidence (             -- invariante 16: evidenza reale, non conteggio
  routine_id  TEXT NOT NULL REFERENCES routine_bindings(routine_id) ON DELETE CASCADE,
  event_id    TEXT NOT NULL,                -- source_events.kind='runtime_outcome' (F4)
  turn_id     TEXT NOT NULL,
  PRIMARY KEY (routine_id, event_id)
);
```

Nessuna colonna contiene percorsi, destinatari, conti o testo dell'utente: la sola prosa ammessa sono nomi di verbo, oggetto e argomento.

**Innesti.**

| Punto | File:riga oggi | Che cosa cambia |
|---|---|---|
| Sostituzione della richiesta prima della pianificazione | `runtime/agent_runtime.py:6936` `_query_for_planning = _tr.cleaned_query or user_query_for_run` [PROVATO] | **Estensione**: dopo il blocco di destinazione, una seconda sostituzione condizionata `_query_for_planning = rw.canonical_query` quando `rw.outcome is REWRITTEN`. Stessa figura architetturale già in produzione (§5.7 la richiede, non la inventa). |
| Copertura dei tre consumatori | `runtime/agent_runtime.py:6952`, `:7094`, `:7142` [PROVATO] | **Nessuna modifica**, ma la riscrittura DEVE stare a monte di tutti e tre (scorciatoia lessicale, motore, ramo con allegati): un innesto dentro `_run_engine` lascerebbe scoperto il ramo `_eng_up_res`. |
| Estrazione dell'intento | `runtime/agent_runtime.py:6002` `intent_raw = extract_intent(query, _llm_call_fast)` dentro `_run_engine` (def a `:5917`) [PROVATO] | **Nessuna modifica.** `query` qui È già `_query_for_planning`: l'intento, e quindi la firma L1, si calcolano sulla forma canonica senza toccare nulla. *Correzione al dossier, che dava l'intento estratto dalla query originale.* |
| Chiave L0 | `runtime/engine/fastpath.py:220` `lookup(query)` → `cluster.py:107 normalize_hash` [PROVATO] | **Nessuna modifica al modulo cache.** La chiave diventa quella della forma povera per costruzione. È la prova che F6 non tocca la struttura condivisa. |
| Rete di sicurezza sui letterali | `runtime/engine/dispatch.py:5889` `_ungrounded_mutating_args(fp_hit.framework, query)` [PROVATO] | **Nessuna modifica**, ma va documentata: un piano L0 con uno step mutante a valore letterale non è servibile sotto una forma canonica povera (i token non compaiono) → rifiuto e ripianificazione. Le routine servibili sono quelle i cui slot viaggiano per `from_step`/segnaposto. |
| Osservazione W1 | `runtime/engine/dispatch.py:6197` `_ap.record_observation(...)` e `:6204` `_ap.seed_from_run(...)` [PROVATO] | **Nessuna modifica: niente secondo osservatore.** F6 è un lettore di `observations`/`autopaths`. |
| Legame forma→principale | `runtime/engine/autopath.py:122-137`: `observations` non ha colonna di soggetto [PROVATO] | **Nessuna modifica.** Il principale arriva dagli eventi `runtime_outcome` di F4, congiunti per `turn_id`. Non si aggiunge una colonna a una tabella condivisa (invariante 8). |
| Riconoscimento della richiesta ellittica | `runtime/compound_decomposer.py:95` `split_query_chunks`, `:337` `detect_chunk_action` [PROVATO] | **Riuso in sola lettura** per le azioni esplicite; gli intervalli di clausola arrivano da F0, che estende la stessa funzione. |
| Forme IT/EN | `runtime/detection_lexicon_seed.py:51` `R = _dl.register`, blocco `tutor_gate.*` a `:589` [PROVATO] | **Estensione**: nuovo blocco `routine.*` (radice flessiva, coppia it/en) sullo stesso modello. Nessuna lista di frasi in Python. |
| Compilatore notturno | `runtime/nightly_orchestrator.py:43` `"learning_loop_review"`; `runtime/jobs/maintenance_tasks.py:142` `task_learning_loop_review` [PROVATO] | **Nuovo passo** `user_routines_compile` in `NIGHTLY_SEQUENCE` subito dopo, con lo stesso schema (idempotente, additivo, mai LLM) e la stessa politica di scadenza già usata per i semi in ombra (`METNOS_SHADOW_TTL_DAYS`, predefinito 21 [PROVATO `maintenance_tasks.py:153`]). |
| Validità delle cache | `runtime/engine/cache_validity.py:100` `ROUTING_EPOCH = "2026-07-10.6"` [PROVATO] | **Sostituzione del valore** all'accensione: la riscrittura cambia la scelta-tool per una classe di richieste, la convenzione dichiarata al `:85` lo impone. Senza il salto, i piani anteriori restano serviti. |
| Audit del turno | `runtime/agent_runtime.py:3899` campo `canonical_query` di `TurnLog` (ADR 0149, sottoprodotto del planner) [PROVATO] | **Nuovi campi distinti** `routine_id` e `routine_canonical`: riusare `canonical_query` sovrapporrebbe due nozioni diverse nello stesso registro. `runtime_ctx` (`agent_runtime.py:6249`) porta `routine_id`; `user_query_raw` resta l'originale, come già fa per la destinazione. |
| Oblio | journal e cancellazione di F1 | **Nuovo consumatore**: il passo 3 della cancellazione (§6.5) chiama `revoke_routines`; una routine sopravvissuta a un «dimentica» è il gemello semantico vietato dalla decisione 1. |

**Ordine di costruzione.**

1. **Lettore di forme, in sola lettura.** Congiunge gli eventi `runtime_outcome=success` con `observations.framework_json` per `turn_id` e proietta ogni piano in `(steps, slot_names, context_sig)`. *Verifica*: su un campione di piani reali il proiettore o produce una forma priva di valori, o rifiuta; il conteggio dei rifiuti e la loro causa sono nel rapporto. Fallisce in modo visibile se il turno sorgente non porta un proprietario risolvibile — oggi accade per costruzione su Telegram [PROVATO `runtime/channels/daemon.py:1785-1788`: `run_turn` senza `owner_user_id`].
2. **Funzioni pure `canonical_text` e `context_sig`.** *Verifica*: property test su piani generati — nessuna stringa in uscita compare in `CONTENT_ARG_KEYS` [PROVATO `runtime/engine/executor.py:1262-1292`]; la forma è stabile per permutazione degli argomenti e cambia per permutazione degli step.
3. **Tabelle e compilatore notturno.** Promozione a `active` solo con `n_success >= METNOS_ROUTINE_MIN_SUCCESS` e forma unica per principale; forma doppia → `conflict`, mai scelta arbitraria. *Verifica*: due esecuzioni consecutive del passo notturno non cambiano una riga (idempotenza) e `deletion_epoch_at_compile` arretrato rispetto all'epoch autorevole impedisce la promozione (§6.5).
4. **Riconoscitore e selezione.** Concetti `routine.*` nel lessico; selezione per azioni e oggetti espliciti, compatibilità di `context_sig`, recenza, margine fissato in F0. *Verifica*: sulle trappole del corpus, due candidati entro il margine producono `AMBIGUOUS` e nessuna riscrittura; zero candidati producono `NO_MATCH`. L'astensione è un esito riportato, non un silenzio.
5. **Innesto in `agent_runtime`, interruttore spento.** *Verifica*: con l'interruttore spento il turno è byte-identico a prima su tutto il corpus (stesso `framework_hash`, stesso `match_source`).
6. **Audit e prova sulle cache.** Originale, `routine_id`, forma canonica e revisione dell'istantanea nel registro del turno. *Verifica*: due principali con default diversi che producono la stessa forma canonica condividono la riga L0 e ricevono argomenti finali diversi; una lettura diretta di `fastpaths` e `observations` dopo il corpus non contiene alcun valore personale.
7. **Accensione.** Salto di `ROUTING_EPOCH`, corpus congelato, turno reale. *Verifica*: braccio 3 contro baseline lineare sulla metrica primaria preregistrata; danni bloccanti a zero con denominatore dichiarato (§11.3).

**Interruttore.** `METNOS_USER_ROUTINES`, predefinito `0`. Spenta: `resolve_routine` non viene mai chiamato (guardia prima dell'importazione, `_query_for_planning` resta quello prodotto dalla destinazione a `agent_runtime.py:6936`); il passo notturno esce con `{"skipped": "disabled"}` senza aprire il database; nessuna riga di `routine_bindings` viene letta o scritta. Restano integre F0 (principale, istantanea), F1 (store e oblio), F2 (confine chat), F3 (riempimento dell'argomento dopo il piano), F4 (ricerca ed episodi) e F5 (compilatore): nessun modulo di quelle fasi importa `routines`, la dipendenza è a senso unico.

**Prove.**

- *Unitarie*: forma canonica priva di valori a partire da un piano con percorso, destinatario e conto letterali; forma stabile per riordino degli argomenti; `context_sig` che cambia quando cambia una classe d'oggetto e non quando cambia un valore; astensione con due forme a pari margine.
- *Integrazione*: richiesta ellittica del proprietario con una sola routine attiva, con due, con nessuna; stessa richiesta da un ospite senza routine (nessuna riscrittura); revoca della routine dentro un «dimentica» e nuova richiesta ellittica che ricade sul motore.
- *Avversariali*: pagina o messaggio che contiene la frase-marcatore di routine (l'origine esterna non produce riscrittura, invariante 4); routine che nomina uno slot corrispondente a un argomento citato in una clausola `when` di capacità (rifiutata alla compilazione, invarianti 5 e 11); richiesta che nomina esplicitamente un valore diverso da quello della routine (la precedenza resta al turno corrente, invariante 6); due principali sulla stessa forma canonica con cache L0 calda (nessuna contaminazione).
- *Iniezione di guasto*: lessico non coperto nella lingua dell'istanza; `observations` svuotata fra compilazione e uso; epoch di cancellazione arretrato durante il passo notturno; catalogo cambiato dopo la compilazione (la firma di validità invalida il piano, non la routine); interruttore spento a metà corpus con comportamento della fase precedente invariato (§11.5).
- *Turno reale §8.5*: uno su `/agent/turn` HTTP con la richiesta ellittica e uno su Telegram; per Telegram l'esito atteso è l'astensione motivata finché F0 non rende obbligatorio il proprietario del turno, e va riportato come tale, non come successo.

**Fuori da questa fase.**

- Il legame fra una forma di piano e la persona nasce dagli eventi `runtime_outcome` e dai puntatori d'episodio: **appartengono a F4**. Senza F4 certificata, F6 non ha come attribuire un successo a un principale e non è chiudibile — è la stessa trappola che ha reso F4 non chiudibile con `relations` in F5, e va detta prima, non scoperta dopo.
- Principale canonico, `owner_user_id` obbligatorio su tutti i canali, intervalli di clausola e margine numerico della selezione: **F0**. Finché il proprietario del turno resta facoltativo, le routine sono spente su Telegram per fallimento chiuso, non per scelta.
- Il riempimento degli slot con valori personali dopo il piano, e la sezione firmata che lo autorizza, sono **F3**: F6 produce nomi di slot e si ferma lì.
- Store, journal di cancellazione e quarantena del ripristino sono **F1**; F6 ne è soltanto un consumatore in scrittura per la revoca.
- La promozione automatica di una routine senza conferma, e la politica del proprietario che la consente, sono **F5**: in F6 una routine nasce `candidate` e diventa `active` per soglia deterministica dichiarata, non per apprendimento.
- Un compositore che spieghi a parole quale routine è stata scelta: fuori dal nucleo per decisione ratificata 2; l'audit qui è un elenco di identificativi e forme, non prosa generata.