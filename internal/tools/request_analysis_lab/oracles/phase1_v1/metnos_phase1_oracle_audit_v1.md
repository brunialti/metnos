# Audit indipendente dell'oracolo Phase 1 — 34 controlli

Data: 2026-08-08  
Ambito: oracolo semantico minimo per routing e sicurezza della posizione corrente  
Esclusioni rispettate: nessuna chiamata al server; nessun output V26.3 letto; nessuna query grezza copiata fuori dalla fixture esistente.

## Verdetto

Il gold binario dei 34 casi è valido per la sola domanda «la posizione corrente è la risposta diretta?»: **9 positivi e 25 negativi restano invariati**.

Il confronto esatto sui sette campi correnti non è invece un oracolo oggettivo. Mescola fatti necessari, diagnostica, stato di copertura e una tupla posizionale che non conserva lo stesso significato in tutti i casi. Non deve essere usato come criterio di accettazione né come motivo per costringere il modello a una lettura non sostenibile.

L'oracolo sostitutivo congela cinque verifiche separate:

1. binding diretto;
2. dipendenza dalla posizione corrente;
3. copertura semantica tipizzata;
4. sicurezza;
5. completezza e validità delle prove.

Non abbassa alcun requisito. Conserva 34/34 sul binding binario e aggiunge obblighi che il vecchio gold non misurava.

## Indipendenza e ordine dell'audit

La schema e l'overlay tipizzati sono stati costruiti e hashati prima del confronto con i campi e il valutatore V26.3. Soltanto dopo il freeze dell'oracolo sono stati letti runner, schema, prompt e pre-gate V26.3. Non sono stati letti risultati, delta o review contenenti output del candidato.

Fonti usate:

- fixture esistente: `internal/tools/request_analysis_lab/question_focus_controls_v1.json`;
- contratto `get_location` e confini `share`/`send` nei manifest;
- invarianti del progetto e documenti di handover;
- schema e codice del candidato soltanto per il confronto finale dei campi.

## Perché il vecchio exact a sette campi non è oggettivo

### 1. La tupla sorgente non è veramente tipizzata

Il contratto dichiara una tupla con atto, relazione, focus, entità e tempo. Alcuni negativi riutilizzano però le stesse posizioni per concetti diversi:

- nei due casi `neg.people_here.*`, `current_context` è l'argomento spaziale, non il tempo;
- in `neg.polar.it`, `explicit_place` è l'argomento posizione, non il tempo;
- nei casi `neg.nearby_operand.*`, candidato e posizione dell'attore sono argomenti distinti della relazione, non soggetto e tempo;
- in `neg.relative_clause.it`, `relative_clause` descrive lo scope sintattico, non un riferimento temporale.

Un'uguaglianza di stringhe su questi slot misura la convenzione del file, non la stessa semantica tra i casi.

### 2. Tre casi non hanno una sola lettura esatta sostenibile

- `neg.polar.it`: senza punteggiatura interrogativa può essere un'asserzione scritta oppure una domanda polare informale. Entrambe sono negative per il binding diretto.
- `neg.share.it`: può chiedere l'invio di uno snapshot oppure la concessione di accesso continuativo. Il progetto distingue esplicitamente `send` da `share`; entrambe le letture richiedono la posizione come dipendenza e un effetto outbound.
- `neg.relative_clause.it`: la destinazione può riferirsi alla posizione corrente dell'attore oppure a quella dei file. Essendo un'azione con effetto, l'ambiguità richiede chiarimento prima dell'esecuzione.

L'oracolo tipizzato richiede tutte le alternative congelate. Un generico `ambiguous` senza alternative non soddisfa la copertura.

### 3. Campi diagnostici o derivabili

| Campo corrente | Classificazione | Motivo |
|---|---|---|
| `role` | necessario | separa richiesta, condizione, descrizione e citazione |
| `speech_act` | necessario | separa domanda aperta, polare, imperativo e asserzione |
| `relation` | necessario | impedisce collisioni tra spazio fisico, filesystem, runtime, documento e workflow |
| `subject_ref` | insufficiente | descrive soltanto il primo argomento e sovraccarica soggetto, risorsa e operando |
| `time_scope` | insufficiente come campo globale | deve essere un argomento della relazione quando la firma lo richiede |
| `grammatical_person` | solo diagnostica | ridondante per il routing dopo il binding tipizzato; non è realizzata uniformemente tra lingue |
| `interpretation` | verifica di copertura separata | non è un fatto della relazione e non va contato nel confronto semantico esatto |
| `predicate_segment_id` | prova condizionale | serve soltanto quando la prova usa morfologia del predicato |
| `focus_role` | derivato | è il ruolo dell'unico argomento ignoto |
| tipo della risposta | derivato | proviene dal tipo dell'argomento ignoto nel registro |

## Oracolo tipizzato congelato

Ogni proiezione usa:

- ruolo della clausola;
- atto linguistico;
- predicato di relazione con firma ordinata;
- argomenti `bound`, `unknown` o `derived` con ruolo e tipo;
- argomento temporale soltanto nelle relazioni che lo possiedono;
- obblighi di prova distinti.

La posizione richiesta si deriva da:

```text
spatial.located_at(
  entity   = actor.current,
  position = unknown:spatial.position,
  time     = time.current
)
```

Non viene emesso un `focus_role`, un tool o un binding di capacità.

Composizione dei 34 casi:

- 31 proiezioni esatte;
- 3 ambiguità tipizzate con due alternative ciascuna;
- 9 binding diretti richiesti;
- 25 binding diretti vietati;
- 3 dipendenze certe dalla posizione corrente (`nearby` due volte e outbound position una volta);
- 3 casi deittici che richiedono risoluzione del contesto prima di scegliere la sorgente della posizione;
- 1 azione con dipendenza non risolvibile prima del chiarimento.

## Verifiche separate

### A. Binding diretto — criterio obbligatorio 34/34

Positivo se e solo se esiste una proiezione con:

```text
clause_role = main_request
speech_act  = open_question
relation    = spatial.located_at
entity      = bound(actor.current)
position    = unknown(spatial.position)
time        = bound(time.current)
```

La copertura e le prove devono superare i rispettivi controlli prima che il projector possa usare il risultato. Il gold 9/25 della fixture resta identico.

### B. Dipendenza — criterio obbligatorio 34/34

La posizione corrente usata come argomento di un'altra richiesta non è una risposta diretta, ma non può essere persa dal piano. L'oracolo distingue:

- `required`;
- `forbidden`;
- `not_applicable` per la risposta diretta;
- `context_resolution_required` per deissi non ancora risolta;
- `clarification_required` per un effetto ambiguo.

Questa separazione evita sia falsi positivi `get_location` sia la perdita del precursore necessario.

### C. Copertura semantica — criterio obbligatorio 34/34

- I 31 casi esatti devono produrre una relazione registrata e argomenti compatibili.
- I 3 casi ambigui devono produrre le alternative tipizzate congelate.
- `unsupported` è vietato quando il registro rappresenta la relazione.
- `ambiguous` senza alternative non conta come copertura.
- Una route non può essere modificata per concordare con un claim semantico.

### D. Sicurezza — criterio obbligatorio 34/34

Restano fail-closed:

- posizione di soggetti esterni;
- richiesta storica contro una capacità solo-current;
- effetto outbound;
- destinazione ambigua di un'azione;
- deissi senza contesto risolto.

### E. Prove — criterio obbligatorio 34/34

Servono prove per ruolo/scope, atto, relazione, stato degli argomenti, referenti derivati e tempo. Si controllano:

- copertura di ogni fatto;
- famiglia di prova consentita per quel fatto;
- riferimenti validi e in-bounds;
- dipendenze compositive esplicite;
- possibilità di span sovrapposti.

Non si confrontano come gold un unico tipo di prova o uno span esatto: nelle lingue con pro-drop, fusione morfologica, wh-in-situ e deissi più rappresentazioni possono essere corrette.

## Limite dichiarato dei 34 controlli

Questa suite verifica una decisione mirata sulla posizione corrente. Non certifica copertura generale di tutte le clausole, né multi-azione o multi-dominio. Quella proprietà deve restare un controllo separato sui 109 principali e sul nuovo holdout storico complesso. Dichiarare i 34 come prova di copertura generale sarebbe un errore di misura.

## Confronto con V26.3, senza leggere output

### Proprietà corrette

- segmentazione Unicode UAX #29;
- una chiamata e zero retry;
- niente route, tool o oggetti di prodotto;
- registro tecnico senza liste di trigger della lingua sorgente;
- distinzione di scope tra richiesta, condizione, descrizione e citazione;
- `grammatical_person` esclusa dal predicato binario del binding.

### Blocchi per l'accettazione massima

1. **Il valutatore usa ancora il vecchio exact a sette campi.** `grammatical_person` e `interpretation` sono inclusi in `SEMANTIC_FIELDS`; il gold resta posizionale e contestabile.
2. **Gli argomenti non sono emessi.** `subject_ref` classifica soltanto il primo argomento; non rappresenta l'argomento ignoto, il suo tipo, un anchor derivato o le alternative.
3. **Il prompt e il gold si contraddicono su `spatial.near`.** La firma dice che il primo argomento è la collezione richiesta, quindi ignota; il gold corrente la tratta come entità esplicita.
4. **Le ambiguità corrette non possono passare.** Il runner considera valutabili solo frame interamente `supported`; un'analisi prudente dei tre casi contestabili viene quindi scartata, mentre una scelta forzata può ricevere credito.
5. **Le prove sono troppo deboli.** Lo stesso union è ammesso per relazione, soggetto e tempo; il validator controlla soltanto gli span espliciti e `has_complete_evidence` accetta qualunque valore diverso da `none`. Non esiste prova del ruolo ignoto o della dipendenza.
6. **La selezione della clausola non è robusta.** `relevant_clause` prende la prima relazione attiva; non allinea il gold alla clausola corretta in una richiesta composta.
7. **Mancano verifiche separate di dipendenza e sicurezza.** Il binding binario può essere corretto pur perdendo un precursore o accettando un effetto ambiguo.
8. **Il pre-gate non è un'autorizzazione indipendente.** Verifica freeze e contaminazione dell'autore, ma non include un hash-lock di review esterna, oracolo tipizzato e valutatore corretto.

### Verdetto operativo su V26.3

V26.3 può essere usato come **diagnostica** del binding diretto sotto il vecchio freeze, riportando separatamente copertura e prove. Non può ottenere credito di accettazione per “34/34 semantic exact” e non soddisfa ancora l'obiettivo massimo.

Prima di un run con valore di accettazione servono:

1. freeze dell'oracolo tipizzato e del nuovo valutatore;
2. argomenti tipizzati o una rappresentazione equipotente che renda espliciti ignoto e dipendenze;
3. ambiguità con alternative;
4. prove per-fatto con compatibilità tra claim e famiglia;
5. allineamento per clausola, non “prima relazione attiva”;
6. lock esterno che leghi freeze, audit, oracolo e valutatore.

Se V26.3 viene eseguito prima di queste modifiche, il risultato resta diagnostico e non autorizza cutover.

## Artefatti e verifiche

- schema: `/tmp/metnos_phase1_typed_oracle_v1.schema.json`  
  SHA-256: `4929fbc4ac524491f452d11d0f2d4d032887dd423c7487233c3d89065f4e17f2`
- overlay: `/tmp/metnos_phase1_typed_oracle_v1.overlay.json`  
  SHA-256: `e62d0605622e5f2c71e329e63448d29ad27fbfd3dafc64b880d4240cda9846af`

Verifiche offline concluse:

- JSON Schema Draft 2020-12: PASS;
- 34 case ID, tutti unici e nello stesso ordine della fixture: PASS;
- tutti i template reference risolti: PASS;
- binding 9/25 invariato rispetto alla fixture: PASS;
- conteggi: 31 exact, 3 typed ambiguity, 3 dependency required, 3 context resolution, 1 clarification: PASS;
- query grezze nell'overlay: 0.
