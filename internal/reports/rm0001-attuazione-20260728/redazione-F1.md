### F1 — Store esatto e oblio in laboratorio

**Contratti.**

```python
# runtime/user_context/contracts.py — il file nasce in F0; F1 vi aggiunge questi tipi [IPOTESI]
class SourceKind(StrEnum):   USER_CLAUSE; INTERACTION_CHOICE; RUNTIME_OUTCOME; CORRECTION
class MemoryState(StrEnum):  OBSERVED; CANDIDATE; ACTIVE_READ; ACTIVE_DEFAULT; CONFLICT
                             REJECTED; SUPERSEDED; EXPIRED; DELETED          # §6.4
class Retention(StrEnum):    DURABLE; CONTEXTUAL; BEHAVIORAL; EPISODIC       # §6.6
class DeleteScope(StrEnum):  KEY; MEMORY_ID                                  # decisione 1
class ItemStatus(StrEnum):   WRITTEN; UNCHANGED; DELETED; ALREADY_ABSENT; REFUSED; FAILED
class StoreHealth(StrEnum):  OK; QUARANTINED; DEGRADED

@dataclass(frozen=True)
class MemoryRecord: memory_id: str; retention: Retention; state: MemoryState
    slot_key: str | None; scope: str; value_text: str; claim_digest: str
    created_at: str; valid_from: str; valid_until: str | None; evidence_ids: tuple[str, ...]

@dataclass(frozen=True)
class ItemResult: index: int; status: ItemStatus; memory_id: str = ""; error_code: str = ""
@dataclass(frozen=True)
class Revisions: prefs_revision: int; memory_revision: int; deletion_epoch: int; policy_version: str
@dataclass(frozen=True)
class DeleteRequest: scope: DeleteScope; slot_key: str = ""; scope_name: str = ""
    memory_ids: tuple[str, ...] = (); expected_epoch: int = -1
@dataclass(frozen=True)
class DeleteOutcome: ok: bool; commit_id: str; new_epoch: int
    results: tuple[ItemResult, ...]                  # esito vettoriale per elemento (§2.1/§2.6/§2.8)
    invalidated_events: int; tombstones: int
    residual: tuple[MemoryRecord, ...]               # inventario post-oblio, decisione 1
    truncated: bool = False; truncated_what: str = ""; used: int = 0; available_total: int = 0

# runtime/user_context/service.py — unica facciata; nessun metodo accetta user_id libero (inv. 3)
def put_memories(principal: PrincipalContext, items: Sequence[NewMemory]) -> list[ItemResult]: ...
def get_memories(principal, memory_ids: Sequence[str]) -> list[ItemResult | MemoryRecord]: ...
def list_memories(principal, *, purpose: str, limit: int, offset: int = 0) -> ListPage: ...
def inventory(principal, *, include_deleted: bool = False) -> ListPage: ...
def forget(principal, request: DeleteRequest) -> DeleteOutcome: ...
def revisions(principal) -> Revisions: ...
def build_snapshot(principal) -> UserContextSnapshot: ...     # costruita, non ancora consumata

# runtime/user_context/deletion_journal.py — store append-only separato (§6.5)
def prepare(principal_id: str, op: DeleteRequest, target_ids: Sequence[str]) -> tuple[str, int]: ...
def commit(commit_id: str) -> None: ...
def epoch_of(principal_id: str) -> int: ...                   # autorità sull'epoch
def replay_pending(*, apply: Callable[[JournalEntry], None]) -> ReplayReport: ...
def continuity(principal_id: str, mirrored_epoch: int) -> StoreHealth: ...
```

**Dati.**

```sql
-- user_memory.sqlite (nuovo, PATH_USER_DATA). Aperto con WAL + busy_timeout + foreign_keys=ON.
CREATE TABLE memory_state (
  principal_id TEXT PRIMARY KEY, memory_revision INTEGER NOT NULL DEFAULT 0,
  deletion_epoch INTEGER NOT NULL DEFAULT 0, policy_version TEXT NOT NULL,
  rows_active INTEGER NOT NULL DEFAULT 0, bytes_total INTEGER NOT NULL DEFAULT 0,
  health TEXT NOT NULL DEFAULT 'ok' CHECK (health IN ('ok','quarantined','degraded')),
  updated_at TEXT NOT NULL);

CREATE TABLE source_events (
  event_id TEXT PRIMARY KEY, principal_id TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('user_clause','interaction_choice','runtime_outcome','correction')),
  turn_id TEXT NOT NULL, authz_revision INTEGER NOT NULL, observed_at TEXT NOT NULL,
  deletion_epoch_at_enqueue INTEGER NOT NULL, scrubber_version TEXT NOT NULL,
  correlation_id TEXT NOT NULL, span_start INTEGER, span_end INTEGER,
  payload TEXT NOT NULL, payload_digest TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','consumed','invalidated')));
CREATE INDEX idx_se_open  ON source_events(principal_id, state, observed_at);
CREATE UNIQUE INDEX idx_se_dedup ON source_events(principal_id, kind, turn_id, payload_digest);

CREATE TABLE memories (
  memory_id TEXT PRIMARY KEY, principal_id TEXT NOT NULL,
  retention TEXT NOT NULL CHECK (retention IN ('durable','contextual','behavioral','episodic')),
  state TEXT NOT NULL, slot_key TEXT, scope TEXT NOT NULL DEFAULT '',
  value_text TEXT NOT NULL, claim_digest TEXT NOT NULL, n_bytes INTEGER NOT NULL,
  created_epoch INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  valid_from TEXT NOT NULL, valid_until TEXT);
CREATE UNIQUE INDEX idx_mem_slot ON memories(principal_id, slot_key, scope)
  WHERE slot_key IS NOT NULL AND state IN ('active_read','active_default');
CREATE INDEX idx_mem_twin  ON memories(principal_id, claim_digest);
CREATE INDEX idx_mem_owner ON memories(principal_id, state, updated_at);

CREATE TABLE evidence (
  memory_id TEXT NOT NULL REFERENCES memories(memory_id) ON DELETE CASCADE,
  event_id  TEXT NOT NULL REFERENCES source_events(event_id) ON DELETE RESTRICT,
  role TEXT NOT NULL, added_at TEXT NOT NULL, PRIMARY KEY (memory_id, event_id));

CREATE TABLE evidence_tombstones (       -- blocca il riuso della stessa fonte (§6.2)
  principal_id TEXT NOT NULL, event_digest TEXT NOT NULL, claim_digest TEXT NOT NULL DEFAULT '',
  deletion_epoch INTEGER NOT NULL, created_at TEXT NOT NULL,
  PRIMARY KEY (principal_id, event_digest, claim_digest));

CREATE TABLE compile_queue (             -- scheletro: lease + epoch, consumatore reale in F5
  queue_id INTEGER PRIMARY KEY AUTOINCREMENT, principal_id TEXT NOT NULL,
  event_id TEXT NOT NULL REFERENCES source_events(event_id) ON DELETE CASCADE,
  deletion_epoch_at_enqueue INTEGER NOT NULL, enqueued_at TEXT NOT NULL,
  lease_owner TEXT, lease_until TEXT, attempts INTEGER NOT NULL DEFAULT 0,
  state TEXT NOT NULL DEFAULT 'ready' CHECK (state IN ('ready','leased','done','invalidated')));
CREATE UNIQUE INDEX idx_cq_event ON compile_queue(event_id);
CREATE INDEX idx_cq_ready ON compile_queue(principal_id, state, enqueued_at);

-- users.db: epoch rispecchiato (§5.2), migrazione idempotente sul modello di `email`
ALTER TABLE users ADD COLUMN memory_deletion_epoch INTEGER NOT NULL DEFAULT 0;
```

**Ampiezza dell'oblio (decisione 1, deterministica, zero modello).** `KEY` risolve il bersaglio per chiave: `delete_pref` su W2 **più** ogni riga di `memories` con lo stesso `(principal_id, slot_key, scope)` in **qualsiasi** stato, non solo attivo — un gemello tipizzato già compilato condivide la chiave e muore con essa. `MEMORY_ID` cancella gli ID indicati e ogni riga dello stesso principale con identico `claim_digest`, calcolato sulla normalizzazione già esistente `normalize_query` (minuscole, punteggiatura finale, spazi compressi, parole vuote IT+EN) [PROVATO] `runtime/engine/cluster.py:78-109`. Un gemello che non è né di chiave né di digest **sopravvive ma non in silenzio**: `DeleteOutcome.residual` è l'inventario completo di ciò che resta al principale, con campi di troncamento §2.7 quando eccede il tetto. Ogni evidenza cancellata lascia un tombstone `(event_digest, claim_digest)` che vieta al compilatore di F5 di ricompilare lo stesso claim dalla stessa fonte. [IPOTESI]

**Innesti.**

| Punto | File:riga oggi | Che cosa cambia |
|---|---|---|
| Percorsi canonici | `runtime/config.py:171-178` [PROVATO] | Estensione: `DB_USER_MEMORY = PATH_USER_DATA/"user_memory.sqlite"` e `LOG_USER_MEMORY_DELETIONS = PATH_USER_STATE/"user_memory_deletions.jsonl"`. Directory diverse per costruzione: un ripristino che riporta indietro i dati senza il journal è rilevabile (§7.11) |
| Epoch rispecchiato in W2 | `runtime/users.py:112-128` (`init_db`, migrazione `email` via `PRAGMA table_info`) [PROVATO] | Estensione: stessa tecnica idempotente per `memory_deletion_epoch` |
| Transazione dell'oblio su W2 | `runtime/users.py:456-460` (`BEGIN IMMEDIATE` in `consume_pairing_token`) [PROVATO] | Precedente da riusare: oggi `set_pref`/`delete_pref` (`:695-751`) sono in autocommit e non chiudono la connessione [PROVATO]. `forget` apre una transazione esplicita, non aggiunge una seconda istruzione a una scrittura nuda |
| Cascata di W2 | `runtime/users.py:269-278` (`delete_user` cancella solo `user_channels`) [PROVATO] | Nuovo: `forget` non può appoggiarsi a `delete_user`; F1 corregge il difetto preesistente aggiungendo `user_prefs` alla cascata e contando le righe residue nella prova d'oblio |
| Vista in sola lettura del profilo | `runtime/sandbox.py:115-118` (`identity_profile` lega `users.db`), consumata da `executors/read_persons/manifest.toml:100` [PROVATO] | Estensione **obbligatoria**: con `users.db` in WAL un lettore in sola lettura dentro bubblewrap non può creare `-shm`. O si legano i due file affiancati, o W2 resta in modalità `truncate` e il WAL vive solo su `user_memory.sqlite`. Difetto che nasce dal lavoro W2 di F0 e che si manifesta qui |
| Durabilità del journal | `runtime/undo.py:37-46` (`flock`+`fsync` su JSONL) [PROVATO] | Nuovo modulo che ne copia la tecnica e **non** la politica d'errore: le due scritture dell'undo sono cedute in silenzio (`runtime/agent_runtime.py:3472-3474` e `:3491-3493`) [PROVATO]; qui un `PREPARED` non scritto interrompe l'oblio prima di toccare gli store |
| Riesecuzione all'avvio | `runtime/metnos_http_server.py:290-297` (blocchi di bootstrap idempotenti, ciascuno con `except` e avviso) [PROVATO] | Estensione: `replay_pending()` prima di servire. A differenza dei vicini, un fallimento non è un avviso: porta `memory_state.health='quarantined'` e ogni lettura fallisce chiusa |
| Conservazione | `runtime/jobs/maintenance_tasks.py:184-217` (`task_state_reaper`, `_run` per voce) e `runtime/nightly_orchestrator.py:42` [PROVATO] | Estensione: una voce `user_memory` (code invalidate, tombstone oltre finestra, quarantena scaduta). Nessun compito notturno nuovo (ADR 0186) |
| Registro della cascata | — | Nuovo: `store.CASCADE_TABLES` dichiara per ogni tabella la colonna del principale; una prova d'invariante fallisce se una tabella dello schema non è dichiarata. È il modo in cui `references`, `memory_fts`, `episode_refs` e `relations` entreranno senza riscrivere l'algoritmo di §6.5 punto 3 |

**Ordine di costruzione.**

1. **Percorsi e apertura.** Costanti in `config.py`, `store._open()` con WAL, `busy_timeout`, `foreign_keys=ON` e chiusura in `finally`. *Verifica:* due processi scrivono in parallelo su file temporaneo senza `database is locked`; nessuna connessione resta aperta a fine chiamata.
2. **Schema e stato.** Le sei tabelle più `memory_state`; `revisions()` legge le due revisioni e l'epoch da store distinti. *Verifica:* creazione da zero, riapertura, e seconda esecuzione senza differenze (migrazione idempotente).
3. **Journal separato.** `prepare`/`commit`/`epoch_of`, un blocco locale per principale che serializza allocazione dell'epoch, cancellazione e consumo della coda. *Verifica:* l'epoch cresce in modo monotono sotto venti richieste concorrenti e nessun `PREPARED` esce senza `fsync`.
4. **CRUD legato al principale con esito vettoriale.** `put/get/list/inventory`, quota su righe attive e byte, troncamento con i campi §2.7. Nessuna firma accetta un identificativo utente libero. *Verifica:* la lettura con un principale diverso restituisce zero righe, non un errore generico; il superamento della quota produce `REFUSED` per elemento e non un fallimento del lotto.
5. **`forget` completo.** Gli otto passi di §6.5 nell'ordine, con la risoluzione del bersaglio della decisione 1, l'invalidazione di **tutti** gli eventi non completati osservati prima del nuovo epoch, i tombstone e la rilettura negativa finale. *Verifica:* rilettura negativa fallita ⇒ `ok=False` e quarantena; è il passo che deve poter fallire in modo visibile.
6. **Riesecuzione e quarantena.** `replay_pending` all'avvio; `continuity()` confronta l'epoch del journal con quello rispecchiato e mette in quarantena quando il journal è più vecchio o assente. *Verifica:* uccisione del processo fra `PREPARED` e `COMMITTED`, riavvio, dato assente e journal chiuso.
7. **Scheletro della coda.** Lease con scadenza e ricontrollo dell'epoch dentro la transazione finale; un consumatore sintetico di prova, non un compilatore. *Verifica:* un lease acquisito prima dell'oblio non promuove nulla dopo e marca l'evento invalidato.
8. **Inventario deterministico su dati sintetici.** Elenco con valore, stato, data, classe di conservazione e identificativi delle evidenze, senza modello. *Verifica:* stessa base, stesso ordine, stesso testo byte per byte in due esecuzioni.

**Interruttore.** `METNOS_USER_MEMORY` — valore predefinito `off`. Spenta: nessun file `user_memory.sqlite` viene creato, nessuna voce nel reaper notturno, `replay_pending` non gira, il servizio ha esattamente il comportamento odierno. Restano attivi anche a interruttore spento, perché non sono condizionabili: la colonna `memory_deletion_epoch` in `users.db` (migrazione già applicata, valore 0), la chiusura corretta delle connessioni del CRUD preferenze e l'eventuale estensione della vista `identity_profile`. Il journal è governato dallo stesso interruttore: non esiste uno stato in cui si cancella senza journalizzare.

**Prove.**

- *Unitarie.* Chiave duplicata per lo stesso slot attivo respinta dall'indice parziale; gemello tipizzato in stato `superseded` cancellato dall'oblio per chiave; gemello per digest esatto cancellato dall'oblio per identificativo; gemello che non è né l'uno né l'altro presente nell'inventario residuo; tombstone che impedisce il reinserimento della stessa coppia fonte-claim; troncamento dell'inventario oltre la quota con `used` e `available_total` coerenti.
- *Integrazione.* Ciclo completo scrittura, elenco, oblio, rilettura negativa su base sintetica; riavvio con basi già popolate; migrazione di una `users.db` reale copiata in luogo isolato (`METNOS_USERS_DB` puntato altrove: `_open_db` crea e scrive al primo accesso [PROVATO] `runtime/users.py:81-87`); salvataggio e ripristino con journal coerente, stantio e assente, con i primi due casi leggibili e il terzo interamente in quarantena.
- *Avversariali.* Lettura e scrittura con principale di un altro utente; oblio con `expected_epoch` arretrato; richiesta di oblio su identificativi inesistenti mescolati a esistenti, che deve dare esito per elemento e non un fallimento unico; identificativo di memoria di un altro principale passato fra i propri.
- *Iniezione di guasto.* Interruzione fra `PREPARED` e la modifica degli store; fra la modifica dei due store; fra il commit locale e `COMMITTED`; journal non scrivibile; disco pieno durante `fsync`; lease del consumatore sintetico scaduto durante un oblio; `user_memory.sqlite` corrotto all'avvio.
- *Turno reale `/agent/turn` (§8.5).* Due turni sulla stessa istanza con lo stesso testo, uno a interruttore spento e uno acceso: identico piano, identica risposta, zero righe scritte in `user_memory.sqlite`, e in entrambi la preferenza di lingua continua a risolversi al confine HTTP [PROVATO] `runtime/metnos_http_server.py:94`. Un terzo turno che invoca `read_persons` dopo la migrazione di `users.db`, per provare che la vista in sola lettura del profilo regge il cambio di modalità del giornale.

**Fuori da questa fase.**

- Qualunque superficie in chat per ricordare, correggere, dimenticare e chiedere l'inventario: F1 espone solo funzioni e prove su dati sintetici; il confine comune HTTP/Telegram e le chiavi i18n sono F2.
- `PrincipalContext`, `authz_revision`, transazioni e WAL su `users.db`, intervalli di clausola: sono F0 e F1 **non è costruibile senza**. Se F0 non li consegna, F1 lavora su un identificativo di attore e viola l'invariante 1.
- La cascata di §6.5 punto 3 è completa **solo per le tabelle di F1**: `references` e `applications` (F3), `memory_fts` ed `episode_refs` (F4), `relations` (F5) non esistono ancora. F1 consegna il registro della cascata e la prova che fallisce quando una tabella nuova non vi è dichiarata; senza quella disciplina, ogni fase successiva riapre l'oblio.
- La catena `supersedes` non è percorribile in F1: `relations` arriva in F4 limitata alle chiavi tipizzate e si estende in F5. Fino ad allora il gemello non di chiave e non di digest resta visibile nell'inventario, non cancellato.
- Il consumatore reale della coda, il filtro di ammissibilità e ogni scrittura implicita sono F5: qui la coda ha lease ed epoch, e un solo consumatore finto vivo nelle prove.
- La propagazione dello snapshot dentro `run_turn` e l'iniezione dopo il piano sono F3: `build_snapshot` esiste e viene provata, ma in produzione nessuno la chiama.