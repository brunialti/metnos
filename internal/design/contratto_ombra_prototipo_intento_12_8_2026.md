# Contratto ombra 0.1 del prototipo universale d'intento

Data: **12 agosto 2026**.

Stato: **contratto di laboratorio, non collegato alla produzione**.

Questo documento esegue il punto 2 dell'«Ordine di lavoro autorizzato» in
`checkpoint_qualita_intento_12_8_2026.md`. Traduce in regole precise le quattro
scelte approvate da Roberto:

1. le azioni condizionate vivono dentro regioni possedute dalla barriera;
2. gli esiti sono identificatori chiusi dichiarati dal contratto;
3. il prototipo usa un registro ombra revisionato e congelato;
4. la continuazione approvata è tipizzata e immutabile.

La scelta 4 vale **per questo prototipo**. Una versione futura potrà provarne
un'altra, ma soltanto con un nuovo numero di contratto e senza cambiare in
silenzio il significato di questa versione.

## 1. In una frase

Il modello può produrre soltanto uno di tre oggetti:

- un insieme ordinato di operazioni, eventualmente con “scatole chiuse” che
  si aprono per un esito preciso;
- un controllo del sistema, per esempio `undo_last_turn`;
- una dichiarazione esplicita che il significato non è rappresentabile.

Le operazioni dentro una scatola non esistono nel percorso incondizionato. Se
l'utente approva una scatola, viene conservata l'esatta continuazione che ha
approvato; la richiesta non viene interpretata di nuovo.

## 2. Che cosa significa “universale” in questo contratto

“Universale” ha qui un significato verificabile, non assoluto per slogan:

- lo schema non contiene nomi speciali per singoli executor;
- nuove operazioni, controlli, barriere ed esiti si aggiungono al registro,
  senza cambiare la forma dell'oggetto;
- lo stesso contratto vale per ogni lingua, perché usa identificatori tecnici
  e non sinonimi della frase;
- ciò che non appartiene al registro non viene trasformato in una rotta
  plausibile: termina in `unrepresentable` oppure in errore di validazione;
- nessun ramo può eseguire un'azione che non possiede strutturalmente.

Il contratto non promette che ogni significato umano sia già coperto dal primo
registro. Promette che la copertura può crescere senza inventare un nuovo
schema e che ciò che non è coperto viene dichiarato senza falsa azione.

## 3. Confini

Il prototipo 0.1 analizza **intento e dipendenze**, non esegue strumenti e non
estrae credenziali o argomenti runtime.

Sono fuori da questo punto:

- modifiche a `runtime.engine.types.Intent`;
- modifiche a manifest o servizi di produzione;
- una proiezione eseguibile nel runtime corrente;
- prompt definitivo, inventario definitivo e oracolo delle 120 richieste;
- misure GPU.

Questi elementi appartengono ai punti successivi dell'ordine autorizzato.

## 4. Le tre radici, esclusive

La radice usa il campo discriminante `kind`. Deve avere esattamente una delle
tre forme seguenti. Campi aggiuntivi sono vietati.

### 4.1 Grafo operativo

```json
{
  "kind": "operation_graph",
  "body": [
    {"kind": "operation", "route": "find/entries"}
  ]
}
```

`body` è una sequenza non vuota di `operation` o `barrier`. L'ordine è parte
del significato.

### 4.2 Controllo di sistema

```json
{
  "kind": "system_control",
  "control": "undo_last_turn"
}
```

`control` è una chiave esatta del registro ombra dei controlli. Un eventuale
campo `inputs` è ammesso soltanto quando il contratto registrato dichiara
input semantici forniti dall'utente; gli input posseduti dal runtime non
possono essere emessi dal modello.

La prima fetta registra `undo_last_turn` senza input model-facing. Il nome non
è codificato nello schema: è un dato del registro.

Una radice `system_control` non può contenere operazioni, barriere o rotte
ordinarie. Una richiesta che mescola in modo inseparabile radici diverse deve
essere dichiarata non rappresentabile; non viene appiattita.

### 4.3 Significato non rappresentabile

```json
{
  "kind": "unrepresentable",
  "reason": "<reason_ref>"
}
```

`reason` è una chiave esatta del registro ombra delle ragioni. Non è testo
libero. Questa radice non può contenere rotta, controllo, barriera o azione.

`unrepresentable` è un documento valido e semanticamente esplicito. Non va
confuso con JSON rotto, schema non valido, errore di trasporto o timeout.

## 5. Operazione ordinaria

Forma minima:

```json
{
  "kind": "operation",
  "route": "find/entries"
}
```

Forma con dipendenze dati:

```json
{
  "kind": "operation",
  "route": "publish/entries",
  "data_from": [
    {"from": 0}
  ]
}
```

Regole:

- `route` è una chiave tecnica esatta del registro delle operazioni;
- non si accettano sinonimi, traduzioni o somiglianze lessicali;
- ogni operazione riceve dal normalizzatore un identificatore derivato dalla
  propria posizione; il modello non emette il proprio identificatore;
- `data_from` si omette quando è vuoto;
- `from` indica l'ordinale, a base zero, di un'operazione precedente
  nell'attraversamento in preordine del documento;
- se origine o destinazione hanno più porte possibili, l'arco deve indicare
  anche `output` e/o `input` con chiavi esatte dichiarate dai rispettivi
  contratti; quando la porta è unica, il normalizzatore può derivarla;
- un arco non può puntare in avanti, a una barriera, a un ramo fratello o a
  un'operazione che non domina la destinazione.

Non sono previsti campi specchio, ancore usate come identità o coppie
`verb/object` duplicate. Il registro può proiettare `route` nella coppia
necessaria al solo confronto con il banco.

## 6. Barriera e regioni possedute

Forma:

```json
{
  "kind": "barrier",
  "barrier": "get/approval",
  "cases": [
    {
      "outcome": "approved",
      "body": [
        {"kind": "operation", "route": "publish/entries"},
        {"kind": "operation", "route": "update/entries"}
      ]
    }
  ]
}
```

Regole:

- `barrier` è una chiave esatta del registro ombra delle barriere;
- il registro dichiara l'insieme chiuso e ordinato degli esiti ammessi;
- `outcome` deve appartenere a quell'insieme e non può ripetersi;
- i casi emessi devono rispettare l'ordine dichiarato dal registro;
- ogni `body` emesso è non vuoto;
- un esito ammesso ma omesso equivale a una continuazione vuota; il
  normalizzatore la materializza senza chiamare il modello;
- almeno un esito deve possedere una continuazione non vuota;
- una continuazione può contenere operazioni e altre barriere;
- una barriera può dichiarare `data_from` con le stesse regole di dominanza
  delle operazioni, se il suo contratto ha ingressi dati;
- prompt, `dialog_id`, token di ripresa e altri dati runtime non sono emessi
  dall'analizzatore.

La prima fetta registra `get/approval` come barriera e usa identificatori
tecnici equivalenti ad approvato e rifiutato. I nomi esatti verranno fissati
nel registro congelato del punto 3; il contratto non dipende dalle parole
inglesi dell'esempio.

### 6.1 Proprietà della scatola chiusa

Un'operazione dentro `cases[i].body` appartiene soltanto a quel caso. Non può
comparire contemporaneamente nella regione esterna con lo stesso significato.

Ne seguono tre proprietà controllabili:

1. senza l'esito richiesto, nessun nodo del caso è raggiungibile;
2. un nodo di un caso non può fornire dati a un fratello o alla regione
   esterna, perché non domina quei percorsi;
3. un nodo precedente alla barriera può fornire dati ai casi, perché esiste
   su tutti i percorsi che vi entrano.

Un flusso che richiede un valore prodotto soltanto in alcuni casi e poi usato
fuori dalla barriera non viene riparato o indovinato: è non rappresentabile in
questa versione.

## 7. Registro ombra

Il registro è un artefatto di laboratorio separato e congelabile. Non modifica
i manifest di produzione.

Ogni snapshot contiene almeno:

```text
contract_version
source_files[] + sha256
operations{}
system_controls{}
barriers{}
unrepresentable_reasons{}
registry_sha256
review_status
```

Ogni operazione dichiara chiave canonica, sorgente autorevole e porte dati.
Ogni controllo dichiara chiave canonica, classificazione esplicita come
controllo di radice e input model-facing ammessi. Ogni barriera dichiara
chiave canonica, insieme ordinato degli esiti e porte dati.

La classificazione non può essere dedotta da:

- nome dell'executor;
- `SYSTEM_VERBS`;
- prefisso di capability `system:*`;
- capability `dialog.user_input`;
- presenza negli helper universali.

Serve un'annotazione ombra esplicita, revisionata e legata con impronta alla
fonte. Il punto 3 stabilirà l'inventario iniziale e le impronte.

## 8. Separazione fra emissione, normalizzazione e validazione

Il prototipo conserva quattro oggetti distinti:

1. byte e JSON emessi dal modello;
2. forma normale nuova e immutabile;
3. esito del validatore;
4. proiezione usata dalla valutazione.

Nessuna fase può mutare l'oggetto della fase precedente.

La normalizzazione può soltanto:

- assegnare identificatori derivati dalla posizione;
- materializzare casi vuoti dichiarati dal registro;
- derivare porte quando il contratto ne ammette esattamente una;
- produrre la serializzazione canonica e le impronte.

Non può:

- cambiare una rotta;
- trasformare un ruolo non eseguibile in operazione;
- inventare un bersaglio di controllo;
- sostituire alias;
- aggiungere o spostare un'azione dentro una barriera;
- convertire un errore tecnico in `unrepresentable`.

## 9. Validazione fail-closed

Il validatore strutturale rifiuta:

- radice assente, multipla o con campi extra;
- discriminante sconosciuto;
- corpo operativo vuoto;
- rotta, controllo, barriera, esito o ragione fuori dal registro congelato;
- esiti duplicati, fuori ordine o casi emessi vuoti;
- input posseduti dal runtime;
- riferimenti dati inesistenti, in avanti o non dominanti;
- riferimenti fra rami fratelli o dal ramo verso l'esterno;
- campi ambigui che il registro non consente di derivare in modo unico;
- qualunque ciclo nel grafo unito.

Il validatore semantico controlla, senza lessico della query:

- compatibilità delle porte dati;
- appartenenza di ogni oggetto alla corretta classe del registro;
- separazione fra radici;
- proprietà di possesso e dominanza delle regioni;
- possibilità di costruire una continuazione tipizzata per ogni caso.

Il validatore non assegna credito di accuratezza. Un documento può essere
formalmente valido e avere scelto la rotta o la scatola sbagliata; questo viene
giudicato dall'oracolo.

## 10. Continuazione immutabile del prototipo

Per ogni caso non vuoto il proiettore crea un oggetto `Continuation` che
contiene:

```text
contract_version
registry_sha256
root_document_sha256
barrier_path
outcome_ref
typed_body
continuation_sha256
```

La continuazione viene costruita una sola volta dalla forma normale. La
decisione dell'utente è legata a `continuation_sha256`.

Nel prototipo valgono queste regole:

- dopo la decisione non si richiama l'analizzatore;
- non si ricostruisce il piano dalla frase originale;
- non si aggiungono, rimuovono o riordinano operazioni;
- un'impronta diversa, un registro diverso o un esito sconosciuto falliscono
  chiusi;
- la continuazione non viene eseguita nel laboratorio: viene soltanto
  costruita e verificata.

Durata, scadenza e integrazione col dialogo reale non vengono decise qui,
perché non servono a valutare l'estrazione. Se in futuro si proverà la
rianalisi, sarà un contratto versionato distinto, come richiesto da Roberto.

## 11. Busta dell'esperimento

Ogni risultato salvato deve legare almeno:

```text
contract_version
query_sha256
sample_sha256
prompt_sha256
schema_sha256
registry_sha256
raw_model_output
normalization_result
validation_result
normalized_document_sha256
projection_result
```

Gli esiti tecnici restano separati:

- trasporto fallito;
- output non decodificabile;
- documento non valido;
- intento valido e rappresentabile;
- `unrepresentable` valido;
- proiezione fallita.

Questa separazione impedisce di contare un errore come astensione prudente.

## 12. Proiezione e compatibilità

La proiezione del laboratorio produce tipi distinti:

```text
OperationGraph
SystemControl
Unrepresentable
Operation
BarrierRegion
OutcomeCase
DataEdge
Continuation
```

L'adattatore per il banco può ricavare da un'operazione la coppia tecnica
necessaria al confronto. Non può:

- trasformare `system_control` in una falsa coppia `verb/object`;
- trasformare `unrepresentable` in lista vuota senza conservarne il tipo;
- appiattire una regione e perdere quale esito possiede le azioni;
- proiettare il documento nel tipo `Intent` di produzione.

Per la valutazione, rotte ordinarie, controllo di sistema, astensione e
possesso condizionale restano colonne separate.

## 13. Vincoli prestazionali del disegno

Il contratto riduce l'uscita model-facing mediante:

- un solo discriminante iniziale;
- nessun identificatore proprio emesso dai nodi;
- nessun campo specchio;
- omissione di liste vuote e casi vuoti;
- registri fuori dall'uscita;
- una chiave tecnica per rotta invece di coppie duplicate;
- nessuna seconda inferenza per normalizzare o riprendere;
- nessun ramo JSON Schema duplicato per ogni executor.

Queste sono proprietà del disegno, non un risultato misurato. Il punto 5
potrà usare una sola misura GPU con controllo affiancato per verificare token,
latenza e qualità insieme.

## 14. Esempio completo

Richiesta concettuale: trovare elementi; dopo approvazione, pubblicarli e
aggiornarli.

```json
{
  "kind": "operation_graph",
  "body": [
    {
      "kind": "operation",
      "route": "find/entries"
    },
    {
      "kind": "barrier",
      "barrier": "get/approval",
      "cases": [
        {
          "outcome": "approved",
          "body": [
            {
              "kind": "operation",
              "route": "publish/entries",
              "data_from": [{"from": 0}]
            },
            {
              "kind": "operation",
              "route": "update/entries",
              "data_from": [{"from": 0}]
            }
          ]
        }
      ]
    }
  ]
}
```

Il rifiuto è omesso e viene materializzato come caso vuoto. Pubblicazione e
aggiornamento appartengono alla sola approvazione. La ricerca è esterna perché
avviene prima del consenso. Nessuna delle due azioni protette può essere
recuperata dal percorso esterno.

## 15. Revisione avversariale

### 15.1 Attacchi

1. Chiamarlo universale può nascondere che il primo registro copre pochissimi
   controlli e barriere.
2. Una scatola corretta può possedere le azioni sbagliate: la struttura non
   prova la fedeltà alla frase.
3. Omettere i casi vuoti riduce token ma potrebbe nascondere una dimenticanza
   del modello.
4. Gli ordinali delle sorgenti dati possono diventare fragili in documenti
   grandi o molto annidati.
5. La continuazione immutabile impedisce reinterpretazioni, ma potrebbe
   diventare obsoleta se il mondo cambia dopo il consenso.
6. Il registro ombra può divergere dai manifest reali.
7. `unrepresentable` può diventare una scorciatoia per evitare casi difficili.
8. Il divieto di far uscire dati da un caso rende sicuri alcuni grafi ma non
   rappresenta flussi con unione condizionale.
9. L'esempio usa nomi inglesi e potrebbe contaminare la valutazione se venisse
   copiato nel prompt.
10. La forma sembra più corta di V23lite, ma regioni, casi e riferimenti
    potrebbero annullare il guadagno sui casi complessi.

### 15.2 Risposte e prove obbligatorie

1. “Universale” è definito come stabilità rispetto ai registri, non copertura
   già perfetta. La copertura va pubblicata per classe.
2. L'oracolo giudicherà separatamente rotta, appartenenza alla regione ed
   esito; la validità strutturale non vale come accuratezza.
3. Il registro dichiara tutti gli esiti e il normalizzatore materializza quelli
   omessi; l'oracolo deve comunque verificare eventuali azioni di rifiuto.
4. Mutation test sposteranno e rinumereranno nodi; il normalizzatore deve
   produrre identità canoniche senza cambiare il significato.
5. Il laboratorio non esegue. Una futura integrazione dovrà definire scadenza
   e nuovo consenso, senza rianalisi silenziosa.
6. Ogni snapshot lega fonti e impronte; ogni deriva rende il risultato non
   confrontabile, non viene assorbita.
7. I totali separeranno astensione corretta, astensione indebita, errore e
   falsa azione; non si userà una media che compensi le classi.
8. La versione 0.1 fallisce chiusa sui merge condizionali. Una futura unione
   tipizzata richiederà una nuova versione, non una riparazione implicita.
9. Gli esempi di questo documento non entrano automaticamente nel prompt. Il
   controllo contaminazione resta obbligatorio.
10. Token e latenza sono ipotesi. Nessun giudizio prestazionale viene dato
    prima della coppia GPU autorizzata al punto 5.

### 15.3 Esito della revisione

La revisione non trova una via con cui un'azione posseduta da un caso possa
uscire legalmente dalla scatola, né una via con cui la ripresa possa cambiare
la continuazione senza invalidarne l'impronta.

Restano limiti dichiarati, non scelte nascoste: inventario ancora da congelare,
merge condizionali non supportati nella 0.1, accuratezza semantica ancora da
misurare e ciclo reale di scadenza fuori dal laboratorio. Nessuno richiede una
decisione nuova per iniziare il punto 3.

## 16. Stato

- punto 2 dell'ordine autorizzato: **completato**;
- implementazione: **non iniziata**;
- punto successivo: **riconciliare e congelare l'inventario del punto 3**;
- misura GPU: **0**;
- runtime/produzione: **nessuna modifica**;
- banco congelato: **nessuna modifica**;
- servizi: **nessun riavvio**;
- commit: **nessuno**.
