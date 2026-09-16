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

- `tests/runtime/infra/test_build_orchestrator.py`: 11 case (start/stop/status,
  unit naming, mock systemd-run, atomic rename, list active).
- `tests/runtime/test_build_runner.py`: 11 case (resume, SIGTERM flush,
  progress schema, batch N=500 boundary).
- `tests/runtime/infra/test_async_lifecycle.py`: 10 case (healthcheck stale,
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

## Runtime audit, 2026-09-15

The original automatic-build design above is historical, not evidence of the
current runtime. Turn `7a5207e0d0a14d1b` asked whether photo indexing was
automatic. Tutor selected executor manifests and a Quick Tour section, then
incorrectly presented an explicit manual command as a universal requirement.
The public sources lacked the complete first-search lifecycle.

The code audit found a separate operational gap:

- Commit `528526037` (2026-08-05) removed index creation from the read executor.
  `find_images_indices._index_missing_result()` now returns `index_missing`
  with `recommended_action`; it does not start a build.
- `index_missing` is an operational error, not a recoverable argument error,
  in `runtime/engine/types.py`. No central consumer of `recommended_action`
  currently turns that result into a queued prerequisite and search continuation.
- `create_images_indices` declares `intelligence="llm"`; the direct LRE
  compiler currently admits deterministic executors only. The old transient
  user-unit design must not be described as the current automatic LRE path.
- LRE completion delivery exists independently: durable events expose the
  outcome, and the outbox sends localized notices to a currently verified
  Telegram association. An admission failure is not a running indexing job.

The bilingual public LRE and Tutor guides now explain initial cost, index
reuse, asynchronous execution, delivery conditions and the current gap. A
static website publication alone does not refresh the admitted Tutor catalog.
No runtime behavior or production catalog is changed by this documentation
correction.

Automatic first-search indexing remains an open regression. Closing it requires
an unchanged content-search request against a missing index to create one
admitted asynchronous job, preserve source/owner authority, report completion
or failure honestly and reuse the completed index on a second search. Repeated
requests must not start duplicate builds. This must be proved end to end before
removing the public limitation; do not restore hidden writes inside a read
executor or bypass LRE admission.

Validation: 45 tests passed across the bilingual photo-indexing source
regressions, public-document inventory, Tutor procedure scope and durable
notification outbox. This verifies source inclusion and existing delivery
contracts, not a successful automatic indexing workflow or a changed live
Tutor answer.

## Resumable-plan implementation addendum, 2026-09-15

The implementation following the audit routes a signed missing-index
prerequisite into the registered `images.index.v1` LRE plan. Discovery,
bounded analysis groups, merge and complete-generation publication are
resumable units. The original archive remains read-only; an incomplete
generation never replaces a readable index. The earlier transient-build
architecture and the audit's missing automatic dispatch describe historical
states, not the candidate implementation.

`lre_plan` and every prerequisite's error, target and argument binding are
technical manifest fields. Birth includes them in the semantic identity;
changing a binding is not a linguistic-only revision. The grammar remains
closed at each nested object. The release preview computes that identity
from captured candidate bytes before admission, without issuing a receipt
or claiming that the candidate has been accepted.

Cold-start photo E2E has passed as the service account, with real models,
an isolated catalog and synthetic photos. Release 44 exposed a missing
identity-grammar integration during actual Birth publication; a follow-up
release is required. Its targeted identity, Birth, reconciliation and LRE
group passes 598 tests. Production publication and final live verification
are tracked separately in `internal/reports/rm0008-release-20260915.md`.

The release-46 correction also routes scheduled incremental maintenance
through this same registered plan, rather than importing the retired inline
builder. Archive location comes from the configured data root, ownership
from the live user registry, and executor authority from the verified
catalog. Incremental work can be large: LRE owns its units, resumption and
model reservations just as for a first build. Ordinary searches of an
existing index do not trigger an unconditional refresh on every query.
Production model artifacts live outside immutable releases and are selected
through the existing embedding-tier configuration, including the face role;
only declared artifact files cross the read-only sandbox boundary.

## Server prerequisite placement correction — 2026-09-16

The conversational destination is not necessarily the reader's execution
location. Ordinary dispatch already keeps server-only readers on the server.
When both verified contracts are server-only (including the default placement),
`admit_prerequisite` now passes `server` to automatic admission. This preserves
the location of the missing-index observation instead of forwarding an unrelated
conversational device. No executor names or query words select this behavior.

Either contract declaring device-only, hybrid or device-capable execution keeps
the original target. Direct remote image indexing remains unsupported; no remote
authority, owner boundary, path guard, approval or budget is relaxed. A result
cannot set placement. Rejections before submission log the fixed boundary and
exception type, never paths or arguments.

Regression tests cover both contracts, remote targets, hostile result metadata,
the real wrapper/guard/adapter/compiler/admission chain and deduplication across
retries and subsequent turns. The latter creates one isolated job without
models or production data. Live release evidence is recorded separately.

## Historical notes

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
