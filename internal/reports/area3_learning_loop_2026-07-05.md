# Report AREA 3 — Introversione: W1 skill-learning loop — 5/7/2026

**Riferimento**: mandato Fable Area 3 · ADR 0185 · Branch `session/detection-lexicon-i18n` (non pushato) · Prod live (restart sera).

## Esito: W1 CONSOLIDATO ✓ — analisi parzialmente rimandata (dichiarato sotto)

### Intervento W1 (il cuore dell'area)
- **(a) Seed shadow**: turno engine OK e costoso (n_step≥4) ripetuto (≥2 osservazioni stesso intent+framework) → autopath `shadow=1` senza aspettare il ✓ umano; servito come hit normale (guard 0174 + firme 0182); il primo ✓ lo conferma champion. Colonna additiva, migrazione preserva-dati.
- **(b) Lacuna→proposta**: hook nel choke-point `_record_lacuna` (copre entrambi i terminator): n_seen≥3 su classi capability → change_intent `create_executor` PROPOSED → triage `/admin/changes`; admission 6-layer al materialize. Anti-spam (dedup fingerprint) e **anti-resurrezione** (REJECTED preservato) per costruzione, testati.
- **(c) Review notturna** `learning_loop_review` in NIGHTLY_SEQUENCE: pota seed mai confermati (TTL 21gg) + conteggi.
- Vincoli rispettati: [[feedback-no-training-amplify-reality]] (solo esecuzioni reali ripetute, no ML), regola dei livelli 0180 (seed=L1 generalizzato; proposte=executor; niente baking L0), vocab §2.2 mai esteso dal loop.

### Prove
`test_learning_loop.py` **10/10** · sweep aree toccate 341 pass · wiring live in prod · dati reali pronti: coppia `find|issues` (109 osservazioni, 4 step, 0 autopath) → **seed naturale al turno notturno ~01:22**; verifica domattina: `SELECT id, shadow FROM autopaths WHERE intent_sig LIKE 'find|issues%'`. Commit `de64442`→`93918d1` + ADR/claude/indice.

### Strada facendo (Area 2 spillover richiesto da Roberto in sessione)
- Censimento remoted: **11 device_ok** (7 files-core + find_dirs/find_packages/get_processes/create_dirs) · 94 non-remoted (85 server-side per natura; esclusi documentati: delete_* D3, famiglia _xlsx/_ocr/_doc → canale wheels follow-up).
- `get_processes` cross-platform (tasklist) + **onestà §2.8 sullo snapshot vuoto** → ha scovato `SystemRoot` mancante nella sandbox → client **0.2.14** (env di sistema Windows) → processi VERI dal PC in chat. 3ª migrazione self-update automatica consecutiva (0.2.12→13→14).

## ⚠ Analisi Area 3 NON completate in questa sessione (per il cancello)
1. **Telos «a secco» (§G)**: generazione con distribuzione EA reale — non eseguita (richiede una sessione dedicata; la coda Fable la tiene).
2. **Audit formale livelli su TUTTI i generatori/adapter**: la regola è operativa (0180 layer_overlap killer) e W1 la rispetta per costruzione; l'audit sistematico carta-contro-codice dei 4 adapter attivi resta da fare.

## Decisioni APERTE per Roberto (ADR 0185 §finale)
1. **Triage vs auto-apply** delle proposte learning_loop (oggi: triage).
2. **Soglie**: SEED_STEPS=4 / SEED_REPEAT=2 / PROPOSE_SEEN=3 / SHADOW_TTL=21gg (env).
3. W2 user-prefs storage (USER.md vs tabella) — prossimo workstream candidato.

## Cosmetici notati (non bloccanti)
- Turno device: il titolo del describe dice «📊 Stato server» anche quando i dati vengono dal PC (l'header è del template health; il tag 📍 device resta corretto a piè). Follow-up presentazione.
