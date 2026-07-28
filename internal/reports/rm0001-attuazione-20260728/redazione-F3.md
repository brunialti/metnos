### F3 — Riferimenti e default operativi

**Contratti.**

```python
# runtime/user_context/contracts.py — estensione F3 del modulo creato in F0 [IPOTESI]
class EffectClass(StrEnum):          # §5.4, vocabolario chiuso
    PRESENTATION = "presentation"; READ = "read"; MUTATING = "mutating"; OUTBOUND = "outbound"

class FillReason(StrEnum):           # esito per argomento, sempre registrato
    FILLED = "filled"; EXPLICIT_PRESENT = "explicit_present"; NO_MATCH = "no_match"
    AMBIGUOUS = "ambiguous"; ANCHOR_MISMATCH = "anchor_mismatch"; REVOKED = "revoked"
    PROVIDER_UNAVAILABLE = "provider_unavailable"; STALE_EPOCH = "stale_epoch"; DISABLED = "disabled"

@dataclass(frozen=True)
class PersonalizationSpec:           # una voce della sezione firmata, già validata
    owner: str; preference_key: str; argument: str; scope_fields: tuple[str, ...]
    value_provider: str; operations: tuple[str, ...]; effect_class: EffectClass
    sensitivity: Sensitivity; identity_anchor: str          # "" = ID non riusabile

@dataclass(frozen=True)
class TurnInvocationContext:         # ciò che un provider runtime può vedere: nessuna connessione
    principal_id: str; authz_revision: int; prefs_revision: int; memory_revision: int
    deletion_epoch: int; turn_id: str; channel: str; conversation_id: str
    tool: str; args_view: Mapping[str, object]; target_device: str; purpose: PurposeCode

@dataclass(frozen=True)
class ReferenceCandidate:
    candidate_id: str; label: str; scope: Mapping[str, str]; identity_anchor: str; provider: str

@dataclass(frozen=True)
class SlotResolution:
    status: str  # matched|ambiguous|absent|conflict|unavailable
    candidate_id: str; reason: FillReason; candidates: tuple[ReferenceCandidate, ...]

@dataclass(frozen=True)
class FillDecision:
    tool: str; argument: str; source_kind: str; source_id: str
    value_digest: str; reason: FillReason; effect_class: EffectClass

# runtime/user_context/personalization.py
def validate_section(owner: str, section: dict, args_schema: dict,
                     capabilities: list[dict]) -> tuple[PersonalizationSpec, ...]: ...   # solleva, nomina il colpevole
def compiled_registry(catalog) -> dict[tuple[str, str], PersonalizationSpec]: ...        # (tool, argument)
def fill_personal_args(executor, args: dict, ctx: TurnInvocationContext
                       ) -> tuple[dict, tuple[FillDecision, ...]]: ...                   # riempie SOLO se assente
def render_control_tokens(args: dict, ctx: TurnInvocationContext) -> dict: ...           # ${PERSONAL:tool.arg}
def personalized_effect_steps(framework, catalog) -> tuple[tuple[int, str, str], ...]: ...  # principale-indipendente

# runtime/user_context/references.py
VALUE_PROVIDERS: dict[str, Callable[[TurnInvocationContext], tuple[ReferenceCandidate, ...]]]
def resolve(slot: str, alias: str, ctx: TurnInvocationContext) -> SlotResolution: ...
def confirm(slot: str, alias: str, cand: ReferenceCandidate, ctx: TurnInvocationContext) -> None: ...
def project_candidates(ctx: TurnInvocationContext) -> tuple[ReferenceCandidate, ...]: ...   # project_paths.json validato

# runtime/agent_runtime.py — rottura di firma consentita da §7.1
_RUNTIME_ARG_SOURCES: dict[str, Callable[[TurnInvocationContext], object]]
```

**Dati.**

```sql
-- users.db (W2 resta autorevole; `prefs_revision` nasce in F1)
CREATE TABLE IF NOT EXISTS user_defaults (
  user_id TEXT NOT NULL, key TEXT NOT NULL, scope_key TEXT NOT NULL DEFAULT '',
  value TEXT NOT NULL, provider TEXT NOT NULL, identity_anchor TEXT NOT NULL DEFAULT '',
  state TEXT NOT NULL DEFAULT 'active' CHECK (state IN ('active','revoked','conflict')),
  version INTEGER NOT NULL DEFAULT 1, source TEXT NOT NULL DEFAULT 'explicit',
  updated_at TEXT NOT NULL, expires_at TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (user_id, key, scope_key));
CREATE INDEX IF NOT EXISTS user_defaults_live ON user_defaults(user_id, state, expires_at);

-- user_memory.sqlite (store e journal di F1). "references" è parola riservata: sempre fra virgolette.
CREATE TABLE IF NOT EXISTS "references" (
  ref_id TEXT PRIMARY KEY, principal_id TEXT NOT NULL, slot TEXT NOT NULL,
  alias_norm TEXT NOT NULL, provider TEXT NOT NULL, candidate_id TEXT NOT NULL,
  identity_anchor TEXT NOT NULL, scope_json TEXT NOT NULL DEFAULT '{}',
  state TEXT NOT NULL DEFAULT 'active' CHECK (state IN ('active','revoked','conflict')),
  confirmed_turn_id TEXT NOT NULL, authz_revision INTEGER NOT NULL,
  deletion_epoch INTEGER NOT NULL, retention_class TEXT NOT NULL DEFAULT 'contextual',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, expires_at TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS references_active
  ON "references"(principal_id, slot, alias_norm) WHERE state='active';
CREATE INDEX IF NOT EXISTS references_principal ON "references"(principal_id, slot, state);

CREATE TABLE IF NOT EXISTS applications (
  app_id INTEGER PRIMARY KEY AUTOINCREMENT, principal_id TEXT NOT NULL,
  turn_id TEXT NOT NULL, step_idx INTEGER NOT NULL DEFAULT -1, tool TEXT NOT NULL,
  argument TEXT NOT NULL, source_kind TEXT NOT NULL, source_id TEXT NOT NULL,
  value_digest TEXT NOT NULL,   -- il valore vive solo nello store d'origine
  effect_class TEXT NOT NULL, reason TEXT NOT NULL, target_device TEXT NOT NULL DEFAULT '',
  authz_revision INTEGER NOT NULL, prefs_revision INTEGER NOT NULL,
  memory_revision INTEGER NOT NULL, deletion_epoch INTEGER NOT NULL, ts TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS applications_principal_ts ON applications(principal_id, ts DESC);
CREATE INDEX IF NOT EXISTS applications_turn ON applications(turn_id);
```

**Innesti.**

| Punto | File:riga oggi | Che cosa cambia |
|---|---|---|
| convalida della sezione al caricamento | `runtime/loader.py:1421` (`rejected.append((str(sub), "invalid_platforms"))`) [PROVATO] | **estensione**: prima di costruire l'Executor, `validate_section` con respinta motivata `personalization:<sintesi>`, stesso schema di `executor_standard:` (`loader.py:1210`) [PROVATO]. Chiude il difetto del precedente: `credentials._validate_form_spec` (`runtime/credentials.py:203`) gira solo alla prima invocazione [PROVATO] |
| sezione trasportata dall'oggetto | `runtime/loader.py:503` `class Executor` e `runtime/loader.py:1428` costruzione [PROVATO] | **estensione**: nuovo campo `personalization: tuple[...] = ()`. Niente rilettura del manifest dal disco a invocazione, come fa `credentials.credential_form_kinds` (`runtime/credentials.py:246` `Path(path).read_text`) [PROVATO] |
| copertura del digest | `runtime/sign.py:192` firma i byte interi del manifest [PROVATO] | **nessun cambio**: la sezione è coperta per costruzione; cambiarla cambia `tools_sig` (`runtime/engine/cache_validity.py:103`) e uccide i piani in cache al primo hit [PROVATO] |
| registro delle sorgenti | `runtime/agent_runtime.py:3538` `_RUNTIME_ARG_SOURCES` con l'unica voce `credential_forms` [PROVATO] | **sostituzione**: firma `provider(ctx: TurnInvocationContext)`; la voce esistente ignora `ctx`. §7.1 consente la rottura, il call-site è uno |
| riempimento runtime-owned | `runtime/agent_runtime.py:3550` `_fill_runtime_sourced_args` [PROVATO] | **estensione**: riceve e passa `ctx`. Politica invariata («sovrascrive sempre», commento a `:3533-3537`) [PROVATO]: NON è il canale dei default personali |
| riempimento personale | `runtime/agent_runtime.py:3608` subito dopo il riempimento runtime-owned [PROVATO] | **nuovo**: `fill_personal_args` — terza politica «solo se assente», che oggi non esiste in nessuno dei due meccanismi. `target_device` è già parametro qui (`:3579`), quindi il valore che attraversa il filo firmato verso il device (`:3685` `invoke_remote`) è dichiarato e registrato in `applications`, non implicito [PROVATO] |
| propagazione del principale | `runtime/agent_runtime.py:3815` `invoke_executor` e `:3838` `submit_executor` [PROVATO] | **estensione**: parametro `principal`; entrambe passano dalla stessa implementazione, quindi anche l'onda parallela è coperta |
| ripresa dopo dialogo | `runtime/orchestration.py:1199` `invoke_executor(...)` [PROVATO] | **estensione**: passa `principal` al posto del solo `actor`. Nessun codice nuovo: passando dal choke point la ripresa riesegue riempimento e ricontrollo di `authz_revision` |
| un solo proprietario per argomento | `runtime/engine/executor.py:2205-2208` `resolve_scope_args` [PROVATO] e gemello parallelo `:1911` [PROVATO] | **estensione**: gli argomenti dichiarati in `[personalization]` sono esclusi dalla catena `args_defaults`, che è chiavata su `actor` grezzo (`runtime/args_defaults.py:33-42`) [PROVATO] |
| cattura implicita | `runtime/args_resolver.py:201` `remember_scope_args` [PROVATO] | **estensione**: non memorizza un argomento dichiarato personalizzabile. Senza questo, la difesa dall'auto-rinforzo (`args_resolver.py:42` `_is_install_root_path`) resterebbe l'unica [PROVATO] |
| controllo di piano | `runtime/engine/dispatch.py:5118` `_finalize_framework_for_run`, dopo il mass gate `:5156` [PROVATO] | **nuovo**: `_insert_personalized_effect_gate`. La condizione è **principale-indipendente** (il tool dichiara `effect_class` mutating/outbound e l'argomento è assente dal piano), quindi la forma del piano è identica per tutti; il prompt porta il token `${PERSONAL:tool.arg}`, reso al choke point come `${stepN.@count}` è reso nel ciclo (`runtime/engine/executor.py:756`) [PROVATO] |
| prova che il valore non entra in cache | `runtime/engine/dispatch.py:6351` `_maybe_record_fastpath` e `:6197` `record_observation` ricevono il framework **finalizzato** [PROVATO] | **nessun cambio, vincolo**: qualunque scrittura del valore nel framework sarebbe cachata. `calendar_id` e `base_path` non sono in `CONTENT_ARG_KEYS` (`runtime/engine/executor.py:1262`, commento esplicito su `base_path` a `:1270`) [PROVATO]: un valore lì dentro sarebbe servito ad altri per coseno |
| ammissione e autorità | `runtime/capabilities.py:158` `effective_capabilities` [PROVATO] | **nessun cambio al calcolo** (già sul valore finale); **nuova regola nel validatore**: è rifiutata una voce il cui `argument` compare in una clausola `when` (es. `client` in `executors/create_events/manifest.toml:104`) o in un hint `arg:<nome>` (es. `executors/read_urls_html/manifest.toml:171`) [PROVATO] |
| provider del ReferenceSlot `project` | `runtime/agent_runtime.py:964` `_render_project_paths_block`, consumato a `:6788` [PROVATO] | **sostituzione del lettore**: unico caricatore validato in `references.project_candidates`; il blocco del planner continua a mostrare i progetti d'**istanza** (configurazione), mai il riferimento scelto dal principale |
| vista del principale | `runtime/tutor_boundary.py:47` e `:64`, con `user_id or actor or device_id or "http-user"` a `:56` [PROVATO] | **sostituzione**: i due adattatori diventano proiezioni del `PrincipalContext` di F0; sparisce la catena di ripieghi e la doppia derivazione del pubblico (`role=="admin"` su HTTP, `role=="host"` su Telegram) |
| revisione in transazione | `runtime/users.py:695` `set_pref` e `:743` `delete_pref`, senza transazione né `close` [PROVATO] | **estensione**: `set_default`/`delete_default` e il bump di `prefs_revision` dentro un `BEGIN IMMEDIATE`, sul modello già presente nello stesso file (`runtime/users.py:460`) [PROVATO] |

**Ordine di costruzione.**

1. **Contratti e validatore, senza consumatori.** Verifica: un manifest di prova con argomento inesistente, `value_provider` ignoto, `effect_class` fuori vocabolario, `preference_key` duplicata fra due executor o argomento nominato in `when`/`arg:` è respinto **al caricamento**, l'executor sparisce dal catalogo e il motivo compare in `catalog.rejected`. Fallimento visibile: firma con `runtime/sign.py sign`, ricarico, executor assente.
2. **Registro compilato e firma dei provider.** `compiled_registry` legge il campo dell'oggetto Executor; `_RUNTIME_ARG_SOURCES` passa a `provider(ctx)`. Verifica: un turno `set_credentials` continua a ricevere `credential_forms`; una `runtime_source` sconosciuta resta l'avviso esistente (`runtime/agent_runtime.py:3565`) e l'argomento assente [PROVATO].
3. **Tabelle e revisioni.** `user_defaults` in `users.db`, `"references"` e `applications` nello store F1. Verifica: interruzione simulata fra scrittura del default e bump di `prefs_revision` non lascia mai una coppia incoerente; una cancellazione del principale svuota le tre tabelle e l'inventario post-oblio elenca esplicitamente ciò che resta (decisione 1).
4. **ReferenceSlot `project`.** Schema e validazione di `project_paths.json`. Verifica: il contenuto attuale è incoerente col repository (`memory_root` su `-opt-myclaw`, «Process name: myclaw») [PROVATO] e la validazione lo segnala invece di servirlo; alias sconosciuto → `absent`; due candidati compatibili → `ambiguous`, una sola domanda, nessuna preselezione.
5. **Riempimento al choke point e isteresi.** Verifica A/B a cache calda fra due principali con default diversi: stesso `canonical_hash`, stesso `framework_json` registrato, `applications` diversi. Una riconferma si chiede solo per i cinque motivi di §7.6; l'aggiunta di un candidato irrilevante da parte del provider non la provoca.
6. **Gate di piano e token.** Verifica: un piano con `create_events` e `calendar_id` assente porta il gate per **qualunque** principale; il prompt reso mostra il calendario risolto oppure l'assenza onesta, e l'esito è registrato in `applications` prima dell'invocazione.
7. **Turni reali (§8.5).** Uno HTTP e uno Telegram, con confronto di `turn_id` e delle righe `applications`.

**Interruttore.** `METNOS_USER_CONTEXT_F3`, valore predefinito `0`. Spenta restano attive: la convalida della sezione al caricamento con i relativi rifiuti (è una proprietà di ammissione, non un comportamento), la costruzione del registro compilato e la nuova firma `provider(ctx)`. Sono spenti: riempimento personale, risoluzione degli slot, inserimento del gate e resa del token, scrittura in `applications`, ed è ripristinata la catena `args_defaults` sugli argomenti dichiarati — cioè il comportamento di F2 è identico byte a byte.

**Prove.**

- **Unitarie:** argomento inesistente; `value_provider` ignoto; stessa `preference_key` da due domini; `effect_class` fuori vocabolario; argomento nominato in una clausola `when`; argomento nominato in un hint `arg:`; `scope_key` canonico stabile invertendo l'ordine dei campi; digest del valore stabile fra due esecuzioni.
- **Integrazione:** «per il lavoro usa il calendario Team» poi «impegni di domani per il lavoro» (riempimento in lettura); «crea la riunione di lunedì per il lavoro» (il controllo mostra il calendario risolto); un calendario nominato nella richiesta prevale sul default; «controlla i test di Atlas» con due progetti simili chiede una volta; la stessa domanda dopo la conferma non richiede nulla.
- **Avversariali:** una pagina letta nel turno propone un candidato «Atlas» che il provider non ha prodotto — mai scelto; il provider ricicla un `candidate_id` con ancora diversa — riconferma obbligata; un ospite senza abilitazione non ottiene riempimenti né vede il profilo del proprietario; impersonazione amministrativa (`runtime/http_routes_agent.py:342-347`) [PROVATO] — `applications` distingue attore autenticato ed effettivo; un piano proveniente dalla cache tenta di portare il valore già valorizzato — l'esplicito del piano non viene sovrascritto ma il gate lo mostra come non-personale.
- **Iniezione di guasto:** provider dei calendari irraggiungibile (`runtime/backends/events/google_workspace.py:295`) [PROVATO] → `provider_unavailable`, argomento assente, il form esistente chiede; manifest ri-firmato con la sezione cambiata → `tools_sig` diverso e piano in cache invalidato al primo hit; interruzione fra scrittura del default e revisione; cancellazione del principale mentre un turno riempie → `deletion_epoch` arretrato, nessuna applicazione; esecuzione remota con device non raggiungibile → il turno differito rigioca con il principale rivalidato, non con la stringa persistita.
- **Turno reale (§8.5):** `/agent/turn` HTTP «crea la riunione di lunedì alle 10 per il lavoro» (mutante, con controllo) e un turno Telegram «gli impegni di domani per il lavoro» (lettura), stesso proprietario, verifica che il calendario applicato sia lo stesso e che i due `applications` citino lo stesso `principal_id`.

**Fuori da questa fase.**

- Relazioni tipizzate `supersedes`/`contradicts`: **F4** per la correzione già ratificata. **Difetto da non nascondere:** l'uscita di F3 pretende UC-11 verde, ma «conserva la relazione temporale» con una relazione tipizzata non esiste prima di F4. In F3 UC-11 è chiudibile **solo** sulla chiave tipizzata, con `state='revoked'` + `version` + `expires_at` in `user_defaults`: una successione per chiave, non una relazione. O si legge così l'uscita, o si anticipa a F3 il solo `supersedes` deterministico su chiavi tipizzate — decisione da prendere prima del codice, non durante.
- Ricerca personale, FTS5, episodi e domande aggregate: **F4**. Un default non trovato per chiave non ricade mai su una ricerca testuale.
- Acquisizione implicita e compilatore: **F5**. In F3 un default o un riferimento nasce esclusivamente da una dichiarazione esplicita raccolta dal confine chat di **F2**.
- Routine e riscrittura canonica pre-cache: **F6**. In F3 nessun valore personale precede il piano.
- Migrazione di `args_defaults` (chiave `actor` grezzo, scadenza per inattività a 90 giorni, `runtime/args_defaults.py:140`) [PROVATO] al principale canonico: decisione separata; F3 gli toglie solo gli argomenti dichiarati.
- Prerequisiti a monte, non opzionali: `PrincipalContext` e `authz_revision` (**F0**), store, revisioni e journal di cancellazione (**F1**). Senza di essi F3 non ha né soggetto né oblio, e `applications` registrerebbe un `principal_id` non verificato.