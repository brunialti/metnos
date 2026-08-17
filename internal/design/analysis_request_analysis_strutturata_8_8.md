# Analisi della normalizzazione strutturata della richiesta

Data: 8 agosto 2026  
Stato: analisi e confronto non invasivo; nessun cutover nel runtime.

## 1. Decisione in una frase

Non va costruita una frase «ripulita» da consegnare all'intent extractor.
La soluzione promettente è una sola analisi LLM a output strutturato, dentro il
confine dell'intent extraction, seguita da validazione e proiezione tecniche
deterministiche.

Il runtime attuale resta, per ora, il vincitore di produzione. La nuova
architettura vince come direzione progettuale, ma non ha ancora superato tutti i
gate necessari per rimuovere le regole correnti.

## 2. Obiettivi

### Obiettivo minimo

Creare una sola facade che sia l'unico punto autorizzato a interpretare la
lingua della richiesta. Inizialmente può usare il comportamento legacy, ma:

- tutti i consumer devono leggere la struttura prodotta dalla facade;
- le forme linguistiche non devono restare sparse nel codice;
- le eventuali risorse lessicali residue devono essere locali, versionate e
  selezionate per lingua, non un'unione implicita IT+EN;
- i confini tecnici e di sicurezza restano deterministici.

### Obiettivo massimo

Sostituire il parsing linguistico query-side con una singola chiamata LLM
vincolata da schema, senza inventari di forme superficiali. Restano nel codice
soltanto:

- l'ontologia tecnica canonica;
- i contratti del catalogo;
- la validazione di schema, provenienza e dipendenze;
- i controlli di sicurezza e del mondo reale.

## 3. Cosa fa davvero oggi il runtime

Non esiste una fase globale che elimini le stop word. Le trasformazioni sono
diverse a seconda del consumer.

In `runtime/prefilter.py` sono presenti:

- `_VERB_TO_CANONICAL`: una grande tabella di forme IT/EN e riscritture
  semantiche, per esempio `mostrami -> read`;
- `_IT_CLITIC_SUFFIXES` e `_strip_italian_clitic`: rimozione di clitici italiani;
- `_OBJECT_HINTS`: parole IT/EN usate per indovinare l'oggetto tecnico;
- `_STOPWORDS_IT` e `_STOPWORDS_EN`: usate soltanto nel soft affinity scoring;
- detector e scorer che consumano combinazioni diverse dei dati precedenti.

La conseguenza concreta è che:

- `detect_canonical_verb` non normalizza i clitici;
- `detect_canonical_verbs_all` prova a normalizzarli;
- `inviamele` e `comprimili` sono riconosciuti soltanto in alcuni percorsi;
- `mostrarsi` non viene ricondotto correttamente;
- `mostra-mi` dipende dal modo in cui il tokenizer spezza il trattino;
- `le` e `quelle` sono escluse solo in alcuni punteggi, non rimosse dalla
  richiesta in generale.

Lo stesso tipo di interpretazione linguistica è distribuito anche in:

- `runtime/vocab.py`;
- `runtime/intent_extractor.py` e relativi prompt IT/EN;
- `runtime/compound_decomposer.py`;
- ordering, time window e recurrence;
- `runtime/args_extractor.py` e `runtime/target_device.py`;
- numerosi resolver specializzati;
- `runtime/engine/dispatch.py`;
- alcuni detector in `runtime/agent_runtime.py`;
- il lato query del goal reducer Sites.

Quindi il problema non è una sola stop list: è che molti componenti rileggono
la stessa frase con regole differenti.

## 4. Perché non eliminare articoli e preposizioni

La nuova interfaccia deve conservare il testo originale e i suoi span. Non deve
produrre una stringa senza stop word.

Parole apparentemente poco informative distinguono relazioni essenziali:

- `scrivi a Lucia` contiene il destinatario;
- `cerca nelle mail` contiene il contenitore sorgente;
- `salva nello store` contiene la destinazione;
- pronomi e clitici collegano un'azione al risultato precedente.

L'LLM può non trattare articoli e preposizioni come predicati senza che il
programma mantenga una lista per ogni lingua. Cancellarli prima dell'analisi
perderebbe invece informazione.

## 5. La normalizzazione dei verbi

Un'unica sostituzione testuale come `mostrami -> visualizzare` non è sufficiente.
Lo stesso verbo può richiedere operazioni diverse:

- mostrare il contenuto di un file: leggere;
- mostrare i processi ordinati per RAM: osservare una collezione di processi;
- mostrare una pagina: renderizzare o aprire;
- `il file si è spostato`: descrizione di uno stato, non richiesta di spostare.

La rappresentazione deve quindi tenere separati almeno tre livelli:

1. lo span originale;
2. il lemma nella lingua originale, per esempio `mostrare`;
3. il significato contestuale e l'azione canonica, per esempio `show` e `read`.

Nel test multilingue congelato, V21 ha trattato correttamente la famiglia
«mostra il contenuto grezzo» in italiano, inglese, francese, spagnolo e tedesco
come `read/files`. Ha anche distinto in cinque lingue un riflessivo descrittivo
da una richiesta successiva. Questo dimostra che la normalizzazione morfologica
e contestuale LLM è plausibile senza una lista di forme flesse.

La stessa prova ha però mostrato che un campo aggiuntivo chiamato `effect` era
spesso sbagliato e faceva peggiorare la proiezione tecnica. Per questo non deve
essere autorità di routing; la variante lite lo elimina.

## 6. Contratto candidato

La facade proposta è concettualmente:

```text
analyze_request(raw_redacted, literal_index, locale_hint?) -> RequestAnalysis
```

L'input contiene la richiesta originale e token numerati. L'output contiene
predicati ordinati. Ogni predicato ha:

- `predicate_id`, identità stabile del nodo;
- `predicate_anchor_token_id`, provenienza nel testo;
- `role`: request, forbid, condition, description o quote;
- `lemma`, nella lingua sorgente;
- `semantic_gloss_en`, breve etichetta pivot, non testo riscritto;
- `semantic_action` e `resource_scope`;
- patient con span e dominio;
- carrier come unione tipata;
- sink come unione tipata;
- `source_predicate_id` per ellissi, clitici e pipeline;
- proiezione tecnica action/object/qualifier allineata allo stesso id.

Carrier e destinazione sono unioni discriminate. Per esempio, una destinazione
è esattamente una delle seguenti forme:

```json
{"kind": "none"}
```

```json
{
  "kind": "primary_output",
  "start_token_id": 9,
  "end_token_id": 9,
  "object": "files"
}
```

```json
{
  "kind": "secondary_persistence",
  "start_token_id": 9,
  "end_token_id": 9,
  "object": "entries"
}
```

Questa forma impedisce di dichiarare contemporaneamente «nessuna
destinazione» e una destinazione, oppure di rappresentare due volte lo stesso
salvataggio.

## 7. Collocazione nel flusso

Ordine raccomandato:

1. redazione credenziali e secret scan deterministici;
2. indicizzazione letterale di path, URL, email, host, numeri e date;
3. una sola chiamata `RequestAnalysis` vincolata da JSON Schema;
4. validazione language-blind di forma, span, ruoli, id e dipendenze;
5. proiezione deterministica tramite contratti tecnici del catalogo;
6. adapter legacy durante la migrazione;
7. normali gate di sicurezza, binding reale ed esecuzione.

Il passo 3 deve stare dentro il confine dell'intent extraction. Una chiamata
LLM separata che riscrive il testo aggiungerebbe latenza e farebbe perdere
provenienza e dipendenze.

## 8. Parametri sperimentali LLM

Configurazione corrente del candidato:

- modello locale Qwen 3.6 35B-A3B, quantizzazione Q4_K_M;
- temperatura 0;
- seed 42;
- thinking disabilitato;
- una risposta JSON con schema strict;
- massimo output 2600 token per i casi complessi;
- testo originale più token numerati;
- nessun elenco di forme verbali della lingua di input.

Dimensioni misurate:

| Variante | Prompt | Schema | Note |
|---|---:|---:|---|
| V21 | 24.258 caratteri | 2.959 | sink in campi ridondanti |
| V22 | 26.153 | 4.657 | patient/carrier/sink tipizzati ma non discriminati |
| V23 | 25.960 | 6.026 | carrier e sink come tagged union |
| V23lite | 24.283 | 5.684 | V23 senza `effect` |

Questi non sono ancora parametri di produzione. Prompt, output budget e
latenza devono essere ridotti dopo aver congelato il contratto corretto.

## 9. Metodo di valutazione

Il gold storico non può essere usato come un unico numero senza audit. Contiene
contraddizioni tra semantica, catalogo ed espansioni del piano, tra cui:

- `find/processes` contro `get/processes`;
- spreadsheet talvolta oggetto e talvolta qualifier;
- attachment come intento o come argomento;
- store come sink implicito o predicato esplicito;
- immagine semantica contro file carrier;
- send contro share.

La valutazione è quindi lessicografica:

- G0: JSON, schema, span, id, dipendenze e sicurezza;
- G1: numero, ordine e ruolo dei predicati;
- G2: lemma, gloss, azione, scope e ruoli argomentali;
- G3: proiezione sul catalogo;
- G4: comportamento end-to-end;
- G5: compatibilità legacy, con overlay adjudicato;
- G6: stabilità, latenza e costo.

`error=0` significa prima di tutto zero errori G0 e nessuna perdita o esecuzione
abusiva di predicati. Non significa modificare il candidato per imitare un gold
internamente contraddittorio.

Corpus usati:

- legacy: 85 mono + 18 compound + 6 XL;
- adversarial congelato: 70 richieste nuove, 5 lingue, 140 predicati;
- digest adversarial:
  `620803a44fc63e88d4b613963ca55e7f9a82a4c87f2f6c912b1e65904ea472f8`.

## 10. Risultati delle alternative

| Famiglia | Risultato sintetico | Decisione |
|---|---|---|
| Riscrittura libera | mantiene spesso il verbo o restituisce testo quasi identico; perde faccette | scartata |
| Eliminazione stop word | perde relazioni e richiede liste per lingua | scartata |
| Token labeling semplice | utile per gli span, insufficiente per significato e multiquery | componente, non soluzione |
| Lemmatizzatore tradizionale | nessun runtime/modello multilingue presente; non risolve polisemia, ruoli o coreferenza | non competitivo come soluzione completa |
| Embedding BGE | action 5/85 raw, route 17/85; ibrido tuned regredisce fuori campione | scartato come autorità |
| Enum diretto action/object | 1/5 sui casi difficili | scartato |
| Facet graph ricco | circa 1/5 nel run reale, pur compilatore ideale 5/5 | scartato |
| Due chiamate gloss -> route | 1/5 e doppia latenza | scartato |
| Schema compatto | 2/5 o mode collapse | scartato |
| Enum globale di circa 284 route | decoder bloccato oltre 250 s | scartato |
| Arbiter logprob | 2/7, stabilmente sbagliato | scartato |
| Array sink opzionale | sempre vuoto, perde recall | scartato |
| V21 two-phase | 18/24 raw; 20/24 dopo normal form; buona analisi semantica | base utile |
| V22 typed graph | corregge la semantica dei casi mirati, ma duplica sink | superata |
| V23 tagged graph | full raw 92/109, 2 invalid; grounding 61,5% | base strutturale superata |
| V23lite senza effect | full raw 98/109, 141/159 clausole, 0 invalid | supera il target temporaneo K1 |

## 11. Numeri di riferimento

Baseline legacy:

- 96/109 casi exact;
- 141/159 clausole exact;
- compound 13/18;
- XL 1/6;
- nessuna flakiness osservata sui run K=3 compound/XL;
- mono disponibile solo K=1 nell'artifact aggregato.

V21:

- mono V6 precedente: 85/85, ma non è lo stesso candidato congelato;
- compound+XL V19: 20/24, con una invalidità;
- compound+XL V21: 18/24 raw e 21/24 validi;
- normal form language-blind: 24/24 validi;
- projector mirato: 20/24, senza regressione sui casi già esatti;
- adversarial independent: tutti i 75 predicati richiesti emessi;
- frame route raw adversarial: 40/50;
- dopo normal form: 50/50 strutturalmente validi;
- gloss contestuale: 48/50;
- semantic action: 46/50;
- `effect`: soltanto 5/50, quindi non affidabile.

V23lite full:

- Phase2 raw: 98/109, contro baseline 96/109;
- clausole: 141/159, pari alla baseline;
- mono: 82/85;
- compound+XL: 16/24;
- invalid: 0;
- p50 3,73 s, p95 10,55 s;
- vecchio compiler folded: 86/109, quindi escluso dal nuovo percorso;
- supera il target temporaneo K1, ma non ancora il target finale 109/109
  coerente/adjudicato;
- restano grounding, stabilità e adversarial da certificare.

Confronto live rappresentativo, stessi id:

- baseline independent: 9/10 firme operative;
- V21 raw frame: 8/10;
- V21 dopo il vecchio compiler: 5/10;
- baseline warm p50 circa 0,6--0,7 s;
- V21 p50 circa 4,6 s;
- V23 sui due target: 2/2 contro baseline 1/2, p50 circa 10,8 s.

La differenza raw-frame/compiled dimostra che la parte LLM non è l'unico
problema: il projector corrente può trasformare un'analisi giusta in una route
sbagliata.

## 12. Catalogo: il vero prerequisito del massimo

Una proiezione senza eccezioni linguistiche richiede metadati tecnici nei
manifest. Il census corrente ha trovato:

- 96 route nel catalog snapshot;
- 89 route derivabili meccanicamente dal nome e 7 speciali;
- 491 occorrenze di argomenti e 255 nomi distinti;
- 51 manifest allineati, 30 con drift, 15 assenti dal checkout;
- 11 builtin non presenti nello snapshot, incluso `write_entries`;
- 83 annotazioni manuali ancora necessarie;
- 998 suggerimenti automatici non revisionati;
- 0/96 contratti pronti per uso runtime fail-closed.

I manifest devono dichiarare, senza termini della lingua utente:

- action/object canonici;
- effetto tecnico solo dove realmente necessario;
- patient/carrier/output domain;
- ownership dell'artefatto primario;
- ruolo e binding degli argomenti;
- regola di proiezione e compatibilità del qualifier.

I suggerimenti automatici non devono essere letti in produzione prima della
revisione: nel test adversarial il projector aggressivo ha ridotto una firma
raw 40/50 a 13/50.

## 13. Codice da rimuovere o migrare dopo il cutover

### Rimuovere quando non esistono più consumer raw-query

- tabelle di forme verbali e strip dei clitici in `runtime/prefilter.py`;
- object hints e stop list query-side;
- mapping superficiali in `runtime/vocab.py`;
- prompt intent IT/EN duplicati;
- split linguistico e rilevamento verbi in `runtime/compound_decomposer.py`;
- repair lessicali in `runtime/engine/dispatch.py`;
- liste e regex linguistiche nei resolver specializzati;
- detector query-side duplicati in `runtime/agent_runtime.py`;
- il prototipo non connesso `runtime/nlu.py` nella sua forma attuale.

### Migrare a consumer di RequestAnalysis

- ranking del prefilter;
- ordering, time window, recurrence e count;
- args extractor per la parte non letterale;
- target device e account resolver;
- decomposizione, dataflow e recipient/channel;
- fingerprint della cache;
- goal reducer Sites per la sola parte che interpreta la richiesta.

### Non rimuovere

- redazione di credenziali e secret scan prima dell'LLM;
- path, URL, email, host e altri literal validator;
- enum tecnici e validazione del catalogo;
- schema degli argomenti e provenance;
- binding reale di device, account, calendario e provider;
- ownership, connectivity e no-silent-fallback;
- policy di mutazione, destinatario, canale, ACL e approvazione;
- dataflow e prerequisite tecnici;
- DOM evidence, selector, visibilità, ambiguità e sensitive gates;
- verifica post-esecuzione.

## 14. Piano di migrazione

1. Congelare schema, modello, prompt, catalog snapshot e gold adjudicato.
2. Versionare `RequestAnalysis` e il validator.
3. Introdurre una facade con adapter legacy, inizialmente in shadow mode.
4. Completare i contratti tecnici dei manifest.
5. Confrontare baseline e candidato sullo stesso input e con gli stessi hash.
6. Migrare i consumer una famiglia alla volta.
7. Rimuovere una lista soltanto quando il relativo reader raw-query è zero.
8. Conservare fallback immediato e telemetria di divergenza.

## 15. Gate di cutover

Il massimo non può entrare in produzione finché non sono soddisfatti tutti i
seguenti punti:

- target temporaneo di sostituzione: almeno 96/109 sulla suite legacy, zero
  invalidità e nessuna regressione non ambigua;
- target finale aggiornato: **109/109 sul gold coerente/adjudicato**, senza
  ottenere il punteggio tramite eccezioni legate alle frasi del gold; il
  punteggio sul legacy originale resta pubblicato separatamente;

- zero invalidità strutturali dopo normal form;
- nessuna perdita o esecuzione indebita di predicati;
- risultato pari o migliore del baseline su ogni famiglia non ambigua;
- gold contraddittorio adjudicato e congelato;
- stabilità K>=5 sui boundary e sui mismatch;
- test IT/EN/FR/ES/DE e almeno una lingua holdout;
- test di timeout, output malformato, server down e fallback;
- latenza entro un budget accettato;
- contratti catalogo pronti per tutte le route usate;
- suite sicurezza e credenziali verde;
- zero consumer linguistici fuori dalla facade.

## 16. Stato del repository

Questa analisi non ha modificato il runtime di produzione. I benchmark e i
prototipi sono stati eseguiti in `/tmp`; il presente documento è la memoria
durevole delle conclusioni. Le modifiche preesistenti nel worktree non sono
state alterate.

Test unitari eseguiti sullo snapshot corrente:

- suite core selezionata: 170 passed;
- suite audit query/NLU/sites: 293 passed;
- suite audit resolver/safety/pipeline: 316 passed;
- 0 failure nelle selezioni; i run certificano la baseline, non il candidato
  ancora esterno al runtime.

## 17. Verdetto corrente

La riscrittura libera e la cancellazione delle stop word sono definitivamente
scartate. La normalizzazione dei verbi deve essere una proprietà strutturata e
contestuale, non una sostituzione di stringhe.

Il miglior disegno corrente è V23lite come forma di `RequestAnalysis`: predicate graph,
ruoli, lemma/gloss, patient/carrier/sink tipizzati e nessun `effect` usato per
il routing. Ha superato il gate numerico temporaneo K1 con 98/109 e zero
invalidità. La normal form deterministica è promossa; il projector semantico
aggressivo è bocciato finché i manifest non sono completi e revisionati.

Il cutover effettivo resta sospeso finché V24 non raggiunge il target finale,
le ripetizioni di stabilità, la suite adversarial e i test di outage/fallback.
Le liste non sono ancora rimovibili dal runtime, ma la soglia di sostituzione
numerica iniziale è stata superata.

## 18. Convergenza V24.1/V25 e nuovo target 100%

La normal form V24.1, congelata prima della cross-validation, porta i frame
V23lite a 107/109 sul legacy originale e 108/109 sul gold coerente, con 109
frame validi e zero regressioni. Il delta rispetto a V24 deriva da due soli
binding tecnici: ownership dell'artifact per il sink e relazione di argomento
per un allegato esistente. Il projector non legge testo, lemma o gloss.

Il caso spreadsheet residuo è una contraddizione della suite: la riga
COMPOUND attende `create/files`, ma il commento normativo nello stesso file
dichiara che `create/files` non esiste e il caso XL equivalente attende
`create/files_spreadsheet`. Il risultato corretto va formalizzato con overlay
versionato; il punteggio legacy originale deve continuare a essere mostrato.

V25 risolve semanticamente il residuo location: 7/7 richieste in sei lingue,
incluso portoghese holdout, producono `get/places`, e 5/5 controlli negativi
restano corretti. Tuttavia il campo evidence `question_binding` rimane `none`:
la route è giusta, ma la prova strutturata non è completa. La fase successiva
deve fondere decisione e prova in una tagged union obbligatoria del risultato
richiesto, evitando un claim opzionale parallelo.

Il target operativo diventa quindi:

- 109/109 adjudicato e punteggio legacy pubblicato a parte;
- zero invalidità;
- grounding completo per ogni predicato esplicito o interrogativo;
- zero regressioni sulla suite non ambigua;
- stabilità K>=5 sui gate;
- successivo holdout storico complesso, redatto e congelato prima del run.

## 19. Latenza e forma finale

La misura offline sui 109 output V23 mostra che, a prompt già in cache, la
latenza è quasi interamente decoding: circa 789 ms fissi più 9,915 ms per token
di output (R² 0,9983). V23 emette circa 260 token per nodo. Un grafo leggibile
senza duplicare phase 1/phase 2 scende a circa 63 token per nodo e proietta una
mediana mono di circa 1,42 s; la variante tuple scende a 41 token/nodo e circa
1,21 s. Queste sono proiezioni, non ancora prove live.

La forma compatta deve essere provata dopo il 100% semantico, prima su un mix
congelato. Varianti compatte precedenti sono andate in mode collapse; non si
può inferire equivalenza dalla sola trasformazione post-hoc. Il costo del
projector V24.1 è trascurabile (mediana 2,50 ms).

## 20. Espansione storica

Lo storico locale dei turni contiene migliaia di richieste reali e molte
multi-step. Verrà usato soltanto dopo il raggiungimento del target sulla suite
congelata. La selezione deve essere blind rispetto all'output del candidato,
redatta con placeholder tipizzati, deduplicata contro legacy/adversarial e
adjudicata indipendentemente. Nessuna query grezza, credenziale o informazione
personale deve entrare negli artifact versionati.

## 21. Separare la prova semantica dalla route

V25.2 ha mostrato un errore di direzione: quando claim semantico e route erano
in conflitto, il feedback chiedeva di ottenere `get/places`; il modello spesso
ha modificato route e patient per rendere coerente un claim sbagliato. Il
validator aveva trasformato una spiegazione fallibile in autorità.

La correzione è sperimentare prima una fase 1 autonoma e piccola. Essa descrive
clausole, tipo di domanda, variabile risposta, soggetto/persona/deissi, tempo,
relazione ed evidenza. Non conosce catalogo o route. Soltanto un frame phase 1
valido viene poi confrontato con contratti tecnici; una divergenza non viene
mai riparata sovrascrivendo silenziosamente l'altra parte.

Questa separazione è anche la risposta più pulita al problema originario della
normalizzazione dei verbi: il modello non riscrive liberamente la frase e non
cerca sinonimi in liste. Produce lemma/relazione/ruoli ancorati alla sorgente;
la route tecnica è una proiezione successiva e verificabile.

La segmentazione di evidenza deve essere Unicode UAX #29 o equivalente.
Whitespace-only è già falsificata dal cinese; tabelle per singole lingue o
script riprodurrebbero il problema i18n che si vuole eliminare.

## 22. Checkpoint V26.2 minimal Phase 1

La prova V25.3 ha separato due numeri prima confusi: il primo output riconosce
correttamente il binding desiderato in 34/34 casi, ma coincide con tutta la
tupla semantica attesa soltanto in 24/34. Il primo numero dimostra che il
segnale esiste; non dimostra ancora che la rappresentazione sia completa.

V26.2 elimina i campi ridondanti di V25.3 e conserva una tupla minima:
ruolo, atto linguistico, relazione, soggetto, persona grammaticale, tempo e
tre evidenze tipizzate obbligatorie. L'evidence è un oggetto con tagged union
separate per relazione, soggetto e tempo. Non contiene route, action, tool,
object di prodotto o esempi della lingua sorgente; usa segmentazione Unicode
UAX #29 e fa una sola chiamata senza retry.

Stato al freeze:

- review indipendente: eseguire soltanto V26.2, non V25.3.1/V26/V26.1;
- 27/27 mutation e 21/21 controlli sul legame freeze/audit/review;
- zero righe surface/miste e zero overlap di almeno tre token su 109+34+70;
- run nativo inizialmente ancora da contabilizzare;
- gate completi e hash: §16 di
  `internal/design/handover_request_analysis_8_8_2026.md`.

L'accettazione richiede contemporaneamente 34/34 frame valutabili, 34/34
binding, 34/34 tuple semantiche, 9/9 positivi, 0/25 leakage ed evidenza completa.
Soltanto dopo si integra questa Phase 1 nella stessa chiamata del grafo
V23lite/V24.1. Non si aggiunge una seconda chiamata e non si corregge una route
per farla concordare con un claim fallibile.

La base di prova corrente è 109 principali + 34 controlli mirati + 70
adversarial = 213 casi. Dopo il 109/109 si congela un holdout storico aggiuntivo
che enfatizza multi-azione, multi-dominio e dipendenze tra clausole.

### 22.1 Esito live e correzione di rotta

L'unico run V26.2 K1 sui 34 ha prodotto 26 casi valutabili, 20 binding corretti,
5 tuple semantiche esatte, 3/7 positivi valutabili e 2/19 falsi positivi; 8 casi
non sono valutabili e 7 di questi dichiarano ambiguità. L'evidenza è completa
in 17/26. Mediana 3,109 s, p95 4,195 s, 34 chiamate e zero retry.

La semplificazione ha oltrepassato il punto utile. Il prompt V26.2 definisce
soltanto `spatial.located_at` e non spiega le altre relazioni enumerate; ha
anche perso i confini tecnici per richieste, condizioni, quote, domande
aperte/polari, soggetti e tempo. Ne seguono 17 errori di relazione e 15 di
`interpretation`, dominati da `unsupported`/`ambiguous`.

La prossima ipotesi non reintroduce campi ridondanti: combina lo **schema
minimo** con un **prompt definizionale completo** per il registro semantico e
i suoi vincoli. Queste definizioni sono ontologia tecnica language-neutral,
non esempi, sinonimi o trigger della lingua sorgente. Il nuovo freeze deve
essere revisionato indipendentemente prima di un altro run.

## 23. Checkpoint V26.4 typed relational Phase 1

V26.3 ha confermato la diagnosi sul prompt ma è stato bloccato prima del run:
validator fail-open, dipendenze transitive non interamente legate e pre-gate
autore non separato. L'oracolo indipendente ha poi corretto l'obiettivo: 31
proiezioni esatte e 3 ambiguità tipizzate; la tupla piatta a sette campi non è
il gold.

V26.4 usa invece un grafo normalizzato con un atomo primario per clausola,
producer ausiliari, output tipizzati e edge `from_atom_output` soltanto verso
atomi precedenti. Firme, output slot/type e adapter derivano da un registro
tecnico congelato; il modello non ripete ruolo, tipo o coercion. Il validator
controlla arità, binding, proof compatibility, role/speech/scope, copertura
mista, alternative, DAG, profondità e limiti. Il runner è self-contained,
UAX #29, una call e zero retry.

Il primo schema relation-specific era 236.377 byte ed è stato scartato senza
inferenza. Lo schema finale generico ha due soli rami atomici, 14.518 byte
formattato e 7.302 canonico; non riduce il validator, che resta
registry-driven e fail-closed.

Freeze offline:

- core 69/69;
- probe indipendente 125/125;
- rappresentazione e gate oracolo 34/34;
- contamination 0 righe surface/mixed, 0 query intere e 0 overlap >=3 su
  109 + 34 + 70;
- `verify_freeze()` PASS;
- server/model call 0;
- freeze SHA
  `83d3b4441d83d8f90e5eb591f9eeaf052885150e6806cd3ceefd046d948ccde6`.

La review indipendente finale passa il **solo graph contract** sui byte
congelati (MD SHA
`9e77d7261a144615d2329febefcfbab1d2b05c174e2c43819d149bf8d6dca35a`,
JSON SHA
`ccbc518837af72a60e67da5e2ae445bffd17f64ff1dfba727d54fadcd2c8ac62`).
Il gate deve legare anche l'addendum SHA
`53254dca278c2d2da7f4ac2958502866b3758dd332f672643950c87ccffe1c4d`,
che corregge la descrizione della profondità senza cambiare il PASS.
Non certifica accuracy live o cutover. L'author gate resta
`inference_allowed=false`, il gate esterno manca ed è respinto dal runner.

Artifact e replay:
`internal/tools/request_analysis_lab/candidates/v264/README.md`. Il prossimo
passo, se autorizzato dal coordinatore, è un lock esterno hash-chained e un
solo run nativo sui 34. Anche un eventuale 34/34 deve essere seguito dai 109
principali e dal holdout storico multi-azione/multi-dominio prima di qualsiasi
integrazione runtime.
