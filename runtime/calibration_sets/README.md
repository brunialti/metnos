# Planner-split calibration sets

Per-language thresholds for the PLANNER split decision (ADR 0151).

## Lookup order (3-level fallback)

`runtime/calibration_check.py::ensure_calibration(lang)` cerca:

1. **User override**: `~/.config/metnos/planner_split_calibration.json`
   - Se esiste E `data["lang"] == lang` E non e' stale → ritorna quello.
2. **Library pre-baked** (questa directory): `runtime/calibration_sets/<lang>.json`
   - Se esiste → ritorna quello.
3. **Conservative default in-memory**: `threshold_default=0.80`,
   nessun threshold_by_verb, rank_distance_min=0.15.
   - Caller puo' opzionalmente schedulare un task one-shot v2
     `calibrate_planner_split(lang=<lang>)` per generare la calibration
     reale in background (idempotency key `calibration_<lang>`).
     Finche' il task non completa, il default in-memory e' usato.

## Lingue pre-baked attualmente

| lang | threshold_default | notes |
|------|------------------:|-------|
| `it` | 0.80 | Placeholder, real calibration pending |
| `en` | 0.75 | Placeholder, -5pt vs IT per bias linguistico Gemma |

Soglie deliberatamente conservative finche' bench corpus #H0c.2 non
dimostra sicurezza su verbi mutating. Quando il bench reale gira,
soglie scenderanno (riducendo % fallback monolithic).

## Per aggiungere una nuova lingua

1. Genera la calibration: `python -m runtime.calibrate planner-split --lang es`
   (CLI non ancora implementata — vedi #H0e in
   [[project_pending_2026_05_19_v3]]).
2. Verifica il file generato in `~/.config/metnos/planner_split_calibration.json`.
3. Copia in `runtime/calibration_sets/es.json`.
4. Apri PR.

## Per override personalizzato

```bash
cp runtime/calibration_sets/it.json \
   ~/.config/metnos/planner_split_calibration.json
# edita le soglie come preferisci
```

Override non subisce update se il file ha `lang` corretto e non e' stale.

## Staleness check (deferred)

Un calibration set diventa stale quando:
- `metnos_commit` differisce di > 30 giorni di log E pool tool e' cambiato
  (nuovi executor che alterano il pool top-K medio).
- Modello LLM cambiato (`model` field differente da quello configurato).
- Schema `version` bumped.

Auto re-generation in background ogni 90 giorni (idempotent via key).
Implementazione: task scheduler v2, vedi `runtime/scheduler_v2/builtin_callbacks.py`.

## Schema

Vedi `_schema.json` (JSON Schema draft-07).
