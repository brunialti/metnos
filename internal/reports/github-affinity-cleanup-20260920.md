# I 35 tag di affinity che GitHub ha preso in prestito — 20 settembre 2026

Lavoro pronto da eseguire, **non eseguito**: i manifest stanno nei dati utente
e sono firmati, quindi una modifica sul posto romperebbe la firma in esercizio.
Ogni rimozione richiede una ripubblicazione (§7.10), quindi va fatta da chi
governa il ciclo di rilascio, non a mano.

## La regola applicata

Su uno strumento con provider, **ogni tag di affinity deve portare almeno un
token che la famiglia nativa non rivendica.** Se non lo porta, quel tag rende
lo strumento indistinguibile dal nativo, e la selezione può preferirlo su una
domanda che non lo riguarda.

Verbi generici e parole vuote non contano come token propri.

La regola non tocca i tag che nominano il provider: `file github`,
`ls repo`, `directory repo`, `github folder` restano, anche se condividono
`file` o `cartella` col dominio nativo. È il token proprio a salvarli.

## Cosa togliere, per strumento

| strumento | tag ambigui | su totale |
|---|---|---|
| `send_messages_github` | **12** | 15 |
| `delete_messages_github` | **12** | 15 |
| `create_tasks_github` | 4 | 15 |
| `read_tasks_github` | 4 | 15 |
| `find_files_github` | 1 | 12 |
| `list_dirs_github` | 1 | 9 |
| `read_files_github` | 1 | 9 |

**35 tag in totale su 7 strumenti.** Gli altri 9 strumenti GitHub sono puliti.

### `send_messages_github`
`invia mail`, `invia email`, `invia messaggio`, `invia messaggi`,
`invia posta`, `invia messages`, `invia inbox`, `manda mail`, `manda email`,
`manda messaggio`, `manda messaggi`, `manda posta`

### `delete_messages_github`
`cancella mail`, `cancella email`, `cancella messaggio`, `cancella messaggi`,
`cancella posta`, `cancella messages`, `cancella inbox`, `elimina mail`,
`elimina email`, `elimina messaggio`, `elimina messaggi`, `elimina posta`

### `create_tasks_github`
`crea task`, `crea promemoria`, `create task`, `create promemoria`

### `read_tasks_github`
`leggi task`, `leggi promemoria`, `read task`, `read promemoria`

### uno ciascuno
`find_files_github`: `count files` · `list_dirs_github`:
`contenuto della cartella` · `read_files_github`: `contenuto del file`

## Cosa dice questa misura

**Non è una svista.** Due strumenti su sette hanno l'**80%** dei tag presi in
prestito: è l'affinity del dominio posta copiata su strumenti GitHub, non un
termine sfuggito. Fra i 35 c'è `manda mail`, la frase che il commento in
`runtime/prefilter.py` incolpa dell'incidente di over-recall.

**Il problema è concentrato.** `find_files_github` ha un solo tag ambiguo su
dodici: quello strumento è etichettato bene. Quindi non serve rifare
l'affinity di GitHub, servono 35 rimozioni puntuali.

## Il limite di questo intervento

Ripulire i 35 tag chiude l'incidente, **non** la causa. La causa è che GitHub
esprime il provider come suffisso nel nome mentre Google lo esprime come
argomento: due meccanismi per la stessa cosa. Finché resta così, ogni nuovo
strumento `*_github` può ripetere l'errore, e serve un controllo che lo
impedisca (RM-0010 F1).

Con l'allineamento al modello Google (RM-0010 §6bis, strada (c)) questi
strumenti non esisterebbero e i 35 tag nemmeno.

## Come verificare dopo l'intervento

La stessa regola, rieseguita, deve restituire **zero** tag ambigui sui 16
strumenti GitHub, e non deve toccare `file github`, `ls repo`,
`directory repo`, `github folder`, `leggi file github`.
