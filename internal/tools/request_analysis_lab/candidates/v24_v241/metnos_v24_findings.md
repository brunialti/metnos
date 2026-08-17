# V24 offline — graph grounding e catalog projection language-neutral

Data: 2026-08-08. Ambito rispettato: solo `/tmp`, nessun edit del repository,
nessuna chiamata LLM/server. Le regole, lo schema e il projector sono stati
congelati prima della cross-validation; dopo il lock non sono stati modificati.

## Verdetto

V24 è una direzione nettamente migliore di V23 per la proiezione: sul full set
da 109 record arriva a **100/109 exact** e **140/159 clausole**, contro 81/109 e
120/159 del fold V23. Sui soli frame V24 validi è **100/107**, senza regressioni
rispetto alla route candidata grezza. La normalizzazione diventa strutturale e
catalog-driven: il projector non legge query, lingua, lemma, gloss o liste di
sinonimi.

Non è però pronto al cutover. L'audit catalogo ha **0/96 manifest contract
production-reviewed**; perciò la modalità runtime del prototipo fallisce
correttamente closed su 109/109. Il risultato 100/109 è una misura shadow in
`research` mode su metadata congelati e dichiaratamente provvisori, non una
giustificazione per un fallback runtime.

## Artifact e freeze

- Schema: `/tmp/metnos_v24_contract.schema.json`
- Validator/projector/retry harness: `/tmp/metnos_v24_offline.py`
- Metadata catalogo congelati: `/tmp/metnos_v24_rules_frozen.json`
- Lock: `/tmp/metnos_v24_freeze.lock.json`
- Risultati completi: `/tmp/metnos_v24_results.json`
- Mutation suite: `/tmp/metnos_v24_mutation_results.json`
- Stabilità/performance: `/tmp/metnos_v24_stability_perf.json`

Hash registrati nel lock:

| Artifact | SHA-256 |
|---|---|
| rules | `e41561e12c835b15bce40e25eb2eb28ead3b27228c8bd05867dc1f9d7b6c8357` |
| schema | `0d08b7205632efef8a7f9dadc0c63fe81b42e72daf03b062d7663eb5f98b7e24` |
| validator/projector | `7ab109898feddf5fc8c32c1b921f0220ab097b7e72a742d99879f32af1430e64` |

Il lock marca `cross_validation_started_at=2026-08-08T16:11:14.518771Z`.

## Contratto V24

V24 elimina i due array paralleli `semantic_heads`/`predicates`: usa un solo
array source-ordered `nodes`, con id denso e arco tipizzato
`input_from_predicate_id`. Ogni nodo contiene una candidate route tecnica e le
evidenze che la possono influenzare.

### Patient come tagged union

`patient` ha quattro alternative disgiunte:

1. `none`: nessun patient semanticamente asserito;
2. `explicit`: `object` più span non-zero nel testo sorgente;
3. `implicit`: `object` più base semantica tipizzata, senza fingere uno span;
4. `anaphoric`: span del pronome/clitico e obbligo di un arco verso un predicato
   precedente.

Questo elimina lo stato ambiguo V23 `patient_object != none` con span `(0,0)` e
copre anche il caso reale `object=none` con pronome esplicito e upstream. Nei 109
record migrati ci sono 157 nodi: 75 `explicit`, 45 `implicit`, 36 `none`, 1
`anaphoric`. Sessantanove record V23 contenevano almeno un patient span0; in V24
nessuna variante patient attiva usa più `(0,0)`.

### Carrier e sink tipizzati

`carrier` resta una union disgiunta (`source_container`, `transport_channel`,
`representation`), ma aggiunge `value_type` tecnico (`uri`,
`filesystem_path`, `message_store`, `raster_image`, ecc.). Il validator controlla
la compatibilità kind/object/value type e, per URI/path, anche la forma
language-neutral dell'evidenza.

`sink` è una union `none | primary_output | secondary_persistence`. Un sink
attivo richiede sempre span non-zero, object e `destination_role`. Il projector:

- non usa mai `primary_output` per sostituire l'object della route primaria;
- aggiunge una route soltanto per `secondary_persistence` e solo se esiste un
  sink catalogato;
- sopprime la materializzazione duplicata se esiste un consumer esplicito
  successivo o se il predicato primario possiede già lo stesso artifact domain.

I due frame V23 con sink attivo span0 vengono rifiutati; nessuna route viene
prodotta. La policy fa un solo retry vincolato con feedback strutturato e, dopo
un secondo errore, restituisce un risultato non eseguibile `fail_closed`.

### Evidence coverage

Ogni nodo dichiara gli `evidence_refs` che devono coprire esattamente i facet
attivi: predicate, patient, carrier, source, sink, attribute. Il validator
rifiuta evidenze mancanti o stale. Il projector emette inoltre una trace per
ogni decisione con rule id, route prima/dopo, facet usati e stato del manifest.
Quindi la copertura è verificata sia in ingresso sia nella decisione di
proiezione.

### Attribute claim

La metadata lookup non viene dedotta da parole. V24 prevede un claim catalogato
e source-grounded. Il contratto shadow `get_files.fields.media_metadata` lega il
claim tecnico `media_metadata` all'argomento `fields` di `get_files`; accetta
patient `files|images` e proietta `get/files`. Questo risolve il boundary EXIF
nel test astratto senza cercare `EXIF` nella query. Il frame V23 catturato non
contiene questo claim, quindi resta intenzionalmente non correggibile offline.

## Perché è i18n-safe

Il projector non accede a `query`, `tokens` semantici, `lemma` o
`semantic_gloss_en`; gloss e lemma restano solo auditabili. Non contiene
stopword, articoli, preposizioni, suffissi, verbi o sinonimi di una lingua. Le
sole regex sono per tipi tecnici universali (URI/path/literal anchor). I valori
come `read`, `files`, `source_container` e `media_metadata` sono id canonici del
contratto, non termini da cercare nell'input.

Non esistono override per id/query/gold. Le uniche eccezioni sono metadata di
catalogo generali e dichiarativi: alias route, object basis, autorità del
carrier, scope supportati, argomenti e sink. Sono fuori dal codice del
projector e sono hash-frozen.

## Algoritmo di proiezione

1. valida schema, grafo, span, ruoli, carrier type ed evidence coverage;
2. in runtime verifica che il manifest sia `production_reviewed`, altrimenti
   fail-closed;
3. parte dalla candidate route strutturata, senza riscrivere il testo;
4. applica alias/capability dichiarati dal catalogo;
5. permette al carrier di cambiare dominio solo secondo la sua autorità
   manifest (`source_owner`, `locator`, `destination_channel`, `context`);
6. applica eventuali attribute binding source-grounded;
7. verifica che ogni route corretta esista nel catalogo/overlay;
8. proietta separatamente una eventuale `secondary_persistence`, con dedup.

Esempi di regole catalog-driven, non linguistiche:

- `open/files` non ha executor canonico; `read_files` dichiara l'alias di
  content access;
- un URI `transport_channel` è source owner per `read`, quindi prevale su un
  patient file erroneamente generalizzato;
- `dirs` come source container è un locator e non sostituisce un patient file
  esplicito; per `list` con patient solo implicito il container può essere il
  dominio della route;
- `messages` è source-of-truth container per una ricerca al suo interno;
- `get_processes` possiede gli snapshot di collection/whole-domain, mentre
  `find/processes` resta l'existence check single-known;
- `send_messages` possiede l'envelope di output: il payload file/entry è un
  argomento e non l'object della route.

## Cross-validation V23 full

| Misura | Candidate raw V23 | Fold V23 | V24 research |
|---|---:|---:|---:|
| Exact record, full denominator | 92/109 | 81/109 | **100/109** |
| Exact clause | 135/159 | 120/159 | **140/159** |
| Exact sui 107 frame V24 validi | 90/107 | 81/107 | **100/107** |
| Mono | 79/85 | 66/85 | **83/85** |
| Compound | 13/24 | 15/24 | **17/24** |

Delta V24 rispetto alla candidate raw: 10 miglioramenti, 2 regressioni
fail-closed, net +8. Le due regressioni sono precisamente i due frame che la raw
signature considerava corretti pur avendo un sink attivo span0; accettarli
sarebbe una violazione del contratto. Tra i 107 frame validi: 10 miglioramenti,
0 regressioni.

Delta rispetto al fold V23: 20 miglioramenti, 1 regressione, net +19. La sola
regressione è l'operazione “attach” del compound 19: V23 la recuperava tramite
matching lessicale sul gloss; V24 lo vieta e richiede un futuro claim tecnico
source-grounded per l'attachment.

### Boundary principali

| Boundary | Risultato |
|---|---|
| patient span0 | tutti convertiti in union esplicita; 107/109 frame validi |
| sink attivo span0 | 2/2 rifiutati, nessuna route prodotta |
| open → read | 1/1 fixture e 1/1 mutation |
| URL carrier | il caso ambiguo file/URL viene corretto a `read/urls`; slice 8/10 exact, i due errori restanti sono downstream non-URL |
| directory content | 11/11 tra i frame validi della slice; i due restanti sono i sink-zero fail-closed |
| process snapshot | tutti gli snapshot reali corretti; existence single-known resta `find/processes` |
| search-in-messages | corretto a `find/messages`, più mutation astratta positiva |
| EXIF/media metadata | mutation 1/1; frame V23 0/1 perché manca l'attribute claim |
| primary sink locator | non sostituisce più `files` con `dirs` |
| secondary persistence | aggiunta e deduplicata correttamente nelle mutation |

## Adversarial V21

V21 non contiene patient/carrier/span, quindi G1/G2 di grounding sono **N/A**:
non è corretto inventare un denominatore. È misurabile soltanto la compatibilità
route/role/source/sink.

| Misura projection-only | Raw V21 | Compiler V21 | V24 |
|---|---:|---:|---:|
| Sui 45 record accettabili | 40/45 | 27/45 | **44/45** |
| Full denominator 50 | 45/50* | 27/50 | **44/50** |

`*` La raw signature conta come corrette anche le cinque strutture passive
invalide; non sono eseguibili. V24 fallisce closed su tutte e cinque per
`legacy_sink_alignment`. Sui 45 accettati migliora quattro catene multiquery
send-payload → message-envelope e non introduce regressioni. Resta una catena
spagnola con candidate `find/entries` invece di `find/files`: il projector non
ha evidenza strutturale per correggere l'errore dell'extractor.

## Mutation, stabilità e costo offline

Mutation suite: **31/31**.

- 15 mutazioni del validator: stale fields, span0, carrier type, forward edge,
  non-request executable, evidence gap, literal anchor;
- 12 boundary del projector: open/read, URI, dirs explicit/implicit, process
  snapshot/existence, messages, media metadata, primary/secondary sink, dedup,
  send envelope;
- 2 test retry;
- 2 test fail-closed/runtime gate.

Venti passaggi completi sui 109 record producono un solo hash output
(`8e80ed39...`): stabilità 20/20. Tempo CPU di validazione+proiezione, senza LLM:
mediana 1.73 ms, p95 2.21 ms, p99 2.51 ms, max 6.39 ms. Il costo dominante resta
quindi la singola inference strutturata, non il projector.

## Nove record non exact: tassonomia, non patch

1. **Due sink span0**: richiedono il retry V24; la cattura offline non contiene
   una seconda risposta.
2. **Due secondary persistence mancanti/mistyped**: il modello V23 emette
   `primary_output` o omette la route write. Il projector non può inventarla.
3. **Share classificato send**: semantic action e candidate route sono entrambe
   `send`; usare il gloss “share” sarebbe il vecchio errore lessicale.
4. **Attach classificato write**: manca un claim catalogato di attachment.
5. **Location classificata processes**: nessun patient/carrier/claim contraddice
   la candidate route; è un errore dell'extractor.
6. **EXIF senza attribute claim**: il nuovo contratto lo rappresenta, V23 no.
7. **Qualifier spreadsheet incoerente nella fixture**: una query attende
   `create/files`, un'altra molto simile `create/files_spreadsheet`; serve
   adjudication del gold, non una regola per frase/id.

Questi errori mostrano il confine corretto: V24 corregge solo ciò che è provato
dal grafo e dal catalogo; non cerca di ottenere error=0 facendo inferenza
lessicale nel projector.

## Cosa sarebbe rimovibile dopo un vero K-cross-val V24

Nel bench un cutover V24 renderebbe rimovibili, condizionatamente:

- `_lexically_related` e `CATALOG_ARGUMENT_NAMES` come segnali di routing;
- la cascata su `semantic_gloss_en`/`effect` di `folded_signature_v22`;
- l'override incondizionato `source_container -> object` che trasforma file in
  directory;
- l'override `primary_output -> object` che confonde destination locator e
  patient domain;
- i tre scalari patient V23 e le combinazioni zero/none ambigue;
- i due array paralleli semantic/predicate e il relativo validator di
  allineamento;
- la rappresentazione legacy del sink come campi indipendenti
  `materialize_to/sink_mode/sink_anchor`.

Non vanno rimossi finché i manifest non sono production-reviewed e V24 non è
stato eseguito live con retry su full+adversarial K>=3.

## Passi necessari per il cutover

1. Portare nei manifest reali, dopo review, object basis, carrier authority,
   route aliases/capabilities, output artifact ownership, argument roles e sink
   routes. Nessun parsing dei nomi executor a runtime.
2. Far emettere direttamente lo schema V24 al singolo intent extractor; il
   legacy adapter usato qui è solo per comparison.
3. Aggiungere argument claim generici e source-grounded almeno per media
   metadata e attachment; non una lista di parole.
4. Rendere retryable gli errori di grounding/sink e non retryable i catalog gap;
   dopo un retry, fallback legacy esplicito o stop non eseguibile, mai repair
   silenzioso.
5. Rieseguire full 109 e adversarial multilingua con K>=3, includendo validità,
   exact route, clause exact, stabilità e latency end-to-end.

Conclusione: **V24 è il miglior projector shadow finora e delimita bene cosa può
essere corretto senza hardcode linguistico. Non è ancora un sostituto runtime**:
il blocco principale non è l'algoritmo ma la mancanza di manifest review e di
alcuni claim strutturati nell'output LLM.
