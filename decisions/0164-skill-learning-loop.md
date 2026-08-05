# ADR 0164 — Skill-learning loop esplicito

**Status**: proposed
**Date**: 2026-05-31
**Context phase**: convergenza competitiva W1; l'implementazione successiva è
consolidata in ADR 0185. La conoscenza dell'utente, separata da W1, è tracciata
in `internal/roadmap/RM-0001-conoscenza-utente-locale.md`.

## Context

Hermes Agent (Nous Research) ha come feature distintiva un *closed learning loop*:
dopo un task complesso scrive una skill riusabile, la indicizza e la ricarica
su task simili (+40% velocità su ricorrenze). OpenClaw no, Metnos a metà.

Metnos ha già TUTTE le infrastrutture base ma non l'orchestrazione che le lega:
- **autopath** (`runtime/engine/autopath.py`, `autopath.sqlite`): impara framework
  cache da feedback ✓ utente (`record_feedback`→`_promote_skill`, champion/challenger,
  anti_skill TTL 30gg). NON auto-semina da turni engine riusciti ma costosi.
- **terminator** (`runtime/engine/terminator.py`, `terminator_log.sqlite`): traccia
  `lacune.n_seen` su fallimenti ricorrenti ma NON agisce.
- **introvertiva** (`runtime/introvertiva.py`): SPECIALIZE su turn JSONL, MVP solo
  identificazione, no auto-promote.
- **synt** (`runtime/synt_multistage.py`) + admission 6-layer (`skill_admission.py`):
  codegen executor + gate (vocab, Jaccard ≥0.5, ager, smoke, verifier).
- **change_intent** (`runtime/change_intents.py`): lifecycle proposte proposed→finalized.

Gap: nessun trigger automatico "task complesso → cattura capacità riusabile da sé".

## Decision

Aggiungere un **loop di apprendimento a due soglie**, ancorato a fatti del turno
(no ML, coerente con la regola "amplify reality, no training"):

1. **Seed da successo costoso** (autopath): a fine turno, se `match_source=engine`
   e `n_step ≥ SEED_STEPS` (default 4) e turno OK → `autopath.seed_from_run()`
   registra il framework come *shadow* (non champion). Vince solo se la query si
   ripete (riusa champion/challenger esistente). Innesto: `runtime/engine/dispatch.py`.

2. **Proposta da fallimento ricorrente** (synt): in `terminator.explain()`, dopo
   `_record_lacuna`, se `lacuna.n_seen ≥ PROPOSE_SEEN` (default 3) →
   `introvertiva.propose_from_lacuna()` → emette `change_intent(kind=create_executor|
   extend, origin_family=learning_loop)`. NON sintetizza inline: solo proposta,
   l'admission 6-layer resta gate. Innesto: `runtime/engine/terminator.py` +
   nuova `propose_from_lacuna` in `runtime/introvertiva.py`.

3. **Review periodica**: nuovo builtin scheduler `learning_loop_review` (every_72h,
   staggerato coi GPU-heavy) consolida shadow→active, pota anti_skill, ranka proposte.

Soglie via env: `METNOS_LL_SEED_STEPS`, `METNOS_LL_PROPOSE_SEEN`, `METNOS_LL_REVIEW_EVERY`.
Default conservativi. Storage riusato: `autopath.sqlite` (campo shadow/champion) +
`change_intents.sqlite`. Nessun nuovo sottosistema.

## Consequences

**Positive**: Metnos cattura capacità da sé (iniziativa autonoma, non auto-applicata);
↑ % turni risolti da fastpath/autopath nel tempo; ↓ lacune ricorrenti. Colma il gap
percepito vs Hermes con ~3-5 gg di estensione (non riscrittura).

**Negative/rischi**: tuning soglie (mitigato: env + default conservativi + osserva-prima);
rumore proposte synt (mitigato: admission 6-layer + triage `/admin/changes`, niente
auto-apply iniziale). Le proposte NON estendono il vocabolario §2.2 da sole (governance
3 criteri resta).

**Sicurezza**: tutto passa da vaglio (runtime) + admission (synt). Seed autopath è shadow,
onesto §2.8 (vince solo su ripetizione reale). Nessuna capacità bypassa i gate.

## Alternatives considered

1. **Skill a testo libero stile Hermes/OpenClaw** (`.md` non vincolate): scartato —
   Metnos ha vocabolario chiuso + codegen, superiore per determinismo. Le `.md` semmai
   come seed verso synt, non sostituto.
2. **Auto-apply proposte synt** (no triage): scartato per l'avvio — prima triage
   `/admin/changes`, poi eventuale auto-promote dietro kill-switch grace (come
   `jobs/promoter.py`) quando maturo.
3. **ML retrain su turni** (fine-tune ranker): scartato — viola "amplify reality";
   cache+seed danno il grosso del beneficio a costo zero.

## Decisioni aperte (da fissare con Roberto prima dell'implementazione)
- Calibrazione SEED_STEPS=4 / PROPOSE_SEEN=3 / REVIEW_EVERY=15 su dati turn JSONL reali.
- Auto-apply vs triage (default proposto: triage).
- Storage user-prefs collegato (W2): `USER.md` vs tabella — fuori scope di questo ADR.
