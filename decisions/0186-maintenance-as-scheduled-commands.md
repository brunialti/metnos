# ADR 0186 — Manutenzione domini esterni = comandi schedulati NL (ritiro github_watcher)

- **Stato**: ACCETTATO (constatazione + bonifica 5/7/2026, mandato Fable Area 4; requisito Roberto `feedback_maintenance_via_scheduled_commands`).
- **Sostituisce**: la sezione «watcher» di ADR 0141 (`runtime/jobs/github_watcher.py`, ritirato).

## Principio (non negoziabile)
La manutenzione di un DOMINIO ESTERNO (issue GitHub, mail, versioni software…) NON è un programma core ad-hoc: è una **query schedulata in linguaggio naturale** (`scheduler_v2`, `callback_key=run_user_query`, `payload={query,…}`) che compone gli EXECUTOR via planner. §2.1/§7.2/§8.3: stessa strada dei turni utente — stessa cache L0/L1, stessi guard, stessa onestà, ispezionabile e modificabile DALLA CHAT.

## Stato di fatto (verificato 5/7)
- Il watcher bespoke era GIÀ stato ritirato (sorgenti rimossi; commento «Fase D: RITIRATO» in builtin_callbacks; flusso documentato in `internal/reports/github_maintenance_flow.html`). Questo ADR lo FORMALIZZA.
- **Flusso A (leggi/classifica)**: task `user_trova_le_issue_aperte_di_brunialti_metno` — every_30m, ENABLED, **825 run / 0 failure** (fire di verifica 5/7: turno reale `kind=answer`, 600ms = hit L0: il comando ricorrente è APPRESO dalla cache come ogni query utente). Store dati: `github_issue_qa` via store-registry (`store_bootstrap`), non moduli dedicati.
- **Flusso B (pubblica risposte approvate)**: task `user_pubblica_issue_github_answered` — esiste, **DISABLED deliberato** (FASE 3 manuale: la pubblicazione resta un gesto umano finché Roberto non la abilita).
- **Bonifica**: riga scheduler morta `github_watcher` (callback inesistente, errore a ogni enable) RIMOSSA; 3 `.pyc` orfani rimossi (`github_watcher`, `github_watch_state`, `github_issue_qa_store`); i 2 task utente superseded (id 33/34) restano DISABLED (origine utente: non si cancellano d'ufficio).

## Confine COMANDO vs BESPOKE (documentato, l'analisi del mandato)
| Attività | Forma | Perché |
|---|---|---|
| Domini esterni/utente (issue, mail, web-check) | **comando NL schedulato** (`run_user_query`) | componibile via executor, ispezionabile in `/admin/timers` (label+query nel payload), modificabile dalla chat, impara in cache |
| Housekeeping degli ORGANI di Metnos (aging executor/mnest, state reaper, i18n translate, image index, promoter, learning_loop_review) | **builtin callback registrato** (`nightly_maintenance` → `NIGHTLY_SEQUENCE`, SoT dell'ordine) | opera su store interni con invarianti (§7.10 firme, §2.3 reverse, GPU-safety): non è esprimibile come composizione di executor utente; resta ispezionabile (dashboard timers, chiavi callback) e modificabile come codice+ADR |
| Audit notturni Fable | **systemd user timer ESTERNO** | audita il runtime dal di fuori: giusto che non dipenda dal runtime che audita |

Regola pratica: se tocca **dati/servizi dell'utente** → comando NL; se tocca **gli organi interni** → builtin registrato; se **osserva il sistema dall'esterno** → timer di sistema.

## Meccanismi verificati (item 2 mandato)
- **Aging-inattività**: esenzioni NEL CODICE (`executor_aging`: handcrafted verb-unique mai; `PROTECTED_NAMES` seed core mai; invecchiano solo i `synth:*`) — la trappola `reference_aging_inactivity_trap` è chiusa alla fonte.
- **Scheduler v2**: 15 entry attive post-bonifica; run in `runs` table; `/admin/timers` con enable/disable/fire.
- **Notturni**: sequenza unica dichiarata (`NIGHTLY_SEQUENCE`), error-isolation per task.
