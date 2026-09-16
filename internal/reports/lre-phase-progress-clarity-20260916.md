# LRE — batch e avanzamento della fase

Data: 16 settembre 2026. Stato: candidato locale, **non installato in produzione**.

## Mandato e limite operativo

Rendere comprensibili percentuale, conteggi e stima; usare «batch», spiegato
senza riferimenti a un dominio specifico; mostrare «Fase x/y». Roberto ha
chiesto esplicitamente di non bloccare il job in corso. Nessun servizio,
contratto, configurazione LRE, job o indice di produzione è stato modificato.
Non effettuare il rilascio canonico mentre questo lavoro è attivo.

## Diagnosi verificata

Il job `wrk_128699f9502c40b596e7d6cd0c57627d` ha censito 30.942 sorgenti,
distribuite in 967 batch di analisi, ciascuno con al massimo 32 elementi.
Durante l'analisi, il totale 1.935 somma una scansione iniziale, 967 batch di
preparazione e 967 batch di analisi. Le fasi successive di assemblaggio e
pubblicazione non sono ancora materializzate: non è il totale definitivo.

Alle 18:18 UTC l'analisi aveva completato 10/967 batch, mentre il conteggio
aggregato era 978/1.935. Quindi circa 1% dell'analisi, non 50% del risultato
finale o del tempo. Alle 18:30 UTC i batch di analisi erano 12, con zero errori
di dominio e zero errori di tentativo registrati. Le foto non sono ancora
pubblicate nell'indice: il conteggio dei batch non certifica la copertura finale.

La stima della fase era presente nelle letture API correnti; l'assenza vista
dall'utente non è stata riprodotta come assenza persistente nel motore.
La UI precedente nascondeva il motivo nell'elenco e non aggiornava il motivo
quando la stima diventava indisponibile per dati vecchi/disconnessione.
Non sono stati allentati i criteri della stima o introdotte estrapolazioni nuove.

## Modifiche del candidato

- `progress_many`: aggiunge a `current_phase` `number`, `count`,
  `committed_units`, `total_units`, `known_units_percent`, riusando la stessa
  query aggregata e il medesimo isolamento per proprietario/revisione.
- Numerazione secondo l'ordine del piano ammesso, esclusa la fase tecnica
  `inventory`. Nessuna fase unica inventata quando ne sono attive più di una;
  nessun totale presentato come definitivo durante la materializzazione.
- Percentuale e barra principali si riferiscono solo alla fase attiva. La
  percentuale aggregata resta nei dettagli tecnici. Il nome specifico del
  lavoro rimane separato dalla definizione generale di batch.
- Motivo di `n.a.` visibile anche nell'elenco; motivo e valore vengono
  aggiornati insieme, inclusi errore di connessione, dati scaduti, motore
  indisponibile e previsione superata. Nessuna stima dell'intero job multifase.
- Layout leggibile anche nella colonna stretta e su telefono; etichette e valori
  separati esplicitamente. Date e numeri seguono la lingua dell'interfaccia;
  la stima non suggerisce una precisione al secondo.
- Dizionario IT/EN completo; «blocco/blocks» sostituito con «batch/batches»
  nelle diciture LRE. Conservati gli hash storici delle traduzioni distribuite
  per aggiornarle senza sovrascrivere le personalizzazioni utente.

Nessuna modifica a schema persistente, scheduler, contabilizzazione, recovery,
concorrenza effettiva o capacità specifica di indicizzazione.

## Evidenze e verifiche

- Test progressi e browser focali: 36 superati prima dell'ultimo raffinamento
  linguistico; suite HTTP/UI finale: 20 superati, inclusi Chromium IT/EN,
  desktop/mobile, dati sintetici e simulazione esplicita di disconnessione.
- Cluster LRE e cataloghi: 713 test superati, 4 saltati dalle condizioni
  preesistenti della suite, 1.168 sottocasi superati; circa 107 secondi.
- Documentazione/Tutor: ricompilazione delle unità delle guide e controlli
  semantici superati. Il catalogo Tutor di esercizio è intenzionalmente intatto.
- Integrità/simmetria del seed confermate; una guardia globale preesistente
  fallisce per `ERR_FROM_STEP_IDENTITIES_UNRESOLVED`: a parità di timestamp il
  test sceglie IT come sorgente, mentre la coppia registrata ha sorgente EN.
  Il confronto con il seed di HEAD dimostra che entrambe le righe sono identiche
  prima/dopo; non appartengono a questa modifica. Non dichiarare quel controllo
  globale verde e non alterare traduzioni estranee per nasconderlo.
- Nessuna riga i18n fuori dal prefisso `UI_DURABLE_` modificata.
- Le prove HTTP richiedono socket locali disponibili: il sandbox impediva la
  chiusura di una prova. Quel solo processo di test è stato terminato; la suite
  è stata ripetuta fuori sandbox in un'installazione temporanea, senza dati o
  credenziali di produzione. Nessun riavvio applicato a Metnos.
- Guide pubbliche IT/EN validate: 99 documenti; pubblicati solo due file statici
  con `deploy.sh --static-only`, deployment `2a04fa8a.mykleos.pages.dev`.
  La guida indica esplicitamente che la nuova UI è ancora da installare.

Osservazioni di produzione, tutte in sola lettura:

- `run-8e2lerba`, 18:07 UTC: 8 batch di analisi;
- `run-0iq0xh_u`, 18:18 UTC: 10 batch di analisi;
- `run-qgznigzp`, 18:30 UTC: 12 batch di analisi, release 62 invariata, HTTP
  operativo, worker pronto, PID invariati e zero riavvii dei servizi.

I risultati completi delle prove di questo intervento sono in
`/tmp/metnos-lre-progress-clarity.t914AaYk/`; le osservazioni amministrative in
`/var/lib/metnos-admin/agent-runs/`. Non sono copie recuperabili dei vecchi job.

## Prima del futuro rilascio

1. Accertare esplicitamente che non vi siano job/turni attivi: il solo
   `stack.quiescent` HTTP non include il lavoro del worker durevole.
2. Risolvere o classificare formalmente la guardia i18n preesistente prima
   della certificazione completa del seed.
3. Ricostruire il catalogo Tutor completo e verificare una domanda reale su
   batch e fase; la prova di segmentazione delle guide non equivale al collaudo
   del modello in esercizio.
4. Usare il rilascio canonico firmato, senza patch manuali alla release 62.
   Servono codice HTTP, proiezione API e seed insieme. Non riscrivere il piano
   immutabile del lavoro già ammesso.
5. Verificare console e un turno reale del dominio dopo l'installazione;
   rimuovere dalle guide l'avviso «in preparazione» soltanto allora.
