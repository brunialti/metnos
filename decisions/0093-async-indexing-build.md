---
id: 0093
title: Async indexing build via systemd user transient unit + async tasks
date: 2026-05-06
status: accepted
area: runtime, executor, ux, lifecycle
related:
  - 0086  # indici di dominio image (scene/persons/gps)
  - 0090  # get_inputs (approval card pattern)
complements:
  - 0086
---

## Context

Build degli indici immagine (SigLIP scene, ArcFace persons, EXIF gps) su
corpora grandi (30k+ foto) richiede 50-100 minuti CPU. Stato pre-ADR 0093:
`find_images_indices` con `force_build=true` invocava `create_images_indices`
come **subprocess inline sincrono** dentro al thread di `run_turn`.

Esperienza live 5/5/2026 sera (turn `19178685`):
- Roberto ha allegato 3 foto e chiesto «cerca questa persona».
- PLANNER ha emesso approval card per build `persons` (~6127s stimati).
- «Sì» → subprocess SigLIP/ArcFace al 178% CPU per 102 minuti.
- Browser HTTP: SSE keepalive 15s teneva il TCP, ma dopo Ctrl+Shift+R o
  tab inattivo il watchdog 30s scattava → utente vedeva «silenzio dal
  server da 30s, ricarica e riprova».
- Build proseguiva server-side a prescindere, ma **UX rotta**.

Vincoli ribaditi (Roberto, 6/5/2026 mattina):

> «soluzioni semplici, robuste e resilienti, che non lascino mai processi
>  appesi o zombie e non creino problemi al restart. il sistema deve
>  essere robusto e non creare problemi in caso di restart»

> «il piu possibile, dove è conveniente, usare soluzioni async e/o
>  threads. ma presidiate e robuste»

## Decision

Build pesante eseguita in **systemd user transient unit** (process
isolato, gestito da systemd, no Popen) + **async tasks** nel daemon HTTP
per orchestrazione, healthcheck, notification dispatch e cleanup.

### Componenti

1. **`runtime/build_runner.py`** — script entry-point invocato da systemd-run.
   Argomenti CLI: `--base-path`, `--idx {scene,persons,gps}`, `--actor`,
   `--channel`, `--chat-id`, `--resume` (default true).
   - Walk recursive image extensions (`.jpg/.jpeg/.png/.heic/.webp/.tiff`).
   - Encode batch N=500: aggiorna `progress.json` con `next_index`,
     `n_done`, `n_total`, `eta_s`, `last_update`. Persiste partial in
     `<idx_dir>/.tmp_<rand>/{entries.jsonl, vectors.npy}`.
   - SIGTERM handler: flush progress + partial, exit 0 → resume al prossimo start.
   - A fine: `os.rename(tmp_dir, idx_dir/idx)` atomico. Scrive marker
     `/tmp/metnos_build_complete/<digest>_<idx>.json` con
     `{ok, n_entries, duration_s, errors_count, actor, channel, chat_id}`.
   - Crash inatteso: `.tmp_*` orfano, sweep async lo pulisce dopo 7gg.

2. **`runtime/build_orchestrator.py`** — API control-plane:
   - `start_async_build(base_path, idx, *, actor, channel, chat_id) -> dict`
     - Computa `unit_name = f"metnos-build-{sha8(base_path)}-{idx}"`.
     - Se unit attivo → `{ok: True, already_running: True, progress: ...}`.
     - Else: `subprocess.run(["systemctl", "--user", "start", "--quiet",
       "--transient", "--unit", unit_name, "--", venv_python, "-m",
       "runtime.build_runner", ...])`. Ritorna in <100ms.
   - `get_build_status(base_path, idx) -> dict | None`
     - Legge `progress.json` + `systemctl --user is-active`. Stato:
       `running | done | aborted | not_started`.
   - `stop_build(base_path, idx) -> dict`
     - `systemctl --user stop`. Cleanup `.tmp_*`.
   - `list_active_builds() -> list[dict]`
     - Itera `~/.local/state/metnos/build_progress/*.json`.
   - `cleanup_orphan_tmp_dirs(...)`: sweep `.tmp_*` non in uso (>7gg).

3. **`runtime/http_async_tasks.py`** — 3 task async lifecycle:
   - **`progress_healthcheck_task`** (ogni 30s): per ogni progress.json
     attivo, se `mtime > 300s stale` E unit attivo → `systemctl --user
     stop` + log warning. Se `!unit_active` E `state != "done"` →
     marca `state="aborted"`.
   - **`notification_dispatcher_task`** (ogni 10s): scansiona
     `/tmp/metnos_build_complete/*.json`. Per ogni file: invia
     `send_messages(to_user=actor, via_channel=channel, body=...)` con
     riassunto build. Sposta in `~/.local/state/metnos/build_completed_archive/`.
   - **`tmpcache_sweeper_task`** (ogni 24h): cleanup `.tmp_*` orfani >7gg
     + archive marker >30gg.

   Tutti registrati via `register_async_tasks(app)` chiamato da
   `make_app()` in `metnos_http_server.py`. `try/except + log + re-spawn`
   al daemon restart (gestiti da `app.on_startup`).

4. **`executors/find_images_indices/find_images_indices.py`** — modificato:
   - Threshold sync vs async: se `_estimate_build_cost.time_s > 120s` →
     async path (orchestrator). Else → sync inline (caso veloce, <100 file).
   - Async path: `start_async_build(...)` → ritorna IMMEDIATO con
     `{ok: True, build_started: True, eta_min: ..., notice: "Indice
     in costruzione in BG. Ti avviso al completamento."}`.
   - Query mentre build in corso: `get_build_status` → notice
     «build at X% (~Y min)».

5. **Endpoint `/admin/builds`** — dashboard htmx-aware (admin only):
   GET ritorna list `{digest, base_path, idx, state, n_done, n_total,
   eta_s, started_at, age_s, unit_active}`.

### Pattern di robustezza

| Vincolo | Implementazione |
|---|---|
| Niente zombie | systemd reaps; unit `Type=simple` con sigterm handler in build_runner |
| Restart-safe | `progress.json` checkpoint + SIGTERM flush; resume da `next_index` |
| Atomic write | `<idx_dir>/.tmp_<rand>/` poi `os.rename()`; crash → orfano + sweep |
| Lock at-most-once | unit name unico → `systemctl --user start` rifiuta duplicati |
| Healthcheck | async task: stale >5min → kill+log; unit dead + state≠done → mark aborted |
| Cleanup | tmpcache_sweeper 24h: `.tmp_*` >7gg; archive marker >30gg |
| Notification | dispatcher async: marker `/tmp/metnos_build_complete/*.json` → send_messages |
| Disable hook | `METNOS_HTTP_DISABLE_BUILD_TASKS=1` env per test isolati |

## Consequences

### Positive

- **UX**: chat HTTP/Telegram NON blocca durante build pesanti. Roberto
  può fare altre query mentre indice si costruisce.
- **Crash-safe**: shutdown daemon non corrompe indice. Resume riprende
  dal checkpoint. Indice precedente (se esisteva) intoccato fino al
  rename atomico finale.
- **Zero zombie**: systemd ciclica reap di processi. Niente Popen detached.
- **Trasparenza**: `/admin/builds` dashboard + `journalctl --user -u
  metnos-build-<digest>-<idx>` per log per-build.
- **Notification**: utente avvisato al completamento via canale
  d'origine (Telegram/HTTP). Non deve fare polling manuale.

### Negative

- **Dipendenza systemd-user**: deve essere disponibile e configurato
  (XDG_RUNTIME_DIR). Ok perché HTTP daemon gira come `User=roberto`,
  user manager è già attivo.
- **Cross-process state**: `progress.json` come canale di comunicazione
  fra build_runner (subprocess) e HTTP daemon (parent). File system
  semantics OK per single-host self-hosted.
- **Sweep settimanale necessario**: orphan `.tmp_*` accumulano se daemon
  HTTP è morto durante un crash mid-build. tmpcache_sweeper_task copre.

### Neutral

- **Threshold sync 120s**: build piccole restano inline (semplicità).
  Build >2min → async (UX). Soglia rivedibile.
- **Notification via send_messages**: riusa executor esistente. Cross-channel
  routing già rodato (ADR 0083).

## Test

- `runtime/tests/test_build_orchestrator.py`: 11 case (start/stop/status,
  unit naming, mock systemd-run, atomic rename, list active).
- `runtime/tests/test_build_runner.py`: 11 case (resume, SIGTERM flush,
  progress schema, batch N=500 boundary).
- `runtime/tests/test_async_lifecycle.py`: 10 case (healthcheck stale,
  notification dispatch, tmpcache sweep).
- **32/32 PASS** post-implementazione.
- Smoke regression IT 8/8 OK.

## Alternatives considered

- **Popen detached + `setsid()` + signal handlers manuali**: scartato.
  Tutti gli edge case (zombie, doppia rea, log scattering) sono già
  risolti da systemd. Riscrivere in Python è anti-pattern §7.2.
- **Threading nel daemon HTTP** (ThreadPoolExecutor): GIL + heavy CPU
  bound → thread serve solo come "yield della call". Restart daemon
  perde il thread. Niente isolation.
- **Celery/Dramatiq broker queue**: sovradimensionato per single-host
  self-hosted. Aggiunge Redis/RabbitMQ.
- **subprocess.Popen + thread monitor**: stesso problema di Popen
  detached, niente vantaggio.
- **systemd transient unit con DBus API diretta** (vs subprocess
  systemctl): più pulito ma libreria extra. systemctl CLI è già stabile
  e self-contained.

## Notes

- Threshold 120s è euristico iniziale. Telemetria future può aggiornare.
- Notification: per ora send_messages testuale. Futuro: payload arricchito
  con thumbnail dei top-K risultati al completamento (richiede
  pre-eseguire query default su indice nuovo).
- Build incrementale (refresh): solo file con mtime > last_refresh.
  Riusa stesso pattern. ETA ridotto proporzionalmente.

## References

- ADR 0086: indici immagine + create_images_indices
- ADR 0090: get_inputs + approval card
- ADR 0083: cross-channel routing send_messages
- CLAUDE.md §7.1 (no backward-compat), §7.2 (semplicità), §7.9 (no LLM
  critical path), §2.4 (robustezza confine NL→determinismo)
- Memoria `metnos_async_indexing_todo.md` (TODO HIGH del 5/5/2026 sera)
