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
  tentativo. Nessun fallimento. XML temporaneo `recovery.xml`.
- Continuazione integrata: vecchi riferimenti conservati, nuovo digest del
  codice, stessa generazione, nessuna ripetizione di chiamate VLM già riuscite,
  pubblicazione e ricerca verificate su corpus sintetico, anche con un file
  non decodificabile. Non è ancora prova di ripresa dell'archivio reale.
- Il lettore rifiuta proprietario, digest o schema diversi, riferimenti
  duplicati/mancanti, risultato non confermato, contenuto corrotto e limiti
  superati. Il calcolo rifiuta tentativi attivi, costo ignoto, limite mancante,
  contabilità non spiegata, budget insufficiente e regressione dell'orologio.

## Stato operativo

Al momento della preparazione il job originale è in attenzione, versione 44,
con 1.306 riferimenti confermati e nessun tentativo attivo. Una nuova
ammissione non è ancora stata eseguita. Rilascio, prova GPS reale, documento
di recupero e avanzamento della continuazione vanno registrati qui dopo
verifica; non dedurli dal solo esito dei test.
