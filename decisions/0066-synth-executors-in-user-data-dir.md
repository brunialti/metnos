---
id: 0066
title: Synthesized executors in user data dir (~/.local/share/metnos/executors/), separate from handcrafted pool in /opt/myclaw/executors/
date: 2026-05-01
status: accepted
area: runtime
related:
  - 0054  # telos cabled via request_new_executor
  - 0051  # synt multistage 5 stages
---

<!-- Formalizzazione di una decisione vissuta nel codice
(`loader.SYNTHESIZED_EXECUTORS_DIR`) e citata in CLAUDE.md §10.6.2 ma
mai resa esplicita come ADR. -->

## Context

Metnos distingue due popolazioni di executor:

* **Handcrafted** in `/opt/myclaw/executors/<name>/` — pool seed canonico
  parte del repo git, firmato, peer-reviewed. ~26 executor al
  1/5/2026 (read_files, find_files, get_now, list_processes, ecc.).

* **Synthesized on-the-fly** prodotti da `synt_multistage` quando il
  PLANNER chiama `request_new_executor` su un name mancante (ADR 0054).
  Es. 1/5/2026: query "info sul processo con PID 1" → sintetizzato
  `get_processes` (PLANNER non trova match nel pool seed).

Lo storage default per il pool synth e' stato impostato in
`runtime/loader.py:26`:

```python
SYNTHESIZED_EXECUTORS_DIR = Path.home() / ".local" / "share" / "metnos" / "executors"
```

Esempio concreto al 1/5/2026 sera:

```
/opt/myclaw/executors/                   ← handcrafted, in git
├── read_files/
├── find_files/
├── get_now/
└── ...

/home/user/.local/share/metnos/executors/   ← synth, generated
├── get_processes/        (synth 1/5 15:15)
├── list_processes/       (synth 27/4)
└── ...
```

La decisione e' vissuta nel codice ma non era stata documentata
formalmente. Roberto 1/5/2026 sera: "perche' i synth non sono dentro
myclaw?" — momento giusto per esplicitare il razionale.

## Decision

I synthesized executor scrivono in `~/.local/share/metnos/executors/`,
NON in `/opt/myclaw/executors/`. Quattro motivi, dal piu' al meno
strutturale:

1. **Permessi runtime.** `/opt/myclaw/` e' path di installazione: in
   produzione puo' essere mounted read-only, owned da `root` o da uno
   user di servizio, sotto systemd `ProtectSystem=full`. Il processo
   `myclaw` gira come user non-root (es. `roberto`) e NON ha permessi
   di scrittura in `/opt`. Synth genera codice a runtime; deve scrivere
   in una dir user-writable.

2. **Code (immutable, VCS) vs data (mutable, runtime).** `/opt/myclaw/`
   e' il repo git: `git status` deve restare pulito. Mescolare synth
   generati ad-hoc con handcrafted firmati renderebbe il working tree
   perpetuamente "sporco" e confonderebbe le due popolazioni semantiche
   ("biblioteca canonica" vs "crescita organica per-deployment"). La
   separazione fisica garantisce la separazione concettuale.

3. **XDG Base Directory standard.** `~/.local/share/<app>/` e' la
   convenzione Linux per application data per-user (`XDG_DATA_HOME`).
   Coerente con il resto del layout Metnos gia' in user-space:
   - `~/.config/metnos/` — config (mail.env, future credentials)
   - `~/.local/state/metnos/` — state runtime (locations.jsonl,
     location_pending/, pairings.db)
   - `~/.local/share/metnos/` — dati persistenti accumulati
     (executors/, i18n.sqlite, image_index_*, history/turns/, blob/,
     introvertiva/audit logs).

4. **Backup separato.** `/opt/myclaw/` e' ricostruibile da `git pull`
   in pochi secondi; non serve backupparlo. `~/.local/share/metnos/`
   e' il vero "stato accumulato" del deployment (synth, history,
   indici): va su NAS (`backup_nas.sh` sezione `dotlocal/`, replica
   pattern giorgio2). Strategie di backup distinte per natura distinta
   degli artefatti.

Conseguenza operativa: il loader scansiona ENTRAMBE le dir
(`load_catalog` con `include_synth=True` di default) ma applica una
*regola di precedenza*: handcrafted vince per costruzione. Se un synth
ha lo stesso name di un handcrafted, viene RIFIUTATO con
`name collision` (CLAUDE.md §10.6.2). Stage 1 di synt deve verificare
i nomi liberi prima di proporre.

## Alternatives considered

* **Tutto in `/opt/myclaw/executors/`** (synth e handcrafted insieme):
  rompe i 4 punti di cui sopra. Inoltre forzerebbe sudo per ogni
  synth-on-the-fly, oppure sudo-less ma con `/opt/myclaw/` chmod
  777 — entrambe inaccettabili.
* **Tutto in `~/.local/share/metnos/executors/`** (anche handcrafted):
  perderemmo VCS-tracking del pool canonico, peer-review pre-merge,
  storia git delle modifiche ai seed executor. Il pool seed e' codice,
  va trattato come codice.
* **Sotto-dir in `/opt/myclaw/`** tipo `/opt/myclaw/executors_synth/`:
  resta il problema permessi (1) + non rispetta XDG (3).
* **`/var/lib/metnos/executors/`**: scelta tradizionale per "data che
  cambia ma non e' user-specific". Funziona per un service
  multi-tenant. Metnos invece e' per-user (host + guest) e i synth
  fatti per actor=roberto possono divergere da quelli per
  actor=guest_*. `~/.local/share/` per-user e' piu' coerente con il
  modello (ADR 0035 host+guest).

## Consequences

* Multi-deployment: lo stesso codebase clonato su due host produce
  pool synth divergenti, naturali per il singolo contesto di uso.
* Multi-user su singolo host (host+guest, ADR 0035): **stato attuale
  1/5/2026 — pool synth condiviso fra tutti gli actor**. Metnos gira
  come UN solo processo daemon (es. user Linux `roberto`); `Path.home()`
  risolve sempre alla home dell'owner del processo, indipendentemente
  dall'`actor` del turno. Tutti i guest che parlano col daemon
  condividono lo stesso `~/.local/share/metnos/executors/`. Decisione
  design APERTA su separazione per-actor (pro: privacy + isolamento;
  contro: duplicazione synth equivalenti). Tre opzioni in valutazione:
  (a) pool condiviso (status quo), (b) pool per-actor sotto-dir,
  (c) pool condiviso con namespace `synth_<actor>_<name>`. Da ratificare
  in ADR successiva quando il modello guest passera' da MVP a
  produzione.
* `git status` sempre pulito anche dopo dozzine di synth-on-the-fly.
* Backup NAS deve coprire SIA `~/.local/share/metnos/` (synth + indici
  + history) SIA il git remote (`/opt/myclaw/` ricostruibile da pull).
  `backup_nas.sh` gia' lo fa correttamente (sezione `dotlocal/`).
* La regola di name-collision (handcrafted wins) e' il garante che il
  pool seed resti autoritativo: un synth non puo' "rimpiazzare" un
  handcrafted, solo affiancarlo con un name diverso.
