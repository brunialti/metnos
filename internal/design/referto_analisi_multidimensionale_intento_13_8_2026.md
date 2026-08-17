# Referto dell'analisi multidimensionale dell'intento

Data: 2026-08-13  
Stato: ciclo di diagnosi e prototipo offline chiuso; punto 5 ancora incompleto.

## Esito in parole semplici

La sola misura live autorizzata è stata consumata una volta ed è integra:
**316/316** risposte. Il candidato misurato, v0.1, non ha superato il controllo.
L'analisi ha mostrato che non bastava correggere il prompt: il modello doveva
produrre troppi dettagli tecnici derivabili, mentre schema e validatore non
esprimevano lo stesso confine.

È stato quindi costruito un nuovo candidato v0.2 con un'IR minima. Tutte le
prove tecniche, strutturali e di sicurezza offline passano, ma v0.2 non è mai
stato misurato live. Non è perciò possibile dichiararne l'accuratezza
semantica, né tantomeno l'universalità.

## Misura live già consumata

Il batch live storico contiene 158 query affiancate su due bracci, quindi 316
record. Raw, journal, batch, sigillo e replay sono integri. Non è stata
eseguita una seconda misura e questa chiusura non ne autorizza una.

L'evaluator v0.3 e l'audit indipendente danno i conteggi corretti:

| Pannello o misura | Braccio A | Braccio B |
|---|---:|---:|
| canonico, 120 casi | **75/120** | **25/120** |
| confronto raw storico v0.2 | 29/120 | 9/120 |
| controlli tipizzati | 1/4 | **0/4** |
| barriere, casi applicabili | **0/3** | **0/3** |
| controlli di sistema, casi applicabili | **2/2** | **2/2** |
| legacy Phase-1, separato | **25/34** | **29/34** |

Il delta canonico B−A è **−50** e il verdetto resta `candidate_fail`. Il
pannello legacy resta separato: non viene convertito o usato per compensare il
pannello canonico.

## Errore della metrica corretto

Il confronto precedente trattava `data_from: []`, materializzato dall'adapter,
come diverso dall'omissione equivalente nell'atteso. Erano **62 falsi
negativi** di rappresentazione: 46 per A e 16 per B. La proiezione canonica li
corregge simmetricamente e l'audit caso per caso trova **zero falsi positivi**.
Radice, route, ordine, archi effettivi, barriere, esiti non vuoti e motivi
restano controllati.

Questa correzione rende giusta la misura; non migliora il candidato. Anche con
la metrica corretta B resta a 25/120 contro 75/120 di A.

## Diagnosi da più punti di vista

### Formato e contratto

Nel braccio B ci sono **57 documenti non validi** e 3 errori tecnici. Il
vecchio schema lasciava esprimere porte e archi che il validatore rifiutava in
seguito. In altre parole, il modello poteva produrre forme formalmente
ammesse dal primo cancello ma respinte dal secondo.

Il conteggio operativo iniziale registrava **139** archi problematici con la
prima definizione aggregata. L'audit completo, calcolato direttamente sul batch
senza elenchi di casi hardcoded, ne trova **156** in 66 record B: 58 problemi
di dominanza e 98 porte di uscita arbitrarie. I numeri 139 e 156 non si
contraddicono: usano due perimetri aggregati diversi; 156 è il censimento
data-driven completo adottato dalla regressione finale.

### Semantica

La sola validità JSON non risolve la scelta dell'intento. Sui 84 casi con
route attese, A sceglie la route esatta in 51 casi e B in 17. Le astensioni
sbagliate sono 19 per A e 26 per B; le astensioni corrette sono 27 per A e 7
per B. Restano quindi confini semantici fra route e rinuncia che una futura
misura dovrà verificare davvero.

### Controllo e sicurezza

Entrambi i bracci falliscono i tre casi di barriera applicabili, 0/3, mentre
entrambi riconoscono i due controlli di sistema, 2/2. I valori globali che
includevano casi non applicabili nascondevano questo confine; il denominatore
applicabile lo rende esplicito.

### Complessità e prestazioni

Chiedere al modello percorsi, ordinali, porte, esiti e continuazioni aumentava
la superficie di errore senza aggiungere una scelta semantica. Sono dati
derivabili dal registro e dalla posizione nel grafo, quindi appartengono a un
compilatore deterministico.

## Criterio di stallo e cambio di analisi applicato

Il criterio adottato è questo: quando cicli successivi lasciano ferme o
peggiorano le metriche centrali e gli errori si concentrano nella
rappresentazione, si interrompono le correzioni locali. Si riesamina il
problema lungo dimensioni indipendenti: formato, semantica, controllo,
sicurezza, prestazioni e generalità.

Il criterio è già stato applicato. Il risultato negativo B=25/120, i 57
documenti invalidi e la concentrazione sugli archi hanno fermato il lavoro sul
formato v0.1. Non sono state aggiunte eccezioni per singole query, hash o
indici del banco; si è cambiata l'architettura model-facing.

## Decisione architetturale: IR minima v0.2

Il modello può emettere soltanto una delle tre radici:

- `operation_graph`, con passi compatti `route` e facoltativo `from`;
- `system_control`, scelto da autorità congelate;
- `unrepresentable`, con motivo tipizzato.

Una barriera contiene soltanto tipo e corpo. Il compilatore assegna ordinali
e percorsi, deriva esclusivamente archi `result` → `primary` quando le porte
del registro sono univoche, espande `approved`, materializza `rejected` vuoto
e costruisce continuazioni legate agli hash. Self, forward, cicli, fughe di
ramo, porte ambigue e chiavi extra falliscono chiusi. Il client strutturato del
laboratorio usa soltanto un trasporto finto interno e sigillato; non effettua
chiamate reali.

## Evidenza offline del candidato v0.2

- suite ufficiale: **30/30 PASS**;
- round-trip strutturale: **124/124**, inclusi 3 casi con barriera, 2 undo e 34
  rinunce nel pannello originario;
- audit indipendente: **36/36 PASS**, zero difetti aperti nel perimetro;
- 500 compilazioni valide: circa **4,007 ms** medi sul piccolo grafo di prova;
- freeze auditato:
  `7b68f9f62965ea24cf830ecc8c717fcdf6d6116ce3fa412082ed1f2287b4e992`.

Il risultato è **zero errori tecnici, strutturali e di sicurezza offline** nel
perimetro finito. Non è un risultato di accuratezza semantica live: il nuovo
candidato non ha ancora elaborato il campione tramite GPU o modello reale.

## Significato attuale dei 120 casi

I 120 casi sono ora un **campione aperto di regressione**: può essere ampliato,
versionato e affiancato da nuovi controlli. Non è una prova universale e non
deve diventare un elenco sul quale ottimizzare query per query. L'obiettivo
"universale" resta una direzione di progetto da verificare su campioni nuovi,
non una proprietà dimostrata da questo ciclo.

## Revisione avversariale finale

- Zero errori offline non significa zero errori semantici live.
- Il round-trip usa l'oracolo soltanto nell'adapter di test e non dimostra che
  un modello sappia produrre l'IR corretta.
- Riutilizzare sempre gli stessi 120 casi può creare sovra-adattamento; per
  questo il campione resta aperto e servono casi futuri non usati nel design.
- Il 139 è il conteggio operativo iniziale; il 156 è il censimento aggregato
  completo. Non vanno presentati come due misure equivalenti.
- I circa 4 ms misurano il compilatore locale, non latenza o costo del modello.
- L'audit del freeze `7b68f...` precede il solo aggiornamento documentale del
  README; il risigillo documentale successivo non sostituisce quell'audit.

Non emerge una nuova policy semantica da decidere dentro il prototipo e non è
stata implementata una barriera multi-ramo. I confini ancora non provati sono
esplicitamente mantenuti chiusi.

## Integrità dopo la chiusura documentale

Il README appartiene al freeze del candidato; il solo aggiornamento del suo
stato di audit ha quindi richiesto un risigillo documentale. Il freeze auditato
`7b68f...` resta l'identità dell'implementazione sottoposta al revisore; il
freeze corrente, che differisce per il README, è
`f00d7ed185cdceab16eaf753b2990c93797ecb9aac66cd401302edefd4b38d5f`,
con payload
`c4070b3ae2486c001af55519ace18ea48988174beac2a5c038a75e302eff5330`.
Il controllo degli artefatti e le 30 prove ufficiali passano dopo il risigillo.

Poiché l'handover è una fonte dell'oracolo sigillato, il lock è stato
riallineato senza cambiare oracolo o attesi. SHA-256 dell'handover:
`6ab25778637a57605fc1ba757b9b05d7cf036e11b1b3ce45fe49a896e59939b6`;
SHA-256 del freeze oracolo:
`9d1cdec28fa7fad2685a0a76a4d4ef62c73d8624e114716b3d43c3a372bc6a54`;
payload lock:
`09cb00bd71926baae677a246ab3b9273853f900e8fd42a2903d3b40f5ca2e230`.
La verifica canonica resta 23/23 fonti, 107/107 mutazioni negative respinte e
6/6 controlli positivi accettati.

## Stato terminale e scelta pendente

Il ciclo multidimensionale/offline è terminato. Il primo punto non completato
dell'ordine autorizzato resta il **punto 5**, perché la nuova IR v0.2 non ha
ancora una misura semantica live affiancata.

Serve una nuova decisione di Roberto: **autorizzare oppure non autorizzare una
nuova misura GPU** per v0.2. Le opzioni devono essere presentate da root in
forma semplice. Questo referto registra la scelta pendente, non la decide e
non autorizza alcuna esecuzione.
