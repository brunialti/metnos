# Revisione indipendente della chiusura del punto 4

Data: 2026-08-12  
Revisore: AI B, indipendente e non umano  
Modalità: sola lettura degli artefatti canonici; nessuna GPU, nessun servizio
avviato o riavviato, nessun commit e nessuna modifica a banco o produzione

## Verdetto

**PASS.** Non risultano difetti bloccanti o non bloccanti nella chiusura del
punto 4. Il punto 5 può iniziare, restando entro il mandato e i vincoli
riportati nell'ultima sezione.

Il significato del PASS è limitato al campione di 120 richieste, ai 38
controlli, al registro ombra 0.1, alle fonti congelate e alle verifiche
eseguite. Non è una prova di accuratezza universale né una firma esterna.

## 1. Coerenza documentale — PASS

### Checkpoint terminale

La sezione terminale `Punto 4 completato — oracolo canonico e verifica
avversariale` è coerente con gli artefatti verificati:

- dichiara completati i punti 1–4;
- identifica il punto 5 come primo punto incompleto;
- dichiara esplicitamente che il punto 5 non è iniziato;
- riporta 120 casi, 102 accordi, 18 adjudication, radici 84/2/34, 34+4
  controlli, 23 fonti, 107/107 negativi, 6/6 positivi e piano 32/32;
- conserva le decisioni semantiche approvate per composto fail-closed e casi
  38, 84 e 113.

Le frasi precedenti che descrivono il punto 4 come incompleto appartengono ai
checkpoint cronologici anteriori; la sezione terminale successiva le supera in
modo esplicito e non lascia dubbio sullo stato corrente.

### Handover, §24

Il §24 è coerente con checkpoint, oracle, freeze e verifiche:

- descrive il punto 4 come completato e il punto 5 come non iniziato;
- riporta gli stessi conteggi e gli stessi limiti della chiusura;
- distingue la vista corrente degli indici materializzati dal futuro registro
  persistente dei corpus;
- non incorpora hash o lock del freeze corrente.

Non emerge una dipendenza crittografica circolare. Il freeze non include se
stesso; il §24, il verificatore e la suite non contengono il digest del freeze
o il suo lock. La dipendenza di contenuto resta lineare: fonti, compreso
l'handover, poi freeze, poi lock del freeze.

### Referto e README

La ricerca mirata di affermazioni contraddittorie non ha trovato dichiarazioni
operative false:

- il referto etichetta l'esito precedente come `stato storico`, dichiara che è
  superato dall'esito finale e marca come storico anche il primo riesame
  avversariale;
- il referto chiude con il punto 4 completato e il punto 5 come primo
  incompleto;
- il README descrive soltanto lo stato finale e specifica correttamente che
  l'audit delle fonti fotografa la lacuna pre-adjudicazione e non è un oracle;
- il conteggio documentato di 83 manifest correnti esclude correttamente il
  manifest sotto `_retired`; il catalog snapshot congelato contiene 96 voci.

Le occorrenze di `punto 4 incompleto` rimaste nei paragrafi cronologici di
checkpoint e handover non sono presentate come stato operativo corrente; le
sezioni terminali le superano esplicitamente.

## 2. Identità dell'oracolo e freeze — PASS

- oracle file SHA-256:
  `3e0d1155fad7b2920b3b04bb6e617747849c8fe4a1abfea5920f1b51dba6a123`;
- oracle payload SHA-256 dichiarato e ricalcolato:
  `2cdec2776fa48594db8729ec1aa17a635956a61f64b2ce7ad00aa32f8cc0427f`;
- freeze file SHA-256 finale:
  `550e5c469c56577153c5072616714a555d54a5e129a9e0933f1c744c55afae31`;
- freeze lock dichiarato e ricalcolato:
  `02ac5ad33d503c7f91bd2490db80e73cb39eb4b1f0e1deef9cee1cf55f74c5c3`;
- fonti congelate: 23/23 presenti e con impronta coerente;
- hash dell'handover nel freeze e ricalcolato:
  `84c5be7efe982dbc1ce244aa87b0c0b7f3a4237eb334592b9f143517c4b17847`.

File, payload e quindi semantica dell'oracolo coincidono con quelli attestati
prima della patch documentale. È cambiato soltanto il freeze previsto dal
risigillo delle fonti documentali; il nuovo freeze è internamente coerente.

## 3. Verifiche deterministiche — PASS

- verificatore oracle: `error_count=0`;
- conteggi oracle: 120 casi, 102 accordi, 18 adjudication, 34 controlli
  esistenti, 4 nuovi, totale 38, 23 fonti;
- suite ufficiale: 107/107 mutazioni negative respinte e 6/6 riordini positivi
  accettati, `error_count=0`;
- identità dell'oracolo durante la suite: `unchanged=true`;
- verificatore del registro: `error_count=0`, 7/7 mutazioni interne respinte;
- audit delle fonti: `error_count=0`; il suo risultato 117+4 resta la
  fotografia congelata della lacuna precedente all'adjudicazione, non un
  giudizio sul completamento dell'oracolo finale;
- verifica della colonna semantica sigillata: codice 0, 120 richieste, 10
  sentinelle, 30 coppie e 6/6 impronte coerenti.

Le quattro classi di robustezza restano chiuse:

- D-01: chiavi JSON duplicate respinte;
- D-02: `NaN`, infinito e overflow non finiti respinti;
- D-03: tipi JSON esatti e oggetti chiusi ricorsivamente;
- D-04: le sei liste di autorità e la baseline sono insiemi esatti, unici,
  con cardinalità obbligatoria e ordine non semantico.

## 4. Piano avversariale — PASS, ancora applicabile 32/32

La patch ha modificato documenti e relativo sigillo, non oracle, payload,
registro, verificatore o suite. Sono stati rieseguiti il verificatore base, la
suite ufficiale completa, il verificatore del registro, l'audit e il controllo
degli output sigillati. Le parti del piano toccate dal risigillo — identità,
freeze, provenienza, fonti, documenti, banco e deriva esterna — sono state
ricontrollate.

Restano pertanto applicabili e verdi tutti i gruppi ATK-01–ATK-32 già
attestati: binding 120/120, testo e hash, schema e grafo, route/reason/control/
barrier, ordine e `data_from`, 102+18, 34+4, collisioni, provenienza double-AI
non umana, catalog snapshot dichiarato arretrato, fail-closed, mutazioni e
integrità esterna.

## 5. Integrità del banco e dell'ambiente — PASS nei limiti osservabili

- banco checkpoint SHA-256:
  `b39f19ce3418ff0e6f2908462ddd5a47c5e1bd410211ce152d6ce2619b3c82cb`;
- campione semantico SHA-256:
  `36aba12b9ec0f366569498bba0fa4dd876f4d2354f736058791182ca41e37af4`;
- registro file SHA-256:
  `4808bfe8b971ee8839c715f7aac91c6473d865724540890e4ec43dba136243d8`;
- registro freeze SHA-256:
  `509e819508d77cd6595f897233b0d5eaef274b5979c5925ab8955a8f8190bd79`;
- output c10/c11, output con riparo e mappatura sigillata superano il
  verificatore dedicato; non sono usati come gold semantico;
- il servizio HTTP è rimasto attivo con lo stesso processo, avviato alle
  03:58:02 CEST; nessun riavvio attribuibile alla revisione;
- nessun processo di misura del laboratorio è attivo e nessun nuovo processo
  GPU è stato creato; il server del modello osservato era preesistente dal 7
  agosto e non è stato chiamato;
- HEAD resta
  `856fe341228eb1fd1868b9c5b7630f55fe7d29b1`, ultimo commit del 7 agosto;
  nessun commit è stato creato;
- il worktree era già sporco, anche in alcuni file di produzione. Le impronte
  pre/post dei file di produzione già modificati restano uguali durante questa
  verifica; nessuna loro variazione è attribuibile al controllo.

## 6. Difetti e limiti

Nessun difetto bloccante o non bloccante rilevato.

Limiti: la verifica di processi, servizi e worktree è osservazionale; non prova
eventi anteriori ai timestamp disponibili. Il freeze protegge dalla deriva
accidentale delle fonti ma non da un autore autorizzato che modifichi e
risigilli coerentemente l'intero insieme.

## 7. Prossimo punto autorizzato

Il primo punto incompleto è il **punto 5**:

> Implementare nel solo laboratorio la fetta minima:
> `unrepresentable`, `system_control=undo_last_turn` e archi di controllo per
> approvazione. Prima prove deterministiche, poi una sola misura GPU con
> controllo fresco affiancato.

Il punto 5 può iniziare, ma il mandato non autorizza modifiche di produzione,
hardcoding sui casi del banco, riuso degli output c10/c11 come gold, più di una
misura GPU o una misura prima delle prove deterministiche. Banco, campione,
registro e oracle restano congelati salvo una nuova autorizzazione esplicita.
