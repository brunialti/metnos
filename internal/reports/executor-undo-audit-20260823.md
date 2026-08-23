# Audit semantico degli executor non annullabili — 23 agosto 2026

## Perimetro e criterio

L'audit parte dai 19 executor che il catalogo generato classificava come
`not_undoable` dopo la chiusura di RM-0006. Per ciascuno sono stati verificati
manifest firmato, effetto reale, risultato restituito, backend e presenza di un
identificatore o di uno stato precedente sufficiente a ripristinare la stessa
situazione. Una compensazione, una ricostruzione approssimata o una nuova
azione con effetti collaterali diversi non sono considerate annullamento.

Il criterio e' universale: un executor e' annullabile soltanto se la sua
esecuzione riuscita produce una ricevuta esatta, il manifest firmato ammette il
percorso inverso e il ripristino puo' verificarne la postcondizione. Non si
usano nomi, query, destinatari o testo naturale per ritrovare il bersaglio.

## Esito

Un elemento era classificato nella categoria sbagliata: `consult_frontier` non
lascia stato utente da ripristinare. Il suo manifest ora dichiara l'effetto
`read_only` e il generatore usa questa proprieta' firmata, oltre alla tassonomia
canonica, per assegnare `not_applicable`. Dopo il contratto per-esecuzione di
ADR 0217 il censimento e' 23 `undoable`, 13 `not_undoable` e 49
`not_applicable`.

Una omissione tecnica e' stata chiusa senza introdurre casi speciali nel
broker:

| Executor | Ricevuta implementata | Stato |
|---|---|---|
| `set_messages` | lettura prima/dopo e delta canonico dei membri effettivamente aggiunti/rimossi | implementato e provato; il reverse applica soltanto il delta, preservando variazioni estranee |

Quattro casi sono stati riprogettati con il contratto generale per-esecuzione.
Analisi e specifiche sono in
`internal/design/undo-redesign-spec-20260823.md`:

| Executor | Perche' non basta una compensazione | Esito implementato |
|---|---|---|
| `open_sites` | il batch puo' contenere sessioni nuove e sessioni riusate, che non devono essere chiuse | ricevuta dei soli ID creati; soli riusi = `no_effect` |
| `set_signatures` | il ramo `forbidden` non puo' essere cancellato per Legge 1 | snapshot completo prima/dopo, compare-and-swap; `forbidden` = `irreversible` |
| `run_processes` (allora `create_processes`) | fermare per nome potrebbe colpire un processo preesistente e la persistenza all'avvio e' un secondo stato | sessione nuova legata a PID+creation-time; persistenza ancora `irreversible` |
| `login_urls` | sovrascrive un cookie jar 0600; copiarlo nel journal duplicherebbe un segreto | backup cifrato actor-bound fuori dal journal e compare-and-swap per digest |

`delete_dirs` resta l'unico caso aperto: Drive ha un cestino esatto, ma nel
sandbox locale target e history sono mount distinti e il rename atomico
fallisce con `EXDEV`. Serve scegliere fra cestino per-filesystem indicizzato e
archivio a due fasi riprendibile; nessuna copia seguita da `rmtree` viene
presentata come rollback.

`set_credentials` e `set_persons` restano intenzionalmente non annullabili per
decisione dell'utente: segreti e dati biometrici si cancellano soltanto con
un'azione esplicita, mai tramite il comando generico di undo.

I dodici casi elencati sotto, insieme a `delete_dirs`, sono correttamente
`not_undoable` con il contratto e i
provider o le decisioni di prodotto attuali:

| Executor | Motivo |
|---|---|
| `act_sites` | puo' produrre qualunque mutazione remota consentita; non esiste un inverso universale |
| `delete_calendars` | l'API elimina il calendario secondario e non espone restore |
| `delete_credentials` | purge intenzionale metadata-only: non conserva i segreti cancellati |
| `delete_images_indices` | elimina un derivato persistente; ricostruirlo e' un rimedio, non il ripristino dello stesso stato |
| `delete_sites` | una sessione chiusa non puo' essere ricreata con identita' e stato identici |
| `install_packages` | disinstallare e' gia' dichiarato come rimedio separato e non ripristina dipendenze/configurazione preesistenti |
| `login_sites` | un logout universale non esiste e chiudere la sessione distruggerebbe anche lo stato precedente al login |
| `send_messages` | consegna remota e notifiche non sono richiamabili universalmente |
| `set_credentials` | la rimozione di segreti richiede una richiesta utente esplicita; l'undo non conserva copie aggiuntive |
| `set_persons` | la rimozione di dati biometrici richiede una richiesta utente esplicita; l'undo non conserva copie aggiuntive |
| `undo_last_turn` | annulla un turno; il redo non e' implementato e non e' equivalente a ripetere le azioni originali |
| `write_images_google_photos` | la Library API espone creazione/lettura/aggiornamento degli elementi creati dall'app, ma non un metodo di eliminazione |

## Stato di chiusura

Il contratto comune, `open_sites`, `set_signatures`, la sessione di
`run_processes` (rinominato da ADR 0218) e `login_urls` sono implementati e provati automaticamente.
Restano la prova reale Windows dello stop tipizzato e la decisione di storage
per `delete_dirs`. Non si progetta undo automatico per `set_credentials` o
`set_persons`.

Ogni promozione richiede test di round-trip reale tramite `undo_last_turn`,
idempotenza del secondo undo, fallimento onesto se la ricevuta e' incompleta e
rigenerazione del catalogo bilingue.

## Fonti provider verificate

- Google Drive, recupero dal cestino tramite `files.update(trashed=false)`:
  <https://developers.google.com/workspace/drive/api/guides/delete>
- Gmail, lettura dello stato `labelIds` e modifica `addLabelIds` /
  `removeLabelIds` per ID messaggio:
  <https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages>
  e
  <https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/modify>
- Google Calendar, `calendars.delete` senza operazione di restore nel catalogo
  API:
  <https://developers.google.com/workspace/calendar/api/v3/reference/calendars/delete>
- Google Photos Library API, metodi correnti della risorsa `mediaItems`:
  <https://developers.google.com/photos/library/reference/rest/>
