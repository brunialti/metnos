# LRE — correzioni prudenti e chiarezza della console

Data: 16 settembre 2026. Stato: **release 54 selezionata e operativa in
produzione, pubblicazione complessiva non certificata**: due ammissioni
executor rifiutate e una verifica semantica Tutor non superata. Lo sviluppo
è avvenuto isolatamente; il rilascio e i riavvii controllati sono stati
autorizzati successivamente da Roberto. LRE resta disabilitato: nessuna
riabilitazione, ripetizione di lavori, variazione di budget o riscrittura dello
storico.

## Perimetro e provenienza

Ramo `codex/lre-resilience`, base `2b06838430ec1ec947333a2790c1d90671070ead`.
Copia di sviluppo: `/opt/metnos/.claude/worktrees/lre-resilience`.
Non sono state modificate le copie di lavoro RM0008 o RM0009 degli altri agenti.
La parte console è stata affidata a un agente distinto, che ha poi svolto una
revisione avversariale del nucleo. Coordinamento e integrazione restano al
responsabile principale.

## Difetti riscontrati

1. Il supervisore apriva tutte le corsie disponibili anche senza unità
   eseguibili. L'apertura dello store e del deposito artefatti ripeteva la
   verifica delle relazioni dell'intera banca dati; le molte corsie vuote
   producevano contesa, non avanzamento.
2. Il servizio intenzionalmente disabilitato smetteva di pubblicare presenza:
   dopo la scadenza della misura il controllore lo trattava come bloccato.
3. Un rifiuto prima dell'invocazione poteva perdere la prova che nessuna
   chiamata modello era partita. Viceversa, dopo l'invocazione, una ricevuta
   assente veniva correttamente bloccata ma descritta genericamente come budget
   esaurito, nascondendo la causa originale.
4. La console sommava risultati confermati, errori ed elementi saltati nel
   conteggio presentato come progresso, senza separare chiaramente presenza
   del motore, stato del lavoro e aggiornamento dei dati.

Nel lavoro foto osservato i sette errori di budget corrispondevano a consumi
non verificabili, non a un importo documentato oltre soglia. Nessuna ricevuta
storica mancante è stata ricostruita o trasformata arbitrariamente in zero.

## Modifiche implementate

- Domanda in sola lettura, raggruppata per lavoro e fase. Una lunga coda di
  una sola fase non nasconde altre risorse. Dimensionamento entro concorrenza
  ammessa e limiti del gestore centrale; la selezione atomica effettiva resta
  l'unica autorità. Se i gruppi superano il campione, si torna esplicitamente
  al numero massimo centrale, senza dedurre inattività da un campione parziale.
- Nessuna nuova corsia su banca dati quiescente. Il risultato negativo viene
  invalidato sia da scritture locali sia da altri processi. Lavori attivi,
  richieste di controllo, residui terminali e lease ancora attivi conservano
  una corsia di manutenzione.
- Migrazione e controllo completo all'avvio; connessioni indipendenti per
  corsie e artefatti su store già pronto, ancora vincolate a schema e chiavi
  esterne. Nessuna connessione SQLite condivisa fra thread.
- Presenza aggiornata nello stato `feature_disabled`, senza nascondere guasti
  fatali o esecuzioni oltre scadenza. Errori ripetuti nella domanda o nella
  manutenzione degradano il servizio e convergono a un errore esplicito.
- Manutenzione delle autorizzazioni separata dall'esecuzione e mantenuta a
  coda vuota; cadenza condivisa fra i collegamenti dello stesso servizio.
- Prova di zero chiamate soltanto prima dell'ingresso nel trasporto executor;
  ricevute assenti dopo l'ingresso restano sconosciute e bloccanti. Messaggio
  distinto e conservazione del codice strutturato della causa precedente.
- Console con stato motore separato, risultati confermati, errori, elementi
  saltati e attenzione; ultimo risultato da record effettivi, inclusi riusi;
  dettagli tecnici richiudibili; richieste limitate nel tempo, dati obsoleti
  segnalati, protezione da risposte fuori ordine e ripristino dopo ritorno
  nella pagina. Un guasto della sola salute non impedisce di leggere i lavori.
- Messaggi IT/EN attraverso il catalogo canonico, documentazione bilingue e
  aggiornamento ADR 0213. Nessuna modifica alle autorizzazioni owner-scoped.

## Verifiche e revisione

Esito della suite finale nucleo LRE + gestore esecuzioni + JavaScript console:
**466 prove superate in 57,43 secondi**. Suite console HTTP + JavaScript
dell'agente revisore: **14 prove superate** (le due JavaScript sono già comprese
nelle 466 e non vanno contate due volte). Controllo delle differenze pulito.

Le prove iniziali hanno riprodotto i difetti di supervisione prima delle
correzioni. Le prove aggiunte coprono dieci finestre del controllore simulate,
assenza di esecuzioni e transazioni di scrittura a coda vuota, invalidazione
della domanda, residui e lease su lavori bloccati, risorse indipendenti dietro
una coda lunga, manutenzione inattiva, mancata ripetizione delle migrazioni,
rifiuti pre-invocazione e assenza di ricevute dopo invocazione.

La revisione indipendente ha rilevato e fatto correggere: migrazione ripetuta
del deposito artefatti; possibile attesa delle risorse indipendenti a causa
del campionamento delle unità; pulizia delle autorizzazioni saltata a coda
vuota; dipendenza della console dalla disponibilità della sola salute.

I test browser eseguono il JavaScript effettivo in Node e il rendering tramite
API: non sostituiscono una verifica visuale in Chromium, qui non disponibile.
I test delle finestre temporali sono simulati, non una misura continua di
quindici minuti sul carico reale.

## Restante prima della certificazione in esercizio

1. Confrontare l'esportazione del candidato con la release effettivamente
   selezionata, distinguendo normalizzazioni dell'esportazione da modifiche
   funzionali; evitare di perdere eventuali modifiche concorrenti.
2. Pubblicare soltanto attraverso il ciclo chiuso di rilascio, senza editare
   release installate, firme o unità di servizio. Coordinare eventuali turni
   utente prima di ogni riavvio. Mantenere LRE disabilitato nella prima verifica.
3. Verificare salute, stabilità del processo disabilitato oltre le finestre del
   controllore e console autenticata, inclusa la traduzione del catalogo.
4. Eseguire una prova reale di dominio attraverso `/agent/turn`, in un perimetro
   non sensibile e senza ripetere automaticamente il lavoro foto incidentato.
   Misurare inattività e attesa di risorse con archivio rappresentativo, non
   inferire il risultato dalle sole prove sintetiche.
5. Valutare separatamente il recupero delle ricevute storiche mancanti: senza
   prove non sbloccare consumi, non aumentare budget e non dichiarare il lavoro
   completato. La presente modifica non implementa un protocollo retroattivo
   di riconciliazione delle ricevute.

Rischi residui dichiarati: con molti gruppi simultaneamente attivi resta il
limite centrale preesistente; non è ancora disponibile una certificazione
prestazionale del carico reale. La pubblicazione dei documenti web è distinta
dal rilascio del servizio; Cloudflare non è una dipendenza dell'esercizio.

## Rilascio autorizzato e verifiche reali — 16 settembre

Sorgenti funzionali: commit `67dfd970`; impronte di rilascio: `2c7f8baf`.
Il confronto con la release 53 distingue le correzioni LRE dai cambiamenti
già consolidati nel ramo di base: licenza MIT e configurazione dell'autorità
nelle installazioni nuove. Le due guide `interface.html`, generate e non
versionate, sono state rigenerate prima dell'esportazione; coincidono con le
copie dello staging precedente. Quest'ultimo è stato conservato come
`export.before-lre-20260916` insieme al suo documento di consegna.

Identità verificate:

- release: `/var/lib/metnos/executor-birth/releases-v1/00000000000000000054`;
- build: `sha256:b9c3573f2dbf67d0f86cf7163e3bee5c18ac490edc9c8fd2b7a8d809f3697b9f`;
- testa selezionata: `sha256:b3c7f924cff5b43f38b8af0065a7b91327aee26d7915b063ed204567e6ae210c`;
- evidenza firmata: `/var/lib/metnos-admin/rm0008-cycle-evidence-b9c3573f2dbf67d0`;
- radice Python pubblica rivista: `sha256:d81d4e812f928ab56f9a58d49e60f733fee8ca7d191e41d5e31e15a2f86c0102`;
- file di configurazione LRE invariato byte per byte:
  `e4729eeeedfc5ef4b1e11c2f120a4ac2a835d55948a3e3065001dc5a63f2ed5b`.

Prima della pubblicazione sono passate ulteriori **89 prove** su confine della
build, autorità dell'installer e guide dell'interfaccia; non sostituiscono le
478 prove funzionali già riportate. Il passaggio canonico ha verificato
l'assenza di turni attivi, attraversato la catena firmata e riavviato i servizi.

Evidenze private delle sonde:
`/var/lib/metnos-admin/agent-runs/run-_iudwns3` (prima),
`/var/lib/metnos-admin/agent-runs/run-zt0ps7jn` (prima verifica della 54),
`/var/lib/metnos-admin/agent-runs/run-lya7flrq` (verifica successiva).
Fra le 07:10:35 e le 07:14:42 UTC: HTTP, Telegram, browser e lavoratore attivi;
salute `ok=true`, `operational=true`, nessuna modalità di sola manutenzione.
Il lavoratore conserva PID 248750, un thread e zero riavvii; la presenza si
aggiorna, pur dichiarando correttamente `feature_disabled`. Il tempo CPU del
servizio cresce di circa 0,146 secondi in 247 secondi (circa 0,06% di un core).
È una misura del servizio **disabilitato**, non una certificazione del carico
con esecuzione abilitata.

Il lavoro foto `wrk_df6d27b3c6c24d828aca6a2b24915b5e` mantiene stato,
versione, numero di eventi, budget e impronta dei record delle unità:
`64f069dafa2f1adb942d8ea2b809cefcbd51c59d9dad9f35925f4843a3907468`.
Restano 967 cartelle e una scoperta confermate; analisi immagini: 13 unità da
verificare e 954 pendenti. Nessuna pubblicazione del nuovo indice. La nuova
API espone `blocking_reason=budget_accounting_incomplete` e
`last_committed_at=2026-09-16T01:10:12.514825Z`, senza cambiare la causa storica.

La console autenticata contiene i nuovi indicatori e nessuna chiave i18n
irrisolta. Il catalogo di produzione contiene tutti i **139 messaggi
`UI_DURABLE_*` in IT e EN**, completi e senza traduzioni pendenti. Le
spiegazioni estese usano lo stesso catalogo. Altre lingue richiedono la
procedura centrale di preparazione/attivazione e traduzione; non sono state
dichiarate già tradotte né è stata cambiata la lingua dell'istanza per provarle.
Il timer di traduzione risulta attivo e in attesa.

Le quattro pagine modificate sono pubblicate con `deploy.sh --static-only`,
secondo le procedure Cloudflare/Wrangler, senza toccare il catalogo locale:
`https://1d5f5395.mykleos.pages.dev`. Le nuove sezioni IT/EN sono state rilette
dal dominio pubblico `metnos.com`. Nessuna pubblicazione GitHub in questo
intervento.

### Allineamento executor: esito intermedio da non confondere con successo

Il cambio release è riuscito, ma il primo allineamento successivo è terminato
con codice 78 dopo aver ammesso otto componenti. Il filtro di visualizzazione
dell'output ha inoltre fallito sulla riga di rifiuto: l'errore del filtro non
è una prova di successo dell'operazione. Il piano canonico successivo è
leggibile e valido: restano `compress_files` e `open_sites` da ammettere; il
registro di attivazione conserva le otto ammissioni già verificate. Non è
stato cancellato, né sono stati saltati controlli. La ripresa del solo
allineamento tramite il medesimo wrapper canonico, sulla release 54
nuovamente autenticata, senza creare una release successiva, ha confermato
due rifiuti:

- `compress_files`: `property_oracle_failed`, richiesta
  `sha256:93cd7b31660fefdd61dd955ec6aea202ee1aed266c1cf89caf2840d2a1f940aa`;
- `open_sites`: `property_case_unavailable`, richiesta
  `sha256:52c85ce6efcbef43d62a61688a827e9c55db577627daa6c22009a59233a13126`.

Evidenza completa della ripresa:
`/var/lib/metnos-admin/agent-runs/run-0mjrbfi5/reconcile.log`;
diagnosi strutturata: `/var/lib/metnos-admin/agent-runs/run-afy_1x34`.
Il registro conserva `restart_owed=true`: otto ammissioni provate nello store
non equivalgono a otto nuove generazioni già attivate in tutti i processi.
Non è stato forzato un riavvio fuori dalla procedura né eliminato il residuo.
Questi rifiuti riguardano componenti esterne alla modifica funzionale LRE;
per chiudere integralmente la pubblicazione occorre diagnosticare e correggere
i loro controlli di proprietà, non esentarli. Non ripetere l'intero rilascio
né proseguire con tentativi identici senza nuove evidenze.

### Prova reale e limiti finali

Turno reale informativo `9582c54e4584418f`, 42,817 secondi,
`final_kind=answer`, nessun executor eseguito (`steps_summary=[]`). Evidenza:
`/var/lib/metnos-admin/agent-runs/run-3k8t2wp4/turn.json`.
Il percorso HTTP/Tutor funziona, ma **la risposta non supera la verifica
semantica**: descrive l'attesa come possibile approvazione ancora mancante e
fa coincidere risultati confermati con elaborazione terminata/artefatti pronti.
La console invece conta unità effettivamente confermate anche in lavori non
terminati. Non è una prova di accettazione funzionale superata. Serve una
correzione mirata della conoscenza/composizione Tutor e una nuova verifica
della medesima domanda; non cambiare l'oracolo per dichiarare successo.

Ultima verifica HTTP alle 07:19 UTC: istanza operativa, LRE disabilitato,
presenza aggiornata. Alla sonda delle 07:18:19 UTC i quattro servizi hanno
ancora gli stessi PID, senza riavvii; il lavoro foto conserva la stessa
impronta. Le nuove schermate e le correzioni runtime sono installate, ma
**né la pubblicazione complessiva né la ripresa delle elaborazioni sono
certificate**. Il carico con LRE abilitato resta non provato.
