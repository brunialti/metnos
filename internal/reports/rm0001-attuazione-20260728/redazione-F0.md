### F0 — Fondazione, identita e prova

**Contratti.**

```python
# runtime/principal_context.py  [IPOTESI]
class AuthSource(str, Enum):          # chiuso, uno per sorgente REALE oggi
    ADMIN_KEY = "admin_key"           # http_auth.py:224 [PROVATO]
    DEVICE_TOKEN = "device_token"     # http_auth.py:227 [PROVATO]
    ADMIN_COOKIE = "admin_cookie"     # http_auth.py:233 [PROVATO]
    USER_COOKIE = "user_cookie"       # http_auth.py:240 [PROVATO]
    CHANNEL_BINDING = "channel_binding"   # users.find_user_by_recipient, users.py:523 [PROVATO]
    INTERNAL_TASK = "internal_task"   # rigioco differito/schedulato, mai da rete

class AuthStrength(str, Enum):
    STRONG = "strong"     # chiave o biscotto verificato + legame corrente
    BOUND = "bound"       # legame canale/dispositivo verificato, senza segreto
    NONE = "none"         # posizione di rete: http_auth.py:273-274 [PROVATO]

class SubjectKind(str, Enum):
    OWNER = "owner"; GUEST = "guest"; SERVICE = "service"

class PrincipalUnavailable(RuntimeError):
    """Fallimento chiuso (§4.2). Porta `reason` di vocabolario chiuso."""

@dataclass(frozen=True, slots=True)
class PrincipalContext:
    principal_id: str          # sempre users.id; MAI 'host', MAI un nome
    owner_user_id: str
    subject_kind: SubjectKind
    authenticated_subject_id: str      # diverso da principal_id se impersonato
    channel: str; device_id: str; binding_id: str
    auth_source: AuthSource; auth_strength: AuthStrength
    authz_revision: int
    conversation_id: str; turn_id: str
    adult_verified: bool | None = None   # None = registro senza marcatore (§4.22)
    @property
    def is_owner(self) -> bool: ...      # sostituisce actor == "host"
    def with_turn(self, turn_id: str) -> "PrincipalContext": ...

def from_http_request(request, body: dict, *, conversation_id: str = "") -> PrincipalContext: ...
def from_channel_binding(channel: str, sender_id: str, *, conversation_id: str = "") -> PrincipalContext: ...
def from_stored_reference(ref: dict) -> PrincipalContext: ...   # riprese, differiti, task
def to_reference(p: PrincipalContext) -> dict: ...              # serializzabile, senza segreti
def project_tutor(p: PrincipalContext): ...                     # TutorPrincipal ristretto
def current_authz_revision(owner_user_id: str) -> int: ...
def bump_authz_revision(conn, owner_user_id: str, reason: str) -> int: ...  # dentro la transazione del chiamante

# runtime/user_context/contracts.py — pure, nessun import di sqlite3/aiohttp/planner
@dataclass(frozen=True, slots=True)
class UserContextSnapshot:
    principal_id: str; prefs_revision: int; memory_revision: int
    deletion_epoch: int; policy_version: str
    prefs: Mapping[str, "PrefRecord"]; defaults: Mapping[str, str]
    memory_ids: tuple[str, ...]        # soli identificatori, mai prosa (§5.2)
    def is_stale(self, *, prefs_revision: int, deletion_epoch: int) -> bool: ...

@dataclass(frozen=True, slots=True)
class PrefRecord:
    key: str; value: str; source: str; updated_at: str

# runtime/user_context/policy.py — importa solo contracts
class SourceKind(str, Enum):
    USER_CLAUSE="user_clause"; INTERACTION_CHOICE="interaction_choice"
    RUNTIME_OUTCOME="runtime_outcome"; CORRECTION="correction"
    EXTERNAL_TEXT="external_text"; ASSISTANT_PROSE="assistant_prose"  # entrambi VIETATI (§4.4)
class Sensitivity(str, Enum): ORDINARY="ordinary"; SENSITIVE="sensitive"; SECRET="secret"
class Purpose(str, Enum):
    PRESENTATION="presentation"; ARG_FILL="arg_fill"; REFERENCE="reference"
    RETRIEVAL="retrieval"; INVENTORY="inventory"
POLICY_VERSION = "f0.1"
def source_admissible(kind: SourceKind) -> bool: ...
def classify_sensitivity(text: str) -> Sensitivity: ...   # tabellare, zero LLM (§7.9)
def budget_for(purpose: Purpose) -> "Budget": ...          # righe, byte, elementi, ms

# runtime/compound_decomposer.py — un solo segmentatore
def split_query_spans(query: str) -> list[tuple[int, int]]: ...   # intervalli del testo GIA' ripulito
def split_query_chunks(query: str) -> list[str]: ...              # derivato dagli intervalli
```

**Dati.**

```sql
-- users.db, creata da users.init_db() (runtime/users.py:112) [PROVATO: oggi assente]
CREATE TABLE IF NOT EXISTS user_revisions (
  user_id         TEXT PRIMARY KEY REFERENCES users(id),
  authz_revision  INTEGER NOT NULL DEFAULT 1 CHECK (authz_revision > 0),
  prefs_revision  INTEGER NOT NULL DEFAULT 1 CHECK (prefs_revision > 0),
  deletion_epoch  INTEGER NOT NULL DEFAULT 0 CHECK (deletion_epoch >= 0),
  updated_at      TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_user_revisions_updated ON user_revisions(updated_at);
-- Monotonia per costruzione: ogni scrittura e' `SET x = x + 1` dentro
-- BEGIN IMMEDIATE, mai un valore assoluto dal chiamante. Nessun innesco SQL:
-- l'incremento appartiene alla transazione applicativa (§6.1).
-- `deletion_epoch` e' qui SOLO come specchio; l'autorita' e' il journal di F1.
```

**Innesti.**

| Punto | File:riga oggi | Che cosa cambia |
|---|---|---|
| Filtro di autenticazione HTTP | `runtime/http_auth.py:273-277` [PROVATO] | **Estensione**: oltre a `role`/`device_id`, il filtro scrive `request["auth_source"]` e `request["auth_strength"]`; il ripiego per posizione di rete resta `role="user"` (nessun cambio) ma marcato `NONE`, e la fabbrica lo rifiuta |
| Attore HTTP | `runtime/http_routes_agent.py:342-347` [PROVATO] | **Censimento**: `return request.get("device_id") or "host"` e' il ripiego n.1. In F0 la fabbrica gira accanto, in ombra; la sostituzione e' F3 |
| Utente logico HTTP | `runtime/http_routes_agent.py:2400-2409` [PROVATO] | **Censimento**: host unico (2400-2403) e `return actor or "anonymous"` (2409) sono i ripieghi n.2 e n.3; il principale sintetico `http_device_<digest>` (2393-2396) NON e' `users.id` e non puo' essere `principal_id` |
| Attore da pairing | `runtime/actor_resolver.py:48,50,53` [PROVATO] | **Censimento**: tre `return "host"` non autenticati (n.4-6). La fabbrica non chiama questa funzione; la scrittura pigra 58-75 resta, ma e' registrata come risoluzione con effetto persistente |
| Principale Telegram | `runtime/channels/daemon.py:1245-1273` [PROVATO] | **Estensione**: diventa l'adattatore che alimenta `from_channel_binding`; la deduzione `"host" if actor == "host"` (:1264-1265) e' il ripiego n.7, e le eccezioni inghiottite a :1258-1259 diventano rifiuto |
| Registrazione del turno | `runtime/agent_runtime.py:6679` [PROVATO] | **Estensione**: `TurnLog` riceve `principal_ref`, `authz_revision`, `auth_source`; la catena `owner_user_id or actor or "host"` (ripiego n.8) resta finche' F3 non propaga il principale |
| Firma del turno | `runtime/agent_runtime.py:6640-6643` [PROVATO] | **Estensione**: parola chiave facoltativa `principal=None`, sola registrazione. Il valore predefinito `actor="host"` e' il ripiego n.9 e cade in F3 |
| Apertura di `users.db` | `runtime/users.py:81-87` [PROVATO] | **Sostituzione**: `PRAGMA journal_mode=WAL`, `PRAGMA busy_timeout=5000`, schema preferenze e revisioni sempre applicati |
| Scrittura preferenze | `runtime/users.py:695-756` [PROVATO] | **Sostituzione**: `set_pref`/`delete_pref` in `BEGIN IMMEDIATE` sul modello di `consume_pairing_token` (`runtime/users.py:456-460` [PROVATO]), incremento di `prefs_revision` nella STESSA transazione, `finally: conn.close()` (oggi assente in tutte e quattro) |
| Lettura preferenze | `runtime/users.py:733-741` [PROVATO] | **Sostituzione**: `list_prefs` ritorna `PrefRecord` con `source` e `updated_at` (oggi scartati nella SELECT). Consumatori da aggiornare insieme: `runtime/http_routes_admin.py:1122` e `runtime/templates/user_detail.html:164-166` [PROVATO] |
| Mutazioni d'identita' | `users.py:269,282,298,342,401,448` [PROVATO] | **Estensione**: sei siti (cancellazione, autonomia, aggiornamento, aggiunta e rimozione canale, consumo del gettone) incrementano `authz_revision` nella propria transazione |
| Revoche fuori `users.db` | `runtime/pairing.py:279` e `runtime/devices.py:625` [PROVATO] | **Estensione**: entrambe chiamano `bump_authz_revision` del proprietario risolto; oggi sono tre revoche indipendenti che non si richiamano |
| Presa in carico della chat | `runtime/active_sessions.py:491` [PROVATO] | **Estensione**: `revoke_session(reason="takeover"\|"admin")` incrementa la revisione; `validate_writer` (:470-487) resta il modello di confronto proprietario gia' corretto |
| Segmentatore comune | `runtime/compound_decomposer.py:95-101` [PROVATO] | **Sostituzione**: `split_query_spans` diventa primario e usa lo STESSO oggetto regex senza gruppi di cattura (`:42-54` [PROVATO]); `split_query_chunks` ne deriva |
| Verifica del Tutor | `runtime/tutor/handoff.py:35` [PROVATO] | **Sostituzione**: `chunk not in query` (contenimento) diventa confronto posizionale sugli intervalli: oggi passa anche se il segmento compare altrove |
| Proiezione Tutor | `runtime/tutor_boundary.py:56` e `:68` [PROVATO] | **Nuovo modulo, non ancora innestato**: `project_tutor` esiste e ha una prova di parita'; la catena `... or "http-user"` e le due derivazioni divergenti di pubblico restano fino a F3 |
| Autorita' per nome | `runtime/system/admin.py:443`, `runtime/vaglio.py:387`, `runtime/admin_chat_commands.py:64` [PROVATO] | **Censimento**: `actor == "host"` (e in `vaglio` anche l'attore VUOTO) e' il ripiego n.10-12; `PrincipalContext.is_owner` esiste ma nessuno lo consuma in F0 |
| Rigioco differito | `runtime/recurring_tasks.py:549-552`, `runtime/deferred_turns.py:71`, `runtime/agent_server.py:234`, `runtime/orchestration.py:604`, `runtime/http_routes_agent.py:1647` [PROVATO] | **Estensione**: cinque siti scrivono e rileggono un riferimento di principale con `from_stored_reference`, che in F0 solo REGISTRA la discordanza di revisione senza rifiutare |
| Cache di piano | `runtime/engine/fastpath.py:147-160`, `runtime/engine/autopath.py:102-137` [PROVATO] | **Nulla cambia**: F0 aggiunge una prova d'invariante che diventa rossa se compare una colonna di identita' o di revisione (§4.8) |

**Ordine di costruzione.**

1. `contracts.py` e `policy.py` puri, senza altro. *Verifica*: prova sul grafo delle importazioni — i due moduli non importano `sqlite3`, `aiohttp`, `agent_runtime`, `engine`, `tutor`; violazione = rosso.
2. Durabilita' di `users.db`: WAL, attesa sull'occupato, chiusura delle connessioni, transazioni esplicite su preferenze. *Verifica*: due scrittori concorrenti su 500 scritture, zero `database is locked`; il conteggio dei descrittori del processo resta piatto.
3. `user_revisions` e incremento nei sei siti di `users.py` piu' le due revoche esterne. *Verifica*: prova di proprieta' — per ogni funzione pubblica che muta identita' o preferenze, la revisione dopo e' strettamente maggiore. **Questo passo deve poter fallire in modo visibile**: un sito di mutazione dimenticato rende rossa l'enumerazione, che elenca i nomi delle funzioni scoperte.
4. `principal_context.py` con le tre fabbriche, in sola ombra. *Verifica*: sul corpus congelato la fabbrica o costruisce o solleva `PrincipalUnavailable`; l'insieme dei rifiuti deve coincidere ESATTAMENTE con i dodici ripieghi censiti, ne' uno in piu' ne' uno in meno.
5. `split_query_spans` e riscrittura di `split_query_chunks`. *Verifica*: per ogni richiesta del corpus, `[q[a:b].strip() for a,b in spans] == split_query_chunks(q)`; piu' la richiesta con elisione «quando e' stato modificato» che oggi il doppio sguardo sugli apostrofi protegge (`compound_decomposer.py:39,52` [PROVATO]).
6. `UserContextSnapshot` costruita in sola lettura da `users.db`. *Verifica*: due costruzioni consecutive senza mutazioni danno lo stesso valore; una `set_pref` in mezzo rende `is_stale` vero.
7. Corpus congelato §11.1 piu' la categoria di trappole ratificata («candidato di disambiguazione fabbricato da contenuto esterno prima della conferma», circa 5 casi) e preregistrazione statistica per fase. *Verifica*: impronta del corpus committata; la partizione idoneo/non idoneo e' nell'etichetta, e una prova rifiuta un caso privo di referente atteso indipendente.
8. Parita' di comportamento. *Verifica*: rigioco di 40 turni reali con interruttore spento, prima e dopo l'intera F0: `final_message`, `final_kind`, `effect_counts` e piano identici campo per campo.

**Interruttore.** `METNOS_USER_CONTEXT`, valori `off|shadow|on`, predefinito **`off`**; in F0 `on` non e' ammesso e viene rifiutato all'avvio. Con `off` restano attivi: la durabilita' di `users.db` (passo 2), l'incremento delle revisioni (passo 3), il segmentatore con intervalli (passo 5) e la prova d'invariante sulle cache. **Non** sono coperti dall'interruttore perche' sono correzioni, non capacita': vanno dimostrati invarianti dal passo 8, non nascosti dietro l'interruttore. Con `shadow` la fabbrica costruisce e registra il principale e la snapshot senza che alcun consumatore le legga: nessuna decisione, nessun rifiuto, nessun messaggio.

**Prove.**

- *Unitarie*: richiesta ammessa per sola posizione di rete → rifiuto, non `host`; impersonazione amministrativa → `authenticated_subject_id` diverso da `principal_id`; pairing senza riga in `users.db` → rifiuto; incremento della revisione da 200 processi concorrenti → sequenza senza salti ne' ripetizioni; nome utente uguale all'identificativo di un altro (`users.py:240` `id=? OR name=?` [PROVATO]) → rifiuto per ambiguita'; testo citato o inoltrato classificato `EXTERNAL_TEXT` → non ammesso.
- *D'integrazione*: due browser dello stesso proprietario su dispositivi diversi → stesso `principal_id`, `device_id` diversi; parita' fra `_callback_principal` odierno e `from_channel_binding` su tutti i pairing reali, con la sola differenza attesa sui casi dedotti per nome; `project_tutor` uguale byte per byte all'uscita odierna di `http_principal` e `telegram_principal` per ogni principale costruibile.
- *Avversariali*: `actor` nel corpo della richiesta da un ruolo non amministrativo; `X-Forwarded-For` contraffatto da un peer non fidato; ripresa di un dialogo il cui stato porta `actor: "host"` scritto a mano; chiave di preferenza aggiunta al registro delle tecniche dopo l'importazione del modulo (`users.py:630-636` congela `PREF_KEYS` all'importazione [PROVATO]) → rifiuto esplicito, non silenzio.
- *Iniezione di guasto*: `users.db` occupato durante un incremento → attesa e successo, mai revisione persa; interruzione del processo fra scrittura del valore e incremento → impossibile per costruzione, dimostrato aprendo il file dopo un `kill -9` iniettato fra le due istruzioni; riga di `user_revisions` assente o corrotta → rifiuto, non revisione zero; revoca del pairing mentre un dialogo e' pendente → la rilettura del riferimento segnala la discordanza nel registro.
- *Turno reale §8.5*: una richiesta identica su `/agent/turn` (HTTP) e su Telegram, eseguita con `off` e poi con `shadow`: stessa risposta finale e stesso piano; nel secondo caso il registro mostra principale, `auth_source` e `authz_revision`, e il registro del turno porta il riferimento serializzato. Nessun messaggio all'utente cambia.

**Fuori da questa fase.**

- `user_memory.sqlite`, journal di cancellazione, quarantena del ripristino: **F1**. Conseguenza da non nascondere: in F0 il campo `deletion_epoch` della snapshot esiste nel contratto ma la sua **autorita' nasce in F1**; finche' il journal non esiste, il valore rispecchiato in `user_revisions` vale 0 e F0 non puo' dichiarare alcun oblio verificato.
- Tabella `user_defaults`, sezione firmata `[personalization]`, `ReferenceSlot project` su `runtime/project_paths.json` (oggi solo blocco di prosa per il planner, `runtime/agent_runtime.py:964` [PROVATO]): **F3**.
- Sostituzione reale dei dodici ripieghi censiti nei consumatori (`_resolve_actor`, `actor == "host"` in `system/admin`, `vaglio`, `admin_chat_commands`, valore predefinito di `run_turn`): **F3**, quando il principale viene propagato; F0 li marca e li misura soltanto.
- Cancellazione a cascata di `user_prefs` in `delete_user` (`runtime/users.py:269-278` non la esegue [PROVATO]): **F1**, con l'inventario post-oblio che la rende visibile.
- Comandi ricorda/correggi/dimentica, concetti `detection_lexicon` e messaggi localizzati dell'inventario: **F2**; F0 cambia solo l'interfaccia di lettura dello store e i due consumatori amministrativi esistenti.
- La categoria di trappole «candidato di disambiguazione fabbricato» viene scritta e congelata in F0, ma e' valutabile solo da **F3** in avanti, quando esiste un riempimento di argomento personale da ingannare.