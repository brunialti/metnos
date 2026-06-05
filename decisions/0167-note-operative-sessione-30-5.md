# ADR 0167 — Note operative consolidate (sessione 30/5/2026)

**Date**: 2026-05-30 (consolidato 2026-06-01)
**Status**: accepted
**Related**: ADR 0112 (scheduler v2 asyncio co-host), ADR 0074 (scheduler gate user-activity), ADR 0090 (engine UI dichiarativo / dialog), ADR 0122 (proposal auto-evaluator), ADR 0078 (HTTP API), ADR 0117 (unified image index), §7.4 (no parallelismo senza speedup), §10.6 (regola d'oro 1-riga/meccanismo)

## Context

La sessione 30/5/2026 ha prodotto nove note operative durature (scheduler,
reaper, dialog, promoter, UI timer, proposer, SSE, NOPASSWD, workflow). Erano
state registrate inline in `CLAUDE.md` §10.6 come voci multi-riga datate, con
env vars e gotcha — in violazione della regola d'oro della sezione («una riga
per meccanismo; dettagli, env vars, bench numbers, date → ADR»). Questo ADR
assorbe il dettaglio; §10.6 conserva solo il pointer di 1 riga (anti-regressione).

## Decision

### 1. Consolidamento scheduler builtin
- `nightly_aging` daily@03:30 = unione di `apply_executor_ager` + `apply_ager`.
- `state_reaper` daily@03:40 = reaper UNICO dello stato persistente:
  `undo` / `_history-blob` / `http_cache` / `location` / `skill_fetch` /
  `install_resume` / `approval_registry` / `turns` / `autopath`. Retention via
  env `METNOS_*_RETENTION_DAYS`.
- GPU-heavy `telos_introspect_nightly` + `intent_classifier_retrain` →
  `every_72h`, staggerati (env `METNOS_TELOS_INTROSPECT_INTERVAL_H` /
  `METNOS_INTENT_RETRAIN_INTERVAL_H`, default 72).
- `i18n_translate_pending` → `every_6h` (cap `METNOS_I18N_CAP_PER_FIRE` def 20).
- Ritirati gli stub `synt_suggest` / `introvertiva_apply`.
- Rimossi i systemd `metnos-i18n-translator.timer` / `.service` (1 sola coda i18n).
- **GOTCHA**: la migrate dello scheduler SALTA i builtin già esistenti →
  editare `_BUILTIN_JOBS` NON aggiorna il DB; serve `UPDATE schedule_entries`.

### 2. Reaper sempre WIRED (regola)
Ogni `cleanup* / sweep* / purge* / gc*` DEVE avere un call-site reale (job
scheduler o invocazione). Un reaper definito e mai chiamato accumula stato
silenziosamente (lezione `dialog_pending.cleanup_expired`). Verificare i
chiamanti con grep (escludendo test/docstring), non assumere.

### 3. UI gestione timer
`GET /admin/timers` + `POST /admin/timers/{name}/{enable|disable|fire}`
(`http_routes_admin.py`) + `SchedulerStorage.enable()`. Tutti i timer di
sistema visibili/gestibili (link in dashboard).

### 4. Promoter kill-switch grace a esito (L3.5)
`jobs/promoter.py::_grace_killswitch` — auto-promozione + ritiro su segnale
negativo (turn-log `error` / `scope_violation`) durante grace. OSSERVA di
default (`METNOS_PROMOTER_KILLSWITCH_ENFORCE=0`, `_ROLLBACK_FAILS=2`); notifica
Telegram admin (i18n `MSG_KILLSWITCH_*`); `resurrect_from_archive` accetta
anche `rolled_back`.

### 5. Dialog TTL + sweep
`dialog_pending.list_pending` salta gli scaduti; `dialog_pending_sweep`
every_1m chiude+notifica STESSO canale (`send_messages` to_user/via_channel,
i18n `MSG_DIALOG_AUTOCLOSED`); TTL 60s default / 600s form-credenziali
(`default_timeout_for`, env `METNOS_DIALOG_TTL_S` / `_FORM_TTL_S`);
`save_pending` atomico (tmp + os.replace).

### 6. engine_proposer pattern H (classify→filter)
Dopo `classify_entries(dimension=D, classes=[...])` filtrare con
`filter_entries(where_field=D, where_value=<classe>)`, MAI `kind` / `type`
(matchano `entry.kind` / `type` del dominio file → 0 risultati). In
`prompts/{it,en}/engine_proposer.j2`.

### 7. chat.html SSE resumable
`onerror` recupera l'esito via `GET /agent/turns/{id}` invece di marcare ✗
(navigate-away NON è errore; il turn è resumable e completa lato server).

### 8. NOPASSWD restart + VLM accuratezza
`/etc/sudoers.d/metnos-http` → `systemctl restart|start|stop
metnos-http.service` senza password (admin/agente). VLM accuratezza: env
`METNOS_VLM_{MAX_EDGE,MAX_TOKENS,CTX,SLOTS}`; `resume_revlm` in
`create_images_indices` riempie solo l'asse VLM riusando SigLIP + volti.

### 9. Workflow rate-limit 429
~16 agenti concorrenti → 429 («server limiting, NOT your usage limit»);
chunkare in ondate da ~5 (`for wave: await parallel(...)`).

## Consequences

- `CLAUDE.md` §10.6 torna conforme alla regola d'oro (1 riga/meccanismo); il
  dettaglio operativo (env vars, gotcha) vive qui e resta a un click.
- Nessuna perdita di informazione: ogni env var, call-site e gotcha è preservato.
- Le note 1-8 sono accepted (in produzione dal 30/5); la 9 è una norma di
  processo per l'orchestrazione workflow.
