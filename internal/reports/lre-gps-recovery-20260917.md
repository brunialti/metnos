# Riparazione GPS e continuazione con storico conservato — 17/9/2026

## Difetti e correzioni

La release 65 ha incontrato un difetto preesistente: la conversione di un
razionale GPS EXIF con denominatore zero interrompeva una fotografia valida.
Inoltre `run_stdio` perdeva i consumi registrati quando `invoke` sollevava
un'eccezione. La revisione originale ha correttamente bloccato ulteriori
tentativi, invece di interpretare l'assenza dei contatori come zero chiamate.

Il candidato omette le coordinate opzionali non convertibili e conserva la
fotografia. Il trasporto gestito rende le eccezioni applicative fallimenti
strutturati con i consumi già registrati; chiamate iniziate senza resoconto
restano incerte. Interruzioni del processo non vengono dichiarate riuscite.

## Recupero esplicito

Il contratto congelato non può essere riscritto dopo il cambio dell'executor.
Una continuazione viene ammessa normalmente con i nuovi contratti, mantenendo
la generazione privata originale. Il lettore interno generale
`committed_entries` accetta riferimenti espliciti a risultati confermati dello
stesso proprietario, controlla schema/impronta e ricostruisce le sole voci.
Non cambia risultati, effetti, tentativi o consumi precedenti. Questa è una
lettura di dati storici, non un'attestazione retroattiva del nuovo codice.

Il dominio foto prepara un piano che usa le classificazioni cartella
confermate, riesamina i gruppi con il nuovo executor e verifica i checkpoint
esistenti; un modello non viene richiamato per una foto con checkpoint valido.
Il nucleo LRE non riconosce nomi di job o modelli per scegliere la strategia.

`remaining_recovery_budgets` sottrae consumi noti e intero massimo contrattuale
dei tentativi senza rendiconto. Sono ammissibili solo limiti finiti di modelli
con costo zero. La riserva non viene registrata come misura effettiva: lo
storico originale mantiene `usage_unknown`. Unità, byte, artefatti e tempo
consumati riducono anch'essi il budget; un tentativo per ogni nuova unità.
Il predecessore deve essere quiescente e viene annullato con il normale
controllo prima di accodare la continuazione, conservando tutti i risultati.

## Verifiche del candidato

- Regressioni prima delle correzioni: cinque fallimenti riprodotti, comprendenti
  razionali Pillow `0/0`, `1/0` e perdita dei consumi su eccezione.
- Prima serie: 267 test passati per GPS, confine subprocesso e contabilità.
- Insieme LRE e quattro moduli executor: **881 passati, 8 saltati**; i salti
  includono quattro combinazioni non pertinenti alla continuazione a singolo
  tentativo. Nessun fallimento. XML `recovery.xml` nell'archivio privato sotto.
- Continuazione integrata: vecchi riferimenti conservati, nuovo digest del
  codice, stessa generazione, nessuna ripetizione di chiamate VLM già riuscite,
  pubblicazione e ricerca verificate su corpus sintetico, anche con un file
  non decodificabile. Non è ancora prova di ripresa dell'archivio reale.
- Il lettore rifiuta proprietario, digest o schema diversi, riferimenti
  duplicati/mancanti, risultato non confermato, contenuto corrotto e limiti
  superati. Il calcolo rifiuta tentativi attivi, costo ignoto, limite mancante,
  contabilità non spiegata, budget insufficiente e regressione dell'orologio.

## Stato prima dell'installazione (storico)

Al momento della preparazione il job originale è in attenzione, versione 44,
con 1.306 riferimenti confermati e nessun tentativo attivo. Una nuova
ammissione non è ancora stata eseguita. Rilascio, prova GPS reale, documento
di recupero e avanzamento della continuazione vanno registrati qui dopo
verifica; non dedurli dal solo esito dei test.

## Risultati reali e correzione del limite di impronta

Release 66 installata tramite `run-mkm08rgv`, build
`sha256:295b52dabfe3cbc0b88c0d168fb8cd09a5116278c1902bdd47792aeaff9a3f1d`.
Executor 0.5.1 pubblicato attraverso Birth; HTTP, worker e Telegram verificati.
La prova sullo snapshot originale (`run-1vb3gu7f`) passa: immagine decodificabile,
GPS omesso, nessuna chiamata al modello. Tutor IT/EN verificato con 3.652 unità
(`run-2bv7_zq_`); turno HTTP `get_now` riuscito (`run-cm8_oe8j`). Guide LRE
pubblicate IT/EN: `https://d105bf72.mykleos.pages.dev`.

La prova reale ha validato 967 risultati cartella, 30.942 sorgenti e 10.914
checkpoint. Conservati i 1.306 riferimenti precedenti; riserva prudenziale di
169.885.696 token per il tentativo senza resoconto, sottratta dal budget insieme
a 12.521.184 token misurati. Il predecessore è stato annullato tramite controllo
ordinario, senza cancellare risultati o riscrivere consumi: versione 46,
`usage_unknown=true`. L'assistente avrebbe dovuto chiarire il cambio di stato
visibile prima di eseguirlo; Roberto ha giustamente segnalato il timore di perdere
il lavoro. La prima procedura si fermava aspettandosi cancellazione sincrona:
il worker ha concluso la transizione; la ripresa usa la stessa ammissione.

Continuazione già ammessa:
`wrk_c8bd069c0d82476d9f252a79c622d3a3`, revisione
`rev_707ccf0f116c4d26af9d4d1b45c82088`, piano
`sha256:07b5fcc405a3eb1915f914de509cee9ce0604cadd29abfcb0d852a2605207146`.
Alle 21:24 CEST è andata in attenzione, versione 5, prima delle analisi:
`_semantic_arguments_digest` applicava il limite di 64 KiB degli eventi a input
letterali già ammessi fino a 1 MiB. Il riferimento a 967 risultati supera il
limite; il piccolo corpus di collaudo non lo riproduceva. Correzione generale:
usare il limite già esistente degli snapshot per calcolare l'impronta; il
contenuto non viene salvato nell'evento. Nuova regressione con 967 riferimenti.
La funzione hash e gli input restano identici: nessun cambio dei contratti
congelati. Installare la correzione e riprovare questa stessa continuazione;
non creare né annullare un altro job.

## Release 67 e ripresa dello stesso job

Il ciclo ufficiale `run-c6omqkrm` ha installato la release 67, build
`sha256:6dddac7b5aedb354d9fa464d4303c7d9d56f17f8b8d22ad55a5c427c28fd6a0c`.
HTTP, worker e Telegram attivi dalla 21:31:56–57 CEST, nessun riavvio inatteso.
I cinque contratti congelati della continuazione sono identici e l'impronta
degli input reali con 967 riferimenti riesce (`run-9uuqtddx`); CPU/VLM 4/4,
F5 `LEGACY`, configurazione invariata. Il controllo aggiuntivo conta
**159 test passati, 4 saltati**, compresa la regressione sul grande input.

Riprova ordinaria della **stessa continuazione**, `run-tk7d2tm8`; nessuna nuova
ammissione o cancellazione. Alle 21:38:22 CEST (`run-itiuhpjz`) è `running`,
versione 7, con il lettore cartelle e tre gruppi di analisi confermati, quattro
analisi attive. Il gruppo del precedente errore GPS è fra quelli attivi.
I primi tre gruppi riutilizzano i checkpoint: zero nuovi token rendicontati
per quelle conferme. Tutti i 1.306 risultati originali sono ancora identici;
il predecessore resta `cancelled`, versione 46, con incertezza contabile
storica conservata. L'errore delle 21:24 resta visibile nello storico dei
tentativi, ma non è un nuovo blocco dopo la riprova.

Tutor verificato sulla 67: 3.652 unità, query IT/EN con 16/12 risultati dalla
guida prevista (`run-7mwy25wb`). Turno reale HTTP riuscito, `get_now`, ID
`9749639fb9ef470c`, 5.773 ms (`run-2joem3iz`). Le guide pubbliche sono quelle
già distribuite per la 66: nessuna nuova pubblicazione di contenuto invariato.

### Verifica del gruppo prima bloccato

Alle **21:42:01 CEST**, `run-4_gctxbf`, la continuazione è ancora `running`,
v7, con **56 gruppi confermati e quattro attivi**. Il gruppo che falliva è
ora **confermato**, risultato `res_ef561464159b4e80af5160af0d4e15da`.
La verifica `run-4k5lmj55` trova tutti i 32 checkpoint validi del gruppo; la
quinta foto ha il nuovo checkpoint delle 21:38:30, GPS omesso, immagine valida.
Questa sonda è in sola lettura e non chiama modelli. La contabilità del job
registra 24.262 token input e 5.969 output, costo zero, `usage_unknown=false`.
Nessun nuovo tentativo fallito dopo la riprova; l'errore delle 21:24 resta
correttamente nello storico. Tutti i 1.306 risultati originali restano identici.
Il recupero e l'avanzamento sono dimostrati; l'archivio intero resta in corso.

Archivio privato stabile:
`/var/lib/metnos-admin/lre-repair-20260917-1bae2964`. Contiene la base di
confronto, il piano di recupero con riferimenti, le ricevute di ammissione e
riprova, le tre relazioni XML dei test e le prove finali. Le singole procedure
e i log completi restano inoltre negli archivi `agent-runs` citati. Nessuna
informazione privata delle fotografie viene inserita nella documentazione.
