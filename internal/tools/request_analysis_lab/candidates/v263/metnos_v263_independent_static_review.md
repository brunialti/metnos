# V26.3 — review statica indipendente pre-inferenza

Data: 2026-08-08. Review esclusivamente offline. Nessun output V26.3 letto,
nessuna chiamata al modello o al server, nessun gate aperto.

## Verdetto

**NON AUTORIZZARE il run del freeze V26.3 corrente.**

Il candidato corregge la regressione principale di V26.2: il prompt contiene
un registro tecnico completo e simmetrico e mantiene la forma minima. Tuttavia
tre difetti indipendenti impediscono un gate fail-closed affidabile:

1. il validator accetta frame semanticamente incoerenti e può accreditarli
   come valutabili o semanticamente esatti;
2. la catena del freeze non lega l'implementazione UAX effettivamente eseguita
   né tutte le dipendenze transitive;
3. il runner considera sufficiente il pre-gate dell'autore e non verifica una
   review, un oracolo o un lock indipendenti.

Il blocco resta valido anche dopo la consegna dell'audit dell'oracolo. Quella
consegna è necessaria, ma non risolve questi tre problemi del candidato.

## Artifact revisionati

Freeze autore V26.3:

- runner: `57580bfc8ee7576b1486bb6f9990d90926d0bc82aa8c4006c7058d8f6d8ea7ac`;
- prompt: `7ae33768167dd4803903008e1e1a690c99d6da7a4e718404be6d194526e74146`;
- schema: `163aeea62eba817c3390d8b2ff7610bf82bb1b31ffd5f914274754ca4034d360`;
- freeze: `80c1b4e70f1ef5cb75d1ddea1458f3a4934598729a03c3c552c562fe8eeaeb0c`;
- fixture: `9196cd736eee25aeabb5b1c1b4803c8b63e71f8c32f63034bf3bfe336f4c32e8`;
- mutation: `6c5608092b417d5d52a2643e93ffb8f2c34b448ae40ae2c0c5400a00cff2babc`;
- audit contaminazione: `4a3de10ed31eaa8e10049bc255632bd320df6bae366c1533e6395ac515743769`;
- author pre-gate, non operativo: `2f7ddffd6cb37f0e7b380adcb9935dba6a3cddc4cfa990b0dec38701aa8ddde8`.

Le copie archiviate e quelle correnti in `/tmp` coincidono. Il self-test
dell'autore è stato ripetuto offline ed è 37/37; non prova però le incoerenze
elencate sotto.

## Esiti per dimensione

### Prompt e registro tecnico — PASS con una contraddizione locale

Il prompt:

- definisce tutti i 4 ruoli, 7 speech act, 10 valori relation incluso `none`,
  7 subject ref, 5 persone grammaticali, 4 tempi, 7 proof kind e 3 stati di
  copertura;
- assegna firma ordinata e descrizione tecnica a tutte le 9 relazioni attive;
- non contiene esempi, sinonimi, trigger o mapping della lingua sorgente;
- separa esplicitamente relazione, soggetto e tempo da route, capability,
  tool, oggetti di prodotto e piano di esecuzione.

La completezza testuale è simmetrica. Resta però una contraddizione nel
contratto di soggetto: il prompt dice che `subject_ref` classifica sempre il
primo argomento della relazione, mentre `current_actor_location_operand`
classifica la posizione dell'attore usata come operando di un'altra relazione.
Per `spatial.near(collection, spatial_position)` questo è il secondo argomento,
non il primo. La rappresentazione mescola quindi subject e operand proprio nel
controllo di dipendenza dalla posizione. Va separato in un campo operand oppure
reso un riferimento argomento indicizzato e generale.

### Schema e prova by-construction — PASS strutturale, FAIL semantico

Aspetti riusciti:

- oggetto `evidence` required con esattamente `relation`, `subject`, `time`;
- tagged union disgiunta per `kind` e `additionalProperties=false`;
- soltanto `explicit_segment` porta estremi;
- gli anchor di clausola e predicato delle altre varianti sono locali e
  impliciti, quindi non possono divergere da ID duplicati;
- gli span espliciti devono essere positivi e interni alla clausola.

Il problema è che lo schema offre lo stesso insieme di sette proof kind a ogni
claim. Accetta quindi, per esempio, `tense_morphology` come prova della
relazione e `speaker_context` come prova di un tempo storico. Il prompt implica
una matrice claim-kind più stretta, ma schema e validator non la applicano.

Inoltre `none` è il primo branch di tutte e tre le union, mentre il prompt lo
presenta per ultimo. Non è una violazione JSON Schema, ma lascia un rischio di
bias nel decoder vincolato. La V26.2 aveva già prodotto prove incomplete; il
rischio non può essere dichiarato risolto da un controllo statico.

### Validator e fail-closed — FAIL bloccante

Il validator controlla schema, ordine e bounds, predicate containment, span
espliciti e la sola coerenza `interpretation`/`relation`. Non applica le altre
postcondizioni dichiarate nel prompt: role/speech, subject/person,
time/evidence, compatibilità claim/proof, grounding completo e arità.

Sonde offline indipendenti sul frame canonico hanno prodotto:

| Mutazione | `validate_frame` |
|---|---|
| `role=request`, `speech_act=quote` | valido |
| `subject_ref=current_actor`, `grammatical_person=third` | valido |
| `time_scope=historical`, proof time=`speaker_context` | valido |
| proof relation=`tense_morphology` | valido |
| `interpretation=supported`, proof subject=`none` | valido |

L'ultima mutazione viene anche contata da `evaluate_records` come valutabile e
semanticamente esatta. Questo contraddice la definizione prompt di `supported`,
che richiede una prova ammessa per ogni claim. Il predicato di binding rifiuta
la prova mancante, ma su una negativa tale rifiuto può essere accreditato come
binding corretto: una mancanza di prova può quindi sembrare una vera
classificazione negativa.

L'arità delle nove firme non è verificabile dal validator perché l'output non
espone gli argomenti tipizzati. Il controllo `nine_relation_arities` conta
soltanto le righe del prompt; non è una postcondizione del frame.

La policy di denominatore è invece corretta nella sola parte esplicita:
`ambiguous`, `unsupported`, invalidità strutturale e trasporto diventano
`NOT_EVALUATED`; non ricevono credito. Il difetto è che diverse invalidità
semantiche non vengono riconosciute come tali.

### UAX #29 — PASS nell'ambiente corrente, FAIL di freeze transitivo

L'implementazione attualmente caricata usa `regex.split(r"\b", flags=WORD |
VERSION1)`, preserva gli offset, elimina soltanto segmenti whitespace e supera
le sonde ZH/JA. Non usa tabelle di lingua o script.

Il freeze V26.3, però, lega soltanto il sorgente del wrapper V26.2:
`return base.unicode_segments(text)`. Non lega direttamente:

- `/tmp/metnos_v253_phase1_relation_runner.py`, che contiene l'implementazione
  UAX effettiva;
- il freeze V25.3;
- il modulo endpoint importato transitivamente da V25.3;
- le versioni di `regex`, Unicode e Python che determinano i confini.

Il freeze V26.2 contiene gli hash V25.3, ma `verify_freeze()` V26.3 non invoca
`prior.verify_freeze()` e non verifica quegli hash transitivi. Cambiare la base
V25.3 può quindi cambiare segmentazione o endpoint lasciando verde il verifier
V26.3. Anche l'isolamento dal gold non è garantito per costruzione se una
dipendenza transitiva non bloccata può cambiare il segmenter.

### Assenza di surface e gold leakage — PASS sul freeze corrente

La fixture congelata contiene soltanto ID opachi, hash, binding atteso e firma;
non contiene le query. Ispezione manuale delle funzioni `request_body`,
`call_once`, `run_case` conferma che al modello arrivano solo richiesta
originale e segmenti. Il prompt non contiene query intere né overlap contigui
di almeno tre token sui dataset congelati 109 + 34 + 70; non contiene esempi o
mapping lessicali.

Questa conclusione vale per i byte correnti. La lacuna delle dipendenze
transitive va chiusa perché resti vera al momento dell'esecuzione.

### Gate operativo — FAIL bloccante

Il file documentato come author pre-gate è oggi materializzato in `/tmp` con il
nome esatto letto dal runner e `inference_allowed=true`. `verify_gate()` valida
soltanto:

- quel booleano;
- hash del freeze;
- hash dell'audit di contaminazione.

Non richiede hash di review indipendente, oracolo, lock esterno o verifier.
Quindi il pre-gate è non operativo soltanto per convenzione documentale, ma è
operativo per il codice congelato. Un invocatore accidentale di `--controls`
supererebbe questo controllo.

## Correzioni richieste prima di un nuovo freeze

1. Rendere claim-specifiche le union di evidence e validare che ogni frame
   `supported` abbia tre prove non-`none` compatibili con claim e valore.
2. Applicare deterministicamente role/speech, subject/person e time/evidence;
   ciò che non è verificabile senza lingua deve almeno essere marcato come
   postcondizione non verificata, non dichiarato validato.
3. Separare subject dal riferimento a un operando non-soggetto oppure usare un
   riferimento argomento indicizzato comune a tutte le relazioni.
4. Legare direttamente runner e freeze V25.3, implementazione UAX, dipendenza
   endpoint e versioni runtime; il verifier deve percorrere la catena, non
   fidarsi degli hash annidati come dati.
5. Sostituire il path del pre-gate autore con un lock indipendente hash-chained
   a freeze, audit oracolo e questa review. Il runner deve rifiutare l'author
   pre-gate anche se contiene `inference_allowed=true`.
6. Aggiungere mutation negative per tutte le sonde sopra e per il caso in cui
   una prova mancante viene erroneamente accreditata su una negativa.

Poiché il freeze è immutabile, queste correzioni richiedono una nuova versione.
V26.3 resta un artifact pre-run utile, ma non deve essere eseguito né ricevere
credito di accettazione.
