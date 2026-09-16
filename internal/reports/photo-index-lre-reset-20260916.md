# Azzeramento indice foto e storico LRE — 16 settembre 2026

Richiesta esplicita di Roberto: cancellare l'indice foto e anche tutto lo
storico LRE, per effettuare personalmente una nuova ricerca e verificare la
ricostruzione. Nessuna nuova indicizzazione avviata dall'agente.

## Esito verificato alle 09:27:05 UTC

- Metnos operativo, stack pronto e inattivo; LRE abilitato e pronto.
- Indice immagini del servizio vuoto; vecchia copia dell'indice dell'archivio
  nel profilo Roberto rimossa dalla posizione attiva.
- Zero lavori, revisioni, unità, tentativi, risultati, eventi, notifiche in
  uscita, artefatti, pubblicazioni, comandi e risoluzioni di attenzione.
- Zero file nel deposito degli artefatti LRE.
- Foto originali non toccate. Entrambi i collegamenti `Immagini` puntano
  ancora a `/mnt/nas_public/media/Immagini`; nessuna scrittura su quel percorso.
- Configurazione LRE invariata:
  `24b0ec70d4bce174a838d86c3dfd3a9e6e30c37731354df776223f5eb3f382e4`.
- Servizi HTTP, Telegram, LRE, browser e controllo di prontezza attivi;
  zero riavvii automatici dopo l'avvio finale.

Evidenza conclusiva: `/var/lib/metnos-admin/agent-runs/run-330i2_ey`.

## Rimozione recuperabile

Le directory sono state spostate, con servizi arrestati e processo di
riconciliazione escluso tramite il suo lucchetto, nella copia privata:
`/var/lib/metnos-admin/agent-runs/run-ug1pnzwx/recovery`.
Non è una directory consultata dalla ricerca o da LRE. Percorsi rimossi:

- `/var/lib/metnos-service/.local/share/metnos/index/image`
  (indice archivio, indici di prova e generazioni parziali);
- `/var/lib/metnos-service/.local/state/metnos/durable_workloads`
  (i sei lavori precedenti, relativi dati, concessioni sulle sorgenti e copie
  di lavoro; database e stato vengono ricreati dal normale avvio);
- `/var/lib/metnos-service/.local/share/metnos/durable_workloads`;
- `/home/roberto/.local/share/metnos/index/image/d789c4c0323b9b68`
  (vecchia copia dello stesso archivio, 30.937 voci).

Le prime tre directory sono state ricreate vuote con proprietario e permessi
precedenti. Nessuna alterazione di release, firme, modelli, budget configurati,
registro delle persone o storico generale dei turni. Le attestazioni di
rilascio e le evidenze amministrative precedenti restano conservate.

Il primo tentativo `run-5apsqc82` si è arrestato prima di spostare dati:
Telegram ha superato i 10 secondi previsti per chiudersi ed è stato terminato
da systemd, rimanendo nello stato `failed`. Il ripristino dell'avvio è stato
richiesto automaticamente. Il secondo tentativo ha verificato PID zero e
assenza di processi nei gruppi di controllo anche per uno stato `failed`,
prima di effettuare gli spostamenti. Il ritardo di chiusura Telegram è un
rilievo operativo distinto; non è stato corretto in questo intervento.

**Avviso per verifiche successive:** il vecchio lavoro foto
`wrk_df6d27b3c6c24d828aca6a2b24915b5e` non deve più esistere nel database attivo.
I precedenti script che ne pretendono presenza e impronta invariata sono
ora obsoleti. La prossima ricerca deve produrre un nuovo lavoro, non riprendere
quello sospeso. La corretta reindicizzazione completa resta da verificare
dopo la richiesta che Roberto farà dalla console.
