# Referto RUN2 candidate v0.3 — 13 agosto 2026

## Esito semplice

RUN2 è completo, integro e valutato. Ha corretto gran parte degli errori di
forma del RUN1, ma non ha superato il controllo semantico:

- canonico: controllo A **79/120**, candidate B **45/120**, delta **−34**;
- tipizzati: A **1/4**, B **1/4**;
- legacy separato: A **25/34**, B **27/34**;
- verdetto: `candidate_fail`.

Il batch contiene 316/316 record e POST accettati, senza retry. Il controllo
pre-gold ricostruisce 316/316 raw, con zero differenze inattese. Freeze
protocollo: `4c77fed4a9a8f481d412f2ff1fd9bcc274892ed8e553015bc2e8821eddccea31`;
batch: `59fd26bbd71fada2aa116b1deced629993900d6ba02b69c1b4752602658e2d1c`;
valutazione: `7e3c384ccde6669fd2c20ff163399893082bfd1913852e3dc4f917eb3bbc3e04`.

## Cosa è migliorato

Nel totale delle 158 risposte B:

- 95 sono grafi validi;
- 33 sono astensioni valide;
- 30 sono documenti IR invalidi;
- gli errori tecnici sono zero.

Rispetto al RUN1, l'esattezza canonica sale da 16 a 45, l'esattezza della
radice da 17 a 66, gli invalidi canonici scendono da 102 a 28 e gli invalidi
totali da 123 a 30. Le radici non rappresentabili passano da 0 a 33. La
riscrittura bilanciata del prompt ha quindi rimosso il priming dominante del
grafo operativo e quasi tutto il difetto `from:[0]` sul primo passo.

## Errore di forma residuo

I 30 invalidi hanno un solo codice: `FROM_SELF_OR_FORWARD`, 38 occorrenze.
Ventinove sono sul primo passo. Non esistono veri riferimenti in avanti: il
modello usa ancora talvolta l'indice del passo corrente.

Schema, validator e compiler sono allineati. Lo schema JSON può imporre interi
unici non negativi, ma non può esprimere che un indice debba essere precedente
e visibile. Validator e compiler respingono correttamente il documento. Non è
stato applicato repair.

Una prova diagnostica, non una correzione, mostra che rimuovere soltanto il
`from` del primo passo renderebbe validi 24 dei 28 invalidi canonici, ma solo 8
diventerebbero semanticamente esatti. Il tetto osservato del solo recupero di
forma sarebbe quindi 53/120, non 73/120.

## Errore semantico residuo

I 47 documenti canonici validi ma non esatti si dividono così:

- 13 astensioni errate;
- 13 azioni errate dove l'intera richiesta era fuori registro;
- 8 radici di astensione corrette ma ragione errata;
- 7 grafi con operazioni non indispensabili;
- 6 grafi con route errate.

Su 34 richieste attese come non rappresentabili, B produce 13 grafi, 11
documenti invalidi e 10 astensioni; soltanto 2 hanno anche la ragione esatta.
Nessun `system_control` viene emesso: i due casi undo diventano astensioni.

La metrica di sicurezza `false_action_avoided` scende da 119 a 94. Il nuovo
prompt ha quindi migliorato la forma, ma ha reso visibile un confine semantico
fra azione, astensione e copertura dell'intera richiesta.

## Revisione avversariale

1. Il miglioramento è reale ma non dimostra universalità: le 120 richieste
   restano un campione aperto di regressione.
2. Correggere in automatico gli indici `from` aumenterebbe la validità senza
   risolvere la maggior parte degli errori semantici; non è autorizzato.
3. Correggere singole route usando query, ID o hash del banco sarebbe
   hardcoding; non è autorizzato.
4. Una nuova regola semantica cambierebbe due variabili insieme e non può essere
   attribuita allo stile del prompt.
5. Il controllo A resta necessario: lo stesso A ha già mostrato variazione fra
   misure integre.

## Passo successivo autorizzato

RUN3 è un esperimento **solo di stile** a tre bracci sul prompt v0.3:

- S0: CURRENT v0.3 byte per byte;
- S1: forma breve prescrittiva ADR 0027;
- S2: le stesse regole come procedura numerata compatta.

Ogni braccio conserva le stesse regole, i tre template di radice, i dati del
registry, lo schema, il core IR, l'adapter, il modello e i limiti. Non viene
introdotta la proposta coverage-before-root: quel prototipo è archiviato come
non selezionato, non congelato e non eseguito.

RUN3 deve usare 158 query per 3 bracci, 474 richieste seriali, ordine latino
ABC/BCA/CAB, controllo interno S0 e confronto separato fra i tre stili. S0 deve
coincidere esattamente con RUN2 su raw, estrazione e metriche aggregate per i
158 casi; anche un solo drift rende il verdetto non attribuibile allo stile.
Prima della GPU servono suite completa, preflight disarmato e due audit
indipendenti.
