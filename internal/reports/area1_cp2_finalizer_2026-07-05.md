# Report AREA 1 · CP2·M2 — Finalizer unico (ADR 0177 T5) — 5/7/2026

**Riferimento**: mandato Fable Area 1 CP2 · Branch `session/detection-lexicon-i18n` (non pushato) · Prod live.

## Esito: CONSOLIDATO ✓ — S5 chiuso

### Cosa
`engine/executor._finalize_answer_text` è ora l'**UNICA fonte** del testo di un turno `answer`, con strategia dichiarata: **render** del template del proposer → **count-only→bullets** (§2.7, `MSG_RENDER_AND_MORE`) → vuoto/degenere → **zero-result deterministico i18n** (`MSG_NO_RESULTS`, mai una call LLM per dire «niente») → **synth LLM fast**.

### Cosa ha sanato (misurato ✓)
- I **due blocchi gemelli** (step `final_answer` + fallback post-loop) erano GIÀ divergenti: il fallback non arricchiva i count-only coi bullets. Ora entrambi delegano al Finalizer (terminator byte-equivalente; il fallback CONVERGE — guadagna i bullets).
- Precisazioni che correggono la mappa S5: `describe_entries` è uno **STEP** che alimenta il render (non una fonte parallela); `output_policy` è **PRE-esecuzione** (`dispatch.normalize_terminal`, modella il piano). Le fonti reali a finalize-time erano 3, non 5.
- i18n: ogni stringa **runtime** del Finalizer passa da `MSG_*`; il template del proposer è testo generato nella lingua utente (non stringa runtime) — la «i18n non garantita» di S5 riguardava questo equivoco.
- Scoperta onesta durante il lavoro: `_render_final_message` ha già un fallback interno sull'empty-template (scalare dell'ultima observation) — la mia prima lettura («risposta muta») era sbagliata; docstring e test corretti all'evidenza.

### Prove del cancello §A
- **Test**: `test_finalizer_unico.py` **7** (render-pieno-senza-LLM, bullets, zero→`MSG_NO_RESULTS`, degenere→synth, statico intatto, non-muto, **contratto anti-gemelli**: 1 sola sede della logica degenere + 2 call-site esatti) · suite area executor/engine **611 passed** · gate §2.8 + pipeline-contract verdi · **bench compound 8/8 stabile** (2 run, post-refactor).
- **Live (prod)**: «che ore sono» → «Sono le 15:42.» (render); compound etc→spreadsheet → hit L0 2.4s con messaggio corretto.
- **Commit**: `ed8022d` (refactor) · `bc33644` (test) · `87171e8`+`0ccfe07` (ADR M2/T5 FATTO + S5 chiuso + indice).

## ⚠ Residui (fuori scope CP2, per la mappa)
- S4 (Executor.run 9 responsabilità / resolver-chain non dichiarata) resta il target di **M6**; il Finalizer ne ha già estratto la sintesi finale.
- `describe_entries` skip-logic (attachments/hint) resta nel loop step — è selezione di STEP, non finalizzazione.

## Prossimo (da tua indicazione)
**AREA 2 — Remote executors: implementazione dei MUTANTI** (shim albero-package → lazy gw → read-only find/read → write/move/delete con undo round-trip + ACL Job Object; e2e sul PC reale).
