# Espansione del typed registry verso un catalogo tecnico completo

**Versione del report:** `metnos.catalog-registry-expansion/1.0`  
**Data:** 2026-08-09  
**Perimetro:** analisi offline e read-only del repository; nessuna rete, nessun server, nessuna modifica al repository.

## Sintesi esecutiva

La strada è praticabile, ma non consiste nel trasformare i 96/103 nomi degli executor in un enorme enum di intenti. La soluzione consigliata separa nettamente tre artefatti compilati:

1. un **registro semantico piccolo e deduplicato**, l'unico mostrato all'analizzatore LLM;
2. un **registro di proiezione catalog-derived**, che collega le relazioni semantiche agli executor e al formato legacy senza leggere il testo dell'utente;
3. una **vista compatta per l'analizzatore**, con ordinali e schema JSON costante.

Il typed registry usato dalla linea V26.5.x è ancora, semanticamente, il registro V26.4.1: 10 relazioni e 26 slot. È solido nel proprio perimetro, ma non contiene una tabella `relation -> executor/route` e non dimostra copertura del catalogo generale. La suite 34 verifica un perimetro mirato; i 109 sono record di benchmark (159 clausole), non 109 tipi di intento da copiare in un enum.

Il catalog snapshot contiene 96 route, mentre il checkout corrente contiene 103 manifest selezionati; l'intersezione è soltanto 81. Quindi lo snapshot non può essere l'autorità del nuovo registro. Serve prima un inventario fresco, firmato e versionato, che distingua executor attivi, storici e importati.

La parte sintattica del contratto è generabile. La parte semantica non lo è ancora: nei 103 manifest non esiste alcun `intent_contract`; solo 10 hanno un contratto `[execution]`, tutti hanno uno `schema_inline` di output ma non un tipo semantico d'uscita, e soltanto due argomenti dichiarano `semantic_type`. I metadata V24/V24.1 sono un ottimo modello di migrazione, ma sono ancora suggerimenti non revisionati e nessuna delle 96 route risulta pronta per il runtime.

**Verdetto:** procedere con un catalog v2 compilato e reviewato. Non autorizzare ancora la sostituzione del motore legacy. Il cutover è autorizzabile solo dopo copertura contrattuale completa degli executor attivi, `109/109` adjudicated ripetuto, suite estesa 70 senza regressioni, holdout storico congelato, e shadow runtime senza differenze semantiche.

## 1. Metodo, fonti e vincolo anti-contaminazione

L'analisi ha usato esclusivamente inventari tecnici, manifest, schema, codice del projector/validator e metadata aggregati dei report. Non sono stati letti né usati testi di query o gold per progettare relazioni, mapping o sinonimi.

Fonti principali:

- `tests/benchmarks/catalog_snapshot.json`;
- manifest correnti sotto `executors/*/manifest.toml` e `runtime/builtin_executor_contracts/*/manifest.toml`;
- `internal/tools/request_analysis_lab/candidates/v2641/metnos_v2641_typed_registry.json`;
- artefatti V26.5.x, che importano il typed registry V26.4.1;
- regole e metadata V23lite/V24/V24.1;
- report di migrazione structured-intent già prodotti in `/tmp`.

Questa separazione va preservata anche nella migrazione: chi redige il registro vede manifest, codice executor e test tecnici, ma non le query/gold di valutazione. Il gold viene aperto dal valutatore soltanto dopo il freeze hashato del registro.

## 2. Censimento reale

### 2.1 Typed registry sottostante a V26.5.x

| Voce | Valore osservato |
|---|---:|
| Versione semantica del registro | `metnos.v26.4-typed-registry/1.0` |
| Relazioni | 10 |
| Slot ordinati totali | 26 |
| Relazioni interrogabili | 7 |
| Relazioni d'azione | 3 |
| Relazioni con output dipendenza | 7 |
| Tipi di riferimento | 14 |
| Ruoli di clausola | 5 |
| Speech act | 5 |
| Gruppi proof family | 10 |
| Compatibilità output esplicite | 5, tutte da `spatial.position` |

Relazioni presenti: `spatial.located_at`, `identity.same_as`, `filesystem.located_at`, `runtime.host_of`, `spatial.near`, `acl.share`, `communication.send`, `movement.destination`, `workflow.position`, `document.position`.

Manca una tabella generale `relation -> route/executor`. Il runner incorpora ancora logica specifica nelle funzioni di dipendenza, proiezione diretta e obblighi di sicurezza. I risultati strutturali offline sono forti nel perimetro mirato (core 69/69, grafo 125/125, infrastruttura 72/72, representability/gate 34/34), ma non equivalgono a copertura delle route generali né a una prova live sui 109 record.

Stato della linea al momento del censimento: V26.5.1–V26.5.4 sono `STATIC BLOCK`; V26.5.5 ha `STATIC PASS` limitato al delta facade; V26.5.6 è `author PRE-INFRA-REVIEW` con inference disabilitata. Nessuno di questi stati aggiunge credito live o amplia le 10 relazioni del registro sottostante.

### 2.2 Snapshot a 96 route

| Voce | Valore osservato |
|---|---:|
| Route | 96 |
| Nomi decomponibili meccanicamente | 89 |
| Nomi speciali/non decomponibili in modo affidabile | 7 |
| Occorrenze argomento | 491 |
| Nomi argomento distinti | 255 |
| Contratti semantici espliciti | 0 |

I sette casi speciali sono `admin`, `consult_frontier`, `get_location`, `get_now`, `login_session`, `reply_messages`, `undo_last_turn`. La decomposizione dei restanti nomi produce candidati utili per il bootstrap, non verità semantica.

Tutte le 96 righe hanno descrizione, affinity, args schema, capabilities, timeout, target kind, revertibility e criticality. Nessuna dichiara però una firma di relazione, ruoli semantici degli argomenti, tipi semantici degli output, ownership o regole di proiezione revisionate.

### 2.3 Checkout corrente a 103 manifest

Il censimento sui due alberi di manifest correnti produce 103 manifest unici.

| Metadata | Copertura osservata |
|---|---:|
| `intent_contract` | 0/103 |
| `[execution]` | 10/103: 9 read-only, 1 mutating |
| output contract presente | 103/103 |
| output con vero tipo semantico | 0/103 |
| argomenti con `semantic_type` | 2 occorrenze |
| occorrenze argomento | 601 |
| nomi argomento distinti | 336 |
| argomenti `runtime_resolved` | 54 occorrenze |
| affinity | 99/103 |
| capabilities | 103/103 |
| revertible=true | 17/103 |

Gli output sono descritti con `schema_inline`: è un pseudo-schema operativo, non un contratto di dominio semantico, cardinalità, ownership o durata.

Confronto snapshot/checkout:

- overlap: 81;
- snapshot-only: 15;
- checkout-only: 22;
- unione nominale: 118, che **non** va interpretata come inventario attivo senza riconciliazione.

Snapshot-only: `change_pulls_github`, `create_issues_github`, `create_tasks_github`, `delete_issues_github`, `delete_messages_github`, `find_issues_github`, `find_pulls_github`, `login_session`, `read_issues_github`, `read_pulls_github`, `read_tasks_github`, `reply_messages`, `send_messages_github`, `set_issues_github`, `set_pulls_github`.

Checkout-only: `act_sites`, `classify_entries`, `compare_entries`, `delete_entries`, `delete_preferences`, `delete_sites`, `describe_entries`, `describe_images`, `extract_entries`, `find_entries`, `find_files_hash`, `find_images_google_photos`, `get_approval`, `get_images_google_photos`, `get_preferences`, `login_sites`, `login_urls`, `open_sites`, `read_sites`, `set_preferences`, `write_entries`, `write_images_google_photos`.

Conclusione del censimento: il primo deliverable implementativo deve essere un **catalog inventory v2** che risolva esplicitamente questi 37 casi di drift e separi `active`, `historical`, `builtin`, `installed/imported` e `unavailable`.

### 2.4 Metadata V23lite/V24/V24.1

I report aggregati registrano:

- V23lite raw: 98/109;
- V24 applicato a V23lite: 105/109, senza regressioni dichiarate;
- V24.1: 107/109 strict;
- V24.1 su V23 full: 102/109, con 107 validi e 2 fail-closed;
- suite adversarial V21: 44/50, con 45 validi e 5 fail-closed;
- handover corrente: 108/109 adjudicated con overlay.

Questi numeri dimostrano il valore di una proiezione strutturata, non una copertura catalog-derived. Le regole V24.1 sono volutamente sparse: 4 contratti sink primari, 1 binding argomento e 1 binding domanda; le regole V24 base aggiungono poche route, una tabella domain/carrier, un binding attributo e due sink di materializzazione.

L'artefatto di migrazione sulle 96 route riporta:

- 51 manifest allineati, 30 con drift argomenti, 15 mancanti;
- 370 campi derivati, 83 `manual_required`, 998 `suggested`;
- 69 argomenti `manual_required`, 422 `suggested`;
- soltanto i domini entries/files risultano materializzabili;
- 0/96 route runtime-ready.

È la prova decisiva che lo schema V24 è un buon ponte di authoring, ma non può essere promosso automaticamente ad autorità runtime.

## 3. Coverage: cosa copriamo davvero oggi

La coverage corrente va descritta su tre assi distinti:

1. **coverage semantica del registro**: 10 relazioni mirate;
2. **coverage tecnica del catalogo**: 96 nello snapshot, 103 manifest nel checkout, stato attivo non riconciliato;
3. **coverage comportamentale**: suite 34 focalizzata e suite generali misurate da altre pipeline.

Non esiste oggi una matrice revisionata `relazione -> executor -> argomenti -> output/effect` che permetta di affermare copertura end-to-end dei 109 record. Inoltre, i 109 sono 109 richieste di prova con 159 clausole, non 109 intent ID. Il numero corretto di relazioni emergerà dalla deduplicazione dei contratti tecnici: più executor/provider possono implementare la stessa relazione, mentre uno stesso executor multi-mode può avere più proiezioni.

Il gate di copertura futuro deve quindi essere:

`tutti gli executor attivi hanno almeno una proiezione revisionata` **e** `tutte le relazioni revisionate hanno test sintetici` **e** `le suite naturali passano senza fallback linguistici`.

## 4. Architettura proposta: tre livelli compilati

```text
manifest firmati + contratti revisionati
                 |
                 v
      catalog compiler deterministico
          /             |             \
         v              v              v
 semantic_registry  projection_registry  audit/provenance
         |              |
         v              |
 compact_analyzer_view  |
         |              |
 input -> LLM -> grafo tipato -> validatore isolato
                                   |
                                   v
                       resolver/projector deterministico
                                   |
                                   v
                         executor / formato legacy
```

### 4.1 `semantic_registry`

Contiene soltanto l'ontologia tecnica deduplicata che serve a comprendere una richiesta:

- tipi e compatibilità fra tipi;
- action/effect ID tecnici;
- relazioni con slot ordinati;
- output semantici prodotti;
- binding ammessi e regole di dipendenza;
- obblighi semantici di sicurezza che valgono per ogni implementazione.

Non contiene nomi di executor, affinity, sinonimi, stopword, verbi flessi, esempi o termini localizzati. Gli ID tecnici non vengono mai confrontati con il testo dell'utente.

### 4.2 `projection_registry`

Contiene il collegamento fra una relazione e uno o più executor concreti:

- identità e hash del manifest;
- relazione implementata;
- route legacy esatta;
- condizioni tecniche di selezione (provider disponibile, capability, tipo input, modalità);
- mapping slot -> argomento executor;
- adattatori strutturali tipati;
- output, ownership, durata e side effect specifici dell'implementazione;
- policy/consenso e materializzazione;
- proiezione verso `Intent`/route/action/object/qualifier legacy;
- stato e provenienza della review.

Questo registro non viene inserito nel prompt. Il projector non legge mai query, lemma o lingua: riceve solo il grafo tipato e il contesto tecnico.

### 4.3 `compact_analyzer_view`

È una compilazione del registro semantico con:

- ordinali stabili sotto uno specifico hash del registro;
- firme a tupla compatte;
- descrizioni tecniche/localizzate opzionali separate dall'autorità;
- schema JSON dell'output costante.

Il modello emette un grafo di atomi in ordine topologico. Una richiesta multi-azione/multi-dominio produce più atomi, senza cambiare lo schema. I legami fra azioni sono binding `from_prior_atom`, non ricostruzioni testuali nel projector.

## 5. Schema minimo generabile

### 5.1 Registro semantico

Esempio concettuale; i nomi sono ID tecnici, non trigger lessicali:

```json
{
  "registry_version": "...",
  "registry_hash": "...",
  "types": [{"id": "type:...", "parents": [], "traits": []}],
  "relations": [{
    "id": "relation:...",
    "kind": "query|action|assertable",
    "action_id": "action:...",
    "default_effect_id": "effect:...",
    "slots": [{
      "role_id": "role:...",
      "type_id": "type:...",
      "cardinality": "one|optional|many",
      "binding_kinds": ["source_span", "context_ref", "typed_literal", "from_prior_atom", "unknown"],
      "unknown_policy": "allow|clarify|reject"
    }],
    "outputs": [{
      "id": "output:0",
      "type_id": "type:...",
      "cardinality": "one|many"
    }],
    "safety_obligations": ["policy:..."]
  }],
  "compatibility_edges": [{
    "producer_type": "type:...",
    "consumer_type": "type:...",
    "adapter_id": "adapter:identity|adapter:..."
  }]
}
```

Campi minimi per una relazione:

- `id`, `kind`, `action_id`;
- lista **ordinata** di `slots` con ruolo, tipo, cardinalità, binding ammessi e politica unknown;
- lista di `outputs` con tipo e cardinalità;
- effetto di default o varianti tecniche esplicite;
- obblighi di sicurezza universali.

### 5.2 Proiezione executor/legacy

```json
{
  "executor_id": "...",
  "manifest_sha256": "...",
  "relation_id": "relation:...",
  "selection": {"capabilities": [], "provider_id": null, "mode": null},
  "slot_arg_bindings": [{
    "slot_index": 0,
    "arg": "...",
    "adapter_id": "adapter:identity",
    "binding_source": "analyzer|context|runtime"
  }],
  "outputs": [{
    "relation_output": "output:0",
    "executor_field": "...",
    "owner_from": {"kind": "slot|session|provider", "slot_index": 0},
    "durability": "ephemeral|session|persistent",
    "effect_id": "effect:..."
  }],
  "legacy_projection": {
    "route": "...",
    "action": "...",
    "object": "...",
    "qualifier": "..."
  },
  "review": {
    "state": "production_reviewed",
    "reviewer": "...",
    "reviewed_at": "...",
    "source_hashes": []
  }
}
```

Ownership, durata e side effect vanno nel livello semantico solo quando sono invarianti della relazione; quando dipendono dall'executor/provider restano nella proiezione. Il compiler deve proibire override che indeboliscano un obbligo di sicurezza universale.

Gli adattatori ammessi devono appartenere a un insieme tecnico chiuso e tipato, per esempio identità, scalar-to-array, estrazione di un campo upstream o risoluzione runtime. Non devono esistere adattatori che sostituiscono verbi, eliminano stopword o interpretano lingua. Un adattatore custom, se inevitabile, deve essere firmato, tipato, limitato e revisionato.

### 5.3 Output dell'analizzatore

Schema logico costante:

```json
{
  "status": 0,
  "atoms": [{
    "r": 17,
    "clause": 0,
    "role": 1,
    "speech": 0,
    "bindings": [
      {"k": "source_span", "v": [12, 24]},
      {"k": "from_prior_atom", "v": [0, 0]}
    ],
    "proof": []
  }]
}
```

`r` è un ordinale valido soltanto con il `registry_hash` del job. Action, slot role/type e output non vengono ripetuti: il validatore li deriva dalla firma. Questo riduce incoerenze e token. La clausola, il ruolo discorsivo e lo speech act restano espliciti quando servono per ordine, condizioni o domande.

## 6. Derivazione meccanica e metadata da revisionare

### 6.1 Derivabile come autorità tecnica

Da manifest firmati e schema machine-readable si possono compilare senza inferenza semantica:

- executor ID, versione, origine e digest;
- nomi proprietà, tipi JSON primitivi, required/default/enum;
- `runtime_resolved`, ma solo se dichiarato esplicitamente;
- capabilities e piattaforme;
- effect operativo, ma solo nei 10 manifest che già lo dichiarano;
- schema output esatto, quando diventerà realmente machine-readable;
- collisioni, riferimenti mancanti e consistenza degli hash;
- ordinali e viste compatte;
- validatori di arità, tipo, cardinalità e compatibilità.

### 6.2 Derivabile soltanto come suggerimento di bootstrap

Può essere proposto automaticamente, ma non promosso senza review:

- split action/object/qualifier dal nome route;
- clustering di executor apparentemente equivalenti;
- candidati relazione/ruolo a partire da manifest, codice executor e test tecnici;
- bozza di mapping argomento -> ruolo;
- bozza di dominio output da `schema_inline` o descrizione;
- bozza di proiezione legacy.

Un LLM offline può aiutare a redigere queste bozze per famiglie, purché non veda benchmark query/gold e produca esclusivamente stato `suggested`.

### 6.3 Richiede review tecnica esplicita

- semantica dei sette nomi speciali e di ogni executor multi-mode;
- `relation_id`, `action_id` ed `effect_id` corretti;
- patient/source/destination, object basis e carrier;
- ruoli, tipi semantici, cardinalità e sorgenti di binding;
- output semantici, ownership, durata, ridondanza e lifetime;
- coercion/adapters e loro eventuale perdita informativa;
- alias e proiezione legacy;
- sink di materializzazione e regole di selezione provider;
- sicurezza, consenso, recipient/channel e mutazioni;
- varianti condizionali dello stesso executor.

Ogni contratto deve avere `review_state=production_reviewed`, reviewer, timestamp, hash delle fonti e versione compiler. Il compiler runtime deve respingere `suggested`, `manual_required`, fonti mutate e contract mancanti.

## 7. Come evitare schema decoder enorme e latenza

### Soluzione raccomandata

1. **Schema JSON costante:** nessun `oneOf` per relazione e nessun enum stringa di 100 route. Il decoder accetta interi e una forma generica; il validatore isolato controlla range, arità, tipi e dipendenze.
2. **Deduplicazione semantica:** il prompt contiene relazioni, non executor. Provider diversi condividono una firma e vengono scelti dopo.
3. **Proiezioni fuori prompt:** route, mapping argomenti, ownership executor-specific e formato legacy restano nel projector deterministico.
4. **Ordinali sotto hash:** tuple compatte e ordine stabile rendono il prefix cacheable e impediscono mismatch fra output e registro.
5. **Una sola inferenza:** il modello produce direttamente il grafo; niente seconda chiamata per normalizzare verbi o ricomporre multi-query.
6. **Campi derivati fuori output:** action, ruoli, tipi e output vengono recuperati dal registro, non rigenerati dal modello.

L'evidenza V24.1 mostra un projector CPU molto economico (mediana 2,50 ms, p95 4,02 ms; inferenza esclusa). L'analisi sperimentale della decodifica attribuisce invece un costo quasi lineare all'output: circa 789 ms fissi più 9,915 ms/token, R² 0,9983. Quindi il guadagno maggiore viene dal ridurre campi emessi e retry, non dal comprimere il projector.

La variante compatta già sperimentata ha ridotto i byte sintetici solo del 2,56%; il suo vantaggio principale è la consistenza strutturale. Non va quindi promessa una grande riduzione di latenza senza misure live sul prompt completo.

### Retrieval opzionale, non autoritativo

Prima si misura il registro semantico completo e cacheato. Solo se il prompt è troppo grande si aggiunge una shortlist:

- embedding multilingue su descrizioni catalogo localizzate, mantenute come dati i18n;
- retrieval sull'intera richiesta più finestre Unicode sovrapposte, quindi unione dei candidati, per non perdere azioni multiple;
- nessun elenco di connettori, stopword, verbi o sinonimi nel codice;
- nessun uso del retrieval come autorità semantica: il modello e il validatore vedono solo relazioni revisionate;
- attivazione soltanto dopo recall candidati 100% sui 109, sui 70 e sul holdout, ripetuta K5.

Un classificatore gerarchico a due chiamate o una chiamata per clausola non è raccomandato come baseline: aumenta latenza e crea nuovi punti di errore sui casi multi-azione. Se il registro deduplicato restasse troppo grande, la seconda scelta è una codifica fattorizzata `action_id + domain_type + signature`, sempre revisionata e validata, non una lista di parole.

## 8. Compiler e invarianti obbligatori

Il catalog compiler deve essere deterministico e fail-closed. Gate minimi:

- inventario attivo completo e senza duplicati ambigui;
- ogni proiezione riferisce relazione, tipi, policy e adapter esistenti;
- ogni argomento required ha un binding o una sorgente runtime esplicita;
- ogni slot obbligatorio è proiettato o produce chiarimento;
- output producer/consumer compatibili;
- ownership e durata coerenti lungo le dipendenze;
- graph coercion aciclico, limitato e senza conversioni implicite lossy;
- un solo sink primario quando la semantica lo richiede;
- nessuna route orfana e nessuna collisione di selezione irrisolta;
- nessun testo localizzato, regexp linguistica o affinity nell'artefatto autoritativo/projector;
- sicurezza universale non indebolibile dalle proiezioni;
- hash, provenance e review state verificati prima del load;
- mutazioni del manifest, del registro o dell'ordinal map respinte.

## 9. Piano di migrazione e test

### Fase 0 — inventario congelato

Generare catalog inventory v2 dai manifest firmati, riconciliare i 15 snapshot-only e 22 checkout-only, classificare active/historical/imported e congelare hash. Nessun mapping viene progettato dalle suite naturali.

### Fase 1 — contratti

Estendere lo schema manifest con `intent_contract`, tipi/ruoli/output/effect/ownership e review provenance. Creare bozze meccaniche/offline, poi revisionare per famiglie. Nessun executor attivo può restare in fallback sul nome o sulla descrizione.

### Fase 2 — compiler e test contract-level

Compilare i tre artefatti e applicare gli invarianti della sezione 8. Aggiungere test sintetici/property-based per:

- ogni relazione;
- ogni proiezione executor;
- ogni edge output -> consumer;
- composizioni multi-azione e multi-dominio;
- ambiguità, unknown, unsupported e chiarimenti;
- sicurezza/consenso e mutazioni dei metadata.

Questi test non richiedono testo naturale e verificano coverage del catalogo indipendentemente dal modello.

### Fase 3 — analizzatore compatto offline

Integrare schema costante e validatore isolato. Verificare rappresentabilità, ordine topologico, clause coverage, dipendenze, output ownership, evidence e safety. Ogni risposta invalida deve essere fail-closed; i retry vanno misurati come errore di qualità e latenza.

### Fase 4 — benchmark congelati

Ordine dei gate:

1. suite focalizzata 34, prima K1 poi K5: smoke test, non gate di sostituzione;
2. suite generale 109 record/159 clausole, strict e adjudicated, K1 poi K5: richiesta `109/109 adjudicated` in ogni run, zero structural invalid e zero regressioni; pubblicare anche lo strict;
3. suite estesa 70 multilingue/adversarial, K1 poi K5: 100% degli esiti accettati dall'oracolo quando l'oracolo è valido, zero regressioni/security violation;
4. contamination audit sull'insieme complessivo già esistente;
5. solo dopo il freeze del registro, creare un nuovo holdout dallo storico, deduplicato e redatto, con giudizio indipendente.

Il holdout deve includere richieste multi-azione, multi-dominio, anafora, condizioni, negazione, citazioni e dipendenze fra output. Va eseguito una volta dopo il freeze, poi ripetuto K volte soltanto come stability check. Dove esiste ambiguità reale, l'oracolo deve ammettere alternative semanticamente equivalenti invece di forzare una sola serializzazione.

Metriche obbligatorie: validità schema, exact semantico, coverage clausole, ordine/dipendenze, safety/evidence, candidate recall se usato retrieval, retry rate, una sola chiamata, latenza end-to-end p50/p95 e differenziale rispetto al legacy sullo stesso ambiente.

### Fase 5 — shadow runtime e cutover

Il legacy continua a eseguire; il nuovo stack gira in shadow. Confrontare per clausola:

- route/executor scelto;
- ordine e dipendenze;
- argomenti e loro provenienza;
- source/sink, ownership e durata;
- side effect e consenso;
- errori, chiarimenti e fail-closed.

Solo dopo zero differenze semantiche accettate per una finestra concordata si abilita canary con rollback immediato. Il compatibility adapter grafo -> `Intent`/route resta per tutta la finestra di rollback.

## 10. Codice legacy rimuovibile, ma solo dopo cutover

Questa è una lista di candidati, non un'autorizzazione alla cancellazione. Ogni rimozione richiede: nuovo grafo sole source of truth, nessun consumer della query grezza, test equivalenti, shadow fire count zero e finestra rollback conclusa.

### 10.1 Rimozione diretta dopo sostituzione completa

`runtime/intent_extractor.py`:

- `_entries_intent_verbs`;
- `_demote_meta_object`;
- `_READING_VERBS`;
- `_OPEN_SOURCE_PROBE`;
- `_bind_reads_to_open_source`;
- `extract_intent`;
- `_parse_json`;
- infine l'intero modulo, quando non restano import.

Prompt legacy:

- `runtime/prompts/it/intent_extractor_v4.j2`;
- `runtime/prompts/en/intent_extractor_v4.j2`;
- `intent_extractor_scaffold.j2` nelle varianti attive;
- candidati pending equivalenti, dopo verifica riferimenti.

`runtime/prefilter.py`:

- `_VERB_TO_CANONICAL`, `_extend_verb_table_from_vocab`, `implements_intent_verb`;
- `_DIRECT_MESSAGE_RE`, `is_direct_message_query`;
- `detect_canonical_verb`, `_IT_CLITIC_SUFFIXES`, `_strip_italian_clitic`, `detect_canonical_verbs_all`;
- `_OBJECT_HINTS`, `_FS_EXTENSIONS`, `_DOMAIN_RE`, `_detect_domain_in_query`, `detect_canonical_object`;
- `_STOPWORDS_IT`, `_STOPWORDS_EN`, `_STOPWORDS`;
- `affinity_score`, `affinity_phrase_score`, `affinity_phrase_recall` quando usano la query grezza;
- `_OBJECT_PRIMARY_TOOLS`, `_QUERY_DEPENDENT_PRECURSORS`, `_EXIF_MARKERS`;
- `_TIME_INTENT_HINTS`, `_TIME_INTENT_RE`, `_SHELL_INTENT_HINTS`, `_SHELL_INTENT_RE`;
- `_GENERIC_AFFINITY_VERBS`, `_rank_adaptive_legacy`.

Strategie raw-language in `runtime/prefilter_strategies/`:

- `token_flat.py`, `cached_token_flat.py`, `token_flat_v2.py` inclusi `_PROVIDER_MARKERS` e `_VERB_FAMILY`;
- `verb_first.py`;
- `trie.py`, `trie_v2.py` inclusi `_QUALIFIER_MARKERS`;
- `bloom.py`, `fts5.py`, `constraint.py`, `length_adaptive.py` quando dipendono da token/affinity della query.

Eventuali moduli di retrieval semantico non vanno cancellati automaticamente: possono essere riusati solo se ricostruiti su descrizioni i18n catalog-derived, senza autorità nel projector.

`runtime/vocab.py`:

- `ACTION_MAPPING` linguistico;
- `_OBJECT_SYNONYMS_IT`, `_OBJECT_SYNONYMS_EN`;
- `canonical_object`, `detect_implicit_actions`;
- rendering di boundary/action mapping dipendente dalla lingua.

Le tassonomie tecniche ancora usate da planner/linter devono prima migrare nel registro generato. Non cancellare alla cieca configurazioni i18n di presentazione/provider.

`runtime/compound_decomposer.py`:

- `_connector_pattern`;
- `_FORMAT_HINTS`, `_FIELD_STOP`, `_FIELD_CUT_PREP`;
- `_SCHEMA_MARKER_RE`, `_TABULAR_WITH_FIELDS_RE`;
- `split_query_chunks`, `_clean_field_name` e derivazioni di field dalla query;
- `detect_chunk_action`, `derive_tool_name`;
- `_send_has_explicit_recipient` soltanto dopo copertura tipata recipient+consent.

Una segmentazione Unicode neutra può essere conservata se serve al retrieval, purché non sia una fonte semantica.

Altri candidati:

- `runtime/nlu.py` intero modulo sperimentale e `tests/runtime/infra/test_nlu_llm_boundary.py`, dopo copertura grafo di ordering/time/recurrence/count/visualize;
- `runtime/fast_path.py::_LOCATION_PATTERNS` e ramo location, dopo contratto revisionato e shadow gate;
- concetti routing-specific in `runtime/detection_lexicon_seed.py` come `undo.intent_bypass`, `system.status_query`, `machine.reference`, `health.section_focus` e marker provider, insieme ai relativi consumer. Il framework lexicon resta se ha consumer non-routing.

### 10.2 Da migrare prima di rimuovere

`runtime/agent_runtime.py`:

- call site di `extract_intent` nei percorsi engine/strato3;
- `_check_top_k_affinity_jaccard`;
- `_query_has_continuation`, `_all_query_verbs_satisfied`, `_query_has_notify_continuation`;
- `_query_requires_availability_check`;
- normalizzazioni count/presentation che devono diventare slot del grafo.

`runtime/engine/routing_pool.py`:

- `_gate_image_modality` e rami provider/query;
- `build_routing_pool`, da riscrivere come resolver sul projection registry.

`runtime/engine/proposer.py`:

- `_render_skeleton`, da alimentare dal grafo e non da action/object legacy.

`runtime/engine/dispatch.py` — riparazioni semantiche da sostituire con validazione/proiezione del grafo:

- `_enforce_missing_clauses`, `_ensure_extract_clause`;
- `_align_framework_action_pairs`, `_align_framework_objects`;
- `_affinity_chunks`, `_align_strong_affinity_producers`;
- `_dropped_required_verbs`, `_normalize_store_clauses`;
- `_decontaminate_clause_objects`, `_fix_unroutable_verbs`;
- `_conform_to_intent_order`, `_enforce_missing_objects`;
- `_fill_clause_args`, `_scope_sink_provider_to_clause`;
- `_decontaminate_reader_qualifier`;
- normalizzatori query-side equivalenti nella pipeline report.

Alcune di queste funzioni non spariranno, ma diventeranno controlli generici su tipi, slot e dipendenze. La cancellazione è ammessa soltanto quando ogni loro comportamento necessario è espresso senza literal linguistici e il contatore shadow dimostra che il ramo legacy non si attiva.

### 10.3 Da conservare

- `_coerce_args_to_schema` e `_strip_unknown_args`, se limitati allo schema executor;
- validazione letterale di path, URL, email e host;
- redazione credenziali/segreti;
- dataflow, connectivity e compatibilità producer/consumer;
- ownership, policy, consenso, ACL, recipient e channel;
- taint tracking, mass-mutation gate e safety gate;
- evidenza DOM e verifica post-execution;
- validazione atomica e rollback.

Gli artefatti lab V24/V26 sono evidenza riproducibile, non codice legacy di produzione: vanno archiviati, non cancellati durante il cutover. Anche l'adapter compatibilità `relation graph -> legacy Intent/route` resta fino alla fine della finestra rollback.

## 11. Rischi e decisioni ancora aperte

1. **Inventario non autorevole:** prima decisione è quali dei 118 nomi nominali siano realmente active/imported/historical.
2. **Granularità delle relazioni:** non fissare il numero copiando route o benchmark; emerge dalla review tecnica e dalla deduplicazione provider.
3. **Executor multi-mode:** possono richiedere più projection record con condizioni mutuamente esclusive.
4. **Output poveri:** `schema_inline` non basta; serve uno schema machine-readable con tipo semantico e lifecycle.
5. **Effetti e sicurezza:** 93/103 manifest non dichiarano execution contract; il compiler deve restare fail-closed.
6. **Latenza:** schema compatto riduce l'output, ma il prompt del registro completo va misurato; retrieval resta subordinato a recall 100%.
7. **i18n:** descrizioni localizzate possono assistere retrieval/model understanding, ma non devono mai entrare in codice di routing, projection rules o validatori semantici.

## 12. Raccomandazione finale

Costruire il nuovo stack in quest'ordine:

1. catalog inventory v2 firmato e riconciliato;
2. schema contract + compiler fail-closed;
3. review tecnica per famiglie fino a copertura completa degli executor attivi;
4. semantic registry deduplicato, projection registry e compact analyzer view;
5. analizzatore a una chiamata con schema costante e validatore isolato;
6. gate 34 -> 109 -> 70 -> holdout -> shadow;
7. cutover canary con rollback;
8. rimozione graduale dei literal linguistici legacy soltanto sulla base di contatori e test.

Questa architettura raggiunge l'obiettivo massimo senza trasferire le vecchie liste hardcoded dentro un altro file: il modello interpreta il linguaggio, il registro dichiara capacità e tipi, il projector applica soltanto metadata tecnici revisionati.

## 13. Hash delle evidenze principali

- catalog snapshot 96: `23ae20dbb890042bfdf8778823bb058f3f0c0022bcd13e965964abb76b44153d`;
- typed registry V26.4.1: `448c058d805e141c871580253e815849b24a9999d98c505cab970fcd55d1e46f`;
- V24 rules: `e41561e12c835b15bce40e25eb2eb28ead3b27228c8bd05867dc1f9d7b6c8357`;
- V24.1 rules: `254056cf4e7d959812f0d6a8a24c931b84191ea685f0e955d0b14cce57c7ff82`;
- V24.1 result: `a3a556e3cfeb4d7fc1531a79218589d517da6c0dbd3b09c13b455b30a95ef4f7`;
- structured-intent migration: `1519e4588bf321a9696a8f950bf8ad63a9c069e19b582dff218050eb8273127a`;
- migration validation: `692ef812236a6441d46d953f1e4560a3a9e3d145dc38ba6ab574b6dba8e70426`;
- migration schema: `ef20e3ce61ea136dd64ebf425b03d744b8778c39120fb1aa54da4c0d107ae40c`;
- inventario contenuti dei 103 manifest correnti: `784d0ae579574a84389aaaa14eadd320b3429d6e3fbe3a43b2eac2de28d73169`.
