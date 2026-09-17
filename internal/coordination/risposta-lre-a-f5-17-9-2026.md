# Riscontro LRE alle richieste F5 — 17 settembre 2026

Risposta a `internal/coordination/richieste-a-lre-17-9-2026.md` nel worktree
`/opt/metnos/.claude/worktrees/rm0009-development`, tenendo conto della
successiva precisazione dell'agente F5 riportata da Roberto e del suo
«procedi». Questo passaggio riguarda documentazione e coordinamento, non
autorizza né esegue una pubblicazione.

## Decisioni operative

- Nessun arresto, riavvio, pausa, annullamento, migrazione o nuova
  indicizzazione. Il lavoro attuale può proseguire.
- Nessuna nuova fusione: l'agente F5 ha già integrato il codice LRE in
  `0f922c5c`. La correzione della barriera resta a quell'agente, senza modifiche
  concorrenti da questo worktree.
- Conservata l'aggiunta documentale rimasta in sospeso in
  `internal/reports/lre-photo-analysis-stop-20260916.md`: sono 42 righe di
  cronologia del ripristino console e del successivo errore di decodifica.
  Aggiunta soltanto una nota iniziale per distinguere quella cronologia
  dallo stato della release 63; nessuna osservazione precedente scartata.
- Questa chiusura aggiunge solo documentazione al ramo LRE: l'eventuale
  nuovo commit documentale non è automaticamente presente nel ramo F5.

## Fusione verificata

Riferimenti letti alle 07:03 UTC del 17 settembre:

- codice LRE: `00b86d95eb8d4b8a67fa730e1dcb2973f1cb6e29`;
- ramo F5: `0f922c5c81afd96120dc0da5064ae4442740dac8`;
- prima della presente chiusura: zero commit esclusivi LRE, 71 esclusivi F5.

I 14 file indicati come conservati dall'agente F5 sono identici al ramo LRE,
compresi console, calcolo dell'avanzamento, messaggi e relative prove.
Sono inoltre invariati il codice dell'indicizzatore foto, i contratti del
piano fotografico, gli schemi e le migrazioni del database LRE.

Rivisti separatamente `runtime/loader.py` e
`runtime/durable_workloads/execution.py`: restano la firma semantica della
cache, l'applicazione dell'istantanea esatta e la classificazione degli errori
LRE. La nuova verifica della generazione avviene anche dopo l'attesa delle
risorse e dello scheduler; il percorso ordinario conserva la cattura d'uso.

## Prove della revisione

Ambiente isolato: `/tmp/metnos-f5-lre-review-20260917.92CSDF`.

- Caricatore, ponte LRE, composizione, stato del ciclo di vita, guardia e
  coordinatore di migrazione: 217 prove superate, una fallita.
- Migrazione dello stato di ciclo di vita: altre 49 prove superate.
- Il fallimento è
  `test_default_registry_exposes_generic_core_and_registered_capabilities`:
  `read_files_ocr` assente dal catalogo verificato nel contesto di test privo
  delle chiavi fidate. Riprodotto identico nel ramo LRE precedente alla fusione;
  nessuna firma aggirata e nessun test modificato o escluso per ottenere verde.

Queste prove non certificano l'installazione F5 né la ripresa del job reale
attraverso una migrazione. Nessuna prova ha usato il database LRE produttivo.

## Barriera: rilievo confermato e assegnato a F5

`install/birth_lifecycle_migration.py::apply_cutover_v1` usa la barriera F4
senza passare il catalogo distribuito. L'elenco storico controlla i punti
d'ingresso che F4 doveva ritirare: per LRE e Telegram sono le precedenti unità
utente, non le unità di sistema oggi operative. HTTP è incluso anche come
unità di sistema. Non è quindi una prova che tutti gli scrittori correnti siano
fermi, anche se i punti d'ingresso storici risultano mascherati o inattivi.

Sonda isolata sul codice fuso: HTTP e vecchie unità dichiarati inattivi,
`system/metnos-durable-worker.service` dichiarato attivo. La barriera accetta
e non interroga quel servizio. Nessuna chiamata systemd reale nella sonda.
L'agente F5 ha confermato il difetto e preso in carico la verifica basata sul
catalogo distribuito. Questa è la fotografia precedente alla sua correzione;
la verifica del nuovo commit è riportata nella sezione seguente. Nessuna
modifica concorrente alla barriera viene implementata qui.

Anche la normale salute `quiescent=true` non basta: riguarda turni HTTP e
browser, non l'assenza di tentativi LRE. L'ultima lettura privata
`run-cpf54c3t`, 06:57 UTC, mostra contemporaneamente quel valore e un tentativo
LRE attivo, 172/967 batch di analisi confermati, zero batch falliti o da
verificare e nessun riavvio del lavoratore. Produzione ancora sulla release 63.

## Aggiornamento: letta e verificata anche la risposta F5

Letta integralmente l'aggiunta nel file delle richieste, commit `e4de8b29`,
e la correzione precedente `1df7197d`. Non è una lettura della sola versione
iniziale del documento.

La correzione aggiunge a `apply_cutover_v1` una verifica delle unità del
catalogo installato e verificato, prima di avviare la copia. Lavoratore
attivo: `cutover_writer_running`; catalogo vuoto, illeggibile o stato non
verificabile: rifiuto. Non avvia né arresta servizi. L'esclusione delle
dipendenze esterne non gestite direttamente è intenzionale; questa modifica
è nei sorgenti F5, non nella release produttiva.

Prove aggiuntive isolate in `/tmp/metnos-f5-barrier-review-20260917.kfUCqs`:
`test_birth_lifecycle_cutover.py`, `test_contract_cutover_guard_session.py`,
`test_services_registry.py` e le due varianti del test sulla ricetta di
rilascio: **117 superate, una saltata, una fallita**. I nuovi casi sulla
barriera mirata passano. Non è una certificazione della migrazione reale.

### Difetto condiviso ancora aperto

Confermato: `_prove_stack_stopped_v1` interroga ancora soltanto le unità
storiche. L'aggiunta F5 protegge la propria migrazione ma non corregge tutti
i chiamanti della barriera. Resta un'attività separata da risolvere e
collaudare nei sorgenti prima di un prossimo rilascio, senza modificare
produzione durante questo job. Non viene dichiarata risolta qui.

Va distinta la barriera generica dal percorso di rilascio che passa
esplicitamente `release_catalog`: quel percorso effettua già la fermata e
la verifica delle unità del catalogo della release precedente. La correzione
condivisa dovrà conservare questo caso, la prima installazione senza catena
precedente e la transizione fra cataloghi: non basta imporre ovunque il
catalogo della radice corrente, che nel processo successore può essere
legittimamente diverso da quello ancora selezionato.

La richiesta operativa di coordinare gli arresti rimane valida. Un rifiuto
automatico è una protezione aggiuntiva, non autorizza una manutenzione né
sostituisce la verifica delle condizioni di ripresa del lavoro LRE.

### Il test rosso segnalato è distinto dal precedente

`test_early_recipe_check_uses_real_canonical_and_independent_codecs[False]`
fallisce sul ramo F5 con `PreflightError: service source recipe`: il confronto
fra ricetta del catalogo e identità attesa non coincide. Ripetuto da solo
insieme alla variante `[True]`: una prova fallita e una superata.
Sul ramo LRE `00b86d95`, nello stesso interprete e con directory isolate,
entrambe le varianti passano. Quindi questa verifica **non conferma** che il
rosso fosse già presente sul ramo LRE prima della fusione. Può essere
preesistente sul ramo F5, ma va distinto e chiuso prima del rilascio.
Non è l'assenza di `read_files_ocr` dal catalogo firmato descritta sopra.
Nessuna impronta è stata aggiornata e nessun controllo è stato aggirato.

Controllo finale di produzione in sola lettura, `run-6e3rmdbs`, 07:11 UTC:
stesso job ancora `running`, **176/967 batch di analisi confermati**, uno
in esecuzione, nessun batch fallito o da verificare, nessuna causa bloccante.
Restano la release 63, gli stessi processi e zero riavvii dei servizi.
Gli errori già registrati nello storico non sono cancellati né nascosti.

## Vincolo prima di un futuro rilascio

La fusione nel ramo F5 non aggiorna automaticamente il ramo LRE. Prima di
`rm0008_release_cycle.py prepare`, ricontrollare che **il ramo effettivamente
usato per produrre il candidato** includa entrambi i lavori. Per il ramo LRE:

```sh
git merge-base --is-ancestor codex/rm0009-development codex/lre-backend-release
```

Un esito non zero impone di fermare la preparazione e riallineare le sorgenti;
non significa fermare i servizi. Non sono stati rigenerati anticipatamente
impronte, censimento o firme e non è stato preparato un nuovo rilascio.
La finestra di attivazione e le prove della ripresa rimangono da concordare
quando F5 sarà pronto. La correzione UI della stima in `00b86d95` rimane
collaudata ma non pubblicata: nessun riavvio per attivarla durante questo job.
