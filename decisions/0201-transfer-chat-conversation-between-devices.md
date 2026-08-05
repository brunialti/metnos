---
id: 0201
title: Trasferimento atomico della conversazione chat fra dispositivi
date: 2026-07-27
status: accepted
area: runtime
related:
  - 0078
  - 0083
  - 0090
  - 0104
  - 0198
complements:
  - 0078
  - 0083
  - 0090
  - 0104
modifies: []
supersedes: []
---

# 0201 - Trasferimento atomico della conversazione chat fra dispositivi

## Contesto

La chat web ammetteva un solo writer attivo per utente, ma modellava la
sessione come appartenenza al browser. Aprendo `chat.metnos.com` su Windows
dopo aver usato il telefono, il conflitto offriva soltanto due esiti:
revocare il telefono e rendere attivo il browser appena aperto, oppure
annullare. Mancava l'operazione semanticamente diversa richiesta dall'utente:
**continuare sul dispositivo corrente la conversazione che era attiva sul
dispositivo precedente**.

Trattare questa operazione come un altro nome del takeover avrebbe perso la
cronologia locale del browser precedente oppure mischiato due storie. Trattare
il device come identità della conversazione avrebbe invece impedito il
trasferimento dei dialoghi pendenti e dei turni persistiti. Servivano quindi
tre concetti separati:

1. il principale autenticato, proprietario dei dati;
2. la conversazione, identità stabile della storia;
3. la lease di scrittura posseduta temporaneamente da un browser.

La stessa modifica ha esposto un debito della superficie: il modal nuovo era
tradotto, ma varie stringhe JavaScript e i form `get_inputs` incorporati nella
bolla erano ancora letterali italiani. La richiesta di prodotto è più forte:
**ogni testo posseduto dalla finestra chat, form incorporati compresi, passa
dal catalogo i18n**.

## Decisione

### Modello dei dati

`runtime/active_sessions.py` conserva due entità distinte in `users.db`:

- `chat_conversations(conversation_id, user_id, channel, legacy_actor,
  created_at, updated_at)` rappresenta la storia logica;
- `active_sessions(..., device_token, conversation_id, revoked_at, ...)`
  rappresenta la lease del writer per la coppia `(user_id, channel)`.

Un `conversation_id` è opaco, con forma `c_...`, ed è sempre verificato contro
`user_id` e canale. Conoscere o indovinare l'identificatore non concede accesso.
Il `device_label` serve soltanto a spiegare il conflitto all'utente e non è una
credenziale. Il `device_token` autorizza la scrittura finché la riga è attiva.

Il canale fa parte dell'identità. Questa decisione trasferisce una
conversazione fra due browser HTTP; non fonde automaticamente HTTP e Telegram
e non cambia l'associazione degli utenti ai canali.

Tutto il contratto è per utente. La lease unica è unica soltanto dentro
`(user_id, channel)`: host e guest possono avere contemporaneamente il proprio
browser attivo, la propria conversazione e il proprio conflitto. Register,
ping, takeover, revoke, invio e lettura della cronologia risolvono ogni volta il
principal autenticato; un token o un `conversation_id` appartenente a un altro
utente viene rifiutato e non modifica la sessione legittima.

### Le tre scelte hanno semantica distinta

Quando `POST /agent/session/register` trova una lease attiva restituisce `409`,
un `takeover_token` one-shot e i metadati non sensibili del writer corrente.
La chat mostra esattamente tre azioni:

1. **Annulla**: non chiama il takeover, non revoca nulla e lascia il browser
   appena aperto in sola lettura. La conversazione candidata locale resta nel
   suo namespace, senza essere copiata o cancellata.
2. **Rendi attiva questa sessione** (`activate_current`): revoca il writer
   precedente e assegna al browser corrente la conversazione candidata che
   aveva già in `localStorage`. La vecchia conversazione non viene fusa.
3. **Continua la sessione precedente** (`continue_existing`): revoca il writer
   precedente e assegna al browser corrente il suo `conversation_id`. Il nuovo
   browser ricarica da server la relativa cronologia; la propria conversazione
   candidata resta separata e recuperabile nel suo namespace locale.

Il timeout visivo del modal è dieci secondi e produce lo stesso esito di
Annulla. Non esiste un default distruttivo.

### Transazione e race

Il registro, durante il conflitto, cattura nel token pendente l'owner, il
canale, il token del vecchio writer, la conversazione candidata e la scadenza.
`confirm_takeover`:

1. verifica l'owner autenticato prima di consumare il token;
2. apre `BEGIN IMMEDIATE`;
3. ricontrolla che il writer attivo sia ancora quello osservato dal modal;
4. sceglie la conversazione in base al `mode`;
5. revoca le lease attive e inserisce la nuova lease nella stessa transazione;
6. pubblica `session_revoked` al vecchio token dopo il commit.

Un modal stantio non può quindi revocare un writer più recente. Il token è
one-shot dopo la verifica dell'owner; un tentativo con un owner differente non
lo brucia. Un riavvio perde soltanto la breve negoziazione in RAM: il browser
ripete `register` e riceve un nuovo conflitto, senza modificare sessioni o
conversazioni.

### Lease di scrittura e stato sola lettura

Ogni invio asincrono della chat porta `device_token` e `conversation_id`.
`/agent/turn/submit` li valida insieme all'owner e al canale prima di creare il
turno; token revocato, owner diverso o conversazione diversa producono un
rifiuto esplicito. L'SSE di sessione rende subito sola lettura il vecchio
browser; il ping è il recupero deterministico se l'evento viene perso.

In sola lettura restano possibili consultazione, copia del testo e lightbox.
Il composer, feedback, retry e form incorporati sono invece non interattivi.
Il server resta l'autorità sul percorso di invio: il CSS non è considerato un
controllo di sicurezza.

### Cronologia e stato conversazionale

Anche lo storage del browser è isolato prima per utente e poi, dove serve, per
conversazione. Il server rende nella pagina uno scope opaco e stabile derivato
dal principal autenticato; non è una credenziale e non sostituisce i controlli
server. Le chiavi correnti sono:

- `metnos_conv_id:v2:<user_scope>` per il puntatore alla conversazione;
- `metnos_device_token:v2:<user_scope>` per la lease locale;
- `metnos_cmd_buffer:v2:<user_scope>` per i comandi del composer;
- `metnos_chat_history:v3:<user_scope>:<conversation_id>` per la cache della
  cronologia.

In questo modo due account che usano lo stesso profilo browser non ereditano
token, comandi o messaggi l'uno dall'altro. Cambiare conversazione svuota il DOM
e rende prima la storia locale dello scope scelto, poi integra i turni server.

`GET /agent/turns/recent` richiede una conversazione owner-bound, legge tutti i
giorni JSONL disponibili e unisce i turni in-flight dello stesso owner. I nuovi
record portano `owner_user_id`; `legacy_actor` è soltanto il ponte ristretto per
i turni anteriori alla migrazione. I dialoghi e i consensi HTTP usano una
chiave stabile composta da owner e conversazione, così possono seguire la
storia trasferita senza trasformare `actor` nell'identità di autorizzazione
degli executor.

### Migrazione dei browser già aperti

Le chiavi globali storiche (`metnos_conv_id`, `metnos_device_token`,
`metnos_cmd_buffer`, `metnos_chat_history` e la history v2 per sola
conversazione) appartengono alla precedente installazione single-user. Soltanto
l'utente host può importarle una volta nel proprio scope; un guest non le legge
e non le cancella. La migrazione non copia una storia su altri utenti o su
tutte le conversazioni.

La colonna `conversation_id` delle vecchie righe è nullable. Il primo ping di
un client aggiornato esegue un binding write-once fra la lease già attiva e il
proprio identificatore locale. Un vecchio client che non invia la conversazione
non riceve un ID inventato dal server. Finché il vecchio writer non ha caricato
la UI aggiornata, `can_continue=false`: il terzo pulsante resta visibile ma
disabilitato e spiega che quel dispositivo deve prima ricaricare la chat.

Questa degradazione evita di promettere il trasferimento di una storia che il
server non può identificare. Le altre due scelte continuano a funzionare.

### i18n come invariante della superficie chat

`chat.html`, `dialog_form.html` e la base dell'iframe ricevono `ui_lang` dal
runtime. Titoli, label, placeholder, attributi accessibili, tooltip, banner,
errori locali, separatori e testo costruito da JavaScript provengono da
`msg(...)`. I cataloghi JavaScript sono dati renderizzati dal catalogo, non un
secondo dizionario di traduzioni nel codice.

Il vincolo non è limitato alle chiavi di questa funzione. Il test
`tests/runtime/http/test_chat_i18n_compliance.py` fallisce se compare prosa
statica nei nodi HTML, nei principali sink DOM, negli alert/confirm o negli
attributi della chat e del form incorporato. Il gate seed ricava
automaticamente le chiavi `msg()` da entrambi i template e richiede righe IT ed
EN. Una nuova lingua usa il `ui_lang` effettivo e la normale catena di fallback
del catalogo finché le sue righe vengono materializzate; non richiede una
condizione JavaScript per-lingua.

## Alternative considerate

### Rinominare il takeover esistente

Non distingue quale conversazione adottare. O perde la storia del telefono o
sostituisce sempre quella di Windows. È respinto perché le due azioni producono
effetti osservabili diversi.

### Copiare o fondere le due cronologie

Una fusione per timestamp non sa associare dialoghi, retry, allegati e turni
in-flight e può creare una storia che non è mai esistita. È respinta. Le
conversazioni restano identità separate; l'utente sceglie quale rendere attiva.

### Rendere la sessione multi-writer

Richiederebbe conflict resolution su input, dialoghi e side effect degli
executor. Non è necessario per spostarsi da un device all'altro e indebolisce
il modello single-writer esistente. È respinto.

### Usare solo la cronologia locale del browser

Non permette a un nuovo device di recuperare turni, stato pendente o allegati
e non resiste alla chiusura del tab. È respinto; il browser conserva una cache
scoped, il server conserva la storia autorevole disponibile.

### Hardcodare le tre label nelle due lingue correnti

Avrebbe risolto soltanto IT/EN e lasciato fuori form, errori e lingue future.
È respinto. Le stringhe appartengono al catalogo centrale e la copertura è
derivata dai template.

## Conseguenze

Il passaggio telefono → Windows è ora un trasferimento esplicito della
conversazione, non una cancellazione mascherata. Il vecchio device riceve la
revoca in tempo reale e non può inviare nuovi turni con la lease scaduta.
Le stesse operazioni eseguite da utenti diversi sono indipendenti, anche se
condividono il computer e l'origine web nel medesimo profilo browser.

Il costo è una nuova identità persistente e una migrazione prudente dei client
legacy. La prima apertura dopo l'aggiornamento può non offrire subito la
continuazione se il writer precedente non ha ancora eseguito il binding; il
messaggio lo dichiara invece di fabbricare una correlazione.

Ogni nuova scritta posseduta dalla chat richiede una chiave catalogo. Questo è
un costo editoriale intenzionale e ora verificato automaticamente. I contenuti
dinamici dell'utente, i nomi propri, gli identificatori tecnici e le risposte
del modello non diventano label UI e non vengono tradotti dal client.

La documentazione pubblica IT/EN descrive sia le tre scelte sia il contratto
HTTP. Essendo parte dell'inventario pubblico ammesso, viene ricompilata dal
Tutor F2 senza schede ad hoc.
