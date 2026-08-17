# Holdout blueprint-first v0.4

Stato: **PREFLIGHT ONLY — ZERO QUERY, ZERO GOLD DI CASO, ZERO RUN**  
Data: 2026-08-13

## 1. Perché esiste v0.4

Il preflight v0.2 è stato respinto da due audit indipendenti nonostante i test verdi. I blocker erano binding incompleti fra matrice e proposal, relazioni non canoniche, lifecycle non legato ai byte, glossari manuali, divergenza safety e mutazioni nominali. v0.1 e v0.2 restano storici e non vengono corretti retroattivamente.

## 2. Autorità e precedenza

L'autorità semantica è, in ordine:

1. registro intent-shadow congelato;
2. schema canonico completo intent-shadow 0.1 (`body`, nodi tipizzati, `data_from`);
3. proiezione normativa v0.4 delle sole regole di binding e safety già approvate;
4. contratto di design come provenienza.

Il vecchio adjudication contract è provenienza della proiezione normativa, non autorità integrale: le sue clausole su arbitrato e confidence sono esplicitamente sostituite. Qui non esistono arbitrato o confidence non alta accettabile.

Il challenger resta soltanto un commitment cronologico separato e non è leggibile da autori o reviewer.

Ogni artefatto di caso futuro lega sia il package autoritativo sia il freeze completo del processo.

## 3. Modello semplice

Un proposal pre-query contiene una lista ordinata di obblighi, senza ID interni:

- `positive`: capacità registrata indispensabile;
- `negated`: capacità registrata esplicitamente negata;
- `outside`: capacità compresa ma fuori registro, con eventuale capacità registrata allettante da non approssimare;
- `control`: undo dell'immediato turno precedente.

Le relazioni usano indici della lista e forme chiuse: indipendenza, consumo con porte esplicite, ordine esplicito, ownership di approvazione e rami condizionali. L'ordine della lista è l'ordine di menzione richiesto nella futura query. Per operazioni indipendenti è solo canonicalizzazione di superficie, non dipendenza.

Non esistono scenario taxonomy o role taxonomy arbitrarie nell'exact match. La novità di scenario è controllata separatamente dal reviewer cross-language.

## 4. Expected derivato

Il validator deriva deterministicamente l'unico expected canonico completo dal proposal:

- route positive soltanto;
- ordine esatto;
- nessuna edge non proposta;
- tutte e sole le edge `consumes`; le porte nel proposal provano la compatibilità, mentre il gold usa la forma canonica minima `{"from": ordinal}` ammessa dallo schema;
- barrier soltanto per i membri `approval_owns`, in un gruppo contiguo;
- nessun ramo rejected materializzato;
- root e reason esatte per outside, conditional branch non rappresentabile e mixed root;
- system control esatto per undo-only.

Il gold è accettato solo se byte-canonicalmente uguale all'expected derivato e se i safety flag coincidono con quelli derivati. La validità schema è necessaria ma non sufficiente.

## 5. Pool distinti

- Pool A: due reviewer identificati e con context ID distinti vedono proposal e autorità prima della query. Derivano expected e safety. Solo accordo esatto e confidence alta crea il gold immutabile.
- Un autore nuovo produce una sola query da una projection meccanica route-free.
- Pool B: due reviewer diversi da A vedono query, lingua, glossario/schema generali e proposal hash opaco. Ricostruiscono obblighi ordinati, relazioni, expected e safety. Solo accordo esatto B1=B2=proposal=gold passa.

I giudizi contengono reviewer ID e context ID. Reviewer ID o context ID non possono attraversare i pool. Query vuote e record non canonici falliscono prima del confronto.

## 6. Projection e i18n

La projection contiene soltanto tuple strutturate `kind`, `author_gloss`, relazioni per indice e constraint di superficie. Per un false-action trap aggiunge il solo `tempting_but_insufficient_gloss`. I gloss delle operazioni sono concatenazioni meccaniche degli scope strutturati revisionati, senza override manuali; una route con scope mancante o sintassi esecutiva è `authorable=false`. I gloss outside/control/barrier sono invece regole normative esplicite dentro `authority.json`, non sono presentati come scope del registro. Non contiene route, expected, reason, path, codice, pattern, esempi o dati di caso. Usa lo stesso percorso neutro per ogni BCP47; l'autore formula direttamente nella lingua assegnata.

Projection e author bundle vengono ricostruiti dal validator e confrontati byte per byte. Un hash fornito dal chiamante non basta.

## 7. Fingerprint

Il fingerprint canonicalizza valori semantici e relazioni, non indici arbitrari, ordine dei dict, lingua, nomi o numeri. Le relazioni sono espresse tramite le firme degli obblighi. Le collisioni sono vietate nel pilot. Nello scale-up sono ammesse soltanto fra casi S4 undo-only, dichiarati separatamente.

## 8. Lifecycle

- 20 contesti autore globalmente unici, una query ciascuno;
- due context/reviewer Pool A e due Pool B, tutti distinti fra loro e dagli autori;
- una revisione nativa per ogni caso e un reviewer cross-language sull'intero pannello, entrambi distinti dagli altri ruoli;
- ruoli chiusi; hash di input e output ricalcolati dai byte reali; review native/cross in JSON chiuso con verdict pass e zero finding; nessun accesso vietato, rete/GPU zero, nessun edit post-sigillo;
- lifecycle completo legato all'envelope;
- qualsiasi failure chiude la versione; niente semplice risampling.

## 9. Pilot

La matrice resta 20 casi, due per ciascuno dei dieci tag: copre le undici celle, entrambi i G4 e tutti i fenomeni safety. Ogni query usa un contesto nuovo. Il pilot valida il processo, non il modello.

## 10. Gate

Prima di creare query devono essere verdi:

- package/freeze/closure/environment;
- schema direttamente eseguibile;
- authority e glossario;
- tutte le celle e sottovarianti;
- derivazione exact proposal→expected→safety;
- projection route-free e bundle exact;
- fingerprint e collision policy pilot/scale;
- Pool A/B, lifecycle e binding dai byte;
- catalogo mutation data-driven, con ogni voce realmente esercitata;
- exact allowlist del namespace: nessun query/gold/run/cache.

Servono due audit indipendenti READY sul byte finale. Solo allora parte automaticamente il pilot. Un errore chiude v0.4 e richiede una nuova versione; non si rattoppa il campione.
