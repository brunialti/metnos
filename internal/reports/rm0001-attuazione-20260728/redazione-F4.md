### F4 — Ricerca personale ed episodi attestati

**Contratti.**

```python
# runtime/user_context/contracts.py — estensione F4 [IPOTESI]
class Purpose(StrEnum):                     # §5.8, valori già fissati dal documento
    PROFILE_INVENTORY = "profile_inventory"; PROFILE_ANSWER = "profile_answer"
    REFERENCE_RESOLUTION = "reference_resolution"; ARGUMENT_DEFAULT = "argument_default"
    EPISODE_RECALL = "episode_recall"; ROUTINE_RESOLUTION = "routine_resolution"
class RetrievalStatus(StrEnum):             # §8.2
    MATCHED="matched"; NO_MATCH="no_match"; CONFLICT="conflict"
    TRUNCATED="truncated"; UNAVAILABLE="unavailable"
class SelectionMode(StrEnum): RANK_PURE="rank_pure"; SOURCE_BALANCED="source_balanced"
class EpisodeOutcome(StrEnum):              # 4 valori reali + pending, vedi Innesti
    SUCCESS="success"; PARTIAL="partial"; ERROR="error"; TIMEOUT="timeout"; PENDING="pending"

@dataclass(frozen=True)
class Citation:  source_kind: str; event_id: str; turn_id: str; observed_at: str
@dataclass(frozen=True)
class RetrievalRequest:
    purpose: Purpose; kinds: tuple[str, ...] = (); slot: str = ""; scope: str = ""
    window: tuple[str, str] | None = None      # (start_iso, end_iso) già risolti
    text: str = ""; max_items: int = 5; text_budget: int = 1200
    selection: SelectionMode = SelectionMode.RANK_PURE; at_iso: str = ""
@dataclass(frozen=True)
class RetrievedItem:
    memory_id: str; kind: str; slot: str; scope: str; text: str; state: str
    observed_at: str; valid_from: str; valid_to: str; source_kind: str
    rank: float; citations: tuple[Citation, ...]
@dataclass(frozen=True)
class RetrievalResult:                      # §2.7 + §8.2 in un solo valore
    status: RetrievalStatus; items: tuple[RetrievedItem, ...]; reason_code: str
    k: int; used: int; available_total: int | None
    truncated: bool; truncated_what: str; text_budget_used: int
    memory_revision: int; prefs_revision: int; deletion_epoch: int
    selection: SelectionMode

# runtime/user_context/retrieval.py [IPOTESI]
class FtsQueryError(ValueError): ...
def compile_fts_query(text: str, *, max_tokens: int = 8) -> str: ...   # puro, no I/O
def search(principal, req: RetrievalRequest) -> RetrievalResult: ...
def current_for_slot(principal, slot: str, scope: str, at_iso: str = "") -> RetrievalResult: ...
def resolve_supersedes(principal, memory_id: str) -> str | None: ...   # deterministico

# runtime/user_context/episodes.py [IPOTESI]
@dataclass(frozen=True)
class EpisodeRef:
    episode_id: str; turn_id: str; observed_at: str; outcome: EpisodeOutcome
    action_shape: tuple[str, ...]; n_items: int; n_mutations: int; n_failures: int
    pending_remote: int; citation_state: str
def record_turn_episode(principal, log) -> str | None: ...        # idempotente per turn_id
def reconcile_remote(turn_id: str, device_id: str) -> bool: ...
def recall_episodes(principal, req: RetrievalRequest) -> RetrievalResult: ...
def read_turn_for_principal(principal, turn_id: str) -> dict | None: ...

# runtime/pipeline_effects.py — funzione pura aggiunta [IPOTESI]
def turn_semantic_outcome(final_kind: str, counts: dict | None) -> str: ...  # success|partial|error
```

**Dati.** [IPOTESI] Tabelle create in `user_memory.sqlite` da questa fase (migrazione additiva della `store.py` di F1).

```sql
-- Indice testuale del SOLO testo ammesso. Tabella autonoma, non external-content:
-- la cancellazione (§6.5 passo 3) resta una DELETE ordinaria dentro la stessa
-- transazione dello store, senza innesti impliciti nel motore SQLite.
CREATE VIRTUAL TABLE memory_fts USING fts5(
    text, memory_id UNINDEXED,
    tokenize="unicode61 remove_diacritics 2");

CREATE TABLE episode_refs (
    id               TEXT PRIMARY KEY,
    owner_user_id    TEXT NOT NULL,
    turn_id          TEXT NOT NULL,
    source_event_id  TEXT NOT NULL,          -- source_events.kind='runtime_outcome' (F1)
    observed_at      TEXT NOT NULL,          -- ISO con offset, da TurnLog.ts_end
    outcome          TEXT NOT NULL CHECK (outcome IN
                       ('success','partial','error','timeout','pending')),
    action_shape     TEXT NOT NULL,          -- soli nomi canonici di executor, '>' separatore
    n_items          INTEGER NOT NULL DEFAULT 0,
    n_mutations      INTEGER NOT NULL DEFAULT 0,
    n_failures       INTEGER NOT NULL DEFAULT 0,
    pending_remote   INTEGER NOT NULL DEFAULT 0,
    citation_state   TEXT NOT NULL CHECK (citation_state IN ('live','archived','expired')),
    authz_revision   INTEGER NOT NULL,
    deletion_epoch   INTEGER NOT NULL,
    created_at       TEXT NOT NULL,
    UNIQUE (owner_user_id, turn_id));
CREATE INDEX idx_episode_owner_time ON episode_refs(owner_user_id, observed_at DESC);
CREATE INDEX idx_episode_owner_outcome ON episode_refs(owner_user_id, outcome, observed_at DESC);

-- relations ANTICIPATA qui e RISTRETTA dal vincolo, non dalla convenzione:
-- F5 amplia il CHECK con 'supports' e 'contradicts' e il vocabolario di decided_by.
CREATE TABLE relations (
    id             TEXT PRIMARY KEY,
    owner_user_id  TEXT NOT NULL,
    kind           TEXT NOT NULL CHECK (kind IN ('supersedes')),
    decided_by     TEXT NOT NULL CHECK (decided_by IN ('deterministic')),
    src_memory_id  TEXT NOT NULL,            -- il nuovo, che supera
    dst_memory_id  TEXT NOT NULL,            -- il superato
    slot           TEXT NOT NULL, scope TEXT NOT NULL,
    observed_at    TEXT NOT NULL, created_at TEXT NOT NULL,
    deletion_epoch INTEGER NOT NULL,
    CHECK (src_memory_id <> dst_memory_id),
    UNIQUE (src_memory_id, dst_memory_id, kind));
CREATE INDEX idx_relations_owner_slot ON relations(owner_user_id, slot, scope, observed_at DESC);
```

**Innesti.**

| Punto | File:riga oggi | Che cosa cambia |
|---|---|---|
| Conteggi autorevoli del turno | `runtime/agent_runtime.py:4997` — `self.effect_counts = pipeline_effect_counts(self.steps)` dentro `TurnLog.write` (`:4981`) [PROVATO] | **estensione**: subito dopo i conteggi, emissione dell'evento `runtime_outcome` e dell'`episode_refs`. `write()` è chiamata più volte per turno (15 siti, es. `:6826`, `:7172` [PROVATO]) → scrittura **idempotente** su `UNIQUE(owner_user_id, turn_id)`, mai append. Principale assente o `owner_user_id` vuoto = nessun episodio, con contatore visibile (invariante 2) |
| Derivazione dell'esito semantico | `runtime/recurring_tasks.py:480` — `_scheduled_turn_outcome(log)` [PROVATO], raggiunto solo dal ramo schedulato | **sostituzione**: la derivazione si sposta in `pipeline_effects.turn_semantic_outcome(final_kind, counts)`, pura; `recurring_tasks` resta adattatore e conserva la convenzione «`None` = success» che il daemon si aspetta (`runtime/scheduler_v2/daemon.py:256` inizializza `status="success"`) [PROVATO] |
| Contatore deterministico | `runtime/pipeline_effects.py:83` — `pipeline_effect_counts` [PROVATO] | **estensione**: nessun secondo contatore. F4 registra `n_items/n_mutations/n_failures` così come sono, angolo cieco `extract_files` compreso e dichiarato (limite già scritto a `runtime/pipeline_effects.py:20-26`) [PROVATO] |
| Lettura del registro turni | `runtime/turn_feedback.py:40` — `_load_turn` scansione lineare che ritorna la **prima** riga corrispondente [PROVATO] | **nuovo modulo** `episodes.read_turn_for_principal`: prende l'**ultima** riga con quel `turn_id` (le riscritture di `write()` producono righe multiple) e verifica `record["owner_user_id"] == principal.owner_user_id`, altrimenti `unavailable/foreign_principal` (accettazione UC-06). `turn_feedback` non viene toccata |
| Conservazione delle citazioni | `runtime/jobs/maintenance_tasks.py:300` — `_turn_logs()` con `METNOS_TURN_LOG_RETENTION_DAYS` 60 e `..._ARCHIVE_RETENTION_DAYS` 365 [PROVATO] | **estensione**: dopo l'archiviazione, `citation_state` passa a `archived` oltre la finestra viva e a `expired` oltre l'archivio. Classe `episodic` di §6.6 quantificata: 60 giorni citabili in linea, 365 con lettura d'archivio, oltre non citabile |
| Esiti remoti tardivi | `runtime/invocations.py:477` `complete_invocation` e `:611` `_close_late_undo` [PROVATO] | **estensione**: un turno con invocazioni remote non chiuse nasce `outcome='pending'`, `pending_remote>0`, e **non** è consultabile come attestato; la chiusura tardiva chiama `episodes.reconcile_remote` che ricalcola l'esito. Senza questo, un episodio dichiarerebbe un esito che il dispositivo smentisce (§2.8) |
| Compilatore FTS | `runtime/prefilter_strategies/fts5.py:80` — `_fts_query` unisce i termini con `OR` senza virgolette [PROVATO] | **riferimento, non riuso**: quel sanificatore è tarato sul richiamo alto del pool strumenti. F4 scrive `compile_fts_query`: soli termini da `\w+`, ciascuno racchiuso fra virgolette con raddoppio delle virgolette interne, unione `AND`, esito **rivalidato** contro `^"[^"]+"( (AND\|OR) "[^"]+")*$` prima del `MATCH`; scarto → `FtsQueryError`, mai testo utente grezzo nella sintassi |
| Isolamento nella ricerca | nessun punto: le tabelle di F1 sono nuove | **nuovo modulo**: unica interrogazione `memory_fts JOIN memories ON …` con `memories.owner_user_id = ?` e stato ammesso nella stessa `WHERE`. Nessuna funzione pubblica accetta `user_id` libero (invariante 3) |
| Tempo esatto | `runtime/time_window_parser.py:367` — `parse_time_window(spec, now=None) -> (start_iso, end_iso)`, `ValueError` su spec non riconosciuta [PROVATO] | **riuso senza modifiche**: «ieri», «questa settimana», `last-7d` risolti qui prima della ricerca. Spec non riconosciuta = `unavailable/time_window_invalid`, mai finestra indovinata |
| Cancellazione | §6.5 passo 3, journal di F1 | **estensione**: la cancellazione elimina anche `memory_fts`, `episode_refs` e `relations` del principale nella stessa transazione; la rilettura negativa del passo 8 interroga esplicitamente le tre tabelle nuove |
| Confine chat | `UserContextBoundary` di F2 | **estensione**: due purpose nuovi (`episode_recall`, `profile_answer`) instradati a `retrieval`/`episodes`. Nessun executor, nessun oggetto `memories` nel vocabolario |
| Messaggi utente | `runtime/messages.py:35` — `get(code, **kwargs)` [PROVATO] | **estensione**: chiavi `MSG_UC_*` per elenco citato, astensione e troncamento, seme IT+EN (§7.13) |
| Osservatore W1 | `runtime/engine/dispatch.py:6195` — `record_observation` + `seed_from_run` [PROVATO] | **invariato**: F4 non aggiunge un secondo osservatore. Nulla di personale entra in `observations`, che non ha colonna di principale [PROVATO `runtime/engine/autopath.py:125-139`] |

**Ordine di costruzione.**

1. `turn_semantic_outcome` puro in `pipeline_effects` + adattatore in `recurring_tasks`. **Verifica**: la suite schedulata esistente resta verde e una prova di equivalenza confronta vecchia e nuova derivazione su tutti i `TurnLog` di un giorno reale; una divergenza fa fallire il passo.
2. Migrazione additiva delle tre tabelle in `store.py` + estensione della cancellazione. **Verifica**: creazione idempotente su store esistente; dopo un oblio, `SELECT count(*)` su `memory_fts`, `episode_refs`, `relations` per quel principale vale 0.
3. `compile_fts_query` e `search` con lookup esatto (chiave, ambito, tempo) prima dell'FTS, secondo l'ordine di §8.1. **Verifica**: prova di proprietà su 10 000 stringhe generate — l'esito compilato o supera la rivalidazione o solleva; nessun `OperationalError` di SQLite raggiunge il chiamante.
4. `resolve_supersedes` e `current_for_slot`. **Verifica**: due dichiarazioni sullo stesso `(slot, scope)` con valore diverso producono una riga `supersedes` e una sola memoria corrente; due dichiarazioni con lo **stesso** valore non producono alcuna riga (`supports` è di F5) e nessuna eccezione.
5. Emissione dell'episodio nel punto di strozzatura + riconciliazione remota. **Verifica**: dieci `write()` ripetute dello stesso turno lasciano una sola riga; un turno con invocazione remota aperta è `pending` e non compare in `recall_episodes` finché non è chiuso.
6. `recall_episodes` e `read_turn_for_principal` con conservazione delle citazioni. **Verifica**: un episodio il cui turno è oltre la finestra viva risponde `unavailable/turn_log_expired`; non risponde mai con l'episodio senza il turno.
7. Selezione: graduatoria pura e variante equilibrata per sorgente, a parità di `k`. **Verifica**: a parità di `k` le due varianti restituiscono esattamente `k` elementi e la registrazione riporta `k`, `used`, `available_total` e budget testuale per ogni caso (contratto di §11.2).
8. Confronto A/B sul corpus congelato e decisione del predefinito. **Verifica**: la variante equilibrata entra come predefinito solo se riduce la ridondanza senza perdere evidenze necessarie; in caso contrario resta la graduatoria pura e il risultato negativo viene registrato.

**Interruttore.** `METNOS_USER_CONTEXT_F4`, valore predefinito `0` (spenta). Spenta restano attivi: W2, il confine chat di F2 con ricorda/correggi/dimentica/cosa-sai sulle chiavi tipizzate, i riferimenti e i default di F3, il lookup esatto per chiave e ambito, la cancellazione con journal. Spenta **non** avviene: emissione di episodi, sincronizzazione dell'indice testuale, ricerca testuale, calcolo di `supersedes`. Le chiamate ai purpose `episode_recall` e `profile_answer` rispondono `unavailable` con `reason_code="phase_disabled"`, mai elenco vuoto presentato come risposta. All'accensione l'indice testuale è **ricostruito** dalle memorie attive e l'accensione fallisce se il conteggio dell'indice non coincide con quello delle memorie indicizzabili: un indice stantio non è un difetto tollerabile.

**Prove.**

- *Unitarie*: termini con virgolette, apici, `*`, `^`, `NEAR`, `MATCH`, parentesi e due punti non alterano la sintassi compilata; una stringa di soli separatori solleva invece di produrre una ricerca vuota; `last-3d` e «dal 12/3 al 15/3» danno lo stesso intervallo del risolutore canonico; conteggi con esito parziale (`ok=False` e `ok_count>0`) producono `partial`, non `error`.
- *Integrazione*: UC-05 — decisione su un `(slot, scope)` tipizzato, decisione successiva contraria, richiesta «secondo la decisione presa» che restituisce la corrente con data, ambito e turno d'origine e la superata marcata; UC-06 — «che cosa abbiamo fatto ieri sui rapporti» che cita turni reali con conteggi; UC-08 — «quali decisioni abbiamo preso questa settimana» che restituisce un elenco deterministico citato, con troncamento dichiarato quando il budget si esaurisce.
- *Avversariali*: memoria di un altro principale con lo stesso testo, mai restituita né contata in `available_total`; richiesta che contiene sintassi FTS ostile; episodio richiesto per un `turn_id` che appartiene a un altro proprietario; testo di una pagina o di un allegato che imita una decisione (respinto dal filtro d'origine, non dall'indice); esca di §11.1 con profilo irrilevante che non deve produrre alcun recupero.
- *Iniezione di guasto*: indice testuale troncato o corrotto a mano → `unavailable/fts_desync`, mai risultati parziali silenziosi; interruzione fra emissione dell'episodio e commit dello store; blocco del file dello store durante una ricerca; cancellazione concorrente con una ricerca in corso; turno sorgente rimosso dall'archivio mentre l'episodio esiste; revoca del principale fra emissione e consultazione.
- *Turno reale* (§8.5): una richiesta «che cosa abbiamo fatto ieri» su `/agent/turn` HTTP e la stessa su Telegram, con esito citato e `turn_id` verificabile nel registro; più un turno di controllo su richiesta irrilevante che dimostra piano e strumenti identici con la fase accesa e spenta (§5.10).

**Fuori da questa fase.**

- **Dipendenza da una fase successiva, dichiarata**: `runtime/user_context/source_events.py` è assegnato a F5 in §13, ma F4 deve scrivere eventi `kind='runtime_outcome'`. F4 costruisce **solo** l'accodamento autenticato di quel tipo, senza filtro né codici di scarto; l'accodamento di `user_clause`, il filtro di §7.2 e i codici di scarto restano F5. Se questo non viene scritto, F4 non è chiudibile.
- `supports`, `contradicts`, relazioni proposte dal modello e ogni transizione su chiave libera: F5. F4 amplia il vincolo `CHECK` solo allora.
- Il compositore locale delle risposte aggregate: **fuori per decisione ratificata**. UC-08 risponde con elenco deterministico citato. Si riapre soltanto con questo criterio, non prima: almeno **3 casi** documentati del corpus congelato (banda di rumore misurata = 1 caso) in cui le fonti recuperate contengono la risposta ma l'elenco entro budget produce astensione o viene giudicato incompleto da due annotatori indipendenti secondo §11.4 — cioè il difetto è nella presentazione e non nel recupero — con ADR separata e misura di danno preregistrata.
- Recupero denso, RRF, comunità e grafo: fuori da RM-0001 (§3.2), riesaminabili solo se il corpus congelato mostra lacune che esatto+FTS non risolvono.
- Riscrittura canonica prima di L0 e `routine_bindings`: F6. F4 non tocca né `fastpath` né `autopath`.
- Apprendimento implicito, promozione silenziosa e compilatore in ombra: F5. F4 indicizza solo ciò che F1 e F2 hanno già scritto in modo esplicito.