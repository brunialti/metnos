---
id: 0079
title: PLANNER anti-collision guard + GC synth + project-paths config
date: 2026-05-04
status: accepted
area: runtime, prefilter, loader, synt
related:
  - 0045  # closed naming vocabulary
  - 0063  # universal helpers exception in prefilter
  - 0066  # synth executors in user data dir
  - 0075  # prefilter primary tools per object (causa-root upstream)
  - 0076  # synth_request short-circuit (catch-all runtime)
complements:
  - 0075
  - 0076
---

## Context

Sprint 1+2 di mitigazione strutturale del «caso list_processes / synt
spurio» osservato 4/5/2026. ADR 0075 risolve la causa-root upstream
(`_OBJECT_PRIMARY_TOOLS` esteso a tutti gli object); ADR 0076 chiude la
falla a runtime (short-circuit deterministico in `handle_synth_request`).
Restavano tre pezzi mancanti per chiudere il giro:

1. **Guard prompt PLANNER**: il prompt non istruiva esplicitamente «NON
   chiamare `request_new_executor` con `expected_name` gia' nel pool ne'
   con un alias di un produttore canonico (es. `list_X` quando esiste
   `get_X`)». La rete di sicurezza c'e' a runtime (0076), ma costa la
   round-trip LLM dello step in cui il PLANNER decide la chiamata
   sbagliata.
2. **GC dei synth in collisione**: `loader.py:288-295` marca i synth
   collidenti come `rejected`, ma i file restano nella user data dir e
   si accumulano (osservato: `get_processes/` spurio nel `/tmp/...
   _v3` di backup il 4/5).
3. **Bug interpretazione nome-prodotto**: la query "statistiche codice
   metnos" portava il PLANNER a `find_files(pattern="*metnos*")` su
   `$HOME` invece di interpretare "metnos" come **nome del prodotto/
   codebase** e usare `/opt/myclaw/` come root.

Sintomo del punto 3: il `read_files_lines` synth (4/5/2026) calcolava
LOC sul tutto sbagliato (file binari, file di immagini, file fuori
scope) producendo un valore ~7x quello reale. Causa primaria del
read_files_lines sintetizzato per quella query, secondario al fatto che
il PLANNER cercava per pattern di filename invece di andare diretto al
code_root.

## Decision

### 1. Guard ANTI-COLLISION nel prompt PLANNER

`agent_runtime.py::PLANNER_SYSTEM_NATIVE` aggiunge una sezione
prescrittiva (CLAUDE.md §6 stile DEVI/NON DEVI/OK/ERRORE), inserita
prima della guida `request_new_executor`:

```
══════════════════════════════════════════════════════════════════════
ANTI-COLLISION (4/5/2026, ADR 0079)
══════════════════════════════════════════════════════════════════════

DEVI: chiamare l'executor che esiste gia' nel tuo pool quando il suo
  nome ESATTO e' presente.
NON DEVI: chiamare `request_new_executor` con un `expected_name` che e'
  gia' nel pool, ne' proporre `list_X`/`find_X`/`read_X` quando esiste
  gia' `get_X` (o viceversa) per lo stesso oggetto: i 5 verbi-produttori
  sono ORTOGONALI (CLAUDE.md §2.2).
OK: pool=[get_processes,...], query "elenca processi running" → get_processes().
ERRORE: pool=[get_processes,...], query "elenca processi running" →
  request_new_executor(expected_name="list_processes"). E' UN ERRORE.
```

### 2. PROJECT PATHS noti — config + sezione prompt

Nuovo file `runtime/project_paths.json` (single source):

```json
{
  "metnos": {
    "code_root": "/opt/myclaw",
    "user_data_root": "~/.local/share/metnos",
    "memory_root": "~/.claude/projects/-opt-myclaw/memory",
    "description": "Codebase Metnos (assistente personale self-hosted). Process name: myclaw."
  }
}
```

`agent_runtime.py::_render_project_paths_block()` legge il JSON al load
del modulo e lo inietta nel prompt PLANNER come blocco "PROJECT PATHS
NOTI". Per ogni progetto: una riga `"<name>" = codebase in <code_root>`.
Stile CLAUDE.md §6:

- DEVI: usare `code_root` come scope quando l'utente nomina un progetto.
- NON DEVI: trattare il nome come pattern di filename.
- OK: "linee di codice di metnos" → `compute_files_loc(paths=["/opt/myclaw"])`.
- ERRORE: `find_files(pattern="*metnos*", base_path="/home/roberto")`.

Aggiunta di nuovi progetti = aggiunta entry nel JSON, niente
modifica al codice.

### 3. GC synth rifiutati per collision

`loader.py::_gc_collisions(catalog)` viene chiamato a fine
`load_catalog(verify=True, include_synth=True)`. Comportamento:

- Itera `catalog.rejected` e seleziona solo le tuple il cui motivo
  contiene `name collision with handcrafted`.
- Verifica che il path sia DENTRO `SYNTHESIZED_EXECUTORS_DIR` (guard:
  i path handcrafted, anche se per errore in rejected, non vengono
  toccati).
- `shutil.move(src, /tmp/metnos_synth_gc_<ts>/<name>/)`. **Backup
  non distruttivo**: la dir e' recoverable.
- Idempotente: se `/tmp/metnos_synth_gc_<ts>/<name>/` esiste, aggiunge
  suffisso `.1/.2/...`.
- Log: `log.info("[loader] GC synth %s → %s (collision)", src, dst)`.

## Consequences

- **Latenza risparmiata pre-runtime**: la guard ANTI-COLLISION nel
  prompt evita lo step PLANNER che propone `expected_name` collidente.
  Se il modello segue la regola, il short-circuit di ADR 0076 non
  scatta nemmeno.
- **Disk pressure azzerato**: il GC mantiene la user data dir pulita
  invocazione dopo invocazione. Niente accumulo di synth «zombie».
- **Bug nome-prodotto chiuso senza LLM**: `runtime/project_paths.json`
  e' deterministico (CLAUDE.md §7.9 «codice deterministico > LLM se
  equipotente»). Espandibile (giorgio2, suprastructure, ...) modificando
  solo il JSON.
- **Test verificati 4/5/2026**:
  - `tests/test_loader_gc.py` (5 test): moves collision, skips
    non-collision, skips handcrafted, idempotenza, integrazione load.
  - `tests/test_compute_files_loc/manifest.toml` (6 test di nascita
    via test_runner): mix py/md/html, skip __pycache__, skip binari,
    count_blank toggle, count_comments toggle, max_files truncated.
  - Live: `compute_files_loc(paths=["/opt/myclaw"])` produce
    345 file, 85 654 linee — coerente con il volume del codebase.
- **Synt obsoleto rimosso**: `~/.local/share/metnos/executors/
  read_files_lines/` spostato in `/tmp/metnos_synth_collision_*_v3/`
  (sostituito dal nuovo handcrafted `compute_files_loc`).

## References

- `runtime/agent_runtime.py` (`_render_project_paths_block`, blocco
  PROJECT PATHS + ANTI-COLLISION nel prompt).
- `runtime/project_paths.json` (config single source).
- `runtime/loader.py` (`_gc_collisions`, integrato in `load_catalog`).
- `tests/runtime/infra/test_loader_gc.py` (5 test).
- `executors/compute_files_loc/` (nuovo handcrafted, sostituisce synth
  buggy).
- `runtime/vocab.py` (qualifier `_loc` aggiunto in QUALIFIERS).
- ADR 0075 (causa-root prefilter), 0076 (catch-all runtime).
