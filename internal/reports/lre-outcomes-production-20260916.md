# LRE: pubblicazione e pulizia dei vecchi lavori — 16 settembre 2026

Stato: release 62 installata e collaudo reale limitato superato. Pulizia finale
definitiva conclusa: zero job e indici foto vuoti; enrolled preservati.

## Autorità e perimetro

Richiesta dell'utente: pubblicare il lavoro verificato, pulire i vecchi job e
lasciare a lui la successiva ricerca sull'archivio foto. La precisazione
«i vecchi job» inizialmente escludeva un eventuale nuovo lavoro di prova.
L'utente ha poi richiesto di eliminare anche quello bloccato durante il rilascio,
non conservare copie dei job e azzerare gli indici foto senza toccare gli
enrolled. Il nuovo tentativo sull'archivio rimane a sua iniziativa.

Sorgenti isolate in `codex/lre-backend-release`: implementazione `9327b06b`,
inventari e impronte del rilascio `6269b6b1`. Nessuna modifica al checkout
principale, alle foto originali o alla configurazione LRE. La cancellazione
degli indici foto è stata autorizzata separatamente alla fine.
Nessun avvio dell'indicizzazione completa autorizzato da questa operazione.

## Pulizia eseguita

Inventario verificato sia tramite API sia direttamente nel database: cinque
job terminali, nessun tentativo attivo, nessun messaggio di completamento
ancora da consegnare. Nessun nuovo job presente all'atto della pulizia.

- `wrk_426b37262d824fbeb3c6022755392a39` — completed.
- `wrk_495453140014412a9c70792b4a23c21f` — completed.
- `wrk_515d36161628473e8b5bd54218023af6` — cancelled.
- `wrk_9ff050ecc9b048098167041d559d6c04` — cancelled.
- `wrk_ab505a8a8a874f76a0029cfc0e5fd059` — failed.

Sotto il lock di riconciliazione, verificata la quiescenza e arrestati i servizi,
sono state spostate esclusivamente le directory di stato e artefatti LRE.
Le directory vuote sono state ricreate con proprietario e permessi originari.
La procedura rifiuta variazioni dell'inventario, directory inattese e processi
residui. Integrità SQLite e chiavi esterne verificate prima dell'operazione.

La prima pulizia aveva prodotto una copia privata recuperabile:
`/var/lib/metnos-admin/agent-runs/run-62b50a38/recovery/`
contenente `lre-state` e `lre-artifacts`. L'utente ne ha successivamente
richiesto la cancellazione definitiva: non considerarla una copia di recupero
disponibile. In questa prima fase gli indici foto non erano bersagli della
pulizia; alias, metadati attivi e impronta di `lre.env` erano invariati.

Il primo tentativo (`run-quq_vwj4`) si è fermato prima di spostare dati perché
lo script di manutenzione pretendeva `MainPID` anche dalle unità timer.
Corretto soltanto quel controllo: un timer senza processo è ammesso dopo
verifica dello stato e del cgroup. Nessuna modifica alle protezioni del prodotto.

Verifica successiva: `run-ojy9h9rw`, 17:01:35 UTC. API e database senza job,
HTTP operativo, LRE abilitato e pronto, nessuna attivazione pendente.

## Rilascio canonico

Preparazione senza privilegi, esportazione, ricopiatura root-owned e
rimisurazione; costruzione del successore firmato, anteprima, transizione e
ammissione attraverso Birth. Nessuna firma manuale né copia diretta nel runtime.

- Release precedente: 61.
- Successore: 62.
- Transizione e ammissione concluse con codice 0; selezione attestata:
  `sha256:3e4e65ab616a814878985902e8e874fd91db73e94ef5ca849df1ca57e4e52e77`.
- Closed build:
  `sha256:ed0ff4ad1423eb618ce2d662cb1acf2572f6416c143a66cb9157208abfa269ec`.
- Esportazione: 1.775 file,
  `4c62ed3088a66b511544a539973fd6c28f3761f2b5745f36affec551d2cbeb9d`.
- Evidenze dell'autorità:
  `/var/lib/metnos-admin/rm0008-cycle-evidence-ed0ff4ad1423eb61`.
- Log integrale:
  `/tmp/metnos-lre-release.SSgcNeOH/release-cross-1789578123156071142.log`.
- Anteprima: solo `create_images_indices`, `find_images_indices` e
  `get_images_indices` cambiano fra i 107 contratti. Nessun altro executor.

Nella preparazione isolata mancavano le risorse editoriali dei tre builtin
store. Il confronto ha rilevato segnaposto `<missing:...>` prima del rilascio:
provisionate le baseline già presenti nelle sorgenti nel solo catalogo temporaneo
e rigenerato l'export. I tre builtin risultano identici al commit iniziale;
nessun segnaposto o cambiamento estraneo è incluso nel candidato.

Controlli aggiuntivi del confine e del ciclo di rilascio: 253 test superati.
Le prove funzionali precedenti (1.045 superate, 10 escluse) sono documentate in
`lre-domain-errors-and-photo-outcomes-20260916.md`; non sostituiscono il collaudo
in esercizio descritto nella chiusura di questo rapporto.

## Collaudo reale

- Turno `e63cc527fc584ea9`: richiesta di indicizzazione di tre file sintetici,
  5.110 byte totali. Job `wrk_a030eb52fc974a209d322975902c870a`.
- Job concluso `completed_with_errors`: cinque unità confermate, nessuna unità
  tecnica fallita e nessun tentativo tecnico con errore. È l'esito atteso dei
  due file intenzionalmente non leggibili, non un fallimento del collaudo.
- Una foto indicizzata, due record negativi; `domain_errors.nitems=2`, un
  `image_decode_failed` e un `image_format_unreadable`. Percorsi e SHA256
  corrispondono esattamente alle tre sorgenti, che non sono state modificate.
- Cinque file della generazione verificati per dimensione e digest. I record
  negativi non hanno vettori, volti o parole chiave inventati.
- Contratto congelato del collaudo:
  `sha256:03d778f04c1a93968ca0755247dd98b887c30a2596bee904c41c11e77d6c750d`,
  cioè l'indicizzatore nuovo, non quello precedente.
- Turno `b4d8f3804a824a21`: `find_images_indices` ha restituito esattamente i due
  record diagnostici, con prefisso fisso inglese e motivo italiano, senza
  allegati né creazione di un altro job. Il campo generico HTTP
  `n_total_matches` vale zero per questa tabella diagnostica: la verifica si
  basa sui record restituiti, non su quel contatore di galleria.
- Console reale HTTP 200, nuove sezioni degli errori presenti e quattro
  etichette correttamente localizzate. Evidenza `run-ol0j34np`.
- Tutor ricompilato automaticamente all'avvio, catalogo
  `sha256:200dfc6e3884abb9f8f6dc4b6b7089b404806e630ed21096290d96e0044f1b16`.
  Turno `a60c651dd5da4f86`: risposta reale distingue elementi da tentativi,
  permanenza della traccia e errori poi recuperati, senza eseguire operazioni.
- Prove private: `run-7edou9zg`, `run-4puypp08`, `run-o0yrjoxv`.
  L'indice sintetico è stato tolto dalle ricerche dopo il collaudo
  (`run-s5tsd56p`); nessun indice fotografico reale coinvolto in questo passo.

Questa prova non certifica la copertura delle oltre 30.000 foto dell'utente.

## Job ammesso durante il rilascio: limite emerso

Il job `wrk_b4e9c2572021439ca79b4d6a7eae0406` è stato creato dall'utente alle
17:06:39 UTC, nella finestra fra transizione del runtime e ammissione finale
dei tre executor. Il contratto congelato usa il vecchio digest
`sha256:27bafecfcba8d56e2e99501da60df80184cfc7df1a04d0b84dc894e016786702`.

Il primo tentativo di discover termina alle 17:07:45 con
`execution.usage_accounting_incomplete`; causa strutturata preservata:
`execution.runner_failed`, classe `contract_violation`. Non c'è una ricevuta
che provi l'assenza di consumi: il gate blocca correttamente, senza inventare
consumi zero. Nessuna foto indicizzata e nessuna unità confermata in quel job.
Il motivo dettagliato della prima violazione non è ricostruibile dalla sola
proiezione di errore; la concomitanza temporale non prova, da sola, un kill.

La finestra di ammissione fra i due passi del rilascio rimane un limite del
ciclo operativo, non corretto in questa release: la quiescenza iniziale non
impedisce una richiesta successiva mentre i servizi sono già tornati pronti.
Non descrivere l'aggiornamento come atomico rispetto ai nuovi lavori.
Non riscrivere retroattivamente i contratti né cancellare il debito contabile
per consentire un retry. È necessario un nuovo lavoro compatibile.

Alla successiva richiesta «cancella vecchi job, ritento», annullato tramite API
con controllo della versione: stato cancelled, versione 7 (`run-jla0sodf`).
Nessun riavvio automatico dell'archivio: l'utente ha scelto di ritentare da sé.

## Azzeramento finale autorizzato

Inventario prima della cancellazione: quattro directory di indici foto,
1.645.785.509 byte complessivi (indice incompleto reale e tre collaudi), più
un `index/images.sqlite` legacy vuoto, senza tabelle. Enrolled separati in
`persons.sqlite` e `persons_examples`: quattro persone, 31 esempi, esclusi
da ogni bersaglio. Censimento privato `run-8ywbnan4`.

La procedura definitiva elimina solo i due job terminali attuali, la precedente
copia dei cinque job e i quattro indici fotografici enumerati; nessuna nuova
copia. Confronta l'inventario sotto arresto dei servizi, protegge da link e
nuovi job, verifica registro e file enrolled per contenuto prima e dopo.
Ricevuta finale `run-78lrn4ej`, 17:22:35 UTC: cancellate le due directory LRE
con i due job attuali, entrambe le directory della precedente copia dei cinque
job, le quattro directory di indici foto e il file legacy vuoto. Nessuna copia
di recupero dei job creata o conservata da questo intervento. Operazione non
reversibile; gli indici foto potranno essere ricostruiti dalle sorgenti.

Registro enrolled e file degli esempi identici per contenuto prima e dopo:
quattro persone e 31 esempi. Alias delle foto originali invariato; le foto non
sono state bersaglio di alcuna cancellazione. Interruttore LRE invariato.

Verifica finale `run-uwc30pkh`, 17:24:27 UTC: release 62 attestata, nessuna claim
o attivazione pendente, HTTP operativo, worker LRE abilitato e pronto, stack
pronto e quiescente. API e database hanno zero job, zero tentativi attivi e
zero eventi da consegnare; nessun indice foto attivo. HTTP, Telegram, browser
e LRE attivi senza riavvii automatici; readiness completata.

Nessun altro lavoro avviato: l'utente può effettuare una nuova ricerca.
