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

## Da fare

| | cosa | dove |
|---|---|---|
| **5** | resto della famiglia E, in ombra. **La verifica preliminare è FATTA** (nota qui sotto): resta una sola cancellazione possibile, già fatta — il lavoro rimasto è costruire le superfici | — |
| **6** | famiglia D nei manifest come `requires` (705 righe) | manifest |
| **7** | consolidare B e C (638 righe, 7 guardie → 2) | — |
| **8** | decidere sulla famiglia F (1023 righe, la meno verificata) | — |

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

- composta «trova i file … **e leggili**» → `find_files_hash` + `read_files` →
  «Nessun risultato trovato»; senza la seconda clausola va bene a `find_files`.
- `seed_from_run` semina righe L1 che `lookup` scarterà per sempre.
- `apply_efficacy_ager` non è agganciato a nessuno scheduler.
