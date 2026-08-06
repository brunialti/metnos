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
| **5** | resto della famiglia E, in ombra — **PRIMA verificare sul CODICE, una per una, le «proprietà già applicate altrove»** (vedi nota) | — |
| **6** | famiglia D nei manifest come `requires` (705 righe) | manifest |
| **7** | consolidare B e C (638 righe, 7 guardie → 2) | — |
| **8** | decidere sulla famiglia F (1023 righe, la meno verificata) | — |

### Nota sul passo 5 — la prima verifica ha smentito la mappatura

Aperto il passo 5 da `normalize_filter_operation_values`, che §5.2 dava come
«mestiere di Guard #0». **Non lo è.** Guard #0 scarta un valore fuori dominio
*di un enum dichiarato*; `filter_entries.kind` è un dominio **aperto**
(categorie di entry), quindi non c'è enum, e `dedup` non è un valore fuori enum
— è un marcatore d'operazione infilato in uno slot di predicato. Nessun
meccanismo esistente lo copre: la guardia resta.

**Conseguenza operativa per chi riprende**: la mappatura caso→proprietà di §5.2
è stata fatta leggendo le docstring. Le altre quattro «esistono già altrove»
(`overwrite_phantom_install_args` — già ritirata e confermata,
`decontaminate_reader_qualifier`, `route_filename_pattern_to_find`,
`normalize_result_folder_exclusion`, `degenerate_find_to_list`) vanno
verificate **sul codice del meccanismo che dovrebbe già coprirle**, una per
una, prima di cancellare. Il metodo è quello usato per il ritiro riuscito:
(1) chi altro applica la proprietà, e la applica DAVVERO in questo caso?
(2) spari nel journal reale; (3) piani ancora affetti nelle cache servite;
(4) oracolo prima/dopo. Se uno dei quattro non torna, la guardia resta.

Il conto realistico della famiglia E scende quindi sotto la stima di §5.2
finché le verifiche non sono fatte.

## Difetti aperti trovati per strada (indipendenti, non toccati)

- 🚨 statistiche executor rotte da un mese: il gancio per-invocazione fu
  cancellato il 4/7 con `af6c7b87`, va ricablato a `engine/executor.py:2624`.
  124 righe su 193 con `last_used_at` vuoto.
- composta «trova i file … **e leggili**» → `find_files_hash` + `read_files` →
  «Nessun risultato trovato»; senza la seconda clausola va bene a `find_files`.
- `seed_from_run` semina righe L1 che `lookup` scarterà per sempre.
- `apply_efficacy_ager` non è agganciato a nessuno scheduler.
