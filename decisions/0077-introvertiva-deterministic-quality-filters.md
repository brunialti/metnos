---
id: 0077
title: Introvertiva — filtri qualità deterministici (vocab validator, flow args, template skip, threshold bump)
date: 2026-05-04
status: accepted
area: runtime, introvertiva
related:
  - 0045  # closed naming vocabulary
  - 0067  # introvertiva MVP
  - 0076  # synth_request short-circuit
modifies:
  - 0067
---

## Context

Audit dell'introvertiva notturna 4/5/2026 (~05:00 locale, 24 proposte
totali, 0 applicate, 1 errore code-level): qualita' delle proposte
**bassa** (~30% utili). Lacune sistematiche identificate:

1. **Nomi proposti che violano il vocabolario chiuso**: `sort_entries_True`,
   `find_files_True`, `find_dirs__opt_myclaw`, `compute_files__tmp_test_sha_txt`.
   Booleani come qualifier (`_True`, `_False`), doppio underscore,
   path letterali — tutti contrari a CLAUDE.md §2.2.
2. **Args di flusso specializzati**: `get_files_metadata/entries="{{step1.entries}}"`.
   `entries` e' un argomento di pipeline, non una costante utente — bug
   semantico nell'algoritmo specialize.
3. **Pattern obsoleti**: `[write_files, fetch_urls, write_files]` e dedupe
   legacy continuano a essere proposti anche dopo la rimozione di
   `fetch_urls` (3/5/2026).
4. **Soglia uses=3 sotto noise floor**: produce candidate "rumore" tipo
   `find_packages/ffmpeg` con 3 sole occorrenze.
5. **Booleani come specialize**: dom=1.0 su un valore booleano e' quasi
   sempre tautologico (val == default), e produce comunque proposed_name
   illegali (`_True`/`_False`).
6. **Bug code-level**: `NameError: name 'time' is not defined` in
   `Synt.specialize` — modulo `time` usato ma non importato in `synt.py`.

## Decision

Tutti i fix applicati in `runtime/introvertiva.py` e `runtime/synt.py`
sono **deterministici**. Nessuno richiede chiamata LLM. Coerente con la
direttiva "codice deterministico > LLM se equipotente, equiefficace o se
codice deterministico [sarebbe] troppo complesso" (4/5/2026).

### Fix in `introvertiva.py`

**A. Validator nome** — `_is_valid_proposed_name(name) -> bool`:
- Richiede `azione_oggetto[_qualifier]` (almeno un `_`).
- Verbo deve essere in `vocab.ACTIONS`.
- Oggetto deve essere in `vocab.OBJECTS`.
- Qualifier non puo' iniziare con cifra, non puo' essere `True`/`False`,
  deve essere alfanumerico+underscore.
- Niente doppi underscore, niente trailing underscore.

Il validator e' applicato dentro `candidates_specialize` PRIMA di
emettere il candidato. Se `proposed_name` non passa, il candidato e'
scartato silenziosamente.

**B. Skip flow args** — `_FLOW_ARGS = frozenset({"entries", "from_step", "results"})`:
sostituisce il vecchio `if k == "from_step": continue` con un set
chiuso. Coerente con la convenzione I/O degli executor (ADR 0064).

**C. Skip template values** — `_is_template_value(val_str) -> bool`:
rifiuta valori che contengono `{{...}}` (placeholder runtime). Un
valore come `"{{step1.entries}}"` non e' una costante user — non e'
specializzabile.

**D. Skip booleani** dentro `candidates_specialize`: anche se
`skip_if_matches_default` non li ha gia' rimossi, un booleano produce
comunque slug `True`/`False` che il validator (A) rifiuterebbe — ma
saltarli a monte risparmia il loop.

**E. Bump default `min_uses` 3 → 10** in `candidates_specialize`. Sotto
10 e' rumore; non ha senso proporre specialize su 3 occorrenze.

**F. Filtro pattern obsoleti** in `candidates_generalize`: skippa
catene che includono executor non piu' nel catalog corrente. Counter
`skipped_obsolete` interno.

### Fix in `synt.py`

**G. Import mancante** — aggiunto `import time` in cima al modulo
(`synt.py:32`). Risolve `NameError: name 'time' is not defined` nel
codice generato da `Synt.specialize` (template `time.strftime`,
`time.time()`).

## Consequences

**Verifica pre-/post-fix sullo stesso input** (turn log 4/5/2026):

| metric            | pre-fix | post-fix |
|-------------------|---------|----------|
| dedupe            | 1       | 1        |
| generalize        | 3 (1 diag) | 1 (+1 diag) |
| specialize        | 20      | 4        |
| **totale**        | **24**  | **6**    |
| spurie (~booleani / path / template) | ~12 | 0 |
| obsolete (fetch_urls) | ~3 | 1 (residuo dedupe legacy_orphan, atteso) |

Riduzione del 75% del volume con qualita' attesa molto piu' alta. I 4
specialize residui sono tutti casi reali (`get_files_metadata_dates_semantic`
58 uses, `move_messages_Posta_indesiderata` 16 uses, ...).

Il dedupe `legacy_orphan` per `fetch_urls` e' la segnalazione corretta,
non spurio: il sistema nota che il mnest si trascina un executor
obsoleto e propone cleanup. Non va filtrato.

## Open

- **Sweep purge_events_for_removed_executors**: il filtro F skippa i
  pattern che includono executor obsoleti, ma i mnest restano nel
  database. Un sweep notturno periodico che PURGI definitivamente
  i mnest legacy e' work item separato (next ADR).
- **Audit del wrapper specialize generato**: l'import `import time`
  e' fixato, ma il wrapper potrebbe avere altre fragilita'. Da
  validare con la prossima esecuzione di `introvertiva_apply`
  (notte 5/5).

## References

- `runtime/introvertiva.py` (`_is_valid_proposed_name`, `_FLOW_ARGS`,
  `_is_template_value`, `candidates_specialize`, `candidates_generalize`).
- `runtime/synt.py` (import `time`).
- `vocab.py` (`ACTIONS`, `OBJECTS`).
- Memory `metnos_introvertiva_quality_audit_4may.md`.
- ADR 0067 (introvertiva MVP — modificato qui sui filtri).
- ADR 0076 (synth_request short-circuit — complementare).
- Direttiva "codice deterministico > LLM" (memory `feedback_deterministic_over_llm`).
