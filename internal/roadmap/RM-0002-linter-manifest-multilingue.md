# RM-0002 — Linter multilingue dei manifest executor

**Stato:** `active`  
**Creazione:** 2026-07-23  
**Ultima revisione:** 2026-07-23  
**Implementazione:** non iniziata; il linter corrente resta prevalentemente
italiano e nessun manifest è stato modificato durante questa analisi  
**Conservazione:** roadmap persistente fino a implementazione dimostrata o
cancellazione esplicita di Roberto  
**Decisione di prodotto:** tutte le superfici linguistiche che possono essere
lette dal planner devono rispettare gli stessi invarianti strutturali, senza
usare l'italiano come approssimazione delle altre lingue e senza riscrivere
automaticamente la semantica dei manifest  
**Documenti di origine:** `CLAUDE.md` §2.5 e §7.9, ADR 0092,
`internal/design/TODO.md::EXE-DESC-001`, codice e catalogo verificati il
2026-07-23  
**Prove iniziali:** audit read-only di 115 manifest, simulazione del linter per
IT/EN, confronto degli atomi macchina e misura del percorso adattivo

## 1. Sintesi decisionale

Metnos deve avere **un solo linter multilingue**, deterministico e centrale.
Non servono un linter italiano e uno inglese, né regole duplicate per ogni
lingua. Il motore deve enumerare tutte le varianti testuali presenti nel
manifest, applicare una sola volta i controlli indipendenti dalla lingua,
applicare a ogni variante i controlli locali e confrontare fra lingue soltanto
gli invarianti macchina che devono restare identici.

La realizzazione raccomandata ha cinque proprietà:

1. controlla ogni lingua realmente presente, non la sola `it` e non una lingua
   scelta per ripiego;
2. separa contratto globale, forma locale e parità fra traduzioni;
3. opera appena prima di scrittura, traduzione, firma o promozione;
4. non modifica, accorcia o traduce alcun testo;
5. non entra nel percorso ordinario dei turni e non può quindi cambiarne
   latenza, piani o autorità.

Il linter non deve fingere di comprendere la traduzione. Può però provare in
modo affidabile che non siano cambiati nomi di executor, argomenti della
chiamata, segnaposto runtime, riferimenti fra tool e forma dichiarata
dell'output. Il significato residuo resta materia di revisione editoriale,
corpus di instradamento ed eventuale valutatore semantico separato.

La lunghezza inglese è minore soltanto **in aggregato**. Non è un'invariante
utilizzabile: nel catalogo reale l'inglese è più lungo in 24 descrizioni
principali su 115 e in 138 descrizioni argomento su 654. Due campi superano il
limite soltanto in inglese. Un linter solo italiano ha quindi falsi negativi
dimostrati.

## 2. Obiettivo e valore

Il manifest è contemporaneamente:

- contratto firmato dell'executor;
- superficie di scelta del planner locale;
- fonte di termini per il prefiltro;
- sorgente di alcuni comportamenti deterministici di estrazione argomenti;
- sorgente per traduzione, importazione e generazione;
- documento operativo per persone e modelli locali.

Un errore in una sola lingua può quindi produrre un executor formalmente
firmato e caricabile, ma difficile da scegliere o impossibile da invocare in
quella lingua. L'obiettivo di RM-0002 è impedire questa classe di divergenza
senza aumentare la fragilità del runtime e senza imporre una bonifica massiva
dei testi legacy.

Il beneficio atteso è osservabile:

- nessun argomento inesistente suggerito da una variante linguistica;
- nessun argomento risolto dal runtime esposto accidentalmente al modello;
- stessa forma di chiamata nelle traduzioni;
- stessi riferimenti di disambiguazione essenziali;
- avvisi di lunghezza completi e non duplicati;
- errori di traduzione fermati prima della scrittura e della firma;
- stessa politica per executor core, builtin, sintetizzati e importati;
- nessun costo nei turni normali.

## 3. Perimetro e fonti verificate

L'analisi ha seguito il percorso effettivo del codice al 23 luglio 2026:

- `runtime/manifest_lint.py`;
- `runtime/manifest_rules.py`;
- `runtime/loader.py`;
- `runtime/executor_standard.py`;
- `runtime/engine/proposer.py`;
- `runtime/prefilter.py`;
- `runtime/args_extractor.py`;
- `runtime/i18n_translator.py`;
- `runtime/sign.py`;
- `runtime/synt_multistage.py` e `runtime/synth_request.py`;
- `runtime/skill_codegen.py` e `runtime/generated_executor_contract.py`;
- manifest core, contratti builtin e import GitHub installati;
- prove correnti su descrizioni, traduzioni e rendering.

Le misure descrivono il working tree osservato, che contiene anche modifiche
non consolidate. Non provano che il processo HTTP già avviato abbia caricato
gli stessi byte. Gli esperimenti presenti su rendering adattivo e schema
model-facing non costituiscono implementazione di questa roadmap e devono
restare in un intervento separato.

Metnos usa il proprio server locale compatibile con il percorso `llama-server`.
Il disegno qui proposto è deterministico e indipendente dal fornitore LLM; non
introduce dipendenze da Ollama o da servizi esterni.

## 4. Stato reale verificato

### 4.1 Inventario effettivo

| Classe | Percorso | Manifest trovati | Copertura CLI attuale |
|---|---|---:|---|
| core | `executors/*/manifest.toml` | 82 | sì |
| builtin firmati | `runtime/builtin_executor_contracts/*/manifest.toml` | 17 | no |
| import installati | `~/.local/share/metnos/executors/skills/*/*/manifest.toml` | 16 | no |
| totale osservato | tre classi | **115** | **82/115** |

Tutti i 115 manifest hanno descrizione principale italiana e inglese. Sono
presenti 654 descrizioni di argomento in forma multilingue, anch'esse complete
per `it` ed `en` nel campione osservato.

Il CLI di `manifest_lint.py --all` usa soltanto `executors/*/manifest.toml`.
Non visita i contratti builtin e non segue la struttura annidata degli import.
Il traduttore dei manifest usa una scansione analoga a un solo livello e non
visita gli import sotto `skills/<skill>/<executor>`.

### 4.2 Comportamento del linter corrente

`_description_text()` seleziona:

1. `it`, se presente;
2. altrimenti `en`;
3. altrimenti il primo valore disponibile.

La stessa preferenza è ripetuta per ogni descrizione argomento. Di conseguenza
un manifest bilingue normale viene controllato soltanto in italiano. Le lingue
aggiuntive non vengono ispezionate.

La scelta non replica il runtime: il loader seleziona la lingua configurata
con `METNOS_LANG` e, se manca, ripiega prima sull'inglese. Con un'istanza inglese
il planner usa quindi proprio la variante che il linter corrente non controlla.
Il prefiltro tokenizza la descrizione localizzata completa, mentre il proposer
testuale usa la testa fino a `OUT:`; entrambe sono superfici attive.

I controlli correnti sono:

- presenza e ordine di `SCOPO/PATTERN/NON/OUT`;
- posizione di `PATTERN` e `NON` rispetto al budget;
- lunghezza di testa, descrizione e argomenti;
- argomenti usati nel `PATTERN`;
- menzioni di argomenti `runtime_resolved`;
- forma dell'output;
- riferimenti morti nel capitolo `NON`;
- sovrapposizione delle affinity.

Il controllo affinity è indipendente dalla lingua, ma una conversione ingenua
del linter a un ciclo `for lang` lo eseguirebbe e lo conterebbe più volte.

### 4.3 Misure linguistiche

Sul catalogo osservato di 115 manifest:

| Superficie | Italiano | Inglese | Differenza EN rispetto a IT |
|---|---:|---:|---:|
| descrizioni complete | 28.415 caratteri | 27.723 | -692 |
| teste prima di `OUT:` | 21.675 | 21.086 | -589 |
| descrizioni argomento | 80.164 | 71.990 | -8.174 |

L'inglese è quindi più corto nel totale, ma non in ogni risorsa:

- 24 descrizioni principali sono più lunghe in inglese;
- 138 descrizioni argomento sono più lunghe in inglese;
- `write_files.description` misura 313 caratteri in italiano e 323 in
  inglese: soltanto l'inglese supera `DESC_MAX=320`;
- `send_messages.args.to_user.description` misura 180 caratteri in italiano e
  181 in inglese: soltanto l'inglese supera `ARG_DESC_MAX=180`.

Il risultato confuta l'ipotesi operativa «se passa l'italiano, passa anche
l'inglese». La tendenza aggregata non è una proprietà per campo.

### 4.4 Risultati del linter simulato per lingua

Sugli 82 manifest core:

| Lingua | Avvisi | Errori |
|---|---:|---:|
| italiano | 95, tutti di lunghezza | 0 |
| inglese | 85, tutti di lunghezza | 1 |

L'errore inglese è `get_location.actor`: il controllo corrente interpreta
«current actor» come esposizione dell'argomento `actor`, anche se in quel punto
`actor` è una parola naturale e la proprietà viene nascosta al proposer. Questo
è un caso importante: dimostra che estendere meccanicamente le espressioni
regolari italiane/inglesi aumenterebbe i falsi positivi.

Sul catalogo completo di 115 manifest, escludendo il controllo affinity per non
duplicarlo:

| Lingua | Avvisi di lunghezza | Errori strutturali |
|---|---:|---:|
| italiano | 108 | 3 |
| inglese | 85 | 4 |

Tre errori, presenti in entrambe le lingue, appartengono a import GitHub già
installati:

- `list_dirs_github` usa `path=` nel `PATTERN`, ma lo schema dichiara `paths`;
- `send_messages_github` usa `target_template=` e `body_template=`, ma lo
  schema dichiara `target` e `body`.

Questi difetti non vengono segnalati dal CLI corrente perché gli import annidati
non sono nel suo inventario. Non sono stati corretti in questa analisi.

### 4.5 Parità strutturale corrente fra IT ed EN

Su tutti i 115 manifest sono stati confrontati deterministicamente:

- insieme e ordine degli argomenti top-level nelle chiamate del `PATTERN`;
- segnaposto `${RUNTIME:...}` e `{{...}}`;
- riferimenti a executor esistenti nel capitolo `NON:`;
- presenza di `entries`/`results` nel capitolo `OUT:`.

Le divergenze osservate sono **zero in tutte e quattro le classi**. Questo è un
buon segnale: la traduzione corrente conserva gli atomi macchina fondamentali.
Non prova però equivalenza semantica della prosa e non compensa i tre errori
condivisi da entrambe le lingue.

### 4.6 Budget adattivo e lunghezza reale

Tutte le 230 descrizioni principali IT/EN osservate hanno una testa entro
`HEAD_MAX=240`. Dopo normalizzazione e rimozione di `OUT:`, la testa più lunga
renderizzata è di 239 caratteri.

Il renderer adattivo presente nel working tree non modifica nessuna delle 115
teste italiane e nessuna delle 115 inglesi: il catalogo corrente non richiede
redistribuzione del budget per le descrizioni principali.

Il percorso alternativo `agent_runtime.render_tools_for_provider()` non ha
chiamanti di produzione osservati: è richiamato soltanto dai test. Le sue
descrizioni storiche di provider e gli esperimenti di compressione degli
argomenti non rappresentano il percorso attivo del planner e non devono guidare
la progettazione del linter.

Questo dato porta a due conclusioni:

1. il linter multilingue non dipende dal renderer adattivo;
2. il budget elastico non è una giustificazione per accettare nuove teste oltre
   il limite editoriale, perché la loro visibilità dipenderebbe dalla
   composizione del pool.

Le descrizioni argomento sono un caso diverso. Il proposer testuale attivo
espone nomi, obbligatorietà ed enum, non la prosa completa degli argomenti. La
prosa localizzata è però letta da `args_extractor.py` per alcuni flag booleani.
Accorciarla automaticamente può quindi cambiare la normalizzazione degli
argomenti anche quando non cambia il prompt del proposer.

### 4.7 Copertura dei confini di generazione e traduzione

Il percorso attuale non applica una politica testuale unica:

| Confine | Controllo corrente | Lacuna |
|---|---|---|
| Synt multistage | linter sulla descrizione sorgente flat | non controlla la futura traduzione |
| `synth_request` | involucro standard + firma | la variante mancante arriva dopo |
| import skill | involucro standard + validatore standard | nessun linter strutturale completo |
| proposte/promozione | validatori propri e firma | nessun punto unico di qualità testuale |
| traduttore manifest | rapporto lunghezze, placeholder e sentinel | non controlla chiamate, argomenti, riferimenti o budget |
| firma | standard executor | non invoca `manifest_lint` |
| loader | standard + firma | non deve diventare il primo punto di scoperta del difetto |
| CLI | linter core a un livello | manca builtin, import e lingue non italiane |

La docstring del linter dichiara uso in «synt-admission + importer + CLI», ma
nel codice osservato l'importer non lo invoca. La documentazione descrive quindi
un'intenzione più ampia dell'enforcement reale.

### 4.8 Stato delle traduzioni e companion

Sono state osservate 173 voci lingua il cui `version_hash` non coincide con il
testo corrente, distribuite su 21 manifest:

- 2 manifest core;
- 3 contratti builtin;
- tutti i 16 import installati.

Questa misura non equivale automaticamente a corruzione: una differenza può
indicare una modifica in attesa di allineamento. Tuttavia rivela tre problemi
di protocollo:

1. il generatore degli import può lasciare
   `sha256:PLACEHOLDER_NOT_SIGNED_IN_WORKTREE` perché il signer crea il companion
   soltanto se manca e non sostituisce un placeholder esistente;
2. il traduttore non visita il percorso annidato degli import e quindi non
   risolve quei placeholder;
3. se più lingue risultano modificate nello stesso file, `_decide_edit_source`
   sceglie alfabeticamente `en`, perché il `mtime` del file non può distinguere
   quale campo sia stato editato. Una doppia modifica intenzionale può quindi
   essere reinterpretata come «inglese sorgente» e riscrivere l'italiano.

Il linter multilingue non deve limitarsi a segnalare testi: deve inserirsi in una
transazione di pubblicazione che renda espliciti inventario, lingua sorgente,
stato e firma.

### 4.9 Comportamento `--strict`

Il CLI corrente, in modalità `--strict`, conta ogni avviso come errore e termina
con codice non zero, ma stampa ancora la riga con etichetta `[warn]`. Il riepilogo
può quindi dire «1 error» mentre la singola riga dice «warn».

Non è un problema semantico del manifest, ma rende meno verificabile il nuovo
percorso di adozione e va corretto insieme alla struttura dei finding.

### 4.10 Costo misurato

Una simulazione completa su 99 manifest residenti nel repository, due lingue e
controlli correnti ha richiesto circa 19,5 ms per ciclo sul sistema di sviluppo.
Il costo è trascurabile nei confini di authoring e traduzione. Non vi è motivo
di pagarlo a ogni turno o a ogni invocazione executor.

## 5. Problema architetturale

Il problema non è «tradurre anche il linter». I messaggi del linter sono
diagnostica interna e possono restare italiani. Il problema è definire su quali
superfici opera ciascuna regola.

Un manifest contiene tre famiglie diverse di dati:

1. **dati globali**, uguali per tutte le lingue: nome, schema, capability,
   output dichiarato, affinity mista, collocazione e firma;
2. **testi locali**, diversi per lingua: descrizione principale e descrizioni
   degli argomenti;
3. **atomi macchina replicati nei testi**, che devono restare uguali:
   chiamate, argomenti, segnaposto, riferimenti canonici e campi di output.

Il linter corrente confonde queste famiglie: sceglie un testo canonico, poi
applica insieme controlli globali e locali. Un semplice ciclo sulle lingue
duplicherrebbe i controlli globali e non proverebbe la parità fra traduzioni.

## 6. Invarianti della soluzione

La realizzazione non deve violare i seguenti vincoli:

- nessuna modifica automatica ai manifest;
- nessuna traduzione automatica avviata dal linter;
- nessun allentamento di schema, autorità o firma;
- nessun controllo LLM nel percorso deterministico;
- nessun blocco retroattivo del caricamento per un nuovo avviso editoriale;
- nessuna assunzione che `it` sia la lingua sorgente o la più lunga;
- nessuna assunzione che `en` sia sempre disponibile negli artefatti candidati;
- nessuna duplicazione delle regole nei tre generatori;
- nessuna scansione diversa fra linter, traduttore, firma e inventario;
- nessuna bonifica massiva dei 108 avvisi di lunghezza italiani;
- nessuna dipendenza dal renderer adattivo sperimentale;
- nessun uso di una memoria semantica o di un LLM per decidere errori bloccanti.

## 7. Architettura proposta

### 7.1 Un solo inventario dei manifest

Introdurre un helper puro, piccolo e senza dipendenze dal loader, per enumerare
le fonti riconosciute:

- executor core a un livello;
- contratti builtin a un livello;
- executor utente diretti;
- executor utente sotto `skills/<skill>/<executor>`;
- eventuale `_imports/<skill>/<executor>` ancora ammesso dal loader durante la
  migrazione.

L'helper deve restituire almeno percorso, classe di origine e nome atteso. Il
loader può mantenere i propri controlli di abilitazione e collisione; il linter
e il traduttore devono però condividere la stessa topologia del filesystem.

Non è corretto importare `_iter_executor_dirs` dal loader: porterebbe nel
dev-tooling firma, configurazione e altri effetti collaterali. La direzione KISS
è estrarre soltanto l'enumerazione neutra in un modulo comune.

### 7.2 Modello delle superfici testuali

Il linter deve trasformare il manifest in record immutabili concettualmente
equivalenti a:

```text
TextSurface(
    resource="description" | "args.<name>.description",
    language="it" | "en" | <altra lingua>,
    text=<stringa esatta>,
    transient=<bool>
)
```

Regole:

- per un manifest su disco, si controllano tutte le chiavi lingua con valore
  stringa non vuoto;
- la presenza obbligatoria di `it` ed `en` resta responsabilità dello standard
  executor e non viene diagnosticata due volte;
- eventuali lingue aggiuntive sono controllate automaticamente;
- una descrizione flat è ammessa soltanto nell'oggetto transitorio dello stage
  Synt, con lingua sorgente passata esplicitamente dal chiamante;
- il linter non usa la catena di ripiego del loader durante l'authoring: deve
  controllare la risorsa esatta, non nascondere una lingua mancante dietro `en`.

### 7.3 Tre passaggi distinti

| Passaggio | Frequenza | Esempi |
|---|---|---|
| globale | una volta per manifest | affinity, catalogo, schema/output, coerenza generale |
| locale | una volta per superficie lingua | capitoli, budget, pattern, runtime arg, output, riferimenti |
| trasversale | una volta per gruppo di traduzioni | parità di chiamate, argomenti, segnaposto e riferimenti |

Questa separazione impedisce il raddoppio degli avvisi affinity e rende esplicito
quando un problema appartiene soltanto a `en` o a `args.foo.description[fr]`.

### 7.4 Struttura dei finding

`Finding` deve portare campi strutturati, non affidarsi al parsing del messaggio:

```text
check       identificatore stabile della regola
severity    error | warn
resource    description | args.<name>.description | manifest
language    codice lingua oppure null per controlli globali
message     spiegazione leggibile
evidence    valori misurati o atomi divergenti, in forma limitata
```

Per la lunghezza si emette un solo finding per risorsa, con tutte le lingue che
superano il limite e le misure delle altre. Esempio concettuale:

```text
length args.to_user.description: max=181>180; it=180, en=181
```

In questo modo il totale dei problemi non raddoppia artificialmente, ma resta
possibile filtrare per lingua.

### 7.5 Controlli globali

Devono essere eseguiti una sola volta:

- sovrapposizione affinity;
- esistenza del nome nel contesto previsto;
- validità della forma globale di output usata come riferimento;
- consistenza fra proprietà e `required` già delegata allo standard;
- inventario e collisioni, quando forniti dal chiamante.

Il linter non deve duplicare i controlli di autorità, firma, lifecycle o
capability di `executor_standard.py`.

### 7.6 Controlli locali per lingua

Per ogni descrizione principale:

- capitoli invarianti `SCOPO:`, `PATTERN:`, `NON:`, `OUT:` presenti e ordinati;
- chiamata dell'executor presente nel `PATTERN`;
- argomenti top-level della chiamata appartenenti allo schema o agli universali;
- nessun argomento runtime-owned passato nella chiamata;
- posizione di `PATTERN` e `NON` rispetto ai budget;
- lunghezza della testa e della descrizione completa;
- riferimenti nel `NON:` risolti nel catalogo completo;
- forma `entries`/`results` o output purpose-specific coerente;
- assenza di segnaposto corrotti o parziali.

Per ogni descrizione argomento:

- lunghezza editoriale;
- conservazione di tipo, esempio, default e vincoli macchina quando espressi in
  forma strutturata riconoscibile;
- nessun tentativo di tradurre nomi di argomento o token riservati;
- nessuna riscrittura o troncamento automatico.

### 7.7 Controlli trasversali fra lingue

Il confronto deve essere stretto sugli atomi macchina e prudente sulla prosa.

**Errori deterministici bloccanti per nuovi/toccati:**

- nome della funzione chiamata diverso o assente;
- insieme degli argomenti top-level del `PATTERN` diverso;
- segnaposto `${RUNTIME:...}` o `{{...}}` mancanti, aggiunti o modificati;
- token di piping come `from_step` alterati;
- riferimento a un executor canonico sostituito da un nome inesistente;
- campo macchina dell'output rimosso dalla traduzione quando è espresso come
  identificatore riconoscibile.

**Avvisi, da promuovere solo dopo corpus ed evidenza:**

- insieme dei riferimenti validi nel `NON:` diverso fra due lingue;
- esempi numerici, unità o wildcard differenti;
- differenza fra `entries` e `results` non già catturata dallo schema;
- rapporto di lunghezza estremo;
- una variante molto più verbosa delle altre.

Non devono essere confrontati con uguaglianza lessicale:

- sinonimi;
- ordine naturale delle frasi;
- articoli e morfologia;
- numero di parole;
- similarità embedding;
- valutazioni libere del significato.

### 7.8 `runtime_resolved`: eliminare il falso positivo linguistico

La regola corrente cerca il nome dell'argomento come parola nella prosa. Questo
è affidabile per identificatori come `spreadsheet_id`, ma non per parole inglesi
comuni come `actor`, `client`, `account` o `provider`.

La nuova regola deve distinguere tre casi:

1. `actor=` nel `PATTERN`: **errore certo**;
2. identificatore in forma codice, backtick, assegnazione o elenco argomenti:
   **errore**, salvo contesto esplicito di omissione;
3. parola naturale omonima, per esempio «current actor»: **avviso ambiguo** o
   astensione, non errore bloccante.

I marker di omissione devono essere organizzati per lingua (`it`, `en`, future
lingue supportate). Per una lingua senza lessico, il linter applica i controlli
strutturali certi e si astiene dal giudizio semantico sul contesto. Non deve
usare il lessico inglese come ripiego universale.

Questa regola evita di cambiare la buona prosa di `get_location` soltanto per
soddisfare un'espressione regolare troppo larga.

### 7.9 Politica delle lunghezze

I limiti devono essere verificati per ogni lingua, ma restano separati dal
budget dinamico del pool.

- `HEAD_MAX`: limite editoriale della testa completa per singola lingua;
- `DESC_MAX`: limite della descrizione completa per singola lingua;
- `ARG_DESC_MAX`: obiettivo editoriale per ogni descrizione argomento;
- budget del renderer: vincolo operativo della superficie mostrata al modello;
- misure token: metrica di prova, non regola deterministica del linter.

Usare il massimo delle lunghezze linguistiche per decidere se la risorsa passa
è equivalente a richiedere che passino tutte le lingue. Il messaggio deve però
mostrare le singole misure, non soltanto il massimo.

Il conteggio in caratteri è coerente con il renderer corrente, che taglia per
caratteri e confine di parola. Per lingue con segmentazione molto diversa
dall'italiano e dall'inglese non basta a stimare il costo token. Una futura
lingua di questo tipo richiede un benchmark tokenizer separato, senza rendere il
linter dipendente dal modello installato.

I 108 avvisi italiani e 85 inglesi del catalogo completo non autorizzano
accorciamenti di massa. Le descrizioni argomento possono influenzare
`args_extractor`; ogni intervento resta per famiglia, con equivalenza verificata.

### 7.10 Modellare la superficie realmente visibile

`manifest_lint._visible_to_llm()` oggi usa un taglio grezzo a 260 caratteri.
Il renderer taglia a confine di parola e, nel working tree, può distribuire un
residuo fino a un limite superiore. Le due implementazioni possono quindi
divergere per un executor futuro oltre budget.

Il linter deve usare gli stessi helper puri di `manifest_rules.py` per calcolare
la superficie visibile. Tuttavia i controlli di sicurezza su un argomento
runtime-owned devono ispezionare tutta la testa potenzialmente visibile fino al
limite superiore, non soltanto il budget medio.

Il renderer di pool dipende dall'insieme dei tool. Il linter per-manifest non
deve simulare una composizione favorevole per assolvere una testa lunga. La
regola editoriale resta per singolo manifest; i test di pool dimostrano in
aggiunta boundedness ed equivalenza sul catalogo reale.

### 7.11 Transazione di traduzione appena prima della scrittura

Il punto corretto per fermare una traduzione difettosa è dopo aver ottenuto il
testo candidato e **prima** di modificare file, stato o firma:

```text
leggi manifest + lang_state
  -> determina in modo non ambiguo la sorgente
  -> traduci in memoria
  -> costruisci il manifest candidato in memoria
  -> linter locale + confronto trasversale
  -> se errore: conserva integralmente manifest/stato/firma correnti
  -> se valido: scrivi manifest e stato come una sola pubblicazione
  -> firma
  -> verifica firma e hash finali
```

Il traduttore non deve applicare sostituzioni al file e scoprire il difetto
dopo. Un fallimento del linter è un risultato tipizzato e ritentabile; non deve
essere mascherato come `noop` o traduzione riuscita.

### 7.12 Protocollo `lang_state`

Il companion deve distinguere:

- fotografia del testo pubblicato;
- lingua sorgente dell'ultima traduzione;
- hash della sorgente;
- modifica locale ancora da propagare;
- conflitto con più lingue modificate.

Politica raccomandata:

- zero lingue cambiate: nessuna azione;
- una lingua cambiata: quella è la sorgente;
- più lingue cambiate e invarianti macchina uguali: non scegliere
  alfabeticamente; accettare entrambe come modifiche intenzionali soltanto se
  l'operazione di authoring lo dichiara, altrimenti stato `multi_source_conflict`;
- risorsa nuova già completa in IT/EN: adottare entrambe, non ritradurne una;
- placeholder: errore di pubblicazione per un artefatto attivo;
- lingua mancante: tradurre dalla sorgente dichiarata, non dalla prima chiave
  alfabetica.

Il signer non può semplicemente aggiornare tutti i `version_hash`: cancellerebbe
l'informazione necessaria a capire quale lingua è stata modificata. Serve un
unico helper di pubblicazione che riceva la sorgente o classifichi esplicitamente
il conflitto prima della firma.

### 7.13 Profili di severità

Una stessa regola deve poter operare con profili chiari:

| Profilo | Uso | Errori | Avvisi |
|---|---|---|---|
| `audit` | catalogo esistente | riportati | riportati, non bloccanti |
| `candidate` | Synt prima della completezza bilingue | bloccano solo difetti certi della lingua sorgente | riportati |
| `translation` | candidato tradotto in memoria | difetti locali e parità macchina bloccano la scrittura | riportati |
| `active_on_touch` | firma/promozione di manifest standard | errori certi bloccano | politica strict esplicita |

Non deve esistere un `--strict` che cambia soltanto il conteggio. La severità
effettiva va resa visibile nella singola riga e nel risultato strutturato.

### 7.14 Punti di integrazione

| Componente | Integrazione proposta |
|---|---|
| `manifest_lint.py` | motore puro multilingue e risultato strutturato |
| inventario comune | scoperta core, builtin, utente diretto e import annidati |
| `synt_multistage.py` | profilo `candidate` sulla lingua sorgente |
| `generated_executor_contract.py` | richiamo comune dopo rendering del candidato completo |
| `skill_codegen.py` | profilo bilingue prima della prima firma/installazione |
| `i18n_translator.py` | profilo `translation` sul candidato in memoria |
| `sign.py` | profilo `active_on_touch`, errori certi soltanto |
| `loader.py` | nessun nuovo blocco editoriale; solo audit opzionale e standard esistente |
| CLI/test | inventario completo e filtri per origine/lingua |

Il loader non deve diventare il primo punto in cui una nuova regola editoriale
rende indisponibile un executor già firmato. La qualità si applica prima della
pubblicazione; il caricamento continua a verificare contratto, firma e standard.

## 8. Impatto macro

### 8.1 Affidabilità del prodotto

L'impatto positivo maggiore è sul confine lingua→vocabolario chiuso. Il planner
riceverà la stessa grammatica di chiamata in ogni lingua, riducendo richieste di
input spurie, argomenti inventati e instradamenti divergenti.

La modifica non migliora da sola la qualità semantica delle descrizioni. Evita
però che una traduzione formalmente fluida corrompa gli atomi che rendono
eseguibile il contratto.

### 8.2 Sicurezza e autorità

Il linter non concede capacità e non cambia il sandbox. Rafforza indirettamente
la sicurezza impedendo che una traduzione suggerisca al modello argomenti
runtime-owned, provider o bersagli non previsti.

Non deve però irrigidire il sistema sulla base di parole naturali ambigue. Un
falso positivo su `actor` non è una violazione di autorità: il vero controllo
resta lo schema model-facing che nasconde l'argomento e il runtime che lo
inietta. La severità deve riflettere la certezza dell'evidenza.

### 8.3 Manutenibilità

Un inventario e un motore centrali riducono divergenza fra tre generatori,
traduttore, signer e CLI. Una modifica a una regola testuale viene recepita nei
punti di pubblicazione senza copiare template o espressioni regolari.

La centralizzazione non deve trasformarsi in un modulo monolitico che ingloba
firma, standard e traduzione. Le responsabilità restano separate e vengono
composte in un piccolo orchestratore di validazione.

### 8.4 Esperienza di sviluppo

L'autore vede il percorso esatto, la lingua e la misura. Non deve eseguire due
comandi né interpretare totali raddoppiati. I legacy restano utilizzabili; i
nuovi errori certi vengono fermati sul componente toccato.

Il caso corrente degli import dimostra il valore pratico: tre errori di pattern
diventano visibili senza scandire manualmente directory diverse.

### 8.5 Prestazioni

Il linter resta fuori dai turni. Il costo attuale stimato di circa 20 ms per un
controllo bilingue esteso è irrilevante rispetto a generazione e traduzione LLM.
L'inventario deve essere calcolato una volta per comando e riusato per tutti i
manifest, soprattutto per riferimenti `NON:` e affinity.

### 8.6 Evoluzione a nuove lingue

La struttura proposta accetta automaticamente nuove chiavi lingua. I controlli
puramente sintattici funzionano senza codice dedicato. Soltanto i giudizi che
usano parole naturali, come i marker di omissione, richiedono un lessico
esplicito e devono astenersi quando manca.

Questo permette elasticità senza dichiarare falsamente che ogni euristica IT/EN
sia universale.

## 9. Impatto micro sul codice

### 9.1 `runtime/manifest_lint.py`

Modifiche previste:

- sostituire `_description_text()` con enumerazione delle superfici;
- dividere `lint_manifest()` in passaggio globale, locale e trasversale;
- arricchire `Finding` con `resource` e `language`;
- usare helper del renderer invece di slicing duplicato;
- rendere il catalogo un input già materializzato;
- correggere la presentazione `--strict`;
- mantenere API transitoria per la descrizione flat Synt con lingua esplicita.

Rischio principale: alterare il numero o la severità dei finding usati da Synt.
Mitigazione: testare il profilo sorgente attuale e introdurre il bilingue prima
in sola osservazione.

### 9.2 Inventario comune

Un modulo piccolo deve contenere solo percorsi e visita delle strutture
riconosciute. Non deve importare `loader.py`, verificare firme o consultare
credenziali. I chiamanti decidono quali origini includere.

Rischio principale: scansionare artefatti ritirati o disabilitati come attivi.
Mitigazione: ogni record porta origine e stato; il CLI può mostrare tutto,
mentre traduttore e ammissione applicano filtri espliciti.

### 9.3 `runtime/i18n_translator.py`

Modifiche previste:

- usare l'inventario comune;
- costruire il TOML candidato in memoria;
- applicare linter locale e trasversale prima della scrittura;
- non scegliere alfabeticamente una sorgente multipla;
- non lasciare manifest, state e firma parzialmente allineati;
- riportare `lint_rejected`, `multi_source_conflict` e `state_placeholder`.

Rischio principale: fermare traduzioni che oggi verrebbero applicate. È un
arresto sicuro e visibile, preferibile a firmare una superficie corrotta.

### 9.4 Generatori e promozione

I tre percorsi restano distinti per input e lifecycle, ma invocano la stessa
funzione di validazione del candidato. I template restano elastici sulla prosa e
vincolanti sugli atomi core.

Rischio principale: un generatore monolingue non può superare subito il profilo
active. Mitigazione: profilo `candidate` alla sorgente, traduzione in memoria,
poi profilo bilingue prima della promozione.

### 9.5 `runtime/sign.py`

La firma deve rifiutare soltanto errori certi per un manifest standard toccato.
Gli avvisi legacy restano visibili ma non impediscono manutenzione non
correlata. Il signer non deve decidere da solo la lingua sorgente.

Rischio principale: rendere impossibile rifirmare un manifest a causa di una
nuova euristica incerta. Mitigazione: soltanto regole strutturali ad alta
precisione nel profilo bloccante; le nuove euristiche iniziano come avvisi.

### 9.6 Test

Le prove esistenti sono prevalentemente sul parser di argomenti del `PATTERN` e
sui budget. Manca una matrice sistematica lingua×origine×lifecycle. La roadmap
prevede di aggiungerla senza moltiplicare copie dello stesso caso.

## 10. Rischi e contromisure

| Rischio | Probabilità | Impatto | Contromisura |
|---|---|---|---|
| falsi positivi su parole naturali come `actor` | alta senza redesign | alto: manifest inutilmente riscritto o bloccato | distinguere codice, pattern e omonimia naturale |
| raddoppio di affinity e avvisi globali | alta con ciclo ingenuo | medio | passaggio globale unico |
| linter e renderer descrivono superfici diverse | media | alto | helper di rendering condivisi e test di equivalenza |
| traduzione valida linguisticamente ma pattern corrotto | concreta | alto | controllo candidato prima della scrittura |
| aggiornamento parziale manifest/state/firma | concreta | alto | pubblicazione transazionale e verifica finale |
| sorgente scelta alfabeticamente fra due edit | concreta | alto | stato di conflitto, sorgente esplicita |
| import annidati invisibili | già presente | alto | inventario comune |
| placeholder `lang_state` persistenti | già presente | medio/alto | vietarli negli artefatti attivi |
| nuovi controlli bloccano legacy non toccati | media | alto | audit non bloccante e profilo on-touch |
| warning di lunghezza induce bonifica massiva | media | alto sulla semantica | nessun autofix; interventi per famiglia con corpus |
| confronto trasversale pretende traduzioni letterali | media | medio | confrontare solo atomi macchina |
| lingua futura senza marker lessicali | alta nel tempo | medio | astensione sulle euristiche non supportate |
| linter nel loader aumenta fragilità di avvio | media se collocato male | alto | applicarlo ai confini di pubblicazione, non ai turni |
| aggiornamento del linter cambia l'ammissione globale | media | alto | regole versionate nei report e introduzione warn-first |
| test solo sintetici non vedono regressioni di routing | alta | alto | corpus IT/EN e turni reali controllati |

## 11. Alternative considerate

### 11.1 Conservare il linter solo italiano

Respinta. Ha già due falsi negativi di lunghezza e non copre un errore inglese.
Il loader può usare l'inglese come ripiego, quindi la variante non è meramente
documentale.

### 11.2 Lintare soltanto la lingua attiva dell'istanza

Respinta come controllo di authoring. Un cambio `METNOS_LANG` o un'installazione
diversa renderebbe attiva una superficie mai validata. Può essere utile come
vista di diagnosi, non come criterio di completezza.

### 11.3 Applicare ogni controllo a ogni lingua

Respinta. Duplicherrebbe affinity e altri finding globali, gonfierebbe i totali
e non confronterebbe le traduzioni.

### 11.4 Usare un LLM per giudicare l'equivalenza

Non ammessa come blocco principale. È costosa, non riproducibile e può dare
falsa sicurezza. Un valutatore LLM separato può produrre avvisi editoriali su
un campione, ma non sostituisce gli invarianti deterministici.

### 11.5 Correggere automaticamente i manifest

Respinta. Accorciamento e riscrittura possono alterare routing,
normalizzazione degli argomenti e confini `NON:`. Il linter diagnostica; una
correzione resta esplicita, firmata e testata.

### 11.6 Eseguire il linter soltanto nel loader

Respinta. Scoprire un difetto al riavvio è troppo tardi e rende un aggiornamento
del linter capace di oscurare executor già firmati. Il controllo corretto è
appena prima della pubblicazione.

### 11.7 Aumentare i limiti perché l'inglese è in media più corto

Respinta. La media non descrive i singoli campi e i limiti influenzano l'intero
pool. Qualunque revisione dei budget richiede benchmark separato, non deriva
dal supporto multilingue.

## 12. Piano di realizzazione

### F0 — Congelamento e corpus

- nessun edit ai manifest;
- isolare le modifiche sperimentali del renderer dalla modifica al linter;
- fotografare i 115 manifest, le 230 descrizioni principali e i 654 argomenti;
- registrare finding per origine e lingua;
- costruire casi sintetici per errori soltanto EN e soltanto IT;
- fissare i tre errori importati come casi attesi dell'audit, non correggerli
  dentro questa fase.

**Uscita:** baseline riproducibile e nessuna modifica di prodotto.

### F1 — Motore multilingue in sola osservazione

- introdurre `TextSurface` e finding strutturati;
- separare controlli globali/locali/trasversali;
- controllare tutte le lingue presenti;
- aggiungere inventario core+builtin+import;
- mantenere invariati i codici di uscita dei percorsi produttivi;
- correggere soltanto la presentazione incoerente di `--strict`.

**Uscita:** rapporto completo, nessun nuovo blocco e nessun manifest modificato.

### F2 — Blocco delle traduzioni difettose

- costruire il candidato in memoria;
- applicare controlli locali e trasversali;
- lasciare intatti file e firma su errore;
- introdurre stati tipizzati del companion;
- eliminare la scelta alfabetica nei conflitti multipli;
- coprire gli import annidati.

**Uscita:** una traduzione che cambia un argomento o segnaposto viene rifiutata
prima di qualunque scrittura.

### F3 — Adozione comune nei generatori

- collegare i tre percorsi al validatore comune;
- profilo sorgente per candidati monolingui;
- profilo bilingue prima della promozione;
- nessun template duplicato per lingua;
- nessuna possibilità di firmare placeholder attivi.

**Uscita:** ogni nuovo executor applica automaticamente la politica centrale.

### F4 — Firma on-touch e pulizia mirata

- bloccare in firma soltanto gli errori strutturali certi;
- mantenere gli avvisi legacy non bloccanti;
- correggere separatamente i tre pattern importati con test propri;
- risolvere i companion inconsistenti senza scegliere arbitrariamente una
  lingua sorgente;
- rieseguire due cicli completi consecutivi.

**Uscita:** nessun errore certo nel catalogo attivo e nessuna regressione nei
flussi d'oro.

### F5 — Estensione oltre IT/EN

- aggiungere una lingua soltanto con corpus e traduttore verificati;
- definire marker lessicali o astensione esplicita;
- misurare token e segmentazione;
- mantenere invarianti gli atomi macchina.

**Uscita:** la nuova lingua non richiede una copia del linter e passa le stesse
prove strutturali.

## 13. Piano di test

### 13.1 Prove unitarie

- errore di `PATTERN` presente soltanto in inglese;
- errore presente soltanto in italiano;
- argomento annidato in dict non scambiato per argomento top-level;
- `runtime_resolved` passato come keyword: errore;
- `runtime_resolved` in backtick senza omissione: errore;
- `runtime_resolved` come parola naturale omonima: non errore bloccante;
- marker di omissione IT ed EN;
- lingua sconosciuta: controllo strutturale e astensione lessicale;
- una sola emissione affinity per manifest;
- una sola emissione di lunghezza per risorsa con misure per lingua;
- parità di `${RUNTIME:...}`, `{{...}}`, executor e argomenti;
- `--strict` con etichetta e totale coerenti.

### 13.2 Prove di inventario

- core;
- builtin;
- executor utente diretto;
- `skills/<skill>/<executor>`;
- `_imports/<skill>/<executor>` se ancora supportato;
- directory ritirata esclusa dal profilo attivo ma visibile nell'audit;
- skill disabilitata classificata senza confonderla con core.

### 13.3 Prove del traduttore

- candidato valido scritto e firmato;
- argomento del `PATTERN` tradotto: nessuna scrittura;
- segnaposto perso: nessuna scrittura;
- errore dopo chiamata LLM: manifest/state/firma byte-identici;
- una lingua editata: sorgente corretta;
- due lingue editate: conflitto, nessuna scelta alfabetica;
- risorsa nuova IT+EN: entrambe adottate;
- placeholder state: pubblicazione rifiutata o inizializzazione esplicita;
- import annidato visitato una sola volta;
- retry idempotente.

### 13.4 Prove dei generatori

- Synt monolingue passa `candidate` ma non `active`;
- traduzione completa passa il profilo bilingue;
- import con pattern/schema divergenti viene fermato;
- proposta promossa usa lo stesso validatore;
- variazione ricca della prosa resta ammessa quando gli atomi core sono validi;
- template non può sovrascrivere la politica di esecuzione o lo standard.

### 13.5 Prove sul catalogo reale

- 115/115 manifest scoperti;
- 230/230 descrizioni principali controllate;
- 654/654 descrizioni argomento censite;
- zero divergenze attuali nelle quattro firme strutturali IT/EN;
- conteggi di baseline spiegabili per origine e lingua;
- nessuna modifica ai file durante `audit`;
- nessuna testa corrente alterata dal rendering adattivo;
- tempo del controllo completo registrato.

### 13.6 Prove di non regressione del runtime

Con linter disattivo e attivo soltanto ai confini di authoring devono essere
identici:

- catalogo caricato;
- firme dei piani;
- pool del proposer;
- ordine degli executor;
- argomenti estratti;
- richieste di consenso;
- effetti e output finali;
- tempi dei turni entro il rumore di misura.

### 13.7 Prove live

Dopo l'eventuale implementazione, non durante questa analisi:

- una query italiana che usa un executor con argomento runtime-owned;
- equivalente inglese;
- una query multidominio IT con almeno file, messaggi e calendario;
- equivalente EN o un sottoinsieme semanticamente controllato;
- un executor importato GitHub dopo correzione del suo pattern;
- riavvio e verifica catalogo soltanto a turno concluso.

## 14. Criteri di arresto e rollback

La promozione si arresta se accade uno dei seguenti eventi:

- un manifest non toccato diventa non caricabile;
- il numero di executor attivi cambia per il solo aggiornamento del linter;
- una traduzione rifiutata modifica comunque manifest, state o firma;
- una parola naturale genera un errore bloccante non strutturale;
- affinity o altri finding globali sono duplicati per lingua;
- il linter e il renderer producono superfici diverse nei casi entro budget;
- il traduttore sceglie una sorgente senza evidenza;
- un import attivo conserva placeholder di stato dopo pubblicazione;
- un avviso di lunghezza porta a un accorciamento automatico;
- un corpus IT/EN cambia piano senza che sia stato modificato il manifest
  relativo.

Il rollback consiste nel disattivare i nuovi punti di blocco mantenendo il
motore in modalità `audit`. Non richiede ripristino dei manifest perché la
prima fase non li modifica e la fase di traduzione conserva il precedente
artefatto fino alla pubblicazione completa.

## 15. Criteri di completamento

RM-0002 può passare a `implemented` soltanto quando:

- esiste un solo inventario condiviso per tutte le topologie ammesse;
- il linter controlla ogni lingua presente e non privilegia `it`;
- i controlli globali sono eseguiti una volta;
- i finding indicano risorsa e lingua;
- la parità degli atomi macchina è verificata deterministicamente;
- `get_location.actor` non è un falso errore bloccante;
- gli errori dei tre pattern importati sono rilevati prima della pubblicazione;
- il traduttore valida in memoria e non scrive su errore;
- `lang_state` non contiene placeholder negli artefatti attivi;
- un conflitto con più lingue modificate non sceglie alfabeticamente;
- i tre generatori usano lo stesso punto comune;
- firma e loader conservano disponibilità e semantica attuali;
- nessun manifest viene accorciato automaticamente;
- il catalogo attivo passa due cicli completi consecutivi;
- corpus IT/EN e prove live non mostrano regressioni di routing o argomenti;
- tempi e conteggi finali sono registrati in un rapporto di implementazione;
- l'indice anti-regressione e l'ADR pertinente vengono aggiornati soltanto dopo
  che il comportamento è realmente attivo.

## 16. Non-obiettivi

RM-0002 non autorizza:

- modifica dei 115 manifest osservati;
- correzione immediata dei tre import GitHub;
- riscrittura delle descrizioni legacy;
- modifica dei limiti 240/320/180;
- promozione del renderer adattivo sperimentale;
- esposizione delle descrizioni argomento nel proposer attivo;
- traduzione delle affinity, che restano lista mista IT+EN;
- introduzione di una dipendenza LLM nel linter;
- supporto dichiarato a nuove lingue senza corpus;
- modifica del loader per rifiutare nuovi avvisi editoriali al boot;
- deploy di documentazione pubblica.

## 17. Decisioni raccomandate

Le seguenti scelte sono sufficientemente supportate dall'analisi:

1. realizzare il linter come motore unico multilingue;
2. controllare tutte le lingue presenti, non soltanto `it`/`en` hardcoded;
3. mantenere `it` ed `en` obbligatorie nello standard active corrente;
4. eseguire una sola volta i controlli globali;
5. confrontare deterministicamente soltanto atomi macchina;
6. trattare la menzione naturale di un argomento runtime comune come ambigua,
   non come errore certo;
7. raggruppare gli avvisi di lunghezza per risorsa;
8. applicare i blocchi appena prima di pubblicazione, traduzione e firma;
9. non aggiungere il linter editoriale al percorso ordinario dei turni;
10. estrarre un inventario neutro condiviso;
11. sostituire la scelta alfabetica della sorgente multipla con un conflitto
    esplicito;
12. non mescolare questa implementazione con gli esperimenti del renderer.

## 18. Registro di avanzamento

| Data | Stato | Evento | Prove |
|---|---|---|---|
| 2026-07-23 | `active` | analisi macro/micro e creazione della roadmap | codice e 115 manifest ispezionati; nessun manifest modificato |
