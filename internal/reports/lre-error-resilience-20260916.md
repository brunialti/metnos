# LRE — resilienza degli errori, tabella e collegamento alla console

16 settembre 2026. Stato: release 63 installata, console e Tutor verificati in
esercizio. Fonti isolate in `codex/lre-backend-release`, commit `4f64b26b`.
Nessun nuovo lavoro di indicizzazione è stato avviato dall'agente: Roberto
ha scelto di ripartire personalmente.

## Causa verificata

Il job `wrk_128699f9502c40b596e7d6cd0c57627d` è fallito alle 18:55:14 UTC.
Aveva confermato scoperta, 967 batch di preparazione e 18 di analisi: 986 batch
complessivi. Un tentativo di analisi ha riportato
`execution.runner_failed / executor_permanent / retry=never`, con causa
`image_description_unavailable` e classe originaria `execution_failed`.
La politica rigorosa ha quindi annullato 948 batch ancora in attesa.

Il codice del dominio appiattiva descrizioni mancanti, vuote, non valide e
troncate; il ponte generale trattava anche una causa opaca come permanente.
La causa precisa del modello non era conservata: non è dimostrato se quel
tentativo sia stato troncato, sia scaduto o abbia ricevuto altra risposta errata.
Il motore era pronto, senza riavvii: non emerge un conflitto di processi come
causa dimostrata di questo arresto. Evidenze private: `run-m22ngktu`,
`run-nm3prs13`.

## Correzioni

- Classi generali distinte e politica in ADR 0213: recuperabili entro il limite,
  poi attenzione con coda conservata; ignoti subito da verificare, senza retry
  ciechi; errori permanenti espliciti e contratti invalidi restano rigorosi.
- Ricevute coerenti con la decisione manuale; recupero lease senza fallimento
  definitivo per il solo esaurimento dei tentativi. Invariati limiti, autorità,
  consumi, controllo degli effetti e contratti congelati.
- Il dominio foto conserva cinque cause chiuse del modello (ADR 0117), distinte
  dai due esiti negativi di decodifica. Nessuna finta foto indicizzata.
- Lo storico può mostrare causa approvata e numero di tentativi; non espone
  testo libero o percorsi. Questi conteggi non diventano elementi non indicizzati.
- Tabella IT/EN compatta con valore e significato, fase x/y e stima della sola
  fase. Distinti batch completati/previsione nella fase e totale noto generale.
- Collegamenti alle pagine interne nella chat HTTP, anche nei messaggi salvati;
  destinazioni tratte dal registro UI, senza origine inventata o HTML arbitrario.

## Verifiche locali

- Cluster LRE e nuovi esiti foto: 750 superati, 4 esclusi dalle condizioni della
  suite; include sei casi di indicizzazione sintetica con ripresa da database,
  zero/uno/tre errori del modello e foto leggibili/non leggibili.
- Cluster fotografico/VLM: 199 superati, 10 esclusi; il controllo i18n separato
  ha confermato il falso negativo preesistente della selezione per timestamp.
  Corretto solo il test affinché invochi il vero allineamento, in sola lettura,
  rispettando `source_lang`; nessuna traduzione estranea alterata per farlo passare.
- Console HTTP/Chromium IT/EN, sicurezza dei link e i18n: 69 superati e 13
  sottocasi. Prove isolate; nessun collegamento a dati di produzione.
- Verifica finale integrata LRE/foto/VLM: 947 superati, 14 esclusi dalle
  condizioni della suite (58,69 s). Documentazione, console, link e ciclo di
  rilascio: 230 superati e 13 sottocasi. Cataloghi i18n e contratto delle guide
  d'installazione: altri 69 superati. Le prove Chromium comprendono il dettaglio
  eliminato (404), errore temporaneo (503), italiano e inglese.
- I test E2E usano modelli sintetici: non certificano copertura o velocità delle
  30.942 sorgenti dell'archivio reale.

## Cancellazioni autorizzate

Il vecchio job fallito è stato eliminato con transazione puntuale su id,
proprietario, revisione, stato e versione, dopo verifica di zero tentativi
attivi e messaggi consegnati. Eliminati job e righe dipendenti del suo storico;
nessun file o servizio toccato. Ricevuta `run-0_26oc0x`, 19:44:42 UTC: API vuota,
dettaglio 404, integrità SQLite verificata, LRE pronto. Nessuna copia di recupero
creata; la cancellazione dello storico non è reversibile.

Roberto ha avviato alle 19:45:53 UTC il job
`wrk_244c2ea1dc1744e2a6d2393a499d2300`, ancora sulla release 62. Informato del
contratto precedente, ha autorizzato annullamento, cancellazione e pubblicazione.
Annullamento tramite API con versione attesa: `run-7yv48aot`, 19:50:07 UTC.
Attesa cooperativa del batch già in corso; nessuna interruzione forzata.
Verifica `run-ghvxg6hm`: cancellato, versione 6, un batch di scoperta confermato,
zero tentativi attivi. Nessuna analisi foto avviata e nessun indice pubblicato.
Eliminazione selettiva conclusa alle 19:57:25 UTC, ricevuta `run-9dzwvwmi`:
API vuota, dettaglio 404, integrità verificata, LRE pronto. Zero file eliminati,
nessuna copia conservata e nessun servizio riavviato per le due pulizie.

La console precedente conservava il dettaglio selezionato anche quando l'API
rispondeva 404 dopo una cancellazione. Ora distingue l'assenza confermata da
errore temporaneo: chiude dettaglio, comandi e stream e aggiorna l'elenco.
Gli errori di rete continuano a conservare l'ultima lettura, marcandola scaduta.
Verificato con Node e Chromium in entrambe le lingue.

## Rilascio e collaudo di esercizio

Il resoconto seguente distingue preparazione, attraversamento e attivazione:
la semplice preparazione del candidato non equivale alla pubblicazione.

Ricontrollo delle 20:06 UTC (`run-6b4q44xi`): i due job eliminati sono assenti
sia dall'API sia dal database. È però presente un nuovo lavoro,
`wrk_1af3d8788cdb44628b4abaae657d08cb`, avviato alle 19:59:06 UTC (21:59 locali),
in scoperta sulla release 62 con un tentativo attivo. Non è stato avviato,
annullato o modificato dall'agente. Richiesta conferma separata per annullare e
cancellare anche questo nuovo lavoro prima del rilascio; l'autorizzazione al
precedente job non viene estesa automaticamente a quello nuovo. Nessun
riavvio o attraversamento di release eseguito in presenza del nuovo lavoro.

Roberto ha poi autorizzato anche questo annullamento e richiesto la
pubblicazione immediata dell'ultima versione. Richiesta cooperativa accettata
alle 20:09:33 UTC (`run-im9ex4rl`), versione 5. Il rilascio è stato preparato
in ambiente isolato: 1.775 file, censimento
`b6010ffcfbdbcfc5cc0fed09578741ce7b63f473f70b32019f83f46ab65f1f70`.
Nessuna deriva nei contratti builtin generati; le baseline delle descrizioni
store sono state provisionate solo nel catalogo temporaneo del preparatore.
Radici riviste: privata
`sha256:3cfac4d7c9ea4731a8f572db2936d4e179244297275e0c1beae82e90ee301896`,
pubblica
`sha256:40188140b09ceda4b3e4dbf2322380e432e3d15d49d7aed68fa156fa6629cf48`.
Verifiche successive di confine, preflight e ciclo di rilascio: 494 superate.

### Pubblicazione completata

- Annullamento del job `wrk_1af3d8788cdb44628b4abaae657d08cb` concluso
  cooperativamente alle 20:10:19 UTC: 969 batch confermati, zero tentativi
  attivi. Cancellazione definitiva `run-72mho4nz`, 20:12:37 UTC: zero file
  cancellati, nessuna copia creata, integrità SQLite e metadati protetti invariati.
- Ciclo canonico concluso con codice 0: release **63**, stato
  `PREFLIGHT_VERIFIED`, build
  `sha256:c15a07e6767e731b6b91f201d9cdd75319ec703f796ab4fbd9dfb1fbc46f3bcc`,
  head
  `sha256:97649f3ac9813f4c15a2b0f0681cc2e84a4ae3f322941b960851df21213092e3`.
- Un solo executor modificato fra 107 sorgenti: `create_images_indices`,
  risultato `store_verified`, generazione
  `sha256:5ce53f8a27189387eb690b1cebed1ea41ce300517a96cb838c34439117e9ba61`.
  Codice candidato
  `sha256:aa2f8617b3a2eb2d6792b3306624c0366d5102c768aaa23fdadb0714a7838eeb`.
  Riavvio coordinato completato; prontezza e parità del catalogo 123/123.
- Registro completo privato:
  `/tmp/metnos-lre-release.SSgcNeOH/release-cross-1789589575330124566.log`.
- Durante la preparazione, alle 20:12:56 UTC, è entrato un altro job,
  `wrk_9c1e165f97284ac69b52b2ecf914cb6b`, ancora con il codice precedente
  `sha256:03d778f04c1a93968ca0755247dd98b887c30a2596bee904c41c11e77d6c750d`.
  Alla transizione risultava `needs_attention` per contabilità incompleta,
  senza risultati confermati o tentativi attivi. Verifica del contratto
  congelato `run-0xdwssxz`; incluso nella pulizia dei vecchi job richiesta
  dall'utente: annullamento versione 7 (`run-2ijxjpby`), eliminazione
  `run-jlj90j3m`, 20:23:50 UTC. Zero file cancellati, zero copie di recupero,
  API vuota e dettaglio 404. Non sono stati riscritti contratti o consumi.
  La finestra di ammissione durante un rilascio resta il limite operativo già
  documentato per la release 62: questo rilascio non la rende atomica.
- Console reale `run-qbcscofh`: HTTP 200, nuova tabella compatta, etichette
  italiane risolte e gestione del dettaglio 404 presenti. LRE abilitato e
  pronto, interruttore privato invariato, nessun job residuo.
- Tutor ricompilato dall'avvio normale: 3.621 unità e altrettanti vettori,
  tutti i sette tipi di fonte, 109 unità LRE; catalogo
  `sha256:388b7660495573f46c1d9a93160485fdfb0aa5aadd9dc9c5aff6aa29d43b3eda`.
  Turno reale `18cd730c07184830` (`run-w8a5v9pb`): distingue correttamente
  fase corrente, totale noto e stima della sola fase; nessuna azione eseguita.
- Guide pubbliche IT/EN pubblicate con le procedure Cloudflare/Wrangler in
  modalità esclusivamente statica: 99 HTML validati, quattro file aggiornati,
  distribuzione `https://0568de02.mykleos.pages.dev`. Le due guide LRE su
  `metnos.com` rilette e confrontate byte per byte con le sorgenti pubblicate.
  Nessun rapporto interno inviato.
- Una prima sonda in linguaggio naturale, con nome dello strumento e più
  negazioni (`c7a511dd0fe0429d`, `run-hwflvu25`), ha ricevuto un rifiuto di
  capacità senza eseguire strumenti. Non viene conteggiata come collaudo
  funzionale superato; non ha creato job o modificato indici.
- Sonda ordinaria separata «Mostrami lo stato degli indici fotografici
  esistenti.»: turno `47c1855d9a5d450b`, `run-9gmxmzet`, 4.575 ms,
  `get_images_indices` eseguito con `ok=true`; nessun job prima o dopo.
  La risposta sintetica della chat è poco informativa (elenco con trattino
  vuoto): il collaudo prova l'esecuzione in lettura, non certifica quel riepilogo
  come buona presentazione né risolve il rifiuto della prima formulazione.
- Audit finale dei processi: nessun nostro processo orfano a consumo elevato.
  Non sono programmati ulteriori riavvii. Roberto può avviare il nuovo test;
  i nuovi job ammessi dopo la pubblicazione non rientrano nella pulizia.

## Limite della certificazione

Il rilascio, l'attivazione dell'executor, l'interfaccia e la prontezza sono
verificati; retry e conservazione dei batch hanno superato i test isolati.
Per scelta dell'utente non è stata avviata un'indicizzazione di collaudo in
produzione. La copertura delle oltre 30.000 foto va quindi verificata sul
nuovo lavoro reale, non dedotta dalla riuscita del rilascio.
