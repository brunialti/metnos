# Referto — confronto fra estrazione di intento attuale e V23lite

Data della misura: **11 agosto 2026**.

## Verdetto

Il criterio di sostituzione **non è raggiunto**. Sulle stesse 120 frasi reali il
nuovo percorso produce molte rotte semanticamente migliori, ma introduce più
regressioni di quante ne risolva.

I tre numeri richiesti, sul totale di 120, sono:

| esito | casi |
|---|---:|
| **accordo** | **65** |
| **nuovo migliore** | **9** |
| **nuovo peggiore** | **46** |

I numeri sommano a 120. La regola di adjudicazione è stretta: “nuovo migliore”
significa che la rotta nuova è interamente corretta rispetto a `runtime/vocab.py`
e ai confini del §2.2. Se entrambi sbagliano, oppure il nuovo è soltanto “meno
sbagliato”, il caso è contato in **nuovo peggiore**. Gli output respinti dal
validatore non ricevono credito operativo.

## Misura eseguita

Lo strumento è
`internal/tools/request_analysis_lab/misure_11_8/confronto_intento.py`; risultato
grezzo e log sono, nella stessa directory, `confronto_intento.json` e
`confronto_intento.log`.

- Campione: le 120 frasi di `prova_cieca.sample()`, seme `20260811`, impronta
  della lista JSON `36aba12b9ec0f366569498bba0fa4dd876f4d2354f736058791182ca41e37af4`.
- Ordine dei bracci: prima `extract_intent(query, llm_call)` corrente, poi
  prompt V23lite di riferimento + `riparo_ruolo.py`, sulle stesse frasi e nello
  stesso ordine.
- GPU: una sola misura; server sano e GPU a 0% prima della partenza.
- Produzione: turni e configurazione letti soltanto. Dati, stato, cache e log
  runtime sono stati reindirizzati a una directory temporanea prima di
  importare il runtime. Nessun servizio riavviato, nessun file runtime
  modificato, nessun commit.
- V23lite: **116/120 valide**. Invalidità: un trasporto/JSON, un arco sorgente,
  due richieste incomplete.
- Accordo grezzo e accordo utilizzabile coincidono: 65. L'attuale non produce
  rotta in 3 casi; il nuovo in 2 casi grezzi e in 5 dopo il validatore.
- Latenza: attuale p50 310 ms, p95 970 ms; nuovo p50 3.961 ms, p95 13.684 ms.
  Il tempo totale dei due bracci è stato rispettivamente 55,1 s e 704,5 s.
- Impronta di `confronto_intento.json`:
  `bd29821e8da22b8f76351786fd5a092700611cb22936207a3509c682a825481f`.

La rotta attuale usa `actions` quando presenti, altrimenti la coppia primaria.
Le 7 `implicit_actions` sono conservate nel JSON ma non incluse nella rotta:
il chiamante di produzione in `agent_runtime.py:6021-6044` costruisce
`engine.types.Intent`, che non ha quel campo. In questo campione, inoltre,
quelle sette aggiunte lessicali sono tutte spurie o ridondanti.

## Adjudicazione dei 55 disaccordi

L'indice è quello zero-based di `confronto_intento.json`, che contiene frase e
frame completi.

### Nuovo migliore — 9

| indice | motivo |
|---:|---|
| 10 | “altre foto” è `find/images`; `persons` è il registro di identità, non il corpus fotografico. |
| 23 | I processi correnti sono uno snapshot: `get/processes`, non `find/processes`. |
| 33 | “Silvia è un utente” è una descrizione, non una richiesta eseguibile: nessuna rotta è corretta. |
| 34 | La tabella formatta record già prodotti: `render/entries`, non `render/texts`. |
| 40 | Il flusso corretto sullo store è `find/entries → send/messages → write/entries`; l'attuale perde entrambi gli oggetti store. |
| 44 | Le issue remote vanno cercate e i record vanno salvati nello store: `find/issues → write/entries`. |
| 47 | Anche qui i processi sono uno snapshot: `get/processes`. |
| 106 | `find/issues → write/entries` distingue correttamente GitHub dallo store locale. |
| 107 | Keep è lo store `entries`; la ricerca non può restare con oggetto nullo. `find/entries → create/files` conserva anche la creazione esplicita del nuovo foglio. |

### Nuovo peggiore — 46

| indice | motivo |
|---:|---|
| 0 | Scambia una ricerca di foto con il registro `persons`. |
| 24 | `describe/entries` non è il conteggio deterministico delle righe inserite; la rotta nuova non è interamente corretta. |
| 25 | Interpreta `undo` come `change/files`; l'attuale lascia intenzionalmente il verbo riservato al builtin. |
| 30 | Perde sia `approval` sia l'aggiornamento di stato della issue. |
| 31 | Frame invalido e nessuna rotta utilizzabile per l'accesso al sito. |
| 32 | Cancella i file ma perde la cancellazione distinta delle directory. |
| 36 | `get/entries` sostituisce erroneamente `get/preferences`. |
| 38 | Inventa uno store `entries` non nominato e usa `write/files` per un nuovo foglio, mentre il catalogo distingue `create_files_spreadsheet`. |
| 39 | Trasforma i README GitHub in file locali; perde lo scope web/URL. |
| 42 | Frame invalido e nessuna rotta utilizzabile per la sessione autenticata. |
| 45 | L'upload Google Photos è `write/images`, non `write/files`. |
| 48 | Il nome nudo di un deposito non è una richiesta; il nuovo inventa `read/files`. |
| 49 | Per un nuovo spreadsheet il catalogo richiede `create/files`, non `write/files`. |
| 50 | `find/entries` è un'operazione aggiuntiva non giustificata dopo la ricerca già eseguita nella sessione; manca inoltre l'apertura della sessione richiesta da `login`. |
| 53 | “Che task ho programmato” enumera il contenitore: la rotta canonica è `list/tasks`, non `find/tasks`. |
| 54 | La raccolta è corretta, ma il nuovo foglio è `create/files`, non `write/files`. |
| 55 | `open` vale soltanto per `sites`: `open/urls` è una coppia non canonica e perde la lettura della sessione. |
| 56 | Perde la cancellazione delle directory. |
| 58 | Perde la cancellazione delle directory sul dispositivo remoto. |
| 59 | Sostituisce `get/approval` e lo stato della issue con operazioni sullo store generico. |
| 60 | Lo spazio del disco non è `get/files`; il nuovo oggetto è errato. |
| 62 | “Cosa sai fare” appartiene al Tutor, ma il nuovo non lo riconosce: emette una richiesta `describe/none` e viene respinto come incompleta. Un fallimento non riceve credito per l'assenza finale di rotta. |
| 63 | La prima raccolta deve interrogare le issue GitHub, non lo store `entries`; il nuovo resta semanticamente errato. |
| 64 | `login/urls` e `act/files` violano i contratti dei verbi web-session; il nuovo duplica inoltre la persistenza del foglio. |
| 65 | `login/urls` è non canonico e non apre la sessione `sites`. |
| 66 | Interpreta l'annullamento come `delete/entries`, una regressione anche di sicurezza. |
| 67 | Il foglio richiesto è nuovo: `create/files`, non `write/files`. |
| 69 | Perde l'approvazione e l'aggiornamento di stato, sostituendoli con `send/messages`. |
| 72 | Scambia le preferenze con i file. |
| 73 | Il foglio richiesto è nuovo: `create/files`, non `write/files`. |
| 76 | Legge lo store invece di cercare le issue GitHub; soltanto la scrittura finale è corretta. |
| 77 | Perde l'approvazione esplicita e duplica `send/messages`; la correzione degli oggetti store non compensa la regressione di sicurezza. |
| 79 | Manca `extract/entries` e la scrittura del rapporto; aggiunge `get/urls` e `classify/entries` non richiesti e usa `create/files` per due artefatti già descritti come contenuto da scrivere. |
| 82 | `get/entries` non legge né cerca le issue remote. |
| 84 | `find/entries → describe/images` non enumera i corpus né conta le foto; entrambi i rami sbagliano e quindi il nuovo non riceve credito. |
| 90 | Corregge `find` in `get` ma emette due volte lo stesso snapshot richiesto una volta sola. |
| 91 | La raccolta iniziale delle nuove issue diventa erroneamente `find/entries`; il resto più preciso non rende corretta l'intera rotta. |
| 93 | Scambia le credenziali con file. |
| 95 | Riconosce `create/tasks` ma perde ricerca delle issue, similarità, classificazione e analisi esplicitamente richieste. |
| 96 | `login/urls` viola il contratto `login/sites` e manca l'apertura della sessione. |
| 98 | Scambia `preferences` con lo store `entries`. |
| 100 | Trasforma i README GitHub in file locali; perde lo scope web/URL. |
| 104 | Porta le prenotazioni Booking nel calendario `events`, anziché leggerle dalla sessione web. |
| 110 | Scambia esplicitamente un calendario contenitore con un evento. |
| 114 | Un album Google Photos scelto dall'utente è dominio `images` e usa `get`; `read/files` è il dominio sbagliato. |
| 115 | “Dove mi trovo” è `get/location`; il nuovo `get/persons` è peggiore dell'attuale già errato. |

## Scorciatoie lessicali osservate

L'estrattore attuale ha evitato il modello in **5/120** casi:

| causa | casi | esito rispetto al nuovo |
|---|---:|---|
| `undo.intent_bypass` | 3 | nessuno sostituibile: V23lite produce `change/files`, `delete/entries` o fallisce il trasporto |
| `machine.reference` + `health.section_focus` | 2 | entrambi arrivano alla stessa rotta `get/processes` senza lessico |
| `system.status_query` | 0 | non misurato sul campione |

Uno dei tre bypass undo è inoltre un falso positivo: la forma substring
`annulla` scatta dentro “appuntamenti annullati” in una richiesta composta e
azzera l'intera analisi. La stessa forma serve però all'undo reale dell'indice
66, che V23lite trasforma pericolosamente in `delete/entries`; non è quindi una
voce cancellabile isolatamente.

Le due equivalenze hardware dipendono da tre forme di superficie uniche
(cinque occorrenze nei payload IT/EN):

1. `machine.reference`: `server` (IT e EN);
2. `health.section_focus.network`: `ip` (IT e EN);
3. `health.section_focus.cpu`: `processore` (IT).

Queste tre forme sono **non più necessarie per scegliere la rotta nei due casi
misurati**, ma non sono cancellabili oggi dal dizionario condiviso:
`health.section_focus` seleziona anche la sezione della risposta e
`machine.reference` è riusato da `_ensure_health_arg`. La rimozione sicura, una
volta esistente un sostituto senza regressioni, è quindi del ramo lessicale in
`intent_extractor.py`, non delle forme condivise nel DB.

La lista puntuale delle voci globali di `detection_lexicon` cancellabili **ora**
è pertanto: **nessuna**. Le tre sopra sono le sole candidate dimostrate per il
solo instradamento del bypass hardware; restano bloccate sia dai consumer non
di instradamento sia dal verdetto qualitativo complessivo.

## Censimento della catena lessicale

Il conteggio distingue gli slot dichiarati dalle forme uniche, perché la stessa
parola compare in più tabelle e lingue.

| superficie | tabelle/concept inclusi | slot | forme uniche |
|---|---|---:|---:|
| `fast_path.py` | ora, data, undo, posizione, prefissi undo, identità assistente | 86 | 79 |
| `prefilter.py` | verbi, oggetti, clitici, posizione relativa, EXIF, ora, shell, stopword, verbi affinity generici | 775 | 711 |
| `detection_lexicon_seed.py`, solo instradamento | 14 concept | 169 | 153 |
| **unione delle tre superfici** | deduplicata fra file | **1.030** | **921** |

I 14 concept conteggiati sono:
`undo.grammar_marker`, `undo.intent_bypass`, `tasks.marker`,
`tasks.recurrence_word`, `tasks.schedule_phrase`, `tasks.recurrence_phrase`,
`skills.marker`, `system.status_query`, `machine.reference`,
`object.store_sink`, `compound.connector_word`, `images.web_search_scope`,
`images.reverse_search_intent`, `query.multistep`.

Il perimetro allargato con quattro concept che partecipano all'instradamento ma
hanno anche consumer diversi (`health.section_focus`, `provider.markers`,
`sites.login_intent`, `sites.login_entry_target`) porta il totale a **1.183
slot / 1.044 forme uniche**. Non li si può contare come dizionario eliminabile
in blocco.

Sul campione, `fast_path.py` scatta tre volte: due undo e una data. Nessuna
delle tre voci è sostituibile da V23lite: sui due undo il nuovo inventa azioni
distruttive; su “che giorno è oggi” V23lite e l'estrattore LLM concordano
erroneamente su `get/numbers`, mentre il fast path corregge a `get/now`. Anche
questo impedisce una cancellazione in blocco del fast path.

## Conclusione operativa

La frase obiettivo non può essere scritta: sul campione il nuovo percorso non
instrada altrettanto bene dell'attuale e non autorizza la rimozione di voci dal
dizionario condiviso. Il risultato utile è più circoscritto: il bypass hardware
ha due casi equivalenti senza lessico, mentre undo, fast path, preferenze,
sessioni web, approvazioni, contenitori e richieste composte mostrano
regressioni concrete. Nessuna modifica di produzione è giustificata da questa
misura.
