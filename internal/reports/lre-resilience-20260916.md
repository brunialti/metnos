# LRE — correzioni prudenti e chiarezza della console

Data: 16 settembre 2026. Stato: candidato isolato, **non pubblicato e non
certificato in produzione**. Nessun riavvio, riabilitazione, ripetizione di
lavori, aumento di budget o modifica dello storico durante questo sviluppo.

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
