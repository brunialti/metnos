# Referto multidimensionale RUN1 — candidate v0.2

Data: 2026-08-13  
Stato: misura completa e integra; candidato respinto; ciclo adattivo RUN2 preparato soltanto offline.

## Esito semplice

Il RUN1 ha completato tutte le **316/316** richieste affiancate, senza retry e
con una sola risposta primaria per richiesta. Sul pannello canonico il
controllo A ottiene **79/120**, mentre candidate v0.2 ottiene **16/120**:
delta B−A **−63**, verdetto `candidate_fail`.

Questo risultato non invalida l'IR minima o il compilatore. Mostra che il
modello ha quasi sempre scelto una forma IR che lo schema JSON ammetteva ma
che la regola di dipendenza rendeva impossibile. La causa dominante è nella
proiezione del prompt, non in una query o in una lingua particolare.

## Integrità della misura

- stato batch e sigillo: `complete`;
- record e POST accettati: **316/316**;
- stop/timeout/retry: nessuno;
- replay pre-gold: **316/316**, zero differenze inattese;
- batch SHA-256:
  `e058cd6c42c410089b1e31d6958d47d4ebac925ed09dd21a1f74ce3145678b00`;
- freeze protocollo RUN1:
  `e17246da0a7472a3d246c7dcd75823e14cd2fb25a0ae1a4c2ec8d176f6fef917`;
- evaluator v0.3: metrica simmetrica, gold aperto soltanto dopo gate completo.

Raw, journal, checkpoint, consumption marker, batch e seal restano immutati.
Non è stata eseguita alcuna riparazione dell'output del modello.

## Risultati separati

| Pannello | A | B |
|---|---:|---:|
| canonico | **79/120** | **16/120** |
| controlli tipizzati | **1/4** | **0/4** |
| legacy Phase-1, separato | **25/34** | **28/34** |

I denominatori restano separati e il legacy non compensa il canonico. Nel
totale delle 158 risposte B, la classificazione tecnica è:

- **123** `document_invalid`;
- **1** `technical_invalid`;
- **34** `valid_representable`.

## Diagnosi multidimensionale

### Forma e contratto

Il modello ha emesso `operation_graph` in **157/158** casi. Tutti i 123
documenti invalidi contengono `FROM_SELF_OR_FORWARD`; le occorrenze sono 221,
tutte self-reference e nessuna vera forward reference. In tutti i 123 casi il
primo passo contiene `from:[0]`; nei 34 documenti validi il primo passo omette
`from`.

Lo structured output ha rispettato il JSON Schema in 157 casi. Il punto è che
JSON Schema può imporre un intero non negativo, ma non può esprimere da solo
che l'indice debba identificare un'operazione strettamente precedente e
visibile. Il validator e il compiler hanno quindi correttamente fallito chiusi.

### Errore tecnico

L'unico `technical_invalid` è una risposta troncata al limite di 4000 token:
per una richiesta di controllo di sistema il modello ha iniziato un
`operation_graph` ed enumerato molte operazioni. `finish_reason=length`; JSON
incompleto. È stata effettuata una sola chiamata, senza retry o repair.

### Prompt e priming

Il prompt v0.2 mostrava prima e in forma completa soltanto la radice
`operation_graph`. L'esempio isolato del consumatore usava `from:[0]`, ma non
diceva in modo inequivoco che il passo iniziale deve omettere `from`.
`system_control` e `unrepresentable` comparivano soltanto come cataloghi in
fondo. La distribuzione osservata — 157 radici operative e self-reference sul
primo passo in 123 casi — è coerente con questo priming sbilanciato.

Il difetto attraversa più lingue; non è spiegato da un branch `it/en`. Il
percorso resta unico e locale-neutral. Il prerequisito separato v0.2.1 chiude
la copertura dei tag BCP47 grandfathered, senza cambiare alcun byte delle 158
richieste del workload.

### Drift del controllo

Il controllo fresco RUN1 misura **79/120**, mentre la misura live precedente
aveva dato **75/120** con la stessa metrica canonica. Il +4 netto è drift
dell'esito tra due misure, non prova di deriva dei file: sorgenti, database,
backend e profilo del RUN1 hanno superato i propri hash gate. Per questo RUN2
riusa esattamente snapshot e pannello RUN1 e continua ad avere un controllo A
affiancato; non attribuisce automaticamente a B ogni differenza fra run.

## Unica correzione autorizzata per RUN2

La correzione sperimentale è soltanto la proiezione del prompt:

- tre template equivalenti per `operation_graph`, `system_control` e
  `unrepresentable`, prima del catalogo;
- scelta della radice prima dei dettagli;
- grafo completo con sorgente senza `from` e consumatore con `from:[0]`;
- regola esplicita: ordinal 0 omette `from`; ogni dipendenza è reale,
  strettamente precedente e visibile;
- nessun esempio JSON negativo con self-reference: `from:[0]` compare una sola
  volta, sul secondo passo dell'esempio positivo completo;
- divieto di enumerare registro o alternative; mini-check dopo il catalogo.

Schema, registry, validator, compiler, adapter semantico, critic OFF, modello,
limiti ed evaluator non cambiano. Non viene aggiunto un repair raw. La patch è
registry-derived, query-free e usa lo stesso percorso per ogni tag BCP47
valido.

## Revisione avversariale

- I 34 documenti validi non dimostrano correttezza semantica generale.
- Togliere meccanicamente i self-edge non è autorizzato: un controfattuale
  migliora alcuni casi ma peggiora il pannello legacy e può cancellare una
  dipendenza voluta.
- La forte associazione con il prompt è una diagnosi falsificabile, non una
  garanzia di convergenza; RUN2 deve misurarla contro A.
- Il controllo può cambiare esito anche con ambiente hash-bound; il confronto
  valido resta quello affiancato nello stesso run.
- Il pannello è regressione aperta, non prova universale e non fonte di esempi
  da copiare nel prompt.

Non emerge una nuova policy semantica né una barriera multi-ramo. RUN2 resta
spento e disarmato fino a suite, preflight e audit indipendente verdi.
