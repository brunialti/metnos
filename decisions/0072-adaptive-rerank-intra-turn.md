---
id: 0072
title: Adaptive re-ranking of the tool pool intra-turn (ranker-agnostic, add-only)
date: 2026-05-04
status: accepted
area: runtime, prefilter
related:
  - 0058  # intent extractor LLM-based
  - 0063  # prefilter universal-helper-verbs exception
  - 0064  # literal `paths` arg for path-only entries consumers
complements:
  - 0058
  - 0063
---

## Context

Il prefilter `rank_adaptive` viene chiamato UNA volta a inizio turno con la
sola query utente come input. I candidati restano costanti per tutto il turno:
5-8 executor + universal helpers, totale ≈ 13-17 tool al PLANNER (cfr. ADR
0063). Il vincolo è strutturale: senza una conoscenza di cosa lo step N
produrrà, il prefilter al turno 0 può solo pesare sui verbi della query
iniziale.

Per pipeline multi-dominio (es. mail → estrai allegato → OCR → comprimi → invia)
il PLANNER potrebbe non vedere il tool che gli serve allo step N perché il
prefilter al turno 0 ha pesato verbi diversi. La conseguenza non è un
"loop_break" silenzioso (catturato da §10.6.3), ma una *miss* di precisione: il
PLANNER finisce a usare un universal helper o richiama lo stesso producer due
volte, sprecando step.

## Decision

Aggiungere `runtime/adaptive_rerank.py` come hook intra-turno. Dopo ogni step
`ok`, il modulo:

1. **estrae keywords dall'observation** (`extract_keywords`): walk strutturato
   dei valori string, taglio per profondità (≤4) e cardinalità (≤30 elementi
   per lista), filtro stopwords IT+EN, skip dei campi tecnici (`audit_path`,
   `digest`, `ts`, `duration_ms`, ...). Cap: 12 keyword per observation.
2. **costruisce una query estesa** (`build_extended_query`):
   `<query originale> + " " + " ".join(keywords)`.
3. **ri-chiama `rank_adaptive`** sulla query estesa.
4. **aggiunge** (mai sostituisce) i nuovi tool emergenti al pool dei candidati,
   fino a un cap di sicurezza `2 × k_max`.

Vincoli operativi:

- **Indipendente dal ranker**: il modulo costruisce solo la query estesa.
  Il rank effettivo resta a `rank_adaptive`. Vale per token-based oggi e per
  embedding domani (cfr. ADR 0073 per la decisione attuale di restare
  token-based).
- **Add-only**: i tool già scelti per il turno restano sempre disponibili.
  Il re-rank non può rimuoverli. Conseguenza: zero rischio di "perdere"
  un tool a metà pipeline.
- **Cap**: pool finale ≤ `2 × k_max` (default 16). Oltre il rumore comincia a
  pesare sulla scelta del PLANNER.
- **Hook in `agent_runtime.py`**: invocato dopo ogni step con `result.ok ==
  true`, dentro un `try/except` largo. Failure del re-rank non interrompe il
  turno (degrade graceful al pool corrente).

## Consequences

- Pipeline multi-dominio scoprono i tool dei domini "successivi" man mano che
  i dati emergono dalle observation. Esempio canonico: query "leggi le mail
  con allegati pdf e archiviameli" — al turno 0 il pool pesa i verbi
  email/read; dopo lo step 1 le entries portano `attachment`, `pdf`,
  `application/pdf` → re-rank aggiunge `read_files_pdf`, `move_files`.
- Costo a regime (catalog 40 executor, misurato 4/5/2026): ~1-5 ms per
  re-rank. Su un turno tipico (5 step ok) costa ~5-25 ms — sotto lo 0.5%
  della latenza totale (5-15 s).
- Ranker-agnostic per scelta deliberata: in futuro è possibile sostituire
  `rank_adaptive` con un ranker embedding-based o ibrido senza toccare il
  modulo `adaptive_rerank`. Il bench 4/5/2026 (ADR 0073) mostra che il
  token-based resta la scelta primaria.

## Test (`tests/test_adaptive_rerank.py`)

13 test che coprono:
- `extract_keywords`: token in dict annidati, skip dei campi audit/metadata,
  resilienza a observation vuote/malformate, cap su `max_keywords`, cap su
  lunghezza dei valori string.
- `build_extended_query`: passthrough quando l'observation è vuota,
  concatenazione corretta delle keyword, robustezza su query vuote.
- `re_rank_for_step`: proprietà add-only (i candidati esistenti non vengono
  mai rimossi), cap `2 × k_max`, `applied=False` quando l'observation è
  vuota o non porta nuove keyword, resilienza a observation `None`/non-dict.
- Smoke perf: re-rank medio < 50 ms (target ≤ 10 ms su catalog reale).

## References

- `runtime/adaptive_rerank.py`
- `runtime/agent_runtime.py` (hook dopo step ok)
- `runtime/tests/test_adaptive_rerank.py`
- ADR 0073 (bench embedding-vs-token, 4/5/2026)
