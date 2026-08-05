---
id: 0189
title: Executor intelligenti come agenti a mandato ristretto
date: 2026-07-11
status: accepted
area: runtime
related: [0001, 0045, 0071, 0159, 0188]
---

# 0189 — Executor intelligenti a mandato ristretto

## Contesto

Un executor tradizionale applica una procedura nota a input tipizzati. Alcuni
compiti hanno invece uno scopo preciso ma un percorso non noto a priori: login
multi-stadio, wizard, UI variabili, risposte API che richiedono recupero. Portare
ogni variazione nel planner irrigidisce il linguaggio e gli attribuisce dettagli
e autorita' che appartengono al dominio operativo.

## Decisione

Metnos ammette **executor intelligenti**. Operativamente sono agenti: osservano,
scelgono un passo, agiscono e verificano. Architetturalmente restano executor:
il loro mandato, contratto e autorita' sono chiusi.

- Il contratto pubblico non cambia. Il planner passa gli stessi argomenti e
  riceve lo stesso schema di output di una implementazione non agentica.
- Lo scopo deriva dal nome e dalla descrizione dell'executor; il modello interno
  non puo' sostituirlo, ampliarlo o creare un secondo piano generale.
- Il ciclo interno e' bounded per passi e tempo:
  `observe -> resolve -> verify -> gate -> execute -> verify`.
- Resolver deterministici e controlli strutturali hanno precedenza. Il modello
  gestisce soltanto l'incertezza residua e sceglie entro un insieme enumerato e
  fissato dal runtime.
- Capability, sandbox, origine dei dati, gate e policy del normale executor
  restano invariati. L'executor non puo' auto-approvarsi o estendere la propria
  autorita'.
- Il successo richiede una postcondizione osservabile. Budget esaurito, stato
  instabile o postcondizione assente producono un errore tipizzato, mai un
  successo inferito.
- Quando l'ostacolo richiede autorita' o informazione umana, l'executor produce
  un handoff esplicito conservando soltanto lo stato necessario e sicuro.

Il pattern e' ortogonale ai domini e non introduce un nuovo suffisso di naming,
un nuovo tipo di pipeline o una sintassi utente. Non ogni executor deve essere
intelligente: la procedura diretta resta preferibile quando lo stato e' noto.

## Criteri di adozione

Un executor puo' adottare il pattern quando esistono tutti questi elementi:

1. scopo stretto e non ambiguo;
2. spazio d'azioni limitato;
3. progresso riosservabile;
4. postcondizione verificabile;
5. budget finito;
6. autorita' separata dall'eventuale modello.

Ricerca aperta, strategia multi-dominio e creazione senza criterio di
completamento restano responsabilita' del planner.

## Catalogo

Il concetto di executor intelligente e' separato dal censimento per dominio.
Il catalogo pubblico e' generato deterministicamente dai manifest firmati sotto
`executors/` e raggruppato secondo l'oggetto canonico del nome. Gli executor
installati o sintetizzati appartengono al catalogo runtime della singola
istanza. Nessuna lista manuale e nessuna mappa per dominio ad hoc.

## Prima applicazione

ADR 0188 applica il pattern a `login_sites` e alla navigazione bounded di
`act_sites`: il planner conosce soltanto input e output, mentre il broker risolve
gli stati intermedi con autorita' e verifiche specifiche del compito.

`extract_entries` applica lo stesso confine all'incertezza semantica: se lo
schema non e' esplicito, lo inferisce una sola volta con un modello locale entro
un numero massimo di campi, poi valida e applica il normale contratto tipizzato.
Il planner non deve conoscere i campi specifici del dominio.

## Criterio di generalizzazione

Una variante di etichetta, lingua, layout, ordine dei passaggi o schema dei
record deve essere risolta dallo stesso executor tramite osservazione,
lessico traducibile, selezione bounded e riosservazione. Non giustifica un ramo
per sito o per caso.

Serve nuovo codice soltanto quando il compito richiede una capacita' che il
contratto non possiede: una nuova primitiva d'azione, un nuovo canale di
osservazione o fattore, una diversa autorita', oppure una nuova postcondizione
verificabile. L'intelligenza rende adattivo un insieme di capacita'; non crea
sensori, permessi o verifiche mancanti.

## Conseguenze

L'adattamento alle variazioni rimane vicino alla competenza che sa verificarlo,
senza trasformare la CLI in un linguaggio o il planner in un automa di
micro-passaggi. Il costo e' che ogni executor intelligente deve avere test sugli
stati, sui budget, sugli handoff e sulle postcondizioni, non soltanto sul caso
felice.

## Runtime standard

L'implementazione comune vive in `runtime/agentic_executor.py` e offre runner
sincroni e asincroni con lo stesso contratto: contesto bounded, proposta,
validazione, esecuzione, postcondizione e budget. Gli executor sincroni possono
usare `deterministic_then_fallback_sync`: il risultato deterministico resta la
risposta canonica quando il fallback non produce un miglioramento validato.

Il runtime non sceglie provider e non contiene prompt. Ogni adapter di dominio
carica i prompt da `runtime/prompts/<lang>/` tramite `prompt_loader`; IT ed EN
devono essere presenti e le altre lingue seguono il fallback standard. Testi
osservati da pagine, documenti o provider sono dati non attendibili e non
diventano istruzioni. Codici di stato e vincoli interni sono identificatori
language-neutral; qualsiasi messaggio mostrato all'utente passa da i18n.

Prima adozione del runtime comune:

- Sites: selezione VLM, navigazione testuale e riduzione del goal;
- `extract_entries`: inferenza bounded dello schema e validazione dei record;
- `read_files_ocr`: Tesseract prima, VLM locale solo su risultato insufficiente;
- `find_images_web`: ranking deterministico prima, ranking semantico locale solo
  in assenza di segnali lessicali.

Il contratto esterno e i gate comuni non sono definiti da questo ADR ma dallo
standard fondativo `EXECUTOR_STANDARD.md` (`metnos.executor/1.0`).
L'intelligenza interna e' un asse ortogonale: non cambia schema pubblico,
autorita', trasporto o criteri di conformita'.
