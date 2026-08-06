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

## Da fare

| | cosa | dove |
|---|---|---|
| **2** | helper unico `insert_steps()` + test che vieta `steps.insert` diretto | `dispatch.py` (`_remap_step_refs` esiste già) |
| **3** | persistere i conteggi di sparo delle guardie, contatore acceso in prod | `dispatch.py` (`_GUARD_FIRE_COUNTS`) |
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
