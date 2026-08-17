# Specifica formale holdout blueprint-first v0.1

Stato: **DESIGN ONLY — ZERO QUERY, ZERO GOLD DI CASO, ZERO RUN**  
Data: 2026-08-13  
Scopo: validare il processo di costruzione del nuovo holdout prima di produrre testo naturale.

## 1. Principio

Il processo è semantic-first, non query-first:

`autorità -> obblighi semantici -> doppio gold pre-query -> proiezione autore -> query nativa -> doppia adjudication post-query -> confronto esatto`

Una query non determina o corregge il gold. Se la query non realizza esattamente il gold pre-query, la query e l'intero tentativo pilota sono respinti.

## 2. Autorità e commitment sperimentale

L'autorità semantica è composta esclusivamente da:

| Artefatto | SHA-256 |
|---|---|
| `intent_shadow_registry_v0_1.json` | `4808bfe8b971ee8839c715f7aac91c6473d865724540890e4ec43dba136243d8` |
| `internal/design/contratto_ombra_prototipo_intento_12_8_2026.md` | `3b82156a3a3d3414f211926a32ccd40593de1d6faa6adfcf76474c8d32717aa5` |
| `holdout_v0_1/adjudication_contract.json` v1.2 | `b143534a2d6f80e397092237c6ead60e14734863cfce7684117b1b9cbdbbc252` |
| `candidate_v0_1/intent_shadow_model_v0_1.schema.json` | `8e229746c152a6a6bc6a5097e3e20a0c4581a3313736d99ae1fd771b5c3c2dc2` |

Questo è lo schema canonico completo del gold (`body`, nodi tipizzati e `data_from`). Lo schema compatto model-facing del candidato (`steps`) non è autorità di adjudication e non può sostituirlo.

Il challenger non è autorità e non è leggibile da autori o adjudicatori. È soltanto un commitment cronologico separato:

- `prompt_challenger_v0_1.freeze.json`
- SHA-256 `91f7acb5b582f1ad064ed19e788207c8a0452e1a712d934a60f8e7e59463d966`

Un futuro manifest deve ricalcolare gli hash dai byte reali. Valori copiati manualmente senza verifica sono un errore bloccante.

## 3. Artefatti e ruoli

### 3.1 Blueprint proposal

Ogni proposal contiene, senza testo di query e senza expected proposto:

- ID opaco;
- language tag;
- cella e sottotipo;
- clausole positive indispensabili;
- clausole negate;
- clausole fuori scope;
- controllo di sistema, se presente;
- dipendenze producer-consumer;
- ordine semanticamente necessario;
- ownership di barriera o ramo;
- tassonomia astratta dello scenario e ruoli degli argomenti;
- vincoli di superficie indispensabili;
- authority manifest hash.

Per operazioni indipendenti, il proposal contiene anche `canonical_mention_order`: l'ordine in cui gli obblighi devono essere menzionati nella futura query. È un vincolo di canonicalizzazione richiesto dal contratto v1.2, non un ordine temporale e non una dipendenza.

Ogni clausola usa riferimenti interni al glossario chiuso delle capacità. Non contiene nomi propri, numeri decorativi, frasi d'esempio o formulazioni suggerite.

### 3.2 Gold semantico pre-query

Due adjudicatori indipendenti del pool A ricevono soltanto proposal e autorità. Non vedono un expected proposto, il challenger o altri casi.

Ognuno deriva:

- expected canonico completo;
- sette safety applicability flag;
- confidence;
- authority hash;
- proposal hash.

Il gold nasce soltanto se i due record coincidono esattamente su expected, safety, authority e proposal hash e hanno entrambi confidence alta. Il gold è poi immutabile. Disaccordo, confidence non alta o gap autoritativo respingono il blueprint; niente arbitrato.

### 3.3 Proiezione autore

La proiezione è prodotta meccanicamente da proposal e glossario. Contiene solo obblighi semantici in forma atomica. Non contiene:

- route, reason o expected canonici;
- testo del gold;
- frasi o esempi;
- nomi propri, numeri o dettagli decorativi prescritti;
- ordine sintattico, salvo quando l'ordine è semanticamente necessario;
- informazioni su altre lingue o casi.

La proiezione ha un proprio hash e deve essere verificata bidirezionalmente contro il proposal prima dell'authoring.

Eccezione chiusa: per operazioni indipendenti la proiezione trasporta `canonical_mention_order` come lista di riferimenti agli obblighi. Prescrive soltanto quale obbligo menzionare prima, senza suggerire sintassi e senza creare `data_from` o semantica temporale.

### 3.4 Query

Un contesto autore nuovo produce una sola query nella lingua assegnata. Sceglie autonomamente formulazione, entità e dettagli locali entro gli obblighi ricevuti. L'output è one-shot e viene sigillato; non è corretto dopo una review.

### 3.5 Adjudication post-query

Due adjudicatori indipendenti del pool B ricevono soltanto:

- query con ID opaco;
- language tag;
- identico pacchetto autoritativo;
- schema chiuso della ricostruzione obligations;
- glossario chiuso delle capacità e tassonomia astratta degli scenari;
- envelope del task con `proposal_sha256` opaco.

Schema, glossario e tassonomia sono generali e identici per tutti i casi; non contengono scenario atom, expected o indizi case-specific. I reviewer non vedono proposal, cella, projection, safety tag, gold, identità autore, altro giudizio o posizione nella matrice. Il pool B è distinto dal pool A e nessun reviewer può partecipare a entrambe le fasi.

I giudizi vengono sigillati prima del confronto. Un custode verifica:

`giudizio B1 == giudizio B2 == gold semantico pre-query`

su expected completo, safety flag e authority hash, con confidence alta.

Ogni giudizio B1/B2 contiene obbligatoriamente anche:

- `query_sha256`;
- `proposal_sha256`;
- una ricostruzione strutturata delle semantic obligations osservabili nella sola query: clausole positive, negate e fuori scope; controllo; dipendenze; ordine di menzione; ownership di ramo/barriera; ruoli e tassonomia di scenario.

Prima del confronto il custode verifica:

- `B1.query_sha256 == B2.query_sha256 == sealed_query.sha256`;
- `B1.proposal_sha256 == B2.proposal_sha256 == semantic_gold.proposal_sha256`;
- ricostruzione B1 uguale a ricostruzione B2;
- ricostruzione concorde esattamente col proposal, inclusi gli atom che non compaiono nell'expected canonico.

I reviewer B non vedono il proposal: il relativo hash è inserito dal custode nell'envelope del task senza esporne il contenuto. La ricostruzione viene confrontata soltanto dopo il sigillo dei due giudizi.

### 3.6 Envelope post-query

Il gold pre-query non viene modificato. Un envelope separato lega:

- proposal hash;
- projection hash;
- author bundle hash;
- gold hash;
- query hash;
- hash dei due giudizi post-query;
- esito del confronto esatto;
- rubriche e authority hash.

## 4. Relazioni fail-closed

Il validatore deve verificare in entrambe le direzioni:

- ogni clausola positiva indispensabile compare nell'expected quando rappresentabile;
- nessuna clausola negata compare come operazione;
- ogni clausola fuori scope indispensabile influenza correttamente la root completa;
- ogni dipendenza ha producer, consumer, visibilità e dominanza valide;
- per operazioni indipendenti, l'expected segue `canonical_mention_order`, la query realizza lo stesso ordine di menzione e non esiste `data_from` fra esse;
- ogni ramo e barriera possiede esattamente il proprio body;
- system control è root esclusiva; il mixed-root indispensabile è trattato secondo l'autorità;
- cella, sottotipo e safety flag sono conseguenze coerenti degli obblighi;
- nessun atom è orfano e nessun elemento dell'expected è inventato;
- operazioni indipendenti seguono l'ordine di menzione e non hanno `data_from`, secondo la decisione Roberto già registrata nel contratto v1.2.

Validità JSON o schema da sole non bastano.

## 5. Fingerprint e indipendenza

Il fingerprint canonico comprende:

- cella e sottotipo;
- famiglie delle clausole positive, negate e fuori scope;
- root kind;
- topologia del grafo e dipendenze;
- polarità;
- ownership di ramo o approvazione;
- tipo di controllo;
- tassonomia dello scenario;
- ruoli degli argomenti.

Nomi, numeri, lingua e punteggiatura non rendono due blueprint diversi.

Nel pilot, tutti i 20 fingerprint devono essere distinti. Expected uguali sono ammessi quando il significato completo e il fingerprint differiscono. Nel futuro set da 320, l'unica collisione semantica inevitabile ammessa è S4 undo-only, dichiarata e contata esplicitamente; resta vietata la traduzione o parafrasi superficiale parallela.

Un reviewer cross-language confronta inoltre query, parafrasi letterali e firme semantiche. È blocker condividere, oltre alla struttura obbligata dalla cella, famiglia d'azione, scenario/oggetto, ruoli e almeno un dettaglio distintivo.

## 6. Isolamento operativo

- Un nuovo contesto senza cronologia ereditata per ogni query.
- Una sola query e una sola lingua per contesto.
- Bundle di input esatto e hashato.
- Nessun riuso del contesto dopo output o review.
- Nessun accesso a prompt, candidate, run, vecchi pannelli, altri assignment/output, gold o review.
- Rete e GPU non usate.
- Qualunque accesso vietato osservato respinge il tentativo.

Questo è isolamento procedurale verificabile, non una garanzia crittografica contro un autore malevolo. Non va descritto diversamente.

## 7. Pilot chiuso da 20 query

Il pilot valida il processo, non l'accuratezza del modello e non la qualità statistica del futuro holdout.

| Lingua | Blueprint A | Blueprint B |
|---|---|---|
| en-GB | G1 singola azione nota | S3 due rami con azioni diverse |
| it-IT | G2 azioni indipendenti | S4 undo-only |
| es-MX | G3 producer-consumer | S1 azione fuori barriera più azione approvata |
| de-DE | G4 mixed known+outside | S2 positiva più diversa azione negata |
| tr-TR | G5 indiretta/ellittica univoca | S5 undo più operazione |
| sr-Cyrl-RS | G1 famiglia diversa da en-GB | S6 outside-only |
| ar-EG | G2 famiglie diverse da it-IT | S3 rami diversi da en-GB |
| hi-IN | G3 dipendenza diversa da es-MX | S2 coppia diversa da de-DE |
| ja-JP | G4 outside-only | S1 approvazione che possiede due azioni |
| zh-Hant-TW | G5 dipendenza referenziale a distanza | S5 operazione diversa da tr-TR |

La matrice copre tutte le undici celle, entrambi i sottotipi G4 e tutti i fenomeni safety. Gli assignment non contengono scenari o frasi condivisi.

## 8. Rubriche binarie

Prima del pilot devono essere congelate rubriche per:

- grammatica e naturalezza regionale;
- univocità della query;
- conformità a cella e semantic obligations;
- scope delle capacità;
- dipendenza, ordine, ramo e barriera;
- anti-template e anti-traduzione;
- confronto gold esatto;
- isolamento e integrità degli artefatti.

`Incerto`, confidence non alta, campo extra, mismatch o eccezione valgono FAIL.

## 9. Gate zero-error

GO soltanto se, al primo tentativo della versione corrente:

- authority, schema, glossario, rubriche e toolchain hanno hash esatti;
- 20/20 proposal sono relazionalmente validi;
- 20/20 gold pre-query hanno accordo esatto del pool A;
- 20/20 fingerprint sono distinti;
- 20/20 proiezioni sono equivalenti e senza leakage;
- 20/20 contesti sono nuovi e i bundle input sono esatti;
- 20/20 query passano lingua, univocità, cella, scope e obligations senza correzioni;
- zero template o near-translation interlingua;
- 20/20 giudizi post-query del pool B coincidono tra loro e col gold;
- 20/20 ricostruzioni post-query delle obligations coincidono tra loro e col proposal;
- 20/20 confidence sono alte;
- zero arbitraggi, deroghe, riparazioni o accessi vietati;
- il catalogo mutation pre-frozen è interamente verde.

Una sola violazione significa NO-GO.

### 9.1 Catalogo mutation minimo

Prima di qualsiasi query, una suite versionata e hashata deve provare che ciascuna delle seguenti mutazioni è respinta:

- authority, challenger commitment, proposal, projection, author bundle, gold o query hash stale/scambiato;
- associazione di un gold o giudizio al case ID sbagliato;
- campo obbligatorio mancante, campo extra o versione errata;
- expected, safety flag o confidence alterati;
- clausola positiva, negata o outside aggiunta/rimossa;
- polarità, root kind, control kind o capability family alterati;
- dipendenza, dominanza, ordine di menzione, branch o barrier ownership alterati;
- atom orfano o elemento dell'expected senza atom sorgente;
- collisione fingerprint non autorizzata;
- language tag, cella o sottotipo alterati;
- riuso di un reviewer fra pool A e B o riuso di un contesto autore;
- accesso vietato, query aggiuntiva o correzione post-sigillo;
- giudizi B riferiti a query hash differenti;
- ricostruzione obligations diversa dal proposal pur con expected uguale.

La suite positiva prova il percorso pulito. Catalogo, fixture sintetiche e risultati attesi vengono congelati prima del pilot; non contengono query naturali del futuro holdout.

## 10. Fallimento e nuovo ciclo

Ogni fallimento viene classificato prima di agire:

- autorità/specifica;
- blueprint/fingerprint;
- proiezione;
- tooling;
- isolamento/orchestrazione;
- authoring linguistico;
- conformità semantica;
- collisione cross-language;
- adjudication.

Il tentativo resta registrato con soli hash, fingerprint e motivo. Non si modifica la singola query per salvarlo e non si ripete per semplice risampling. Si cambia soltanto l'artefatto responsabile, si incrementa la versione del processo e si rigenera integralmente con blueprint, contesti e query nuovi.

## 11. Passaggio a 320

Il 20/20 autorizza soltanto l'esecuzione del processo su scala. Non certifica in anticipo il corpus.

Lo scale-up deve mantenere invariati:

- authority e challenger commitment;
- schema, glossario, rubriche e validator;
- un solo contesto nuovo per query;
- pipeline e separazione dei ruoli;
- fingerprint globale;
- doppio gold pre-query e doppia adjudication post-query;
- gate individuali e cross-language.

Ogni modifica richiede un nuovo pilot. Tutti i 320 casi devono superare gli stessi gate; il pilot non sostituisce alcun controllo finale.

## 12. Limiti dichiarati

Zero errori su 20 significa zero difetti noti nell'esecuzione pilota, non perfezione matematica. Non prova accuratezza del modello, copertura completa delle combinazioni lingua-cella o assenza universale di translationese. Evita però di spendere 320 casi per scoprire errori di specifica già rilevabili prima dell'authoring.
