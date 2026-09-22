# Revisione della pubblicazione incrementale — 8 settembre 2026

## Perimetro

Consenso dell'operatore: aggiornare GitHub pubblico dopo verifica GII e
pubblicare anche la documentazione. Non modificare il servizio in esercizio,
non riscrivere la storia pubblica e non rigenerare autorità o firme produttive.
Base pubblica verificata: `005dd00207aca0b5808f692f562568d5e00ed1d7`.
Sorgente funzionale già verificata in esercizio: `b715a765`; documentazione
IT/EN della revisione privata `fa54e4bf`.

## Classificazione delle segnalazioni

Il prototipo segnalava 387 coppie regola/file perché assimilava il nome
dell'autore, URL GitHub, contatti pubblici del progetto ed esempi a dati
privati. La norma invariabile §7.5 ammette Roberto: conservare nome e
attribuzione pubblica non è un'esenzione per credenziali o configurazione.

Il controllo definitivo conserva la scansione di filesystem e indice Git,
token, chiavi, file sensibili, indirizzi privati, account personali, nomi di
terzi e collegamenti simbolici. I contatti leciti sono un insieme esplicito
di indirizzi di ruolo del progetto; gli esempi nuovi usano domini riservati.
Il token effettivo di pubblicazione viene cercato senza inserirlo negli
argomenti dei processi o nei log.

Quattro esempi firmati sono già presenti, byte per byte, nella base pubblica:

- `executors/send_messages/send_messages.py`: vecchi nomi di account in una
  docstring, non credenziali né configurazione attiva;
- `executors/get_processes/get_processes.py`: nome di macchina in un commento;
- `executors/create_events/manifest.toml`: indirizzi fittizi dei partecipanti;
- `executors/read_messages/manifest.toml`: mittenti commerciali negli esempi.

I quattro casi sono espliciti e vincolati al percorso E al SHA256 dell'intero
file, verificato contro il commit pubblico. Non sono esenzioni per dominio,
per nome di file o per intere categorie di dati. Anche una sola modifica
richiede nuova revisione; token e altri valori non sono esentati. I riferimenti
storici restano quindi presenti: non dichiarare «nessuna stringa personale
esiste nel repository». Non si aggiungono questi riferimenti né si alterano
le firme. Non si tenta una bonifica della storia pubblica senza consenso.

## Bonifica e prova di equivalenza

L'export sostituisce riferimenti locali non firmati (macchina, account di
esempio, proxy) e domini fittizi non riservati. Sono cambiati 12 sorgenti
Python rispetto alla precedente proiezione: l'AST è identico dopo aver
escluso soltanto le docstring. Nessuna istruzione eseguibile è stata cambiata.
Altri cambi riguardano esempi nelle guide, nei prompt, nei template e in
due file del client ausiliario. Tutti i 106 manifest, firme e payload
distribuiti sono byte-identici alla sorgente privata; firme Ed25519 e digest
del codice sono stati verificati.

Profilo privato invariato, 754 sorgenti:
`sha256:2a6c978928a62f8f113d8e13c3664b31b6341df379d760a4256063fdb6256e3b`.
Profilo pubblico revisionato, 742 sorgenti:
`sha256:38c69cf53efbee1f80e2d081cb26f3fb21ce07c66f67977e8b9d1f8ab885f5a2`.
Le sole due impronte di autoriferimento vengono aggiornate con lo strumento
esistente; l'inventario pubblico viene rigenerato e verificato.

Escluso dall'export il residuo locale `--help/`, conservato nel worktree.
Il verificatore non genera più bytecode nei processi figli. Le cache generate
durante la verifica sono state spostate fuori dall'albero pubblico, non
incluse nell'indice. Aggiunti test che rifiutano segreti, nuove copie/modifiche
degli esempi esentati, file sensibili, symlink e divergenza filesystem/indice.

Il publisher ora usa soltanto aggiornamenti incrementali, verifica GII anche
sull'indice finale e non rimuove un eventuale lock Git. Il vecchio wrapper
temporaneo `publish-final.sh` è obsoleto e non va riutilizzato.

## Documentazione online

Distribuzione Cloudflare completata: `f079c956`, progetto esistente `mykleos`.
`https://metnos.com/it/roadmap` e `https://metnos.com/en/roadmap` rispondono 200:
le nove schede corrispondono ai file verificati, compresa RM-0009.
La sitemap pubblica è byte-identica a quella distribuita.
RM-0008 resta correttamente aperta: il successo F4 non chiude F5/F6.

## Esito finale verificato

GitHub aggiornato con commit incrementale
`de801a45b9704e2c29e41d671a06c8754c67292d` su `main`, figlio diretto della
base `005dd002`; albero Git `de338fc1a18c7f30ce36519f0fe19049903b0fc0`.
Il commit remoto è stato riletto dopo il push: corrisponde esattamente.
190 file modificati, provenienti dallo sviluppo accumulato e dalla bonifica;
nessun force-push e nessuna modifica alla release in esercizio.

La suite completa del pacchetto pubblico ha trovato otto prove dipendenti
dal renderer interno escluso dall'export e due fixture contaminate dallo
stato dell'host. Inclusa solo l'esatta dipendenza di certificazione, isolate
le radici temporanee di quelle fixture. Nessuna asserzione rimossa e nessun
controllo produttivo indebolito. Il renderer carica le dipendenze POSIX solo
per scrivere: `--check` resta portabile. Le sei prove delle primitive POSIX
sono marcate per la piattaforma corretta; una prova aggiuntiva impedisce
l'importazione di `fcntl` durante il controllo in sola lettura.

Risultato finale dalla copia pubblica: **2098 passati, 43 skip espliciti,
zero errori/fallimenti**, 110,55 secondi. Non è una prova Windows locale.
Ricevuta `/tmp/metnos-public-retry.VJ6wYh6i/portable-public-final.xml`, SHA256
`e06c934ec1c98e3c9e8f75e4360b2d359e0a5beb077a326c7bd3492855c898cb`.
Altri 31 test privati del controllo GII e delle impronte passati.
GII su filesystem e indice finale: zero segnalazioni non classificate,
comprese scansione del token effettivo e parità integrale delle impronte.

Artefatto riproducibile: `/tmp/metnos-public-retry.VJ6wYh6i/final-export`.
Worktree pubblico: `/tmp/metnos-rm0008-publish-r18.4n19bk9i/public-repo`.
L'indice pubblico censisce 879 Python, inclusi i test e il renderer; il
profilo runtime controllato rimane 742 sorgenti. Le cache prodotte dalle
prove isolate sono conservate fuori dall'albero pubblicato.

