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
canonica, per assegnare `not_applicable`. Il censimento corretto diventa quindi
18 `undoable`, 18 `not_undoable` e 49 `not_applicable`.

Quattro dei 18 executor rimasti hanno un caso certo per l'annullamento, ma il
contratto o la ricevuta sono incompleti:

| Executor | Evidenza | Lavoro necessario |
|---|---|---|
| `open_sites` | crea sessioni nuove e restituisce i `session_id`; `delete_sites` chiude quegli ID esatti | ricevuta `_undo` e inverso che chiuda soltanto le sessioni effettivamente create |
| `delete_dirs` | il backend Drive sposta gia' le cartelle nel cestino ed emette ID e `restore_trashed_files`; il manifest falso impedisce l'undo | ammettere il pattern remoto; per il backend locale salvare contenuti e metadati prima della rimozione, anche con `force=true` |
| `set_messages` | Gmail consente di leggere i `labelIds` e di aggiungere/rimuovere etichette per ID messaggio | acquisire per ogni ID le etichette precedenti e ripristinare esattamente il delta, senza invertire alla cieca `add` e `remove` |
| `set_signatures` | lo store legge la riga esatta prima dell'upsert/delete e il risultato espone gia' parte dello stato precedente | sigillare l'intera riga precedente e ripristinarla o rimuovere soltanto la riga realmente creata |

Altri quattro sono candidati sensati, ma richiedono una decisione o un confine
di sicurezza aggiuntivo prima di poter promettere un vero undo:

| Executor | Perche' non basta una compensazione | Prerequisito |
|---|---|---|
| `create_processes` | fermare per nome potrebbe colpire un processo preesistente e la persistenza all'avvio e' un secondo stato | il client deve restituire identita' di processo/registrazione create e offrire uno stop autenticato su quelle identita' |
| `login_urls` | sovrascrive un cookie jar 0600; copiarlo nel journal duplicherebbe un segreto | snapshot protetto fuori dal journal, con cancellazione o ripristino atomico del file esatto |
| `set_credentials` | una nuova credenziale si puo' eliminare, ma un replace deve recuperare il blob cifrato precedente senza esporlo | backup cifrato opaco legato al turno e politica esplicita di conservazione/scadenza |
| `set_persons` | `add` deve rimuovere solo gli esempi appena creati; `replace` deve conservare esempi biometrici precedenti | ID per-esempio e backup protetto equivalente a quello gia' usato da `delete_persons`, con regole di retention |

I dieci restanti sono correttamente `not_undoable` con il contratto e i
provider attuali:

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
| `undo_last_turn` | annulla un turno; il redo non e' implementato e non e' equivalente a ripetere le azioni originali |
| `write_images_google_photos` | la Library API espone creazione/lettura/aggiornamento degli elementi creati dall'app, ma non un metodo di eliminazione |

## Ordine raccomandato

1. `open_sites`, perche' ha gia' ricevuta e inverso esatti.
2. `delete_dirs`, separando ricevute locali e Drive nello stesso soffitto
   firmato, come avviene per `delete_files`.
3. `set_signatures` e `set_messages`, dopo aver sigillato lo stato precedente.
4. I quattro casi sensibili o device soltanto dopo avere definito retention,
   cifratura e identita' inverse nel rispettivo confine.

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
