---
id: 0155
title: Planner choice > runtime override — il runtime non sovrascrive scelte del planner
date: 2026-05-20
status: proposed
area: runtime | planner | code-review-policy
related:
  - 0153  # auto-remediation pattern per error_class strutturati
  - 0154  # pipeline shape FSM: eccezione codificata e disciplinata
  - 0094  # fast-path: short-circuit deterministico (eccezione legittima)
  - 0107  # vaglio safe-verb shortcut (eccezione legittima)
---

## Contesto

Il 10/5/2026, dopo un bug UX («risposta educata ma inutile» di
`describe_entries` su entries URL-only), e' stato aggiunto in
`agent_runtime.py` un **interceptor incondizionato** del seguente tipo:

```python
# 10/5/2026 fix UX deterministico
if chosen_name == "describe_entries":
    if from_step risale a find_urls (via walk-back helpers):
        SKIP describe_entries  # produrrebbe sintesi educata senza link
        emit auto-final con lista cliccabile
        return
```

L'interceptor pattern-match su `(chosen_tool, predecessor_tool)` e
dirottava il flusso prima della esecuzione, **a prescindere
dall'intent dell'utente**. Funzionava per query «cerca X» ma rompeva
query «riassumi X»: l'utente che voleva sintesi riceveva una lista
link.

Il 20/5/2026, dopo l'introduzione di ADR 0153 (`needs_content_fetch`
auto-remediation), l'interceptor e' diventato **dead code dannoso**:
preveniva la auto-remediation dal funzionare, e contraddiceva il
principio architetturale che il PLANNER e' il decision-maker primario.

Tentativi successivi di "rendere l'interceptor intelligente" (regex
list-intent IT+EN per gating, commit `c60c16d`) hanno introdotto patch
linguistiche hardcoded contro §7.3 («soluzioni generali, mai
hardcoded»).

## Decisione

Il runtime **non sovrascrive scelte deterministiche del planner** se
non via uno dei meccanismi disciplinati esistenti:

1. **Auto-remediation per error_class strutturati** (ADR 0153 + 0154).
   L'executor o la FSM dichiarano "manca X" con un valore enum chiuso;
   il runtime risponde con un prereq registrato in
   `runtime/auto_remediation.REMEDIATIONS`. L'utente vede il pattern di
   remediation tracciato negli step.
2. **Vaglio costituzionale** (ADR 0078 + 0107): block deterministico
   per violazioni di policy, mai per scelte di forma.
3. **Fast-path L1/L2** (ADR 0094 / 0150): short-circuit `pre-planner`,
   non override `post-planner`. Il fast-path SCAVALCA il planner, non
   lo CONTRADDICE.

Sono **vietati**:

- Interceptor che fanno pattern-match su `(chosen_tool, predecessor_tool)`
  per dirottare il flusso.
- Hardcoded keyword/regex sulla query utente per gating.
- Branch nel runtime che dicono "il planner ha chiesto X ma faccio Y".

Per code review: ogni nuovo branch in `agent_runtime` che modifica
`chosen_name`/`raw_args` post-planner-decision deve:

- citare un ADR di remediation registrato, OPPURE
- essere giustificato come fast-path pre-planner, OPPURE
- essere block costituzionale via vaglio.

## Propriet&agrave;

- **Coerente con il modello mentale**: il planner LLM e' il "cervello
  ragionante" (`no&ucirc;s`), il runtime e' la "saggezza pratica
  meccanica" (`m&ecirc;tis`). Non si contraddicono &mdash; si compongono.
- **Lang-agnostic**: nessun keyword matching IT/EN, niente bias per
  lingua dell'utente.
- **Future-proof**: nuovi executor o verbi aggiunti al vocabolario
  vengono raggiunti dal planner senza richiedere update di interceptor
  hardcoded.
- **Debug-friendly**: il turn log mostra la pipeline del planner
  invariata, plus i prereq di remediation come step espliciti.

## Caso canonico (esempio applicato)

`describe_entries` chiamato su entries URL-only:

| Vecchio comportamento (interceptor 10/5)  | Nuovo (rimosso 20/5)                    |
|--------------------------------------------|------------------------------------------|
| SKIP describe, lista link auto-generata    | describe gira, emette `needs_content_fetch` |
| Sintesi "educata" senza dati (bug originale) | Runtime inietta `read_urls_html` via ADR 0153 |
| Lista link mostrata sempre                 | Sintesi reale + link sotto come fonti     |
| Bypass UX broken per query «riassumi X»    | Nessuna eccezione: la pipeline funziona  |

## Alternative considerate

1. **Interceptor "intelligente" con regex list-intent** (sperimentato
   20/5/2026 mattina). Rifiutata: hardcoded vocabolario IT+EN, falsi
   positivi/negativi, viola §7.3.
2. **Override solo se planner conferma con doppio call**. Rifiutata:
   non semantico, complica il loop.
3. **Lasciare interceptor come safety net**. Rifiutata: dead code
   dannoso, blocca auto-remediation.

## Esempi di violazione (per code review futuro)

Pattern da bloccare a code review:

```python
# ❌ VIETATO: pattern-match per dirottare
if chosen_name == "X" and prev_step.tool == "Y":
    chosen_name = "Z"  # override

# ❌ VIETATO: regex IT/EN per gating
if re.search(r"\b(dammi|give me|elenca)\b", user_query, re.I):
    branch_alternativo()

# ❌ VIETATO: hardcoded enum di executor name
if chosen_name in {"describe_entries", "classify_entries", "compute_entries"}:
    bypass_normale()
```

Pattern accettabili:

```python
# ✅ AUTO-REMEDIATION: error_class strutturato (ADR 0153/0154)
if obs.get("error_class") in REMEDIATIONS:
    prereq, retry = remediate(obs)

# ✅ FAST-PATH: pre-planner, deterministico, ADR 0094
if (fp := try_fast_path(query)) is not None:
    return fp

# ✅ POLICY: vaglio costituzionale, ADR 0078
verdict = vaglio.judge(step)
if verdict.kind == "block":
    abort_with_reason(verdict.reason)
```

## Costi

- **Implementation**: 0 nuove righe. Cleanup negativo: 107 righe rimosse
  (commit `31c33ec`).
- **Latency**: invariata.
- **Storage**: zero.
- **Maintenance**: regola di code review &mdash; documentata qui per
  future PR.

## Out of scope

- Cosa fa il planner: questo ADR non vincola le scelte del planner,
  solo che il runtime non le sovrascriva arbitrariamente.
- Vaglio policy: blocca legittimamente, gia' coperto da ADR 0078.
- Refactor del file `agent_runtime.py` (7731 righe). Task #9.

## Implementation status

Rimosso il 20/5/2026 con commit `31c33ec`. La regola di code review
disposta dal presente ADR si applica a tutto il codice futuro.
