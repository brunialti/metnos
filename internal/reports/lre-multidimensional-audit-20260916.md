# Revisione multidimensionale LRE — 16 settembre 2026

Stato: **correzioni implementate, revisionate e verificate in ambiente isolato.
Non distribuito né certificato in produzione**.
Mandato: analizzare il codice, correggere difetti dimostrati e rimuovere
duplicazioni mantenendo le garanzie di autorità, contabilità e copertura.
Base: `e69d47d8`; rapporto operativo preesistente conservato.

## Perimetro e responsabilità

Il nucleo e le integrazioni principali comprendono oltre 25.000 righe Python,
oltre a HTTP, console, schema, manifest e prove. Una suite verde non equivale
alla certificazione dell'indicizzazione reale delle oltre 30.000 foto.

| Dimensione | Perimetro | Verifica |
|---|---|---|
| Recupero e concorrenza | service, worker, execution, coordinator, runtime_bindings, resource_readiness; transizioni storage | revisore indipendente + regressioni |
| Autorità e sicurezza | artifacts, source_authority, inventory, admission, compiler, direct_invocation, schema, events, image_preset, internal_runners | revisore indipendente + regressioni |
| Prestazioni e coerenza delle letture | storage, migrations, control, HTTP | coordinatore + conteggio istruzioni SQL e scrittori concorrenti |
| Osservabilità e traduzioni | console, HTTP, catalogo IT/EN | coordinatore + script browser e prove reali isolate |
| Indicizzazione e completezza | image_indexing, image_index_build, create_images_indices | revisore recupero + coordinatore + revisione incrociata sicurezza |
| Qualità e manutenzione | duplicazioni, codice morto, prove di regressione, documentazione | revisione integrata, nessuna riscrittura indiscriminata |

## Criteri non negoziabili

- Nessuna cancellazione di foto, risultati o storico di esercizio.
- Nessun azzeramento dei consumi incerti o allargamento automatico dei limiti.
- Nessuna disattivazione di firme, delimitazioni dei tentativi o controlli del proprietario.
- Nessun rilancio del lavoro reale né nuova release durante l'indagine.
- Ogni correzione deve avere una prova del difetto e una verifica del comportamento
  corretto; le decisioni nuove di prodotto restano distinte dai difetti di codice.
- Gli errori di una sorgente non possono trasformarsi in copertura completa dichiarata.

## Riscontro iniziale

Suite esistente del nucleo: **557 superate, 4 prove di carico facoltative non
eseguite**, 47,10 secondi. I difetti sotto sono quindi lacune delle prove
precedenti, non una certificazione già raggiunta.

## Rilievi corretti

1. **Query avanzamento quadratica senza statistiche SQLite.** Riprodotta con
   1.200 tentativi sintetici e limite deterministico di due milioni di istruzioni
   SQL; la query iniziale supera il limite. Aggregazione per fase unica, riusata
   anche per il totale, e lettura tentativi prima delle ricerche puntuali sulle
   unità: regressione verde senza `ANALYZE`, senza indici imposti per nome e
   senza scritture del database dalla console. Eliminate le due scansioni e
   aggregazioni quasi identiche dello storico tentativi.
2. **Risposta console internamente incoerente sotto scritture concorrenti.**
   Elenco e dettaglio leggevano stato, contatori e avanzamento in istanti
   diversi. Riprodotto un annullamento fra le letture: `queued` e contatori già
   annullati nella stessa risposta. Transazione di sola lettura per la proiezione
   composta; lo scrittore continua in WAL, la lettura non prenota lo scrittore.
3. **Supervisore bloccato dopo una scadenza.** Impedite nuove ammissioni mentre
   la corsia scaduta è attiva; dopo il ritorno delle chiamate, raccolti gli esiti
   e riconciliato lo stato prima della ripresa. Prova anche con esecuzione reale
   limitata che supera il timeout: lo stesso supervisore gestisce il lavoro
   seguente senza accettare il risultato tardivo. Non termina thread Python
   arbitrari che non ritornano: lì rimane necessaria la supervisione di processo.
4. **Aperture di FIFO prima del controllo del tipo.**
   una sostituzione con file speciale può bloccare hash/lettura indefinitamente.
   Aperture non bloccanti seguite dal controllo del descrittore, non dal solo
   controllo preventivo del percorso. Corretti artefatti, inventario, parti foto,
   copie e lettore entries. Hash delle copie limitato a dimensione congelata più
   un byte; prova deterministica anche di sorgente che cresce durante la lettura.
5. **Segreti in schemi annidati delle invocazioni dirette.** I controlli
   `sensitive`, `writeOnly`, `runtime_resolved`, formato password/secret valgono
   anche per proprietà, array e combinatori annidati prima della persistenza.
   Visita limitata; riferimenti di schema irrisolti negati. I dati letterali non
   vengono scambiati per annotazioni di schema.
6. **Revoca/scadenza durante acquisizione della sorgente.** Autorità riletta
   prima di consegnare una sorgente dopo copia locale o attestazione remota,
   conservando controlli di impronta/locator e rimuovendo la copia privata negata.
7. **Primi lavori invariati monopolizzano la riconciliazione.** Un lavoro in
   pausa/annullamento con corsia ancora attiva poteva impedire di elaborare altri
   lavori con batch piccolo. Il selettore sceglie transizioni eseguibili, non
   stati nominalmente candidati; annullamento prioritario anche dopo scadenza
   del budget. Prove con 257 unità e batch uno, senza ampliare il batch.
8. **Commit tardivo dopo attesa sul database.** La verifica della scadenza usava
   l'ora acquisita prima di ottenere il writer. Ora il clock viene letto dentro
   la transazione; il worker passa il clock, non un'ora già vecchia. Prova con
   clock che avanza durante l'acquisizione, sia default sia callback.
9. **Materializzazione quadratica dei figli.** Quattro selettori con esclusione
   degli stati terminali riscansionavano tutti i predecessori completati. Uso
   degli stati nonterminali indicizzati, senza cambiare i nove stati ammessi.
   Per 100 letture sintetiche con 10.000 predecessori: 12.020.800 → 25.000
   istruzioni SQLite; non è un'accelerazione misurata dei modelli foto. Due prove
   VM e 18 casi di equivalenza sui nove stati impediscono la regressione.
10. **Pubblicazione ripetuta accettava file danneggiati.** La presenza dei nomi
    non bastava a provare integrità. Nuova mappa chiusa di cinque dimensioni e
    impronte; rivalidazione dopo rename, su ripetizione e prima dell'attivazione
    di una generazione esistente. Prove con file mancanti, alterazione a stessa
    dimensione, FIFO, sostituzione durante hash, metadati discordanti. L'indice
    precedente resta attivo quando la nuova generazione è invalida.
11. **Regex dei nomi foto con costo esponenziale.** Due espressioni ambigue
    potevano impegnare un core per un semplice nome non corrispondente. Forme
    equivalenti senza quantificatori annidati ambigui; enumerazione/Unicode e
    processo con tempo massimo su nomi lunghi. Non è dimostrato che questa fosse
    la causa dell'errore di decodifica osservato in esercizio.
12. **Contratto congelato incompleto sulle capacità.** Il digest considerava i
    nomi ma non `when` e `hint`, che possono selezionare autorità diverse. Ora
    include la dichiarazione completa ordinata in modo canonico. Impatto di
    compatibilità intenzionale, descritto sotto; nessuna riscrittura storica.
13. **Normalizzazione schema cancellava dati semantici.** Proprietà chiamate
    `title`, `description`, `examples` e chiavi dentro `default`/`const` non sono
    annotazioni del nodo. Ora vengono preservate; solo le annotazioni nei veri
    nodi di schema vengono escluse dall'impronta semantica.
14. **Ritento notifiche senza prova di mancata consegna.** Un risultato senza
    `delivery_ambiguous` esplicito poteva causare doppio invio. Il ritento richiede
    booleano falso, non campo assente, null, zero o stringa. Effetti ambigui non
    vengono ritentati implicitamente.
15. **Schema accettato senza vincoli immutabili.** Tabelle presenti e versione
    corretta non rilevavano trigger rimossi o sostituiti con no-op. La verifica
    confronta i trigger richiesti con le istruzioni canoniche delle migrazioni,
    senza riparazione silenziosa. Provati aggiornamenti dalle versioni 1–6,
    trigger alterati e rollback atomico della migrazione.
16. **Errore console presentato come nessun lavoro.** Caricamento iniziale e
    indisponibilità ora sono distinti dall'elenco realmente vuoto. In errore di
    refresh le righe note restano visibili con stato non aggiornato. Messaggi
    aggiunti al catalogo canonico tramite API i18n, IT/EN; prove Node, HTTP e
    browser isolato in entrambe le lingue.

## Pulizia eseguita

- Unica aggregazione dei tentativi per fase, riutilizzata nei totali del lavoro.
- Unica sequenza delle migrazioni al posto di sei rami ripetuti; stessa sequenza
  di istruzioni e punti di iniezione dei guasti, stessa transazione.
- Eliminati controllo duplicato della cattura contabile prima del trasporto e
  ramo irraggiungibile sulla risposta executor.
- Unico controllo annidato delle annotazioni di autorità, senza duplicato top-level.
- Nessuna riscrittura generale di storage o del ciclo di vita: modifiche locali
  legate a riproduzioni e prove.

## Questioni da non confondere con correzioni già certificate

- Il contratto rigoroso dell'indicizzazione chiude il lavoro per una foto non
  decodificabile. Consentire continuazione e pubblicazione con errori richiede
  copertura esplicita per ogni foto, stato non ingannevole e una politica
  deliberata: non basta intercettare l'eccezione e saltare il file.
- Non è stata diagnosticata la singola foto dell'errore reale: non affermare
  che sia corrotta, né che HEIC o regex siano la causa senza una prova sul file.
- Il flag opzionale di attestazione della generazione nel ponte di esecuzione
  non è collegato dalla fabbrica runtime. Il caricatore e il contratto vengono
  comunque verificati: non è dimostrato un aggiramento. La difesa aggiuntiva
  F5 va riallineata al contratto canonico Birth, non inventata localmente.
- `retention_until` impedisce il download ma non cancella automaticamente blob
  scaduti ancora referenziati. Nessuna nuova pulizia distruttiva introdotta.
- La pulizia delle sessioni residue elenca/ordina tutta la directory prima del
  limite 256: memoria non limitata dal lotto. Difetto potenziale documentato,
  non collegato ai blocchi riprodotti né corretto con una riscrittura in questo audit.
- Cancellazione owner: un crash dopo il database e prima del filesystem può
  lasciare blob privati orfani, non perdita inter-owner; recupero/GC va provato.
- SQLite WAL con `synchronous=NORMAL` non certifica conservazione dell'ultimo
  commit dopo mancanza di alimentazione. I test di processo non sostituiscono
  una prova di power loss né una decisione sui requisiti di durabilità fisica.
- Il budget delle query HTTP non interrompe automaticamente thread già avviati
  quando il browser abbandona la richiesta. Risolta la query quadratica osservata;
  non viene promesso un limite universale di CPU per qualunque query futura.

## Compatibilità e condizioni prima del rilascio

1. Il digest dei contratti con capacità non vuote cambia intenzionalmente. Non
   riscrivere digest o contratti nei lavori ammessi e non rilanciarli alla cieca.
   Verificare il flusso canonico di nuova revisione/lavoro compatibile conservando
   storico e risultati; nessun riuso forzato di risultati non più attestabili.
2. Gli indici precedenti restano leggibili. Le pubblicazioni storiche senza
   `generation_files` non superano la nuova ripresa rapida della pubblicazione.
   I checkpoint privati sono riusabili nella stessa generazione, non trasferiti
   automaticamente a un nuovo lavoro.
3. La modifica dell'executor richiede release canonica con attestazione, non
   firma manuale, copia nell'installazione o semplice riavvio. Preparare release,
   verificare compatibilità, poi prova limitata e distribuzione controllata in
   una finestra senza turni attivi. Non eseguito in questo audit.
4. La certificazione delle oltre 30.000 foto deve confrontare inventario chiuso,
   sorgenti uniche attese, esiti per sorgente, entries pubblicate e file derivati
   integri. Servono zero omissioni non dichiarate e prova di ripresa dopo stop
   nella stessa generazione. Il numero di blocchi o il verde del servizio non
   provano questa copertura. Nessuna dichiarazione di indice completo in assenza
   di queste evidenze.

## Verifica indipendente e integrata

I revisori hanno letto i percorsi assegnati e riprodotto i rilievi. Nella verifica
incrociata il revisore recupero ha controllato migrazioni, clock e letture coerenti
(40 prove verdi); il revisore sicurezza ha controllato integrità di pubblicazione,
aggregazione avanzamento e convergenza (125 prove verdi). Nessuna regressione
bloccante rilevata. Questi conteggi si sovrappongono alle suite sotto e non vanno
sommati.

- Prima esecuzione integrata: 734 prove superate in 67,48 s, incluse le quattro
  prove di carico abilitate con `METNOS_TEST_CONTENTION_SCALE=1`: profilo fino a
  31 corsie e 967 unità fanout, database temporanei.
- Browser e HTTP: 16 prove superate in 11,09 s con Chromium reale isolato,
  comprese IT/EN e layout mobile; nessun collegamento alla sessione utente reale.
- Dopo l'ultima aggiunta documentale e il test di crescita del file: 60 prove
  foto/Tutor superate; inventario pubblico valido, 99 documenti IT/EN.
- Verifica finale consolidata: **737 prove superate in 66,76 s**, nessuno skip,
  con prove di contesa ampliate abilitate. È il conteggio che sostituisce la
  prima esecuzione, non si somma a essa.
- Cluster adiacente loader/ciclo di vita, contratti, prerequisiti, documenti
  pubblici e catalogo i18n: **285 prove e 1.168 sottocasi superati in 14,62 s**.
- Totale delle tre selezioni disgiunte finali: **1.038 test superati**, oltre ai
  1.168 sottocasi del cluster contratti. Non include doppioni delle prove dei
  revisori o delle esecuzioni mirate precedenti. `git diff --check` pulito.

Comando integrato riproducibile dalla radice del worktree (Python del venv):

```sh
METNOS_TEST_CONTENTION_SCALE=1 /opt/metnos/.venv/bin/python -m pytest \
  tests/runtime/durable_workloads \
  tests/runtime/executors/test_image_index_build_phases.py \
  tests/runtime/executors/test_image_index_filename_bounds.py \
  tests/runtime/executors/test_image_index_publication_integrity.py \
  tests/runtime/executors/test_executor_scheduler_durable.py \
  tests/runtime/executors/test_executor_birth_durable_guard.py \
  tests/runtime/scheduler_v2/test_image_index_refresh.py \
  tests/runtime/tutor/test_photo_indexing_documentation.py -q --tb=short
```

Guide IT/EN, ADR 0213/0117 e indice anti-regressione aggiornati. La pubblicazione
delle sole guide statiche è distinta dalla compilazione del Tutor installato e
dalla distribuzione del runtime; il rapporto resta interno, fuori da `docs/`.
Pubblicazione statica completata mediante `deploy.sh --static-only`, con dati
e configurazione Metnos temporanei isolati: 4 file aggiornati, 117 invariati,
deployment `https://e16e9890.mykleos.pages.dev`. Nessun catalogo Tutor installato
ricompilato, nessun rapporto privato inviato, nessun push GitHub.

## Esito finale

Correzioni concrete e prove di regressione disponibili nel worktree
`/opt/metnos/.claude/worktrees/lre-backend-release`. Nessuna cancellazione di
foto, lavori o storico; nessun riavvio e nessuna modifica dei dati produttivi.
Il rapporto operativo preesistente `lre-photo-analysis-stop-20260916.md` è
conservato separatamente. Distribuzione e verifica dell'intero archivio reale
non eseguite: LRE non viene dichiarato globalmente certificato da questo audit.
