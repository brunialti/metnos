# LRE — stima nascosta dal confronto fra orologi

16 settembre 2026. Correzione locale collaudata, **non pubblicata**.
Produzione ancora sulla release 63; nessun servizio riavviato e nessun job,
indice o contratto modificato durante questa indagine.

## Riscontro

La schermata fornita mostra dati appena verificati, fase 3/5 e cinque batch
confermati su 967, ma la stima è nascosta con il motivo `data_stale`.
Le risposte di elenco e dettaglio espongono invece una stima valida.

Lettura privata `run-rmdko1ew`, 21:06:25 UTC: il job
`wrk_4121cc6258c447759e94ccc6f8090e5c` è in esecuzione, con sette batch di analisi
confermati e l'ottavo in corso. I primi sette tentativi sono riusciti al primo
tentativo. La stima della fase è `2026-09-19T10:01:49.978474Z`, uguale in elenco
e dettaglio. È una previsione iniziale variabile, non una scadenza garantita né
la stima dell'intero lavoro. Lo stato dimostra avanzamento, non uno stallo.

## Difetto e correzione

Il browser sottraeva `progress.observed_at` del server da `Date.now()` locale:
uno scarto negativo anche di un secondo produceva `data_stale`; oltre trenta
secondi in avanti produceva lo stesso risultato. Il confronto della scadenza
e il controllo periodico dipendevano anch'essi dall'orologio del PC.
La prova di regressione ha fallito sul codice precedente con `data_stale`
al posto di una stima valida. Non è stato misurato direttamente l'orologio del
PC dell'utente: il difetto è riprodotto e compatibile con la schermata, senza
attribuire al PC uno scarto specifico non osservato.

Ora ogni risposta senza cache riceve un riferimento `performance.now()`
all'inizio della richiesta. L'età include prudenzialmente il trasporto e non
dipende dalla sincronizzazione degli orologi. La scadenza viene confrontata
con l'istante del server più il tempo trascorso; la verifica periodica usa lo
stesso orologio monotono. Il riferimento è soltanto locale: non è serializzato
e non altera API o firma dei dati usata per evitare ridisegni inutili.

Restano attivi i controlli su dati realmente scaduti, previsione superata,
motore non pronto e motivi dichiarati dal server. Nessuna nuova stringa:
messaggi e formattazione restano nel percorso i18n esistente.

## Collaudi

- Console Node e calcolo delle stime lato server: 35 prove superate.
- Chromium isolato, italiano e inglese: 2 prove superate, con schermate ampie
  e strette, elenco e dettaglio, errori di connessione e ripristino.
- API LRE e controllo amministrativo: 15 prove superate.
- Casi aggiunti: scarto del PC da meno a più 48 ore; scarto negativo di un
  secondo; veri trenta secondi di inattività; previsione superata; dati privi
  di osservazione; nove secondi di trasporto; rinnovo della lettura senza
  cambiare contatori o ricreare il dettaglio; salti dell'orologio durante
  la verifica periodica. `git diff --check` superato.

Tutti i test usano dati isolati sotto `/tmp/metnos-lre-eta-audit.djnITQ`;
i browser ricevono soltanto risposte sintetiche. Queste prove non sono un
collaudo della correzione installata né certificano la copertura dell'archivio.

## Pubblicazione ancora da eseguire

Il ciclo canonico della release chiusa ferma e riavvia i servizi.
Non è stato attraversato mentre è attivo il lavoro dell'utente; non sono
state modificate copie firmate o introdotte scorciatoie di pubblicazione.
Occorre concordare una finestra sicura prima del rilascio, poi verificare la
console installata e completare le prove reali previste dal progetto.
