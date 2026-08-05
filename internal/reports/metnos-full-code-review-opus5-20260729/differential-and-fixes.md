# Confronto differenziale e decisioni sulle proposte

Data: 2026-07-29  
Revisori confrontati: Claude Code Opus 5 (indipendente) e Codex

## Decisione per rilievo esterno

| Rilievo | Decisione | Motivazione |
|---|---|---|
| D1 — gate Tutor fail-open | Accettato | Ricostruzione confermata nel codice; causa principale dell'incidente live. |
| D2 — nessuna deadline complessiva | Accettato | Scheduler, provider e confine HTTP non condividono un budget residuo. |
| D3 — LAN anonima risolta come host | Accettato | Il concatenamento ruolo→attore→utente è confermato e viola l'isolamento per utente. |
| D4 — probe non cancellate | Accettato con estensione | `Future.cancel()` non ferma il runner; occorre propagare la deadline anche ai runner e al registro servizi. |
| D5 — loader ricompilano | Accettato | Il percorso caldo esegue hashing e lock esclusivi; compilazione e ammissione vanno separate. |
| D6 — `after_rank=1` | Accettato | Il gate non verifica la proprietà dichiarata. |
| D7 — trust store condiviso | Accettato come difesa in profondità | Il pin della chiave separa i domini, ma non è autorizzazione forte contro lo stesso utente Unix. |
| D8 — descrizione servizi persa | Accettato in parte | La F2 statica contiene già lo scopo, quindi non spiega da sola la risposta fallita; la capsule live va comunque completata. |
| D9 — sticky senza device esplicito | Respinto come difetto | È comportamento voluto da ADR 0181. Disabilitarlo sarebbe una regressione; la causa è D1. |
| D10 — handoff prima dell'autonomia | Accettato | Non è privilege escalation, ma persiste stato/proposta prima del gate pertinente. |
| D11 — retry dello stesso classificatore | Accettato | Può raddoppiare l'attesa senza creare una sorgente di recupero indipendente. |
| D12 — classe eccezione nella capsule | Accettato | Il prompt deve ricevere un reason code chiuso, non dettagli implementativi. |
| D13 — catalogo attribuito al composer | Accettato | La classificazione del gap è diagnosticamente errata. |
| D14 — risposta completa in telemetria | Respinto come difetto | `final_message` serve anche alla cronologia; la minimizzazione riguarda la query. Si correggono commento e permessi, non si elimina implicitamente la risposta. |

## Decisione per rischio esterno

| Rischio | Decisione | Azione |
|---|---|---|
| R1 — proofreader IT/EN | Accettato | Condizioni di copertura localizzate e nessun fallback euristico inglese per nuove lingue. |
| R2 — lessico Sites | Accettato | Separare il lessico Tutor dal dominio browser. |
| R3 — feedback negativo solo hash | Accettato come rischio | Progettare la simmetria semantica senza cancellazioni eccessive. |
| R4 — selezione senza invariante sul primo | Accettato | Aggiungere invariante e prova mirata. |
| R5 — cache probe senza versione | Accettato | Includere `source_version` nella chiave. |
| R6 — digest fissato all'import | Accettato | Calcolarlo nel ciclo di compilazione/ammissione appropriato. |
| R7 — identity guard e timeout | Accettato | Acquisizioni tracciate e rilascio inverso su ogni uscita. |
| R8 — budget servizi incoerente | Accettato | Rendere budget e runner coerenti sotto la stessa deadline. |

## Valutazione delle patch proposte da Opus 5

1. **Modalità e disponibilità separate — accettata.** Sarà introdotto un risultato tipizzato. Un `UNKNOWN` semantico può declinare; un errore tecnico produce una risposta Tutor localizzata e conclusiva.
2. **Deadline unica — accettata e ampliata.** Non basta un timeout HTTP: il budget residuo deve raggiungere scheduler, provider, probe e composer, con rilascio delle risorse.
3. **Fiducia LAN esplicita — accettata.** Default chiuso; una richiesta anonima non viene mai associata automaticamente all'host.
4. **Compilazione/ammissione catalogo separate — accettata.** Il catalogo si compila in installazione, deploy o aggiornamento; la richiesta legge un artefatto firmato ammesso.
5. **Controfattuale reale — accettata.** Produzione e certificazione useranno la stessa funzione di punteggio.
6. **Disattivare sticky senza menzione device — respinta.** Contraddice ADR 0181 e corregge il livello sbagliato.
7. **Descrizione e audience nella probe — accettata con fonte canonica.** La descrizione sarà localizzata; l'audience non verrà duplicata in costanti divergenti.
8. **Chiave catalogo nominata — accettata come separazione di dominio.** Non verrà descritta come barriera contro la compromissione dell'account di servizio.

## Correzioni aggiunte da Codex

- invariante centrale `0700`/`0600` per dati, stato, turni e richieste di posizione, applicata dall'installer e riparata idempotentemente all'avvio;
- correzione dei timeout fittizi in job e generatore descrizioni mediante propagazione al provider;
- audit i18n delle superfici amministrative rimaste fuori dal catalogo;
- rimozione progressiva della mappa hardcoded executor→campo a favore dei manifest;
- verifica separata della concorrenza della connessione i18n globale.

## Ordine di applicazione

1. confine Tutor e deadline reale;
2. autenticazione LAN e permessi dei dati per utente;
3. catalogo nel percorso caldo, probe e controfattuale;
4. proofreader/i18n e classificazione dei gap;
5. test mirati, suite completa, compilazione catalogo, certificazioni F3/F4, deploy e prova live;
6. aggiornamento documentale e chiusura RM-0003 soltanto se la prova live è positiva.

Le correzioni che cambiano un contratto saranno documentate prima della chiusura. RM-0003 resta aperta finché Tutor non dimostra dal servizio installato di rispondere alle query informative che prima uscivano verso il planner.
