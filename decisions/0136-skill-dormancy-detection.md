---
id: 0136
title: Skill dormancy detection + provider qualifier formalization
date: 2026-05-15
status: accepted
area: loader | prefilter | naming
related:
  - 0123  # skill importer agentskills.io
  - 0132  # backend plugins external
complements:
  - 0123
---

## Context

ADR 0123 ha introdotto l'importer skill (`metnos-skills import`) che
installa N executor in `~/.local/share/metnos/executors/_imports/<skill>/`.
Esempio attuale: `google-workspace` skill installa 17 executor
`*_google_workspace` (gmail, drive, calendar, contacts, ecc.).

Problema operativo: questi executor sono visibili al PLANNER nel
top-K del prefilter MA possono essere inutilizzabili se le credenziali
esterne non sono presenti/valide (OAuth flow non completato, token
scaduto, refresh fallito). Bug live: PLANNER routava `send_messages_google_workspace`
senza OAuth attivo → executor fail con error_class="missing_credentials".

Inoltre il suffix `_google_workspace` non rientrava nelle 3 famiglie
qualifier §2.2 (`formato/codifica`, `modalita'`, `safety policy`).

## Decision

### (A) 4ª famiglia qualifier "provider"

§2.2 esteso a 4 famiglie qualifier:
- formato/codifica
- modalita' (operazione/granularita'/mezzo)
- safety policy
- **provider** (15/5/2026): backend specifico non-default.
  - `_google_workspace` (skill gmail/drive/calendar ADR 0123)
  - `_metnos` (default locale implicito, di solito omesso)
  - Estensibile a `_outlook, _slack, _telegram` per skill future

Lookup `tool_grammar._PROVIDER_SUFFIX_MARKERS`: filtra dal pool grammar
gli executor con suffix provider quando la query non contiene un marker
semantico associato (`google/drive/gmail/workspace` per
google_workspace).

### (B) Dormancy detection deterministico §7.9

Nuovo modulo `runtime/skill_credentials.py`:

```python
def compute_dormancy(provenance: dict) -> (dormant: bool, reason: str):
    skill = parse_skill_from_provenance(provenance)  # "google-workspace"
    if skill not in _CHECKS:
        return False, ""  # graceful: skill sconosciuta NOT dormant
    return invert(_CHECKS[skill]())
```

Registry `_CHECKS: dict[skill_name, callable]`. Oggi:
- `google-workspace`: verifica `~/.local/share/metnos/skills/
  google-workspace/google_token.json` esistente + `refresh_token`
  valorizzato.

Plugin esterni (ADR 0132): possono registrare il loro check tramite
entry-point future. Default skill sconosciuta → `dormant=False` (degrade
graceful, non castrare custom skill).

### (C) Executor.dormant + filter_dormant

`runtime/loader.py::Executor`:
- nuovo campo `dormant: bool = False`
- nuovo campo `dormant_reason: str = ""`
- calcolati per ogni manifest a `load_catalog` (filesystem stat +
  JSON parse, cost trascurabile)

`runtime/prefilter.py`:
- `_filter_dormant(catalog)`: skip entries con `dormant=True`
- applicato a `rank`, `rank_adaptive`, `rank_with_intent`

Il PLANNER NON vede gli executor dormant nel pool. Resta visibili in
introspezione (`metnos-skills list`, admin UI).

## Consequences

- 17 executor `*_google_workspace` correttamente filtrati quando OAuth
  scade/manca (verificato live simulando token removed).
- Catalog non cambia (executor restano caricati e firmati). Solo
  filter prefilter cambia output.
- Fail-open su exception del check (non castrare per errore di
  check function buggata).
- 14 unit test `runtime/tests/test_skill_credentials.py` coprono:
  parse provenance, check google ok/missing/invalid/no-refresh,
  compute_dormancy unknown/missing/ok, _filter_dormant, fail-open.

## Notes

- La validazione "OAuth token funziona davvero" richiederebbe network
  call → fuori scope. Il check filesystem+refresh_token e' best-effort
  ma sufficiente per evitare i 99% dei routing inutili.
- Pattern estendibile: nuove skill aggiungono entry in `_CHECKS`. Per
  granularita' fine (es. "scopes-aware"), la firma `()→(bool, str)`
  puo' diventare `(scopes: list)→(bool, str)` in futuro.
