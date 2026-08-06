# Accrescimento del planner — analisi sistemica (6/8/2026)

> **Stato: ANALISI COMPLETA, nessuna decisione presa, nessun codice toccato.**
> Documento di traccia incrementale: scritto man mano che l'analisi avanzava,
> così che una sessione successiva (anche con un modello meno capace) possa
> riprendere senza rifare le misure. Ogni sezione dichiara se è
> MISURATA (dato verificabile, con il comando che lo produce), DEDOTTA
> (ragionamento sopra i dati) o PROPOSTA (non ancora decisa da Roberto).
> **Chi riprende: leggere §0 (la versione semplice), poi §7 (stato).** §1-§6
> sono la prova e le misure: si scende lì solo per verificare o per fare.
>
> Domanda di partenza (Roberto, 6/8): *«il planner si sta gonfiando, quali
> strategie per gestire l'accrescimento?»* — precisata poi in *«le guardie sono
> case-by-case, è pericoloso, crescita indefinita»* e *«ideale, ma da
> verificare, è passare a case-by-rule; ma deve produrre una qualità almeno
> comparabile»*. §5 risponde a quest'ultima.
>
> Precedenti: `audit_metnos_multidominio_21_7.md` §T3 · ADR 0177 (revisione
> architettura motore) · consegna `project_session_6_8_2026_handover.md`.

---

## 0. La versione semplice (leggere questa; il resto è la prova)

Roberto, 6/8, dopo la prima stesura: **«più semplice»**. Aveva ragione: §4 e §5
proponevano di *costruire* un motore di regole, cioè di rispondere a «troppa
meccanica» con altra meccanica. Restano come fondo e come prova, non come primo
passo. La versione semplice è questa.

**Non serve un motore di regole. Servono tre cose piccole e una tabella.**

1. **Chiudere il cricchetto.** L'esenzione dalla conformazione allo schema vale
   per la coppia `(tool, arg)`, non per il nome nudo su tutti i tool. Poche
   righe. Dopo, aggiungere una guardia non allarga più il buco: 34 guardie
   restano brutte, ma smettono di essere **pericolose** (§3.6).
2. **Un solo `insert_steps()`.** Chi inserisce uno step non rimappa più a mano:
   una funzione fa tutte le rimappe, un lint vieta `steps.insert` diretto.
   Chiude la classe-bug T3 dell'audit 21/7 (§3.5).
3. **Accendere il contatore di sparo.** Oggi esiste ed è spento, quindi nessuna
   guardia si può ritirare, quindi il numero può solo salire. Acceso, nasce il
   **meno uno** che non è mai esistito: una guardia che non spara si spegne in
   quarantena e si guarda se l'errore torna (§3.2, §3.4).

Poi — **solo se serve, e senza linguaggi nuovi** — il risparmio vero. Le 10
guardie della famiglia «casi puri» stanno dentro 7 proprietà (§5.2):

- **5 non vanno riscritte: vanno cancellate.** La proprietà è già applicata da
  un componente che il progetto ha già (`coerce_args`, `arg_provenance`,
  indipendenza delle clausole, collocazione degli arg, §2.2). Il caso va
  ricondotto lì, non riespresso altrove.
- **1 è una tabella**: concetto→argomento, tipo→campo, modalità→produttore.
  Una guardia sola la legge; i casi nuovi diventano righe. Una riga non porta
  vincolo d'ordine, non porta esenzione di schema, non porta rimappatura.
- **1 è una proprietà nuova e generale** (un'aggregazione su un contenitore si
  fa sui contenuti) e si merita di restare una guardia vera.

**La rete c'è già** e non va inventata: `test_provenance_equivalence.py` è un
oracolo byte-identico. Gli mancano solo i casi — 11 oggi contro 2353 piani reali
su disco (§5.3). Si migra una famiglia alla volta, con la guardia ancora accesa,
e la si cancella solo a uscita identica.

**Il freno**, per non ricominciare fra due mesi: il test di contratto già
fallisce a ogni guardia aggiunta (§3.1). È lì che si scrive il tetto — sopra
quel numero non si aggiunge una guardia, si aggiunge una riga.

I passi 1-3 non cambiano il comportamento del planner e valgono da soli.

### 0.2 «Le due alternative in parallelo, e poi si decide» — sì, e manca UNA cosa

Roberto, 6/8: *«se capisco in parallelo le due alternative e poi si decide?»*.
È la forma giusta e lo strumento **ora esiste**: `snapshot_plans.py` fotografa
l'uscita della pipeline su 2424 piani reali; una regola nuova si scrive accanto
alla guardia e si confronta piano per piano. Si è già dimostrato oggi su una
correzione vera: **0 piani diversi su 2424** (§3.8).

Ma c'è un limite che va tolto prima, ed è piccolo: **i piani su disco sono
salvati DOPO le guardie**, quindi già riparati. Vanno benissimo per dire «la
regola non peggiora nulla»; non possono dire «la regola ripara quanto la
guardia», perché il danno da riparare nel corpus non c'è più.

→ **Passo 0 della sequenza: registrare anche il piano GREZZO** (uscita del
proposer prima della pipeline) accanto a quello eseguito. Poche righe in
`record_observation`, nessun cambio di comportamento, e da quel giorno il
corpus diventa la forma giusta per il confronto. Senza, le due alternative si
possono confrontare solo sui 74 piani della suite.

### 0.1 La semplice è migliore della prima? (domanda di Roberto: «il metro è la qualità»)

Risposta onesta: **su due assi sì, su uno no.** Non è una semplificazione
gratuita, ma nemmeno un miglioramento su tutta la linea.

| asse | prima stesura (§4-§5) | versione semplice (§0) |
|---|---|---|
| qualità dei piani prodotti | oracolo byte-identico | **identico**: stesso cancello, stessa rete |
| rischio dell'intervento | migrazione per famiglia, ognuna una superficie di regressione | **superiore**: i passi 1-3 non cambiano il comportamento, punto |
| capacità di FERMARE la crescita | strutturale: un caso nuovo può solo essere una riga | **inferiore**: la crescita diventa innocua, non si ferma |

**Sulla qualità dei piani sono pari per costruzione**, e non per fiducia: in
entrambe nulla si cancella finché l'uscita non è byte-identica sul golden. Il
metro non cambia perché cambia la soluzione.

**Dove la semplice è meglio.** La prima avrebbe *tradotto* in regole le 5
guardie la cui proprietà è già applicata altrove: duplicazione già esistente,
spostata in un TERZO posto, con in più il motore che la legge. La semplice le
cancella. E soprattutto non costruisce il sottosistema nuovo — il linguaggio
delle condizioni era il punto debole che §4.6 dichiarava già: «le azioni sono
facili, la complessità si nasconde nelle condizioni». Un motore di regole
costruito per contenere la crescita è a sua volta una cosa che cresce.

**Dove la semplice è peggio, e va detto.** Dopo i passi 1-3 un caso nuovo è
ancora una guardia nuova: innocua (niente cricchetto, niente rimappatura a
mano, e ritirabile perché il contatore è acceso) ma pur sempre un'aggiunta.
`dispatch.py` continua a crescere. E il freno diventa **procedurale** — un
tetto in un test — mentre nella prima era strutturale. I freni procedurali si
logorano: il test di contratto **esiste dal 5/7** (`a8f7ef00`) e da allora le
guardie sono passate **da 14 a 34**. Non ha frenato niente.

**Il buco dichiarato**: la famiglia F (i modelli di flusso, 1023 righe in un
solo commit — la crescita più grossa del periodo) nella prima aveva una
risposta (tabella di modelli firmati, §3.3/§5.5); nella semplice **non ce l'ha**.

**Conclusione.** La semplice è migliore come PRIMO passo e come rapporto
valore/rischio; non è una risposta completa. La via che tiene entrambe le cose:
fare 1-3, tenere il contatore acceso qualche settimana, e decidere sulla
famiglia F **con i dati invece che con l'architettura** — a quel punto si saprà
quali guardie sparano davvero, che è esattamente ciò che oggi non sappiamo.

---

## 1. Le misure (MISURATE)

### 1.1 Curva di crescita

`runtime/engine/dispatch.py`, righe totali per data:

| data | righe | note |
|---|---|---|
| 3/6 | 283 | nascita del modulo |
| 15/6 | 587 | |
| 19/6 | 1301 | compound engine v3 |
| 22/6 | 1916 | (audit ADR 0177 fotografa qui: «1916 LOC, 11 guard») |
| 1/7 | 2413 | |
| 6/7 | 3368 | nasce il registro `GUARD_PIPELINE` (15 guardie) |
| 10/7 | 3924 | |
| 13/7 | 4446 | sites F2 |
| 5/8 | 6792 | +2346 in UN commit (`f624d384`) |
| 6/8 | **7055** | 34 guardie |

Comando: `git log --format='%H|%ad' --date=short -- runtime/engine/dispatch.py`
poi `git show $h:runtime/engine/dispatch.py | wc -l`.

**25× in due mesi.** Non è deriva lenta: è crescita costante con due salti
(engine v3 a giugno, il commit del 5/8).

### 1.2 Composizione interna

Su 7055 righe e 104 funzioni:

- **3834 righe (54%) sono le 34 funzioni-guardia dirette.**
- `run_turn` = 660 righe (la funzione più grande, non è una guardia).
- Le **tre** guardie `normalize_*_report_pipeline` da sole = **1023 righe**
  (15% del modulo, 27% del codice-guardia).

Comando: script AST in §A.1 (in coda).

### 1.3 Come nasce una guardia

I messaggi di commit sono la prova diretta. Estratti letterali:

- `fix(§7.9): «quanto è grande la cartella X» = peso file ricorsivi` → guardia
  `route_folder_size` (133 righe)
- `fix(§7.9): «sposta i file DA cartella X» enumera (find_files→move)` → guardia
  `enrich_move_source_dir`
- `fix(§2.9): «cancella file E directory in X» scopa la clausola dirs` → guardia
  `scope_dirs_clause_to_contents`
- `feat(describe/health): ... query hardware robuste` → guardia `ensure_health_arg`

Diverse `rationale` nel registro citano l'identificativo del turno che le ha
generate (`turn 5cdf80d0`, `turn b66ec6f3`, `turn 1f1eb714`).

**Legge di crescita osservata: un turno reale fallito → una guardia nuova,
permanente.** Nessuna guardia è mai stata ritirata.

---

## 2. Diagnosi (DEDOTTA)

### 2.1 Il sospetto di Roberto è confermato: CASO invece di PROPRIETÀ

Classificazione delle 34 guardie per natura (dal campo `rationale` del registro,
`dispatch.py:5539-5760`):

| famiglia | n | righe~ | esempi |
|---|---|---|---|
| **A. Proprietà di dataflow/struttura** (generali, da tenere) | 9 | ~700 | `coerce_args_to_schema`, `conform_to_intent_order`, `resolve_store_field_refs`, `propagate_sink_schema_to_extract`, `enforce_complete_sink_cardinality`, `enforce_create_only_artifact_policy` |
| **B. Copertura dell'azione** (TRE proiezioni di UNA proprietà) | 3 | ~370 | `enforce_missing_clauses` (verbo), `enforce_missing_objects` (oggetto), `align_framework_action_pairs` (coppia) |
| **C. Legame provider** (quattro guardie, una proprietà) | 4 | ~200 | `enforce_provider_binding`, `align_provider_client`, `scope_sink_provider_to_clause`, `align_strong_affinity_producers` |
| **D. Precondizioni di dominio** (starebbero nel manifest) | 5 | ~700 | `ensure_site_session_precursor` (362 righe!), `route_mail_delete_to_trash`, `ensure_extract_clause`, `scope_dirs_clause_to_contents`, `enrich_move_source_dir` |
| **E. Casi puri** (una frase-query → una forma di piano) | 10 | ~600 | `route_folder_size`, `ensure_health_arg`, `normalize_result_folder_exclusion`, `overwrite_phantom_install_args`, `route_filename_pattern_to_find`, `degenerate_find_to_list`, `normalize_filter_operation_values`, `decontaminate_reader_qualifier`, `ensure_extracted_period_scope`, `route_text_web_image_search` |
| **F. Modelli di flusso** (piani-template travestiti da guardie) | 3 | **1023** | `normalize_document_report_pipeline`, `normalize_multisource_entity_report_pipeline` (468), `normalize_message_event_report_pipeline` (355) |

Solo la famiglia A è codice che *deve* stare in un motore di pianificazione.
Le altre cinque sono, ciascuna per una ragione diversa, **conoscenza messa nel
posto sbagliato**.

### 2.2 La causa radice: il piano non ha un sistema di tipi

Un piano è una lista di dizionari. Non esiste un modo per **dichiarare** «un
piano è valido se e solo se P» e ottenere che le violazioni siano impossibili
per costruzione o riparate da un solo meccanismo generico. Quindi ogni
proprietà desiderata diventa una **procedura** che ispeziona i dizionari e li
muta. Da qui, meccanicamente:

1. **Ogni proprietà nuova = una procedura nuova**, che va ordinata a mano
   contro le altre 33. Il registro `Guard` *dichiara* `reads`/`writes`, ma
   nessuno **verifica** che l'ordine dichiarato sia coerente con quelle
   dichiarazioni (→ verifica in §3.1). L'ordine è commento, non contratto
   eseguibile.
2. **Le proprietà si scoprono come casi** (un turno fallisce), quindi si
   scrivono come casi. Nessuno risale dal caso alla proprietà perché non c'è un
   posto dove una proprietà si scrive.
3. **Nessuna guardia si può ritirare**, perché nessuno sa se spara ancora
   (→ §3.2: lo strumento per saperlo ESISTE ed è spento).

### 2.3 Le due patologie già viste dal vivo (6/8)

Dalla consegna, osservate durante il lavoro sulla posizione:

- **due guardie che si correggono a vicenda** (l'affinity reintroduceva ciò che
  il gate provider aveva escluso) → in una pipeline sequenziale a ordine fisso,
  chi viene dopo vince, in silenzio. Non c'è modo di accorgersene se non da un
  turno sbagliato.
- **un buco fra due guardie che controllavano proiezioni diverse della stessa
  cosa** (verbo coperto, oggetto coperto, coppia scoperta) → esattamente la
  famiglia B.

Entrambe sono conseguenze dirette di §2.2, non incidenti indipendenti.

---

## 3. Verifiche (MISURATE)

### 3.1 L'ordine è FOTOGRAFATO, non derivato ✗

`tests/runtime/engine/test_guard_pipeline_contract.py` esiste e fa tre cose:

- `test_guard_pipeline_order_contract` — confronta l'ordine con una tupla
  d'oro `EXPECTED_PIPELINE` scritta a mano. È un **dosso**, non una verifica:
  «il fallimento del test è il momento in cui si ragiona sull'ordine» (docstring
  del test). Chi aggiunge una guardia aggiorna la tupla e va avanti.
- `test_writes_subset_of_observed_mutations` + `test_no_perclause_writes_before_
  crossclause_same_field` — questi sì derivano qualcosa dai metadati. Buoni.
- **T4 idempotenza**: `guards(guards(fw)) == guards(fw)`, su corpus reale.

**Il punto critico**: l'idempotenza NON è la confluenza. Due guardie che si
contendono lo stesso campo lasciano la catena perfettamente idempotente (l'ultima
vince, e vince stabilmente) ed è comunque il bug del 6/8 (l'affinity
reintroduceva ciò che il gate provider aveva escluso). **La proprietà verificata
non è la proprietà che serve.** Nessun test cerca l'interferenza fra guardie.

### 3.2 La misura d'uso esiste ed è SPENTA ✗

`METNOS_GUARD_FIRE_COUNT` (`dispatch.py:5784`) conta, per guardia, quante volte
ha davvero mutato il piano. Unico consumatore: `tests/benchmarks/grammar_args_ab.py`.
**In produzione è off.** Conseguenza: nessuno sa quali guardie sparino ancora,
quindi nessuna si può ritirare, quindi il contatore può solo salire.

Costo, misurato: la pipeline INTERA costa **0,53 ms su un piano a 4 step**
(16 µs a guardia). **La latenza non è il problema** — va detto, per non
sprecare lavoro dove non serve. Il costo dell'accrescimento è altrove (§3.6).

### 3.3 Autopath L1 NON può ospitare i modelli di flusso ✗ (correzione)

Verificato: uno scheletro L1 è il `Framework` intero serializzato
(`autopath.py:102-119`, `framework_json` = `Framework.to_dict()`). Esprime una
pipeline **lineare** multi-step con `from_step` e placeholder. **Non** esprime
condizioni sulla query, **non** rami, **non** propagazione di schema di colonne
(`types.py:40-46`, `:58-59`).

E tre barriere lo escludono comunque per la famiglia F:

- `is_query_specific` (`executor.py:1464`) rifiuta promozione **e** servizio se
  un arg letterale sta in `CONTENT_ARG_KEYS` (`paths`, `path`, `title`,
  `pattern` concreto…). Nominare il foglio di destinazione = piano non
  promuovibile.
- `_ungrounded_mutating_args` (`dispatch.py:249-301`) respinge ogni step
  `create_/write_/move_` i cui token non compaiono nella query corrente.
- `_should_cache_plan` (`dispatch.py:158`) esclude i piani con gate di consenso.

**Non esiste** alcuna via di inserimento manuale di una entry L1: le uniche
scritture sono `_promote_autopath` da riscontro umano e `seed_from_run`
(`autopath.py:654`, `:716`).

→ **La superficie dichiarativa per la famiglia F va costruita, non riusata.**
Una tabella di modelli **firmati** (il modello canonico è `reverse_pattern`
§2.3: catalogo chiuso + dichiarazione), non l'autopath.

*(Difetto trovato per strada, fuori tema ma da registrare: `seed_from_run` non
chiama `is_query_specific`, quindi semina righe che `lookup` scarterà per
sempre. La riga viva `describe_messages__fd456bf522c4` con `time_window:"today"`
è uno di questi zombie.)*

### 3.4 `executor_aging`: la forma si riusa, la METRICA no ✓/✗

**Riusabile**: il contatore `touch()` con upsert (`executor_aging.py:220-233`),
il registro-eventi append-only (`:79-90`), la UI `/admin/executors/stats`
(`http_routes_admin.py:783-819`, già polimorfa), il ritiro **reversibile**
(`undeprecate`, `:417-434` — usato 29 volte davvero), l'aggancio notturno
(`builtin_callbacks.py:37-46`, tre righe).

**E le guardie hanno un punto di raccolta MIGLIORE degli executor**:
`_apply_deterministic_structure_guards` è un loop unico che nessun percorso
aggira, e la strumentazione pre/post è già scritta (`dispatch.py:5786-5798`).
Basta persisterla invece di tenerla in un dizionario di processo.

**NON riusabile — ed è il punto delicato**: per un executor «30 giorni senza
chiamate» significa una cosa sola. Per una guardia, **zero spari ha due letture
opposte**:

- (a) il modello non produce più quell'errore → si ritira;
- (b) la guardia sta *prevenendo* l'errore, e il piano arriva sano proprio
  perché lei c'è → ritirarla lo fa ricomparire.

Nessun dato distingue (a) da (b). Serve un passo che gli executor non hanno:
**quarantena osservata** — si spegne la guardia e si verifica che il tasso
d'errore non risalga. Da progettare, non da copiare.

Inoltre la soglia in *giorni* è sbagliata: le guardie hanno frequenze di sparo
che differiscono di ordini di grandezza per costruzione (`coerce_args_to_schema`
spara quasi sempre, `route_folder_size` su una classe rara ma legittima). La
soglia va sul **traffico** (spari / piani osservati), non sul calendario.

**Precedente già nel repo**: la `rationale` di `overwrite_phantom_install_args`
(`dispatch.py:5556`) *è già* una regola di ritiro per inattività, scritta a
mano — «rimovibile con evidenza journal 0 fire su ≥14 giorni». L'invecchiamento
delle guardie si sta già facendo: manualmente, una alla volta, con un grep e
una data in un commento.

*(Difetto grave trovato per strada, DA APRIRE A PARTE: il punto di raccolta
degli executor **è rotto**. `touch()` viveva in `agent_runtime.py` ed è stato
cancellato il 4/7 col planner legacy (`af6c7b87`); l'engine v3 non l'ha mai
ricablato. Zero telemetria da turni reali da un mese, e `change_observer`
decide i rollback su numeri fermi. Va ricablato a `engine/executor.py:2624`.)*
### 3.5 T3 (helper unico di rimappatura) NON fatto ✗

Esiste `_remap_step_refs` (`dispatch.py:2124`) che rimappa args e
`final_message`, ma **non esiste** `insert_steps`: ogni guardia che inserisce
uno step costruisce la propria `idx_map` a mano, o non la costruisce affatto.

Colto sul fatto in `_route_folder_size` (ramo B):

```python
for s in steps:
    sfs = _args(s).get("from_step")
    if isinstance(sfs, int) and sfs >= idx + 1:
        s.args["from_step"] = sfs + 1
steps.insert(idx, comp)
```

Rimappa **solo** `from_step`. Non tocca `${stepN.field}` negli args, non tocca
`final_message` — e la docstring lo mette per iscritto («il template
final_message può essere STANTÌO … non serve toccarlo qui»). È esattamente la
classe-bug T3 dell'audit 21/7, viva e vegeta, in una guardia scritta DOPO
l'audit.

### 3.6 Il pericolo vero: il cricchetto sul backstop di schema 🚨

`_coerce_args_to_schema` (Guard #0, dichiarato «backstop UNICO sul confine
LLM→pipeline») costruisce l'insieme degli argomenti esenti dalla conformazione
allo schema unendo i `writes` di **tutte** le guardie, **per nome nudo**:

```python
owned = frozenset(w.split(".", 1)[1]
                  for g in GUARD_PIPELINE
                  for w in g.writes
                  if w.startswith("args.") and not w.endswith(".*"))
```

Oggi sono **21 nomi, esenti su OGNI tool**:

```
@presentation_limit, base_path, client, content_template, dst_folder,
exist_ok, fields, from_step, include_health, key, kind, mode, name_regex,
path, path_template, paths, pattern, queries, recursive, where_field,
where_regex
```

Cioè: `path`, `paths`, `pattern`, `mode`, `exist_ok`, `dst_folder` — dove
scrivere, che cosa cancellare, se sovrascrivere.

**Questo è il carattere di cricchetto della crescita.** Una guardia nuova che
deve scrivere `args.X` su UN tool esenta `X` su TUTTI i tool, per sempre.
L'audit 21/7 lo aveva già segnalato (T1, «restringere `guard_owned` alla coppia
(tool, arg)»), ma come difetto puntuale. Visto come problema di crescita è
peggio: **non è un buco, è un buco che si allarga da solo a ogni guardia
aggiunta.** È la risposta meccanica a «è pericoloso».


### 3.7 Gli spari, MISURATI (6/8 pomeriggio) — e la sorpresa sui test

Roberto: *«se capisco in parallelo le due alternative e poi si decide? vale
anche per i test, visto che sono così numerosi potrebbero dare un aiuto
statisticamente»*. Fatto. Sonda passiva (`scratchpad/guard_probe.py`) che
avvolge ogni `Guard.fn` e registra sparo + **percorsi cambiati**; nessuna
modifica al codice di prodotto. Due corpora:

| corpus | piani | cosa può dire | cosa NON può dire |
|---|---|---|---|
| `observations` + `fastpaths` | **2424** | convergenza, contesa, errori ingoiati | frequenza d'uso: i piani sono salvati DOPO le guardie, quindi già riparati |
| suite `tests/runtime` | **74** | casi rari su piani grezzi, con query e intent veri | statistica: sono 74, non 5540 |

**Prima sorpresa, e va detta perché smonta un'aspettativa: 5540 test
attraversano la pipeline solo 74 volte.** I test sono numerosi, i piani no.
L'aiuto che danno non è statistico — è di **copertura**: 7 guardie sparano
SOLO nella suite e 6 SOLO nel traffico reale. I due corpora non si sostituiscono.

**Convergenza — spari su piani già riparati, per mese del piano** (su un
pipeline a punto fisso questa colonna dev'essere zero):

| guardia | mag | giu | lug | **ago** |
|---|---|---|---|---|
| `align_framework_objects` | 13,3% | 18,4% | 4,3% | **0,0%** |
| `coerce_args_to_schema` | 16,2% | 15,0% | 6,1% | **0,0%** |
| `resolve_store_field_refs` | 0,0% | 11,1% | 1,1% | **0,0%** |
| `enforce_provider_binding` | 0,0% | 4,5% | 0,0% | **2,7%** |
| `align_framework_action_pairs` | 1,9% | 2,8% | 0,5% | **0,0%** |

Il calo monotòno dice che quegli spari sono **deriva del catalogo** (piano di
maggio contro mondo di agosto), non non-convergenza. **L'unica eccezione è
`enforce_provider_binding`**, che spara ancora sui piani di agosto — ed è la
stessa che compare nella contesa qui sotto. È lì che vale la pena guardare.

**Contesa misurata** (due guardie che nello stesso piano cambiano lo stesso
campo — la proprietà che l'idempotenza NON cattura, §3.1):

```
5  align_framework_objects -> enforce_provider_binding | steps[2].tool
2  align_framework_objects -> resolve_store_field_refs | steps[1].args.key[0]
2  align_framework_objects -> resolve_store_field_refs | steps[2].args.repo
2  align_framework_objects -> resolve_store_field_refs | steps[3].args.key[0]
1  align_framework_objects -> enforce_provider_binding | steps[1].tool
```

La prima riga **è** il difetto vivo del 6/8 (l'affinity che reintroduce ciò che
il gate provider aveva escluso), ora misurato invece che raccontato.

**Guardie mute in ENTRAMBI i corpora: 14 su 34.**

```
align_provider_client · align_strong_affinity_producers · conform_to_intent_order
enforce_create_only_artifact_policy · enrich_move_source_dir
ensure_site_session_precursor · normalize_filter_operation_values
normalize_grouped_artifact_templates · normalize_multisource_entity_report_pipeline
normalize_result_folder_exclusion · overwrite_phantom_install_args
route_filename_pattern_to_find · route_text_web_image_search · scope_sink_provider_to_clause
```

È la lista da cui parte il «meno uno» — **candidate, non condannate**: sui piani
già riparati il silenzio è anche ciò che ci si aspetta da una guardia che ha
fatto il suo lavoro. Serve la quarantena osservata (§3.4). Una però è già
decidibile: `overwrite_phantom_install_args` porta scritta nella `rationale` la
propria condizione di ritiro («0 fire su ≥14 giorni») ed è a **0 spari su 2424
piani reali**. La domanda che aspettava dal 7/7 ha una risposta.

*(Nota metodologica per chi ripete la misura: la prima stesura di
`replay_by_age.py` dava numeri diversi — attribuiva 29 spari a
`overwrite_phantom_install_args`. Sbagliata: due misure indipendenti, la sonda
sulla pipeline vera e un controllo mirato su 556 piani, danno 0. Vale la sonda,
non lo script per età. Se un numero non torna, si controlla con la guardia
isolata prima di scriverlo.)*

### 3.8 La misura ha trovato subito un difetto vivo (e l'ha chiuso)

Al primo giro la sonda ha stampato, 47 volte:

```
align_framework_action_pairs noop: NameError("name 'covered' is not defined")
```

`align_framework_action_pairs` è la guardia aggiunta **oggi** (`2c0c5cd2`).
Alle righe 1318-1319 aggiornava un insieme `covered` che in quella funzione non
esiste — resto di una stesura precedente; la copertura si rilegge da `parsed`,
che viene già aggiornato subito dopo. Ogni guardia ha un `except Exception →
log.warning("… noop")`, quindi l'errore era **invisibile**: la guardia scriveva
`step.tool` e `step.args`, poi saltava, e il piano usciva **mutato a metà** con
`changed` mai alzato. Su un piano con un solo disallineamento il risultato
visibile è corretto — ecco perché i test e i turni reali di oggi sono passati.
È §2.8 in forma pura: **47 fallimenti silenziosi su 2424 piani**.

Corretto (le due righe erano vestigiali). Verifica fatta come si deve, ed è la
dimostrazione che lo strumento di §5.3 funziona davvero:

- fotografia dell'uscita su **2424 piani reali prima**, correzione, fotografia
  **dopo**: **0 piani diversi** — byte per byte;
- eccezioni ingoiate: 47 → **0**;
- `tests/runtime/engine` + `infra`: **1757 passed**;
- turni reali: «chi sono io» → `read_persons` (corretto); «elenca i file … e
  mettili nel foglio …» → `list_dirs` + `create_files_spreadsheet`, cioè la
  guardia NON riscrive il produttore in scrittore — che era il modo in cui la
  prima stesura sbagliava.

**Difetto DIVERSO trovato durante quei turni, da aprire a parte**: «trova i
file .toml in *dir* **e leggili**» va a `find_files_hash` + `read_files` e
risponde «Nessun risultato trovato». La stessa richiesta senza la seconda
clausola va correttamente a `find_files`. È il ranking del pool **per clausola**
che sceglie il fratello sbagliato in composta, e produce un falso negativo
(§2.8). Nessun rapporto con la correzione qui sopra: verificato che il piano non
attraversa il ramo toccato.

---

## 4. Il disegno (PROPOSTA — da decidere con Roberto)

### 4.1 La tesi in una riga

> **I casi sono infiniti, le proprietà sono finite. Oggi stanno tutti e due nel
> codice, quindi il codice cresce con l'insieme infinito.**

Metnos incontrerà per sempre forme di query nuove: è un assistente personale, non
un dominio chiuso. La crescita dei CASI non si può fermare e non va fermata. Il
guasto è che oggi il caso N+1 costa:

- **+1 procedura** (~100 righe) nel modulo del planner,
- **+1 vincolo d'ordine** contro le altre 34, verificato da nessuno,
- **+K nomi d'argomento** esenti dallo schema, globalmente e per sempre (§3.6),
- **+1 superficie di regressione** (la rimappatura degli step, rifatta a mano),
- **e mai −1 di niente.**

L'obiettivo non è «meno guardie». È **scollegare il numero di MECCANISMI dal
numero di CASI**: il caso N+1 deve costare una RIGA in una tabella, zero vincoli
d'ordine, zero esenzioni nuove.

### 4.2 Non è un'architettura importata: è la dottrina di Metnos applicata dov'è saltata

Il progetto ha già risolto tre volte lo stesso problema, altrove:

| dove | come | risultato |
|---|---|---|
| **§2.3 `reverse_pattern`** | catalogo CHIUSO di 5 pattern; il manifest dichiara quale | un executor nuovo non scrive codice di undo |
| **ADR 0199 `credential_form`** | sezione nel manifest firmato | «Nuovo tipo = sezione manifest + chiavi i18n, **zero codice**» |
| **`detection_lexicon`** | NL→canonico, seed IT+EN | niente liste di sinonimi nel codice (regola d'utente) |

Il planner è l'unico sottosistema rimasto fuori da questa dottrina. Il disegno
qui sotto non introduce un'idea nuova: **estende al planner la regola che il
resto del sistema già segue.**

Nota: Roberto ha già scelto istintivamente questa via per il punto 2 della
consegna (`find_places` e «le 3 vie più vicine»): la decisione è stata
*dichiarare il confine nel capitolo `NON:` del manifest* invece di aggiungere la
guardia n. 35. **La risposta al punto 1 è la generalizzazione di quella scelta.**

### 4.3 Prova di fattibilità: i «casi» sono quattro primitive

Verificato sul **caso peggiore** della famiglia E, `_route_folder_size`
(133 righe, due rami, il più intricato del gruppo). Si decompone integralmente
in:

**Azioni** — quattro, chiuse:
1. `swap_tool(da → a)`
2. `set_arg(step, chiave, valore)`
3. `drop_arg(step, chiave)`
4. `insert_producer(tool, args, posizione)` ← *richiede* l'helper unico T3

**Condizioni** — chiuse anch'esse:
- `concept(<id lessico>)` — già esistente (`fs.size_query` è in `detection_lexicon`)
- forma del piano: `produttore terminale`, `consumatore(tool, predicati-arg)`,
  `assenza di <tool>`

`route_folder_size` diventa, per intero:

```
id: fs.folder_size
quando: concept(fs.size_query) ∧ produttore-contenitore terminale ∧ ¬compute(size)
allora: swap_tool(→find_files), set_arg(recursive=true),
        insert_producer(compute_entries, {op:sum, key:size}, dopo)
```

133 righe → 4. E il ramo strutturale (A) è una seconda riga con la stessa
grammatica. **Le tabelle interne `_FS_CONTAINER_PRODUCERS` e `_SIZE_SUM_KEYS`
esistono già dentro il modulo**: il codice sta già andando verso la forma
dichiarativa, si ferma a metà strada.

### 4.4 Le quattro mosse, in ordine di rapporto valore/rischio

#### Mossa 1 — togliere il cricchetto (prerequisito di sicurezza)

`guard_owned` per **(tool, arg)** invece che per nome globale (§3.6, = T1
dell'audit ristretto al suo effetto di crescita). Piccola, chiusa, e **rende la
crescita sicura prima di renderla limitata**: dopo, una guardia nuova non allarga
più il buco nel backstop.

#### Mossa 2 — misurare, per poter ritirare

Accendere `METNOS_GUARD_FIRE_COUNT` in produzione e persistere per guardia
*ultimo sparo* + *conteggio*. Costo: irrilevante (§3.2). Senza questo dato
nessuna guardia si può ritirare — ed è il motivo per cui il numero può solo
salire. È lo stesso ciclo di vita che Metnos applica già agli executor
(`executor_aging.py`, → §3.4), portato sulle guardie.

#### Mossa 3 — le quattro superfici dichiarative (il cuore)

| famiglia | oggi | domani | costo del caso N+1 |
|---|---|---|---|
| **F** modelli di flusso | 3 guardie, **1023 righe** | template di piano come DATI (autopath L1 o tabella firmata) | una riga, oggi ~400 righe |
| **E** casi puri | 10 guardie, ~600 righe | tabella `quando → allora` con catalogo chiuso §4.3 | una riga |
| **D** precondizioni di dominio | 5 guardie, ~700 righe | `requires` nel manifest + UNA guardia generica che le soddisfa | una riga di manifest |
| **B+C** copertura e provider | 7 guardie | consolidamento: 1 proprietà di copertura (la coppia) + 1 legame provider | — |
| **A** dataflow | 9 guardie | restano codice: sono il planner vero | — |

Esito: **34 procedure → ~11 meccanismi + 4 tabelle**, e la derivata della
crescita va a zero.

#### Mossa 4 — prevenzione alla sorgente

Con i dati della Mossa 2 si scopre quali guardie sparano perché il *generatore*
sbaglia (descrizione di manifest ambigua per il modello locale, grammatica
troppo permissiva). Quelle si chiudono a monte — un capitolo `NON:` nel manifest,
una regola di grammatica — e la guardia **muore**. Il commento è già nel codice
(`dispatch.py:5756`): «meno spari con la grammar-args = i guard diventano no-op
perché l'LLM non produce più l'errore». La teoria c'è da luglio; mancano i dati.

### 4.5 Il freno (perché non ricominci)

Il test di contratto **fallisce già** a ogni modifica della pipeline: è il punto
esatto in cui qualcuno sta per aggiungere la guardia n. 35. Lì va messo un
tetto dichiarato: **sopra il tetto non si aggiunge una guardia, si aggiunge una
riga.** L'architettura si fa rispettare nel momento in cui la si sta per
violare, che è l'unico momento in cui serve.

### 4.6 Il rischio onesto di questa proposta

La parte facile sono le azioni (quattro primitive, verificate). **La complessità
sta nelle condizioni.** `route_folder_size` è lungo per come *riconosce* il
caso, non per come lo ripara. Se il linguaggio delle condizioni deve diventare
Python arbitrario, la tabella diventa un linguaggio e la complessità è stata
spostata, non tolta.

La mitigazione è anche il criterio che dà il limite:

> **Il linguaggio delle condizioni è CHIUSO. Un caso che non ci sta dentro non
> è un caso: è una proprietà nuova — e QUELLA si merita una guardia vera.**

Le tabelle assorbono i casi (infiniti); la pipeline cresce solo per le proprietà
(finite: le invarianti di un dataflow sono un insieme limitato). È questo che
trasforma la crescita indefinita in crescita convergente.

---

## 5. Da caso-per-caso a caso-per-REGOLA (domanda di Roberto, 6/8)

> *«ideale, ma da verificare, è passare da case-by-case (una guardia per ciascun
> caso) a un case-by-rule (regole multi caso). ma deve produrre una qualità
> almeno comparabile»*

Due requisiti distinti, entrambi da dimostrare. Il primo è quello che decide se
la proposta vale: **se ogni guardia diventasse esattamente una riga, avrei solo
cambiato sintassi.** La crescita resterebbe lineare, solo più economica. La
vittoria vera è una regola che copre N casi, così che le regole crescano meno
dei casi.

### 5.1 Il criterio che distingue una regola vera da una riscrittura

> **Una regola copre più casi solo se è enunciata al livello della PROPRIETÀ,
> non della frase.**

`route_folder_size` scritto come «se la query dice *quanto è grande la cartella*»
è una riga per un caso: nessun guadagno. Scritto come proprietà —

> *un'aggregazione su un CONTENITORE si calcola sui suoi CONTENUTI, mai sulla
> sua enumerazione*

— copre di colpo: peso di una cartella, peso di una casella di posta, conteggio
foto di un album, dimensione di un archivio. Casi che oggi non esistono ancora e
che **non produrrebbero una guardia n. 35**.

### 5.2 Verifica: le 10 guardie di famiglia E si generalizzano davvero?

Fatta risalendo da ciascuna guardia alla proprietà che sottende. Risultato — e
questa è la parte che non mi aspettavo:

| guardia (caso) | proprietà sottesa | dove la proprietà ESISTE GIÀ |
|---|---|---|
| `normalize_filter_operation_values` | un arg-enum prende un valore del suo enum | **è il mestiere di Guard #0** (`coerce_args`) |
| `overwrite_phantom_install_args` | un arg senza àncora nella query e senza sorgente runtime è fantasma | **`arg_provenance.py`** (ADR 0177 S4) |
| `decontaminate_reader_qualifier` | un qualifier è giustificato dalla PROPRIA clausola, non da una sorella | **indipendenza delle clausole** (principio già nominato) |
| `route_filename_pattern_to_find` | un arg-selettore sta sul produttore che sa usarlo | collocazione degli arg |
| `normalize_result_folder_exclusion` | un predicato su percorso si applica al campo che porta il percorso | collocazione degli arg |
| `route_folder_size` | aggregazione su contenitore = sui contenuti (§5.1) | — (proprietà nuova, generale) |
| `degenerate_find_to_list` | un verbo di ricerca senza predicato è un verbo di enumerazione | ortogonalità dei 5 verbi, **§2.2** |
| `ensure_health_arg` | una query che chiede una sfaccettatura abilita l'arg che la produce | (concetto → arg): 1 regola, N righe |
| `ensure_extracted_period_scope` | un vincolo scalare esplicito si lega al campo del tipo corrispondente | (tipo → campo): 1 regola, N righe |
| `route_text_web_image_search` | la modalità della richiesta seleziona il produttore | (modalità → produttore): 1 regola, N righe |

Il conto, esplicito perché non si sbagli rileggendo: le proprietà distinte sono
**7** — enum, arg fantasma, indipendenza clausole, collocazione degli arg (vale
per due guardie), aggregazione su contenitore, ortogonalità dei verbi, e **una
sola** mappa dichiarativa che raccoglie le tre righe «1 regola, N righe».

**Le 10 guardie stanno dentro 7 proprietà, e 5 di quelle 7 esistono già altrove
nel sistema** — due sono letteralmente il mestiere di componenti che il progetto
ha già (`coerce_args`, `arg_provenance`), una è §2.2 della parte invariante.
Delle 2 che restano: una è una proprietà nuova e generale (aggregazione), l'altra
è la tabella.

Non è una riscrittura in altra sintassi: **è conoscenza che era già nel sistema,
ri-implementata caso per caso in un secondo posto.** Questa è la risposta al
primo requisito, ed è più forte di quanto sperassi.

**VERIFICA del 6/8 sera, sul codice, tutte e cinque. Ne regge UNA.** La colonna
«dove la proprietà ESISTE GIÀ» era stata compilata leggendo le docstring. Letta
sul codice del meccanismo che dovrebbe coprire il caso, il conto cambia:

| guardia | il meccanismo copre davvero? | spari reali (journal, 21 gg) | esito |
|---|---|---|---|
| `overwrite_phantom_install_args` | **SÌ** — le cause sono chiuse a monte da luglio (`arg_provenance` + config-args marcati) | 0 (22 gg) | **RITIRATA** `84e1512c` |
| `normalize_filter_operation_values` | NO — Guard #0 scarta un valore fuori da un enum **dichiarato**; `filter_entries.kind` è dominio APERTO, e `dedup` non è un valore fuori enum ma un marcatore d'operazione in uno slot di predicato | 0 | resta |
| `decontaminate_reader_qualifier` | NO — il pool è l'**unione** dei pool per-clausola (`routing_pool.py:326-387`) e il ranking usa la query INTERA: l'unione è proprio ciò che rende possibile la contaminazione. Nessun componente vincola il qualifier di uno step alla sua clausola | 0 | resta |
| `route_filename_pattern_to_find` | NO — non sposta un arg: **inserisce un produttore** (`find` gemello) e ricuce il read. Nessun meccanismo lo fa; Guard #0 non toglierebbe `paths`, che `read_files` dichiara | 0 | resta |
| `normalize_result_folder_exclusion` | NO — nessuno applica un predicato di percorso al campo che porta il percorso | **4** | resta (e **spara**) |
| `degenerate_find_to_list` | NO — `align_framework_action_pairs` (che gira prima) costruisce `f"{verb}_{obj}"` = `list_files`, che **non esiste**, e si ferma. E un allineamento cieco del verbo sarebbe sbagliato: il contenuto semantico è «find SENZA selettore», che nessun meccanismo di verbi conosce | 0 | resta |

Il conto onesto della famiglia E: **una cancellazione, non cinque.** Le altre
quattro proprietà sono vere e generali, ma **nessun componente le applica**:
il lavoro lì non è cancellare una guardia, è **costruire la superficie** che la
sostituisce. Costo e rischio della Mossa 3 vanno riletti con questo numero.

Materiale grezzo già in casa, per chi la costruirà: la mappa step→clausola
esiste (`step_chunk` dentro `_fill_clause_args`), ma è una **variabile locale di
una guardia**, non una superficie condivisa. Renderla condivisa è il primo
mattone dell'indipendenza delle clausole.

Lezione, che vale oltre questa tabella: **una docstring dice che cosa una
guardia crede di fare, non che cosa un altro componente fa davvero.** Prima di
cancellare si legge il codice del meccanismo che dovrebbe già coprire il caso —
qui, quattro volte su cinque, non lo copriva.

Le tre marcate «1 regola, N righe» sono il caso onesto intermedio: la regola è
una, le righe crescono coi casi — ma una riga di tabella non porta vincolo
d'ordine, non porta esenzione di schema (§3.6), non porta rimappatura a mano
(§3.5). È il costo che si voleva ottenere.

### 5.3 Il secondo requisito: la garanzia di qualità ESISTE GIÀ

E non va inventata. `tests/runtime/infra/test_provenance_equivalence.py` è un
**oracolo di equivalenza golden**, costruito a luglio per esattamente questo tipo
di rifacimento. Il suo scopo dichiarato (docstring):

> «La RETE che protegge errore=0 durante il refactor […] Cattura l'output della
> GUARD_PIPELINE ATTUALE su un corpus di framework grezzi come GOLDEN congelato
> su file. Ogni fase del refactor deve produrre output **BYTE-IDENTICO** sui casi
> che oggi funzionano — se diverge, si vede esattamente quale caso/campo cambia
> PRIMA di procedere.»

È il protocollo giusto parola per parola. Gli manca solo la scala: **il golden
oggi ha 11 casi**.

**Il corpus reale è già su disco, e non è piccolo:**

| fonte | contenuto | n |
|---|---|---|
| `autopath.sqlite` → `observations` | piani reali con `framework_json` completo | **2353** |
| idem, intenti distinti | `intent_sig` | **87** |
| `fastpaths.sqlite` → `fastpaths` | piani L0 **con la query originale** (`canonical_text`) | **70** |
| `bench/compound_scaling_bench.py` | query a complessità crescente | ~114 |

Cioè un oracolo **200 volte** più grande di quello attuale, costruibile oggi da
traffico vero, senza generare niente di sintetico.

### 5.4 Il protocollo di migrazione (una famiglia alla volta, mai big-bang)

1. **Allargare il golden** ai piani reali (§5.3). Congelare l'uscita della
   pipeline attuale: quella diventa la definizione di «qualità attuale».
2. **Scrivere le regole di UNA famiglia**, con la guardia ancora al suo posto.
3. **Confronto in ombra**: entrambe girano, si confrontano i `to_dict()`.
   Divergenza = si classifica in tre modi, e tutti e tre sono guadagno:
   - *difetto della regola* → si corregge;
   - *lacuna della regola* → si aggiunge una riga;
   - *la guardia sbagliava* → **la regola è migliore**, si registra e si aggiorna
     il golden con motivazione esplicita.
4. **Byte-identico su tutto il corpus → si cancella la guardia.** Non prima.
5. Famiglia successiva.

Reversibile a ogni passo, misurabile a ogni passo, e nessun momento in cui la
qualità è ignota. «Almeno comparabile» non resta un auspicio: diventa un test
che è verde o rosso.

### 5.5 Correzione a §4.4 dopo le verifiche

La Mossa 3 diceva «famiglia F → autopath L1». **Sbagliato**, verificato in §3.3:
L1 non esprime né condizioni né rami né propagazione di schema, e tre barriere
(`is_query_specific`, `_ungrounded_mutating_args`, `_should_cache_plan`)
escluderebbero comunque quei piani. La superficie per la famiglia F va
**costruita** — tabella di modelli firmati sul modello di `reverse_pattern`
(§2.3) — oppure la famiglia F si lascia com'è e si accetta il costo. È la
decisione più aperta delle quattro, ed è anche la più grossa (1023 righe).

---

## 6. Sequenza operativa consigliata

In ordine: ognuno è utile da solo, e ognuno abilita il successivo. **Nella
lettura semplificata (§0) i primi tre sono tutto ciò che serve per fermare la
crescita pericolosa; dal 4 in poi si entra nel risparmio, e si fa solo se
serve.**

| # | cosa | perché ora | dove |
|---|---|---|---|
| **0** | registrare anche il piano GREZZO accanto a quello eseguito | senza, il corpus non può dire se una regola *ripara* quanto la guardia (§0.2) | `autopath.record_observation` |
| **1** | `guard_owned` per **(tool, arg)**, non per nome globale | toglie il cricchetto: dopo, aggiungere una guardia non allarga più il buco nello schema. Piccolo e chiuso | `dispatch.py:3644-3662` + `engine/coerce_args.py:66` |
| **2** | helper unico `insert_steps()` che fa TUTTE le rimappe + lint che vieta `steps.insert` diretto | è T3 dell'audit 21/7, «il fix a più alto ritorno anti-regressione», e **serve comunque** come primitiva `insert_producer` di §4.3 | `dispatch.py:2124` (`_remap_step_refs` esiste già) |
| **3** | persistere i conteggi di sparo, contatore acceso in prod | **misura già fatta a mano il 6/8 (§3.7)**: 14 guardie su 34 mute in entrambi i corpora. Persisterla la rende continua invece che una fotografia | `dispatch.py:5761-5798` |
| **4** | allargare il golden ai piani reali | **prototipo già funzionante**: `scratchpad/snapshot_plans.py`, 2424 piani, usato oggi per validare una correzione a 0 differenze. Va portato nel repo | `tests/runtime/infra/test_provenance_equivalence.py` |
| **5** | migrare la famiglia E a regole, in ombra | prima famiglia: 10 guardie → 7 proprietà, 5 già esistenti altrove | §5.2 + §5.4 |
| **6** | famiglia D nei manifest (`requires`) + 1 guardia generica | dottrina ADR 0199 già ratificata | manifest + 1 guardia |
| **7** | consolidare B (copertura sulla coppia) e C (legame provider) | 7 guardie → 2 | — |
| **8** | decidere sulla famiglia F | la più grossa e la più aperta (§5.5) | — |

Passi 1-4 sono **preparazione senza rischio**: nessuno cambia il comportamento
del planner, tutti e quattro sono utili anche se poi il resto non si facesse.

### 6.1 Difetti trovati per strada (fuori tema, da aprire a parte)

- 🚨 **Il punto di raccolta statistiche degli executor è rotto da un mese —
  MISURATO.** Il gancio stava in `agent_runtime.py:9278-9284` («ogni
  invocazione effettiva di un executor») ed è stato cancellato il **4/7** con
  `af6c7b87` (rimozione del planner legacy, −3424 righe); l'engine v3 non l'ha
  mai ricablato. Va a `engine/executor.py:2624`.

  Il dato in `~/.local/state/metnos/executor_stats.db` lo conferma senza
  margini: su 193 righe, **124 hanno `last_used_at` NULL** e 64 sono ferme a
  prima del 5/7. **In un mese si sono mosse 5 righe**, e quattro portano
  l'orario `01:0x` — cioè il job notturno, non un turno reale:

  ```
  2026-07-06T07:05:31Z  find_issues_github          83
  2026-07-08T01:02:57Z  get_processes             1082
  2026-07-30T01:02:12Z  set_preferences              1
  2026-07-31T01:02:05Z  find_images_google_photos    1
  2026-08-06T01:00:34Z  get_places                  52
  ```

  L'unico chiamante superstite di `touch()` è
  `engine/fastpath.py:629`, che **non** conta le invocazioni: trasferisce gli
  usi ereditati da un fastpath morto (di qui gli orari `01:0x`).

  Conseguenze: `change_observer` decide i rollback su numeri fermi, e
  `apply_executor_ager` — che gira davvero ogni notte (`nightly_aging` dentro
  `nightly_maintenance`, ultimo esito `ok` il 6/8 alle 01:00) — misura
  inattività su un contatore che nessuno incrementa. È l'ingresso della
  «trappola aging-inattività»: `move_messages` (àncora 25/5), `read_events`
  (27/5) e `create_events` (23/6) sono `synth:reactive`, **non** in
  `PROTECTED_NAMES` e ben oltre la soglia. `move_messages` è l'unica via per
  cancellare una mail (§5). Restano invece al sicuro gli `handcrafted`
  (esenti dal 13/6), le `skill` (`_is_synth(...,'skill') == False`) e
  `get_files` (protetto).

  *Non ancora spiegato, da chiarire quando si apre il difetto*: quei tre non
  sono stati deprecati, benché il job giri e la condizione a
  `executor_aging.py:386-390` sia soddisfatta. Prima ipotesi da verificare: il
  demone scrive/legge un `executor_stats.db` diverso da
  `~/.local/state/metnos/` (HOME diverso per l'unit di sistema).

  (Nota per §4: è la stessa telemetria che servirebbe alle guardie. Prima di
  costruirla per le guardie, ricablare questa — e usarne il ricablaggio come
  prova che il punto di raccolta regge.)
- `seed_from_run` semina entry L1 senza `is_query_specific`: righe che `lookup`
  scarterà per sempre (zombie `describe_messages__fd456bf522c4`).
- `apply_efficacy_ager` non è agganciato a nessuno scheduler (ADR 0114 lo
  prevedeva daily@04:30). Codice vivo solo nei test.

---

## 7. Stato del lavoro / ripresa

**Analisi chiusa e misurata; i passi 0-4 del §6 sono FATTI e in prod.** Il
diario dei lavori — che cosa è stato fatto, con quale commit, e che cosa resta —
vive in `internal/design/planner_growth_worklog.md`: quello è il documento da
aprire per riprendere, questo è la prova. Riassunto: difetto vivo di §3.8
chiuso, piano grezzo registrato, cricchetto di §3.6 chiuso in uscita, porta
unica di inserimento step, spari persistiti, oracolo sui piani reali nel repo,
**prima guardia ritirata**. Il passo 5 è aperto con la verifica di §5.2 fatta e
in gran parte negativa.

Gli strumenti di misura del passo 4 sono nel repo
(`internal/tools/build_guard_corpus.py`, oracolo in
`tests/runtime/infra/test_guard_corpus_equivalence.py`); le sonde d'analisi
(`guard_probe.py`, `replay_corpus.py`, `snapshot_plans.py`,
`count_guard_errors.py`) sono rimaste nello scratchpad di sessione: servono a
misurare, non a proteggere.

Tutte le sezioni sono scritte: §1 misure · §2 diagnosi e tassonomia (famiglie
A-F) · §3 sei verifiche, tutte concluse · §4 disegno · §5 risposta al
case-by-rule con la garanzia di qualità · §6 sequenza operativa · §A comandi
per rifare le misure.

**In una riga**: i casi sono infiniti, le proprietà sono finite; oggi stanno
tutti e due nel codice, quindi il codice cresce con l'insieme infinito.

**La risposta operativa sta in §0** (tre interventi piccoli e una tabella;
niente motore di regole). Qui sotto, perché quella risposta è quella giusta.

**Le tre cose da sapere se leggi solo questo paragrafo:**

1. Il pericolo non è la latenza (0,53 ms l'intera pipeline) né il numero in sé:
   è il **cricchetto** di §3.6 — ogni guardia nuova esenta un altro nome di
   argomento dalla conformazione allo schema **su ogni tool**, per sempre.
   Oggi 21 nomi, fra cui `path`, `paths`, `mode`, `exist_ok`, `dst_folder`.
2. Il passaggio a case-by-rule è **verificato**, non asserito (§5.2): le 10
   guardie di famiglia E stanno su 7 proprietà. Ma delle cinque «esistono già
   altrove», lette sul codice, **ne regge una sola** — quella già ritirata. Le
   altre quattro proprietà sono vere e generali e **nessuno le applica**: lì il
   lavoro è costruire la superficie, non cancellare la guardia.
3. La garanzia di «qualità almeno comparabile» **non va inventata**: è
   `tests/runtime/infra/test_provenance_equivalence.py`, scritto a luglio
   proprio per questo e già in forma di oracolo byte-identico. Gli mancano solo
   i casi: 11 oggi, contro 2353 piani reali già su disco (§5.3).

**Decisione attesa da Roberto**: i passi 5-8, cioè la migrazione vera, una
famiglia alla volta e ognuna in ombra col protocollo §5.4. La verifica di §5.2
ne ha cambiato il prezzo: nella famiglia E c'era **una** cancellazione, ed è
fatta; il resto è costruzione di superfici. I passi 1-4 (preparazione senza
rischio) sono chiusi.

**Se riprendi e vuoi ricontrollare prima di fidarti**: §A rifà tutte le misure
di §1 e §3 con comandi copiabili. I difetti fuori tema trovati per strada sono
in §6.1; quello dell'executor stats — grave e indipendente — è chiuso il 6/8
(`f9cdc528`): il gancio è in `ExecutorScheduler.invoke`, non nell'anello del
motore, perché lì non passano né i turni serviti da L0 né i builtin. Resta
aperta la composta «trova i file … e leggili», che va al fratello sbagliato e
risponde falso-negativo (§3.8, ultimo capoverso).

---

## A. Appendice — comandi per rifare le misure

### A.1 Composizione di dispatch.py

```python
import re, ast
src = open('runtime/engine/dispatch.py').read()
tree = ast.parse(src)
gp = re.search(r'^GUARD_PIPELINE: tuple = \((.*?)^\)$', src, re.S|re.M).group(1)
names = list(dict.fromkeys(re.findall(r'Guard\(\s*"([a-z_0-9]+)"', gp)))
funcs = {n.name: n.end_lineno-n.lineno+1 for n in ast.walk(tree)
         if isinstance(n, ast.FunctionDef)}
print(len(names), sum(funcs.get("_"+g, funcs.get(g,0)) for g in names),
      len(src.splitlines()))
```

### A.2 Curva di crescita

```bash
git log --format='%H|%ad|%s' --date=short -- runtime/engine/dispatch.py \
| while IFS='|' read h d s; do
    echo "$d $(git show $h:runtime/engine/dispatch.py | wc -l) $s"; done | tac
```
