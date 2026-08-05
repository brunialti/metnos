---
id: 0197
title: Tutor semantico con recupero firmato e composizione locale fondata
date: 2026-07-23
status: accepted
area: runtime | tutor | i18n
related: [0092, 0134, 0189, 0195, 0196]
---

# 0197 - Tutor semantico con composizione locale fondata

## Contesto

Le domande su come usare Metnos sono linguisticamente troppo varie per un
selettore a frasi. Le prime schede Tutor contenevano `affinity` ed esempi
`exact`: funzionavano sui casi previsti, ma costituivano un secondo lessico di
routing, difficile da mantenere e non generalizzabile. Anche la resa letterale
di una scheda era insufficiente per comporre risposte naturali a domande
diverse, pur avendo recuperato la fonte corretta.

## Decisione

L'ammissione applica soltanto controlli strutturali deterministici: forma di un
segreto, comando di controllo e verbo operativo canonico iniziale. Non contiene
forme linguistiche di aiuto. Un classificatore locale chiuso distingue
`EXPLAIN`, `ACT`, `MIXED` e `UNKNOWN`; soltanto `EXPLAIN` prosegue. La scelta
della fonte usa embedding BGE-M3 locali:

- ogni scheda pubblicata dichiara una descrizione `[semantic]` per lingua;
- il compilatore genera `card_vectors`, normalizza e valida shape, finitezza e
  norma, registra impronta del modello e hash del testo;
- vettori e schede vivono nello stesso catalogo SQLite firmato, letto in sola
  lettura e sostituito atomicamente con last-known-good;
- le schede non contengono più `affinity`, liste `exact` o sinonimi usati dal
  runtime per scegliere una risposta;
- soglia e margine sono una politica numerica unica e configurabile, non una
  tabella linguistica.

Dopo il recupero, le guide informative sono formulate dal tier locale `fast`
usando soltanto domanda e contesto recuperato. Il prompt è localizzato e
impone risposta fondata, nessuna azione, nessun completamento da conoscenza
esterna e un esito interno esplicito se il contesto non basta. Il filtro di
audience avviene prima di costruire quel contesto: una fonte amministrativa non
è mai passata al modello per un utente non amministratore.

Le procedure amministrative e i messaggi di sicurezza restano deterministici.
Il compositore non riceve handler o strumenti e attraversa
`executor_scheduler` con `resource_class="llm"`, classe `0`: usa quindi la
stessa retropressione centrale degli altri consumatori LLM senza aprire un pool
proprio. Sul canale HTTP il lavoro bloccante è spostato fuori dal ciclo eventi.

Una domanda generale sull'assistente può selezionare la scheda marcata
`kind="capability_overview"`. Il tipo è metadato di contenuto, non una frase di
routing. Una domanda esplicativa senza fonte produce una lacuna; `ACT` e
`UNKNOWN` ricadono nel motore prima di aprire il catalogo, quindi un guasto F2
non sottrae una richiesta operativa.

## Conseguenze

- Nuove formulazioni possono raggiungere la stessa guida senza aggiornare
  liste di sinonimi nel codice o nelle schede.
- Sostituire il modello locale invalida e ricompila l'indice per costruzione.
- Un errore di embedding, catalogo o composizione produce un esito onesto; non
  autorizza una risposta inventata e non consuma dialoghi pendenti.
- L'ID Tutor già persistito viene conservato dall'endpoint asincrono, così il
  riferimento mostrato in chat è ispezionabile senza duplicare il turno.
- I turni che iniziano con un verbo operativo canonico bypassano il Tutor; gli
  altri attraversano il classificatore chiuso, ma `ACT` e `UNKNOWN` non
  eseguono retrieval né composizione.

## Evidenza iniziale

- sei schede IT/EN senza `affinity` o `exact`, dodici vettori da 1024 elementi
  nel catalogo firmato schema 2;
- «Cosa fanno executor github» seleziona GitHub con coseno 0,849 e margine
  0,194; cinque domande canoniche sopra soglia;
- «Cosa fanno Mario e Lucia domani?» non viene sottratta al planner;
- 38 test Tutor e 214 test di lessico, prompt e i18n verdi.

## Addendum — ingresso interamente semantico

La compatibilità transitoria basata su concetti `help.*` è stata rimossa il 23
luglio 2026. Formulazioni ellittiche e domande di seguito non aggiungono nuove
frasi al lessico: l'ultimo scambio Tutor resta per 15 minuti in memoria,
isolato per principal e conversazione, e viene ammesso nella composizione
soltanto quando aumenta materialmente il punteggio di retrieval. Il compositore
restituisce un esito tipizzato: `answer`, `insufficient` o `unavailable`.
Per le domande d'uso e configurazione, la composizione presenta per prima una
richiesta naturale di esempio da adattare, nella lingua corrente, e soltanto dopo il contratto
tecnico. La decisione appartiene allo stesso giudizio semantico del compositore:
non introduce regex, sinonimi o casi speciali per argomento.
