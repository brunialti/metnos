# ADR 0185 — W1: skill-learning loop dall'esito dei turni reali

- **Stato**: ACCETTATO (mandato Fable Area 3; validato unit + wiring live 5/7/2026).
- **Contesto**: il gap vs Hermes (report fattibilità 30/5): nessun trigger «task complesso → cattura capacità». L1 impara SOLO dal ✓ umano; il terminator conta le lacune (`n_seen`) ma non agisce; l'introvertiva post-0180 è solo dedupe.

## Decisione: due trigger deterministici su FATTI del turno + review notturna
Rispetta [[feedback-no-training-amplify-reality]] (si amplificano esecuzioni REALI ripetute; niente ML, il loop NON estende il vocab §2.2) e la regola dei livelli 0180 (il seed è L1-da-esecuzioni; le proposte sono executor-da-lacune; niente valori query-specific baked in L1).

### (a) Turno engine OK e COSTOSO ripetuto → autopath SHADOW
`engine/autopath.seed_from_run` (chiamata dal dispatch dopo `record_observation` sui turni `answer`): se `n_steps >= METNOS_SEED_STEPS` (4) E lo stesso `(intent, framework)` ha `>= METNOS_SEED_REPEAT` (2) osservazioni E nessun autopath active per l'intent → promozione con **`shadow=1`** (colonna additiva). Il seed è servito come hit normale (guard ADR 0174 + firme ADR 0182 validano a lettura); il **primo ✓ umano lo conferma champion** (la ri-promozione da feedback azzera shadow; una ri-semina non degrada mai una riga confermata). Chiude il cold-start ripetuto (~16-24s) dei compound costosi senza aspettare il feedback esplicito.

### (b) Lacuna ricorrente → change_intent PROPOSED
`runtime/learning_loop.propose_from_lacuna`, hook nel CHOKE-POINT `terminator._record_lacuna` (copre Simple E Metis): lacuna con `n_seen >= METNOS_PROPOSE_SEEN` (3) di classe **capability** (`out_of_scope`/`wrong_tool` — `wrong_args`/`missing_input` sono errori d'USO, esclusi) → `change_intent` `create_executor` in **PROPOSED**: il triage umano su `/admin/changes` resta il gate, l'admission 6-layer vale alla materializzazione. NIENTE sintesi inline. Anti-spam/anti-resurrezione per costruzione via `upsert_intent`: dedup per fingerprint (convergence bump) + stato REJECTED **preservato** (testato).

### (c) Review notturna
`task_learning_loop_review` (in `NIGHTLY_SEQUENCE` dopo state_reaper): pota i seed shadow MAI confermati e non usati da `METNOS_SHADOW_TTL_DAYS` (21) + conteggi per dashboard/log. (Il mandato indicava every_72h: consolidato nel notturno esistente — review leggera, un'entry in meno in dashboard; soglia da calibrare con Roberto.)

## Prove
- `test_learning_loop.py` **10/10**: seed dopo ripetizione / turni economici mai seminati / no-doppioni / ✓-conferma-champion; lacuna→intent / convergenza senza duplicati / REJECTED non risorge / classi d'uso escluse; review pota gli stantii.
- Wiring live in prod (restart 5/7 sera). Dati reali pronti: la coppia `find|issues` (109 osservazioni, 4 step, nessun autopath) verrà **seminata dal turno notturno ricorrente** (~01:22) — verifica: `SELECT id, shadow FROM autopaths WHERE intent_sig LIKE 'find|issues%'`.

## Decisioni APERTE (per Roberto, dal report §8)
1. **Triage vs auto-apply** delle proposte learning_loop: oggi = TRIAGE (`/admin/changes`), nessun auto-apply.
2. **Calibrazione soglie**: SEED_STEPS=4, SEED_REPEAT=2, PROPOSE_SEEN=3, SHADOW_TTL=21gg (tutte env).
3. W2 user-prefs (storage USER.md vs tabella) — fuori scope W1.
