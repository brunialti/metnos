# Arresto dell'analisi foto LRE — 16 settembre 2026

Stato: cause riprodotte e correzioni candidate verificate; pubblicazione e
recupero operativo da verificare separatamente. Nessuna ripetizione del
lavoro durante la diagnosi. Release osservata inizialmente: 57.

## Esito alle 12:22 (Europe/Rome)

Il servizio è pronto ma il lavoro `wrk_b7a73e10713b4c15a720d264cf8fd98a`
è `needs_attention`, revisione `rev_d9c8f1bcfbea475da2680a9d00fe9226`.
La scansione ha censito 30.942 sorgenti in 967 gruppi; il blocco `discover`
e tutti i 967 blocchi `folders` sono confermati. `analyze` ha 14 blocchi
da verificare, 953 pendenti e nessuno in esecuzione. Non si è perso tutto
il lavoro, ma non è provata la recuperabilità automatica dei blocchi interrotti.

## Cronologia dimostrata

- 12:04:42: servizio configurato con 31 corsie di orchestrazione automatiche.
  Non significa 31 analisi foto o 31 processi contemporaneamente.
- 12:15:11: scansione completata e confermata.
- 12:17:42: preparazione dei 967 gruppi completata.
- 12:17:47: quattro errori `sqlite3.OperationalError: database is locked`
  nel ciclo parallelo, entrando in transazioni per `reconcile_expired` e
  `adopt_reusable_results`; il servizio passa a `degraded`.
- 12:18:18: arresto delle corsie non concluso entro il limite, 14 ancora attive.
- 12:18:29–31: sei tentativi falliscono con
  `execution.executor_unavailable` / `capability_unavailable`, ripetizione
  manuale. Il codice colloca questa categoria nella cattura di un'eccezione
  del caricamento dell'esecutore verificato. Non dimostra che l'esecutore
  manchi dall'installazione: `discover` aveva usato lo stesso nome.
- 12:19:55: ancora otto blocchi segnati in esecuzione, uno assegnato e sei
  da verificare; file intermedi scritti alle 12:19:50. Quindi in quel momento
  non era un arresto completo, pur essendo già degradato.
- 12:20:21: ultimo file intermedio osservato; dieci parti `analysis`, non
  dieci gruppi LRE confermati.
- 12:20:49: processo del servizio terminato con codice 1; picco memoria
  contabilizzato da systemd 44,3 GB. Non è una misura della memoria delle
  sole foto né prova di esaurimento memoria.
- 12:20:52: servizio ripartito, PID 531750. Roberto ha confermato di averlo
  riavviato manualmente. Nessun riavvio è stato richiesto da questa diagnosi;
  questo episodio non è una prova di riavvio automatico del servizio.
- 12:21:19: recupero di nove assegnazioni scadute. Quella mai eseguita torna
  pendente; otto iniziate passano a verifica manuale per consumo incerto.
  I sei precedenti errori rimangono: totale 14. Servizio di nuovo `ready`.
- 12:22:03: API ancora `needs_attention`, nessun blocco in esecuzione;
  avanzamento delle unità note 50%, previsione finale assente. Il 50% non
  misura metà delle foto analizzate né metà del tempo necessario.

## Cause provate e limiti della diagnosi

`service._run_parallel_cycle` somma tutti i fallimenti delle corsie raccolti
nello stesso passaggio. Almeno tre fanno fallire il ciclo del servizio:
quattro contese SQLite contemporanee sono quindi sufficienti senza tre
distinti giri falliti. Ogni corsia esegue anche manutenzione e materializzazione
in `ExecutionBridge.run_once`, oltre al proprio lavoro. Questa è una causa
verificata di fragilità sotto contesa; il detentore originale del blocco di
scrittura non è stato identificato.

La causa interna esatta delle sei eccezioni del caricatore non è conservata
nel DTO: `ExecutionBridge._prepare_executor` le traduce tutte nella stessa
categoria. Non è dimostrato che abbiano la stessa causa della contesa SQLite.
Non disabilitare firme, attestazioni o controlli di generazione per aggirarle.

La scansione iniziale scrive parti immutabili ma non conserva un cursore di
ripresa: una scansione interrotta ricomincia. Ora quella fase è confermata;
non deve essere cancellata o rifatta per recuperare la fase di analisi.
La previsione corrente esclude esplicitamente i lavori con più fasi, quindi
per questo piano resta assente anche quando il lavoro procede regolarmente.

## Intervento da progettare e verificare prima della ripresa

1. Riprodurre la contesa con un archivio isolato e più corsie, senza foto reali;
   misurare la durata delle transazioni e separare manutenzione/esecuzione.
2. Distinguere contesa temporanea da guasto del servizio, con attesa limitata
   e senza invalidare tentativi ancora vivi; mantenere fencing e limiti.
3. Contare i fallimenti del ciclo, non scambiare fallimenti simultanei di
   corsie per distinti cicli consecutivi. Provare arresto e recupero sotto carico.
4. Conservare un codice diagnostico chiuso della causa del caricatore,
   senza esporre eccezioni arbitrarie, percorsi o credenziali.
5. Verificare risorse, assegnazioni e consumi incerti prima di autorizzare
   la ripetizione dei 14 blocchi. Non cambiare direttamente lo stato nel DB.
6. Mostrare distintamente servizio pronto, lavoro da verificare, fase,
   unità confermate, parallelismo effettivo e motivo della stima assente.

## Evidenze in sola lettura

Evidenze private create dal lettore amministrativo sotto
`/var/lib/metnos-admin/agent-runs/`:

- `run-4zbpp0vp`: avanzamento alle 10:19:55 UTC.
- `run-nia1ni9l`: stato recuperato alle 10:21:36 UTC.
- `run-mkmg8_4a`: salute, release attestata, servizi e API alle 10:22 UTC.

Registri del servizio `metnos-durable-worker.service`, intervallo
10:04–10:22 UTC; lettura locale del codice corrispondente alla release 57.
La diagnosi non ha scritto al database di esercizio, cancellato indici,
premuto «Riprova» o modificato il numero di corsie.

## Correzioni candidate e prove indipendenti

- **Disponibilità del catalogo:** la data del database di statistiche entrava
  nella firma della cache. Una chiamata ordinaria da HTTP, Telegram o LRE
  poteva invalidarla e, se concorrente a entrambe le autenticazioni, produrre
  `store_snapshot_unstable`. Riproduzione con contratto firmato e quattro
  lettori concorrenti. Ora viene firmato e applicato lo stesso snapshot
  immutabile degli override di ciclo di vita. Conteggi d'uso ignorati,
  archiviazione/deprecazione e rimozione degli override ancora invalidanti,
  anche con WAL; stato illeggibile rifiutato. Una seconda review ha scoperto
  e fatto correggere anche un'applicazione finale scollegata dalla firma.
- **Scritture inutili:** controlli preliminari conservativi in sola lettura
  evitano le transazioni di recupero/riuso quando non c'è lavoro. Tutte le
  verifiche originali restano nella transazione autorevole; nessuna nuova
  autorità derivata dal controllo preliminare. Test su writer esterno,
  rinnovo concorrente della concessione, pausa, orologio e revisioni precedenti.
- **Supervisione:** contese native SQLite distinguibili da guasti, attesa
  crescente e limitata senza interrompere subito le corsie vive; un gruppo
  di eccezioni simultanee conta come un solo ciclo fallito. Firme, fencing,
  contabilizzazione e massimi di tentativi restano invariati.
- **Diagnostica:** gli errori del caricatore conservano una causa enumerata,
  senza testo arbitrario dell'eccezione; nessuna nuova ripetizione automatica.
- **Interfaccia:** descrizione/cartella/fase del lavoro, lettura chiara dei
  tempi e dei motivi di n.a., parallelismo non confuso con thread/processi,
  avvisi distinti dalla salute del servizio. Prove Chromium IT/EN isolate.

Stress riproducibile opzionale `test_contention_scale.py`: 31 corsie,
966 unità, circa 4 MB di risultati sintetici, nessun modello né file reale.
Prima: 2 SQLITE_BUSY, 5,60 s, manutenzione ripetuta circa 990 volte per tipo.
Dopo: 0 SQLITE_BUSY, 3,92 s, 966 unità confermate e nessuna scrittura di
manutenzione inutile. Non è una garanzia per qualunque scala o carico.

## Blocco residuo del lavoro già interrotto

Lettura `run-igmgra5p` alle 10:39:56 UTC: `usage_unknown=1`,
`usage_complete=0`, consumo noto 116.489 token in ingresso e 3.637 in uscita,
costo registrato zero. I sei errori del caricatore hanno uso verificato;
gli otto tentativi interrotti hanno uso incerto. Non inferire che l'uso
ignoto sia zero perché i modelli sono locali.

Non esiste oggi un percorso canonico per riconciliare questo flag storico:
la ripetizione manuale è bloccata dal controllo contabile, la registrazione
dei consumi richiede una concessione viva, e l'ammissione di revisione è
ammessa soltanto su un lavoro iniziale. Nessuna scrittura SQL amministrativa
è autorizzata come sostituto. Un nuovo lavoro non riusa automaticamente la
scansione idempotente (il riuso canonico è limitato a unità pure).

Chiesta a Roberto una scelta: nuova procedura esplicita di riconciliazione
prudenziale conservando i risultati, oppure nuovo lavoro con vecchio storico
intatto e preparazione ripetuta. Nel frattempo si possono pubblicare le
correzioni preventive senza sbloccare arbitrariamente il lavoro esistente.
