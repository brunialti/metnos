# Referto del punto 3 — inventario congelato del prototipo d'intento

Data: **12 agosto 2026**.

Stato: **punto 3 completato, errore finale 0**.

## Esito in parole semplici

È stato creato il catalogo separato del prototipo. Ogni voce normale, ogni
controllo e ogni barriera ha una classe esplicita: il catalogo non decide dal
nome, da una parola della richiesta o da una capability.

La prima verifica mostrava zero errori, ma la revisione cerca-difetti ha
scoperto che il verificatore non considerava errore una voce del catalogo non
classificata. Quel primo zero è stato rifiutato. La regola è stata resa
obbligatoria, le voci speciali sono state separate e il ciclo è stato ripetuto
fino a ottenere zero errori reali.

Il catalogo resta soltanto nel laboratorio. Non è importato dalla produzione.

## 1. Artefatti

- registro: `internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/intent_shadow_registry_v0_1.json`;
- congelamento: `internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/intent_shadow_registry_v0_1.freeze.json`;
- verificatore: `internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/verify_registry.py`;
- istruzioni: `internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/README.md`.

## 2. Conteggi finali

| Voce | Totale |
|---|---:|
| executor nel catalogo sorgente | 96 |
| executor ordinari classificati | 94 |
| rotte ordinarie distinte | 80 |
| controlli di sistema | 1 |
| barriere | 1 |
| esiti chiusi di barriera | 2 |
| ragioni di non rappresentabilità | 6 |
| file sorgente legati da impronta | 12 |
| prove di mutazione catturate | 7 |
| errori finali | **0** |
| limiti dichiarati separatamente | 3 |

Le voci rimaste non classificate sono: **nessuna**.

## 3. Classi congelate

### 3.1 Operazioni ordinarie

Le rotte sono ricavate dai nomi canonici del catalogo congelato mediante la
grammatica tecnica dei nomi. Più executor qualificati possono condividere la
stessa rotta d'intento; i loro nomi restano elencati nella voce per non perdere
la provenienza.

Ogni rotta 0.1 usa una porta semantica astratta `primary` e un risultato
`result`. Queste porte descrivono il flusso dell'intento, non gli argomenti
reali degli executor.

Verbi del catalogo fuori da ACTIONS: `consult`, `reply`.

Oggetti del catalogo fuori da OBJECTS: `frontier`, `location`, `now`, `session`.

Queste differenze restano visibili; non vengono riparate o nascoste.

### 3.2 Controllo di sistema

Il solo controllo promosso è `undo_last_turn`. La voce è legata al manifest,
alla firma presente, al file di codice e alle rispettive impronte. Gli input
`_actor` e `log_path` restano posseduti dal runtime e non possono essere
emessi dal modello.

### 3.3 Barriera

La sola barriera promossa è `get/approval`, con i due esiti chiusi
`approved` e `rejected`, derivati dai rami pubblici `on_approve` e
`on_reject`. Nessun argomento del manifest è model-facing: la continuazione
viene dalla regione tipizzata, non da un dizionario inventato dal modello.

### 3.4 Voci speciali non promosse

Voci privilegiate fuori dalla grammatica ordinaria: nessuno.

Verbi di sistema osservati nel catalogo ma non promossi: `admin`.

Queste voci non diventano controlli per analogia. In particolare `admin`
resta un'osservazione: Roberto ha autorizzato `undo_last_turn` come prima
fetta, non la promozione automatica di ogni `SYSTEM_VERB`.

Alias interni riservati osservati: nessuno.

Voci riservate presenti direttamente nel catalogo: `undo_last_turn`.

Voci classificate da manifest ma assenti dal catalogo: `get_approval`.

## 4. Fonti e congelamento

Il registro lega con SHA-256 il contratto 0.1, il vocabolario, la grammatica
dei nomi, il catalogo benchmark, le fonti degli helper e del registro
privilegiato, i due manifest, i due file di firma e il codice dei due
executor.

Impronta canonica del registro: `7a4f2916af8963d8d16fb79cb355d23e9739163f2241a84fa28bc6c070ed4a0a`.

Impronta canonica del congelamento: `fcb8c855e2752209007ac6bc76309b4029f410797351dfb5bde9932b0686ee9f`.

Il congelamento lega inoltre registro, verificatore e contratto normativo.
Qualunque deriva produce errore invece di cambiare il confronto in silenzio.

## 5. Ciclo fino a errore zero

1. Costruzione iniziale e controllo deterministico.
2. Revisione avversariale della completezza: trovato che le voci non
   classificate non facevano fallire il controllo.
3. Aggiunta dell'invariante obbligatorio e di una prova di mutazione dedicata.
4. Separazione delle voci privilegiate e dei verbi di sistema non autorizzati,
   senza promuoverli.
5. Rigenerazione delle impronte e nuova verifica.
6. Esito finale: **errore 0**, codice di uscita 0.

Le prove di mutazione verificano almeno deriva delle fonti, esiti duplicati,
passaggio di input runtime al modello, classificazione errata di undo,
collisione della barriera con una rotta ordinaria, deriva dell'impronta e
presenza di voci non classificate.

## 6. Revisione avversariale

### Attacchi

1. Il catalogo benchmark congelato può non coincidere con tutti gli executor
   vivi presenti domani.
2. Raggruppare per rotta d'intento può nascondere differenze fra executor
   qualificati.
3. Le porte `primary/result` sono astratte e non provano compatibilità degli
   argomenti runtime.
4. I file di firma sono legati da impronta, ma questo verificatore non
   sostituisce la verifica crittografica di fiducia della produzione.
5. Le ragioni di `unrepresentable` sono una tassonomia del prototipo, non una
   verità ricavata dai manifest.
6. `admin` potrebbe in futuro meritare una classe propria; promuoverlo ora
   sarebbe una decisione non autorizzata.
7. Le prove di mutazione condividono parte del codice col verificatore e non
   costituiscono una revisione indipendente.
8. Errore zero prova coerenza e congelamento, non accuratezza semantica.

### Risposte

1. Tutte le fonti sono pin-nate; una variazione rende il confronto non valido
   finché non viene revisionata.
2. Ogni rotta conserva l'elenco completo degli executor sorgente.
3. Le porte dichiarano esplicitamente la loro natura astratta. La proiezione
   runtime resta vietata.
4. Il referto non dichiara la firma crittograficamente riverificata; dichiara
   soltanto firma presente e file pin-nato.
5. L'oracolo del punto 4 conterà separatamente ogni tipo di astensione.
6. `admin` resta fuori dalle tre classi operative e conduce a
   `outside_registry`, senza falsa rotta.
7. Le mutazioni sono una barriera minima; la revisione del punto 4 dovrà
   attaccare anche il contenuto.
8. Il prossimo lavoro autorizzato è proprio costruire l'oracolo completo.

### Esito della revisione

Il registro è completo rispetto alle fonti pin-nate: nessuna voce del catalogo
resta senza una destinazione esplicita fra rotta ordinaria, classe riservata o
osservazione non promossa. Nessun nome speciale entra in una classe per
somiglianza.

I limiti rimasti sono dichiarati come limiti, non nascosti per ottenere zero.
Non emerge una nuova scelta necessaria per iniziare il punto 4.

## 7. Stato finale

- punto 3 dell'ordine autorizzato: **completato**;
- primo punto incompleto: **punto 4, oracolo completo**;
- verifica finale: **errore 0**, uscita 0;
- GPU: **0**;
- produzione/runtime: **nessuna modifica**;
- banco congelato: **nessuna modifica**;
- servizi: **nessun riavvio**;
- commit: **nessuno**.
