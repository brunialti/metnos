# Accrescimento del planner — diario dei lavori

> **A che serve**: riprendere senza ricostruire nulla se la sessione si
> interrompe. L'analisi e le misure stanno in `analysis_planner_growth_6_8.md`
> (leggere §0 e §7); qui c'è solo **che cosa è stato fatto e che cosa resta**.
> Aggiornato a ogni passo chiuso. Ultimo aggiornamento: 6/8/2026.

## Regola di lavoro (vale per ogni passo)

Ogni passo si chiude così, nell'ordine, senza saltare:

1. modifica → 2. `snapshot_plans.py` prima/dopo sui piani reali → 3. si guarda
**ogni** differenza e si dichiara perché è giusta → 4. test mirati che falliscono
sul codice vecchio → 5. `pytest -q tests/runtime` intera → 6. riavvio prod
(`sudo -n systemctl restart metnos-http.service`) → 7. almeno un turno reale sul
dominio toccato → 8. commit in italiano, senza trailer.

L'oracolo vive nel repo (`tests/runtime/infra/test_guard_corpus_equivalence.py`,
804 piani reali) e il corpus si ricostruisce da un'istanza viva con
`internal/tools/build_guard_corpus.py`. Le sonde usate per l'analisi
(`guard_probe.py`, `replay_corpus.py`, `snapshot_plans.py`,
`count_guard_errors.py`) sono rimaste nello scratchpad di sessione: servono a
misurare, non a proteggere.

## Fatto

| | cosa | commit | esito |
|---|---|---|---|
| — | `align_framework_action_pairs` non salta più nel proprio `except` | `81328fb0` | 47 fallimenti silenziosi su 2424 piani → 0. Oracolo: 0 differenze. Test nuovi rossi sul codice vecchio |
| — | il documento di analisi | `ccd5e524` | — |
| **0** | l'observation porta anche il piano **grezzo** (`framework_raw_json`) | `4bdc6de6` | colonna additiva; vuota sugli hit di cache (assente ≠ identico). Verificato su turno reale |

| **1** | il piano che parte per l'executor è conforme ai manifest | `b641daeb` | 122 piani su 2423 cambiano, **tutte cadute**; nessun tool cambia. Trovati e sanati 2 contratti disallineati |

### Nota sul passo 1: la prima strada era sbagliata

Il primo tentativo stringeva **all'ingresso** (esenzione valida solo dove il
tool dichiara l'arg). Ha rotto un test, e il test aveva ragione: un arg fuori
schema è spesso la **prova** che una guardia legge — `include_health` su
`get_files` non appartiene al contratto di get_files, ed è esattamente per
questo che dimostra un instradamento sbagliato. Toglierlo all'ingresso acceca
chi lo legge.

La strada buona è **ingresso tollerante, uscita stretta**: `strip_unknown_args`
ultima della pipeline, dove nessuno deve più leggere quegli arg. È una
proprietà (conformità in uscita), non un caso, e vale una guardia vera.

Ricaduta utile: verificando una per una le cadute si sono trovati **due
contratti disallineati** — il ramo Drive di `find_files` legge `query`, quello
di `write_files_spreadsheet` legge `path`, nessuno dei due dichiarato nel
manifest. Sanati (args nuovi + re-firma). L'oracolo era rosso finché la causa
era lì ed è tornato verde da solo: nessun golden rigenerato.

**Da far rivedere a Fable**: le descrizioni dei due args nuovi
(`find_files.query`, `write_files_spreadsheet.path`) sono testo model-facing
scritto da Opus per necessità strutturale.

| **2** | un solo modo di inserire uno step, e rimappa tutto | `f1fdf64a` | 4 guardie migrate a `insert_steps`; test statico vieta `steps.insert`. 0 differenze sui piani reali; provato in diretta che `${stepN}` e `final_message` prima restavano indietro |

### Nota sul passo 2

Il corpus reale non contiene piani che esercitano le rimappe mancanti, quindi
l'oracolo è verde per assenza di casi, non per assenza di bug. La prova sta
altrove: su `enrich_move_source_dir` ora `${step1.path}` diventa
`${step2.path}` e il `final_message` segue — prima restavano entrambi appesi
alla numerazione vecchia.

Fuori dalla porta unica resta `ensure_extracted_period_scope`: non inserisce
soltanto, ricabla i consumatori dell'extract sul filtro nuovo con una mappa non
uniforme. È dichiarato nel test, non dimenticato.

Osservato per strada, non toccato: «sposta i file **di** X» non attiva
`enrich_move_source_dir` (il lessico `fs.files_in_folder` copre «da»/«in»,
non «di»). Con «dalla cartella X» funziona. Non l'ho esteso di proposito: «di»
è ambiguo («i file di ieri»), e allargarlo qui sarebbe un lessico a orecchio.

| **3** | gli spari delle guardie si contano e restano | `d9624fc4` | `engine/guard_stats.py`, acceso di default (costo 0,03 ms/piano), riversato ogni 5 piani. Verificato in prod: 6 turni → 15 attraversamenti su tutte e 35 le guardie |

### Nota sul passo 3

Il numero di guardie è 35, non 34: `strip_unknown_args` è nata al passo 1. È
l'unica aggiunta di questa sessione, ed è una **proprietà** (conformità in
uscita), non un caso.

Come leggere `guard_stats.db`:

```bash
sqlite3 ~/.local/state/metnos/guard_stats.db \
  'select name, fires, seen, last_fire_at from guard_fire order by fires desc'
```

Zero spari **non** è una condanna: può voler dire «non serve più» oppure
«serve, e il piano arriva sano proprio perché c'è». Il ritiro richiede la
quarantena osservata — si spegne la guardia e si guarda se l'errore risale.

| **4** | l'oracolo di equivalenza sui piani reali entra nel repo | `a5a765a7` | 804 casi anonimizzati (osservazioni + fastpath **con** il testo della query), golden a impronte, test di copertura. Strumento in `internal/tools/build_guard_corpus.py` |

### Nota sul passo 4

Il corpus è **anonimizzato**: nomi e percorsi personali sostituiti, e ogni
piano che porti valori lunghi (contenuto reale bake-ato, corpi di mail) è
scartato. Verificato a mano che non resti nulla di personale prima di
committarlo.

I fastpath ci sono perché portano il **testo della query**: senza, le guardie
che la leggono non sparano mai e il corpus non le copre. Con loro, il corpus
esercita 13 guardie su 35 — le altre 22 non sono coperte, ed è scritto nel test
invece che sottinteso.

| **5a** | ritirata la prima guardia della storia della pipeline | `84e1512c` | `overwrite_phantom_install_args`: condizione di ritiro scritta nella guardia stessa, verificata su journal (22 gg, 0 spari), cache servite (0 piani avvelenati) e corpus |

| — | l'uso degli executor torna a lasciare traccia (difetto indipendente, chiuso) | — | gancio nell'UNICO punto attraversato da subprocess, remoto, builtin e onda parallela (`ExecutorScheduler.invoke`); verificato in prod su tre turni reali |

### Nota sul passo 5a — il primo «meno uno»

La guardia riparava **a valle** un veleno le cui **cause** sono chiuse a monte
da luglio. È §7.3 nella sua forma pulita: il sintomo era la rete, il fix
generalizza la causa, e quando la causa è chiusa la rete si può togliere.

Trovata qui una **trappola metodologica** da ricordare: i piani presi dalle
osservazioni non portano il testo della query. Una guardia che legge la query
si comporta nel replay diversamente che in esercizio — questa, con la query
vuota, sembrava riparare **41 piani** che in produzione non toccava affatto.
Se una divergenza riguarda una guardia che legge la query, verificarla sui casi
con `query` piena prima di trarne conclusioni. È scritto anche nel test.

Conto: **35 → 34 guardie**. Al netto della nascita di `strip_unknown_args`,
siamo al numero di partenza — ma con il cricchetto chiuso, la rinumerazione
unificata e la misura accesa.

## RIAPERTURA (6/8 sera) — la chiusura era giusta sulla risposta sbagliata

Roberto, dopo aver letto la chiusura: *«è una follia. un file .py che cresce di
oltre 20 volte in due mesi e arriva a oltre 7k LOC non è gestibile alla lunga.
serve pensare in grande e lateralmente: regole generali per pattern
universali»*.

Ha ragione, e l'errore è mio: ho misurato **una** sostituzione (tabella di dati
+ interprete), ho visto che non paga — è vero, i dati sono il 4% — e mi sono
fermato lì. «Quella sostituzione non paga» non è «non esiste una
riformulazione». Rifatta la classificazione **per CAUSA** invece che per
famiglia, il quadro cambia:

| causa (perché la guardia esiste) | guardie | righe | spari 21gg |
|---|---|---|---|
| **1. il piano non ha TIPI** — il dataflow fra step non è dichiarato da nessuna parte | 9 | 1059 | 162 |
| **2. il POOL offre il candidato sbagliato** — l'applicabilità di un tool non è dichiarata | 13 | 1079 | 249 |
| **3. gli ARGS non hanno un contratto abbastanza forte** | 9 | 627 | 10 |
| **4. modelli di FLUSSO interi** — non è riparazione, è pianificazione | 3 | 1020 | 101 |

Tre leve universali, una per causa, più uno spostamento di livello:

1. **Tipi sul dataflow.** Ogni tool dichiara che cosa produce e che cosa
   consuma; il piano diventa una pipeline tipizzata. Allora «manca il
   produttore», «il campo non esiste», «le colonne del sink non arrivano
   all'extract», «l'ordine non segue le clausole» smettono di essere nove
   procedure e diventano **un validatore + un inseritore**. Metà meccanica già
   esiste (`insert_steps`). Assorbe ~1000 righe.
2. **Applicabilità dichiarata + un gate del pool.** Tredici guardie riparano
   *dopo* una scelta che il pool non doveva nemmeno offrire. La prova che
   funziona è in casa e costa 30 righe: `_gate_image_modality` (2/7) ha ucciso
   una classe intera togliendo i tool-immagine dal pool quando la query non
   nomina immagini. Generalizzato = `[applicability]` nel manifest, come già si
   fa per `[placement]`, `[execution]`, `[credential_form]`. Assorbe ~1000
   righe e **il picco degli spari** (249).
3. **Contratto degli args alla generazione**, non alla riparazione: la
   grammatica vincolata esiste già per i NOMI (ADR 0133/0156); estesa agli args
   rende irrappresentabile ciò che oggi si conforma a valle.
4. **La famiglia F non si riscrive: cambia livello.** Quelle 1020 righe non
   riparano un piano sbagliato — *sono* un piano. Il posto di un piano per una
   classe di richieste è L1/skill, non `dispatch.py`. (§3.3 diceva che L1 oggi
   non regge condizioni e rami: allora si estende L1, che è un lavoro con un
   confine, invece di tenere 1020 righe nel motore.)

**E la regola che ferma la crescita per costruzione**: oggi una guardia nuova
costa una funzione e una riga nel registro — cioè niente. Deve costare una
dichiarazione: *a quale delle tre cause appartiene, e perché il meccanismo di
quella causa non la copre*. Se non sa rispondere, non è una guardia nuova: è
un'estensione del meccanismo. È la stessa disciplina del vocabolario chiuso
degli executor (§2.2), applicata al motore.

Spezzare `dispatch.py` in più file è cosmesi finché le cause restano: 7000
righe in cinque file sono sempre 7000 righe.

**Ordine proposto** (rendimento per rischio, misurato): prima la leva 2 su UNA
sola famiglia (i provider: 185 spari, 234 righe, e il precedente
`_gate_image_modality` come modello), in ombra col corpus a 804 piani. Se la
misura tiene, la leva 1. La 3 e la 4 dopo.

---

## Passi 5-8: CHIUSI (6/8/2026, decisione delegata da Roberto)

**Non si migrano. La misura dice che si riscriverebbe il codice che lavora.**

Spari reali per famiglia, journal di 21 giorni (`grep '\[<marcatore>'`, i
marcatori non sono i nomi delle guardie):

| famiglia | righe | spari 21gg | che cos'è |
|---|---|---|---|
| **F** (passo 8) | 1020 | **101** | i tre `normalize_*_report_pipeline`: la famiglia più grossa è anche **la più viva** |
| **C** (passo 7) | 234 | **185** | quasi tutti da `scope_sink_provider_to_clause` |
| **D** (passo 6) | 761 | **127** | quasi tutti da `ensure_extract_clause` |
| **B** (passo 7) | 319 | 27 | copertura verbo/oggetto/coppia |
| **E** (passo 5) | 654 | **11** | la famiglia quasi morta — ed è l'unica già lavorata (1 ritiro) |

Le tre ragioni, in ordine di peso:

1. **Il risparmio non c'è dove lo cercavamo.** La sequenza proponeva di
   consolidare B, C, D, F: sono esattamente le famiglie che portano il traffico
   (440 spari su 451). La famiglia quasi muta è E, già trattata: 1 ritiro fatto
   e le altre quattro proprietà **non applicate da nessuno** (verifica §5.2) —
   migrarle vuol dire costruire superfici nuove, non cancellare guardie.
2. **Il `requires` nei manifest sposterebbe il grilletto, non il corpo.** Delle
   cinque guardie D solo due sono davvero precondizioni, e sono le due grosse:
   `ensure_site_session_precursor` (361 righe di consenso/sessione/credenziali)
   e `ensure_extract_clause` (190 di propagazione schema). Un manifest può
   dichiarare *che* serve un produttore a monte, non *come* costruirlo.
3. **L'oracolo copre 13 guardie su 34.** Una riscrittura di 2300 righe con una
   rete che ne verifica un terzo non è «reversibile a ogni passo»: è una
   scommessa. Il protocollo §5.4 chiede byte-identico su tutto il corpus, e il
   corpus non ce l'ha.

**Il problema di crescita, però, era reale — ed è quello che i passi 1-4 hanno
chiuso.** Non era il numero di guardie: era il cricchetto (ogni guardia nuova
allargava il buco nello schema, per sempre), la rinumerazione dimenticata, e
l'invisibilità. Oggi una guardia nuova non allarga niente, non può sbagliare
l'inserimento, deve passare 804 piani reali, e i suoi spari si contano.

### Criteri di riapertura (perché «non si fa» non sia un rinvio)

- **Famiglia F** → quando serve un **quarto** report-pipeline. A tre, la
  tabella dichiarativa costa più di quanto renda; a quattro si ripaga.
- **Inserimento di produttore** (D+E) → quando una **quinta** guardia deve
  inserire un produttore a monte. Oggi sono quattro
  (`ensure_site_session_precursor`, `ensure_extract_clause`,
  `enrich_move_source_dir`, `route_filename_pattern_to_find`) e la metà
  meccanica esiste già (`insert_steps`).
- **Ritiro** → automatico nel senso che la lista si presenta da sola: vedi
  sotto.

### Quello che sostituisce la migrazione: il ritiro diventa ordinario

`guard_stats.dormant()` elenca le guardie che **non hanno riparato niente** in
una finestra lunga, e la lista compare ogni notte nel riepilogo lifecycle
accanto agli executor invecchiati (`METNOS_GUARD_DORMANT_DAYS`, default 60;
`METNOS_GUARD_DORMANT_MIN_SEEN`, default 500). Tre requisiti insieme —
silenzio, massa, tempo — perché una guardia giovane o poco attraversata non è
dormiente.

Resta una lista di **candidate**, mai un verdetto: zero spari significa «non
serve più» oppure «il piano arriva sano proprio perché c'è», e il protocollo in
quattro passi resta manuale. Ma la domanda «quali?» adesso è una query, non un
progetto — ed è per questo che in due mesi ne era stata ritirata una sola.
Primo verdetto utile: ottobre (il contatore parte dal 6/8).

### Nota sulle statistiche degli executor

Il gancio per-invocazione fu cancellato il 4/7 con `af6c7b87` insieme al planner
legacy e mai ricablato in engine v3. La consegna lo dava a
`engine/executor.py:2624`: **sarebbe stato il posto sbagliato**, perché lì
passano solo gli step dell'anello del motore — non i turni serviti da L0, non le
riprese dopo un dialogo, non i builtin in-process. Un executor usato SOLO via
cache sarebbe risultato inattivo e l'aging l'avrebbe deprecato. Il punto giusto
è `ExecutorScheduler.invoke` (ADR 0196: «ogni invocazione attraversa lo
scheduler»), dove passano tutti e quattro i trasporti.

Due cose che non erano ovvie e sono nel codice:

- lo scheduler ammette anche **slot interni** che executor non sono (mode, sonde
  e compositore del Tutor prendono lo slot `llm`): hanno nome e politica ma
  nessuna unità firmata su disco. Il discriminante è `code_path`; senza,
  l'aging si sarebbe trovato righe di ciclo di vita per cose che un ciclo di
  vita non ce l'hanno.
- il gancio **non registra sotto pytest**, come `guard_stats`: la suite invoca
  executor veri migliaia di volte. Verificato dopo la suite intera — il registro
  reale è rimasto a 193 righe e allo stesso `last_used_at`.

Danno del mese di buio: **nessuna deprecazione ingiusta**. L'ultimo evento di
aging è del 8/7 e da allora non ce ne sono altri, perché tutto ciò che era
deprecabile lo era già stato a giugno; il costo è stato di misura persa, non di
capacità ritirate.

### Nota sul passo 5 — verifica finita: delle cinque, ne regge UNA

La mappatura caso→proprietà di §5.2 era stata fatta leggendo le **docstring**.
Rifatta sul **codice del meccanismo che dovrebbe già coprire il caso**, una per
una, il risultato è quasi tutto negativo (tabella completa in §5.2
dell'analisi):

| guardia | copre davvero? | perché | spari (journal 21 gg) |
|---|---|---|---|
| `overwrite_phantom_install_args` | **SÌ** | cause chiuse a monte da luglio | 0 → **ritirata** |
| `normalize_filter_operation_values` | no | `kind` è dominio APERTO: nessun enum da cui `dedup` sia fuori | 0 |
| `decontaminate_reader_qualifier` | no | il pool è l'**unione** dei pool per-clausola e il ranking usa la query intera: l'unione È la contaminazione. Nessuno vincola il qualifier alla sua clausola | 0 |
| `route_filename_pattern_to_find` | no | non sposta un arg: **inserisce un produttore** e ricuce il read | 0 |
| `normalize_result_folder_exclusion` | no | nessuno applica un predicato di percorso al campo che porta il percorso | **4** |
| `degenerate_find_to_list` | no | `align_framework_action_pairs` cerca `list_files`, che non esiste, e si ferma; e il contenuto vero è «find SENZA selettore», che nessun allineamento di verbi conosce | 0 |

**Conseguenza per chi riprende.** Nella famiglia E c'era **una** cancellazione,
ed è fatta. Le altre quattro proprietà sono vere e generali ma **nessun
componente le applica**: lì il lavoro non è togliere una guardia, è costruire la
superficie che la sostituisce — con la guardia ancora al suo posto, in ombra,
protocollo §5.4. Il prezzo del passo 5 va riletto con questo numero, e la
decisione «costruire o lasciare» è di Roberto.

Il metodo resta quello del ritiro riuscito: (1) chi altro applica la proprietà,
e la applica DAVVERO in questo caso? (2) spari nel journal reale; (3) piani
ancora affetti nelle cache servite; (4) oracolo prima/dopo. Qui (1) è bastato a
fermare tutte e quattro. Materiale già in casa per chi costruirà: la mappa
step→clausola esiste (`step_chunk` in `_fill_clause_args`) ma è una variabile
locale di una guardia, non una superficie condivisa.

Nota di metodo, dal journal: `journalctl -u metnos-http.service` copre **21
giorni** (riparte dal 16/7), e gli INFO ci sono tutti — i marcatori delle
guardie si contano con `grep '\[<marcatore>'`, che NON è il nome della guardia
(`normalize_result_folder_exclusion` logga `[result_scope`).

## Difetti aperti trovati per strada (indipendenti, non toccati)

- `seed_from_run` semina righe L1 che `lookup` scarterà per sempre.
- `apply_efficacy_ager` non è agganciato a nessuno scheduler.

### Non era un difetto: i verbi nudi nell'affinity di `find_files_hash`

Segnalati come sospetti («un cercatore di duplicati non dovrebbe rivendicare
*trova*/*find*»), poi **misurati**, e la misura ha smentito il sospetto due
volte di seguito:

| ipotesi | misura sul corpus congelato (223 query) |
|---|---|
| «il verbo nudo è rumore: il verb-boost lo conta già» → toglierlo ovunque | **top-1 97 → 80.** È un marcatore di PRIMATO di famiglia: distingue il default dai fratelli provider (`list_dirs` vs `list_dirs_github`, `find_files` vs `find_files_github`), che il +10 del verbo non può separare perché ce l'hanno tutti |
| «allora toglierlo ai soli fratelli qualificati» (4 casi) | 0 cambi sul corpus, ma fuori corpus appiattisce `read_files_csv` su `read_files` per «leggi il csv» (24→20 contro 21): il verbo è la base additiva su cui il qualifier costruisce |

Su `find_files_hash` in particolare: togliere i due tag rompe il pareggio 24-24
con `find_files` su «trova i file .md …» — pareggio che oggi risolve
l'ordine alfabetico — ma gli fa perdere margine sulle sue query di firma e lo
fa scivolare dal 2° al 4° posto su «trova i duplicati nella cartella immagini».
E il path VIVO (`rank_with_intent`) quei verbi non li conta affatto. Lasciato
com'è, con la misura scritta in `decisions/anti-regression-index.md` perché
nessuno la rifaccia da capo.

Lo strumento è ora nel repo: `scripts/bench_prefilter_corpus.py` (`--dump` /
`--confronta`, elenca ogni query in cui il primo cambia). Il corpus congelato
esisteva dal 5/6 e non lo leggeva nessuno.

### Chiuso: la composta «trova i file … e leggili» — e il passo 0 che l'ha chiusa

Il sintomo diceva «routing»: `find_files_hash` (il cercatore di DUPLICATI) al
posto di `find_files`, e «Nessun risultato trovato» con entrambi gli step
ok=True. La colonna del **piano grezzo** aggiunta al passo 0 ha detto subito che
il proposer non c'entrava: il suo piano era `find_files + read_files +
describe_entries`, corretto. La riscrittura veniva DOPO.

Non era una guardia (verificato: la pipeline offline non tocca il tool, e
`guard_stats` segna solo `fill_clause_args` su quel turno). Era il **Validator**:

1. `read_files(from_step=1)` — la forma canonica del piping, §4.1 — violava
   `requires_one_of ['path','paths','entries','name']`, perché il ramo dei
   `requires_one_of` non sapeva ciò che il ramo dei `required` sa da sempre:
   `from_step` diventa `entries` solo all'invoke. **Nove gruppi del catalogo**
   ne erano colpiti (read_files, delete_files, get_urls, read_files_doc,
   read_files_spreadsheet, get/write_images_google_photos, find_images_indices,
   find_persons_indices): ogni turno FRESCO con un consumatore piped pagava un
   re-propose LLM (i turni serviti da L0/L1 non passano dal Validator, quindi
   non lo pagavano — e infatti il difetto è rimasto invisibile).
2. Il re-propose è **cieco** (esclude l'impronta del piano fallito, non dice che
   cosa non andava) e il piano che tornava veniva accettato **comunque**. Così
   un piano corretto è stato scambiato con uno peggiore.

Due difetti in fila, ognuno generale, nessuno dei due «un caso». Chiusi
entrambi: il disgiuntivo riconosce il piping quando il gruppo ha un arg-lista
(un gruppo di soli scalari resta violato, ha il suo controllo), e la ri-proposta
si accetta solo se il conto degli errori scende — lo stesso criterio che il
re-propose dei verbi scoperti usa venti righe più su.

**Ricaduta sul metodo**: il passo 0 non serviva solo al corpus. È la prima
domanda da fare davanti a un misroute — *il proposer aveva già sbagliato, o
gliel'abbiamo rotto noi dopo?* — e si legge con una query su
`observations.framework_raw_json`.
