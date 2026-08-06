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

Gli strumenti di misura sono nello scratchpad di sessione (`guard_probe.py`,
`replay_corpus.py`, `snapshot_plans.py`, `count_guard_errors.py`); portarli nel
repo è il passo 4.

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

## Da fare

| | cosa | dove |
|---|---|---|
| **4** | portare l'oracolo a 2424 piani nel repo | `tests/runtime/infra/` |
| **5** | famiglia E a regole, in ombra (689 righe: 6 guardie da cancellare, 3 a tabella) | — |
| **6** | famiglia D nei manifest come `requires` (705 righe) | manifest |
| **7** | consolidare B e C (638 righe, 7 guardie → 2) | — |
| **8** | decidere sulla famiglia F (1023 righe, la meno verificata) | — |

## Difetti aperti trovati per strada (indipendenti, non toccati)

- 🚨 statistiche executor rotte da un mese: il gancio per-invocazione fu
  cancellato il 4/7 con `af6c7b87`, va ricablato a `engine/executor.py:2624`.
  124 righe su 193 con `last_used_at` vuoto.
- composta «trova i file … **e leggili**» → `find_files_hash` + `read_files` →
  «Nessun risultato trovato»; senza la seconda clausola va bene a `find_files`.
- `seed_from_run` semina righe L1 che `lookup` scarterà per sempre.
- `apply_efficacy_ager` non è agganciato a nessuno scheduler.
