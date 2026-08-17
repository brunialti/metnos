# Review indipendente dei residui V24.1 verso il 100%

> **Stato successivo**: questa review ha motivato i 34 controlli e il passaggio
> alla semantica compositiva. La continuazione corrente è V26.4 typed
> relational, congelata offline ma non autorizzata al run; partire dal §20 di
> `handover_request_analysis_8_8_2026.md` e da
> `internal/tools/request_analysis_lab/candidates/v264/README.md`. Non
> rieseguire V26.2/V26.3 né le varianti intermedie V25.3.1, V26 o V26.1.

Data: 2026-08-08  
Ambito: artifact V23lite/V24/V24.1 in /tmp, schema question_binding,
benchmark e catalogo locali.  
Vincoli rispettati: nessun server, nessuna modifica al repository.

## 1. Verdetto

V24.1 è una miglioria reale e ben congelata:

- V23lite raw: 98/109 exact;
- V24 sullo stesso V23lite: 105/109;
- V24.1 frozen: 107/109 strict, 109/109 frame validi, 0 regressioni;
- 10/10 mutation V24.1;
- 20 round offline, un solo hash di output;
- validator+projector CPU: mediana 2,50 ms, p95 4,02 ms;
- runtime gate correttamente fail-closed perché i metadata non sono ancora
  production-reviewed.

Sink nello store e attachment non sono più residui concettuali: V24.1 li
risolve tramite ownership/durability e argument relation tipizzate, senza
leggere query, lemma, gloss o gold.

I due residui finali hanno natura diversa:

1. create/files contro create/files_spreadsheet è un gold stale e va
   adjudicato;
2. dove mi trovo → get/persons è un errore vero dell'analyzer e non può essere
   riparato onestamente dal projector.

Con la sola adjudication spreadsheet, V24.1 vale 108/109 sul gold corretto.
Il 109° punto deve provenire da nuova evidenza strutturata di question focus.

Raccomandazione: non fare emettere all'LLM l'atomic binding
runtime.current_location come etichetta autoritativa isolata. Far emettere
invece un contratto denotazionale compositivo:

- relazione spaziale;
- variabile risposta = posizione;
- soggetto = attore corrente;
- tempo = corrente;
- evidenza sorgente tipizzata.

Il projector deve derivare runtime.current_location e unificare il contratto
con il semantic contract del manifest get_location. L'attuale
question_binding può restare come vista derivata/backward-compatible.

## 2. Evidenze controllate

### 2.1 Freeze V24.1

Lock: /tmp/metnos_v241_freeze.lock.json

- rules:
  254056cf4e7d959812f0d6a8a24c931b84191ea685f0e955d0b14cce57c7ff82
- schema:
  82b24c50dfcf10eed7736231c7640e75f773025864dd933889a474d531b375b4
- projector:
  964ab447f730a0a145178d23c05404e876e06be0b56688d9372dc5e890c8d68e
- V23lite dataset:
  860ed8b6ba5646fd5a5d2d96bf3a9fa09d7f2ffc9dff6560a9fc54eafc7367fa
- cross-validation_started_at:
  2026-08-08T16:20:09.311738+00:00

Il lock precede i risultati di cross-validation e incorpora anche gli hash
della base V24 e degli altri dataset. Per un prototipo offline la disciplina
di freeze è corretta.

### 2.2 Risultati

Da /tmp/metnos_v241_results.json:

| Dataset | Baseline V24 | V24.1 | Valid | Fail closed | Regressioni |
|---|---:|---:|---:|---:|---:|
| V23lite 109 | 105 | 107 | 109 | 0 | 0 |
| V23 full 109 | 100 | 102 | 107 | 2 | 0 |
| V21 adversarial 50, projection-only | 44 | 44 | 45 | 5 | 0 |

Da /tmp/metnos_v241_stability_perf.json:

- 20 × 109 proiezioni;
- unique_output_hashes = 1;
- output SHA-256:
  83275fa19a32da2ddcb198d16f239947db66b78ee85ad12de9b9ffc73618dfda.

Questa stabilità riguarda il solo componente deterministico su frame già
estratti. Non misura stabilità dell'inference LLM.

## 3. Residuo spreadsheet: adjudication legittima

Caso legacy:

    leggi le mail di Anthropic, estrai gli importi e crea un foglio di calcolo

Gold corrente:

    read/messages, extract/entries, create/files

Frame/candidato:

    read/messages, extract/entries, create/files_spreadsheet

La modifica del gold è fondata su evidenze anteriori e candidate-independent:

1. tests/benchmarks/intent_compound_bench.py:79 dichiara espressamente:
   create/files non esiste e foglio va a create/files_spreadsheet.
2. Nello stesso file, il caso XL quasi equivalente attende
   create/files_spreadsheet.
3. tests/benchmarks/catalog_snapshot.json espone
   create_files_spreadsheet con affinity foglio di calcolo e non espone un
   executor generico create_files.
4. tests/benchmarks/compound_extract_create_bench.py contiene più casi
   indipendenti extract → foglio/spreadsheet attesi sul capability
   create_files_spreadsheet.
5. Il frame V23lite ha qualifier spreadsheet e span sorgente
   foglio di calcolo: non è un'inferenza post-hoc del projector.

Contro-evidenza da registrare, non da nascondere:
runtime/prompts/it/intent_extractor_v4.j2:132–133 insegna ancora
create/files per un foglio. È debito del prompt legacy, non ragione per
conservare un gold contrario al catalogo e alla stessa suite.

Verdetto: l'overlay è legittimo. Non è legittimo chiamare questo punto
“miglioramento del modello”: è correzione della misura.

Punteggi da pubblicare separatamente:

- strict legacy: 107/109;
- adjudicated semantic: 108/109;
- gold corrections credited: 1;
- model/system corrections credited: 9 rispetto al V23lite raw.

## 4. Residuo current location: causa esatta

Frame V23lite per dove mi trovo:

- token: dove / mi / trovo;
- predicate anchor: token 3;
- lemma: trovare;
- gloss: find;
- semantic action: get;
- scope: single_known;
- patient implicito: persons;
- candidate route: get/persons;
- carrier/sink: none.

V24 target ha tentato patient kind=elided senza source edge ed è stato
correttamente rifiutato con semantic_1_patient_source_alignment. V23lite lo
ha reso implicit e quindi valido, ma semanticamente errato.

Il confronto interno più utile è:

- dove mi trovo → get/persons, errato;
- dimmi la mia posizione → get/places, corretto.

Il modello riconosce il dominio quando è nominalizzato esplicitamente, ma
perde la variabile di domanda locativa nella costruzione interrogativa
riflessiva. Questo non è un problema di route projection: Phase 1 ha già
assegnato l'answer domain sbagliato.

La produzione corrente maschera il difetto con
runtime/fast_path.py:141–160, che contiene una lista esatta IT/EN, inclusi
dove mi trovo e where am I. Il nuovo analyzer non può sostituire quel path
finché non dimostra parità su positivi e controlli negativi.

## 5. Audit dell'attuale question_binding

Schema V24.1:

    question_binding =
      none
      oppure
      catalog_query(
        binding_id = runtime.current_location,
        start_token_id,
        end_token_id
      )

Rules:

    runtime.current_location -> get/places

Validator:

- controlla enum JSON;
- controlla che lo span sia nel range;
- richiede evidence_refs = question;
- non rappresenta speech act, answer variable, subject, time o relazione;
- non controlla coerenza con patient o route candidate.

Projector:

- se il binding è presente, sostituisce incondizionatamente la route con
  get/places.

Mutation corrente:

- una positiva prova current_location → places;
- una negativa prova soltanto che evidence_refs mancante è rifiutato;
- non esistono negative controls semantici persona/identità/luogo.

Sonda statica persistita:
/tmp/metnos_v241_question_binding_blind_controls.json

Iniettando lo stesso claim formalmente valido, validator e projector accettano
6/6 e producono sempre get/places anche per:

- who am I;
- where is Lucia;
- who is here;
- where is report.pdf;
- una citazione.

Questo non dimostra che question_binding sia insicuro: ogni claim semantico
presuppone un analyzer corretto. Dimostra però che:

1. lo span è range-grounding, non entailment;
2. il projector non può difendersi da una misclassification dell'analyzer;
3. l'atomic enum non espone dimensioni su cui fare type checking o controlli
   di coerenza;
4. il 109/109 non può essere rivendicato usando soltanto la mutation sintetica.

## 6. Alternative language-neutral

### A. Atomic catalog binding con proof obligations

Evoluzione minima di V24.1. Il modello continua a emettere
runtime.current_location ma deve aggiungere:

- speech_act = open_question;
- focus_role = geo_position;
- subject = current_actor;
- temporal_scope = current;
- focus evidence;
- subject evidence: explicit span, predicate morphology o speaker deixis.

Il validator accetta il binding solo se la firma completa coincide col
contratto catalogo e rifiuta qualsiasi incoerenza con patient/route.

Vantaggi:

- delta piccolo;
- facile adapter per V24.1;
- nessuna lista linguistica;
- probabile soluzione rapida del singolo residuo.

Svantaggi:

- un binding ID per ogni tipo di domanda crea crescita combinatoria;
- binding e proof fields duplicano la stessa decisione;
- resta forte il rischio che il modello emetta un pacchetto internamente
  coerente ma semanticamente sbagliato;
- non separa bene question focus da uso della posizione come input.

Verdetto: accettabile come esperimento V24.2, non come forma finale.

### B. Desired-observation contract con catalog unification

Il modello non sceglie una route né un binding di capability. Descrive
l'osservazione necessaria:

    kind: attribute_query
    subject: current_actor
    property: geo_position
    temporal_scope: current
    answer_type: spatial_position
    cardinality: one
    evidence: ...

Il manifest get_location dichiara:

    subject_type: actor
    property: geo_position
    temporal_support: current/latest
    output_type: spatial_position
    canonical_route: get/places

Il projector unifica goal e capability. La route è derivata, non votata dal
modello.

Vantaggi:

- struttura piatta, quindi meno fragile di un AST annidato;
- language-neutral e catalog-driven;
- generalizza get_now, identity, file metadata e altri attribute query;
- distingue output richiesto da dipendenza: ristoranti vicino a me chiede
  una lista di luoghi e usa current location solo come input;
- elimina la necessità di un enum per ogni parafrasi o domanda.

Svantaggi:

- richiede semantic output contract nei manifest;
- property/output ontology deve essere progettata e reviewata;
- current 0/96 production-reviewed resta un blocker.

Verdetto: migliore compromesso e raccomandazione principale.

### C. Relational query algebra / typed lambda

Forma non convenzionale ma più precisa:

    ASK location:SpatialPosition
    WHERE located_at(
      entity = current_actor,
      location = ?,
      time = current
    )

Una rappresentazione JSON o una tuple prefix può evitare nested decoding:

    [ask, spatial_position, located_at, current_actor, variable, current]

Il type checker sa quale argomento è la variabile risposta.

Controlli negativi naturali:

- who am I:
  relation=identity, variabile=identity/person;
- where is Lucia:
  located_at(explicit_person, ?, current);
- who is here:
  located_at(?, current_context, current);
- where is report.pdf:
  filesystem_located_at(explicit_file, ?);
- am I in Rome:
  polar truth value, nessuna variabile location;
- where was I yesterday:
  time=historical, non soddisfatto da get_location current.

Vantaggi:

- massima composizionalità;
- answer focus esplicito;
- type checking forte;
- estensione a multiquery e relazioni annidate.

Svantaggi:

- maggiore burden di generazione;
- serve un relation/type registry rigoroso;
- JSON annidato aumenta errori e token; preferibile una tuple piatta;
- richiede più mutation e retry policy.

Verdetto: direzione V25/long-term; ottima alternativa se B diventa troppo
limitata.

### D. Universal Dependencies + semantic role head

Pipeline syntax-oriented:

1. parser UD multilingue o head LLM produce dipendenze/morfologia;
2. un semantic role head identifica variabile interrogata, soggetto e
   relazione;
3. il compilatore costruisce B o C.

È utile per distinguere:

- dove mi trovo: focus locativo + soggetto/riflessivo prima persona;
- dove trovo Lucia: focus locativo, Lucia oggetto;
- chi trovo qui: focus persona, qui adjunct locativo;
- pro-drop spagnolo e morfologia fusa turca.

Vantaggi:

- forte source grounding;
- word order e clitici diventano struttura, non liste;
- può fungere da segnale indipendente per retry/abstention.

Svantaggi:

- i modelli UD e la loro copertura sono di fatto language-dependent;
- qualità irregolare su pro-drop, code-switch, lingue low-resource;
- la sintassi non distingue da sola geo-location, posizione in un documento
  o stato in un workflow;
- nuova dipendenza e costo operativo.

Verdetto: shadow verifier utile; non raccomandato come autorità primaria
i18n-by-default.

### E. Catalog-denotation contrastive selection

Generare dal catalogo descrizioni tecniche di postcondition e chiedere al
modello, nello stesso round-trip, quale risultato soddisfa la domanda:

- posizione corrente dell'attore;
- identità dell'attore;
- posizione di una persona nominata;
- entità presenti in un luogo;
- posizione filesystem/runtime.

Nessuna frase trigger è nel codice; le alternative derivano dai manifest.

Vantaggi:

- integrazione semplice col catalogo;
- buon canary/tie-breaker;
- può produrre una confidence margin o abstention.

Svantaggi:

- il direct enum ha già mostrato perdita semantica sui casi difficili;
- bias d'ordine e dimensione del catalogo;
- una seconda inference aumenta latenza; due head nella stessa inference non
  sono davvero indipendenti;
- facile overfit se le descrizioni includono gli esempi del benchmark.

Verdetto: usare solo come diagnosi o bounded retry, non come route authority.

## 7. Matrice comparativa

Le prime cinque colonne sono su scala 1–5, con 5 migliore. Generation burden
è invertito: 5 = più leggero. Il rischio overfit è una stima qualitativa.

| Soluzione | Disambigua focus/soggetto | i18n by default | Type checking | Catalog extensibility | Generation burden | Rischio overfit |
|---|---:|---:|---:|---:|---:|---:|
| A atomic + proof | 4 | 4 | 3 | 2 | 5 | medio |
| B desired observation | 5 | 5 | 4 | 5 | 4 | basso |
| C relational algebra | 5 | 5 | 5 | 5 | 2 | basso |
| D UD + roles | 4 | 2 | 4 | 4 | 3 | medio |
| E contrastive catalog | 3 | 4 | 2 | 4 | 3 | alto |

Vincitore raccomandato: B, con la type discipline di C e D soltanto in shadow.

## 8. Contratto raccomandato

### 8.1 Analyzer output

    query_goal:
      kind: relation_query
      speech_act: open_question
      relation: spatial.located_at
      focus_role: location
      arguments:
        entity:
          kind: deictic_actor
          ref: current_actor
          evidence:
            kind: explicit_span | predicate_morphology | speaker_context
            start_segment_id: ...
            end_segment_id: ...
        location:
          kind: variable
          value_type: spatial_position
        time:
          kind: current
      focus_evidence:
        start_segment_id: ...
        end_segment_id: ...

Il campo desired_observation è derivato:

    subject=current_actor
    property=geo_position
    time=current
    output=spatial_position

### 8.2 Manifest output contract

    semantic_contract:
      capability_id: runtime.current_location
      executor: get_location
      subject: current_actor
      property: geo_position
      temporal_support: [current, latest]
      output_type: spatial_position
      canonical_route: get/places

Questi sono ID tecnici, non parole da cercare nella query. Un sistema con
catalogo finito necessita di un'ontologia tecnica finita; ciò non viola i18n.
Il divieto deve riguardare trigger, stopword, suffissi e sinonimi della lingua
sorgente.

### 8.3 Validator

Deve:

1. richiedere role=request e speech_act=open_question;
2. richiedere una sola variabile risposta;
3. type-checkare gli argomenti di spatial.located_at;
4. richiedere entity=current_actor e time=current per il capability singolare;
5. accettare evidenza del soggetto esplicita, morfologica o deittica;
6. permettere overlap degli span: in turco focus, soggetto e predicato possono
   stare nello stesso token;
7. rifiutare route/patient candidate incoerenti e chiedere un solo retry,
   invece di sovrascriverli silenziosamente;
8. derivare question_binding, non fidarsi di un binding emesso;
9. fail-closed dopo retry;
10. non leggere query, lemma, gloss o gold nel projector.

### 8.4 Token/span i18n

Gli attuali token whitespace non sono una base universale:

- cinese e giapponese possono essere una sola unità;
- turco fonde focus, copula e prima persona;
- spagnolo può omettere il pronome;
- clitici e apostrofi variano;
- Thai e code-switch richiedono segmentazione diversa.

Raccomandazione:

- presegmentare con una procedura Unicode stabile e fornire segmenti numerati
  al modello;
- consentire span di clausola e span sovrapposti;
- non chiedere al modello di contare caratteri raw;
- rappresentare l'evidenza implicita con una union esplicita
  predicate_morphology/speaker_context, non con span zero;
- tenere una copia verificabile degli offset originali.

## 9. Negative controls congelati

Artifact:
/tmp/metnos_question_focus_negative_controls_v1.json

Hash:
22dac65689f720e07022ac204ba0fd25feebdf5e8db98a9d89f5bf00c120dcbf

Contiene:

- 34 casi;
- 9 positivi;
- 25 negativi;
- 8 lingue: IT, EN, FR, ES, DE, TR, JA, ZH.

Famiglie negative:

- self identity;
- third-person location;
- person-at-place;
- place identity;
- filesystem location;
- process/host location;
- nearby search che usa current location solo come operando;
- polar question;
- historical location;
- share;
- quote;
- condition;
- relative clause;
- workflow/document/metaphorical position.

Regola frozen:

    binding positivo se e solo se
      speech_act = open_question
      relation = located_at
      focus_role = location
      entity_ref = current_actor
      time_ref = current

Questa suite è un adversarial design set, non un holdout statisticamente cieco.
Dopo freeze di schema/prompt va aggiunto un piccolo holdout curato
indipendentemente, senza mostrarlo a chi fa tuning.

## 10. Rischi i18n specifici

1. Pro-drop: il soggetto current_actor può essere nella flessione verbale.
2. Fusione morfologica: un solo segmento può provare focus e soggetto.
3. Wh-in-situ: la variabile non è necessariamente a inizio frase.
4. Zero copula: alcune lingue non hanno un verbo equivalente a essere.
5. Politeness/evidential markers: non vanno scambiati per predicate.
6. Deissi: qui/near me può essere un argomento di un'altra richiesta.
7. Namespace: location geografica, filesystem, runtime host, documento e
   workflow sono relazioni diverse.
8. Metafora: dove mi trovo può chiedere progress, non GPS.
9. Temporalità: get_location non soddisfa una domanda storica.
10. Privacy: current_actor non autorizza la posizione di una persona nominata.
11. Code-switch: relation e subject possono essere espressi in lingue diverse.
12. Segmentazione: token IDs whitespace non sono universalmente granulari.

Mitigazione principale: focus/subject/time compositivi + negative controls,
non altre parafrasi nel prompt.

## 11. Come congelare l'overlay senza gaming

Artifact proposto:
/tmp/metnos_intent_gold_adjudication_overlay_v1.proposed.json

Hash:
8392aabcfaa2c40b14eb69371a0f6e0a0efc9e4faf1268e3308f79319f1f9723

È intenzionalmente status=proposed_pending_human_review.

Regole:

1. non modificare il gold originale in place;
2. versionare un overlay trasparente con old/new expected;
3. legarlo all'hash esatto della fixture;
4. includere rationale e fonti indipendenti;
5. richiedere due reviewer umani;
6. congelare overlay, schema, prompt, catalogo, model policy e negative suite
   prima dei run finali;
7. applicare lo stesso overlay a baseline e a ogni candidato;
8. preservare e pubblicare sia strict legacy sia adjudicated score;
9. vietare accesso all'overlay da runtime, prompt e projector;
10. nessun edit dopo aver visto i run finali;
11. una revisione successiva crea una nuova benchmark version e invalida
    l'acceptance precedente;
12. non adjudicare dove mi trovo: get/places è semanticamente corretto e il
    sistema deve guadagnare quel punto.

Il case locator usa fingerprint di query+old expected, non una regola runtime
o un match lessicale. L'overlay contiene una sola correzione.

## 12. È legittimo dichiarare 100% adjudicated?

Sì, con formulazione rigorosa:

- 109/109 sulla benchmark version adjudicata;
- 108/109 oppure altro valore sulla legacy strict, pubblicato accanto;
- una correzione gold separata dai miglioramenti di sistema;
- location risolta da una nuova inference reale, non da relabeling;
- overlay frozen prima dei run K;
- stesso scoring per baseline e candidato;
- negative leakage 0;
- nessun runtime access all'overlay.

Non è legittimo:

- cambiare il gold spreadsheet e mostrare soltanto 109/109;
- cambiare persons/places per adeguarlo all'errore del modello;
- aggiungere dove/where o la query esatta al prompt;
- aggiungere un override per case id/query/hash nel projector;
- contare la mutation sintetica question_binding come prova dell'inference;
- ritoccare overlay o prompt dopo un failure K.

## 13. Acceptance gate per il 109°

Prima di dichiarare convergenza:

1. freeze analyzer schema, prompt, segmentation, catalog semantic contracts,
   model/tier/seed e overlay;
2. eseguire i 34 controls per K≥5;
3. positive recall = 9/9 in ogni run;
4. negative leakage = 0/25 in ogni run;
5. full 109 adjudicated = 109/109 per K≥5;
6. full 109 legacy strict pubblicato separatamente;
7. adversarial 70 e security suite senza regressioni;
8. bounded retry rate, fail-closed e latenza misurati;
9. nessuna query del control set copiata come esempio nel prompt;
10. manifest get_location semantic contract production-reviewed;
11. solo dopo shadow agreement rimuovere
    runtime/fast_path.py:_LOCATION_PATTERNS;
12. conservare rollback e comparazione baseline.

## 14. Conclusione

V24.1 ha già vinto la parte deterministicamente risolvibile. Non serve un altro
repair del projector.

Il punto finale richiede di modellare non “che verbo vedo”, ma “quale variabile
la domanda vuole come risposta”. Per dove mi trovo:

- relazione: located_at;
- variabile: geo_position;
- soggetto: current_actor;
- tempo: current.

Questa struttura separa naturalmente persona, identità e luogo, resta
multilingue e si unifica col catalogo senza liste di parole. È la soluzione
raccomandata per raggiungere un 100% adjudicated tecnicamente difendibile,
senza trasformare il benchmark in una regola di produzione.
