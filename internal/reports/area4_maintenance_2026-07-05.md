# Report AREA 4 — Gestione/manutenzione — 5/7/2026

**Riferimento**: mandato Fable Area 4 · ADR 0186 · requisito 🚨 `feedback_maintenance_via_scheduled_commands`.

## Esito: CONSOLIDATO ✓ (constatazione+bonifica+formalizzazione: la migrazione era già avvenuta)

### Item 1 — anti-pattern github_watcher
- **Già ritirato** nei fatti (sorgenti assenti, commento «Fase D: RITIRATO»); il flusso vive come **comandi NL schedulati**: flusso A `user_trova_le_issue_aperte…` every_30m ENABLED (**825 run / 0 failure**; fire di verifica = TURNO REALE `kind=answer`, 600ms = hit L0: il comando ricorrente è APPRESO come ogni query utente — il §2.1/§8.3 realizzato); flusso B `user_pubblica_issue_github_answered` esiste DISABLED deliberato (FASE 3 pubblicazione manuale).
- **Bonifica eseguita**: riga scheduler morta `github_watcher` (callback inesistente) rimossa; 3 `.pyc` orfani rimossi; task 33/34 superseded lasciati DISABLED (origine utente). Store dati consolidato in `store_bootstrap` (`github_issue_qa`), riferimenti codice puliti.
- **Formalizzazione**: ADR 0186 + nota superseded in ADR 0141.

### Item 2 — analisi meccanismi + confine documentato
- **Confine comando-vs-bespoke** (in ADR 0186): dati/servizi utente → comando NL (`/admin/timers`, modificabile dalla chat); organi interni (aging, reaper, i18n, index, promoter, learning_loop_review) → builtin registrati in `NIGHTLY_SEQUENCE` (SoT ordine, error-isolation); osservatori esterni (audit Fable) → systemd timer fuori dal runtime.
- **Aging-inattività**: esenzioni verificate NEL CODICE (`executor_aging`: handcrafted verb-unique mai, `PROTECTED_NAMES` mai; solo `synth:*` invecchiano) — trappola chiusa alla fonte.
- **Scheduler v2**: 15 entry post-bonifica; run log; enable/disable/fire dalla dashboard.

## Prove
Fire reale task 35 → run 825 success + turno `kind=answer` nel log · bonifica idempotente · commit `f52a6c4` (+claude/indice/report). Nessuna modifica al runtime: zero rischio regressione (suite non toccata da quest'area).

## Residui (non bloccanti)
- FASE 3 (pubblicazione risposte issue) resta manuale finché Roberto non abilita il task 36.
- I 2 task superseded disabled (33/34): cancellarli è una scelta di Roberto (origine utente).
